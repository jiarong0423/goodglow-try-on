"""離線測試：用本機假伺服器走完整 run 流程，不連 YouCam、不扣點。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import noise_test as nt  # noqa: E402

from PIL import Image  # noqa: E402

TEST_KEY = "test-key-abc123"  # gitleaks:allow  (fake value used only by the tests)


def make_photo(path: Path, size=(1200, 1600), gps=True):
    img = Image.new("RGB", size, (200, 150, 140))
    exif = Image.Exif()
    if gps:
        exif[0x8825] = {2: (25.0, 2.0, 0.0)}  # GPSInfo
    exif[0x010F] = "TestCam"
    img.save(path, "JPEG", exif=exif.tobytes())


class FakeYouCam(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        ok = self.headers.get("Authorization") == f"Bearer {TEST_KEY}"
        if not ok:
            self._send(401, {"status": 401, "error": "unauthorized"})
        return ok

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not self._authed():
            return
        st = self.state
        if self.path == "/s2s/v2.0/file":
            st["n_file"] += 1
            fid = f"file{st['n_file']}"
            port = self.server.server_address[1]
            self._send(200, {"status": 200, "data": {"files": [{
                "file_id": fid, "file_name": payload["files"][0]["file_name"],
                "requests": [{"method": "PUT", "url": f"http://127.0.0.1:{port}/upload/{fid}?sig=x",
                              "headers": {"Content-Type": "image/jpeg",
                                          "Content-Length": str(payload["files"][0]["file_size"])}}]}]}})
        elif self.path == "/s2s/v2.0/task/skin-analysis":
            st["tasks"].append(payload)
            self._send(200, {"status": 200, "data": {"task_id": f"task{len(st['tasks'])}"}})
        else:
            self._send(404, {"status": 404})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.headers.get("Authorization"):
            self._send(400, {"error": "presigned url must not get auth header"})
            return
        self.state["uploads"].append(body)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._authed():
            return
        st = self.state
        task = self.path.rsplit("/", 1)[-1]
        st["polls"][task] = st["polls"].get(task, 0) + 1
        if st["polls"][task] == 1:
            self._send(200, {"status": 200, "data": {"task_status": "running"}})
            return
        idx = int(task.removeprefix("task"))
        if idx in st["fail_tasks"]:
            self._send(200, {"status": 200, "data": {"task_status": "error", "error_code": "error_lighting_dark"}})
            return
        raw = 70.0 + (idx % 3)  # 70, 71, 72 輪替 → 已知雜訊
        self._send(200, {"status": 200, "data": {"task_status": "success", "results": {"output": [
            {"type": "hd_redness", "ui_score": 77, "raw_score": raw,
             "mask_urls": ["https://example.com/hd_redness_output.png"]},
            {"type": "hd_pore", "forehead": {"raw_score": 80.0, "ui_score": 80},
             "nose": {"raw_score": 29.1, "ui_score": 58}},
        ]}}})


class FakeServer:
    def __enter__(self):
        FakeYouCam.state = {"n_file": 0, "tasks": [], "uploads": [], "polls": {}, "fail_tasks": set()}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouCam)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = nt.main(argv)
    return code, out.getvalue(), err.getvalue()


class ParseTests(unittest.TestCase):
    def test_list_format_with_subregions(self):
        data = {"results": {"output": [
            {"type": "texture", "ui_score": 68, "raw_score": 57.33, "mask_urls": ["https://x"]},
            {"type": "hd_pore", "nose": {"raw_score": 29.1, "ui_score": 58}},
        ]}}
        self.assertEqual(nt.parse_scores(data),
                         [("texture", "whole", 57.33, 68.0), ("hd_pore", "nose", 29.1, 58.0)])

    def test_score_info_dict_format(self):
        data = {"all": 75.5, "skin_age": 31,
                "hd_redness": {"raw_score": 72.01, "ui_score": 77, "output_mask_name": "x.png"},
                "hd_texture": {"whole": {"raw_score": 66.39, "ui_score": 75}}}
        rows = nt.parse_scores(data)
        self.assertIn(("hd_redness", "whole", 72.01, 77.0), rows)
        self.assertIn(("hd_texture", "whole", 66.39, 75.0), rows)
        self.assertIn(("all", "whole", 75.5, None), rows)

    def test_live_format_20261015(self):
        """2026-10-15 實跑回應（網址已移除）：區域在平的 region 欄位、skin_type 是文字。"""
        data = {"results": {"output": [
            {"ui_score": 87, "raw_score": 91.74, "type": "hd_redness", "mask_urls": ["<url removed>"]},
            {"type": "hd_skin_type", "region": "whole", "skin_type": "Normal"},
            {"type": "hd_skin_type", "region": "t_zone", "skin_type": "Normal"},
            {"ui_score": 92, "raw_score": 94.88, "type": "hd_pore", "region": "forehead"},
            {"ui_score": 99, "raw_score": 100.0, "type": "hd_pore", "region": "nose"},
            {"score": 87.33, "type": "all"},
            {"score": 39, "type": "skin_age"},
            {"type": "resize_image", "mask_urls": ["<url removed>"]},
        ]}, "task_status": "success"}
        self.assertEqual(nt.parse_scores(data), [
            ("hd_redness", "whole", 91.74, 87.0), ("hd_pore", "forehead", 94.88, 92.0),
            ("hd_pore", "nose", 100.0, 99.0), ("all", "whole", 87.33, None), ("skin_age", "whole", 39.0, None)])
        self.assertEqual(nt.parse_labels(data), [("hd_skin_type", "whole", "Normal"), ("hd_skin_type", "t_zone", "Normal")])

    def test_strip_urls(self):
        self.assertEqual(nt.strip_urls({"a": ["https://s3/x?sig"], "b": 1}), {"a": ["<url removed>"], "b": 1})


class StatsTests(unittest.TestCase):
    def test_band_values(self):
        d = nt.describe([70.0, 71.0, 72.0, 70.0, 71.0, 72.0, 70.0, 71.0, 72.0, 70.0])
        self.assertEqual(d["n"], 10)
        self.assertTrue(d["enough_n"])
        self.assertAlmostEqual(d["mdc95_normal"], 1.96 * 2 ** 0.5 * d["sd"])
        self.assertEqual(d["pair_diff_p95"], 2.0)
        self.assertEqual(d["noise_band"], max(d["mdc95_normal"], 2.0))

    def test_single_value_has_no_band(self):
        d = nt.describe([70.0])
        self.assertIsNone(d["noise_band"])
        self.assertFalse(d["enough_n"])

    def test_condition_shift_flag(self):
        rows = [{"status": "success", "label": "same", "concern": "hd_redness", "region": "whole",
                 "raw_score": v, "ui_score": ""} for v in (70, 70.5, 71, 70, 70.5)]
        rows += [{"status": "success", "label": "window", "concern": "hd_redness", "region": "whole",
                  "raw_score": v, "ui_score": ""} for v in (80, 80.5, 81)]
        cond = nt.summarize_rows(rows)["conditions"][0]
        self.assertAlmostEqual(cond["max_shift"], 80.5 - 70.4)
        self.assertTrue(cond["condition_exceeds_noise"])


class PhotoTests(unittest.TestCase):
    def test_reencode_strips_exif_and_resizes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.jpg"
            make_photo(p, size=(3024, 4032))
            data, w, h = nt.prepare_photo(p)
            self.assertEqual((w, h), (1920, 2560))
            img = Image.open(io.BytesIO(data))
            self.assertEqual(len(img.getexif()), 0)

    def test_too_small_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "small.jpg"
            make_photo(p, size=(800, 1000))
            with self.assertRaises(nt.PhotoError):
                nt.prepare_photo(p)


class RunFlowTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        for i in range(4):
            make_photo(self.photos / f"IMG_{i}.jpg")
        self.runs = self.root / "runs"
        patcher = mock.patch.multiple(nt, POLL_SECONDS=0.0, RETRY_WAITS=())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.td.cleanup)

    def _run(self, srv, *extra, env_key=TEST_KEY):
        env = {nt.KEY_ENV: env_key} if env_key else {}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(nt, "PROJECT_ROOT", self.root):  # 不讀到真的 .env
            if not env_key:
                os.environ.pop(nt.KEY_ENV, None)
            return run_main(["run", "--photos", str(self.photos), "--label", "same_light",
                             "--runs-dir", str(self.runs), "--api-base", srv.base, *extra])

    def test_requires_run_flag(self):
        with FakeServer() as srv:
            code, _, err = self._run(srv)
        self.assertEqual(code, 2)
        self.assertIn("ERROR", err)
        self.assertEqual(FakeYouCam.state["tasks"], [])

    def test_requires_key(self):
        with FakeServer() as srv:
            code, _, err = self._run(srv, "--run", env_key="")
        self.assertEqual(code, 2)
        self.assertIn(nt.KEY_ENV, err)

    def test_max_calls_guard(self):
        with FakeServer() as srv:
            code, _, err = self._run(srv, "--run", "--max-calls", "3")
        self.assertEqual(code, 2)
        self.assertEqual(FakeYouCam.state["tasks"], [])

    def test_mixed_hd_sd_rejected(self):
        with FakeServer() as srv:
            code, _, _ = self._run(srv, "--run", "--concerns", "hd_redness,redness")
        self.assertEqual(code, 2)

    def test_full_run_stores_scores_only(self):
        with FakeServer() as srv:
            code, out, err = self._run(srv, "--run")
        self.assertEqual(code, 0, err)
        st = FakeYouCam.state
        self.assertEqual(len(st["tasks"]), 4)
        self.assertEqual(st["tasks"][0]["dst_actions"], ["hd_redness"])
        for body in st["uploads"]:  # 上傳的照片沒有 EXIF
            self.assertEqual(len(Image.open(io.BytesIO(body)).getexif()), 0)

        run_dir = next(self.runs.iterdir())
        names = sorted(p.name for p in run_dir.iterdir())
        self.assertEqual(names, ["run_meta.json", "schema_sample.json", "scores.csv", "summary.json", "summary.md"])
        for p in run_dir.iterdir():
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("example.com", text, p.name)  # 結果網址不落檔
            self.assertNotIn("sig=", text, p.name)
            self.assertNotIn(TEST_KEY, text, p.name)
        self.assertNotIn(TEST_KEY, out + err)

        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        red = next(e for e in summary["per_label"] if e["concern"] == "hd_redness")
        self.assertEqual(red["raw_score"]["n"], 4)
        self.assertEqual(red["raw_score"]["range"], 2.0)
        self.assertFalse(red["raw_score"]["enough_n"])
        self.assertIn("OK", out)

    def test_task_error_recorded_and_exit_1(self):
        with FakeServer() as srv:
            FakeYouCam.state["fail_tasks"] = {2}
            code, _, err = self._run(srv, "--run")
        self.assertEqual(code, 1)
        self.assertIn("ERROR", err)
        self.assertIn("error_lighting_dark", err)
        run_dir = next(self.runs.iterdir())
        csv_text = (run_dir / "scores.csv").read_text(encoding="utf-8")
        self.assertIn("error_lighting_dark", csv_text)

    def test_summarize_merges_runs(self):
        with FakeServer() as srv:
            self.assertEqual(self._run(srv, "--run")[0], 0)
        run_dir = next(self.runs.iterdir())
        code, out, _ = run_main(["summarize", str(run_dir), "--out", str(self.root / "merged")])
        self.assertEqual(code, 0)
        self.assertIn("hd_redness", out)
        self.assertTrue((self.root / "merged" / "summary.md").exists())


class KeyTests(unittest.TestCase):
    def test_dotenv_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, ".env").write_text("OTHER=1\nexport YOUCAM_API_KEY='from-dotenv'\n")
            with mock.patch.object(nt, "PROJECT_ROOT", Path(td)), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(nt.KEY_ENV, None)
                self.assertEqual(nt.load_api_key(), "from-dotenv")
            with mock.patch.object(nt, "PROJECT_ROOT", Path(td)), mock.patch.dict(os.environ, {nt.KEY_ENV: "env-wins"}):
                self.assertEqual(nt.load_api_key(), "env-wins")


class CheckTests(unittest.TestCase):
    def test_check_reports_bad_photo(self):
        with tempfile.TemporaryDirectory() as td:
            make_photo(Path(td) / "ok.jpg")
            make_photo(Path(td) / "small.jpg", size=(600, 800))
            code, out, err = run_main(["check", "--photos", td])
        self.assertEqual(code, 2)
        self.assertIn("OK   ok.jpg", out)
        self.assertIn("ERROR small.jpg", err)


if __name__ == "__main__":
    unittest.main()
