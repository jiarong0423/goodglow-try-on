"""Audit the text the server returns: send a set of representative requests to a running server (--mock) and check how much Chinese remains in English mode and whether Chinese mode changed.

  python3 tools/audit_api_strings.py http://localhost:8802 --lang en     # list the Chinese strings still left in English-mode responses (not in the table / untranslated)
  python3 tools/audit_api_strings.py http://localhost:8802 --lang zh --hash   # print a hash of each request's Chinese response (to compare with the server before a change)
  Add --strict: exit 1 if English mode still contains Chinese.

When comparing hashes, the code fields added in stage 3 (verdict_code, role_key) are removed first, so "only codes were added" does not count as a change.
Requests cover: presets, skin-card, status, catalog, demo-photo (two entry points), final-look (two entry points x demo subjects),
recommend (two entry points x makeup style x sensitivity), card, match, skin-plan (three data sources: all/official/partner), looks, makeup, layers, face (demo photo).
Failure paths print ERROR and return non-zero.

--leaks (alias-mode audit) runs two independent checks:
  1. brand_alias.leaks(): the substituter's own verdict.
  2. Independent list: read all brand data files directly (official site, 2018 public swatch list) and normalize brand and product names (strip diacritics, case, punctuation),
     then search for them (as substrings) in every string value and every key of the response; full shade names are checked in two places: a code/lighter/deeper field whose whole value equals the full shade name,
     or any string containing a multi-word full shade name that includes a digit (e.g. WX 64 Honeyed). Plain color names (Peach, Black Brown) are ordinary words in running text and do not count.
     Images (data: URIs and very long encoded strings) are skipped. If either check finds anything, exit 1.
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
    """Read the brand data files directly (bypassing the substituter) and return [(normalized, original name, kind)]."""
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
    """Return the list entries that appear in the response object (keys included). A full shade name that is only the code itself (e.g. X-004) does not count, because the code is the part kept visible."""
    found = set()

    def check(text, field=False):
        if is_opaque(text):
            return
        t = norm_key(text)
        for k, (orig, kind) in names.items():
            if kind in ("brand", "product"):
                hit = k in t
            else:                                                   # full shade name
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
    for src in ("all", "official", "partner"):         # source is chosen by the client: alias mode must not leak in any of the three
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
    ap.add_argument("--leaks", action="store_true", help="alias-mode audit: list any real brand/product names still visible in the responses (needs app/brand_alias.json and the brand data)")
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
        print(f"{len(hashes)} requests; {len(leaks)} kinds of real brand/product names leaked")
        for k, v in sorted(leaks.items()):
            print(f"  [{len(v)}×] {k[5:]}   ← {v[0]}")
        return 1 if leaks else 0
    if args.hash:
        for k, v in hashes.items():
            print(f"{v}  {k}")
        return 0
    print(f"{len(hashes)} requests; {len(left)} kinds / {total} occurrences of Chinese strings remain in English mode")
    for s, names in sorted(left.items(), key=lambda kv: -len(kv[1]))[:80]:
        print(f"  [{len(names)}×] {s[:110]}   ← {names[0]}")
    return 1 if (args.strict and left) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
