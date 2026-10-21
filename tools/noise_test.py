#!/usr/bin/env python3
"""重拍雜訊測試：同一張臉重拍多次，量 YouCam AI Skin Analysis 分數的雜訊範圍。

只存分數。不存照片、不存遮罩圖、不存 API 回應裡的網址。
照片在記憶體內轉正方向、縮到 HD 規格、重新編碼成 JPEG（同時去掉 EXIF／GPS）後才上傳。

子指令：
  check      只驗照片規格，不連網、不扣點
  run        上傳並分析（需 --run 旗標與 YOUCAM_API_KEY 環境變數）
  summarize  合併一或多次 run 的 scores.csv，比較不同拍攝條件

API 格式依 https://docs.perfectcorp.com/reference/ai_skin_analysis（2026-10-15 讀取）。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import itertools
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://yce-api-01.makeupar.com"
KEY_ENV = "YOUCAM_API_KEY"
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
HD_MIN_SHORT = 1080
HD_MAX_LONG = 2560
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_CONCERNS = ["hd_redness"]
DEFAULT_MAX_CALLS = 20
MIN_N_FOR_BAND = 10
POLL_SECONDS = 3.0
POLL_TIMEOUT = 300.0
RETRY_WAITS = (5.0, 10.0, 20.0)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = PROJECT_ROOT / "runs"

CSV_FIELDS = [
    "run_id", "label", "photo_name", "photo_sha12", "width", "height",
    "concern", "region", "raw_score", "ui_score", "label_value", "status", "error_code", "task_seconds",
]


class PhotoError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


def log_error(msg: str) -> None:
    print(f"ERROR {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 照片

def list_photos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise PhotoError(f"找不到照片目錄：{folder}")
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES)


def prepare_photo(path: Path) -> tuple[bytes, int, int]:
    """回傳 (JPEG bytes, 寬, 高)。只在記憶體內處理，不寫檔。"""
    try:
        return prepare_image_bytes(path.read_bytes())
    except PhotoError as exc:
        raise PhotoError(f"{path.name}：{exc}") from None


def prepare_image_bytes(raw: bytes) -> tuple[bytes, int, int]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as src:
        img = ImageOps.exif_transpose(src).convert("RGB")
    w, h = img.size
    if max(w, h) > HD_MAX_LONG:
        scale = HD_MAX_LONG / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        w, h = img.size
    if min(w, h) < HD_MIN_SHORT:
        raise PhotoError(f"短邊 {min(w, h)}px，低於 HD 下限 {HD_MIN_SHORT}px")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)  # 不帶 exif 參數 → EXIF／GPS 不會寫入
    data = buf.getvalue()
    if len(data) >= MAX_UPLOAD_BYTES:
        raise PhotoError(f"重新編碼後 {len(data)} bytes，超過 10MB")
    return data, w, h


def load_api_key() -> str:
    """環境變數優先；沒有就讀專案根目錄 .env（只取這一個鍵，不印出值）。"""
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip().removeprefix("export ").strip() == KEY_ENV:
                return value.strip().strip("'\"")
    return ""


def sha12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------- API

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """換主機的轉址不帶 Authorization：金鑰只會送給原本要呼叫的那個主機。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect)


class YouCamClient:
    def __init__(self, api_key: str, base: str = API_BASE):
        self._key = api_key
        self.base = base.rstrip("/")

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "<key>") if self._key else text

    def _request(self, method: str, url: str, body: bytes | None = None,
                 headers: dict | None = None, auth: bool = True) -> dict:
        hdrs = dict(headers or {})
        if auth:
            hdrs["Authorization"] = f"Bearer {self._key}"
        for attempt in range(len(RETRY_WAITS) + 1):
            req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
            try:
                with _OPENER.open(req, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                detail = self._redact(exc.read().decode("utf-8", "replace"))[:300]
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt < len(RETRY_WAITS):
                    time.sleep(RETRY_WAITS[attempt])
                    continue
                raise ApiError(exc.code, f"HTTP {exc.code} {method} {url.split('?')[0]}：{detail}") from None
            except urllib.error.URLError as exc:
                if attempt < len(RETRY_WAITS):
                    time.sleep(RETRY_WAITS[attempt])
                    continue
                raise ApiError(None, f"連線失敗 {method} {url.split('?')[0]}：{exc.reason}") from None
        raise AssertionError("unreachable")

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        return self._request(method, f"{self.base}{path}", body, {"Content-Type": "application/json"})

    def upload(self, data: bytes, file_name: str) -> str:
        meta = {"files": [{"content_type": "image/jpeg", "file_name": file_name, "file_size": len(data)}]}
        try:
            resp = self._json("POST", "/s2s/v2.0/file", meta)
        except ApiError as exc:
            if exc.status != 404:
                raise
            # 舊版文件的路徑寫法；File API 不扣點，退回重試無成本
            resp = self._json("POST", "/s2s/v2.0/file/skin-analysis", meta)
        entry = resp["data"]["files"][0]
        put = entry["requests"][0]
        self._request(put.get("method", "PUT"), put["url"], data, put.get("headers", {}), auth=False)
        return entry["file_id"]

    def create_task(self, file_id: str, concerns: list[str]) -> str:
        resp = self._json("POST", "/s2s/v2.0/task/skin-analysis",
                          {"src_file_id": file_id, "dst_actions": concerns, "format": "json"})
        return resp["data"]["task_id"]

    def wait(self, task_id: str) -> dict:
        deadline = time.monotonic() + POLL_TIMEOUT
        while True:
            data = self._json("GET", f"/s2s/v2.0/task/skin-analysis/{task_id}").get("data", {})
            if data.get("task_status") in ("success", "error"):
                return data
            if time.monotonic() > deadline:
                raise ApiError(None, f"等待逾時（{POLL_TIMEOUT:.0f}s），task_status={data.get('task_status')}")
            time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- 解析

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _flatten(name: str, node: dict) -> list[tuple[str, str, float | None, float | None]]:
    # 實際回應（2026-10-15 實跑）：每個區域一筆，區域放在平的 "region" 欄位；
    # "all"、"skin_age" 用 "score"；hd_skin_type 只有文字 "skin_type"（由 parse_labels 處理）
    region = str(node.get("region") or "whole")
    if "raw_score" in node or "ui_score" in node:
        return [(name, region, _num(node.get("raw_score")), _num(node.get("ui_score")))]
    if _num(node.get("score")) is not None:
        return [(name, region, _num(node.get("score")), None)]
    rows = []
    for region, sub in node.items():
        if isinstance(sub, dict) and ("raw_score" in sub or "ui_score" in sub):
            rows.append((name, region, _num(sub.get("raw_score")), _num(sub.get("ui_score"))))
    return rows


def parse_scores(data: dict) -> list[tuple[str, str, float | None, float | None]]:
    """支援兩種格式：v1.7 起的 output 清單，以及 score_info.json 式的字典。"""
    results = data.get("results", data)
    out = results.get("output", results) if isinstance(results, dict) else results
    rows: list = []
    if isinstance(out, list):
        for item in out:
            if isinstance(item, dict):
                rows += _flatten(str(item.get("type", "?")), item)
    elif isinstance(out, dict):
        for key, val in out.items():
            if _num(val) is not None:
                rows.append((key, "whole", _num(val), None))  # "all"、"skin_age"
            elif isinstance(val, dict):
                rows += _flatten(key, val)
    return rows


def parse_labels(data: dict) -> list[tuple[str, str, str]]:
    """文字型結果，例如 hd_skin_type 的 whole／t_zone／u_zone → Normal、Oily…"""
    results = data.get("results", data)
    out = results.get("output", []) if isinstance(results, dict) else results
    labels = []
    if isinstance(out, list):
        for item in out:
            if isinstance(item, dict) and isinstance(item.get("skin_type"), str):
                labels.append((str(item.get("type", "?")), str(item.get("region") or "whole"), item["skin_type"]))
    return labels


def strip_urls(obj):
    """留結構不留網址，供第一次實跑確認回應格式。"""
    if isinstance(obj, dict):
        return {k: strip_urls(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_urls(v) for v in obj]
    if isinstance(obj, str) and obj.startswith(("http://", "https://")):
        return "<url removed>"
    return obj


# ---------------------------------------------------------------- 統計

def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def describe(values: list[float]) -> dict:
    n = len(values)
    d = {"n": n, "mean": statistics.fmean(values), "min": min(values), "max": max(values)}
    d["range"] = d["max"] - d["min"]
    if n >= 2:
        sd = statistics.stdev(values)
        diffs = [abs(a - b) for a, b in itertools.combinations(values, 2)]
        d["sd"] = sd
        d["mdc95_normal"] = 1.96 * math.sqrt(2) * sd       # 常態假設下的最小可偵測變化
        d["pair_diff_p95"] = quantile(diffs, 0.95)          # 經驗分位數，不假設分布
        d["noise_band"] = max(d["mdc95_normal"], d["pair_diff_p95"])
    else:
        d.update(sd=None, mdc95_normal=None, pair_diff_p95=None, noise_band=None)
    d["enough_n"] = n >= MIN_N_FOR_BAND
    return d


def summarize_rows(rows: list[dict]) -> dict:
    groups: dict[tuple, dict[str, list[float]]] = {}
    for r in rows:
        if r.get("status") != "success":
            continue
        key = (r["label"], r["concern"], r["region"])
        for kind in ("raw_score", "ui_score"):
            v = r.get(kind)
            if v not in (None, ""):
                groups.setdefault(key, {}).setdefault(kind, []).append(float(v))
    per_label = []
    for (label, concern, region), kinds in sorted(groups.items()):
        entry = {"label": label, "concern": concern, "region": region}
        for kind, vals in kinds.items():
            entry[kind] = describe(vals)
        per_label.append(entry)

    # 不同拍攝條件之間的平均位移，跟雜訊帶比
    by_metric: dict[tuple, list[dict]] = {}
    for e in per_label:
        if "raw_score" in e:
            by_metric.setdefault((e["concern"], e["region"]), []).append(e)
    conditions = []
    for (concern, region), entries in sorted(by_metric.items()):
        if len(entries) < 2:
            continue
        means = {e["label"]: e["raw_score"]["mean"] for e in entries}
        bands = [e["raw_score"]["noise_band"] for e in entries if e["raw_score"]["noise_band"] is not None]
        shift = max(means.values()) - min(means.values())
        tightest = min(bands) if bands else None
        conditions.append({
            "concern": concern, "region": region, "label_means": means,
            "max_shift": shift, "tightest_noise_band": tightest,
            "condition_exceeds_noise": (tightest is not None and shift > tightest),
        })
    failures = sum(1 for r in rows if r.get("status") != "success")
    return {"per_label": per_label, "conditions": conditions, "failed_rows": failures}


def _f(v, digits=2):
    return "—" if v is None else f"{v:.{digits}f}"


def render_markdown(summary: dict, title: str) -> str:
    lines = [f"# {title}", "",
             "分數用 raw_score（1–100，越高越好）。雜訊帶 = max(常態 MDC95, 兩兩差距 P95)；",
             f"n < {MIN_N_FOR_BAND} 時雜訊帶只供參考。", "",
             "| 條件 | 指標 | 區域 | n | 平均 | SD | 全距 | MDC95 | 兩兩差 P95 | 雜訊帶 | ui SD |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in summary["per_label"]:
        r = e.get("raw_score")
        if not r:
            continue
        ui = e.get("ui_score", {})
        mark = "" if r["enough_n"] else " ⚠"
        lines.append(
            f"| {e['label']} | {e['concern']} | {e['region']} | {r['n']}{mark} | {_f(r['mean'])} | "
            f"{_f(r['sd'])} | {_f(r['range'])} | {_f(r['mdc95_normal'])} | {_f(r['pair_diff_p95'])} | "
            f"**{_f(r['noise_band'])}** | {_f(ui.get('sd'))} |")
    if summary["conditions"]:
        lines += ["", "## 拍攝條件影響", "",
                  "| 指標 | 區域 | 各條件平均 | 最大位移 | 最窄雜訊帶 | 條件影響 > 雜訊 |",
                  "|---|---|---|---|---|---|"]
        for c in summary["conditions"]:
            means = "、".join(f"{k} {v:.2f}" for k, v in c["label_means"].items())
            lines.append(f"| {c['concern']} | {c['region']} | {means} | {_f(c['max_shift'])} | "
                         f"{_f(c['tightest_noise_band'])} | {'是' if c['condition_exceeds_noise'] else '否'} |")
    lines += ["", f"失敗列數：{summary['failed_rows']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- 指令

def cmd_check(args) -> int:
    try:
        photos = list_photos(Path(args.photos))
    except PhotoError as exc:
        log_error(str(exc))
        return 2
    if not photos:
        log_error(f"{args.photos} 內沒有 jpg/jpeg/png 照片（iPhone HEIC 請先匯出成 JPEG）")
        return 2
    bad = 0
    for p in photos:
        try:
            data, w, h = prepare_photo(p)
            print(f"OK   {p.name}  上傳尺寸 {w}x{h}  {len(data) // 1024} KB")
        except Exception as exc:  # PIL 讀不了的檔也算不合格
            bad += 1
            log_error(f"{p.name}：{exc}")
    print(f"共 {len(photos)} 張，合格 {len(photos) - bad} 張。check 不連網、不扣點。")
    return 2 if bad else 0


def cmd_run(args) -> int:
    if not args.run:
        log_error("run 會扣 API 點數，必須加 --run 才執行；只想驗照片請用 check")
        return 2
    api_key = load_api_key()
    if not api_key:
        log_error(f"找不到 {KEY_ENV}：請設環境變數，或寫進 {PROJECT_ROOT / '.env'}")
        return 2
    concerns = [c.strip() for c in args.concerns.split(",") if c.strip()]
    if len({c.startswith("hd_") for c in concerns}) > 1:
        log_error("HD（hd_ 開頭）與 SD 指標不能混在同一次請求")
        return 2
    try:
        photos = list_photos(Path(args.photos))
    except PhotoError as exc:
        log_error(str(exc))
        return 2
    if not photos:
        log_error(f"{args.photos} 內沒有照片")
        return 2
    if len(photos) > args.max_calls:
        log_error(f"照片 {len(photos)} 張超過 --max-calls {args.max_calls}；要跑請提高上限")
        return 2

    prepared = []
    for p in photos:  # 全部先驗完才開始扣點
        try:
            prepared.append((p, *prepare_photo(p)))
        except Exception as exc:
            log_error(f"{p.name}：{exc}（未呼叫 API，未扣點）")
            return 2

    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{args.label}"
    run_dir = Path(args.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    client = YouCamClient(api_key, args.api_base)
    started = dt.datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []
    ok = failed = 0
    schema_written = False

    with open(run_dir / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for i, (path, data, w, h) in enumerate(prepared, 1):
            base = {"run_id": run_id, "label": args.label, "photo_name": path.name,
                    "photo_sha12": sha12(path), "width": w, "height": h}
            t0 = time.monotonic()
            try:
                file_id = client.upload(data, f"{base['photo_sha12']}.jpg")
                task_id = client.create_task(file_id, concerns)
                result = client.wait(task_id)
                secs = round(time.monotonic() - t0, 1)
                if result.get("task_status") == "success":
                    if not schema_written:
                        (run_dir / "schema_sample.json").write_text(
                            json.dumps(strip_urls(result), ensure_ascii=False, indent=2), encoding="utf-8")
                        schema_written = True
                    scores = parse_scores(result)
                    if not scores:
                        raise ApiError(None, "task 成功但解析不到任何分數，請看 schema_sample.json")
                    new = [dict(base, concern=c, region=r, raw_score=raw, ui_score=ui, label_value="",
                                status="success", error_code="", task_seconds=secs)
                           for c, r, raw, ui in scores]
                    labels = parse_labels(result)
                    new += [dict(base, concern=c, region=r, raw_score="", ui_score="", label_value=v,
                                 status="success", error_code="", task_seconds=secs)
                            for c, r, v in labels]
                    ok += 1
                    shown = "、".join([f"{c}/{r} {_f(raw)}" for c, r, raw, _ in scores] +
                                     [f"{c}/{r} {v}" for c, r, v in labels])
                    print(f"[{i}/{len(prepared)}] {path.name}  {shown}  ({secs}s)", flush=True)
                else:
                    code = str(result.get("error_code") or result.get("error") or "task_error")
                    new = [dict(base, concern="", region="", raw_score="", ui_score="", label_value="",
                                status="error", error_code=code, task_seconds=secs)]
                    failed += 1
                    log_error(f"[{i}/{len(prepared)}] {path.name} task 失敗：{code}（失敗不扣點）")
            except (ApiError, KeyError, ValueError) as exc:
                secs = round(time.monotonic() - t0, 1)
                msg = client._redact(str(exc))[:200]
                new = [dict(base, concern="", region="", raw_score="", ui_score="", label_value="",
                            status="error", error_code=msg, task_seconds=secs)]
                failed += 1
                log_error(f"[{i}/{len(prepared)}] {path.name}：{msg}")
            writer.writerows(new)
            fh.flush()
            rows += new

    summary = summarize_rows(rows)
    meta = {"run_id": run_id, "label": args.label, "concerns": concerns, "photos": len(prepared),
            "succeeded": ok, "failed": failed, "api_base": args.api_base,
            "started": started, "finished": dt.datetime.now().isoformat(timespec="seconds"),
            "stored": "scores only; no photos, masks or result URLs"}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown(summary, f"重拍雜訊測試 {run_id}")
    (run_dir / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"輸出：{run_dir}")
    if failed:
        log_error(f"{failed}/{len(prepared)} 張失敗，成功 {ok} 張")
        return 1
    print(f"OK 全部 {ok} 張成功（成功的 task 會扣點，到 API 後台看實際扣點）")
    return 0


def read_scores(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            path = path / "scores.csv"
        with open(path, newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    return rows


def cmd_summarize(args) -> int:
    try:
        rows = read_scores(args.inputs)
    except OSError as exc:
        log_error(f"讀不到 scores.csv：{exc}")
        return 2
    summary = summarize_rows(rows)
    md = render_markdown(summary, "重拍雜訊測試：合併摘要")
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "summary.md").write_text(md, encoding="utf-8")
        print(f"輸出：{out}")
    if not summary["per_label"]:
        log_error("沒有任何成功的分數可摘要")
        return 1
    return 0


def cmd_credit(args) -> int:
    """查點數餘額（不扣點）。端點取自其他參賽者 README，尚未經官方文件確認。"""
    api_key = load_api_key()
    if not api_key:
        log_error(f"找不到 {KEY_ENV}：請設環境變數，或寫進 {PROJECT_ROOT / '.env'}")
        return 2
    client = YouCamClient(api_key, args.api_base)
    try:
        resp = client._json("GET", "/s2s/v1.0/client/credit")
    except ApiError as exc:
        log_error(f"查餘額失敗：{client._redact(str(exc))}；請改到 API 後台看")
        return 1
    print(json.dumps(strip_urls(resp), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="只驗照片規格，不連網")
    c.add_argument("--photos", required=True, help="照片目錄")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("run", help="上傳並分析（扣點）")
    r.add_argument("--photos", required=True, help="照片目錄（一個目錄 = 一種拍攝條件）")
    r.add_argument("--label", required=True, help="拍攝條件名稱，例如 same_light、window")
    r.add_argument("--concerns", default=",".join(DEFAULT_CONCERNS), help="逗號分隔，預設 hd_redness")
    r.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="張數上限，防止誤扣點")
    r.add_argument("--run", action="store_true", help="確認要扣點才加")
    r.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    r.add_argument("--api-base", default=API_BASE, help=argparse.SUPPRESS)
    r.set_defaults(func=cmd_run)

    k = sub.add_parser("credit", help="查點數餘額（不扣點）")
    k.add_argument("--api-base", default=API_BASE, help=argparse.SUPPRESS)
    k.set_defaults(func=cmd_credit)

    s = sub.add_parser("summarize", help="合併多次 run 比較拍攝條件")
    s.add_argument("inputs", nargs="+", help="run 目錄或 scores.csv")
    s.add_argument("--out", help="輸出目錄（不給就只印）")
    s.set_defaults(func=cmd_summarize)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
