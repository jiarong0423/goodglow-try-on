"""End-to-end tests for English mode and brand aliases (really starts a --mock server and goes over HTTP).

Red team 10-21 F5: previously the English / alias paths had unit tests only, and no test went through HTTP. Each case here fails if anyone removes the aliasing or English localization in server.py's
`_json`, or lets some response bypass it:
- English: every response in the audit plan (requests from tools/audit_api_strings.py) must have no Chinese left.
- Chinese: the same requests without a language must return what they did before (Chinese present, not English-localized).
- Aliases: build aliases using the fictional demo catalog as "real brand data" (also runs in a clean clone); responses must not contain brand names, product names, or shade names;
  shade codes and color hex codes must remain. When real local brand data exists, run the full audit again with it.
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

import audit_api_strings as audit  # noqa: E402
import brand_alias  # noqa: E402
import server  # noqa: E402

CJK = audit.CJK
REAL_DATA = (ROOT / "app" / "partner_shades_official.json").is_file() and (ROOT / "app" / "brand_alias.json").is_file()


def start(alias=None, catalog="all"):
    td = tempfile.TemporaryDirectory()
    state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td.name) / "ledger.json", catalog=catalog)
    if alias is not None:
        state.alias = alias
    httpd = server.make_server("127.0.0.1", 0, state)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return td, httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def fetch(base, lang, path, body=None):
    """Version of audit.call: lang may be None (no language header)."""
    headers = {"Content-Type": "application/json"}
    if lang:
        headers["X-App-Lang"] = lang
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class EnglishAndChineseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td, cls.httpd, cls.base = start()
        cls.plan = audit.requests_plan(cls.base, "en")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.td.cleanup()

    def test_plan_covers_the_paths_the_red_team_found_missing(self):
        names = " ".join(n for n, _p, _b in self.plan)
        for need in ("demo-photo albinism", "demo-photo vitiligo", "match foundation partner", "card albinism official", "skin-plan"):
            self.assertIn(need, names)

    def test_english_responses_have_no_chinese_left(self):
        left = {}
        for name, path, body in self.plan:
            status, resp = fetch(self.base, "en", path, body)
            self.assertLess(status, 500, (name, status))
            for p, s in audit.strings(audit.strip_added(resp)):
                left.setdefault(s, []).append(f"{name} {p}")
        self.assertEqual(left, {}, "英文模式回傳還有中文：補進 docs/i18n/zh_en_map.csv，或讓該欄位走 server._json")

    def test_chinese_responses_are_not_translated_and_query_lang_works(self):
        zh_hits = 0
        for name, path, body in self.plan:
            status, resp = fetch(self.base, None, path, body)
            if any(True for _ in audit.strings(resp)):
                zh_hits += 1
        self.assertGreater(zh_hits, len(self.plan) // 2, "不帶語言標頭時應回中文原文")
        _s, en = fetch(self.base, None, "/api/presets?lang=en")
        _s, zh = fetch(self.base, None, "/api/presets")
        self.assertEqual([x for _p, x in audit.strings(en)], [])
        self.assertTrue([x for _p, x in audit.strings(zh)])

    def test_status_reports_the_catalog_that_is_really_loaded(self):
        """Homepage numbers must not be hard-coded: use the actual catalog from the status response (a clean clone only has the fictional demo catalog)."""
        _s, st = fetch(self.base, "zh", "/api/status")
        self.assertIn(st["catalog_kind"], ("demo", "official"))
        cat = json.loads((ROOT / "app" / ("catalog.json" if st["catalog_kind"] == "demo" else "partner_shades_official.json")).read_text(encoding="utf-8"))
        self.assertEqual(st["catalog_stats"]["shades"], sum(len(p["shades"]) for p in cat["products"]))
        self.assertEqual(st["catalog_stats"]["brands"], len({p["brand"] for p in cat["products"]}))
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<b>661</b>", html)

    def test_hostile_echo_does_not_stall_the_server(self):
        """Red team F1: when a very long malicious user string is echoed into a response, English localization must not bog down the server."""
        import time
        evil = "http://" + (":" + " 模式：") * 1500
        t = time.time()
        fetch(self.base, "en", "/api/makeup", {"brow_hex": evil, "demo": True, "style": "natural", "condition": "albinism", "selection": {}})
        self.assertLess(time.time() - t, 3.0)


class AliasWithDemoCatalogTests(unittest.TestCase):
    """Use the fictional demo catalog as "real brand data" to verify the alias flow over HTTP; no local brand file needed."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "app" / "catalog.json").read_text(encoding="utf-8"))
        cls.brands = sorted({p["brand"] for p in cls.catalog["products"]})
        cls.td_alias = tempfile.TemporaryDirectory()
        alias_file = Path(cls.td_alias.name) / "alias.json"
        alias_file.write_text(json.dumps({b: chr(ord("S") + i) for i, b in enumerate(cls.brands)}), encoding="utf-8")
        cls.ba = brand_alias.load(alias_file, [ROOT / "app" / "catalog.json"])
        cls.td, cls.httpd, cls.base = start(cls.ba, catalog="demo")      # fictional catalog only, same result whether or not a local real brand file exists

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.td.cleanup()
        cls.td_alias.cleanup()

    def match(self, lang, cat="foundation"):
        return fetch(self.base, lang, "/api/match", {"hex": "#E1CDC6", "category": cat, "source": "demo", "top": 5})[1]

    def test_brand_product_and_shade_names_are_gone_in_both_languages(self):
        for lang in ("zh", "en"):
            resp = self.match(lang)
            self.assertTrue(resp["matches"])
            self.assertEqual(self.ba.leaks(resp), [], lang)
            for m in resp["matches"]:
                self.assertRegex(m["hex"], r"^#[0-9A-F]{6}$")
                self.assertNotRegex(m["code"], r"[㐀-鿿]")            # shade name (Chinese) removed, only the code remains

    def test_alias_is_applied_to_the_whole_audit_plan(self):
        names = audit.independent_names(ROOT / "app")      # a clean clone has no real brand file -> the name list may be empty, so ba.leaks is used as well
        for lang in ("zh", "en"):
            for name, path, body in audit.requests_plan(self.base, lang):
                status, resp = fetch(self.base, lang, path, body)
                self.assertLess(status, 500, name)
                self.assertEqual(self.real_leaks(resp), [], (lang, name))
                self.assertEqual(audit.independent_leaks(resp, names), set(), (lang, name))

    def real_leaks(self, resp):
        """Demo catalog product names are generic words like "lip gloss" and "foundation", the same as the category names after aliasing, so they are not a leak."""
        generic = {w for labels in brand_alias.CATEGORY_LABEL.values() for w in labels.values()} | {"粉底液", "遮瑕", "腮紅", "眉筆"}
        return [x for x in self.ba.leaks(resp) if x not in generic]

    def test_without_alias_the_same_request_does_show_names(self):
        """Reverse check: this test is effective and does not always pass (brand names show up when there is no alias)."""
        td, httpd, base = start(None)
        try:
            resp = fetch(base, "zh", "/api/match", {"hex": "#E1CDC6", "category": "foundation", "source": "demo", "top": 5})[1]
            self.assertNotEqual(self.ba.leaks(resp), [])
        finally:
            httpd.shutdown()
            httpd.server_close()
            td.cleanup()

    def test_image_payload_is_never_rewritten(self):
        resp = fetch(self.base, "en", "/api/demo-photo?condition=albinism")[1]
        if not resp.get("ok"):
            self.skipTest("沒有示範照")
        self.assertTrue(resp["image"].startswith("data:image"))
        import base64
        raw = base64.b64decode(resp["image"].split(",", 1)[1])
        self.assertEqual(raw[:2], b"\xff\xd8")            # still a complete JPEG


@unittest.skipUnless(REAL_DATA, "沒有本機真實品牌資料與代稱對照表（app/partner_shades_official.json、app/brand_alias.json）")
class AliasWithRealDataTests(unittest.TestCase):
    """With local brand data, run the full audit on the real data: brand names, product names and full shade names must not appear, for all three data sources."""

    @classmethod
    def setUpClass(cls):
        cls.ba = brand_alias.load()
        cls.names = audit.independent_names(ROOT / "app")
        cls.td, cls.httpd, cls.base = start(cls.ba)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.td.cleanup()

    def test_full_plan_has_zero_leaks_in_both_languages(self):
        self.assertGreater(len(self.names), 100)
        for lang in ("zh", "en"):
            for name, path, body in audit.requests_plan(self.base, lang):
                status, resp = fetch(self.base, lang, path, body)
                self.assertLess(status, 500, name)
                self.assertEqual(self.ba.leaks(resp), [], (lang, name))
                self.assertEqual(audit.independent_leaks(resp, self.names), set(), (lang, name))

    def test_client_chosen_source_cannot_bypass_the_alias(self):
        for src in ("official", "partner", "all"):
            resp = fetch(self.base, "en", "/api/match", {"hex": "#C8A090", "category": "foundation", "source": src, "top": 80})[1]
            blob = json.dumps(resp, ensure_ascii=False)
            for word in ("Alpha Wear", "Beta Fix", "Gamma Long-Wear", "Delta Matte", "EpsilonPro", "ZetaBrand", "EtaBrand"):
                self.assertNotIn(word, blob, (src, word))


if __name__ == "__main__":
    unittest.main()
