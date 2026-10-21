"""評審登入：只用來打開線上版的「真實 YouCam 呼叫」；展示模式（重播錄好的結果）一直免登入。

- 帳號密碼只來自環境變數 GOODGLOW_JUDGE_USER／GOODGLOW_JUDGE_PASSWORD（部署平台設，不進 repo、不印出）。
  沒設、或密碼少於 MIN_PASSWORD 個字元，就視為沒有評審登入，伺服器不會開真實呼叫。
- 登入成功發一個簽章 cookie（HttpOnly、SameSite=Lax，6 小時）；簽章金鑰每次啟動隨機產生，重啟後舊 cookie 失效。
- 連續輸錯 FAIL_LIMIT 次後鎖 FAIL_WINDOW 秒（全域計算，擋暴力猜測）。
"""
import hashlib
import hmac
import http.cookies
import os
import secrets
import threading
import time
from collections import deque

COOKIE = "gg_judge"
SESSION_SECONDS = 6 * 3600
MIN_PASSWORD = 10
FAIL_LIMIT = 6
FAIL_WINDOW = 60

USER_ENV = "GOODGLOW_JUDGE_USER"
PASSWORD_ENV = "GOODGLOW_JUDGE_PASSWORD"


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


class JudgeAuth:
    def __init__(self, user: str, password: str):
        if not user or len(password) < MIN_PASSWORD:
            raise ValueError(f"評審帳號不可空白，密碼至少 {MIN_PASSWORD} 個字元")
        self._user = _digest(user)
        self._password = _digest(password)
        self._secret = secrets.token_bytes(32)
        self._fails: deque[float] = deque()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        user, password = env.get(USER_ENV, ""), env.get(PASSWORD_ENV, "")
        if not user or not password:
            return None
        return cls(user, password)

    # ---- 輸錯鎖定
    def locked(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            while self._fails and now - self._fails[0] > FAIL_WINDOW:
                self._fails.popleft()
            return len(self._fails) >= FAIL_LIMIT

    def _record_failure(self, now: float) -> None:
        with self._lock:
            self._fails.append(now)

    # ---- 帳密比對（兩項都比，用定時比較）
    def check(self, user: str, password: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        ok_user = hmac.compare_digest(_digest(str(user)), self._user)
        ok_pass = hmac.compare_digest(_digest(str(password)), self._password)
        if ok_user and ok_pass:
            return True
        self._record_failure(now)
        return False

    # ---- 登入狀態 cookie
    def _sign(self, expiry: int) -> str:
        return hmac.new(self._secret, str(expiry).encode(), hashlib.sha256).hexdigest()

    def issue(self, now: float | None = None) -> str:
        expiry = int((time.time() if now is None else now) + SESSION_SECONDS)
        return f"{expiry}.{self._sign(expiry)}"

    def valid_token(self, token: str, now: float | None = None) -> bool:
        exp, _, sig = (token or "").partition(".")
        if not exp.isdigit() or not sig:
            return False
        if int(exp) < (time.time() if now is None else now):
            return False
        return hmac.compare_digest(sig, self._sign(int(exp)))

    def valid_cookie_header(self, header: str | None, now: float | None = None) -> bool:
        if not header:
            return False
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(header)
        except http.cookies.CookieError:
            return False
        morsel = jar.get(COOKIE)
        return bool(morsel) and self.valid_token(morsel.value, now)


def set_cookie_value(token: str, secure: bool) -> str:
    flags = f"Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}" + ("; Secure" if secure else "")
    return f"{COOKIE}={token}; {flags}"


def clear_cookie_value(secure: bool) -> str:
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" + ("; Secure" if secure else "")


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Judge sign-in</title>
<style>
:root{color-scheme:light dark;--bg:#faf7f5;--fg:#2a2523;--mut:#6b625e;--card:#fff;--line:#e4dcd7;--acc:#8a4b3a}
@media (prefers-color-scheme:dark){:root{--bg:#1d1a19;--fg:#f1ebe8;--mut:#b3a8a3;--card:#272322;--line:#3b3533;--acc:#e0a08c}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:420px;margin:0 auto;padding:32px 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}
h1{font-size:1.25rem;margin:0 0 8px}p{color:var(--mut);margin:0 0 16px}
label{display:block;font-weight:600;margin:12px 0 4px}
input{width:100%;box-sizing:border-box;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font-size:16px}
button{margin-top:16px;width:100%;padding:12px;border:0;border-radius:10px;background:var(--acc);color:#fff;font-size:16px;font-weight:600}
button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
#msg{min-height:1.4em;margin-top:12px;color:var(--acc)}
a{color:var(--acc)}
</style></head><body><main><div class="card">
<h1>Judge sign-in</h1>
<p>The app works without signing in (demo mode replays results recorded from real YouCam calls).
Signing in switches on live mode: your own photo is sent to the YouCam API. Live calls are capped.</p>
<form id="f" __HIDE_FORM__>
<label for="u">Username</label><input id="u" name="username" autocomplete="username" required>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>
<div id="out" __HIDE_OUT__><p>You are signed in: live mode is on for this browser.</p>
<button id="go">Open the app</button><button id="bye" class="ghost">Sign out</button></div>
<div id="msg" role="status"></div>
<p style="margin-top:16px"><a href="/?lang=en">Back to the app (demo mode)</a></p>
</div></main>
<script>
const msg=document.getElementById('msg');
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return [r.status,await r.json().catch(()=>({}))];}
document.getElementById('f').addEventListener('submit',async(e)=>{e.preventDefault();msg.textContent='';
  const [st,j]=await post('/api/judge-login',{username:document.getElementById('u').value,password:document.getElementById('p').value});
  if(st===200&&j.ok){location.href='/?lang=en';}else{msg.textContent=st===429?'Too many attempts. Please wait a minute.':'Wrong username or password.';}});
document.getElementById('go').addEventListener('click',()=>{location.href='/?lang=en';});
document.getElementById('bye').addEventListener('click',async()=>{await post('/api/judge-logout');location.reload();});
</script></body></html>
"""


def login_page(signed_in: bool) -> bytes:
    hidden = "hidden"
    html = LOGIN_PAGE.replace("__HIDE_FORM__", hidden if signed_in else "").replace("__HIDE_OUT__", "" if signed_in else hidden)
    return html.encode("utf-8")
