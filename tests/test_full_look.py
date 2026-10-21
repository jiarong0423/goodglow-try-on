import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import full_look as fl  # noqa: E402
import looks  # noqa: E402
import recommend as rec  # noqa: E402

P = looks.load_presets()


class FullLookTests(unittest.TestCase):
    def test_every_base_look_is_allowed_for_its_style(self):
        for cond in ("albinism", "vitiligo"):
            for style in ("grooming", "natural", "polished"):
                out = fl.full_look(cond, style, 5, "neutral", None, True, P)
                looks.check_style_selection(style, out["selection"], P)
                self.assertTrue(out["why"])

    def test_red_lip_only_in_polished(self):
        for cond in ("albinism", "vitiligo"):
            for style in ("grooming", "natural"):
                lip = fl.full_look(cond, style, 5, "neutral", None, True, P)["selection"]["lip"]
                self.assertNotIn(lip, ("red1", "red2", "red3"), (cond, style))

    def test_very_pale_skin_softens_contour_blush_and_lash(self):
        out = fl.full_look("vitiligo", "polished", 2, "neutral", None, True, P)["selection"]
        self.assertEqual(out["contour"], "soft")
        self.assertEqual(out["blush"], "soft")
        self.assertNotEqual(out["brow"], "taupe_deep")

    def test_deep_vitiligo_polished_keeps_defined_contour_and_deep_red(self):
        out = fl.full_look("vitiligo", "polished", 8, "neutral", None, True, P)["selection"]
        self.assertEqual(out["contour"], "defined")
        self.assertEqual(out["lip"], "red3")

    def test_high_sensitivity_downgrades_and_explains(self):
        out = fl.full_look("vitiligo", "polished", 8, "neutral", {"irritation": "often"}, True, P)
        s = out["selection"]
        self.assertTrue(out["high_sensitivity"])
        self.assertEqual(s["foundation"], "medium")
        self.assertEqual(s["contour"], "keep")
        self.assertEqual(s["highlight"], "nose")
        self.assertNotEqual(s["lip"], "red3")
        self.assertTrue(out["adjustments"])
        looks.check_style_selection("polished", s, P)

    def test_no_brow_sample_falls_back_from_photo_brow(self):
        s = fl.full_look("albinism", "natural", 2, "pink_cool", None, False, P)["selection"]
        self.assertEqual(s["brow"], "fill_light")

    def test_pale_brow_kept_as_is(self):
        #: the official try-on draws brows above the original brows for white brows -> when brows are very faint, the full look never adds brows
        for style in ("grooming", "natural", "polished"):
            out = fl.full_look("albinism", style, 2, "pink_cool", None, True, P, brow_visible=False)
            self.assertEqual(out["selection"]["brow"], "keep", style)
            self.assertTrue(out["brow_pale"])
        r = rec.recommend("albinism", "polished", skin_hex="#DDC9C0", brow_hex="#C8B4A9")   # measured shade on the albinism demo photo
        self.assertEqual(r["full_look"]["selection"]["brow"], "keep")

    def test_visible_brow_still_gets_brow(self):
        r = rec.recommend("vitiligo", "polished", normal_hex="#9E7663", patch_hex="#AF8A81", brow_hex="#1D1D1E")
        self.assertNotEqual(r["full_look"]["selection"]["brow"], "keep")
        self.assertFalse(r["full_look"]["brow_pale"])

    def test_vitiligo_steps_include_local_correction_albinism_does_not(self):
        titles = lambda c: [x["title"] for x in fl.full_look(c, "natural", 5, "neutral", None, True, P)["steps"]]  # noqa: E731
        self.assertIn("局部校色", titles("vitiligo"))
        self.assertNotIn("局部校色", titles("albinism"))

    def test_recommend_albinism_photo_brow_full_look(self):
        # 10-16 caught in browser testing: albinism natural look "fill in slightly following the original brow color" computed the shade without the brow color -> 400
        for style in ("grooming", "natural", "polished"):
            r = rec.recommend("albinism", style, skin_hex="#D2B8AC", brow_hex="#B49A86")
            self.assertIn("full_look", r, style)

    def test_recommend_returns_full_look_with_level(self):
        r = rec.recommend("vitiligo", "natural", normal_hex="#936A5C", patch_hex="#B69F9A",
                          sensitivity={"fragrance": "yes"})
        self.assertIn("full_look", r)
        self.assertTrue(r["full_look"]["high_sensitivity"])
        self.assertIsInstance(r["skin_level"], int)


if __name__ == "__main__":
    unittest.main()
