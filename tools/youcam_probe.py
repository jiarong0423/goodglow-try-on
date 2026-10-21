#!/usr/bin/env python3
"""YouCam 探測工具：查價目、對任一功能跑一次 task，存去網址的回應；公開測試照可另存結果圖。

子指令：
  cost                       查各功能扣點（GET /s2s/v2.0/credit/feature-cost，不扣點）
  task FEATURE PHOTO BODY    上傳 PHOTO，以 BODY（JSON 檔）建立 task 並等結果；要加 --run 才會真的送出（扣點），沒加只預演
                             --save-images 只能用在 photos/public 或 photos/crops 的公開照片

結果寫到 runs/<時間>_probe_<feature>/：response.json（網址已移除）、images/（選用）、meta.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noise_test as nt  # noqa: E402

PUBLIC_DIRS = {"public", "crops"}


def client() -> nt.YouCamClient:
    key = nt.load_api_key()
    if not key:
        nt.log_error(f"找不到 {nt.KEY_ENV}")
        sys.exit(2)
    return nt.YouCamClient(key)


def balance(c: nt.YouCamClient) -> int:
    resp = c._json("GET", "/s2s/v1.0/client/credit")
    return sum(int(r.get("amount", 0)) for r in resp.get("results", []))


def cmd_cost(args) -> int:
    c = client()
    token, skus = None, []
    for _ in range(20):
        path = "/s2s/v2.0/credit/feature-cost?page_size=20" + (f"&starting_token={token}" if token else "")
        resp = c._json("GET", path)
        res = resp.get("result") or resp.get("data") or {}
        skus += res.get("skus", [])
        token = res.get("next_token")
        if not token:
            break
    keyword = (args.filter or "").lower()
    for s in skus:
        text = json.dumps(s, ensure_ascii=False)
        if keyword in text.lower():
            print(text)
    print(f"共 {len(skus)} 筆 SKU")
    return 0


def find_urls(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            find_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_urls(v, out)
    elif isinstance(obj, str) and obj.startswith("https://"):
        out.append(obj)
    return out


def cmd_task(args) -> int:
    photo = Path(args.photo).resolve()
    if args.save_images and photo.parent.name not in PUBLIC_DIRS:
        nt.log_error("--save-images 只允許公開測試照片（photos/public、photos/crops）")
        return 2
    body = json.loads(Path(args.body).read_text(encoding="utf-8"))
    if not args.run:
        print(f"DRY-RUN task feature={args.feature} photo={photo.name} body_keys={sorted(body)}；沒有呼叫 YouCam、不扣點。確定要送出再加 --run。")
        return 0
    c = client()
    jpeg, w, h = nt.prepare_photo(photo)
    if args.max_long and max(w, h) > args.max_long:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(jpeg))
        s = args.max_long / max(w, h)
        img = img.resize((round(w * s) - (1 if round(w * s) >= args.max_long else 0),
                          round(h * s) - (1 if round(h * s) >= args.max_long else 0)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=95)
        jpeg, (w, h) = buf.getvalue(), img.size

    run_dir = Path(args.runs_dir) / f"{dt.datetime.now():%Y%m%d_%H%M%S}_probe_{args.feature}_{photo.stem}"
    run_dir.mkdir(parents=True)
    before = balance(c)
    t0 = time.monotonic()
    file_id = c.upload(jpeg, f"{photo.stem}.jpg")
    body = dict(body, src_file_id=file_id)
    task_id = c._json("POST", f"/s2s/v2.0/task/{args.feature}", body)["data"]["task_id"]
    deadline = time.monotonic() + nt.POLL_TIMEOUT
    while True:
        resp = c._json("GET", f"/s2s/v2.0/task/{args.feature}/{task_id}")
        data = resp.get("data", {})
        if data.get("task_status") in ("success", "error"):
            break
        if time.monotonic() > deadline:
            nt.log_error("等待逾時")
            return 1
        time.sleep(nt.POLL_SECONDS)
    secs = round(time.monotonic() - t0, 1)

    saved = []
    if args.save_images and data.get("task_status") == "success":
        (run_dir / "images").mkdir()
        for i, url in enumerate(find_urls(data, [])):
            name = url.split("?")[0].rsplit("/", 1)[-1] or f"image_{i}"
            target = run_dir / "images" / f"{i:02d}_{name}"
            with urllib.request.urlopen(url, timeout=60) as r:
                target.write_bytes(r.read())
            saved.append(target.name)
    after = balance(c)

    (run_dir / "response.json").write_text(json.dumps(nt.strip_urls(resp), ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    body_logged = {k: v for k, v in body.items() if k != "src_file_id"}
    meta = {"feature": args.feature, "photo": photo.name, "upload_size": [w, h], "body": body_logged,
            "task_status": data.get("task_status"), "error": data.get("error") or data.get("error_code"),
            "seconds": secs, "balance_before": before, "balance_after": after, "units_spent": before - after,
            "saved_images": saved}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"輸出：{run_dir}")
    if data.get("task_status") != "success":
        nt.log_error(f"task 失敗：{meta['error']}")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("cost")
    a.add_argument("--filter", help="只印含此字串的 SKU")
    a.set_defaults(func=cmd_cost)
    b = sub.add_parser("task")
    b.add_argument("feature", help="例如 skin-analysis、face-attr-analysis、skin-tone-analysis、makeup-vto")
    b.add_argument("photo")
    b.add_argument("body", help="task 參數 JSON 檔（不含 src_file_id）")
    b.add_argument("--run", action="store_true", help="真的送出（會扣該功能的點數）；沒加只印預演")
    b.add_argument("--save-images", action="store_true")
    b.add_argument("--max-long", type=int, default=0, help="上傳前把長邊縮到小於此值（Makeup VTO 要 < 1920）")
    b.add_argument("--runs-dir", default=str(nt.DEFAULT_RUNS_DIR))
    b.set_defaults(func=cmd_task)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
