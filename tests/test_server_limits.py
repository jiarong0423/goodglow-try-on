"""伺服器的抗濫用限制：像素上限（解壓炸彈）、格式白名單、請求格式錯誤、連線上限與逾時、安全標頭、大檔分段送出。"""
import base64
import io
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def png_bytes(size, mode="1"):
    buf = io.BytesIO()
    Image.new(mode, size).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def data_url(raw: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(cls.td.name) / "l.json", catalog="demo")
        cls.httpd = server.make_server("127.0.0.1", 0, state)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.td.cleanup()

    def post(self, path, body, raw=None, headers=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


@unittest.skipUnless(Image, "沒有 Pillow")
class ImageLimitTests(_Base):
    def test_huge_canvas_in_a_tiny_png_is_rejected_before_decoding(self):
        raw = png_bytes((12000, 12000))
        self.assertLess(len(raw), 2_000_000)
        for path in ("/api/patch-mask", "/api/face"):
            status, out = self.post(path, {"image": data_url(raw), "region": data_url(raw)})
            self.assertEqual(status, 413, path)
            self.assertEqual(out["error_code"], "image_too_large", path)

    def test_decompression_bomb_error_is_a_clean_400(self):
        raw = png_bytes((14000, 14000))                       # PIL 自己的炸彈檢查會丟 DecompressionBombError
        status, out = self.post("/api/patch-mask", {"image": data_url(raw), "region": data_url(raw)})
        self.assertIn(status, (400, 413))
        self.assertFalse(out["ok"])

    def test_only_jpeg_png_webp_are_accepted(self):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64)).save(buf, "BMP")
        with self.assertRaises(server.RequestError) as cm:
            server.open_checked(buf.getvalue())
        self.assertEqual(cm.exception.code, "bad_photo")

    def test_garbage_bytes_are_a_clean_400(self):
        status, out = self.post("/api/patch-mask", {"image": data_url(b"not an image"), "region": data_url(b"x")})
        self.assertEqual(status, 400)
        self.assertNotIn("0x", json.dumps(out))              # 不回傳內部位址或例外字串


class RequestShapeTests(_Base):
    def test_non_object_json_is_a_clean_400(self):
        for raw in (b"[1,2,3]", b'"text"', b"123", b"null"):
            status, out = self.post("/api/match", None, raw=raw)
            self.assertEqual(status, 400, raw)
            self.assertEqual(out["error_code"], "bad_json")

    def test_non_numeric_content_length_is_a_clean_400(self):
        s = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])), timeout=10)
        try:
            s.sendall(b"POST /api/match HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\nConnection: close\r\n\r\n")
            reply = s.recv(4096)
        finally:
            s.close()
        self.assertTrue(reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400"), reply[:40])


class HardeningTests(_Base):
    def test_security_headers_on_api_and_static(self):
        for path in ("/api/status", "/index.html"):
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff", path)
                self.assertEqual(r.headers.get("X-Frame-Options"), "DENY", path)

    def test_static_files_are_served_byte_for_byte(self):
        want = (ROOT / "web" / "style.css").read_bytes()
        with urllib.request.urlopen(self.base + "/style.css", timeout=20) as r:
            self.assertEqual(r.read(), want)

    def test_handler_has_a_socket_timeout(self):
        self.assertEqual(server.Handler.timeout, server.SOCKET_TIMEOUT)
        self.assertLessEqual(server.SOCKET_TIMEOUT, 30)

    def test_connection_cap_closes_extra_connections(self):
        state = server.State(True, 3, "http://127.0.0.1:9", 50, Path(self.td.name) / "l2.json", catalog="demo")
        handler = type("H", (server.Handler,), {"state": state, "timeout": 5})
        httpd = server.BoundedHTTPServer(("127.0.0.1", 0), handler, max_connections=1)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            hold = socket.create_connection(("127.0.0.1", port), timeout=10)      # 占住唯一的名額（不送請求）
            try:
                import time
                time.sleep(0.3)
                extra = socket.create_connection(("127.0.0.1", port), timeout=10)
                extra.sendall(b"GET /api/status HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                extra.settimeout(5)
                try:
                    got = extra.recv(1024)
                except ConnectionResetError:                                         # 直接關掉時，對方可能收到 RST
                    got = b""
                self.assertEqual(got, b"")                                           # 第二條被直接關掉，沒有回應
                extra.close()
            finally:
                hold.close()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_service_worker_does_not_cache_failed_responses(self):
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("if (res.ok) {", sw)


class CredentialGuardTests(unittest.TestCase):
    def test_api_base_only_youcam_or_local(self):
        ok = ["https://yce-api-01.makeupar.com", "https://api.makeupar.com/x", "http://127.0.0.1:9", "http://localhost:8000"]
        bad = ["https://evil.example.com", "http://yce-api-01.makeupar.com", "https://makeupar.com.evil.com", "ftp://127.0.0.1", "https://127.0.0.1.evil.io"]
        for u in ok:
            self.assertTrue(server.check_api_base(u), u)
        for u in bad:
            self.assertFalse(server.check_api_base(u), u)

    def test_status_does_not_claim_a_key_in_mock_mode(self):
        with tempfile.TemporaryDirectory() as td:
            st = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td) / "l.json", catalog="demo")
            self.assertFalse(st.status()["key_present"])

    def test_cross_host_redirect_drops_authorization(self):
        import urllib.request
        sys.path.insert(0, str(ROOT / "tools"))
        import noise_test as nt
        h = nt._SafeRedirect()
        req = urllib.request.Request("https://api.example.com/a", headers={"Authorization": "Bearer FAKE"})
        new = h.redirect_request(req, None, 302, "Found", {}, "https://other.example.net/b")
        self.assertNotIn("Authorization", new.headers)
        same = h.redirect_request(req, None, 302, "Found", {}, "https://api.example.com/b")
        self.assertIn("Authorization", same.headers)


if __name__ == "__main__":
    unittest.main()
