"""公開部署（Dockerfile）與重運算並行上限。映像不含品牌資料、不帶金鑰、非 root、展示模式＋虛構型錄。"""
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

    def test_image_has_no_brand_data_secrets_or_keys(self):
        for pat in ("app/partner_shades.json", "app/partner_shades_official.json", "app/brand_codes_local.json", "app/brand_alias.json", ".env"):
            self.assertIn(pat, DOCKERIGNORE, pat)
        self.assertNotRegex(DOCKERFILE, r"YOUCAM_API_KEY\s*=")                 # 映像裡不寫金鑰
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
            self.assertTrue(state.heavy.acquire(timeout=1))                    # 名額被占住
            t0 = time.time()
            st, r = self.post(base, "/api/makeup", body)
            self.assertEqual((st, r["error_code"]), (503, "busy"))
            self.assertLess(time.time() - t0, 3)
            st, _ = self.post(base, "/api/match", {"hex": "#E1CDC6", "category": "foundation", "source": "demo", "top": 2})
            self.assertEqual(st, 200)                                         # 輕量請求（色號比對）不受影響
            state.heavy.release()
            st, _ = self.post(base, "/api/makeup", body)
            self.assertEqual(st, 200)                                         # 名額還回去就恢復
        finally:
            server.HEAVY_WAIT_SECONDS = old
            httpd.shutdown(); httpd.server_close(); td.cleanup()

    def test_slot_is_released_after_an_error(self):
        td, httpd, state, base = self.start(1)
        try:
            self.assertEqual(self.post(base, "/api/makeup", {"style": "nope", "selection": {}})[0], 400)
            self.assertTrue(state.heavy.acquire(timeout=1))                   # 出錯後名額有還
            state.heavy.release()
        finally:
            httpd.shutdown(); httpd.server_close(); td.cleanup()


if __name__ == "__main__":
    unittest.main()
