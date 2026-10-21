"""Assist layer: parts YouCam cannot do or handle on its own are filled in by local coloring on the phone .
Reproduction steps are in demo/repro/README.md: `app/server.py --mock --simulate-youcam-error`, go to the try-on page."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402

JS = (ROOT / "web/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web/index.html").read_text(encoding="utf-8")


def start(simulate_error=None):
    td = tempfile.TemporaryDirectory()
    state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td.name) / "l.json", catalog="demo", simulate_error=simulate_error)
    httpd = server.make_server("127.0.0.1", 0, state)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return td, httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def post(base, path, body, lang="zh"):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-App-Lang": lang}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class SimulatedYouCamError(unittest.TestCase):
    def test_flag_makes_tryon_requests_fail_like_youcam_and_hint_is_localized(self):
        td, httpd, base = start("error_face_angle_invalid")
        try:
            body = {"style": "natural", "undertone": "neutral", "condition": "albinism", "demo": True, "selection": {"foundation": "full"}}
            for path in ("/api/makeup", "/api/looks", "/api/layers"):
                st, r = post(base, path, body)
                self.assertEqual((st, r["error_code"]), (422, "error_face_angle_invalid"), path)
            st, r = post(base, "/api/makeup", body, lang="en")
            self.assertRegex(r["hint"], r"^[\x00-\x7f]+$")                      # English mode hint contains no Chinese
            st, r = post(base, "/api/match", {"hex": "#E1CDC6", "category": "foundation", "source": "demo", "top": 2})
            self.assertEqual(st, 200)                                           # local matching is unaffected
        finally:
            httpd.shutdown(); httpd.server_close(); td.cleanup()

    def test_default_server_does_not_fail(self):
        td, httpd, base = start(None)
        try:
            st, _ = post(base, "/api/makeup", {"style": "natural", "undertone": "neutral", "condition": "albinism", "demo": True, "selection": {}})
            self.assertEqual(st, 200)
        finally:
            httpd.shutdown(); httpd.server_close(); td.cleanup()

    def test_flag_is_refused_without_mock(self):
        with self.assertRaises(ValueError):
            server.State(False, 3, "http://127.0.0.1:9", 50, simulate_error="error_face_angle_invalid")


class EstimatedUnits(unittest.TestCase):
    def test_mock_counts_what_live_mode_would_spend(self):
        td, httpd, base = start(None)
        try:
            def status():
                with urllib.request.urlopen(base + "/api/status", timeout=10) as r:
                    return json.load(r)
            self.assertEqual(status()["est_units"], 0)
            body = {"style": "natural", "undertone": "neutral", "condition": "albinism", "demo": True, "selection": {}}
            self.assertEqual(post(base, "/api/makeup", body)[0], 200)
            self.assertEqual(post(base, "/api/makeup", body)[0], 200)
            self.assertEqual(status()["est_units"], 2)                      # Makeup VTO costs 1 unit per call
            post(base, "/api/face", {"style": "natural", "demo": True, "condition": "albinism"})
            st = status()
            self.assertEqual((st["est_units"], st["est_calls"]["face-attr-analysis(1-5)"]), (12, 1))   # Face Attribute costs 10 units
            self.assertEqual(st["calls_used"], 0)                           # no real call made, no units charged
        finally:
            httpd.shutdown(); httpd.server_close(); td.cleanup()


class ClientFallbackWiring(unittest.TestCase):
    def test_try_on_and_adjust_pages_fall_back_to_local_simulation(self):
        self.assertIn("async function localAssistImage()", JS)
        self.assertEqual(JS.count("await localAssistImage()"), 2)                 # try-on page and per-item adjustment page
        self.assertIn("window.ShadePreview.render(", JS[JS.index("async function localAssistImage()"):JS.index("function sourceLine")])
        self.assertIn("window.Camouflage.apply(", JS[JS.index("async function localAssistImage()"):JS.index("function sourceLine")])  # vitiligo entry adds local concealer patching
        self.assertNotIn("simulate_error", JS)
        self.assertNotIn("simulate-youcam", JS)                                  # the frontend does not recognize the simulation flag: follows the real error path, no back door

    def test_source_is_labelled_on_the_try_on_page(self):
        self.assertIn('id="base-source"', HTML)
        self.assertGreaterEqual(JS.count("sourceLine("), 3)


if __name__ == "__main__":
    unittest.main()
