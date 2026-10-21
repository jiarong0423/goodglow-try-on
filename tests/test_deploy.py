"""Public deployment (Dockerfile) and the cap on concurrent heavy computation. The image has no brand data, no keys, runs as non-root, in demo mode with a fictional catalog."""
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402

DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")


class DockerfileChecks(unittest.TestCase):
    def test_runs_mock_with_demo_catalog_as_non_root(self):
        self.assertIn("--mock --catalog demo", DOCKERFILE)
        self.assertIn("--host 0.0.0.0", DOCKERFILE)
        self.assertIn("${PORT}", DOCKERFILE)
        self.assertRegex(DOCKERFILE, r"(?m)^USER goodglow")
        self.assertIn("HEALTHCHECK", DOCKERFILE)

    def test_live_mode_needs_both_switch_and_key_and_is_capped(self):
        cmd = DOCKERFILE[DOCKERFILE.index("CMD ["):]
        self.assertIn('"$GOODGLOW_LIVE\\" = \\"1\\"', cmd)
        self.assertIn('-n \\"$YOUCAM_API_KEY\\"', cmd)                           # missing switch or missing key -> demo mode
        self.assertIn('-n \\"$GOODGLOW_JUDGE_USER\\"', cmd)
        self.assertIn('-n \\"$GOODGLOW_JUDGE_PASSWORD\\"', cmd)
        self.assertIn("--judge-live", cmd)
        self.assertIn("--max-calls ${GOODGLOW_MAX_CALLS}", cmd)
        self.assertIn("--budget-cap ${GOODGLOW_BUDGET_CAP}", cmd)
        self.assertRegex(DOCKERFILE, r"GOODGLOW_MAX_CALLS=5")
        self.assertRegex(DOCKERFILE, r"GOODGLOW_BUDGET_CAP=30")
        self.assertIn("mkdir -p runs && chown -R goodglow:goodglow runs", DOCKERFILE)

    def test_image_has_no_brand_data_secrets_or_keys(self):
        for pat in ("app/partner_shades.json", "app/partner_shades_official.json", "app/brand_codes_local.json", "app/brand_alias.json", ".env"):
            self.assertIn(pat, DOCKERIGNORE, pat)
        self.assertNotRegex(DOCKERFILE, r"YOUCAM_API_KEY\s*=")                 # the image does not write a key
        self.assertNotIn("--brand-alias", DOCKERFILE)

    def test_only_needed_files_are_copied(self):
        copied = re.findall(r"(?m)^COPY (\S+)", DOCKERFILE)
        self.assertEqual(copied, ["requirements.txt", "app", "web", "demo", "tools/noise_test.py"])
        for p in copied:
            self.assertTrue((ROOT / p).exists(), p)


class HeavyLimit(unittest.TestCase):
    def start(self, max_heavy):
        td = tempfile.TemporaryDirectory()
        state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td.name) / "l.json", catalog="demo", max_heavy=max_heavy)
        httpd = server.make_server("127.0.0.1", 0, state)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return td, httpd, state, f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(self, base, path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_busy_server_answers_503_instead_of_piling_up(self):
        old = server.HEAVY_WAIT_SECONDS
        server.HEAVY_WAIT_SECONDS = 0.3
        td, httpd, state, base = self.start(1)
        try:
            body = {"style": "natural", "undertone": "neutral", "condition": "albinism", "demo": True, "selection": {}}
            self.assertTrue(state.heavy.acquire(timeout=1))                    # the slot is occupied
            t0 = time.time()
            st, r = self.post(base, "/api/makeup", body)
            self.assertEqual((st, r["error_code"]), (503, "busy"))
            self.assertLess(time.time() - t0, 3)
            st, _ = self.post(base, "/api/match", {"hex": "#E1CDC6", "category": "foundation", "source": "demo", "top": 2})
            self.assertEqual(st, 200)                                         # lightweight requests (shade matching) are unaffected
            state.heavy.release()
            st, _ = self.post(base, "/api/makeup", body)
            self.assertEqual(st, 200)                                         # once the slot is returned, service resumes
        finally:
            server.HEAVY_WAIT_SECONDS = old
            httpd.shutdown(); httpd.server_close(); td.cleanup()

    def test_slot_is_released_after_an_error(self):
        td, httpd, state, base = self.start(1)
        try:
            self.assertEqual(self.post(base, "/api/makeup", {"style": "nope", "selection": {}})[0], 400)
            self.assertTrue(state.heavy.acquire(timeout=1))                   # the slot is returned after an error
            state.heavy.release()
        finally:
            httpd.shutdown(); httpd.server_close(); td.cleanup()


if __name__ == "__main__":
    unittest.main()
