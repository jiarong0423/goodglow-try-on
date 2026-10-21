import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import color_engine as ce  # noqa: E402


class FaceShadeTests(unittest.TestCase):
    def test_derived_zones_lighter_highlight_deeper_contour(self):
        z = ce.derive_zones("#DDC9C0")
        L = ce.hex_to_lab("#DDC9C0")[0]
        self.assertGreater(ce.hex_to_lab(z["highlight"]["hex"])[0], L)
        self.assertLess(ce.hex_to_lab(z["contour"]["hex"])[0], L - 10)

    def test_concealer_matches_concealer_category(self):
        rows = ce.face_shades("#DDC9C0", None, "cool", "demo", ("concealer",))
        self.assertEqual(rows[0]["category"], "concealer")
        self.assertTrue(rows[0]["matches"])

    def test_pale_contour_without_close_match_gives_swatch_and_warning(self):
        #：極淺膚色的灰褐修容，型錄粉底不是偏橘就是太淺 → 不硬推色號
        row = ce.face_shades("#DDC9C0", None, None, "official" if ce.OFFICIAL_CATALOG.exists() else "demo", ("contour",))[0]
        if not row["matches"]:
            self.assertIn("灰褐", row["warn"])
        else:
            self.assertLessEqual(row["matches"][0]["delta_e"], 8)

    def test_expected_zones_used_when_given(self):
        zones = {"highlight": {"hex": "#B78676"}, "contour": {"hex": "#926251"}}
        rows = ce.face_shades("#B38071", zones, "cool", "demo", ("highlight", "contour"))
        self.assertEqual([r["hex"] for r in rows], ["#B78676", "#926251"])
        self.assertTrue(all(r["basis"].startswith("預期妝容") for r in rows))


if __name__ == "__main__":
    unittest.main()
