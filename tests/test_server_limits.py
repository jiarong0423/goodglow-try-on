"""Server abuse limits: pixel cap (decompression bomb), format allowlist, malformed requests, connection cap and timeout, security headers, chunked sending of large files."""
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


@unittest.skipUnless(Image, "Pillow is not installed")
class ImageLimitTests(_Base):
    def test_huge_canvas_in_a_tiny_png_is_rejected_before_decoding(self):
        raw = png_bytes((12000, 12000))
        self.assertLess(len(raw), 2_000_000)
        for path in ("/api/patch-mask", "/api/face"):
            status, out = self.post(path, {"image": data_url(raw), "region": data_url(raw)})
            self.assertEqual(status, 413, path)
            self.assertEqual(out["error_code"], "image_too_large", path)

    def test_decompression_bomb_error_is_a_clean_400(self):
        raw = png_bytes((14000, 14000))                       # PIL's own bomb check raises DecompressionBombError
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
        self.assertNotIn("0x", json.dumps(out))              # no internal addresses or exception strings are returned


class RequestShapeTests(_Base):
    def test_non_object_json_is_a_clean_400(self):
        for raw in (b"[1,2,3]", b'"text"', b"123", b"null"):
            status, out = self.post("/api/match", None, raw=raw)
            self.assertEqual(status, 400, raw)
            self.assertEqual(out["error_code"], "bad_json")
        status, out = self.post("/api/match", None, raw=b"[" * 200000)                  # very deep nesting: a clean 400, not a dropped connection
        self.assertEqual((status, out["error_code"]), (400, "bad_json"))

    def test_non_numeric_content_length_is_a_clean_400(self):
        s = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])), timeout=10)
        try:
            s.sendall(b"POST /api/match HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\nConnection: close\r\n\r\n")
            reply = s.recv(4096)
        finally:
            s.close()
        self.assertTrue(reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400"), reply[:40])


class BodyHandlingTests(_Base):
    def test_post_without_json_content_type_is_415(self):
        req = urllib.request.Request(self.base + "/api/match", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 415)

    def test_body_over_the_limit_is_413_and_not_read(self):
        s = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])), timeout=10)
        try:
            s.sendall(f"POST /api/match HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {server.MAX_BODY + 1}\r\nConnection: close\r\n\r\n".encode())
            reply = s.recv(4096)
        finally:
            s.close()
        self.assertIn(b" 413 ", reply.split(b"\r\n")[0])

    def test_invalid_utf8_and_bad_fields_are_clean_400s(self):
        self.assertEqual(self.post("/api/match", None, raw=b"\xff\xfe{")[0], 400)
        self.assertEqual(self.post("/api/match", {"hex": ["x"], "category": 5, "top": "z"})[0], 400)

    def test_slow_body_gets_408_after_the_deadline(self):
        old = server.BODY_DEADLINE
        server.BODY_DEADLINE = 1
        try:
            s = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])), timeout=10)
            try:
                s.sendall(b"POST /api/match HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{")
                import time as _t
                _t.sleep(1.3)
                s.sendall(b"\"")                         # one more byte arrives after the deadline
                reply = s.recv(4096)
            finally:
                s.close()
        finally:
            server.BODY_DEADLINE = old
        self.assertIn(b" 408 ", reply.split(b"\r\n")[0])


class HardeningTests(_Base):
    def test_security_headers_also_on_404(self):
        for path in ("/nope-not-here", "/../server.py"):
            req = urllib.request.Request(self.base + path)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            h = ctx.exception.headers
            self.assertEqual(ctx.exception.code, 404, path)
            self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(h.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(h.get("Referrer-Policy"), "no-referrer", path)
            self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy") or "", path)
            self.assertIn("camera=(self)", h.get("Permissions-Policy") or "", path)
            self.assertEqual(h.get("Strict-Transport-Security"), "max-age=31536000", path)
        import base64, hashlib, re
        html = (server.WEB_DIR / "env_check.html").read_text(encoding="utf-8")
        for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S):
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            self.assertIn("'sha256-" + digest + "'", server.CSP, "an inline script is not allowed by the CSP hash")
        # the /judge sign-in page has an inline script too: without its hash the browser blocks it and the form would fall back to a native submit
        for page in (server.judge_auth.login_page(False), server.judge_auth.login_page(True)):
            text = page.decode("utf-8")
            scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S)
            self.assertTrue(scripts)
            for body in scripts:
                digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
                self.assertIn("'sha256-" + digest + "'", server.CSP, "the /judge inline script is not allowed by the CSP hash")
            self.assertIn('<form id="f" method="post"', text, "the sign-in form must never fall back to GET (password in the URL)")
        index = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('<form id="judge-form" method="post"', index, "the in-app sign-in form must never fall back to GET")
        # the policy itself, directive by directive: a weakened or widened policy fails here, not only a missing hash
        csp = {d.strip().split(" ", 1)[0]: d.strip().split(" ", 1)[1].split() for d in server.CSP.split(";") if d.strip()}
        self.assertEqual(sorted(csp), ["base-uri", "connect-src", "default-src", "font-src", "form-action", "frame-ancestors", "frame-src",
                                       "img-src", "media-src", "object-src", "script-src", "style-src", "worker-src"])
        self.assertEqual(csp["default-src"], ["'self'"])
        self.assertEqual((csp["frame-ancestors"], csp["base-uri"], csp["object-src"], csp["form-action"]),
                         (["'none'"], ["'none'"], ["'none'"], ["'self'"]))
        self.assertNotIn("'unsafe-inline'", csp["script-src"])
        hosts = {"https://cdn.jsdelivr.net", "https://plugins-media.makeupar.com", "https://plugins-media.perfectcorp.com", "https://d161aii70lzc9u.cloudfront.net"}
        self.assertEqual({t for t in csp["script-src"] if t.startswith(("http", "*"))}, hosts)      # no wildcard or scheme-wide script source
        for name in ("script-src", "worker-src", "connect-src", "frame-src", "style-src"):
            self.assertNotIn("https:", csp[name], name)
            self.assertNotIn("*", csp[name], name)
        self.assertEqual({t for t in csp["frame-src"]}, {"https://plugins-media.makeupar.com", "https://plugins-media.perfectcorp.com", "https://www.perfectcorp.com"})

    def test_path_traversal_never_leaves_web_dir(self):
        s = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])), timeout=10)
        try:
            s.sendall(b"GET /../app/server.py HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            reply = s.recv(65536)
        finally:
            s.close()
        self.assertIn(b" 404 ", reply.split(b"\r\n")[0])
        self.assertNotIn(b"BoundedHTTPServer", reply)

    def test_simulated_tag_is_wired_in_the_page(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for ident in ("base-tag", "adj-tag"):
            self.assertIn(f'id="{ident}"', html)
            self.assertIn("-tag", js)
        self.assertIn("sim-tag", html)

    def test_security_headers_on_api_and_static(self):
        for path in ("/api/status", "/index.html"):
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff", path)
                self.assertEqual(r.headers.get("X-Frame-Options"), "DENY", path)
            head = urllib.request.Request(self.base + path, method="HEAD")          # HEAD answers like GET without a body
            with urllib.request.urlopen(head, timeout=20) as r:
                self.assertEqual((r.status, r.read()), (200, b""), path)
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff", path)

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
            hold = socket.create_connection(("127.0.0.1", port), timeout=10)      # occupy the only slot (no request sent)
            try:
                import time
                time.sleep(0.3)
                extra = socket.create_connection(("127.0.0.1", port), timeout=10)
                extra.sendall(b"GET /api/status HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                extra.settimeout(5)
                try:
                    got = extra.recv(1024)
                except ConnectionResetError:                                         # when closed directly, the peer may receive an RST
                    got = b""
                self.assertEqual(got, b"")                                           # the second connection is closed directly, no response
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
