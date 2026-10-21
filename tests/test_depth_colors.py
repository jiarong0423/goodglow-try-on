import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import color_engine as ce  # noqa: E402
import looks  # noqa: E402

P = looks.load_presets()


def blush_of(skin_hex, level="soft", tone="pink_cool"):
    eff = looks.build_effects({"undertone": tone, "blush": level, "skin_hex": skin_hex}, P)
    return next(e for e in eff if e["category"] == "blush")["palettes"][0]


class DepthColorTests(unittest.TestCase):
    def test_deep_skin_blush_deeper_and_stronger_than_pale(self):
        #: on deep skin the three intensities of light pink blush returned the same image -> blush is now banded by skin depth x undertone
        pale, deep = blush_of("#F0E0DA"), blush_of("#5E3A2E")
        self.assertGreater(ce.hex_to_lab(pale["color"])[0], ce.hex_to_lab(deep["color"])[0] + 20)
        self.assertGreater(deep["colorIntensity"], pale["colorIntensity"])

    def test_blush_levels_keep_order_within_band(self):
        flush, soft, medium = (blush_of("#A47568", lv)["colorIntensity"] for lv in ("flush", "soft", "medium"))
        self.assertLess(flush, soft)
        self.assertLess(soft, medium)

    def test_no_skin_falls_back_to_undertone_color(self):
        eff = looks.build_effects({"undertone": "pink_cool", "blush": "soft"}, P)
        self.assertEqual(next(e for e in eff if e["category"] == "blush")["palettes"][0]["color"],
                         P["undertones"]["pink_cool"]["blush"])

    def test_fixed_color_lips_not_rescaled(self):
        eff = looks.build_effects({"undertone": "neutral", "lip": "red2", "skin_hex": "#5E3A2E"}, P)
        pal = next(e for e in eff if e["category"] == "lip_color")["palettes"][0]
        self.assertEqual(pal["color"], "#A31930")


class RednessTests(unittest.TestCase):
    def test_pink_skin_shifts_foundation_hue_toward_yellow(self):
        f = "#E8C4B8"
        out = looks.neutralize_redness(f, "#E3CDC6")      # albinism male cheek, hue about 25°
        h = lambda x: math.degrees(math.atan2(ce.hex_to_lab(x)[2], ce.hex_to_lab(x)[1]))  # noqa: E731
        self.assertGreater(h(out), h(f) + 2)

    def test_warm_skin_unchanged(self):
        self.assertEqual(looks.neutralize_redness("#E8C4B8", "#D2B48C"), "#E8C4B8")


class NewOptionTests(unittest.TestCase):
    def test_fine_black_lash_in_natural_and_polished_not_grooming(self):
        opts = P["styles"]
        self.assertIn("fine_black", opts["natural"]["options"]["lash"])
        self.assertIn("fine_black", opts["polished"]["options"]["lash"])
        self.assertNotIn("fine_black", opts["grooming"]["options"]["lash"])

    def test_nude_balm_in_grooming(self):
        self.assertIn("nude_balm", P["styles"]["grooming"]["options"]["lip"])
        eff = looks.build_effects({"undertone": "neutral", "lip": "nude_balm"}, P)
        self.assertEqual(next(e for e in eff if e["category"] == "lip_color")["palettes"][0]["color"], "#D99B9B")


if __name__ == "__main__":
    unittest.main()
