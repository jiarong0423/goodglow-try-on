#!/usr/bin/env python3
"""Check that the numbers the documentation states are the numbers the code and data produce.

  python3 -B tools/check_claims.py          # prints one line per claim; exit 1 if any claim is wrong

Each claim is computed from the repository (replay index files, the reproducible measurement, the price table, the
argument defaults, the skin tone card) and the documentation must contain exactly that number. CI runs this on every
push, so a number cannot drift away from what was measured.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

README = (ROOT / "README.md").read_text(encoding="utf-8")
EVIDENCE = (ROOT / "evidence" / "README.md").read_text(encoding="utf-8")
FAILED: list[str] = []


def check(label: str, expected, doc: str, pattern: str) -> None:
    """pattern has one group; the documented value must equal `expected` (compared as text)."""
    m = re.search(pattern, doc)
    got = m.group(1) if m else None
    ok = got is not None and str(got) == str(expected)
    print(("OK   " if ok else "FAIL ") + f"{label}: code says {expected}, documentation says {got}")
    if not ok:
        FAILED.append(label)


def replay_count() -> int:
    return sum(len(json.loads(p.read_text(encoding="utf-8"))) for p in sorted((ROOT / "demo" / "replay").glob("*/exact/index.json")))


def main() -> int:
    import budget
    import server
    import skin_card

    check("saved real Makeup VTO calls behind demo mode", replay_count(), README, r"\*\*(\d+) real YouCam Makeup VTO calls\*\*")
    check("skin tone card depth levels", len(skin_card.LEVEL_NAMES), README, r"(\d+)-level")
    check("Makeup VTO price (credits)", budget.PRICES["makeup-vto"], README, r"Makeup VTO costs (\d+) credit")
    check("Face Attribute price (credits)", budget.PRICES["face-attr-analysis(1-5)"], README, r"Face Attribute calls, (\d+) credits each")
    check("minimum photo short side (px)", server.MIN_SHORT, README, r"Photos under (\d+) px on the short side are rejected")

    ap_defaults = {a.dest: a.default for a in _parser().__dict__["_actions"]}
    check("default --max-calls", ap_defaults["max_calls"], README, r"--max-calls (\d+) --budget-cap")
    check("default --budget-cap", ap_defaults["budget_cap"], README, r"--budget-cap (\d+)\b")

    out = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools" / "repro_region_gap.py"), "--json"], capture_output=True, text=True, cwd=ROOT)
    if out.returncode != 0:
        print("FAIL repro_region_gap.py did not pass its own tolerance check")
        FAILED.append("repro tolerance")
    else:
        r = json.loads(out.stdout)
        check("original lightness gap (L*)", f"{r['original']['gap_abs']:.1f}", EVIDENCE, r"Original photo \| (\d+\.\d)")
        check("gap with YouCam dark-shade foundation (L*)", f"{r['youcam_foundation_dark']['gap_abs']:.1f}", EVIDENCE, r"dark shade \| (\d+\.\d)")
        check("gap with YouCam light-shade foundation (L*)", f"{r['youcam_foundation_light']['gap_abs']:.1f}", EVIDENCE, r"light shade \| (\d+\.\d)")
        check("gap with our layered local fill (L*)", f"{r['local_layers']['gap_abs']:.1f}", EVIDENCE, r"local fill \| (\d+\.\d)")
        check("local fill shrinks the gap by (%)", round(r["local_layers"]["gap_shrunk_pct"]), EVIDENCE, r"Our layered local fill \| [\d.]+ \| about (\d+)%")
    print(f"\n{len(FAILED)} claim(s) wrong" if FAILED else "\nall claims match the code and data")
    return 1 if FAILED else 0


def _parser():
    """Build server.py's argument parser without starting anything (reads the defaults it declares)."""
    import argparse
    holder = {}
    real = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        holder["p"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = grab
    try:
        try:
            import server
            server.main([])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real
    return holder["p"]


if __name__ == "__main__":
    sys.exit(main())
