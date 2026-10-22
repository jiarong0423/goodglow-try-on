"""Demo mode: run each combination the front end actually sends for the demo photo through real try-on, and record it as an exact replay.

The combination list comes from the browser: the real front-end flow runs once and every /api/makeup request is intercepted
. Recording goes straight through app/server.py's State.makeup (same code path as live mode),
and each result is saved to demo/replay/<entry>/exact/<code>.jpg, with demo/replay/<entry>/exact/index.json recording options and sha256.

  python3 -B tools/record_demo_replays.py --payloads local run notes (not shipped)            # dry run: count only, calls nothing, spends no units
  python3 -B tools/record_demo_replays.py --payloads ... --run --cap 136                                         # live run (1 unit per combination)
Already-recorded combinations (file exists) are skipped, so an interrupted run can simply be rerun.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_server():
    spec = importlib.util.spec_from_file_location("server", ROOT / "app" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "app"))
    sys.path.insert(0, str(ROOT / "tools"))
    spec.loader.exec_module(mod)
    return mod


def shrink_jpeg(raw: bytes, long_side: int = 1280, quality: int = 85) -> bytes:
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    s = long_side / max(img.size)
    if s < 1:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def plan(server, payloads: list[dict]) -> list[dict]:
    """Drop duplicates and "keep as is" (keep as is never calls the API), and return the combinations still to record."""
    seen, todo = set(), []
    for p in payloads:
        b = p["body"]
        cond = server.demo_condition(b)
        style = b.get("style", "natural")
        sel = dict(b.get("selection") or {}, undertone=b.get("undertone", "neutral"))
        for k in ("brow_hex", "skin_hex", "foundation_hex", "face_shape"):      # same as server.State.makeup
            if b.get(k):
                sel[k] = b[k]
        if all(sel.get(k, "keep") == "keep" for k in server.EXACT_ITEMS):
            continue
        key = server.exact_key(cond, style, sel)
        if key in seen:
            continue
        seen.add(key)
        out = server.DEMOS[cond]["replay"] / "exact" / f"{key}.jpg"
        todo.append({"key": key, "cond": cond, "style": style, "tag": p.get("tag"), "body": b, "out": out,
                     "selection": {k: sel.get(k, "keep") for k in server.EXACT_ITEMS}, "undertone": sel["undertone"],
                     "done": out.is_file()})
    return todo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--payloads", required=True)
    ap.add_argument("--run", action="store_true", help="live run (spends units); without it this is a dry run")
    ap.add_argument("--cap", type=int, default=0, help="total budget cap in units (ledger runs/budget_ledger.json)")
    ap.add_argument("--limit", type=int, default=0, help="at most this many this run (0 = all); record 1 first on the first live run")
    args = ap.parse_args(argv)
    server = load_server()
    payloads = json.loads(Path(args.payloads).read_text(encoding="utf-8"))
    todo = plan(server, payloads)
    need = [t for t in todo if not t["done"]]
    if args.limit:
        need = need[:args.limit]
    by_tag: dict[str, int] = {}
    for t in need:
        by_tag[t["tag"]] = by_tag.get(t["tag"], 0) + 1
    import budget as bg
    spent = bg.Budget(args.cap or 1).spent
    print(json.dumps({"captured": len(payloads), "unique": len(todo), "already_recorded": len(todo) - len(need),
                      "to_record": len(need), "by_tag": by_tag, "ledger_spent": spent, "cap": args.cap,
                      "fits_cap": spent + len(need) <= args.cap if args.cap else None}, ensure_ascii=False, indent=1))
    if not args.run:
        print("Dry run: no API call and no units spent (add --run --cap N to record for real)")
        return 0
    if not args.cap or spent + len(need) > args.cap:
        print(f"ERROR cap too low: spent {spent} + to record {len(need)} > cap {args.cap}", file=sys.stderr)
        return 2
    state = server.State(False, len(need) + 2, server.nt.API_BASE, args.cap)
    ok = fail = 0
    for n, t in enumerate(need, 1):
        idx_path = t["out"].parent / "index.json"
        try:
            res = state.makeup(t["body"])
        except server.RequestError as exc:
            fail += 1
            print(f"ERROR {n}/{len(need)} {t['tag']} {t['key']}: {exc}", file=sys.stderr, flush=True)
            if "預算" in str(exc) or "上限" in str(exc):
                break
            continue
        raw = base64.b64decode(res["image"].split(",", 1)[1])
        t["out"].parent.mkdir(parents=True, exist_ok=True)
        saved = shrink_jpeg(raw)               # YouCam returns a PNG of about 3.4 MB; shrink it to a JPEG with a 1280 long side for display
        t["out"].write_bytes(saved)
        idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.is_file() else {}
        idx[t["key"]] = {"tag": t["tag"], "style": t["style"], "undertone": t["undertone"], "selection": t["selection"],
                         "colors": {k: t["body"].get(k) for k in ("skin_hex", "brow_hex", "foundation_hex") if t["body"].get(k)},
                         "photo": str(server.DEMOS[t["cond"]]["photo"].relative_to(ROOT)),
                         "sha256": hashlib.sha256(saved).hexdigest(), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                         "raw_bytes": len(raw), "recorded_at": dt.datetime.now().isoformat(timespec="seconds")}
        idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        ok += 1
        print(f"ok {n}/{len(need)} {t['tag']} {t['key']}", flush=True)
    print(json.dumps({"recorded": ok, "failed": fail, "ledger_spent": state.budget.spent}, ensure_ascii=False))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
