"""Completeness tests for the Chinese/English toggle (stage 1: foundation + static text in index.html).
English is taken verbatim from docs/i18n/zh_en_map.csv, not rewritten; the zero-regression screen snapshots for Chinese are compared separately in the browser (see docs/i18n/APP_I18N_PLAN_20261021.md).
"""
import csv
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_EN_SHA = "74a809fd9b6ef190b7fff15cc6cf88cfd14f973045ac08dbf82f0e04565ff79f"   # per-row sha256 of id<TAB>en (946 rows)
CJK = re.compile(r"[㐀-鿿]")


def run(*args):
    return subprocess.run([sys.executable, "-I", "-B", *args], cwd=ROOT, capture_output=True, text=True)


class TestTable(unittest.TestCase):
    def setUp(self):
        self.rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))

    def test_en_column_hash_and_count(self):
        self.assertEqual(len(self.rows), 946)
        sha = hashlib.sha256("\n".join(f"{r['id']}\t{r['en']}" for r in self.rows).encode()).hexdigest()
        self.assertEqual(sha, CSV_EN_SHA)

    def test_every_row_has_exactly_five_columns(self):
        # An English cell with an unquoted comma is split into extra columns and its sentence is cut short on screen.
        raw = list(csv.reader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        self.assertEqual({len(r) for r in raw}, {5})

    def test_every_row_has_english_except_unit_word(self):
        blank = [r["id"] for r in self.rows if not r["en"].strip()]
        self.assertEqual(blank, ["s0008"])        # the unit character has no English counterpart, intentionally left blank

    def test_no_chinese_left_in_english_except_code_key(self):
        left = [r["id"] for r in self.rows if CJK.search(r["en"])]
        self.assertEqual(left, ["s0889", "s0898", "s0899", "s0900", "s0901"])   # regular expressions used for program matching (including Chinese aliases), not on-screen text


class TestBuild(unittest.TestCase):
    def test_dictionary_is_in_sync_with_csv(self):
        r = run("tools/build_i18n.py", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(CSV_EN_SHA, r.stdout)

    def test_all_html_strings_are_annotated(self):
        r = run("tools/i18n_annotate_html.py", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestHtml(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.en = (ROOT / "web/i18n/en.js").read_text(encoding="utf-8")

    def keys(self):
        ids = set(re.findall(r'data-i18n="([^"]+)"', self.html))
        for attr in re.findall(r'data-i18n-attr="([^"]+)"', self.html):
            ids.update(p.split(":", 1)[1] for p in attr.split(";"))
        return ids

    def test_every_annotated_key_exists_in_dictionary(self):
        missing = [k for k in sorted(self.keys()) if f'"{k}":' not in self.en]
        self.assertEqual(missing, [])

    def test_position_override_is_used(self):
        self.assertIn("s0015@web/index.html:73", self.keys())
        self.assertIn('"s0015@web/index.html:73": "Take photo"', self.en)
        self.assertIn('"s0015": "Photo"', self.en)

    def test_scripts_and_toggle_present_in_order(self):
        a, b, c = (self.html.index(s) for s in ('src="i18n/en.js"', 'src="i18n.js"', 'src="app.js"'))
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertIn('id="lang-toggle"', self.html)

    def test_service_worker_caches_i18n_files(self):
        sw = (ROOT / "web/sw.js").read_text(encoding="utf-8")
        self.assertIn("'/i18n.js'", sw)
        self.assertIn("'/i18n/en.js'", sw)

    def test_speech_feature_is_removed(self):
        app = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertNotIn("speechSynthesis", app)
        self.assertNotIn("opt-voice", (ROOT / "web/index.html").read_text(encoding="utf-8"))


JS_FILES = ["web/app.js", "web/capture_guide.js", "web/makeup_layers.js", "web/camo_editor.js", "web/shade_preview.js"]


class TestJsWrapping(unittest.TestCase):
    """Stage 2: all Chinese strings inside JS are wrapped in __t / __T; English templates are taken verbatim from the CSV."""

    def setUp(self):
        self.rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        self.src = "\n".join((ROOT / f).read_text(encoding="utf-8") for f in JS_FILES)

    def test_apply_tool_reports_nothing_left_to_wrap(self):
        r = run("tools/i18n_apply_js.py", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_template_english_is_copied_verbatim_from_csv(self):
        missing = []
        for r in self.rows:
            if "${" in r["zh"] and f"__T('{r['id']}'," in self.src:
                if f"() => `{r['en']}`)" not in self.src:
                    missing.append(r["id"])
        self.assertEqual(missing, [])

    def test_wrapped_ids_exist_in_dictionary(self):
        en = (ROOT / "web/i18n/en.js").read_text(encoding="utf-8")
        ids = set(re.findall(r"__t\('(s\d+(?:@[^']*)?)'", self.src))
        missing = [i for i in sorted(ids) if f'"{i}":' not in en]
        self.assertEqual(missing, [])

    def test_comparison_literals_stay_chinese(self):
        app = (ROOT / "web/app.js").read_text(encoding="utf-8")
        # stage 3: verdicts returned by the server are matched by code so on-screen text can follow the language
        self.assertIn("ms[0].verdict_code === 'no_match'", app)
        self.assertIn("x.key === 'closest'", app)
        self.assertIn("['gap', 'no_match'].includes(r.matches[0].verdict_code)", app)
        self.assertNotIn("verdict === '沒有接近色'", app)
        self.assertIn("/^[＋+]/.test(extra)", app)       # Chinese uses a full-width plus sign, English a half-width one; both must be recognized

    def test_js_syntax_valid_with_node(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("沒有 node")
        for f in JS_FILES + ["web/i18n.js", "web/i18n/en.js"]:
            r = subprocess.run([node, "--check", str(ROOT / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)

    def test_helpers_defined_before_use(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        order = re.findall(r'<script src="([^"]+)"', html)
        self.assertEqual(order[:2], ["i18n/en.js", "i18n.js"])
        self.assertLess(order.index("i18n.js"), order.index("app.js"))
        i18n = (ROOT / "web/i18n.js").read_text(encoding="utf-8")
        self.assertIn("window.__t = t;", i18n)
        self.assertIn("window.__T =", i18n)


if __name__ == "__main__":
    unittest.main()
