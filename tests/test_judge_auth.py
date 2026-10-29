"""Judge login: logged-out visitors always get demo mode; only logged-in browsers make real calls; credentials come only from environment variables, and wrong attempts lock out."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import judge_auth  # noqa: E402
import server  # noqa: E402

FAKE_USER = "judge-demo"
FAKE_PASS = "not-a-real-password-123"  # gitleaks:allow  test-only fake value
WRONG = "wrong-guess-for-test-xx"


class JudgeAuthUnit(unittest.TestCase):
    def test_from_env_needs_both_and_a_long_enough_password(self):
        self.assertIsNone(judge_auth.JudgeAuth.from_env({}))
        self.assertIsNone(judge_auth.JudgeAuth.from_env({judge_auth.USER_ENV: "a"}))
        with self.assertRaises(ValueError):
            judge_auth.JudgeAuth.from_env({judge_auth.USER_ENV: "a", judge_auth.PASSWORD_ENV: "short"})
        self.assertIsNotNone(judge_auth.JudgeAuth.from_env({judge_auth.USER_ENV: FAKE_USER, judge_auth.PASSWORD_ENV: FAKE_PASS}))

    def test_check_and_lockout(self):
        a = judge_auth.JudgeAuth(FAKE_USER, FAKE_PASS)
        self.assertTrue(a.check(FAKE_USER, FAKE_PASS, now=1000))
        self.assertFalse(a.check(FAKE_USER, "wrong-password-xx", now=1000))
        self.assertFalse(a.check("other", FAKE_PASS, now=1000))
        for i in range(judge_auth.FAIL_LIMIT):
            a.check("x", "y", now=1000 + i)
        self.assertTrue(a.locked(now=1010))
        self.assertFalse(a.locked(now=1000 + judge_auth.FAIL_WINDOW + 20))

    def test_token_expiry_and_tamper(self):
        a = judge_auth.JudgeAuth(FAKE_USER, FAKE_PASS)
        t = a.issue(now=5000)
        self.assertTrue(a.valid_token(t, now=5001))
        self.assertFalse(a.valid_token(t, now=5000 + judge_auth.SESSION_SECONDS + 1))
        exp, _, sig = t.partition(".")
        self.assertFalse(a.valid_token(f"{int(exp) + 99999}.{sig}", now=5001))      # change the expiry time
        self.assertFalse(a.valid_token("", now=5001))
        self.assertFalse(a.valid_token("abc.def", now=5001))
        other = judge_auth.JudgeAuth(FAKE_USER, FAKE_PASS)                          # a different start has a different signing key
        self.assertFalse(other.valid_token(t, now=5001))

    def test_cookie_header(self):
        a = judge_auth.JudgeAuth(FAKE_USER, FAKE_PASS)
        t = a.issue()
        self.assertTrue(a.valid_cookie_header(f"x=1; {judge_auth.COOKIE}={t}"))
        self.assertFalse(a.valid_cookie_header(None))
        self.assertFalse(a.valid_cookie_header(f"{judge_auth.COOKIE}=garbage"))
        self.assertIn("HttpOnly", judge_auth.set_cookie_value(t, True))
        self.assertIn("Secure", judge_auth.set_cookie_value(t, True))
        self.assertNotIn("Secure", judge_auth.set_cookie_value(t, False))


class JudgeAuthServer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        ledger = Path(self.td.name) / "l.json"
        demo = server.State(True, 3, "http://127.0.0.1:9", 50, ledger, catalog="demo")
        live = server.State(True, 7, "http://127.0.0.1:9", 50, ledger, catalog="demo")      # max_calls serves as a marker for which state was reached
        self.auth = judge_auth.JudgeAuth(FAKE_USER, FAKE_PASS)
        self.httpd = server.make_server("127.0.0.1", 0, demo, live, self.auth)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()

    def req(self, path, body=None, cookie=None, headers=None):
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, headers=h, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def status(self, cookie=None):
        st, raw, _ = self.req("/api/status", cookie=cookie)
        self.assertEqual(st, 200)
        return json.loads(raw)

    def login(self, user=FAKE_USER, password=FAKE_PASS, headers=None):
        return self.req("/api/judge-login", {"username": user, "password": password}, headers=headers)

    def test_anonymous_is_demo_and_signed_in_is_live_state(self):
        s = self.status()
        self.assertEqual((s["max_calls"], s["judge_login"], s["judge_signed_in"]), (3, True, False))
        st, raw, hdr = self.login()
        self.assertEqual(st, 200)
        cookie = hdr["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)                      # local http test
        pair = cookie.split(";")[0]
        s = self.status(cookie=pair)
        self.assertEqual((s["max_calls"], s["judge_signed_in"]), (7, True))
        self.assertEqual(self.status(cookie=pair + "tamper")["max_calls"], 3)   # a tampered cookie is treated as not logged in

    def test_secure_flag_behind_https_proxy(self):
        st, _, hdr = self.login(headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(st, 200)
        self.assertIn("Secure", hdr["Set-Cookie"])

    def test_wrong_login_and_lockout(self):
        st, raw, hdr = self.login(password=WRONG)
        self.assertEqual(st, 401)
        self.assertIsNone(hdr.get("Set-Cookie"))
        for _ in range(judge_auth.FAIL_LIMIT):
            self.login(password=WRONG)
        self.assertEqual(self.login()[0], 429)                  # during lockout even the correct password is rejected

    def test_logout_clears_cookie(self):
        st, _, hdr = self.req("/api/judge-logout", {})
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=0", hdr["Set-Cookie"])
        r = urllib.request.Request(self.base + "/api/judge-logout", data=b"x=1", method="POST",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as cm:                 # a cross-site form post cannot sign a judge out
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(cm.exception.code, 415)

    def test_login_page_is_public_and_does_not_leak_credentials(self):
        st, raw, _ = self.req("/judge")
        self.assertEqual(st, 200)
        html = raw.decode()
        self.assertIn("Judge sign-in", html)
        self.assertNotIn(FAKE_PASS, html)
        self.assertNotIn(FAKE_USER, html)

    def test_bad_login_bodies(self):
        self.assertEqual(self.req("/api/judge-login", [1, 2])[0], 400)
        for ctype, code in (("application/json", 400), (None, 415), ("text/plain", 415)):
            h = {"Content-Type": ctype} if ctype else {}
            r = urllib.request.Request(self.base + "/api/judge-login", data=b"not json", method="POST", headers=h)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(r, timeout=10)
            self.assertEqual(ctx.exception.code, code, ctype)
        r = urllib.request.Request(self.base + "/api/judge-login", data=b"\xff\xfe{", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:       # bad UTF-8 is a 400, not a dropped connection
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_lockout_is_per_source_so_one_source_cannot_lock_out_the_judge(self):
        attacker = {"X-Forwarded-For": "203.0.113.9"}
        for _ in range(judge_auth.FAIL_LIMIT):
            self.login(password=WRONG, headers=attacker)
        self.assertEqual(self.login(headers=attacker)[0], 429)
        self.assertEqual(self.login(headers={"X-Forwarded-For": "198.51.100.7"})[0], 200)

    def test_forged_leading_forwarded_for_entry_does_not_dodge_the_lock(self):
        for i in range(judge_auth.FAIL_LIMIT):
            self.login(password=WRONG, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"})
        self.assertEqual(self.login(headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.9"})[0], 429)


class JudgeLinkInApp(unittest.TestCase):
    def test_banner_links_to_judge_page_only_when_server_enables_it(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        i = js.index("if (s.judge_login)")
        block = js[i:i + 400]
        self.assertIn("a.href = '/judge'", block)
        self.assertIn("s.judge_signed_in", block)
        self.assertIn("judge_login", (ROOT / "app" / "server.py").read_text(encoding="utf-8"))


class BannerCounterInApp(unittest.TestCase):
    def test_live_counter_is_reread_after_every_billed_call(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const BILLED_ROUTES = /^\\/api\\/(makeup|looks|layers|face)$/;", js)
        self.assertIn("refreshBanner();", js[js.index("async function api("):js.index("function optionList(")])
        self.assertIn("async function refreshBanner()", js)
        self.assertIn("id = 'mode-text'", js)          # the counter text lives in its own node, so the sign-in link survives a refresh


class NoAuthConfigured(unittest.TestCase):
    def test_routes_do_not_exist_without_judge_login(self):
        with tempfile.TemporaryDirectory() as td:
            demo = server.State(True, 3, "http://127.0.0.1:9", 50, Path(td) / "l.json", catalog="demo")
            httpd = server.make_server("127.0.0.1", 0, demo)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                for path, data in (("/judge", None), ("/api/judge-login", b"{}")):
                    r = urllib.request.Request(base + path, data=data, method="POST" if data else "GET")
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(r, timeout=10)
                    self.assertEqual(ctx.exception.code, 404, path)
                with urllib.request.urlopen(base + "/api/status", timeout=10) as resp:
                    self.assertNotIn("judge_login", json.load(resp))
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
