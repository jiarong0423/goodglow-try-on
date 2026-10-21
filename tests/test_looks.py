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
        self.assertIn("transparencyIntensity", lip["palettes"][0])  # gloss 必填欄位

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
        self.assertEqual([e["category"] for e in effects][:2], ["skin_smooth", "foundation"])   # 先底妝再其他
        pal = effects[1]["palettes"][0]
        self.assertEqual(pal["color"], "#C79A79")
        self.assertEqual(set(pal), {"color", "colorIntensity", "glowIntensity", "coverageIntensity"})  # 官方 schema
        self.assertNotIn("pattern", effects[1])
        self.assertEqual(self.p["foundation"][0]["id"], "keep")
        # 好氣色覆蓋：有最接近膚色的色號時，快速兩款都先上底妝；沒有色號就不上
        # 白化症（預設 condition）用「輕薄」保留膚質
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
        self.assertEqual(steps[0][1].get("blush", "keep"), "keep")              # 第一層只有底妝
        self.assertEqual(steps[-1][1]["brow"], "taupe")                          # 最後一層＝整套
        self.assertEqual(steps[-1][1]["foundation_hex"], "#C79A79")

    def test_vitiligo_quick_looks_use_sheer_glow_foundation_toward_skin(self):
        # 白斑靠局部遮瑕蓋住，全臉底妝只做色調統一、保留水光
        import color_engine as ce
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#8B5739",
                                        skin_hex="#946D60", condition="vitiligo")
            self.assertEqual(sel["foundation"], "veil", style)
            f = looks.build_effects(sel, self.p)[1]["palettes"][0]
            self.assertGreaterEqual(f["glowIntensity"], 35)
            self.assertLessEqual(f["coverageIntensity"], 32)
            # 試妝顏色往周圍膚色靠：比色號本身更接近 #946D60（色號推薦不變）
            skin = ce.hex_to_lab("#946D60")
            self.assertLess(ce.delta_e2000(ce.hex_to_lab(f["color"]), skin), ce.delta_e2000(ce.hex_to_lab("#8B5739"), skin))
            self.assertEqual(sel["foundation_hex"], "#8B5739")
        # 沒有周圍膚色時照色號
        sel = looks.quick_selection("natural", "glow", "neutral", self.p, foundation_hex="#8B5739", condition="vitiligo")
        self.assertEqual(looks.build_effects(sel, self.p)[1]["palettes"][0]["color"], "#8B5739")

    def test_max_coverage_adds_concealer_with_official_fields(self):
        effects = looks.build_effects({"foundation": "max", "foundation_hex": "#8B5739"}, self.p)
        cats = [e["category"] for e in effects]
        self.assertEqual(cats, ["skin_smooth", "foundation", "concealer"])
        f, c = effects[1]["palettes"][0], effects[2]["palettes"][0]
        self.assertEqual((f["colorIntensity"], f["coverageIntensity"]), (100, 100))
        self.assertEqual(set(c), {"color", "colorIntensity", "colorUnderEyeIntensity", "coverageLevel"})   # 官方 schema
        self.assertEqual(c["color"], "#8B5739")

    def test_center_highlight_after_foundation_with_face_shape_pattern(self):
        sel = {"foundation": "medium", "foundation_hex": "#C79A79", "highlight": "center", "blush": "soft"}
        effects = looks.build_effects(sel, self.p)
        self.assertEqual([e["category"] for e in effects][:4], ["skin_smooth", "foundation", "highlighter", "blush"])
        hl = effects[2]
        self.assertEqual(hl["pattern"]["name"], "OvalFace2")                     # 預設：T 字＋面中
        self.assertEqual(set(hl["palettes"][0]), {"color", "colorIntensity", "glowIntensity", "shimmerIntensity",
                                                  "shimmerDensity", "shimmerSize"})   # 官方 schema
        import color_engine as ce
        self.assertGreater(ce.hex_to_lab(hl["palettes"][0]["color"])[0], ce.hex_to_lab("#C79A79")[0])   # 比底妝淺
        sq = looks.build_effects(dict(sel, face_shape="Square"), self.p)[2]
        self.assertEqual(sq["pattern"]["name"], "SquareFace5")
        self.assertEqual([i for i, _ in looks.layers(sel)], ["foundation", "highlight", "blush"])

    def test_albinism_quick_looks_add_contour_nose_highlight_and_brow(self):
        # 白化症「讓五官立體改善氣色」→ 修容＋鼻樑微光＋補眉；灰褐、不用古銅
        import color_engine as ce
        want = {"grooming": "soft", "natural": "soft", "polished": "defined"}
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#F1CCB1", condition="albinism")
            self.assertEqual((sel["contour"], sel["highlight"], sel["foundation"]), (want[style], "nose", "light"), style)
            self.assertEqual(sel["brow"], "fill_light", style)                     # 沒有照片眉色：退回固定淺色
            looks.check_style_selection(style, sel, self.p)
            effects = looks.build_effects(sel, self.p)
            cats = [e["category"] for e in effects]
            self.assertEqual(cats[:4], ["skin_smooth", "foundation", "contour", "highlighter"])   # 先修容再打亮
            self.assertEqual(effects[3]["pattern"]["name"], "OvalFace38")            # 只留鼻樑，避開背光側顴骨
            self.assertEqual(effects[2]["pattern"]["name"], "HeartFace2")            # 大面積含下顎（外部建議）
            self.assertGreaterEqual(effects[2]["palettes"][0]["colorIntensity"], 65)
            con = effects[2]["palettes"][0]
            self.assertEqual(set(con), {"color", "colorIntensity"})                 # 官方 schema
            L, a, b = ce.hex_to_lab(con["color"])
            fL, fa, fb = ce.hex_to_lab("#F1CCB1")
            self.assertLess(L, fL - 30)                                              # 比底妝暗 30 以上（L −24 會隱形）
            self.assertLess((a * a + b * b) ** 0.5, (fa * fa + fb * fb) ** 0.5 * 0.9)  # 彩度收掉、不變古銅
        self.assertEqual([i for i, _ in looks.layers(sel)][:3], ["foundation", "contour", "highlight"])
        # 有照片眉色時改照原眉色
        sel = looks.quick_selection("natural", "glow", "neutral", self.p, brow_hex="#A18372", skin_hex="#CDB5AA",
                                    foundation_hex="#F1CCB1", condition="albinism")
        self.assertEqual(sel["brow"], "photo_defined")                         # 照原眉色深一階（外部建議）
        self.assertEqual(sel["blush"], "apricot")
        effects = looks.build_effects(sel, self.p)
        self.assertEqual(next(e for e in effects if e["category"] == "blush")["palettes"][0]["color"], "#C48B7B")
        self.assertEqual(next(e for e in effects if e["category"] == "eyebrows")["palettes"][0]["color"], "#917463")
        # 底妝試妝色往取色膚色靠（N4.5 偏黃橘）
        f = next(e for e in effects if e["category"] == "foundation")["palettes"][0]
        self.assertLess(ce.delta_e2000(ce.hex_to_lab(f["color"]), ce.hex_to_lab("#CDB5AA")),
                        ce.delta_e2000(ce.hex_to_lab("#F1CCB1"), ce.hex_to_lab("#CDB5AA")))
        self.assertEqual((f["colorIntensity"], f["coverageIntensity"]), (25, 22))
        g = looks.quick_selection("grooming", "glow", "neutral", self.p, brow_hex="#A18372", skin_hex="#CDB5AA",
                                  foundation_hex="#F1CCB1", condition="albinism")
        self.assertEqual((g["brow"], g.get("blush", "keep")), ("photo_soft", "keep"))   # 理容：補一點、不加腮紅
        # 沒有底妝色號：不上底妝，也不換修容
        self.assertEqual(looks.quick_selection("natural", "glow", "neutral", self.p).get("contour", "keep"), "keep")

    def test_vitiligo_quick_looks_use_tzone_highlight_without_shimmer(self):
        # 白斑症「覆蓋白斑、打亮 T 字」；不加珠光，免得 T 字上的白斑更明顯
        for style in self.p["styles"]:
            sel = looks.quick_selection(style, "glow", "neutral", self.p, foundation_hex="#A77557", condition="vitiligo")
            self.assertEqual(sel["highlight"], "tzone", style)
            self.assertEqual(sel.get("contour", "keep"), "keep", style)
            hl = next(e for e in looks.build_effects(sel, self.p) if e["category"] == "highlighter")
            self.assertEqual(hl["pattern"]["name"], "OvalFace61")
            self.assertEqual(hl["palettes"][0]["shimmerIntensity"], 0)
            self.assertGreaterEqual(hl["palettes"][0]["glowIntensity"], hl["palettes"][0]["colorIntensity"] * 2.5)   # 顏色少、光澤多

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
