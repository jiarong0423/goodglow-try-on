#!/usr/bin/env python3
"""Local server: serves the PWA and proxies YouCam Makeup VTO (healthy-glow try-on for albinism).

- The API key is read only on the server side (environment variable or the project .env), never sent to the browser, never printed.
- Photos are processed in memory only and never written to disk; result images are turned into data URLs for the browser, and the server keeps nothing.
- The frontend sends only "look style + each option id"; the parameters are built by app/looks.py from presets.json, which also checks whether the style allows them.
- --mock: no connection to YouCam, no points spent. With demo photos the recorded real results are replayed; with the person's own photo the original image is returned, marked as demo mode.

Usage:
  python3 app/server.py --mock
  python3 app/server.py --max-calls 20
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import re
import shutil
import sys
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app"))
import budget as bg  # noqa: E402
import color_engine as ce  # noqa: E402
import looks  # noqa: E402
import noise_test as nt  # noqa: E402
import recommend as rec  # noqa: E402
import skin_card as sc  # noqa: E402
import face_rules  # noqa: E402
import i18n_srv  # noqa: E402
import brand_alias  # noqa: E402
import judge_auth  # noqa: E402

WEB_DIR = ROOT / "web"
MAX_BODY = 8 * 1024 * 1024               # request body cap (the app sends photos of at most about 2 MB after in-browser resizing)
BODY_DEADLINE = 15                        # whole-body read deadline in seconds: a trickle upload cannot hold a thread
MAX_RESULT_BYTES = 12 * 1024 * 1024       # cap on a result image downloaded from YouCam
MAX_PIXELS = 16_000_000                   # images whose header declares more pixels than this are rejected before decoding (small file, huge image = decompression bomb)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
SOCKET_TIMEOUT = 20                       # read/write timeout of a single connection (seconds): slow or stalled connections must not hold a thread forever
MAX_CONNECTIONS = 64                      # upper limit of concurrently handled connections; when full, new connections are closed immediately
# for public deployment: how many CPU-heavy or YouCam-calling requests (light-patch mask, try-on, layers, facial features) may run at once; if they cannot queue, answer 503 instead of dragging the whole machine down.
HEAVY_ROUTES = {"/api/patch-mask", "/api/makeup", "/api/looks", "/api/layers", "/api/face"}
HEAVY_WAIT_SECONDS = 8
DEFAULT_MAX_HEAVY = 2
MIN_SHORT = 640           # reject low resolution (no upscaling). Camera Kit on phones outputs about 680x1080, and the old threshold of 1080 would reject every Kit photo (, measured on a device)
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")    # the expected-makeup zone colors accept only #RRGGBB
VTO_MAX_LONG = 1919       # Makeup VTO requires the long side < 1920
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".jpg": "image/jpeg",
    ".mjs": "text/javascript; charset=utf-8", ".wasm": "application/wasm",   # web/vendor: ONNX Runtime Web (10-16)
}
ERROR_HINTS = {
    "error_face_position_invalid": "臉要完整出現在畫面裡",
    "error_face_position_too_small": "臉太小：手機拿近一點",
    "error_face_position_out_of_boundary": "臉超出畫面：往中間移一點",
    "error_face_angle_invalid": "臉太斜：手機舉到眼睛高度、正對鏡頭",
    "error_below_min_image_size": "照片解析度不夠：改用前鏡頭最高畫質",
}

# demo photos (Pexels, modifiable license) and recorded real results: all under demo/ (version-controlled, sources in demo/SOURCES.md)
DEMO_DIR = ROOT / "demo"
#: switched to the bright photos of the four-person standard set (the old 19276432 was dark and landed on card level 5; old photos and replay images are archived in runs/)
DEMO_PHOTO = DEMO_DIR / "photos" / "standard4" / "albinism_F_5273693.jpg"
# points: sampling points (demo photos only). Albinism: face landmark 50 (the lit-side cheek) and the middle of the left brow; vitiligo: confirmed manually on a 10% grid image
DEMOS = {
    "albinism": {"photo": DEMO_PHOTO, "credit": "Pexels（可修改授權）https://www.pexels.com/photo/5273693/",
                 "replay": DEMO_DIR / "replay" / "albinism",
                 "points": {"normal": [0.302, 0.527], "brow": [0.325, 0.373]}, "face": None, "expected": None},
    "vitiligo": {"photo": DEMO_DIR / "photos" / "vitiligo_20551205.jpg",
                 "credit": "Pexels（可修改授權）https://www.pexels.com/photo/20551205/",
                 "replay": DEMO_DIR / "replay" / "vitiligo",
                 "points": {"normal": [0.72, 0.58], "patch": [0.33, 0.52], "brow": [0.62, 0.41]},
                 "face": DEMO_DIR / "face" / "vitiligo_20551205.json",
                 # expected makeup (simulated): from the AI-generated image only the light/dark colors are taken and applied back to the original image; shades are sampled from this image's three zones
                 "expected": DEMO_DIR / "expected" / "vitiligo_20551205.json"},
}

# finalized looks (simulated) per entry: tools/export_final_looks.py exports from runs/ to demo/final/ (, owner authorized wiring them into both entries)
FINAL_DIR = DEMO_DIR / "final"
FINAL_KEYS = {("albinism", "F"): "albinism_F", ("albinism", "M"): "albinism_M", ("vitiligo", "M"): "vitiligo_M", ("vitiligo", "F"): "vitiligo_F"}
FINAL_DEFAULT_WHO = {"albinism": "F", "vitiligo": "M"}


def _sensitivity(v) -> dict | None:
    """Answers to the three sensitivity questions: only known keys and values are accepted, everything else is ignored."""
    if not isinstance(v, dict):
        return None
    out = {}
    if v.get("irritation") in ("often", "sometimes", "never"):
        out["irritation"] = v["irritation"]
    if v.get("fragrance") in ("yes", "no", "unsure"):
        out["fragrance"] = v["fragrance"]
    if isinstance(v.get("phototherapy"), bool):
        out["phototherapy"] = v["phototherapy"]
    return out or None


def _ratio(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def demo_condition(payload: dict) -> str:
    return "vitiligo" if payload.get("condition") == "vitiligo" else "albinism"


EXACT_ITEMS = ("foundation", "contour", "highlight", "brow", "lash", "blush", "lip")


def exact_key(condition: str, style: str, selection: dict) -> str:
    """Key of an exact replay: entry + look style + the try-on parameters actually sent to YouCam (looks.build_effects).
    A change in options, colors (foundation shade, brow color, skin color) or presets changes the key, so old recordings are never used by mistake (done this way after the 10-16 brow-card revision)."""
    try:
        effects = looks.build_effects(selection)
    except looks.PresetError:
        effects = {k: selection.get(k, "keep") for k in EXACT_ITEMS}
    norm = {"c": condition, "s": style, "e": effects}
    return hashlib.sha1(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def exact_replay(condition: str, style: str, selection: dict) -> bytes | None:
    """Demo mode: the result recorded by tools/record_demo_replays.py with exactly the same set of options; None if there is none."""
    f = DEMOS[condition]["replay"] / "exact" / f"{exact_key(condition, style, selection)}.jpg"
    return f.read_bytes() if f.is_file() else None


def replay_image(condition: str, key: str, style: str = "natural") -> bytes | None:
    """Demo mode: find a recorded result by look style and variant (e.g. grooming_clear.jpg); if not found, fall back to another style of the same variant."""
    folder = DEMOS[condition]["replay"]
    if key.startswith("foundation_only_"):
        # foundation only: use the recorded first-layer result of the layer view (same look style preferred, otherwise the first layer of any style)
        cands = [folder / f"{style}_layer1_foundation.jpg"] + sorted(folder.glob("*_layer1_foundation.jpg"))
        f = next((c for c in cands if c.is_file()), None)
        return f.read_bytes() if f else None
    if key.startswith("foundation_"):
        f = folder / f"{style}_{key}.jpg"
        f = f if f.is_file() else folder / f"{key}.jpg"
        return f.read_bytes() if f.is_file() else None
    names = [f"{key}.jpg"] if key in ("red1", "red2", "red3", "champagne") else []
    base = key if key in ("glow", "clear") else "clear"
    names += [f"{style}_{base}.jpg", f"natural_{base}.jpg", f"grooming_{base}.jpg", f"polished_{base}.jpg"]
    for n in names:
        f = folder / n
        if f.is_file():
            return f.read_bytes()
    return None


class RequestError(Exception):
    def __init__(self, status: int, code: str, hint: str):
        super().__init__(hint)
        self.status, self.code, self.hint = status, code, hint


def to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def decode_image(payload: dict) -> bytes:
    data = payload.get("image", "")
    if "," in data[:100]:
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise RequestError(400, "bad_photo", "照片編碼錯誤") from None


def open_checked(raw: bytes):
    """Check the header format and image size before opening the image (PIL decodes lazily): only JPEG / PNG / WebP are accepted, pixel limit MAX_PIXELS."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(raw))
        fmt, (w, h) = im.format, im.size
    except Exception:  # noqa: BLE001  includes DecompressionBombError and format errors
        raise RequestError(400, "bad_photo", "讀不到這張照片") from None
    if fmt not in ALLOWED_FORMATS:
        raise RequestError(400, "bad_photo", "照片格式只收 JPEG、PNG、WebP")
    if w * h > MAX_PIXELS:
        raise RequestError(413, "image_too_large", "照片畫面太大，請縮小後再試")
    return im


def prepare_vto_image(raw: bytes) -> bytes:
    """Correct the orientation, reject short side < MIN_SHORT, shrink to long side < 1920, re-encode (strips EXIF)."""
    from PIL import Image, ImageOps

    try:
        with open_checked(raw) as src:
            img = ImageOps.exif_transpose(src).convert("RGB")
    except RequestError:
        raise
    except Exception:  # noqa: BLE001
        raise RequestError(400, "bad_photo", "讀不到這張照片") from None
    w, h = img.size
    if min(w, h) < MIN_SHORT:
        raise RequestError(400, "low_resolution",
                           f"照片解析度不夠（短邊 {min(w, h)}px，需要 {MIN_SHORT}px 以上）：請用前鏡頭最高畫質重拍")
    if max(w, h) > VTO_MAX_LONG:
        s = VTO_MAX_LONG / max(w, h)
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


INTERNAL_KEYS = {"note", "_note", "foundation_note", "face_rules", "demo_note", "partner_note", "official_note"}


def public_view(obj):
    """Data returned to the frontend contains no internal note fields (approach, source discussion, dates etc. stay in file notes only and are not shipped to the screen or network responses)."""
    if isinstance(obj, dict):
        return {k: public_view(v) for k, v in obj.items() if k not in INTERNAL_KEYS}
    if isinstance(obj, list):
        return [public_view(x) for x in obj]
    return obj


_REQ = threading.local()      # language of the current request (set as soon as do_GET / do_POST starts), used by mock_label


def _label_font(size: int, lang: str):
    from PIL import ImageFont
    cands = (["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]
             if lang != "en" else []) + ["/System/Library/Fonts/Supplemental/Arial.ttf",
                                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in cands:
        try:
            return ImageFont.truetype(path, size), path.endswith(".ttc") and lang != "en"
        except OSError:
            continue
    return ImageFont.load_default(), False


def mock_label(jpeg: bytes, text: str = "") -> bytes:
    """Own photo in demo mode: return the original image with a thin label added at the bottom (by language), so it is not mistaken for a real try-on result.
    The option list is no longer drawn onto the image (the options are already shown in the caption below the image; 10-21 phone test: the old black bar was in English and got truncated)."""
    from PIL import Image, ImageDraw

    lang = getattr(_REQ, "lang", "zh")
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    d = ImageDraw.Draw(img)
    size = max(18, img.width // 28)
    font, cjk = _label_font(size, lang)
    label = "展示模式｜模擬（未呼叫 YouCam）" if (lang != "en" and cjk) else "Demo mode | simulated (YouCam not called)"
    bar = int(size * 1.9)
    d.rectangle([0, img.height - bar, img.width, img.height], fill=(0, 0, 0))
    d.text((size // 2, img.height - bar + (bar - size) // 2 - 2), label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def nearest_replay(condition: str, style: str = "natural") -> bytes | None:
    """When a demo photo has no recording for exactly the same set of options in demo mode, fall back to the closest recorded official result (no burned-in text;
    the caption below the image and MOCK_SAME state that demo mode recorded only a few combinations)."""
    folder = DEMOS[condition]["replay"]
    cands = [folder / f"{style}_glow.jpg", folder / f"{style}_clear.jpg", folder / "natural_glow.jpg"] + sorted(folder.glob("*.jpg"))
    f = next((c for c in cands if c.is_file()), None)
    if f:
        return f.read_bytes()
    # no named recording: use this entry's finalized look image (the full look made on the same demo photo, demo/final), and only then any exact recording
    try:
        key = FINAL_KEYS.get((condition, FINAL_DEFAULT_WHO[condition]))
        rel = json.loads((FINAL_DIR / "recipes.json").read_text(encoding="utf-8"))[key]["images"].get("final")
        if rel and (FINAL_DIR / rel).is_file():
            return (FINAL_DIR / rel).read_bytes()
    except (OSError, KeyError, ValueError):
        pass
    exact = sorted((folder / "exact").glob("*.jpg"))
    return exact[0].read_bytes() if exact else None


def replay_key(selection: dict, which: str | None) -> str:
    if selection.get("foundation", "keep") != "keep":
        only = all(selection.get(k, "keep") == "keep" for k in ("blush", "lip", "brow", "lash"))   # highlight does not affect which replay image is chosen
        # foundation only = the "whole-face foundation" of the simplified flow; replay if recorded, otherwise label it as demo mode
        return f"foundation_only_{selection['foundation']}" if only else f"foundation_{selection['foundation']}"
    if which in ("glow", "clear"):
        return which
    lip, lash = selection.get("lip", "keep"), selection.get("lash", "keep")
    if lip in ("red1", "red2", "red3"):
        return lip
    if lash == "champagne":
        return "champagne"
    if any(selection.get(k, "keep") != "keep" for k in ("brow", "lash")):
        return "clear"
    return "glow"


def find_urls(obj, out: list) -> list:
    if isinstance(obj, dict):
        for v in obj.values():
            find_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_urls(v, out)
    elif isinstance(obj, str) and obj.startswith(("http://", "https://")):
        out.append(obj)
    return out


class State:
    def __init__(self, mock: bool, max_calls: int, api_base: str, budget_cap: int = 60,
                 ledger: Path | None = None, catalog: str = "all", alias: bool = False,
                 simulate_error: str | None = None, max_heavy: int = DEFAULT_MAX_HEAVY):
        self.mock = mock
        self.heavy = threading.BoundedSemaphore(max(1, int(max_heavy)))
        # simulate_error (--simulate-youcam-error): only used with --mock; makes try-on requests return a YouCam error code, reproducing "the local simulation takes over when YouCam cannot process it"
        if simulate_error and not mock:
            raise ValueError("--simulate-youcam-error can only be used with --mock")
        self.simulate_error = simulate_error
        self.est_units = 0           # estimate of "how many points this would cost in live mode" while in demo mode (official prices in app/budget.py PRICES); spends no points and does not call YouCam
        self.est_calls = {}
        # alias=True (--brand-alias): for recordings, screenshots and public pages. Internally real brand data is still used for matching; brand names returned to the screen become letter aliases and product names become categories
        self.alias = brand_alias.load() if alias else None
        # catalog="demo": for recording videos and screenshots, shows only the fictional demo catalog, so no third-party brand names appear on screen (contest rule: videos must not contain third-party trademarks)
        self.catalog = "demo" if catalog == "demo" else "all"
        # the home-page numbers and the shade-page text must match the catalog actually loaded: with no current official data (clean clone) or when demo is specified, only the fictional demo catalog exists
        self.catalog_kind = "demo" if self.catalog == "demo" or not ce.OFFICIAL_CATALOG.exists() else "official"
        _cats = ce.load_catalogs(self.catalog_kind)
        self.catalog_stats = {"shades": sum(len(p["shades"]) for p in _cats), "brands": len({p["brand"] for p in _cats})}
        self.budget = bg.Budget(budget_cap, ledger or bg.LEDGER)
        self.max_calls = max_calls
        self.calls = 0
        self.lock = threading.Lock()
        self.key = "" if mock else nt.load_api_key()
        self.client = None if mock else nt.YouCamClient(self.key, api_base)
        self.presets = looks.load_presets()

    def status(self) -> dict:
        return {"mode": "mock" if self.mock else "live", "calls_used": self.calls, "max_calls": self.max_calls,
                "key_present": bool(self.key), "presets_version": self.presets["version"],
                "budget": self.budget.status(), "catalog": self.catalog,
                "catalog_kind": self.catalog_kind, "catalog_stats": self.catalog_stats,
                "est_units": self.est_units, "est_calls": dict(self.est_calls)}

    def src(self, payload: dict) -> str:
        """Catalog source: demo-catalog mode always uses the fictional demo catalog, otherwise follows the request (default all = current official shades first)."""
        return "demo" if self.catalog == "demo" else payload.get("source", "all")

    # ---- one Makeup VTO
    def render(self, jpeg: bytes, selection: dict, demo: str | None, which: str | None = None,
               style: str = "natural", layer: str | None = None) -> str:
        """demo: the entry of a demo photo (albinism / vitiligo), None for the person's own photo. layer: which layer of the layer view (used for demo-mode replay)."""
        if self.mock:
            self._estimate("makeup-vto")
            if demo:
                if layer:
                    f = DEMOS[demo]["replay"] / f"{style}_{layer}.jpg"
                    data = f.read_bytes() if f.is_file() else None
                    if data:
                        return to_data_url(data)
                    if layer.startswith("final"):
                        layer = None                     # the last layer = the full look, fall back to the recording of the full look
                if not layer:
                    data = exact_replay(demo, style, selection) or replay_image(demo, replay_key(selection, which), style)
                    if not data and selection.get("foundation", "keep") != "keep":
                        # a version including foundation is not recorded yet: use the recording of the same variant without foundation (the screen already says it is a similar result)
                        data = replay_image(demo, replay_key(dict(selection, foundation="keep"), which), style)
                    if data:
                        return to_data_url(data)
                    data = nearest_replay(demo, style)
                    if data:
                        return to_data_url(data)
            return to_data_url(mock_label(jpeg))
        if not self.key:
            raise RequestError(500, "no_key", "伺服器找不到 API key")
        cost = self._reserve_call("makeup-vto")
        try:
            return self._render_live(jpeg, selection, data_holder := {})
        finally:
            self.budget.settle("makeup-vto", cost, data_holder.get("ok", False))

    def _reserve_call(self, feature: str) -> int:
        """Take one slot of the per-start call cap and reserve points; give the slot back if the points are refused."""
        with self.lock:
            if self.calls >= self.max_calls:
                raise RequestError(429, "max_calls", f"已達本次上限 {self.max_calls} 次，防止誤扣點")
            self.calls += 1
        try:
            return self.budget.reserve(feature)
        except bg.BudgetExceeded as exc:
            with self.lock:
                self.calls -= 1
            raise RequestError(429, "budget", f"已達點數總預算：{exc}") from None

    def _render_live(self, jpeg: bytes, selection: dict, holder: dict) -> str:
        body = looks.build_body(selection, self.presets)
        try:
            file_id = self.client.upload(jpeg, "selfie.jpg")
            task_id = self.client._json("POST", "/s2s/v2.0/task/makeup-vto", dict(body, src_file_id=file_id))["data"]["task_id"]
            deadline = time.monotonic() + nt.POLL_TIMEOUT
            while True:
                data = self.client._json("GET", f"/s2s/v2.0/task/makeup-vto/{task_id}").get("data", {})
                if data.get("task_status") in ("success", "error"):
                    break
                if time.monotonic() > deadline:
                    raise RequestError(504, "timeout", "試妝服務太久沒回應，請再試一次")
                time.sleep(nt.POLL_SECONDS)
        except nt.ApiError as exc:
            print(f"ERROR makeup-vto：{self.client._redact(str(exc))[:200]}", file=sys.stderr, flush=True)
            raise RequestError(502, "api_error", "試妝服務暫時連不上，稍後再試") from None
        if data.get("task_status") != "success":
            code = str(data.get("error") or data.get("error_code") or "task_error")
            print(f"ERROR makeup-vto task：{code}", file=sys.stderr, flush=True)
            raise RequestError(422, code, ERROR_HINTS.get(code, "這張沒辦法試妝，換個角度或光線再拍一次"))
        holder["ok"] = True  # once YouCam returns success the points are already charged, so even if the download fails afterwards it must be booked
        urls = find_urls(data.get("results"), [])
        if not urls:
            raise RequestError(502, "no_result", "試妝結果是空的，請再試一次")
        with urllib.request.urlopen(urls[0], timeout=60) as r:
            result = r.read(MAX_RESULT_BYTES + 1)
        if len(result) > MAX_RESULT_BYTES:
            raise RequestError(502, "no_result", "試妝結果是空的，請再試一次")
        print(f"makeup-vto ok, used {self.calls}/{self.max_calls}, budget left {self.budget.remaining()}", file=sys.stderr, flush=True)
        return to_data_url(result)

    # ---- endpoint logic
    def photo_from(self, payload: dict) -> tuple[bytes, str | None]:
        if payload.get("demo"):
            cond = demo_condition(payload)
            return prepare_vto_image(DEMOS[cond]["photo"].read_bytes()), cond
        return prepare_vto_image(decode_image(payload)), None

    def _estimate(self, feature: str) -> None:
        with self.lock:
            self.est_units += bg.PRICES[feature]
            self.est_calls[feature] = self.est_calls.get(feature, 0) + 1

    def _maybe_simulated_failure(self):
        if self.simulate_error:
            raise RequestError(422, self.simulate_error, ERROR_HINTS.get(self.simulate_error, "這張沒辦法試妝，換個角度或光線再拍一次"))

    def looks(self, payload: dict) -> dict:
        self._maybe_simulated_failure()
        style, tone = payload.get("style", "natural"), payload.get("undertone", "neutral")
        jpeg, demo = self.photo_from(payload)
        out = {}
        for which in ("glow", "clear"):
            try:
                sel = looks.quick_selection(style, which, tone, self.presets,
                                            payload.get("brow_hex"), payload.get("skin_hex"),
                                            payload.get("foundation_hex"), demo_condition(payload))
                looks.check_style_selection(style, sel, self.presets)
            except looks.PresetError as exc:
                raise RequestError(400, "bad_selection", str(exc)) from None
            out[which] = {"selection": sel, "image": self.render(jpeg, sel, demo, which, style)}
        return {"ok": True, "mode": self.status()["mode"], "looks": out}

    def makeup(self, payload: dict) -> dict:
        self._maybe_simulated_failure()
        style = payload.get("style", "natural")
        sel = dict(payload.get("selection") or {}, undertone=payload.get("undertone", "neutral"))
        for k in ("brow_hex", "skin_hex", "foundation_hex", "face_shape"):
            if payload.get(k):
                sel[k] = payload[k]
        try:
            looks.check_style_selection(style, sel, self.presets)
            looks.build_effects(sel, self.presets)
        except looks.PresetError as exc:
            raise RequestError(400, "bad_selection", str(exc)) from None
        jpeg, demo = self.photo_from(payload)
        return {"ok": True, "mode": self.status()["mode"], "selection": sel,
                "image": self.render(jpeg, sel, demo, None, style), "hint": looks.balance_hint(sel, self.presets)}

    # ---- layer view: foundation -> + blush -> + lip -> + brow -> + lash (1 Makeup VTO call per layer)
    def layers(self, payload: dict) -> dict:
        self._maybe_simulated_failure()
        style = payload.get("style", "natural")
        sel = dict(payload.get("selection") or {}, undertone=payload.get("undertone", "neutral"))
        for k in ("brow_hex", "skin_hex", "foundation_hex", "face_shape"):
            if payload.get(k):
                sel[k] = payload[k]
        try:
            looks.check_style_selection(style, sel, self.presets)
            steps = looks.layers(sel)
            for _, layer_sel in steps:
                looks.build_effects(layer_sel, self.presets)
        except looks.PresetError as exc:
            raise RequestError(400, "bad_selection", str(exc)) from None
        if not steps:
            return {"ok": True, "layers": []}
        jpeg, demo = self.photo_from(payload)
        out = []
        for n, (item, layer_sel) in enumerate(steps, 1):
            key = "final" if n == len(steps) else f"layer{n}_{item}"
            opt = (self.presets["blush"]["levels"] if item == "blush" else self.presets[item])
            label = next((o["label"] for o in opt if o["id"] == layer_sel[item]), layer_sel[item])
            out.append({"item": item, "item_label": looks.LAYER_LABEL[item], "option_label": label,
                        "image": self.render(jpeg, layer_sel, demo, None, style, key)})
        return {"ok": True, "mode": self.status()["mode"], "layers": out}

    # ---- suggestions from facial features (Face Attribute items 1-5 = 10 points, runs only when the person presses it)
    def face(self, payload: dict) -> dict:
        style = payload.get("style", "natural")
        jpeg, demo = self.photo_from(payload)
        if self.mock:
            self._estimate("face-attr-analysis(1-5)")
            f = DEMOS.get(demo or "", {}).get("face")
            if not (f and f.is_file()):
                return {"ok": True, "available": False, "hint": "展示模式：這張照片沒有錄好的五官結果。"}
            results = json.loads(f.read_text(encoding="utf-8"))["results"]
            return {"ok": True, "available": True, "mode": "mock", "advice": face_rules.advise(results, style)}
        if not self.key:
            raise RequestError(500, "no_key", "伺服器找不到 API key")
        cost = self._reserve_call("face-attr-analysis(1-5)")   # counts against the same per-start cap: a face check costs 10 points
        ok = False
        try:
            file_id = self.client.upload(jpeg, "selfie.jpg")
            task_id = self.client._json("POST", "/s2s/v2.0/task/face-attr-analysis",
                                        {"src_file_id": file_id, "features": face_rules.FEATURES,
                                         "face_angle_strictness_level": "medium"})["data"]["task_id"]
            deadline = time.monotonic() + nt.POLL_TIMEOUT
            while True:
                data = self.client._json("GET", f"/s2s/v2.0/task/face-attr-analysis/{task_id}").get("data", {})
                if data.get("task_status") in ("success", "error"):
                    break
                if time.monotonic() > deadline:
                    raise RequestError(504, "timeout", "五官分析太久沒回應，請再試一次")
                time.sleep(nt.POLL_SECONDS)
            ok = data.get("task_status") == "success"
        except nt.ApiError as exc:
            print(f"ERROR face-attr：{self.client._redact(str(exc))[:200]}", file=sys.stderr, flush=True)
            raise RequestError(502, "api_error", "五官分析暫時連不上，稍後再試") from None
        finally:
            self.budget.settle("face-attr-analysis(1-5)", cost, ok)
        if not ok:
            code = str(data.get("error") or "task_error")
            print(f"ERROR face-attr task：{code}", file=sys.stderr, flush=True)
            raise RequestError(422, code, ERROR_HINTS.get(code, "這張沒辦法分析五官，換個正面、光線均勻的照片再試"))
        return {"ok": True, "available": True, "mode": "live", "advice": face_rules.advise(data.get("results") or {}, style)}

    # ---- shade-card matching (local, no points)
    def match(self, payload: dict) -> dict:
        try:
            res = ce.match_hex(payload["hex"], payload.get("category", "foundation"), payload.get("undertone"),
                               self.src(payload), int(payload.get("top", 3)))
        except (KeyError, ValueError) as exc:
            raise RequestError(400, "bad_color", f"色碼或類別錯誤：{exc}") from None
        return {"ok": True, "matches": res}

    def skin_plan(self, payload: dict) -> dict:
        try:
            if payload.get("condition") == "vitiligo":
                plan = ce.vitiligo_plan(payload["normal_hex"], payload["patch_hex"], payload.get("undertone"),
                                        self.src(payload))
            else:
                plan = ce.albinism_base_plan(payload["skin_hex"], payload.get("undertone"), self.src(payload))
        except (KeyError, ValueError) as exc:
            raise RequestError(400, "bad_color", f"取色資料不完整：{exc}") from None
        return {"ok": True, "plan": plan}

    def card(self, payload: dict) -> dict:
        tone_id = payload.get("undertone", "neutral")
        sel = dict(payload.get("selection") or {})
        for k in ("brow_hex", "skin_hex", "foundation_hex", "face_shape"):
            if payload.get(k):
                sel[k] = payload[k]
        try:
            items = rec.shades_for(sel, tone_id, self.presets, self.src(payload))
        except looks.PresetError as exc:
            raise RequestError(400, "bad_selection", str(exc)) from None
        if payload.get("skin_hex") and payload.get("condition") != "vitiligo":   # the base / edge-blending color for vitiligo is given by /api/recommend
            # skin color -> skin-tone card cell -> counter shade in the same cell (not compared directly by color difference: photos and product swatches live in different color spaces)
            tone = None if payload.get("undertone_unsure") else {"pink_cool": "cool", "warm_ivory": "warm"}.get(tone_id, "neutral")
            try:
                cell, shades = ce.card_shades(payload["skin_hex"], "foundation", tone, self.src(payload))
            except ValueError as exc:
                raise RequestError(400, "bad_color", f"膚色色碼錯誤：{exc}") from None
            items.insert(0, {"item": "foundation", "category": "foundation", "hex": payload["skin_hex"],
                             "card": cell, "matches": shades})
        condition = "vitiligo" if payload.get("condition") == "vitiligo" else "albinism"
        if payload.get("skin_hex"):
            # face shades: albinism adds concealer; if highlight / contour are applied, give the matching shades (vitiligo concealer, edge blending and corrector come from /api/recommend)
            want = (["concealer"] if condition == "albinism" else []) + \
                   [k for k in ("highlight", "contour") if sel.get(k, "keep") != "keep"]
            zones = payload.get("zones") if isinstance(payload.get("zones"), dict) else None
            if zones and not all(isinstance((zones.get(k) or {}).get("hex"), str) and HEX_RE.match(zones[k]["hex"])
                                 for k in ("highlight", "contour")):
                zones = None
            tone = None if payload.get("undertone_unsure") else {"pink_cool": "cool", "warm_ivory": "warm"}.get(tone_id, "neutral")
            if want:
                try:
                    items.extend(ce.face_shades(payload["skin_hex"], zones, tone, self.src(payload), tuple(want)))
                except ValueError as exc:
                    raise RequestError(400, "bad_color", f"膚色色碼錯誤：{exc}") from None
        return {"ok": True, "items": items, "care": ce.care_plan(condition), "sensitive": ce.sensitive_notes(condition)}

    def recommend(self, payload: dict) -> dict:
        try:
            out = rec.recommend(payload.get("condition", "albinism"), payload.get("style", "natural"),
                                payload.get("skin_hex"), payload.get("normal_hex"), payload.get("patch_hex"),
                                payload.get("undertone"), self.src(payload), self.presets,
                                payload.get("brow_hex"), _ratio(payload.get("patch_ratio")),
                                _sensitivity(payload.get("sensitivity")))
        except (ValueError, KeyError, looks.PresetError) as exc:
            raise RequestError(400, "bad_input", f"取色或妝感資料不完整：{exc}") from None
        return {"ok": True, "recommendation": out}

    def catalog_info(self) -> dict:
        demo = json.loads(ce.DEMO_CATALOG.read_text(encoding="utf-8"))
        partner = json.loads(ce.PARTNER_CATALOG.read_text(encoding="utf-8")) if ce.PARTNER_CATALOG.exists() else {}
        official = json.loads(ce.OFFICIAL_CATALOG.read_text(encoding="utf-8")) if ce.OFFICIAL_CATALOG.exists() else {}
        count = lambda d: sum(len(p["shades"]) for p in d.get("products", []))
        return {"ok": True, "demo_note": demo.get("note"), "partner_note": partner.get("note"),
                "official_note": official.get("note"), "partner_source": partner.get("source"),
                "counts": {"demo": count(demo), "official": count(official), "partner_2018": count(partner)}}

    # ---- light-patch mask (local, no points): the page sends the photo (or the simulated made-up image) + the makeup region, and multi-scale detection is computed here in Python
    #
    def patch_mask(self, payload: dict) -> dict:
        import time
        from PIL import Image, ImageOps
        import numpy as np
        import patch_detect
        t0 = time.time()
        try:
            with open_checked(decode_image(payload)) as src:
                img = ImageOps.exif_transpose(src).convert("RGB")
            with open_checked(decode_image({"image": payload.get("region", "")})) as r:
                region_img = r.convert("L")
        except RequestError:
            raise
        except Exception:  # noqa: BLE001
            raise RequestError(400, "bad_image", "照片或範圍讀不到") from None
        if max(img.size) > 3200:                                       # too large: shrink first (detection scales are adjusted proportionally)
            s = 3200 / max(img.size)
            img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
        out_size = region_img.size
        region = np.asarray(region_img.resize(img.size, Image.BILINEAR)) > 127
        png, ratio = patch_detect.mask_png(np.asarray(img), region, out_size)
        return {"ok": True, "mask": to_data_url(png, "image/png"), "ratio": round(ratio, 4),
                "ms": round((time.time() - t0) * 1000)}

    def demo_photo(self, condition: str = "albinism") -> dict:
        d = DEMOS["vitiligo" if condition == "vitiligo" else "albinism"]
        out = {"ok": True, "image": to_data_url(d["photo"].read_bytes()), "credit": d["credit"], "points": d["points"]}
        if d.get("expected") and d["expected"].is_file():
            meta = json.loads(d["expected"].read_text(encoding="utf-8"))
            out["expected"] = {"image": to_data_url((ROOT / meta["image"]).read_bytes()), "zones": meta["zones"],
                               "how": meta["how"]}
            if meta.get("layer_fit"):           # fitted shade + opacity for the makeup overlay (10-17 B; without internal notes)
                out["expected"]["layer_fit"] = {k: meta["layer_fit"][k] for k in ("shades", "opacity")}
        return out

    def final_look(self, condition: str, who: str | None = None) -> dict:
        """Finalized look (simulated): reads only demo/final/, no YouCam call, no points."""
        if condition not in FINAL_DEFAULT_WHO:
            raise RequestError(400, "bad_condition", "入口只有膚色很淺、膚色深淺不一兩種")
        who = who or FINAL_DEFAULT_WHO[condition]
        key = FINAL_KEYS.get((condition, who))
        if not key:
            raise RequestError(400, "bad_who", "這個入口目前沒有這個選項的定案妝容")
        recipes = FINAL_DIR / "recipes.json"
        if not recipes.is_file():
            raise RequestError(404, "no_final", "定案妝容暫時看不到")
        r = json.loads(recipes.read_text(encoding="utf-8"))[key]
        who_options = sorted(w for (c, w) in FINAL_KEYS if c == condition)
        img = {k: (to_data_url((FINAL_DIR / v).read_bytes()) if v else None) for k, v in r["images"].items()}
        # flow: local layered foundation -> this official makeup-vto step -> recipe card. Demo mode only shows recorded official results, with no YouCam call and no points;
        # without a recording only the local result is shown, with a note (, owner: proactively slot in the YouCam service; the backend for real calls is handled separately by shared/auto-finish)
        official = ({"image": img["official"], "status": "replay", "note": "官方這一步：YouCam 試妝錄好的結果（展示模式重播，不扣點）"}
                    if img["official"] else
                    {"image": None, "status": "not_recorded", "note": "官方這一步尚未錄製；展示模式不呼叫 YouCam，以下顯示本地結果"})
        return {"ok": True, "condition": condition, "who": who, "who_options": who_options, "title": r["title"],
                "label": r["label"], "credit": r["credit"], "steps": r["steps"],
                "base": {"image": img["base"], "note": "本地逐層底妝"}, "official": official,
                "image": img["final"]}


class Handler(BaseHTTPRequestHandler):
    state: State
    live_state: State | None = None            # state for real calls, used only after judge login (--judge-live)
    auth: "judge_auth.JudgeAuth | None" = None
    timeout = SOCKET_TIMEOUT

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower() == "https"

    def _logged_in(self) -> bool:
        return bool(self.auth) and self.auth.valid_cookie_header(self.headers.get("Cookie"))

    def _pick_state(self) -> State:
        """Not logged in is always demo mode; only requests carrying a valid judge cookie take real calls."""
        return self.live_state if (self.live_state is not None and self._logged_in()) else type(self).state

    def _read_body(self, limit: int) -> bytes | None:
        """Read the request body under a total deadline; answer the error and return None when it is bad."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json(400, {"ok": False, "error_code": "bad_length", "hint": "請求格式錯誤"})
            return None
        if length <= 0 or length > limit:
            self._json(413, {"ok": False, "error_code": "body_size", "hint": "照片太大"})
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._json(415, {"ok": False, "error_code": "bad_type", "hint": "請求格式錯誤"})
            return None
        deadline = time.monotonic() + BODY_DEADLINE
        chunks, got = [], 0
        try:
            while got < length:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                self.connection.settimeout(min(left, SOCKET_TIMEOUT))   # each wait is bounded by what is left of the total deadline
                chunk = self.rfile.read1(min(64 * 1024, length - got))
                if not chunk:
                    self.close_connection = True
                    self._json(400, {"ok": False, "error_code": "bad_length", "hint": "請求格式錯誤"})
                    return None
                chunks.append(chunk)
                got += len(chunk)
        except TimeoutError:
            self.close_connection = True
            self.connection.settimeout(SOCKET_TIMEOUT)
            self._json(408, {"ok": False, "error_code": "slow_body", "hint": "上傳太慢，請再試一次"})
            return None
        except OSError:                           # connection reset
            self.close_connection = True
            return None
        self.connection.settimeout(SOCKET_TIMEOUT)
        return b"".join(chunks)

    def _source_key(self) -> str:
        """Lockout key: the last X-Forwarded-For entry (added by the trusted proxy), else the socket peer."""
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        return (fwd or self.client_address[0])[:64]

    def _judge_post(self, path: str):
        """/api/judge-login, /api/judge-logout: bypass the photo routes and take no compute slot."""
        secure = self._secure()
        if path == "/api/judge-logout":
            self._set_cookies = [judge_auth.clear_cookie_value(secure)]
            return self._json(200, {"ok": True})
        raw = self._read_body(4096)
        if raw is None:
            return
        key = self._source_key()
        if self.auth.locked(key=key):
            return self._json(429, {"ok": False, "error_code": "locked", "hint": "too many attempts"})
        try:
            body = json.loads(raw)
            user, password = str(body.get("username", "")), str(body.get("password", ""))
        except (ValueError, AttributeError):    # JSONDecodeError and bad UTF-8 are both ValueError
            return self._json(400, {"ok": False, "error_code": "bad_json", "hint": "bad request"})
        if not self.auth.check(user, password, key=key):
            return self._json(401, {"ok": False, "error_code": "bad_login", "hint": "wrong username or password"})
        self._set_cookies = [judge_auth.set_cookie_value(self.auth.issue(), secure)]
        return self._json(200, {"ok": True})

    def _security_headers(self):
        self._sec_sent = True
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def end_headers(self):
        """Every response carries the security headers, including the 404 and 4xx pages from send_error."""
        if not getattr(self, "_sec_sent", False):
            self._security_headers()
        self._sec_sent = False
        super().end_headers()

    def log_message(self, fmt, *args):  # log only method and path
        sys.stderr.write(f"{self.log_date_time_string()} {self.command} {self.path.split('?')[0]}\n")

    def _lang(self) -> str:
        """Request language: the X-App-Lang header (sent by the frontend api()) or ?lang=; only 'en' switches to English, everything else stays Chinese."""
        q = urllib.parse.parse_qs(self.path.partition("?")[2]).get("lang", [""])[0]
        return "en" if (self.headers.get("X-App-Lang") or q).strip().lower() == "en" else "zh"

    def _json(self, code: int, obj: dict):
        if self.state.alias is not None:                      # surface alias: first replace brand / product names, then do the language conversion
            obj = self.state.alias.scrub(obj, self._lang())
        if self._lang() == "en":
            obj = i18n_srv.localize(obj)
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for cookie in getattr(self, "_set_cookies", []):
            self.send_header("Set-Cookie", cookie)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _REQ.lang = self._lang()
        self.state = self._pick_state()
        path = self.path.split("?")[0]
        if path == "/judge" and self.auth:
            page = judge_auth.login_page(self._logged_in())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(page)
            return
        if path == "/api/status":
            status = self.state.status()
            if self.auth:
                status["judge_login"] = True
                status["judge_signed_in"] = self._logged_in()
            return self._json(200, status)
        if path == "/api/presets":
            return self._json(200, public_view(self.state.presets))
        if path == "/api/demo-photo":
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            return self._json(200, self.state.demo_photo((query.get("condition") or ["albinism"])[0]))
        if path == "/api/catalog":
            return self._json(200, public_view(self.state.catalog_info()))
        if path == "/api/final-look":
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            try:
                return self._json(200, self.state.final_look((query.get("condition") or ["albinism"])[0],
                                                             (query.get("who") or [None])[0]))
            except RequestError as exc:
                return self._json(exc.status, {"ok": False, "error_code": exc.code, "hint": exc.hint})
        if path == "/api/skin-card":
            return self._json(200, {"ok": True, "cells": sc.card(), "levels": sc.LEVEL_NAMES})
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        with target.open("rb") as fh:                      # send in chunks, so a large file (the 14 MB wasm) is not read into memory whole
            shutil.copyfileobj(fh, self.wfile, 64 * 1024)

    def do_POST(self):
        _REQ.lang = self._lang()
        self.state = self._pick_state()
        path = self.path.split("?")[0]
        if self.auth and path in ("/api/judge-login", "/api/judge-logout"):
            return self._judge_post(path)
        routes = {"/api/looks": self.state.looks, "/api/makeup": self.state.makeup, "/api/match": self.state.match,
                  "/api/skin-plan": self.state.skin_plan, "/api/card": self.state.card,
                  "/api/recommend": self.state.recommend, "/api/face": self.state.face,
                  "/api/layers": self.state.layers, "/api/patch-mask": self.state.patch_mask}
        if path not in routes:
            self.send_error(404)
            return
        raw = self._read_body(MAX_BODY)               # read the whole request before queueing, so a slow upload does not hold a compute slot
        if raw is None:
            return
        heavy = path in HEAVY_ROUTES
        if heavy and not self.state.heavy.acquire(timeout=HEAVY_WAIT_SECONDS):
            return self._json(503, {"ok": False, "error_code": "busy", "hint": "現在使用的人比較多，請幾秒後再試一次"})
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return self._json(400, {"ok": False, "error_code": "bad_json", "hint": "請求格式錯誤"})
            return self._json(200, routes[path](payload))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:   # malformed JSON or payload fields: a 400, never a dropped connection
            print(f"ERROR bad request on {path}: {type(exc).__name__}", file=sys.stderr, flush=True)
            return self._json(400, {"ok": False, "error_code": "bad_json", "hint": "請求格式錯誤"})
        except RequestError as exc:
            return self._json(exc.status, {"ok": False, "error_code": exc.code, "hint": exc.hint})
        finally:
            if heavy:
                self.state.heavy.release()


class BoundedHTTPServer(ThreadingHTTPServer):
    """The number of connections is capped (MAX_CONNECTIONS): when full, new connections are closed immediately instead of spawning unlimited threads."""
    daemon_threads = True

    def __init__(self, *args, max_connections: int = MAX_CONNECTIONS, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(host: str, port: int, state: State, live_state: State | None = None,
                auth: "judge_auth.JudgeAuth | None" = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state, "live_state": live_state, "auth": auth})
    return BoundedHTTPServer((host, port), handler)


def check_api_base(url: str) -> bool:
    """The hidden --api-base allows only YouCam https hosts or a local test server, so the key can never be redirected elsewhere."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and (host == "makeupar.com" or host.endswith(".makeupar.com")):
        return True
    return u.scheme in ("http", "https") and host in ("127.0.0.1", "localhost")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--mock", action="store_true", help="do not call YouCam, spend no credits")
    ap.add_argument("--max-calls", type=int, default=20, help="maximum real YouCam calls per server start")
    ap.add_argument("--budget-cap", type=int, default=60, help="total credit budget across starts (ledger: runs/budget_ledger.json)")
    ap.add_argument("--judge-live", action="store_true",
                    help="public deployment: demo mode for everyone by default (no sign-in); only a judge signed in with GOODGLOW_JUDGE_USER / GOODGLOW_JUDGE_PASSWORD gets real YouCam calls (needs YOUCAM_API_KEY)")
    ap.add_argument("--catalog", choices=("all", "demo"), default="all",
                    help="demo = show only the fictional demo catalog (for recordings and screenshots; no brand names on screen)")
    ap.add_argument("--brand-alias", action="store_true",
                    help="replace real brand names on screen with letter aliases and product names with categories; matching still uses real shade data (for recordings and screenshots)")
    ap.add_argument("--simulate-youcam-error", nargs="?", const="error_face_angle_invalid", default=None, metavar="CODE",
                    help="(only with --mock) make try-on requests return a YouCam error code, to reproduce the local simulation taking over when YouCam cannot process a photo; default error_face_angle_invalid")
    ap.add_argument("--max-heavy", type=int, default=int(os.environ.get("GOODGLOW_MAX_HEAVY", DEFAULT_MAX_HEAVY)),
                    help="maximum concurrent heavy requests (mask, try-on, layers, face), default 2; environment variable GOODGLOW_MAX_HEAVY")
    ap.add_argument("--api-base", default=nt.API_BASE, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not check_api_base(args.api_base):
        print("ERROR --api-base must be a YouCam https host (*.makeupar.com) or a local test server", file=sys.stderr)
        return 2
    if args.judge_live and args.mock:
        print("ERROR --judge-live cannot be combined with --mock", file=sys.stderr)
        return 2
    live_state = auth = None
    try:
        # --judge-live: the externally visible state is always demo mode; the state for real calls is given only to the logged-in judge
        state = State(args.mock or args.judge_live, args.max_calls, args.api_base, args.budget_cap, catalog=args.catalog,
                      alias=args.brand_alias, simulate_error=args.simulate_youcam_error, max_heavy=args.max_heavy)
        if args.judge_live:
            auth = judge_auth.JudgeAuth.from_env()
            if auth is None:
                print(f"ERROR --judge-live needs the environment variables {judge_auth.USER_ENV} and {judge_auth.PASSWORD_ENV}", file=sys.stderr)
                return 2
            live_state = State(False, args.max_calls, args.api_base, args.budget_cap, catalog=args.catalog,
                               alias=args.brand_alias, max_heavy=args.max_heavy)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR invalid startup settings: {exc}", file=sys.stderr)
        return 2
    if not args.mock and not (live_state or state).key:
        print(f"ERROR {nt.KEY_ENV} not found: set the environment variable or write it to {ROOT / '.env'}; add --mock to only look at the interface", file=sys.stderr)
        return 2
    httpd = make_server(args.host, args.port, state, live_state, auth)
    if args.judge_live:
        mode = f"demo for everyone + live after judge sign-in (max {args.max_calls} calls per start; budget {live_state.budget.status()})"
    else:
        mode = "mock (no credits)" if args.mock else f"live (max {args.max_calls} calls per start; budget {state.budget.status()})"
    print(f"http://{args.host}:{args.port}  mode: {mode}  presets {state.presets['version']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
