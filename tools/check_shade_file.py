#!/usr/bin/env python3
"""Check a retailer shade file before the server loads it (same keys as app/catalog.json; see docs/SHADE_FILE.md).

Usage: python3 -I -B tools/check_shade_file.py <path-to-json>
Prints the product and shade counts, or the first problem with its location. Exit code 0 = ok, 1 = problem, 2 = usage.
Reads one file, writes nothing, makes no network calls.
"""
import json
import re
import sys

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
UNDERTONES = {"cool", "neutral", "warm"}


def problems(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("products"), list) or not doc["products"]:
        yield "top level: expected an object with a non-empty 'products' list"
        return
    for i, p in enumerate(doc["products"]):
        where = f"products[{i}]"
        if not isinstance(p, dict):
            yield f"{where}: expected an object"
            continue
        for key in ("brand", "name", "category"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                yield f"{where}.{key}: required, non-empty text (the loader raises KeyError on a missing 'brand')"
        url = p.get("source_url")
        if url is not None and not (isinstance(url, str) and url.startswith("https://")):
            yield f"{where}.source_url: must start with https:// (otherwise no link is shown)"
        shades = p.get("shades")
        if not isinstance(shades, list) or not shades:
            yield f"{where}.shades: expected a non-empty list"
            continue
        for j, s in enumerate(shades):
            w = f"{where}.shades[{j}]"
            if not isinstance(s, dict):
                yield f"{w}: expected an object"
                continue
            if not isinstance(s.get("code"), str) or not s["code"].strip():
                yield f"{w}.code: required, non-empty text"
            if not isinstance(s.get("hex"), str) or not HEX.match(s["hex"]):
                yield f"{w}.hex: required, '#RRGGBB'"
            if "undertone" in s and s["undertone"] not in UNDERTONES:
                yield f"{w}.undertone: one of cool / neutral / warm, or leave it out"


def main(argv):
    if len(argv) != 2:
        print("usage: check_shade_file.py <path-to-json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"ERROR cannot read {argv[1]}: {exc}")
        return 1
    found = list(problems(doc))
    if found:
        for line in found[:20]:
            print("ERROR " + line)
        print(f"ERROR {len(found)} problem(s); the app would not load this file safely")
        return 1
    n = sum(len(p["shades"]) for p in doc["products"])
    print(f"OK {len(doc['products'])} product(s), {n} shade(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
