"""中英切換（第 1 階段：基礎＋index.html 靜態字）的完整性測試。
英文逐字取自 docs/i18n/zh_en_map.csv，不改寫；中文零回歸的畫面快照另在瀏覽器比對（見 docs/i18n/APP_I18N_PLAN_20261021.md）。
"""
import csv
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_EN_SHA = "abe5c6381dd5bf92b904eadbe622285a1c47835a05d725e8c9daaf7480fe4c51"   # id<TAB>en 逐行 sha256（925 列）
CJK = re.compile(r"[㐀-鿿]")


def run(*args):
    return subprocess.run([sys.executable, "-I", "-B", *args], cwd=ROOT, capture_output=True, text=True)


class TestTable(unittest.TestCase):
    def setUp(self):
        self.rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))

    def test_en_column_hash_and_count(self):
        self.assertEqual(len(self.rows), 925)
        sha = hashlib.sha256("\n".join(f"{r['id']}\t{r['en']}" for r in self.rows).encode()).hexdigest()
        self.assertEqual(sha, CSV_EN_SHA)

    def test_every_row_has_english_except_unit_word(self):
        blank = [r["id"] for r in self.rows if not r["en"].strip()]
        self.assertEqual(blank, ["s0008"])        # 單位字「個」英文不需要單位，刻意留空

    def test_no_chinese_left_in_english_except_code_key(self):
        left = [r["id"] for r in self.rows if CJK.search(r["en"])]
        self.assertEqual(left, ["s0889", "s0898", "s0899", "s0900", "s0901"])   # 程式比對用的正規表示式（含中文別名），不是畫面文字


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
    """第 2 階段：JS 內的中文字串都包了 __t／__T；英文樣板逐字取自 CSV。"""

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
        # 第 3 階段：伺服器回傳的判定改用代碼比對，畫面文字才能跟著語言換
        self.assertIn("ms[0].verdict_code === 'no_match'", app)
        self.assertIn("x.key === 'closest'", app)
        self.assertIn("['gap', 'no_match'].includes(r.matches[0].verdict_code)", app)
        self.assertNotIn("verdict === '沒有接近色'", app)
        self.assertIn("/^[＋+]/.test(extra)", app)       # 中文是全形＋、英文是半形 +，兩種都要認

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
