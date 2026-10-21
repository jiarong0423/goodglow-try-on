"""證據頁（evidence/index.html，放在 GitHub，不在 App 裡）：資料完整、圖都在、不含品牌／人名／病名；App 與 Docker 映像不含它。"""
import json
import re
import sys
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
import server  # noqa: E402
import audit_api_strings as audit  # noqa: E402
import test_demo_fallback_and_hygiene as hyg  # noqa: E402

DATA = json.loads((ROOT / "evidence" / "process.json").read_text(encoding="utf-8"))
BAD = re.compile(r"光化性角化|黑色素瘤|Koebner|伍氏燈|死白|melanoma|keratosis|Wood's lamp|Ghostly|改善前後|before/after|白斑|白化症|vitiligo|albinism", re.I)


class ProcessDataTests(unittest.TestCase):
    def test_four_subjects_with_progressive_steps(self):
        ids = [s["id"] for s in DATA["subjects"]]
        self.assertEqual(ids, ["light_a", "light_b", "uneven_a", "uneven_b"])
        counts = {s["id"]: len(s["steps"]) - 1 for s in DATA["subjects"]}
        self.assertEqual(counts["light_a"], 14)           # 11 層本機＋裁框＋YouCam＋定案
        for s in DATA["subjects"]:
            self.assertEqual(s["steps"][0]["kind"], "start")          # 第一格一定是「起點」，不是憑空出現的原圖
            self.assertEqual(s["steps"][-1]["kind"], "final")

    def test_layer_counts_match_the_recipes(self):
        recipes = json.loads((ROOT / "demo" / "final" / "recipes.json").read_text(encoding="utf-8"))
        by = {s["id"]: s for s in DATA["subjects"]}
        self.assertEqual(by["light_a"]["local_layers"], len(recipes["albinism_F"]["steps"]))      # 11
        self.assertEqual(by["light_b"]["local_layers"], len(recipes["albinism_M"]["steps"]) - 1)  # 4（第 5 項柔焦是定案時加的）

    def test_every_image_exists_and_every_text_is_bilingual(self):
        for s in DATA["subjects"]:
            for k in ("title_zh", "title_en", "credit"):
                self.assertTrue(s[k].strip(), (s["id"], k))
            for st in s["steps"]:
                self.assertTrue((ROOT / "evidence" / st["img"]).is_file(), st["img"])
                if st["diff"]:
                    self.assertTrue((ROOT / "evidence" / st["diff"]).is_file(), st["diff"])
                self.assertTrue(st["zh"].strip() and st["en"].strip(), (s["id"], st["n"]))
                self.assertRegex(st["source_sha256"], r"^[0-9a-f]{64}$")
                self.assertNotRegex(st["en"], r"[㐀-鿿]")

    def test_no_names_brands_or_disease_terms(self):
        text = json.dumps(DATA, ensure_ascii=False)
        self.assertEqual(hyg.blocked_names(text), [])
        self.assertIsNone(BAD.search(text), BAD.search(text))
        names = audit.independent_names(ROOT / "app")                     # 有本機品牌資料時，真實品牌／產品名也不能出現
        self.assertEqual(audit.independent_leaks(DATA, names), set())


class ProcessScriptSyntax(unittest.TestCase):
    def test_all_page_scripts_parse(self):
        """process.js 等前端檔案語法要能過（曾因英文句子裡的單引號整頁空白）。沒有 node 就略過。"""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("沒有 node")
        for f in ("evidence/process.js", "web/app.js", "web/i18n.js", "web/capture_guide.js", "web/makeup_layers.js"):
            r = subprocess.run([node, "--check", str(ROOT / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f}\n{r.stderr[:400]}")


class ProcessPageTests(unittest.TestCase):
    def test_evidence_is_not_part_of_the_app(self):
        """證據頁只在 GitHub：App 不連過去、不由伺服器提供、不進離線快取、不進 Docker 映像。"""
        td = tempfile.TemporaryDirectory()
        state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td.name) / "l.json", catalog="demo")
        httpd = server.make_server("127.0.0.1", 0, state)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            for path in ("/process.html", "/process.json", "/img/process/light_a/00.jpg", "/evidence/index.html"):
                with self.assertRaises(urllib.error.HTTPError, msg=path) as cm:
                    urllib.request.urlopen(base + path, timeout=20)
                self.assertEqual(cm.exception.code, 404, path)
        finally:
            httpd.shutdown()
            httpd.server_close()
            td.cleanup()
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("process.html", html)
        self.assertNotIn("evidence", html)
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertNotIn("process", sw)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("evidence", dockerfile)

    def test_evidence_page_is_self_contained_for_github_pages(self):
        html = (ROOT / "evidence" / "index.html").read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="([^"#]+)"', html):
            if ref.startswith("http"):
                continue
            self.assertTrue((ROOT / "evidence" / ref).resolve().is_file() or ref.endswith(".md"), ref)
        self.assertTrue((ROOT / "evidence" / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
