"""Pre-judging fixes of 2026-10-29: truthful provenance of every try-on picture (replayed real call with its date, live call, local simulation),
the home page lists the same eight steps as the flow, product-page links only from catalog data (never guessed, dropped in alias mode),
and the plain-text summary for the beauty advisor. No network, no credits."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand_alias  # noqa: E402
import color_engine as ce  # noqa: E402
import server  # noqa: E402
from test_server import FakeYouCam, ServerTestBase, jpeg_data_url  # noqa: E402

EXACT_ALBINISM = {"demo": True, "condition": "albinism", "style": "natural", "undertone": "pink_cool",
                  "selection": {"foundation": "light", "contour": "keep", "highlight": "center", "brow": "photo_soft",
                                "lash": "champagne", "blush": "soft", "lip": "natural_gloss"},
                  "skin_hex": "#E1CDC6", "brow_hex": "#CBB4A8", "foundation_hex": "#F0D4C6"}


class ProvenanceMockTests(ServerTestBase):
    def test_exact_replay_reports_kind_and_recording_date_from_index(self):
        code, res = self.post("/api/makeup", EXACT_ALBINISM)
        self.assertEqual(code, 200, res)
        self.assertEqual(res["source"]["kind"], "replay_exact")
        index = json.loads((server.DEMO_DIR / "replay" / "albinism" / "exact" / "index.json").read_text(encoding="utf-8"))
        sel = dict(EXACT_ALBINISM["selection"], undertone="pink_cool", skin_hex="#E1CDC6", brow_hex="#CBB4A8", foundation_hex="#F0D4C6")
        key = server.exact_key("albinism", "natural", sel)
        self.assertIn(key, index)
        self.assertEqual(res["source"]["recorded_at"], index[key]["recorded_at"][:10])
        self.assertRegex(res["source"]["recorded_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_unrecorded_options_report_closest_replay(self):
        # vitiligo has named recordings: the closest saved YouCam call. albinism has none: the pre-made local look, which must be reported as a simulation
        code, res = self.post("/api/makeup", dict(EXACT_ALBINISM, condition="vitiligo", selection={"lip": "tinted_balm", "blush": "flush"}))
        self.assertEqual(code, 200, res)
        self.assertEqual(res["source"], {"kind": "replay_closest"})
        code, res = self.post("/api/makeup", dict(EXACT_ALBINISM, selection={"lip": "tinted_balm", "blush": "flush"}))
        self.assertEqual(code, 200, res)
        self.assertEqual(res["source"], {"kind": "sim_look"})

    def test_own_photo_in_demo_mode_is_a_labelled_simulation(self):
        code, res = self.post("/api/makeup", {"style": "natural", "undertone": "neutral", "selection": {"lip": "tinted_balm"},
                                              "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertEqual(res["source"], {"kind": "demo_label"})

    def test_quick_looks_carry_a_source_each(self):
        code, res = self.post("/api/looks", {"demo": True, "condition": "vitiligo", "style": "natural", "undertone": "neutral"})
        self.assertEqual(code, 200, res)
        for which in ("glow", "clear"):
            self.assertIn(res["looks"][which]["source"]["kind"], ("replay_exact", "replay_closest"))

    def test_exact_recorded_at_unknown_key_is_none(self):
        self.assertIsNone(server.exact_recorded_at("albinism", "0000000000000000"))


class ProvenanceLiveTests(ServerTestBase):
    mock_mode = False

    def test_live_call_reports_kind_live(self):
        code, res = self.post("/api/makeup", {"style": "natural", "undertone": "neutral", "selection": {"brow": "taupe"},
                                              "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertEqual(res["source"], {"kind": "live"})
        self.assertEqual(len(FakeYouCam.tasks), 1)


class ProductLinkTests(unittest.TestCase):
    def test_attach_urls_only_where_the_catalog_has_a_product_page(self):
        urls = {("Acme", "Acme Fluid"): "https://example.test/acme-fluid"}
        obj = {"items": [{"matches": [{"brand": "Acme", "product": "Acme Fluid", "code": "N10"},
                                      {"brand": "Zeta", "product": "Zeta Fluid", "code": "Z1"}]}],
               "image": "data:image/jpeg;base64,AAAA", "n": 3}
        out = ce.attach_urls(obj, urls)
        self.assertEqual(out["items"][0]["matches"][0]["url"], "https://example.test/acme-fluid")
        self.assertNotIn("url", out["items"][0]["matches"][1])
        self.assertEqual(out["image"], obj["image"])
        self.assertEqual(ce.attach_urls(obj, {}), obj)                        # no catalog links at all: untouched

    def test_demo_catalog_has_no_links(self):
        demo = json.loads(ce.DEMO_CATALOG.read_text(encoding="utf-8"))["products"]
        self.assertTrue(all("source_url" not in p for p in demo))

    def test_alias_mode_drops_links_and_leak_check_flags_them(self):
        ba = brand_alias.BrandAlias({"Acme": "A"}, {"Acme Fluid": "foundation"}, {"Acme Fluid": ["N10"]})
        row = {"brand": "Acme", "product": "Acme Fluid", "code": "N10", "url": "https://acme.example/fluid"}
        self.assertEqual(ba.leaks(row), ["Acme", "Acme Fluid", "url:https://acme.example/fluid"])
        out = ba.scrub(row, "en")
        self.assertNotIn("url", out)
        self.assertEqual(out["brand"], "Brand A")
        self.assertEqual(ba.leaks(out), [])


class ProductLinkServerTests(ServerTestBase):
    def test_official_rows_link_to_the_product_page_and_demo_rows_do_not(self):
        with tempfile.TemporaryDirectory() as td:
            official = {"version": "test", "products": [
                {"brand": "Acme", "name": "Acme Fluid", "category": "foundation", "source_url": "https://example.test/acme-fluid",
                 "shades": [{"code": "N10", "hex": "#F5E2DC", "undertone": "cool"}, {"code": "N20", "hex": "#E8C8B8", "undertone": "neutral"}]},
                {"brand": "Acme", "name": "Acme Stick", "category": "foundation",
                 "shades": [{"code": "S1", "hex": "#F4E0DA", "undertone": "cool"}]}]}
            f = Path(td) / "official.json"
            f.write_text(json.dumps(official), encoding="utf-8")
            with mock.patch.object(ce, "OFFICIAL_CATALOG", f):
                code, res = self.post("/api/match", {"hex": "#F5E2DC", "category": "foundation", "source": "official", "top": 3})
                self.assertEqual(code, 200, res)
                by_code = {m["code"]: m for m in res["matches"]}
                self.assertEqual(by_code["N10"]["url"], "https://example.test/acme-fluid")
                self.assertNotIn("url", by_code["S1"])                         # same brand, product without a page: no link invented
            code, res = self.post("/api/match", {"hex": "#F5E2DC", "category": "foundation", "source": "demo", "top": 3})
            self.assertEqual(code, 200, res)
            self.assertTrue(res["matches"] and all("url" not in m for m in res["matches"]))


class PageWiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_home_page_lists_the_same_eight_steps_as_the_flow(self):
        how = re.search(r'<ol class="how">(.*?)</ol>', self.html, re.S).group(1)
        ids = re.findall(r'<li><b>(\d)</b><span data-i18n="(s\d+)">', how)
        self.assertEqual([n for n, _ in ids], [str(i) for i in range(1, 9)])
        steps = re.search(r"const STEPS = \[(.*?)\];", self.js).group(1)
        self.assertEqual([i for _, i in ids], re.findall(r"__t\('(s\d+)'", steps))

    def test_try_on_badges_are_set_from_provenance_not_a_fixed_simulated_label(self):
        for ident in ("base-tag", "adj-tag"):
            tag = re.search(rf'<span class="sim-tag fc-tag" id="{ident}"[^>]*>', self.html).group(0)
            self.assertNotIn("data-i18n", tag)
        for fn in ("function provBadge", "function provLine", "function setSource", "setSource('base', res.source", "setSource('adj', res.source"):
            self.assertIn(fn, self.js)
        for kind in ("replay_exact", "replay_closest", "sim_look", "'live'", "'local'", "'photo'"):
            self.assertIn(kind, self.js)
        self.assertIn("sourceLine([sourceName(res.source)", self.js)                 # the "Source:" line follows the provenance, never a fixed "YouCam try-on"
        self.assertNotIn("__t('s0980'", self.js)                                  # the old "Saved YouCam result · simulated" line is gone
        self.assertIn('id="final-off-fig"><span class="sim-tag" data-i18n="s0985">YouCam 重播</span>', self.html)
        self.assertIn('<span class="sim-tag" id="expect-tag" data-i18n="s0060">模擬</span>', self.html)   # the AI expected look stays Simulated

    def test_demo_mode_explains_why_the_full_look_is_subtle(self):
        self.assertIn("__t('s0988'", self.js)
        self.assertIn("state.mode === 'mock' && state.demo", self.js)

    def test_advisor_copy_and_product_links_are_wired(self):
        for ident in ("btn-copy-card", "btn-copy-staff", "staff-text", "copy-status"):
            self.assertIn(f'id="{ident}"', self.html)
        for fn in ("function advisorText", "function copyForAdvisor", "function productLink", "navigator.clipboard", 'rel="noopener noreferrer"'):
            self.assertIn(fn, self.js)
        en = (ROOT / "web" / "i18n" / "en.js").read_text(encoding="utf-8")
        for i in ("s0982", "s0983", "s0984", "s0985", "s0986", "s0987", "s0988", "s0989", "s0992", "s0993", "s0994", "s0995", "s0999"):
            self.assertIn(f'"{i}":', en)

    def test_readme_first_screen_has_the_hosted_demo_and_the_video(self):
        head = self.readme[:600]
        self.assertIn("https://goodglow-new.jace0423.com", head)
        self.assertIn("https://youtu.be/BsHrLIcwiOw", head)


class ReadmeConsistencyTests(unittest.TestCase):
    """README labels match the badges the page really uses, and the stated pixel difference is what the saved results measure."""

    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_no_blanket_simulated_claims_left(self):
        self.assertNotIn("All images are simulated", self.readme)
        self.assertNotIn("Every image is labelled Simulated. Data", self.readme)
        self.assertNotIn("closest otherwise, labelled simulated", self.readme)
        for label in ("Replayed YouCam", "Live YouCam", "YouCam + local simulation", "Simulated"):
            self.assertIn(f"| **{label}** |", self.readme)

    def test_stated_pixel_difference_matches_the_saved_results(self):
        from PIL import Image, ImageChops, ImageStat
        means = []
        for cond in ("albinism", "vitiligo"):
            idx = json.loads((ROOT / "demo" / "replay" / cond / "exact" / "index.json").read_text(encoding="utf-8"))
            for key, row in idx.items():
                o = Image.open(ROOT / row["photo"]).convert("RGB")
                r = Image.open(ROOT / "demo" / "replay" / cond / "exact" / f"{key}.jpg").convert("RGB").resize(o.size)
                means.append(sum(ImageStat.Stat(ImageChops.difference(o, r)).mean) / 3)
        self.assertEqual(len(means), 80)
        self.assertAlmostEqual(min(means), 3, delta=0.5)
        self.assertAlmostEqual(max(means), 8, delta=0.5)
        self.assertIn("about 3 to 8 of 255 per pixel", self.readme)

    def test_cloudflare_beacon_is_not_in_the_repository_and_is_disclosed(self):
        for f in list((ROOT / "web").glob("*.html")) + list((ROOT / "web").glob("app.js")) + list((ROOT / "app").glob("*.py")):
            self.assertNotIn("cloudflareinsights", f.read_text(encoding="utf-8").lower(), f.name)
        self.assertIn("Cloudflare Web Analytics", self.readme)

    def test_skin_tone_analysis_answer_uses_repo_facts(self):
        import budget
        self.assertEqual(budget.PRICES["skin-tone-analysis"], 20)
        self.assertEqual(budget.PRICES["makeup-vto"], 1)
        self.assertIn("billed at 20 credits per call", self.readme)
        self.assertIn("we make no claim that either is more accurate", self.readme)

    def test_try_it_explains_the_copy_summary(self):
        self.assertIn("Copy for the beauty advisor", self.readme[:1500])


if __name__ == "__main__":
    unittest.main()
