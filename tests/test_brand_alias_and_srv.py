"""Unit tests for brand aliases (app/brand_alias.py) and server-side English localization (app/i18n_srv.py). Do not depend on local brand data."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import brand_alias  # noqa: E402
import i18n_srv  # noqa: E402


class BrandAliasTests(unittest.TestCase):
    def setUp(self):
        self.ba = brand_alias.BrandAlias(
            {"Acme": "A", "Acme Pro": "AP", "Zeta Co": "Z"},
            {"Acme Silk Foundation SPF 15": "foundation", "Zeta Lip Tint": "lip"})

    def test_brand_becomes_letter_zh_and_en(self):
        self.assertEqual(self.ba.scrub_text("Zeta Co", "zh"), "Z 牌")
        self.assertEqual(self.ba.scrub_text("Zeta Co", "en"), "Brand Z")

    def test_product_becomes_category_and_brand_is_replaced(self):
        zh = self.ba.scrub_text("Acme Acme Silk Foundation SPF 15：AB5（同深淺）", "zh")
        self.assertEqual(zh, "A 牌 粉底：AB5（同深淺）")
        en = self.ba.scrub_text("Zeta Co Zeta Lip Tint: 905", "en")
        self.assertEqual(en, "Brand Z Lip color: 905")

    def test_shade_codes_and_hex_untouched(self):
        self.assertEqual(self.ba.scrub_text("AB5 #E1CDC6 X-004", "zh"), "AB5 #E1CDC6 X-004")

    def test_nested_objects_and_non_strings(self):
        obj = {"brand": "Zeta Co", "n": 3, "list": [{"product": "Zeta Lip Tint", "ok": True}], "x": None}
        out = self.ba.scrub(obj, "en")
        self.assertEqual(out["brand"], "Brand Z")
        self.assertEqual(out["list"][0]["product"], "Lip color")
        self.assertEqual((out["n"], out["x"], out["list"][0]["ok"]), (3, None, True))

    def test_word_boundary_does_not_hit_inside_other_words(self):
        self.assertEqual(self.ba.scrub_text("Acmeology", "zh"), "Acmeology")

    def test_leaks_reports_remaining_real_names(self):
        self.assertEqual(self.ba.leaks({"a": ["Zeta Co ok"]}), ["Zeta Co"])
        self.assertEqual(self.ba.leaks(self.ba.scrub({"a": ["Zeta Co ok"]}, "zh")), [])

    def test_aliases_must_be_unique(self):
        with self.assertRaises(ValueError):
            brand_alias.BrandAlias({"A1": "M", "A2": "M"}, {})

    def test_load_fails_loudly_without_files(self):
        with self.assertRaises(FileNotFoundError):
            brand_alias.load(Path("/nonexistent/alias.json"), Path("/nonexistent/partner.json"))


class ServerLocalizerTests(unittest.TestCase):
    def setUp(self):
        self.loc = i18n_srv.Localizer({
            "exact": {"很接近": "Very close", "淺一階": "One step lighter", "很淺": "Very light"},
            "templates": [["差 {level_diff} 級", "{level_diff} level(s) apart"],
                          ["第 {lv} 級（{name}）", "Level {lv} ({name})"]]})

    def test_exact_match_and_whitespace_normalised(self):
        self.assertEqual(self.loc.tr("很接近"), "Very close")
        self.assertEqual(self.loc.tr("淺一階 "), "One step lighter")

    def test_template_match_and_captured_value_localised(self):
        self.assertEqual(self.loc.tr("差 2 級"), "2 level(s) apart")
        self.assertEqual(self.loc.tr("第 2 級（很淺）"), "Level 2 (Very light)")

    def test_unknown_string_is_returned_unchanged(self):
        self.assertEqual(self.loc.tr("表裡沒有的句子"), "表裡沒有的句子")

    def test_multi_sentence_only_when_every_sentence_known(self):
        self.assertEqual(self.loc.tr("很接近。很淺。"), "Very close Very light")
        self.assertEqual(self.loc.tr("很接近。不認識。"), "很接近。不認識。")

    def test_localize_walks_objects_and_leaves_non_strings(self):
        out = self.loc.localize({"a": ["很淺", 3], "b": {"c": None}, "d": True})
        self.assertEqual(out, {"a": ["Very light", 3], "b": {"c": None}, "d": True})


class ShadeAliasTests(unittest.TestCase):
    """Shade labels keep only the code and drop the shade name (10-21): real brand shade names are trademarked material; the code is what is needed to find the product."""

    def test_code_extraction_rules(self):
        f = brand_alias.shade_alias
        self.assertEqual(f("Pearl (X-004)", 1), "X-004")
        self.assertEqual(f("Plain Cool Maple (QN-062)", 1), "QN-062")
        self.assertEqual(f("Mild (204) Tinted", 1), "204")
        self.assertEqual(f("WX 64 Honeyed", 1), "WX 64")
        self.assertEqual(f("3D1 Cool Bone", 1), "3D1")
        self.assertEqual(f("270 Double Y", 1), "270")
        self.assertEqual(f("AB5", 1), "AB5")
        self.assertEqual(f(".5X", 1), ".5X")
        self.assertEqual(f("#01", 1), "#01")
        self.assertEqual(f("Light 100 Cool", 1), "100 Cool")        # keep cool/warm, otherwise Light 150 Cool / Warm would collide into the same label
        self.assertEqual(f("Rich Deep 450 Warm", 1), "450 Warm")

    def test_name_only_shade_becomes_position_number(self):
        self.assertEqual(brand_alias.shade_alias("Plum Whisper", 7), "No.7")
        self.assertEqual(brand_alias.shade_alias("Aurora Step, Done", 3), "No.3")

    def setUp(self):
        self.ba = brand_alias.BrandAlias(
            {"Acme": "A"},
            {"Acme Silk Foundation": "foundation", "Acme Lip": "lip"},
            {"Acme Silk Foundation": ["Sand Beige (Z-046)", "WX 64 Honeyed", "AB5"],
             "Acme Lip": ["Plum Whisper", "Soft Pink (213)", "Soft Rose (213)"]})

    def test_structured_fields_use_product_context(self):
        row = {"brand": "Acme", "product": "Acme Lip", "code": "Plum Whisper", "hex": "#B05060"}
        out = self.ba.scrub(row, "en")
        self.assertEqual((out["brand"], out["product"], out["code"], out["hex"]), ("Brand A", "Lip color", "No.1", "#B05060"))
        row2 = {"product": "Acme Silk Foundation", "top": {"mix": {"lighter": "Sand Beige (Z-046)", "deeper": "WX 64 Honeyed"}}}
        out2 = self.ba.scrub(row2, "zh")
        self.assertEqual(out2["top"]["mix"], {"lighter": "Z-046", "deeper": "WX 64"})

    def test_same_code_inside_one_product_stays_distinguishable(self):
        m = self.ba.shade_map["Acme Lip"]
        self.assertEqual(len(set(m.values())), len(m))
        self.assertEqual((m["Soft Pink (213)"], m["Soft Rose (213)"]), ("213", "213-2"))

    def test_free_text_replaces_compound_names_only(self):
        out = self.ba.scrub_text("建議 Sand Beige (Z-046) 或 WX 64 Honeyed", "zh")
        self.assertEqual(out, "建議 Z-046 或 WX 64")
        self.assertEqual(self.ba.scrub_text("Plum Whisper 是一種說法", "zh"), "Plum Whisper 是一種說法")   # pure shade names are not substituted globally (would hit ordinary wording)

    def test_leaks_flags_real_shade_names_in_structured_fields(self):
        row = {"product": "Acme Lip", "code": "Plum Whisper"}
        self.assertEqual(self.ba.leaks(row), ["Acme", "Acme Lip", "Plum Whisper"])
        self.assertEqual(self.ba.leaks(self.ba.scrub(row, "zh")), [])


class OpaqueStringTests(unittest.TestCase):
    """Red team F2: a brand string that happens by chance inside image base64 must not be corrupted by substitution."""

    def setUp(self):
        self.ba = brand_alias.BrandAlias({"XYZ": "M"}, {})

    def test_data_url_untouched(self):
        s = "data:image/jpeg;base64,AAAA9XYZ+BBBB"
        self.assertEqual(self.ba.scrub_text(s, "zh"), s)
        self.assertEqual(self.ba.leaks({"img": s}), [])

    def test_long_unspaced_blob_untouched_but_short_text_is_scrubbed(self):
        blob = "A" * 3000 + "XYZ" + "B" * 100
        self.assertEqual(self.ba.scrub_text(blob, "zh"), blob)
        self.assertEqual(self.ba.scrub_text("XYZ 的粉底", "zh"), "M 牌 的粉底")

    def test_nested_image_field_untouched(self):
        out = self.ba.scrub({"image": "data:image/png;base64,xxXYZxx", "label": "XYZ"}, "zh")
        self.assertEqual(out, {"image": "data:image/png;base64,xxXYZxx", "label": "M 牌"})


class LoadAllPartnerFilesTests(unittest.TestCase):
    def test_products_from_every_partner_file_are_covered(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "alias.json").write_text(json.dumps({"Acme": "A", "Zeta": "Z"}), encoding="utf-8")
            (td / "a.json").write_text(json.dumps({"products": [{"brand": "Acme", "name": "Acme Base", "category": "foundation",
                                                                 "shades": [{"code": "Sand Beige (Z-046)"}]}]}), encoding="utf-8")
            (td / "b.json").write_text(json.dumps({"products": [{"brand": "Zeta", "name": "Zeta Tint", "category": "lip",
                                                                 "shades": [{"code": "Rose"}]}]}), encoding="utf-8")
            ba = brand_alias.load(td / "alias.json", [td / "a.json", td / "b.json", td / "missing.json"])
            self.assertEqual(ba.scrub_text("Zeta Zeta Tint", "en"), "Brand Z Lip color")
            self.assertEqual(ba.scrub({"product": "Acme Base", "code": "Sand Beige (Z-046)"}, "zh")["code"], "Z-046")

    def test_brand_missing_from_alias_file_fails_loudly(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "alias.json").write_text(json.dumps({"Acme": "A"}), encoding="utf-8")
            (td / "a.json").write_text(json.dumps({"products": [{"brand": "Zeta", "name": "Zeta Tint", "category": "lip", "shades": []}]}),
                                       encoding="utf-8")
            with self.assertRaises(ValueError):
                brand_alias.load(td / "alias.json", [td / "a.json"])


class LocalizerSafetyTests(unittest.TestCase):
    """Red team F1: when a client string is echoed into a response, pattern matching must not be dragged down by a long malicious string."""

    def setUp(self):
        self.loc = i18n_srv.Localizer({"exact": {}, "templates": [["http://{a}:{b} 模式：{c} presets {d}", "x {a} {b} {c} {d}"]]})

    def test_adversarial_string_is_fast(self):
        import time
        for n in (60, 800, 3000):
            s = "http://" + (":" + " 模式：") * n
            t = time.time()
            self.assertEqual(self.loc.tr(s), s)
            self.assertLess(time.time() - t, 0.3, n)

    def test_extremely_long_string_is_returned_unchanged(self):
        s = "很接近" * (i18n_srv.MAX_TOTAL)
        self.assertEqual(self.loc.tr(s), s)

    def test_normal_template_still_works(self):
        self.assertEqual(self.loc.tr("http://h:80 模式：甲 presets 乙"), "x h 80 甲 乙")


if __name__ == "__main__":
    unittest.main()
