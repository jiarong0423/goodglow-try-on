"""英文模式與品牌代稱的端到端測試（真的起一台 --mock 伺服器、走 HTTP）。

紅隊 F5：之前英文／代稱路徑只有單元測試，沒有任何測試走過 HTTP。這裡的每一條，只要有人把 server.py 的
`_json` 裡的代稱或英文化拿掉、或讓某個回傳繞過它，就會失敗：
- 英文：稽核計畫（tools/audit_api_strings.py 的請求）的每個回應，都不能還剩中文。
- 中文：同一批請求不帶語言時，回傳要和以前一樣（有中文、沒有被英文化）。
- 代稱：用虛構示範型錄當「真實品牌資料」建代稱（乾淨 clone 也跑得起來），回傳不能出現品牌名、產品名、色號色名；
  色號代碼與色碼要留著。有本機真實品牌資料時，再用真資料跑一次完整稽核。
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
    """audit.call 的版本：lang 可為 None（不帶語言標頭）。"""
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
        """首頁數字不能寫死：用狀態回傳的實際型錄（乾淨 clone 只有虛構示範型錄）。"""
        _s, st = fetch(self.base, "zh", "/api/status")
        self.assertIn(st["catalog_kind"], ("demo", "official"))
        cat = json.loads((ROOT / "app" / ("catalog.json" if st["catalog_kind"] == "demo" else "partner_shades_official.json")).read_text(encoding="utf-8"))
        self.assertEqual(st["catalog_stats"]["shades"], sum(len(p["shades"]) for p in cat["products"]))
        self.assertEqual(st["catalog_stats"]["brands"], len({p["brand"] for p in cat["products"]}))
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<b>661</b>", html)

    def test_hostile_echo_does_not_stall_the_server(self):
        """安全審查：回波進回應的使用者字串很長又惡意時，英文化不能把伺服器拖垮。"""
        import time
        evil = "http://" + (":" + " 模式：") * 1500
        t = time.time()
        fetch(self.base, "en", "/api/makeup", {"brow_hex": evil, "demo": True, "style": "natural", "condition": "albinism", "selection": {}})
        self.assertLess(time.time() - t, 3.0)


class AliasWithDemoCatalogTests(unittest.TestCase):
    """用虛構示範型錄當成「真實品牌資料」，驗證代稱流程走過 HTTP；不需要本機品牌檔。"""

    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "app" / "catalog.json").read_text(encoding="utf-8"))
        cls.brands = sorted({p["brand"] for p in cls.catalog["products"]})
        cls.td_alias = tempfile.TemporaryDirectory()
        alias_file = Path(cls.td_alias.name) / "alias.json"
        alias_file.write_text(json.dumps({b: chr(ord("S") + i) for i, b in enumerate(cls.brands)}), encoding="utf-8")
        cls.ba = brand_alias.load(alias_file, [ROOT / "app" / "catalog.json"])
        cls.td, cls.httpd, cls.base = start(cls.ba, catalog="demo")      # 只用虛構型錄，本機有沒有真實品牌檔都一樣

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
                self.assertNotRegex(m["code"], r"[㐀-鿿]")            # 色名（中文）已去掉，只剩代碼

    def test_alias_is_applied_to_the_whole_audit_plan(self):
        names = audit.independent_names(ROOT / "app")      # 乾淨 clone 沒有真實品牌檔 → 名單可能是空的，所以另外用 ba.leaks
        for lang in ("zh", "en"):
            for name, path, body in audit.requests_plan(self.base, lang):
                status, resp = fetch(self.base, lang, path, body)
                self.assertLess(status, 500, name)
                self.assertEqual(self.real_leaks(resp), [], (lang, name))
                self.assertEqual(audit.independent_leaks(resp, names), set(), (lang, name))

    def real_leaks(self, resp):
        """示範型錄的產品名是「唇彩」「粉底」這類一般詞，和代稱後的品類名一樣，不算外洩。"""
        generic = {w for labels in brand_alias.CATEGORY_LABEL.values() for w in labels.values()} | {"粉底液", "遮瑕", "腮紅", "眉筆"}
        return [x for x in self.ba.leaks(resp) if x not in generic]

    def test_without_alias_the_same_request_does_show_names(self):
        """反向確認：這個測試是有效的，不是永遠通過（沒代稱時會看到品牌名）。"""
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
        self.assertEqual(raw[:2], b"\xff\xd8")            # 仍是完整的 JPEG


@unittest.skipUnless(REAL_DATA, "沒有本機真實品牌資料與代稱對照表（app/partner_shades_official.json、app/brand_alias.json）")
class AliasWithRealDataTests(unittest.TestCase):
    """有本機品牌資料時，用真資料跑完整稽核：品牌名、產品名、色號全名都不能出現，三種資料來源都一樣。"""

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
