"""翻面卡片（試妝頁、逐項調整頁）：預設先看你的照片、淡入淡出（只動 opacity）、有「點一下」提示、怕動態的人直接換。
選 C（比較不刺激）、預設原始的、翻開才有驚喜、提示點選。"""
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
            self.assertRegex(HTML, rf'<img id="{p}-original" class="on"')        # 預設顯示你的照片
            self.assertNotRegex(HTML, rf'<img id="{p}-current" class="on"')
            self.assertIn(f'id="btn-{p}-flip"', HTML)
        self.assertNotIn('id="base-compare" class="compare"', HTML)             # 不再並排

    def test_each_page_starts_on_the_photo_and_tapping_an_option_reveals_the_look(self):
        self.assertIn("paintFace('base', 'photo')", JS)
        self.assertIn("paintFace('adj', 'photo')", JS)
        self.assertIn("flipFace('adj', 'look')", JS)                              # 換了選項就翻開
        self.assertIn("b === 'keep' ? 'photo' : 'look'", JS)                       # 選保留原樣就回到你的照片

    def test_hint_and_accessible_button(self):
        self.assertEqual(HTML.count('class="fc-hint"'), 2)
        self.assertIn("aria-pressed", JS[JS.index("function paintFace"):JS.index("function flipFace")])
        self.assertIn('aria-live="polite"', HTML)

    def test_crossfade_only_no_3d_no_motion_for_reduced_motion(self):
        block = CSS[CSS.index("/* 翻面卡片"):CSS.index(".fc-cap {")]
        self.assertIn("transition: opacity", block)
        for bad in ("perspective", "backface-visibility", "rotateY", "scaleX"):
            self.assertNotIn(bad, block)
        self.assertRegex(CSS, r"prefers-reduced-motion: reduce\) \{ \.fc-stage img \{ transition: none; \} \}")


if __name__ == "__main__":
    unittest.main()
