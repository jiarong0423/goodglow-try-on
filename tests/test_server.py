"""伺服器測試：mock 模式與假 YouCam（不連網、不扣點）。"""
import base64
import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
import server  # noqa: E402
import noise_test as nt  # noqa: E402
from PIL import Image  # noqa: E402

KEY = "test-key-xyz"


def jpeg_data_url(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (220, 200, 195)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class FakeYouCam(BaseHTTPRequestHandler):
    tasks = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": "unauthorized"})
        port = self.server.server_address[1]
        if self.path == "/s2s/v2.0/file":
            return self._send(200, {"data": {"files": [{"file_id": "f1", "requests": [
                {"method": "PUT", "url": f"http://127.0.0.1:{port}/upload", "headers": {"Content-Type": "image/jpeg"}}]}]}})
        if self.path == "/s2s/v2.0/task/makeup-vto":
            FakeYouCam.tasks.append(payload)
            return self._send(200, {"data": {"task_id": f"t{len(FakeYouCam.tasks)}"}})
        if self.path == "/s2s/v2.0/task/face-attr-analysis":
            FakeYouCam.tasks.append(payload)
            return self._send(200, {"data": {"task_id": "fa1"}})
        self._send(404, {})

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(200, raw=b"", ctype="text/plain")

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path.startswith("/s2s/v2.0/task/makeup-vto/"):
            return self._send(200, {"data": {"task_status": "success",
                                             "results": [{"download_url": f"http://127.0.0.1:{port}/result.jpg"}]}})
        if self.path.startswith("/s2s/v2.0/task/face-attr-analysis/"):
            return self._send(200, {"data": {"task_status": "success", "results": {
                "faceshape": "Oblong", "lipshape": ["Thin"], "eyelid": {"left_shape": "Round"},
                "eyebrow": {"left_body_thickness": "Sparse"}, "cheekbone": {"overrall": "Low Cheekbone"}}}})
        if self.path == "/result.jpg":
            buf = io.BytesIO()
            Image.new("RGB", (100, 100), (200, 50, 80)).save(buf, "JPEG")
            return self._send(200, raw=buf.getvalue(), ctype="image/jpeg")
        self._send(404, {})


class ServerTestBase(unittest.TestCase):
    mock_mode = True
    budget_cap = 50

    def setUp(self):
        patches = [mock.patch.object(nt, "POLL_SECONDS", 0.0), mock.patch.object(nt, "RETRY_WAITS", ())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.fake = None
        api_base = nt.API_BASE
        if not self.mock_mode:
            FakeYouCam.tasks = []
            self.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouCam)
            threading.Thread(target=self.fake.serve_forever, daemon=True).start()
            api_base = f"http://127.0.0.1:{self.fake.server_address[1]}"
            p = mock.patch.object(nt, "load_api_key", return_value=KEY)
            p.start()
            self.addCleanup(p.stop)
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = server.State(self.mock_mode, 3, api_base, self.budget_cap,
                                  Path(self._td.name) / "ledger.json")
        self.httpd = server.make_server("127.0.0.1", 0, self.state)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.fake:
            self.fake.shutdown()
            self.fake.server_close()

    def post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return json.load(r)


class MockModeTests(ServerTestBase):
    mock_mode = True

    def test_presets_and_status(self):
        self.assertEqual(self.get("/api/status")["mode"], "mock")
        self.assertIn("styles", self.get("/api/presets"))

    def test_looks_with_own_photo_returns_two_labelled_images(self):
        code, res = self.post("/api/looks", {"style": "natural", "undertone": "pink_cool", "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertEqual(set(res["looks"]), {"glow", "clear"})
        self.assertTrue(res["looks"]["glow"]["image"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("red1", json.dumps(res["looks"]))

    def test_low_resolution_rejected(self):
        code, res = self.post("/api/looks", {"style": "natural", "image": jpeg_data_url((800, 1000))})
        self.assertEqual(code, 400)
        self.assertEqual(res["error_code"], "low_resolution")

    def test_style_blocks_red_lip_outside_polished(self):
        code, res = self.post("/api/makeup", {"style": "natural", "selection": {"lip": "red1"},
                                              "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 400)
        self.assertEqual(res["error_code"], "bad_selection")
        code, res = self.post("/api/makeup", {"style": "polished", "selection": {"lip": "red3"},
                                              "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertIsNotNone(res["hint"])  # 深紅唇建議平衡

    def test_color_endpoints(self):
        code, res = self.post("/api/match", {"hex": "#F5E2DC", "category": "foundation", "source": "demo"})
        self.assertEqual(code, 200)
        self.assertEqual(res["matches"][0]["code"], "C00 瓷粉")
        code, res = self.post("/api/skin-plan", {"condition": "vitiligo", "normal_hex": "#9C6B4D", "patch_hex": "#EBD3CB"})
        self.assertEqual(code, 200)
        self.assertIn(res["plan"]["level"], ("two_shade", "two_shade_plus"))
        code, res = self.post("/api/card", {"undertone": "pink_cool", "selection": {"brow": "taupe", "lip": "natural_gloss"},
                                            "skin_hex": "#F2DDD5"})
        self.assertEqual(code, 200)
        self.assertEqual([i["item"] for i in res["items"]], ["foundation", "brow", "lip", "concealer"])   # 白化症加遮瑕
        self.assertTrue(all(i["matches"] for i in res["items"]))
        found = res["items"][0]
        self.assertEqual(found["card"]["tone"], "cool")                        #
        base_src = "official" if server.ce.OFFICIAL_CATALOG.exists() else "demo"      # 品牌資料只在本機版
        self.assertTrue(all(m["source"] == base_src and "fit" in m for m in found["matches"]))
        self.assertEqual(self.post("/api/match", {"hex": "zz", "category": "foundation"})[0], 400)
        cells = self.get("/api/skin-card")["cells"]
        self.assertEqual(len(cells), 30)

    def test_card_face_shades_follow_style_and_expected_zones(self):
        #「臉部色號」：有上打亮／修容才給；有預期妝容就用它的分區色，沒有就依膚色推算
        body = {"undertone": "pink_cool", "skin_hex": "#DDC9C0", "condition": "albinism",
                "selection": {"highlight": "lift", "contour": "soft", "lip": "red2"}}
        code, res = self.post("/api/card", body)
        self.assertEqual(code, 200)
        face = {i["item"]: i for i in res["items"] if i["item"] in ("concealer", "highlight", "contour")}
        self.assertEqual(set(face), {"concealer", "highlight", "contour"})
        self.assertEqual(face["highlight"]["basis"], "依膚色推算")
        zones = {"base": {"hex": "#E0CCC4"}, "highlight": {"hex": "#EEDFD8"}, "contour": {"hex": "#B9A9A2"}}
        code, res = self.post("/api/card", dict(body, zones=zones))
        hl = next(i for i in res["items"] if i["item"] == "highlight")
        self.assertEqual((hl["hex"], hl["basis"]), ("#EEDFD8", "預期妝容的打亮／修容區取色"))
        code, res = self.post("/api/card", dict(body, zones={"highlight": {"hex": "bad"}, "contour": {"hex": "#B9A9A2"}}))
        self.assertEqual(next(i for i in res["items"] if i["item"] == "highlight")["basis"], "依膚色推算")   # 壞色碼不採用
        code, res = self.post("/api/card", dict(body, selection={"lip": "nude_balm"}))
        self.assertEqual([i["item"] for i in res["items"] if i["item"] in ("highlight", "contour")], [])
        code, res = self.post("/api/card", dict(body, condition="vitiligo"))
        self.assertNotIn("concealer", [i["item"] for i in res["items"]])     # 白斑的遮瑕由 /api/recommend 給

    def test_simplified_flow_foundation_only_replays_real_result(self):
        code, res = self.post("/api/makeup", {"demo": True, "condition": "vitiligo", "style": "grooming",
                                              "undertone": "pink_cool", "selection": {"foundation": "full"},
                                              "foundation_hex": "#8B5739"})
        self.assertEqual(code, 200, res)
        rec_img = (server.DEMO_DIR / "replay" / "vitiligo" / "grooming_layer1_foundation.jpg").read_bytes()
        self.assertEqual(base64.b64decode(res["image"].split(",", 1)[1]), rec_img)
        code, res = self.post("/api/recommend", {"condition": "albinism", "style": "natural", "skin_hex": "#D2B8AC"})
        self.assertIn("敏感", res["recommendation"]["sensitive"]["title"])

    def test_layers_mock_replays_recorded_foundation_layer(self):
        sel = {"foundation": "medium", "blush": "flush", "lip": "tinted_balm"}
        code, res = self.post("/api/layers", {"demo": True, "condition": "vitiligo", "style": "grooming",
                                              "undertone": "neutral", "selection": sel, "foundation_hex": "#946D60"})
        self.assertEqual(code, 200, res)
        self.assertEqual([l["item"] for l in res["layers"]], ["foundation", "blush", "lip"])
        rec_layer = (server.DEMO_DIR / "replay" / "vitiligo" / "grooming_layer1_foundation.jpg").read_bytes()
        self.assertEqual(base64.b64decode(res["layers"][0]["image"].split(",", 1)[1]), rec_layer)   # 第一層用實跑的底妝結果

    def test_face_advice_mock_replays_recorded_result(self):
        code, res = self.post("/api/face", {"demo": True, "condition": "vitiligo", "style": "grooming"})
        self.assertEqual(code, 200)
        self.assertTrue(res["available"])
        self.assertTrue(any("方" in t for t in res["advice"]["tips"]))       # 實測 Square
        code, res = self.post("/api/face", {"demo": True, "condition": "albinism"})
        self.assertFalse(res["available"])

    def test_demo_replay_is_style_aware_and_from_committed_demo_folder(self):
        imgs = {}
        for style in ("grooming", "natural"):
            code, res = self.post("/api/looks", {"demo": True, "condition": "vitiligo", "style": style, "undertone": "neutral"})
            self.assertEqual(code, 200)
            imgs[style] = res["looks"]["glow"]["image"]
        self.assertNotEqual(imgs["grooming"], imgs["natural"])
        src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('"runs/', src)                                     # 執行期不依賴不進版控的 runs/

    def test_exact_replay_used_when_recorded(self):
        # tools/record_demo_replays.py 錄的「完全同一組選項」優先；沒錄到的組合退回舊規則
        import tempfile
        sel = {"foundation": "light", "highlight": "center", "lip": "natural_gloss"}
        with tempfile.TemporaryDirectory() as td:
            old = server.DEMOS["vitiligo"]["replay"]
            try:
                server.DEMOS["vitiligo"]["replay"] = Path(td)
                key = server.exact_key("vitiligo", "natural", dict(sel, undertone="neutral", skin_hex="#946D60",
                                                                   foundation_hex="#946D60"))   # 代號含實際試妝參數（顏色）
                (Path(td) / "exact").mkdir()
                fake = io.BytesIO()
                Image.new("RGB", (8, 8), (10, 200, 30)).save(fake, "JPEG")
                (Path(td) / "exact" / f"{key}.jpg").write_bytes(fake.getvalue())
                code, res = self.post("/api/makeup", {"demo": True, "condition": "vitiligo", "style": "natural",
                                                      "undertone": "neutral", "selection": sel,
                                                      "skin_hex": "#946D60", "foundation_hex": "#946D60"})
                self.assertEqual(code, 200, res)
                self.assertEqual(base64.b64decode(res["image"].split(",", 1)[1]), fake.getvalue())
                code, res2 = self.post("/api/makeup", {"demo": True, "condition": "vitiligo", "style": "natural",
                                                       "undertone": "neutral", "selection": dict(sel, lip="tinted_balm"),
                                                       "skin_hex": "#946D60", "foundation_hex": "#946D60"})
                self.assertNotEqual(base64.b64decode(res2["image"].split(",", 1)[1]), fake.getvalue())
            finally:
                server.DEMOS["vitiligo"]["replay"] = old

    def test_vitiligo_demo_has_expected_look_with_zone_levels(self):
        res = self.get("/api/demo-photo?condition=vitiligo")
        ex = res["expected"]
        self.assertTrue(ex["image"].startswith("data:image/jpeg"))
        self.assertEqual(set(ex["zones"]), {"base", "highlight", "contour"})
        self.assertEqual(ex["zones"]["base"]["level"], 6)
        self.assertIn("模擬", ex["how"])
        self.assertNotIn("expected", self.get("/api/demo-photo?condition=albinism"))

    def test_patch_mask_on_demo_photo(self):
        # 採用 E4：網頁送照片＋上妝範圍，伺服器回遮罩（大小＝範圍圖大小）
        photo = self.get("/api/demo-photo?condition=vitiligo")["image"]
        reg = Image.new("L", (180, 240), 0)
        from PIL import ImageDraw
        ImageDraw.Draw(reg).ellipse((40, 50, 140, 200), fill=255)
        buf = io.BytesIO(); reg.save(buf, "PNG")
        region = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        code, res = self.post("/api/patch-mask", {"image": photo, "region": region})
        self.assertEqual(code, 200, res)
        self.assertTrue(res["mask"].startswith("data:image/png;base64,"))
        mask = Image.open(io.BytesIO(base64.b64decode(res["mask"].split(",", 1)[1])))
        self.assertEqual(mask.size, (180, 240))
        self.assertGreater(res["ratio"], 0.05)          # 示範照臉中間有大片淺色區塊
        self.assertLess(res["ratio"], 0.95)

    def test_patch_mask_bad_input(self):
        code, res = self.post("/api/patch-mask", {"image": "data:image/jpeg;base64,AAAA", "region": ""})
        self.assertEqual(code, 400)
        self.assertEqual(res["error_code"], "bad_photo")

    def test_vitiligo_demo_layer_fit_shades_and_opacity(self):
        # B：化妝塗層用擬合對到的色號顏色＋不透明度；只送顏色與數字，不送內部說明；不放品牌名稱
        lf = self.get("/api/demo-photo?condition=vitiligo")["expected"]["layer_fit"]
        self.assertEqual(set(lf), {"shades", "opacity"})
        self.assertEqual(set(lf["shades"]), {"concealer", "foundation", "contour"})
        for s in lf["shades"].values():
            self.assertRegex(s["hex"], r"^#[0-9A-F]{6}$")
            self.assertTrue(s["code"])
            self.assertNotIn("brand", s)
        self.assertTrue(all(0 <= v <= 1 for v in lf["opacity"].values()))

    def test_albinism_demo_is_bright_standard_photo_with_points(self):
        res = self.get("/api/demo-photo?condition=albinism")
        self.assertIn("5273693", res["credit"])
        self.assertEqual(set(res["points"]), {"normal", "brow"})

    def test_final_look_both_entries_and_albinism_who(self):
        # 定案妝容（模擬）：只讀 demo/final/，兩個入口都有；白化症有女／男
        import color_engine as ce
        official = {p["brand"] for p in ce.load_catalogs("official")}
        seen = []
        for q, cond, who in (("condition=albinism", "albinism", "F"), ("condition=albinism&who=M", "albinism", "M"),
                             ("condition=vitiligo", "vitiligo", "M"), ("condition=vitiligo&who=F", "vitiligo", "F")):
            res = self.get(f"/api/final-look?{q}")
            self.assertEqual((res["condition"], res["who"], res["label"]), (cond, who, "模擬"))
            self.assertTrue(res["image"].startswith("data:image/jpeg"))
            self.assertTrue(res["steps"] and all(s["item"] and s["where"] for s in res["steps"]))
            # 三段流程：本地底妝一定有；官方這一步沒錄好就註明、不呼叫 YouCam
            self.assertTrue(res["base"]["image"].startswith("data:image/jpeg"))
            self.assertIn(res["official"]["status"], ("replay", "not_recorded"))
            self.assertEqual(res["official"]["image"] is None, res["official"]["status"] == "not_recorded")
            self.assertTrue(res["credit"].startswith("照片："))
            seen.append(res["image"])
            strip = lambda v: {k: strip(x) for k, x in v.items() if k != "image"} if isinstance(v, dict) else v   # 圖片 base64 可能碰巧含字母組合
            text = json.dumps(strip(res), ensure_ascii=False)
            for brand in official:
                self.assertNotIn(brand, text)
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(self.get("/api/final-look?condition=vitiligo")["official"]["status"], "replay")   # 示範照男生有錄好的官方結果
        self.assertEqual(self.get("/api/final-look?condition=albinism")["who_options"], ["F", "M"])
        self.assertEqual(self.get("/api/final-look?condition=vitiligo")["who_options"], ["F", "M"])
        src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('"runs/', src)

    def test_final_look_rejects_unknown_entry_or_who(self):
        for q in ("condition=other", "condition=vitiligo&who=X", "condition=albinism&who=X"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(f"/api/final-look?{q}")
            self.assertEqual(cm.exception.code, 400)

    def test_demo_photo_available(self):
        res = self.get("/api/demo-photo")
        self.assertTrue(res["image"].startswith("data:image/jpeg"))

    def test_vitiligo_demo_photo_points_and_replay(self):
        res = self.get("/api/demo-photo?condition=vitiligo")
        self.assertIn("20551205", res["credit"])
        self.assertEqual(set(res["points"]), {"normal", "patch", "brow"})
        code, looks = self.post("/api/looks", {"demo": True, "condition": "vitiligo", "style": "grooming",
                                               "undertone": "pink_cool"})
        self.assertEqual(code, 200)
        self.assertTrue(looks["looks"]["glow"]["image"].startswith("data:image/jpeg"))
        code, rec = self.post("/api/recommend", {"condition": "vitiligo", "style": "grooming",
                                                 "normal_hex": "#936A5C", "patch_hex": "#B69F9A"})
        self.assertEqual(code, 200)
        self.assertIn(rec["recommendation"]["base"]["level"], ("two_shade", "two_shade_plus"))


class DemoCatalogTests(ServerTestBase):
    """--catalog demo：錄影片用，畫面不出現第三方品牌（比賽規則：影片不得含第三方商標）。"""

    def setUp(self):
        super().setUp()
        self.state.catalog = "demo"

    def test_no_official_brand_names_in_any_color_response(self):
        import color_engine as ce
        official = {p["brand"] for p in ce.load_catalogs("official")}
        self.assertEqual(self.get("/api/status")["catalog"], "demo")
        responses = [
            self.post("/api/recommend", {"condition": "vitiligo", "style": "grooming", "normal_hex": "#946D60",
                                         "patch_hex": "#B49B96", "brow_hex": "#1B1B1B"})[1],
            self.post("/api/recommend", {"condition": "albinism", "style": "natural", "skin_hex": "#D9CBC1",
                                         "brow_hex": "#C8BCAD"})[1],
            self.post("/api/card", {"undertone": "neutral", "skin_hex": "#CDB5AA",
                                    "selection": {"brow": "taupe", "lip": "natural_gloss", "blush": "soft"}})[1],
        ]
        text = json.dumps(responses, ensure_ascii=False)
        for brand in official:
            self.assertNotIn(brand, text)
        self.assertNotIn('"source": "official"', text)


class LiveModeTests(ServerTestBase):
    mock_mode = False

    def test_makeup_calls_youcam_with_zero_smoothing_and_counts_calls(self):
        code, res = self.post("/api/makeup", {"style": "natural", "undertone": "neutral",
                                              "selection": {"brow": "taupe"}, "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertTrue(res["image"].startswith("data:image/jpeg"))
        sent = FakeYouCam.tasks[-1]
        self.assertEqual(sent["src_file_id"], "f1")
        self.assertEqual(sent["effects"][0], {"category": "skin_smooth", "skinSmoothStrength": 0, "skinSmoothColorIntensity": 0})
        self.assertEqual(self.get("/api/status")["calls_used"], 1)

    def test_max_calls_guard(self):
        for _ in range(3):
            self.assertEqual(self.post("/api/makeup", {"style": "natural", "selection": {},
                                                       "image": jpeg_data_url((1200, 1600))})[0], 200)
        code, res = self.post("/api/makeup", {"style": "natural", "selection": {}, "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 429)
        self.assertEqual(len(FakeYouCam.tasks), 3)

    def test_layers_live_one_call_per_layer_foundation_first(self):
        sel = {"foundation": "medium", "blush": "soft", "lip": "keep", "brow": "keep", "lash": "keep"}
        before = len(FakeYouCam.tasks)
        code, res = self.post("/api/layers", {"style": "natural", "undertone": "neutral", "selection": sel,
                                              "foundation_hex": "#C79A79", "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        self.assertEqual([l["item"] for l in res["layers"]], ["foundation", "blush"])
        sent = FakeYouCam.tasks[before:]
        self.assertEqual(len(sent), 2)                                          # 每層 1 次
        self.assertEqual([e["category"] for e in sent[0]["effects"]], ["skin_smooth", "foundation"])
        self.assertEqual([e["category"] for e in sent[1]["effects"]], ["skin_smooth", "foundation", "blush"])

    def test_face_advice_live_uses_budget_and_five_features(self):
        before = self.get("/api/status")["budget"]["spent"]
        code, res = self.post("/api/face", {"style": "natural", "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 200, res)
        sent = FakeYouCam.tasks[-1]
        self.assertLessEqual(len(sent["features"]), 5)                       # 1–5 項才是 10 點
        self.assertEqual(self.get("/api/status")["budget"]["spent"] - before, 10)
        adv = res["advice"]
        self.assertEqual(adv["suggest"], {"brow": "photo_soft", "lip": "natural_gloss"})
        self.assertTrue(any("橫向" in t for t in adv["tips"]))

    def test_key_never_in_response(self):
        _, res = self.post("/api/looks", {"style": "grooming", "undertone": "neutral", "image": jpeg_data_url((1200, 1600))})
        self.assertNotIn(KEY, json.dumps(res))



class BudgetCapTests(ServerTestBase):
    mock_mode = False
    budget_cap = 2

    def test_budget_cap_blocks_and_counts_only_success(self):
        for _ in range(2):
            self.assertEqual(self.post("/api/makeup", {"style": "natural", "selection": {},
                                                       "image": jpeg_data_url((1200, 1600))})[0], 200)
        code, res = self.post("/api/makeup", {"style": "natural", "selection": {}, "image": jpeg_data_url((1200, 1600))})
        self.assertEqual(code, 429)
        self.assertEqual(res["error_code"], "budget")
        self.assertEqual(self.get("/api/status")["budget"]["spent"], 2)
        self.assertEqual(len(FakeYouCam.tasks), 2)


if __name__ == "__main__":
    unittest.main()
