"""稽核伺服器回傳的文字：對一台執行中的伺服器（--mock）打一組代表性請求，看英文模式還剩多少中文、中文模式有沒有變。

  python3 tools/audit_api_strings.py http://localhost:8802 --lang en     # 列出英文模式回傳裡還剩的中文字串（表外／漏譯）
  python3 tools/audit_api_strings.py http://localhost:8802 --lang zh --hash   # 印每個請求中文回應的雜湊（拿來和改動前的伺服器比）
  加 --strict：英文模式還有中文就 exit 1。

雜湊比對時會先拿掉第 3 階段新增的代碼欄位（verdict_code、role_key），所以「只多了代碼」不算變動。
請求涵蓋：presets、skin-card、status、catalog、demo-photo（兩個入口）、final-look（兩個入口×示範對象）、
recommend（兩個入口×妝感×敏感度）、card、match、skin-plan（三種資料來源 all／official／partner）、looks、makeup、layers、face（示範照）。
失敗路徑印 ERROR 並回非零。

--leaks（代稱模式稽核）有兩道獨立檢查：
  1. brand_alias.leaks()：代換器自己的判定。
  2. 獨立名單：直接讀所有品牌資料檔（官網、2018 公開色票），把品牌名、產品名正規化（去音符、大小寫、標點）後，
     在回傳的每個字串值與每個 key 裡找（子字串）；色號全名則看兩處：欄位 code／lighter／deeper 的值整個等於色號全名，
     或任何字串裡出現「含數字的多字色號全名」（例如 WX 64 Honeyed）。單純色名（Peach、Black Brown）出現在一般文字裡是一般用語，不算。
     圖片（data: 與很長的編碼字串）略過。兩道任何一道有東西就 exit 1。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")
ADDED_KEYS = {"verdict_code", "role_key"}


def call(base, lang, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-App-Lang": lang},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def strip_added(o):
    if isinstance(o, dict):
        return {k: strip_added(v) for k, v in o.items() if k not in ADDED_KEYS}
    if isinstance(o, list):
        return [strip_added(x) for x in o]
    return o


def strings(o, path=""):
    if isinstance(o, str):
        if CJK.search(o):
            yield path, o
    elif isinstance(o, list):
        for i, x in enumerate(o):
            yield from strings(x, f"{path}[{i}]")
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from strings(v, f"{path}.{k}")


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", "".join(c for c in s if not unicodedata.combining(c)).casefold())


def is_opaque(s: str) -> bool:
    return s.startswith("data:") or (len(s) > 2000 and not any(c.isspace() for c in s))


def independent_names(app_dir: Path):
    """直接讀品牌資料檔（不經代換器），回傳 [(正規化後, 原名, 種類)]。"""
    names = {}
    for fn in ("partner_shades_official.json", "partner_shades.json"):
        f = app_dir / fn
        if not f.is_file():
            continue
        for p in json.loads(f.read_text(encoding="utf-8")).get("products", []):
            for kind, v in (("brand", p.get("brand")), ("product", p.get("name"))):
                if v and len(norm_key(v)) >= 3:
                    names[norm_key(v)] = (v, kind)
            for sh in p.get("shades", []):
                v = sh.get("code") or ""
                if len(v.split()) >= 2 and len(norm_key(v)) >= 6:
                    names.setdefault(norm_key(v), (v, "shade"))
    return names


def independent_leaks(resp, names) -> set:
    """回傳物件（含 key）裡出現的名單項目。色號全名若只是「代碼」本身（例如 X-004）不算，因為代碼是檯面保留的部分。"""
    found = set()

    def check(text, field=False):
        if is_opaque(text):
            return
        t = norm_key(text)
        for k, (orig, kind) in names.items():
            if kind in ("brand", "product"):
                hit = k in t
            else:                                                   # 色號全名
                hit = (field and t == k) or (k in t and any(c.isdigit() for c in orig))
            if hit:
                found.add(f"{kind}:{orig}")

    def walk(o, field=False):
        if isinstance(o, str):
            check(o, field)
        elif isinstance(o, list):
            for x in o:
                walk(x, field)
        elif isinstance(o, dict):
            for k, v in o.items():
                check(str(k))
                walk(v, k in ("code", "lighter", "deeper"))
    walk(resp)
    return found


def requests_plan(base, lang):
    plan = [("GET presets", "/api/presets", None), ("GET skin-card", "/api/skin-card", None),
            ("GET status", "/api/status", None), ("GET catalog", "/api/catalog", None),
            ("GET demo-photo albinism", "/api/demo-photo?condition=albinism", None),
            ("GET demo-photo vitiligo", "/api/demo-photo?condition=vitiligo", None)]
    for cond in ("albinism", "vitiligo"):
        for who in ("F", "M"):
            plan.append((f"GET final-look {cond} {who}", f"/api/final-look?condition={cond}&who={who}", None))
    sens_variants = [None, {"irritation": "often", "phototherapy": True, "fragrance": "yes"},
                     {"irritation": "never", "phototherapy": False, "fragrance": "no"},
                     {"irritation": "sometimes", "phototherapy": False, "fragrance": "unsure"}]
    for cond, extra in (("albinism", {"skin_hex": "#E1CDC6", "brow_hex": "#CBB4A8"}),
                        ("vitiligo", {"normal_hex": "#C99A82", "patch_hex": "#EBD3C9", "patch_ratio": 0.3, "brow_hex": "#3B2A24"})):
        for style in ("grooming", "natural", "polished"):
            for sens in sens_variants:
                plan.append((f"POST recommend {cond} {style} {('s' + str(sens_variants.index(sens)))}", "/api/recommend",
                             {"condition": cond, "style": style, "undertone": None, "sensitivity": sens, **extra}))
        for tone in ("pink_cool", "neutral", "warm_ivory"):
            plan.append((f"POST card {cond} {tone}", "/api/card",
                         {"undertone": tone, "undertone_unsure": False, "selection": {}, "skin_hex": extra.get("skin_hex") or extra["normal_hex"],
                          "condition": cond, "source": "all"}))
        for style in ("natural", "polished"):
            demo = {"style": style, "undertone": "neutral", "condition": cond, "demo": True, **{k: v for k, v in extra.items() if k in ("skin_hex", "brow_hex")}}
            plan.append((f"POST looks {cond} {style}", "/api/looks", demo))
            plan.append((f"POST makeup {cond} {style}", "/api/makeup", {**demo, "selection": {}}))
            plan.append((f"POST layers {cond} {style}", "/api/layers", {**demo, "selection": {}}))
            plan.append((f"POST face {cond} {style}", "/api/face", {"style": style, "demo": True, "condition": cond}))
    for src in ("all", "official", "partner"):         # source 由用戶端決定：代稱模式三種都不能外洩
        for cat in ("foundation", "concealer", "blush", "lip", "corrector", "brow"):
            plan.append((f"POST match {cat} {src}", "/api/match", {"hex": "#E1CDC6", "category": cat, "source": src, "top": 80}))
        plan.append((f"POST card albinism {src}", "/api/card", {"undertone": "neutral", "undertone_unsure": False, "selection": {},
                                                                 "skin_hex": "#E1CDC6", "condition": "albinism", "source": src}))
        plan.append((f"POST recommend vitiligo {src}", "/api/recommend", {"condition": "vitiligo", "style": "natural", "undertone": None,
                                                                          "sensitivity": None, "normal_hex": "#C99A82", "patch_hex": "#EBD3C9",
                                                                          "patch_ratio": 0.3, "brow_hex": "#3B2A24", "source": src}))
    plan.append(("POST skin-plan", "/api/skin-plan", {"condition": "vitiligo", "normal_hex": "#C99A82", "patch_hex": "#EBD3C9",
                                                      "patch_ratio": 0.3, "source": "all"}))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--lang", default="en", choices=("en", "zh"))
    ap.add_argument("--hash", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--leaks", action="store_true", help="代稱模式稽核：回傳裡還看得到真實品牌名／產品名就列出（需 app/brand_alias.json 與品牌資料）")
    args = ap.parse_args()
    ba = None
    if args.leaks:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app"))
        import brand_alias
        ba = brand_alias.load()
    names = independent_names(Path(__file__).resolve().parent.parent / "app") if args.leaks else {}
    left, total, hashes = {}, 0, {}
    for name, path, body in requests_plan(args.base, args.lang):
        status, resp = call(args.base, args.lang, path, body)
        resp = strip_added(resp)
        if ba is not None:
            for n in ba.leaks(resp):
                left.setdefault("LEAK:" + n, []).append(name)
                total += 1
            for n in independent_leaks(resp, names):
                left.setdefault("LEAK:" + n, []).append(name)
                total += 1
        hashes[name] = f"{status}:{hashlib.sha256(json.dumps(resp, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]}"
        if args.lang == "en":
            for p, s in strings(resp):
                left.setdefault(s, []).append(name)
                total += 1
    if args.leaks:
        leaks = {k: v for k, v in left.items() if k.startswith("LEAK:")}
        print(f"請求 {len(hashes)} 個；真實品牌／產品名外洩 {len(leaks)} 種")
        for k, v in sorted(leaks.items()):
            print(f"  [{len(v)}×] {k[5:]}   ← {v[0]}")
        return 1 if leaks else 0
    if args.hash:
        for k, v in hashes.items():
            print(f"{v}  {k}")
        return 0
    print(f"請求 {len(hashes)} 個；英文模式剩餘中文字串 {len(left)} 種／{total} 處")
    for s, names in sorted(left.items(), key=lambda kv: -len(kv[1]))[:80]:
        print(f"  [{len(names)}×] {s[:110]}   ← {names[0]}")
    return 1 if (args.strict and left) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
