#!/usr/bin/env python3
"""本機伺服器：提供 PWA，並代理 YouCam Makeup VTO（白化症好氣色試妝）。

- API key 只在伺服器端讀（環境變數或專案 .env），不送進瀏覽器、不印出。
- 照片只在記憶體內處理，不落檔；結果圖轉成 data URL 回給瀏覽器，伺服器不留。
- 前端只送「妝感＋各項選項 id」，參數由 app/looks.py 依 presets.json 組，並檢查妝感允不允許。
- --mock：不連 YouCam、不扣點。用示範照片時重播錄好的真實結果；用自己的照片時回原圖並標示「展示模式」。

用法：
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

WEB_DIR = ROOT / "web"
MAX_BODY = 16 * 1024 * 1024
MAX_PIXELS = 40_000_000                   # 檔頭宣告的畫面超過這個像素數就在解碼前拒收（小檔案大畫面＝解壓炸彈）
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
SOCKET_TIMEOUT = 20                       # 單一連線讀寫逾時（秒）：慢速或停住的連線不能一直占著執行緒
MAX_CONNECTIONS = 64                      # 同時處理的連線上限；滿了就直接關掉新連線
# 公開部署用：吃 CPU 或會呼叫 YouCam 的請求同時最多幾個（淺色區塊遮罩、試妝、逐層、五官）；排不上隊就回 503，不拖垮整台。
HEAVY_ROUTES = {"/api/patch-mask", "/api/makeup", "/api/looks", "/api/layers", "/api/face"}
HEAVY_WAIT_SECONDS = 8
DEFAULT_MAX_HEAVY = 2
MIN_SHORT = 1080          # 拒收低解析度（實測放大會失真）
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")    # 預期妝容分區色只收 #RRGGBB
VTO_MAX_LONG = 1919       # Makeup VTO 要求長邊 < 1920
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".jpg": "image/jpeg",
    ".mjs": "text/javascript; charset=utf-8", ".wasm": "application/wasm",   # web/vendor：ONNX Runtime Web（10-16）
}
ERROR_HINTS = {
    "error_face_position_invalid": "臉要完整出現在畫面裡",
    "error_face_position_too_small": "臉太小：手機拿近一點",
    "error_face_position_out_of_boundary": "臉超出畫面：往中間移一點",
    "error_face_angle_invalid": "臉太斜：手機舉到眼睛高度、正對鏡頭",
    "error_below_min_image_size": "照片解析度不夠：改用前鏡頭最高畫質",
}

# 示範照片（Pexels，可修改授權）與錄好的真實結果：都在 demo/（進版控，來源見 demo/SOURCES.md）
DEMO_DIR = ROOT / "demo"
# 改用四人標準組的明亮照（舊的 19276432 偏暗，取到色卡 5 級；舊照與重播圖封存在 runs/）
DEMO_PHOTO = DEMO_DIR / "photos" / "standard4" / "albinism_F_5273693.jpg"
# points：取色點（只用於示範照）。白化症：臉部點位 landmark 50（受光側臉頰）、左眉中段；白斑症：10% 格線圖人工確認
DEMOS = {
    "albinism": {"photo": DEMO_PHOTO, "credit": "Pexels（可修改授權）https://www.pexels.com/photo/5273693/",
                 "replay": DEMO_DIR / "replay" / "albinism",
                 "points": {"normal": [0.302, 0.527], "brow": [0.325, 0.373]}, "face": None, "expected": None},
    "vitiligo": {"photo": DEMO_DIR / "photos" / "vitiligo_20551205.jpg",
                 "credit": "Pexels（可修改授權）https://www.pexels.com/photo/20551205/",
                 "replay": DEMO_DIR / "replay" / "vitiligo",
                 "points": {"normal": [0.72, 0.58], "patch": [0.33, 0.52], "brow": [0.62, 0.41]},
                 "face": DEMO_DIR / "face" / "vitiligo_20551205.json",
                 # 預期妝容（模擬）：AI 生成圖只取顏色明暗套回原圖；色號改從這張的三區取色
                 "expected": DEMO_DIR / "expected" / "vitiligo_20551205.json"},
}

# 各入口的定案妝容（模擬）：tools/export_final_looks.py 從 runs/ 匯出到 demo/final/（授權接進兩個入口）
FINAL_DIR = DEMO_DIR / "final"
FINAL_KEYS = {("albinism", "F"): "albinism_F", ("albinism", "M"): "albinism_M", ("vitiligo", "M"): "vitiligo_M", ("vitiligo", "F"): "vitiligo_F"}
FINAL_DEFAULT_WHO = {"albinism": "F", "vitiligo": "M"}


def _sensitivity(v) -> dict | None:
    """敏感三題的答案：只收已知的鍵與值，其他忽略。"""
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
    """精確重播的代號：入口＋妝感＋實際送給 YouCam 的試妝參數（looks.build_effects）。
    選項、顏色（底妝色號、眉色、膚色）、presets 任一改變都會換代號，舊錄影不會被誤用（ 眉色卡改版後改成這樣）。"""
    try:
        effects = looks.build_effects(selection)
    except looks.PresetError:
        effects = {k: selection.get(k, "keep") for k in EXACT_ITEMS}
    norm = {"c": condition, "s": style, "e": effects}
    return hashlib.sha1(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def exact_replay(condition: str, style: str, selection: dict) -> bytes | None:
    """展示模式：tools/record_demo_replays.py 實跑錄好的「完全同一組選項」結果；沒有就回 None。"""
    f = DEMOS[condition]["replay"] / "exact" / f"{exact_key(condition, style, selection)}.jpg"
    return f.read_bytes() if f.is_file() else None


def replay_image(condition: str, key: str, style: str = "natural") -> bytes | None:
    """展示模式：依妝感與款式找錄好的結果（例如 grooming_clear.jpg），找不到就退回同款式的其他妝感。"""
    folder = DEMOS[condition]["replay"]
    if key.startswith("foundation_only_"):
        # 只上底妝：用逐層看第一層的錄影結果（同妝感優先，否則任一妝感的第一層）
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
    """先看檔頭的格式與畫面大小再開圖（PIL 是延遲解碼）：只收 JPEG／PNG／WebP，像素上限 MAX_PIXELS。"""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(raw))
        fmt, (w, h) = im.format, im.size
    except Exception:  # noqa: BLE001  含 DecompressionBombError、格式錯誤
        raise RequestError(400, "bad_photo", "讀不到這張照片") from None
    if fmt not in ALLOWED_FORMATS:
        raise RequestError(400, "bad_photo", "照片格式只收 JPEG、PNG、WebP")
    if w * h > MAX_PIXELS:
        raise RequestError(413, "image_too_large", "照片畫面太大，請縮小後再試")
    return im


def prepare_vto_image(raw: bytes) -> bytes:
    """轉正方向、拒收短邊 < 1080、縮到長邊 < 1920、重新編碼（去 EXIF）。"""
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
    """回給前端的資料不含內部備註欄位（作法、來源討論、日期等只留在檔案備註，不出貨到畫面或網路回應）。"""
    if isinstance(obj, dict):
        return {k: public_view(v) for k, v in obj.items() if k not in INTERNAL_KEYS}
    if isinstance(obj, list):
        return [public_view(x) for x in obj]
    return obj


_REQ = threading.local()      # 目前這個請求的語言（do_GET／do_POST 一進來就設定），mock_label 用


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
    """自己的照片在展示模式：回原圖，底部加一條細標示（依語言），避免被當成真的試妝結果。
    不再把選項清單畫到圖上（選項已顯示在圖下方的說明文字； 手機實測：舊版黑條是英文、被截斷）。"""
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
    """示範照在展示模式找不到「完全同一組選項」的錄影結果時，退回最接近的一張錄好的官方結果（不燒字；
    畫面下方的說明文字與 MOCK_SAME 會註明展示模式只錄了幾種組合）。"""
    folder = DEMOS[condition]["replay"]
    cands = [folder / f"{style}_glow.jpg", folder / f"{style}_clear.jpg", folder / "natural_glow.jpg"] + sorted(folder.glob("*.jpg"))
    f = next((c for c in cands if c.is_file()), None)
    if f:
        return f.read_bytes()
    # 沒有具名的錄影檔：用這個入口的定案妝容圖（同一張示範照做的完整妝容，demo/final），再不行才用任一張精確錄影
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
        only = all(selection.get(k, "keep") == "keep" for k in ("blush", "lip", "brow", "lash"))   # 打亮不影響重播選圖
        # 只有底妝＝簡化流程的「全臉底妝」；有錄才重播，沒有就標示展示模式
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
        # simulate_error（--simulate-youcam-error）：只在 --mock 用，讓試妝類請求回 YouCam 的錯誤碼，重現「YouCam 處理不了時改由本機模擬接手」
        if simulate_error and not mock:
            raise ValueError("--simulate-youcam-error 只能和 --mock 一起用")
        self.simulate_error = simulate_error
        self.est_units = 0           # 展示模式下「如果是正式模式會花幾點」的估算（官方價目 app/budget.py PRICES）；不扣任何點、不呼叫 YouCam
        self.est_calls = {}
        # alias=True（--brand-alias）：錄影、截圖、公開頁面用。內部仍用真實品牌資料比對，回傳給畫面的品牌名換成字母代稱、產品名換成品類
        self.alias = brand_alias.load() if alias else None
        # catalog="demo"：錄影片、截圖用，只顯示虛構示範型錄，畫面不出現第三方品牌名稱（比賽規則：影片不得含第三方商標）
        self.catalog = "demo" if catalog == "demo" else "all"
        # 首頁數字與色號頁說明要和實際載入的型錄一致：沒有官網現行資料（乾淨 clone）或指定 demo 時，只有虛構示範型錄
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
        """型錄來源：示範型錄模式一律用虛構示範型錄，否則照請求（預設 all＝官網現行色號優先）。"""
        return "demo" if self.catalog == "demo" else payload.get("source", "all")

    # ---- 一次 Makeup VTO
    def render(self, jpeg: bytes, selection: dict, demo: str | None, which: str | None = None,
               style: str = "natural", layer: str | None = None) -> str:
        """demo：示範照的入口（albinism／vitiligo），自己的照片為 None。layer：逐層看的第幾層（展示模式重播用）。"""
        if self.mock:
            self._estimate("makeup-vto")
            if demo:
                if layer:
                    f = DEMOS[demo]["replay"] / f"{style}_{layer}.jpg"
                    data = f.read_bytes() if f.is_file() else None
                    if data:
                        return to_data_url(data)
                    if layer.startswith("final"):
                        layer = None                     # 最後一層＝整套妝，退回整套的錄影結果
                if not layer:
                    data = exact_replay(demo, style, selection) or replay_image(demo, replay_key(selection, which), style)
                    if not data and selection.get("foundation", "keep") != "keep":
                        # 還沒錄含底妝的版本：先用同款不含底妝的錄影結果（畫面已註明是相近結果）
                        data = replay_image(demo, replay_key(dict(selection, foundation="keep"), which), style)
                    if data:
                        return to_data_url(data)
                    data = nearest_replay(demo, style)
                    if data:
                        return to_data_url(data)
            return to_data_url(mock_label(jpeg))
        if not self.key:
            raise RequestError(500, "no_key", "伺服器找不到 API key")
        with self.lock:
            if self.calls >= self.max_calls:
                raise RequestError(429, "max_calls", f"已達本次上限 {self.max_calls} 次，防止誤扣點")
            self.calls += 1
        try:
            cost = self.budget.reserve("makeup-vto")
        except bg.BudgetExceeded as exc:
            raise RequestError(429, "budget", f"已達點數總預算：{exc}") from None
        try:
            return self._render_live(jpeg, selection, data_holder := {})
        finally:
            self.budget.settle("makeup-vto", cost, data_holder.get("ok", False))

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
        holder["ok"] = True  # YouCam 回 success 就已扣點，之後下載失敗也要記帳
        urls = find_urls(data.get("results"), [])
        if not urls:
            raise RequestError(502, "no_result", "試妝結果是空的，請再試一次")
        with urllib.request.urlopen(urls[0], timeout=60) as r:
            result = r.read()
        print(f"makeup-vto ok，已用 {self.calls}/{self.max_calls}，預算剩 {self.budget.remaining()}", file=sys.stderr, flush=True)
        return to_data_url(result)

    # ---- 端點邏輯
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

    # ---- 逐層看：底妝 → ＋腮紅 → ＋唇 → ＋眉 → ＋睫毛（每層 1 次 Makeup VTO）
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

    # ---- 依五官給建議（Face Attribute 1–5 項＝10 點，本人按了才跑）
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
        try:
            cost = self.budget.reserve("face-attr-analysis(1-5)")
        except bg.BudgetExceeded as exc:
            raise RequestError(429, "budget", f"已達點數總預算：{exc}") from None
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

    # ---- 色卡比對（本機，不扣點）
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
        if payload.get("skin_hex") and payload.get("condition") != "vitiligo":   # 白斑的打底／修邊色由 /api/recommend 給
            # 膚色 → 膚色色卡格子 → 同一格的專櫃色號（不直接比色差：照片和產品色票的顏色空間不同）
            tone = None if payload.get("undertone_unsure") else {"pink_cool": "cool", "warm_ivory": "warm"}.get(tone_id, "neutral")
            try:
                cell, shades = ce.card_shades(payload["skin_hex"], "foundation", tone, self.src(payload))
            except ValueError as exc:
                raise RequestError(400, "bad_color", f"膚色色碼錯誤：{exc}") from None
            items.insert(0, {"item": "foundation", "category": "foundation", "hex": payload["skin_hex"],
                             "card": cell, "matches": shades})
        condition = "vitiligo" if payload.get("condition") == "vitiligo" else "albinism"
        if payload.get("skin_hex"):
            # 臉部色號：白化症加遮瑕；有上打亮／修容就給對應色號（白斑的遮瑕、修邊、校色由 /api/recommend 給）
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

    # ---- 淺色區塊遮罩（本機，不扣點）：網頁送照片（或上完妝的模擬圖）＋上妝範圍，這裡用 Python 算多尺度偵測
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
        if max(img.size) > 3200:                                       # 太大先縮（偵測照比例調整尺度）
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
            if meta.get("layer_fit"):           # 化妝塗層用的擬合色號＋不透明度（ B；不含內部說明）
                out["expected"]["layer_fit"] = {k: meta["layer_fit"][k] for k in ("shades", "opacity")}
        return out

    def final_look(self, condition: str, who: str | None = None) -> dict:
        """定案妝容（模擬）：只讀 demo/final/，不呼叫 YouCam、不扣點。"""
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
        # 流程：本地逐層底妝 → 官方 makeup-vto 這一步 → 配方卡。展示模式只放錄好的官方結果，不呼叫 YouCam、不扣點；
        # 沒錄好的就只顯示本地結果並註明（；真實呼叫後端另由 shared/auto-finish 負責）
        official = ({"image": img["official"], "status": "replay", "note": "官方這一步：YouCam 試妝錄好的結果（展示模式重播，不扣點）"}
                    if img["official"] else
                    {"image": None, "status": "not_recorded", "note": "官方這一步尚未錄製；展示模式不呼叫 YouCam，以下顯示本地結果"})
        return {"ok": True, "condition": condition, "who": who, "who_options": who_options, "title": r["title"],
                "label": r["label"], "credit": r["credit"], "steps": r["steps"],
                "base": {"image": img["base"], "note": "本地逐層底妝"}, "official": official,
                "image": img["final"]}


class Handler(BaseHTTPRequestHandler):
    state: State
    timeout = SOCKET_TIMEOUT

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def log_message(self, fmt, *args):  # 只記方法與路徑
        sys.stderr.write(f"{self.log_date_time_string()} {self.command} {self.path.split('?')[0]}\n")

    def _lang(self) -> str:
        """請求語言：X-App-Lang 標頭（前端 api() 會帶）或 ?lang=；只有 'en' 才換成英文，其餘一律維持中文。"""
        q = urllib.parse.parse_qs(self.path.partition("?")[2]).get("lang", [""])[0]
        return "en" if (self.headers.get("X-App-Lang") or q).strip().lower() == "en" else "zh"

    def _json(self, code: int, obj: dict):
        if self.state.alias is not None:                      # 檯面代稱：先換品牌／產品名，再做語言轉換
            obj = self.state.alias.scrub(obj, self._lang())
        if self._lang() == "en":
            obj = i18n_srv.localize(obj)
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _REQ.lang = self._lang()
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self._json(200, self.state.status())
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
        with target.open("rb") as fh:                      # 分段送出，大檔（14 MB 的 wasm）不整個讀進記憶體
            shutil.copyfileobj(fh, self.wfile, 64 * 1024)

    def do_POST(self):
        _REQ.lang = self._lang()
        path = self.path.split("?")[0]
        routes = {"/api/looks": self.state.looks, "/api/makeup": self.state.makeup, "/api/match": self.state.match,
                  "/api/skin-plan": self.state.skin_plan, "/api/card": self.state.card,
                  "/api/recommend": self.state.recommend, "/api/face": self.state.face,
                  "/api/layers": self.state.layers, "/api/patch-mask": self.state.patch_mask}
        if path not in routes:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json(400, {"ok": False, "error_code": "bad_length", "hint": "請求格式錯誤"})
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"ok": False, "error_code": "body_size", "hint": "照片太大"})
        raw = self.rfile.read(length)                 # 先把請求讀完再排隊，慢速上傳不佔用運算名額
        heavy = path in HEAVY_ROUTES
        if heavy and not self.state.heavy.acquire(timeout=HEAVY_WAIT_SECONDS):
            return self._json(503, {"ok": False, "error_code": "busy", "hint": "現在使用的人比較多，請幾秒後再試一次"})
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return self._json(400, {"ok": False, "error_code": "bad_json", "hint": "請求格式錯誤"})
            return self._json(200, routes[path](payload))
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error_code": "bad_json", "hint": "請求格式錯誤"})
        except RequestError as exc:
            return self._json(exc.status, {"ok": False, "error_code": exc.code, "hint": exc.hint})
        finally:
            if heavy:
                self.state.heavy.release()


class BoundedHTTPServer(ThreadingHTTPServer):
    """連線數有上限（MAX_CONNECTIONS）：滿了就直接關掉新連線，不再無限開執行緒。"""
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


def make_server(host: str, port: int, state: State) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    return BoundedHTTPServer((host, port), handler)


def check_api_base(url: str) -> bool:
    """隱藏的 --api-base 只允許 YouCam 的 https 主機或本機測試伺服器，金鑰不會被導去別處。"""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and (host == "makeupar.com" or host.endswith(".makeupar.com")):
        return True
    return u.scheme in ("http", "https") and host in ("127.0.0.1", "localhost")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--mock", action="store_true", help="不連 YouCam、不扣點")
    ap.add_argument("--max-calls", type=int, default=20, help="本次啟動最多呼叫幾次真 API")
    ap.add_argument("--budget-cap", type=int, default=60, help="跨次啟動的點數總預算（帳本 runs/budget_ledger.json）")
    ap.add_argument("--catalog", choices=("all", "demo"), default="all",
                    help="demo＝只顯示虛構示範型錄（錄影片、截圖用，畫面不出現品牌名稱）")
    ap.add_argument("--brand-alias", action="store_true",
                    help="畫面上的真實品牌名換成字母代稱（M 牌）、產品名換成品類；內部仍用真實色號比對（錄影、截圖用）")
    ap.add_argument("--simulate-youcam-error", nargs="?", const="error_face_angle_invalid", default=None, metavar="CODE",
                    help="（只能和 --mock 用）試妝類請求回 YouCam 的錯誤碼，重現「YouCam 處理不了時改由本機模擬接手」；預設 error_face_angle_invalid")
    ap.add_argument("--max-heavy", type=int, default=int(os.environ.get("GOODGLOW_MAX_HEAVY", DEFAULT_MAX_HEAVY)),
                    help="同時處理的重運算請求上限（遮罩、試妝、逐層、五官），預設 2；環境變數 GOODGLOW_MAX_HEAVY")
    ap.add_argument("--api-base", default=nt.API_BASE, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not check_api_base(args.api_base):
        print("ERROR --api-base 只能是 YouCam 的 https 主機（*.makeupar.com）或本機測試伺服器", file=sys.stderr)
        return 2
    try:
        state = State(args.mock, args.max_calls, args.api_base, args.budget_cap, catalog=args.catalog,
                      alias=args.brand_alias, simulate_error=args.simulate_youcam_error, max_heavy=args.max_heavy)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR 啟動參數有問題：{exc}", file=sys.stderr)
        return 2
    if not args.mock and not state.key:
        print(f"ERROR 找不到 {nt.KEY_ENV}：設環境變數或寫進 {ROOT / '.env'}；只想看介面請加 --mock", file=sys.stderr)
        return 2
    httpd = make_server(args.host, args.port, state)
    mode = "mock（不扣點）" if args.mock else f"live（本次上限 {args.max_calls} 次；總預算 {state.budget.status()}）"
    print(f"http://{args.host}:{args.port}  模式：{mode}  presets {state.presets['version']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
