"""拍攝小幫手（web/capture_guide.js）的判斷規則測試。
 ；門檻與出處以 docs/EVIDENCE_LIMITS.md 為準。
JS 純函式用 node 跑；沒有 node 的環境略過。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

# 1500×2000 的照片（官方標準化裁切同尺寸），臉寬 990（0.66）置中、正面、光線正常
BASE = {"imgW": 1500, "imgH": 2000, "face": {"x": 255, "y": 500, "w": 990, "h": 1250},
        "noseX": 750, "mouthX": 750, "eyeWL": 150, "eyeWR": 148, "lightness": 72, "detScore": 0.92}


def case(**kw):
    m = json.loads(json.dumps(BASE))
    for k, v in kw.items():
        m[k] = v
    return m


CASES = {
    "frontal_ok": case(),
    # 證據表第 1 列：側臉偏轉 0.162 > 0.15 擋下
    "side_face": case(noseX=750 + 0.162 * 990, mouthX=750 + 0.12 * 990, eyeWR=100),
    "slightly_side": case(noseX=750 + 0.10 * 990),
    # 證據表第 2 列：整張原圖臉寬 0.55–0.56 → 引導通過、自動裁；0.45 提醒；0.25 擋下
    "face_055": case(imgW=2400, imgH=3200, face={"x": 534, "y": 900, "w": 1332, "h": 1690}, noseX=1200, mouthX=1200, eyeWL=200, eyeWR=198),
    "face_small_warn": case(face={"x": 412, "y": 700, "w": 675, "h": 850}, noseX=750, mouthX=750),
    "face_tiny": case(face={"x": 562, "y": 800, "w": 375, "h": 480}, noseX=750, mouthX=750, eyeWL=60, eyeWR=59),
    # 證據表第 3 列：頭頂被切
    "cut_top": case(face={"x": 255, "y": -80, "w": 990, "h": 1250}),
    "near_edge": case(face={"x": 255, "y": 20, "w": 990, "h": 1250}),
    "too_dark": case(lightness=22),
    "too_bright": case(lightness=95),
    # 證據表第 4 列：極淺膚色低對比，偵測分數低或兩個偵測器點位差大 → 只提醒不擋
    "low_contrast_score": case(detScore=0.48, lightness=88),
    "low_contrast_jitter": case(jitter=0.09, lightness=89),
    "no_face": case(detScore=None),
    "low_res": case(imgW=900, imgH=1200, face={"x": 153, "y": 300, "w": 594, "h": 750}, noseX=450, mouthX=450, eyeWL=90, eyeWR=89),
}

RUNNER = """
const g = require(process.argv[1]);   // node -e：第一個參數在 argv[1]
const cases = JSON.parse(process.argv[2]);
const out = {};
for (const [k, m] of Object.entries(cases)) { out[k] = g.assess(m); out[k].live = g.liveHint(out[k]); }
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "沒有 node，略過前端規則測試")
class CaptureGuideRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", RUNNER, str(ROOT / "web" / "capture_guide.js"), json.dumps(CASES)],
                           capture_output=True, text=True, timeout=30, check=True)
        cls.res = json.loads(r.stdout)

    def st(self, name, item_id):
        return next(i["status"] for i in self.res[name]["items"] if i["id"] == item_id)

    def test_frontal_passes_and_crops_to_066(self):
        r = self.res["frontal_ok"]
        self.assertEqual(r["overall"], "pass")
        self.assertAlmostEqual(r["crop"]["faceRatio"], 0.66, places=2)
        self.assertEqual(r["crop"]["w"] * 4, r["crop"]["h"] * 3)

    def test_side_face_blocked_with_evidence_row_1(self):
        r = self.res["side_face"]
        self.assertEqual(r["overall"], "block")
        it = next(i for i in r["items"] if i["id"] == "frontal")
        self.assertEqual((it["status"], it["evidence"]), ("block", [1]))
        self.assertGreater(it["value"]["offset"], 0.15)
        self.assertIsNone(r["crop"])

    def test_slightly_side_is_warn_not_block(self):
        self.assertEqual(self.st("slightly_side", "frontal"), "warn")

    def test_face_size(self):
        self.assertEqual(self.st("face_055", "face_size"), "pass")       # 0.555 → 引導通過，之後自動裁到 0.66
        self.assertAlmostEqual(self.res["face_055"]["crop"]["faceRatio"], 0.66, places=2)
        self.assertEqual(self.st("face_small_warn", "face_size"), "warn")
        self.assertEqual(self.st("face_tiny", "face_size"), "block")
        self.assertEqual(self.res["face_tiny"]["overall"], "block")

    def test_cut_off_blocked_and_near_edge_warned(self):
        self.assertEqual(self.st("cut_top", "in_frame"), "block")
        self.assertEqual(self.res["cut_top"]["overall"], "block")
        self.assertEqual(self.st("near_edge", "in_frame"), "warn")

    def test_lightness_never_blocks_and_never_asks_for_flash_or_strong_light(self):
        for name in ("too_dark", "too_bright"):
            self.assertEqual(self.st(name, "lightness"), "warn")
            self.assertNotEqual(self.res[name]["overall"], "block")
        hint_dark = next(i["hint"] for i in self.res["too_dark"]["items"] if i["id"] == "lightness")
        self.assertIn("不用開閃光燈", hint_dark)
        for r in self.res.values():
            for it in r["items"]:
                self.assertNotIn("強光", it["hint"])

    def test_low_contrast_warns_instead_of_blocking(self):
        for name in ("low_contrast_score", "low_contrast_jitter"):
            self.assertEqual(self.st(name, "detect_stability"), "warn")
            self.assertEqual(self.res[name]["overall"], "warn")
            it = next(i for i in self.res[name]["items"] if i["id"] == "detect_stability")
            self.assertEqual(it["evidence"], [4])

    def test_no_face_blocked(self):
        self.assertEqual(self.res["no_face"]["overall"], "block")

    def test_low_resolution_warns(self):
        self.assertEqual(self.st("low_res", "resolution"), "warn")

    def test_hints_are_gentle_and_have_no_condition_names(self):
        for r in self.res.values():
            for it in r["items"]:
                for bad in ("白化", "白斑", "錯誤", "失敗", "異常", "YouCam"):
                    self.assertNotIn(bad, it["hint"])
                self.assertTrue(it["evidence"])


@unittest.skipUnless(NODE, "沒有 node，略過前端規則測試")
class LiveHintRules(unittest.TestCase):
    """備用相機的即時提示：同一組數字規則，壓成一個要提示的方向。"""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", RUNNER, str(ROOT / "web" / "capture_guide.js"), json.dumps(CASES)],
                           capture_output=True, text=True, timeout=30, check=True)
        cls.live = {k: v["live"] for k, v in json.loads(r.stdout).items()}

    def test_frontal_ok_is_ok(self):
        self.assertEqual(self.live["frontal_ok"], "ok")

    def test_no_face(self):
        self.assertEqual(self.live["no_face"], "no_face")

    def test_small_face_says_closer(self):
        self.assertEqual(self.live["face_tiny"], "closer")
        self.assertEqual(self.live["face_small_warn"], "closer")

    def test_cut_or_near_edge_says_center(self):
        self.assertEqual(self.live["cut_top"], "center")
        self.assertEqual(self.live["near_edge"], "center")

    def test_side_face_says_front(self):
        self.assertEqual(self.live["side_face"], "front")
        self.assertEqual(self.live["slightly_side"], "front")

    def test_priority_closer_before_front(self):
        # 臉又小又側時先講「再靠近」，一次只講一件事
        self.assertEqual(self.live["face_tiny"], "closer")

    def test_native_camera_wires_live_hint(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("nativeTimer = setInterval(checkNative, 700);", app)
        self.assertIn("window.CaptureGuide.liveHint(g)", app)
        self.assertIn("img.videoWidth", (ROOT / "web" / "face_auto.js").read_text(encoding="utf-8"))


class TestBlockedPhotoFlow(unittest.TestCase):
    """ 手機實測：側臉被擋下時，標題不能說「只差一點點」，也不能顯示從臉緣取到的假顏色。"""

    def setUp(self):
        self.app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.auto = (ROOT / "web" / "face_auto.js").read_text(encoding="utf-8")

    def test_block_headline_does_not_say_almost_fine(self):
        self.assertNotIn("只差一點點", self.app)
        self.assertIn("角度或距離還不適合取色", self.app)

    def test_blocked_photo_skips_auto_sampling(self):
        self.assertIn("if (state.guideBlock) setSkin(null);", self.app)
        self.assertIn("else if (!points.normal) runAutoColors(img, dataUrl);", self.app)

    def test_turned_face_samples_wider_cheek(self):
        self.assertIn("face[454].x - face[1].x", self.auto)
        self.assertIn("turned ? wl > wr", self.auto)


class TestUnifiedStyle(unittest.TestCase):
    """ 手機實測後統一風格：不用 emoji 圖示、底部按鈕列實底、預設白底。"""

    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_no_emoji_icons_in_markup(self):
        import re
        emoji = re.compile("[\U0001F300-\U0001FAFF\u270B\u2600\u263E]")
        self.assertEqual(emoji.findall(self.html.replace("\u2600\ufe0e", "")), [])

    def test_dock_is_solid_not_transparent_gradient(self):
        tail = self.css[self.css.rindex("統一風格"):]
        self.assertIn(".dock { background: var(--bg);", tail)

    def test_albinism_entry_no_longer_forces_dark(self):
        self.assertNotIn("c.id === 'albinism';   // 白化症怕光：預設暗色", self.app)
        self.assertIn("prefs.darkSet && prefs.dark", self.app)


if __name__ == "__main__":
    unittest.main()
