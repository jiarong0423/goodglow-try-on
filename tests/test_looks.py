import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import looks  # noqa: E402


class LooksTests(unittest.TestCase):
    def setUp(self):
        self.p = looks.load_presets()

    def test_every_item_starts_with_keep(self):
        for item in ("lip", "brow", "lash", "contour"):
            self.assertEqual(self.p[item][0]["id"], "keep", item)
        self.assertEqual(self.p["blush"]["levels"][0]["id"], "keep")

    def test_empty_selection_is_only_zero_smoothing(self):
        effects = looks.build_effects({})
        self.assertEqual(effects, [{"category": "skin_smooth", "skinSmoothStrength": 0, "skinSmoothColorIntensity": 0}])

    def test_glow_uses_undertone_colors(self):
        effects = looks.build_effects({"undertone": "pink_cool", "blush": "soft", "lip": "natural_gloss"})
        blush = next(e for e in effects if e["category"] == "blush")
        lip = next(e for e in effects if e["category"] == "lip_color")
        self.assertEqual(blush["palettes"][0]["color"], self.p["undertones"]["pink_cool"]["blush"])
        soft = next(lv for lv in self.p["blush"]["levels"] if lv["id"] == "soft")
        self.assertEqual(blush["palettes"][0]["colorIntensity"], soft["intensity"])
        self.assertEqual(lip["palettes"][0]["color"], self.p["undertones"]["pink_cool"]["lip"])
        self.assertIn("transparencyIntensity", lip["palettes"][0])  # required field for gloss

    def test_satin_and_matte_have_no_gloss_fields(self):
        for lip_id in ("red2", "red3"):
            lip = next(e for e in looks.build_effects({"lip": lip_id}) if e["category"] == "lip_color")
            self.assertNotIn("gloss", lip["palettes"][0])

    def test_unknown_option_rejected(self):
        with self.assertRaises(looks.PresetError):
            looks.build_effects({"brow": "jet_black"})
        with self.assertRaises(looks.PresetError):
            looks.build_effects({"undertone": "olive"})

    def test_foundation_goes_first_with_matched_shade(self):
        effects = looks.build_effects({"foundation": "medium", "foundation_hex": "#c79a79", "lip": "tinted_balm"})
        self.assertEqual([e["category"] for e in effects][:2], ["skin_smooth", "foundation"])   # foundation first, then the rest
        pal = effects[1]["palettes"][0]
        self.assertEqual(pal["color"], "#C79A79")
        self.assertEqual(set(pal), {"color", "colorIntensity", "glowIntensity", "coverageIntensity"})  # official schema
        self.assertNotIn("pattern", effects[1])
        self.assertEqual(self.p["foundation"][0]["id"], "keep")
        # good-complexion coverage : when there is a nearest-skin shade, both quick looks apply foundation first; without a shade, no foundation
        # albinism (default condition) uses "sheer" to keep skin texture
        want = {"grooming": "light", "natural": "light", "polished": "light"}
        for q in ("glow", "clear"):
            for style in self.p["styles"]:
                sel = looks.quick_selection(style, q, "neutral", self.p, foundation_hex="#C79A79")
                self.assertEqual(sel["foundation"], want[style], (style, q))
                self.assertEqual(looks.build_effects(sel, self.p)[1]["palettes"][0]["color"], "#C79A79")
                self.assertEqual(looks.quick_selection(style, q, "neutral", self.p).get("foundation", "keep"), "keep")

    def test_layers_stack_in_order_and_skip_keep(self):
        sel = {"foundation": "medium", "foundation_hex": "#C79A79", "blush": "soft", "lip": "keep",
               "brow": "taupe", "lash": "keep", "undertone": "neutral"}
        steps = looks.layers(sel)
        self.assertEqual([item for item, _ in steps], ["foundation", "blush", "brow"])
        self.assertEqual(steps[0][1].get("blush", "keep"), "keep")              # the first layer is foundation only
        self.assertEqual(steps[-1][1]["brow"], "taupe")                          # the last layer = the full set
        self.assertEqual(steps[-1][1]["foundation_hex"], "#C79A79")

    def test_vitiligo_quick_looks_use_sheer_glow_foundation_toward_skin(self):
        # vitiligo is covered by local concealer; the full-face foundation only evens the tone and keeps the dewy finish
        import color_engine as ce
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#8B5739",
                                        skin_hex="#946D60", condition="vitiligo")
            self.assertEqual(sel["foundation"], "veil", style)
            f = looks.build_effects(sel, self.p)[1]["palettes"][0]
            self.assertGreaterEqual(f["glowIntensity"], 35)
            self.assertLessEqual(f["coverageIntensity"], 32)
            # try-on color shifts toward the surrounding skin: closer to #946D60 than the shade itself (shade recommendation unchanged)
            skin = ce.hex_to_lab("#946D60")
            self.assertLess(ce.delta_e2000(ce.hex_to_lab(f["color"]), skin), ce.delta_e2000(ce.hex_to_lab("#8B5739"), skin))
            self.assertEqual(sel["foundation_hex"], "#8B5739")
        # with no surrounding skin color, follow the shade
        sel = looks.quick_selection("natural", "glow", "neutral", self.p, foundation_hex="#8B5739", condition="vitiligo")
        self.assertEqual(looks.build_effects(sel, self.p)[1]["palettes"][0]["color"], "#8B5739")

    def test_max_coverage_adds_concealer_with_official_fields(self):
        effects = looks.build_effects({"foundation": "max", "foundation_hex": "#8B5739"}, self.p)
        cats = [e["category"] for e in effects]
        self.assertEqual(cats, ["skin_smooth", "foundation", "concealer"])
        f, c = effects[1]["palettes"][0], effects[2]["palettes"][0]
        self.assertEqual((f["colorIntensity"], f["coverageIntensity"]), (100, 100))
        self.assertEqual(set(c), {"color", "colorIntensity", "colorUnderEyeIntensity", "coverageLevel"})   # official schema
        self.assertEqual(c["color"], "#8B5739")

    def test_center_highlight_after_foundation_with_face_shape_pattern(self):
        sel = {"foundation": "medium", "foundation_hex": "#C79A79", "highlight": "center", "blush": "soft"}
        effects = looks.build_effects(sel, self.p)
        self.assertEqual([e["category"] for e in effects][:4], ["skin_smooth", "foundation", "highlighter", "blush"])
        hl = effects[2]
        self.assertEqual(hl["pattern"]["name"], "OvalFace2")                     # default: T-zone + mid-face
        self.assertEqual(set(hl["palettes"][0]), {"color", "colorIntensity", "glowIntensity", "shimmerIntensity",
                                                  "shimmerDensity", "shimmerSize"})   # official schema
        import color_engine as ce
        self.assertGreater(ce.hex_to_lab(hl["palettes"][0]["color"])[0], ce.hex_to_lab("#C79A79")[0])   # lighter than the foundation
        sq = looks.build_effects(dict(sel, face_shape="Square"), self.p)[2]
        self.assertEqual(sq["pattern"]["name"], "SquareFace5")
        self.assertEqual([i for i, _ in looks.layers(sel)], ["foundation", "highlight", "blush"])

    def test_albinism_quick_looks_add_contour_nose_highlight_and_brow(self):
        # owner: albinism "define features to improve complexion" -> contour + nose-bridge glow + brow fill; grey-brown, no bronze
        import color_engine as ce
        want = {"grooming": "soft", "natural": "soft", "polished": "defined"}
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#F1CCB1", condition="albinism")
            self.assertEqual((sel["contour"], sel["highlight"], sel["foundation"]), (want[style], "nose", "light"), style)
            self.assertEqual(sel["brow"], "fill_light", style)                     # no photo brow color: fall back to a fixed light shade
            looks.check_style_selection(style, sel, self.p)
            effects = looks.build_effects(sel, self.p)
            cats = [e["category"] for e in effects]
            self.assertEqual(cats[:4], ["skin_smooth", "foundation", "contour", "highlighter"])   # contour first, then highlight
            self.assertEqual(effects[3]["pattern"]["name"], "OvalFace38")            # keep only the nose bridge, avoid the cheekbone on the backlit side
            self.assertEqual(effects[2]["pattern"]["name"], "HeartFace2")            # large area including the jaw (外部建議round 3)
            self.assertGreaterEqual(effects[2]["palettes"][0]["colorIntensity"], 65)
            con = effects[2]["palettes"][0]
            self.assertEqual(set(con), {"color", "colorIntensity"})                 # official schema
            L, a, b = ce.hex_to_lab(con["color"])
            fL, fa, fb = ce.hex_to_lab("#F1CCB1")
            self.assertLess(L, fL - 30)                                              # darker than the foundation by 30 or more (L -24 would be invisible)
            self.assertLess((a * a + b * b) ** 0.5, (fa * fa + fb * fb) ** 0.5 * 0.9)  # chroma pulled in, does not turn bronze
        self.assertEqual([i for i, _ in looks.layers(sel)][:3], ["foundation", "contour", "highlight"])
        # with a photo brow color, follow the original brow color
        sel = looks.quick_selection("natural", "glow", "neutral", self.p, brow_hex="#A18372", skin_hex="#CDB5AA",
                                    foundation_hex="#F1CCB1", condition="albinism")
        self.assertEqual(sel["brow"], "photo_defined")                         # one step deeper than the original brow color (外部建議round 3)
        self.assertEqual(sel["blush"], "apricot")
        effects = looks.build_effects(sel, self.p)
        self.assertEqual(next(e for e in effects if e["category"] == "blush")["palettes"][0]["color"], "#C48B7B")
        self.assertEqual(next(e for e in effects if e["category"] == "eyebrows")["palettes"][0]["color"], "#917463")
        # the foundation try-on color shifts toward the sampled skin color (N4.5 is yellow-orange)
        f = next(e for e in effects if e["category"] == "foundation")["palettes"][0]
        self.assertLess(ce.delta_e2000(ce.hex_to_lab(f["color"]), ce.hex_to_lab("#CDB5AA")),
                        ce.delta_e2000(ce.hex_to_lab("#F1CCB1"), ce.hex_to_lab("#CDB5AA")))
        self.assertEqual((f["colorIntensity"], f["coverageIntensity"]), (25, 22))
        g = looks.quick_selection("grooming", "glow", "neutral", self.p, brow_hex="#A18372", skin_hex="#CDB5AA",
                                  foundation_hex="#F1CCB1", condition="albinism")
        self.assertEqual((g["brow"], g.get("blush", "keep")), ("photo_soft", "keep"))   # grooming: fill slightly, no added blush
        # no foundation shade: no foundation, and the contour is not swapped either
        self.assertEqual(looks.quick_selection("natural", "glow", "neutral", self.p).get("contour", "keep"), "keep")

    def test_vitiligo_quick_looks_use_tzone_highlight_without_shimmer(self):
        # owner: vitiligo "cover the patches, highlight the T-zone"; no shimmer, so the patches on the T-zone do not stand out more
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#A77557", condition="vitiligo")
            self.assertEqual(sel["highlight"], "tzone", style)
            self.assertEqual(sel.get("contour", "keep"), "keep", style)
            hl = next(e for e in looks.build_effects(sel, self.p) if e["category"] == "highlighter")
            self.assertEqual(hl["pattern"]["name"], "OvalFace61")
            self.assertEqual(hl["palettes"][0]["shimmerIntensity"], 0)
            self.assertGreaterEqual(hl["palettes"][0]["glowIntensity"], hl["palettes"][0]["colorIntensity"] * 2.5)   # little color, more gloss

    def test_foundation_needs_matched_shade(self):
        with self.assertRaises(looks.PresetError):
            looks.build_effects({"foundation": "sheer"})
        with self.assertRaises(looks.PresetError):
            looks.build_effects({"foundation": "sheer", "foundation_hex": "#GGGGGG"})

    def test_styles_reference_real_options_and_start_with_keep(self):
        ids = {item: {o["id"] for o in (self.p["blush"]["levels"] if item == "blush" else self.p[item])}
               for item in looks.ITEMS}
        for style, s in self.p["styles"].items():
            for item in looks.ITEMS:
                self.assertEqual(s["options"][item][0], "keep", (style, item))
                self.assertTrue(set(s["options"][item]) <= ids[item], (style, item))
            for which in ("glow", "clear"):
                looks.check_style_selection(style, looks.quick_selection(style, which, "neutral"))

    def test_red_lips_only_in_polished_and_never_in_quick_looks(self):
        reds = {"red1", "red2", "red3"}
        for style, s in self.p["styles"].items():
            has_red = bool(reds & set(s["options"]["lip"]))
            self.assertEqual(has_red, style == "polished", style)
            for which in ("glow", "clear"):
                self.assertNotIn(s["quick"][which].get("lip"), reds)
        with self.assertRaises(looks.PresetError):
            looks.check_style_selection("natural", {"lip": "red1"})

    def test_grooming_quick_looks_have_no_blush_or_gloss(self):
        for which in ("glow", "clear"):
            sel = looks.quick_selection("grooming", which, "neutral")
            self.assertEqual(sel.get("blush", "keep"), "keep")
            self.assertNotEqual(sel.get("lip"), "natural_gloss")

    def test_balance_hint_only_for_deep_red(self):
        self.assertIsNotNone(looks.balance_hint({"lip": "red3"}))
        self.assertIsNone(looks.balance_hint({"lip": "red3", "brow": "taupe_deep"}))
        self.assertIsNone(looks.balance_hint({"lip": "red1"}))


if __name__ == "__main__":
    unittest.main()
