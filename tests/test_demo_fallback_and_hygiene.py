"""手機實測後的修正：示範圖不再燒英文黑條、API 不回內部備註、畫面文字不點名真人與病名。"""
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# 不得出現在畫面或對照表的真人姓名：測試檔本身不放人名，只放詞的 sha256 前 16 碼（拉丁字母小寫單字；中文取相鄰兩字）。
BLOCKED_NAME_HASHES = {
    "3308991c8f095800", "3a847c66e9d50e3c", "3fab6afd3e550b43", "569fd0b29e0157a9", "5f48a7270984523a", "72ca296a4d08bb1b",
    "a30b3219f40f0ec7", "a314e3ef281f0cf2", "c5422c052bfbd7bb", "cada5e764bd9d5cf", "d37c5e7961d0ed50", "e1838f6fc2593a0a",
    "e388075da4fa4e95", "f21cc83ae6b213d4", "f378db0146c41d1e", "f4aa0655cdb8d4fc",
    "b67148d187cf4395", "c9b1c9567c3a2ba6", "ea94896c63e85303",
}


def blocked_names(text: str) -> list:
    import hashlib
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", text)]
    for run in re.findall(r"[㐀-鿿]+", text):
        words += [run[i:i + 2] for i in range(len(run) - 1)]
    return sorted({w for w in words if hashlib.sha256(w.encode()).hexdigest()[:16] in BLOCKED_NAME_HASHES})

import server  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def jpeg(color=(250, 250, 250), size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


@unittest.skipUnless(Image, "沒有 Pillow")
class MockLabelTests(unittest.TestCase):
    def test_label_is_a_thin_bottom_bar_not_a_top_banner(self):
        out = Image.open(io.BytesIO(server.mock_label(jpeg()))).convert("L")
        self.assertEqual(out.size, (800, 1000))
        top = [out.getpixel((x, y)) for y in (0, 10, 40) for x in (5, 400, 790)]
        self.assertTrue(all(v > 230 for v in top), "頂端不應該有黑條")
        bottom = out.getpixel((790, 995))
        self.assertLess(bottom, 40, "底部有細標示條")

    def test_english_label_does_not_crash(self):
        server._REQ.lang = "en"
        try:
            self.assertGreater(len(server.mock_label(jpeg())), 1000)
        finally:
            server._REQ.lang = "zh"


class NearestReplayTests(unittest.TestCase):
    def test_prefers_final_look_image_then_any_exact_replay(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "exact").mkdir()
            (td / "exact" / "a.jpg").write_bytes(b"EXACT")
            old_demos, old_final = server.DEMOS, server.FINAL_DIR
            try:
                server.DEMOS = {"albinism": {"replay": td}}
                server.FINAL_DIR = td / "final"
                self.assertEqual(server.nearest_replay("albinism"), b"EXACT")           # 只有精確錄影
                (td / "final").mkdir()
                (td / "final" / "f.jpg").write_bytes(b"FINAL")
                (td / "final" / "recipes.json").write_text(json.dumps({"albinism_F": {"images": {"final": "f.jpg"}}}), encoding="utf-8")
                self.assertEqual(server.nearest_replay("albinism"), b"FINAL")           # 有定案圖就用定案圖
            finally:
                server.DEMOS, server.FINAL_DIR = old_demos, old_final


class PublicViewTests(unittest.TestCase):
    def test_internal_keys_are_stripped_recursively(self):
        data = {"version": "v", "note": "內部", "blush": {"levels": [{"id": "a", "label": "淡", "note": "內部"}]},
                "face_rules": {"x": "內部"}, "foundation_note": "內部"}
        out = server.public_view(data)
        self.assertEqual(out, {"version": "v", "blush": {"levels": [{"id": "a", "label": "淡"}]}})

    def test_presets_response_has_no_internal_words(self):
        presets = json.loads((ROOT / "app" / "presets.json").read_text(encoding="utf-8"))
        text = json.dumps(server.public_view(presets), ensure_ascii=False)
        self.assertEqual(re.findall(r"外部審查|使用者|隔壁|待查證|白化症|白斑", text), [])


class TrackedDataHygiene(unittest.TestCase):
    def test_presets_json_file_has_no_internal_notes(self):
        text = (ROOT / "app" / "presets.json").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"外部審查|使用者|隔壁|待查證|白化症|白斑", text), [])

    def test_user_facing_text_names_no_real_people_or_condition(self):
        import ast
        parts = []
        for f in ("color_engine.py", "full_look.py"):           # 只看會進到畫面的字串；docstring 與註解是內部說明，不算
            tree = ast.parse((ROOT / "app" / f).read_text(encoding="utf-8"))
            docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
            parts += [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
        text = "\n".join(parts)
        self.assertEqual(blocked_names(text), [])
        for bad in ("巴西白化症", "來源待查證"):
            self.assertNotIn(bad, text, bad)

    def test_table_has_no_names_or_condition_in_english(self):
        import csv
        rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        for r in rows:
            self.assertEqual(blocked_names(r["en"]), [], r["id"])
            self.assertNotIn("albinism skin", r["en"], r["id"])

    def test_screen_text_has_no_disease_names_or_ghostly_wording(self):
        """ 裁示：畫面不出現病名（光化性角化、黑色素瘤、Koebner、伍氏燈）、標題不用「死白」。成分名與就醫提醒保留。"""
        import csv
        rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        bad = re.compile(r"光化性角化|黑色素瘤|Koebner|同形反應|伍氏燈|死白|melanoma|keratosis|Wood's lamp|Ghostly|isomorphic", re.I)
        hits = [(r["id"], m.group(0)) for r in rows for m in [bad.search(r["zh"] + " " + r["en"])] if m]
        self.assertEqual(hits, [])
        self.assertTrue(any("dermatologist" in r["en"] for r in rows))      # 就醫提醒還在

    def test_no_eyes_closed_capture_wording(self):
        """多數人用前鏡頭、生活上和一般人沒有差異 → 不提供「閉眼拍」；畫面與文件都不再出現。"""
        import csv
        rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        bad = re.compile(r"閉眼|閉著眼|閉上眼|eyes closed|closed eyes|eyes-closed", re.I)
        self.assertEqual([r["id"] for r in rows if bad.search(r["zh"] + " " + r["en"])], [])
        for f in ("README.md", "README.en.md", "docs/submission/devpost_draft_en.md", "docs/submission/video_script_v3.md"):
            if (ROOT / f).is_file():                      # 對外版不含投稿草稿，檔案不在就略過
                self.assertIsNone(bad.search((ROOT / f).read_text(encoding="utf-8")), f)

    def test_screen_text_names_no_organizations(self):
        """ 裁示 1a-B：畫面不點名機構（NHS、MSD、WHO、Mayo Clinic、PMC、CMAJ、DermNet…），出處用通用說法。照片授權標示（Pexels、Unsplash）除外。"""
        import csv
        rows = list(csv.DictReader(open(ROOT / "docs/i18n/zh_en_map.csv", encoding="utf-8")))
        bad = re.compile(r"\bNHS\b|\bMSD\b|\bWHO\b|Mayo|\bPMC\b|CMAJ|DermNet|IJDVL|Lavedo|VR Foundation|British Association|\bBAD\b")
        hits = [(r["id"], m.group(0)) for r in rows for m in [bad.search(r["zh"] + " " + r["en"])] if m]
        self.assertEqual(hits, [])

    def test_tracked_text_files_name_no_real_people(self):
        """追蹤中的程式、文件、對照表都不點名真人（紅隊 #8）。"""
        import subprocess
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        if not files:
            self.skipTest("不在 git 工作樹內")
        hits = {}
        for f in files:
            if Path(f).suffix.lower() not in (".py", ".js", ".md", ".csv", ".json", ".html", ".txt"):
                continue
            try:
                found = blocked_names((ROOT / f).read_text(encoding="utf-8"))
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            if found:
                hits[f] = found
        self.assertEqual(hits, {})


if __name__ == "__main__":
    unittest.main()
