"""Flip card (try-on page, per-item adjustment page): shows your own photo by default, cross-fades (only opacity animates), has a "tap" hint, and switches instantly for people sensitive to motion.
Owner: pick C (less jarring), original by default, a surprise on flip, hint to tap."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web/index.html").read_text(encoding="utf-8")
JS = (ROOT / "web/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web/style.css").read_text(encoding="utf-8")


class FlipCardTests(unittest.TestCase):
    def test_both_pages_use_one_card_with_the_photo_first(self):
        for p in ("base", "adj"):
            self.assertIn(f'id="{p}-stage"', HTML)
            self.assertRegex(HTML, rf'<img id="{p}-original" class="on"')        # shows your photo by default
            self.assertNotRegex(HTML, rf'<img id="{p}-current" class="on"')
            self.assertIn(f'id="btn-{p}-flip"', HTML)
        self.assertNotIn('id="base-compare" class="compare"', HTML)             # no longer side by side

    def test_each_page_starts_on_the_photo_and_tapping_an_option_reveals_the_look(self):
        self.assertIn("paintFace('base', 'photo')", JS)
        self.assertIn("paintFace('adj', 'photo')", JS)
        self.assertIn("flipFace('adj', 'look')", JS)                              # changing the option flips it open
        self.assertIn("b === 'keep' ? 'photo' : 'look'", JS)                       # choosing keep-as-is returns to your photo

    def test_hint_and_accessible_button(self):
        self.assertEqual(HTML.count('class="fc-hint"'), 2)
        self.assertIn("aria-pressed", JS[JS.index("function paintFace"):JS.index("function flipFace")])
        self.assertIn('aria-live="polite"', HTML)

    def test_crossfade_only_no_3d_no_motion_for_reduced_motion(self):
        block = CSS[CSS.index("/* Flip card"):CSS.index(".fc-cap {")]
        self.assertIn("transition: opacity", block)
        for bad in ("perspective", "backface-visibility", "rotateY", "scaleX"):
            self.assertNotIn(bad, block)
        self.assertRegex(CSS, r"prefers-reduced-motion: reduce\) \{ \.fc-stage img \{ transition: none; \} \}")


if __name__ == "__main__":
    unittest.main()
