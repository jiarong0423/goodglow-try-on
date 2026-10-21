import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import brow_card as bc  # noqa: E402
import color_engine as ce  # noqa: E402
import recommend as rec  # noqa: E402


class BrowTagTests(unittest.TestCase):
    def test_brand_words_win_over_color(self):
        # 品牌說明寫 warm 就算暖（即使同時寫 ash）；只寫 ash／grey 才算冷灰
        self.assertEqual(bc.tag("#9E755C", "Sample Warm Blonde, warm ash-blonde tone")["family"], "金")
        self.assertEqual(bc.tag("#998477", "Sample Grey Blonde, yellowy ashy grey")["family"], "冷灰褐")
        self.assertEqual(bc.tag("#151515", "Onyx")["family"], "黑")
        self.assertEqual(bc.tag("#151515")["level"], 6)

    def test_pale_ok_excludes_black_and_deep(self):
        self.assertTrue(bc.tag("#998B86")["pale_ok"])          # 淺灰褐（taupe）可以
        self.assertFalse(bc.tag("#2D2A2A")["pale_ok"])         # 黑不行
        self.assertFalse(bc.tag("#584A43", "Taupe")["pale_ok"])  # 第 4 級深灰褐不行


class BrowTargetTests(unittest.TestCase):
    def test_pale_skin_light_brows_get_gold_or_light_taupe_never_black(self):
        for strength in ("soft", "defined"):
            t = bc.target("#CBB4A8", "#E1CDC6", strength)
            self.assertFalse(t["visible"])
            self.assertLessEqual(t["level"], 3)
            self.assertNotEqual(t["family"], "黑")
        self.assertEqual(bc.target("#CBB4A8", "#E1CDC6", "soft")["family"], "金")

    def test_dark_visible_brows_keep_own_color_never_lighter(self):
        soft, defined = bc.target("#1B1B1B", "#B38071", "soft"), bc.target("#1B1B1B", "#B38071", "defined")
        self.assertTrue(soft["visible"])
        self.assertEqual(soft["hex"], "#1B1B1B")
        self.assertLessEqual(ce.hex_to_lab(defined["hex"])[0], ce.hex_to_lab("#1B1B1B")[0])

    def test_black_brows_match_black_pencils(self):
        ms = ce.brow_matches("#1B1B1B", "all")
        self.assertTrue(ms and all("黑" in m["tag"] for m in ms))
        self.assertEqual(ms[0]["fit"], "同深淺、同色系")


class FullLookBrowTests(unittest.TestCase):
    def test_full_look_never_lightens_dark_brows(self):
        # ：黑眉毛不能被完整妝容換成淺金棕／灰褐
        for style in ("grooming", "natural", "polished"):
            r = rec.recommend("vitiligo", style, normal_hex="#B38071", patch_hex="#B49B96", brow_hex="#1B1B1B")
            b = r["full_look"]["selection"]["brow"]
            self.assertIn(b, ("keep", "photo_soft", "photo_defined"), style)

    def test_albinism_full_look_brows_stay_light(self):
        # ：完整妝容不上眉（官方抓不準白眉位置）；眉色卡參考色仍不給黑
        for style in ("natural", "polished"):
            r = rec.recommend("albinism", style, skin_hex="#E1CDC6", brow_hex="#CBB4A8")
            self.assertEqual(r["full_look"]["selection"]["brow"], "keep", style)
            self.assertFalse([x for x in r["full_look"]["shades"] if x["item"] == "brow"])
            for level in ("soft", "defined"):
                self.assertNotIn("黑", r["brow"][level]["tag"]["label"])


if __name__ == "__main__":
    unittest.main()
