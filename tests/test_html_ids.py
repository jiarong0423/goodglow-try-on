"""Every id in web/index.html must be unique: the app looks elements up by id, so a duplicate silently steals another element's text."""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HtmlIdTests(unittest.TestCase):
    def test_ids_are_unique(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'\sid="([^"]+)"', re.sub(r"<!--.*?-->", "", html, flags=re.S))
        dups = [i for i, n in Counter(ids).items() if n > 1]
        self.assertEqual(dups, [])


if __name__ == "__main__":
    unittest.main()
