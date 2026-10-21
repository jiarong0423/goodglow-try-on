"""Reproduction check: how much "YouCam whole-face foundation" and "local layer-by-layer fill" each shrink the lightness gap between the lighter patch and the surrounding skin tone.

  python3 -B tools/repro_region_gap.py            # print a table; exit 1 if a number is outside the tolerance
  python3 -B tools/repro_region_gap.py --json     # output JSON

Inputs (all under demo/, public photos and their results; sha256 in demo/repro/SHA256SUMS.txt):
- demo/photos/vitiligo_20551205.jpg           original photo (Pexels)
- demo/repro/vto_foundation_dark.jpg/light.jpg  YouCam Makeup VTO whole-face foundation (coverage 80, smoothing 0), matched to the darker / lighter side
- demo/repro/local_layers.jpg                 result of the local layer-by-layer fill
- demo/repro/patch_mask.png                   lighter-patch mask (multi-scale rule-based computation, app/patch_detect.py, no model decision)
Method (all hard-coded, rerunnable):
- Lighter patch = inside the mask; surrounding skin tone = pixels in the 14–70 px ring outside the mask that are skin-colored (a*>6, 25<L*<75).
- Lightness gap = mean L* of the ring (surround) − mean L* inside the mask; compare how much the gap shrinks (%) from the original photo to each result.
- Honest note: the local layer-by-layer fill is designed to shrink exactly this gap, so this is a check that it does so, not an independent blind test;
  YouCam has no region parameter, so this only measures the actual effect of its whole-face foundation.
Failure paths print ERROR and return non-zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import patch_detect as pd  # noqa: E402

W, H = 1438, 1918
INPUTS = {
    "original": ROOT / "demo/photos/vitiligo_20551205.jpg",
    "youcam_foundation_dark": ROOT / "demo/repro/vto_foundation_dark.jpg",
    "youcam_foundation_light": ROOT / "demo/repro/vto_foundation_light.jpg",
    "local_layers": ROOT / "demo/repro/local_layers.jpg",
}
MASK = ROOT / "demo/repro/patch_mask.png"
# expected values (from rerunning this file's method on the files above) and the tolerance
EXPECTED = {"original": 7.4, "youcam_foundation_dark": 7.0, "youcam_foundation_light": 7.1, "local_layers": 4.4}
TOL = 0.4


def load(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGB").resize((W, H), Image.LANCZOS))


def measure() -> dict:
    mask = np.asarray(Image.open(MASK).convert("L").resize((W, H), Image.NEAREST)) > 127
    ring = binary_dilation(mask, iterations=70) & ~binary_dilation(mask, iterations=14)
    out = {}
    for name, path in INPUTS.items():
        lab = pd.srgb_to_lab(load(path))
        L, a = lab[..., 0], lab[..., 1]
        skin = (a > 6) & (L > 25) & (L < 75)
        r = ring & skin & ~mask
        out[name] = {"patch_L": float(L[mask].mean()), "around_L": float(L[r].mean())}
        out[name]["gap"] = out[name]["around_L"] - out[name]["patch_L"]
    base = out["original"]["gap"]
    for name in out:
        out[name]["gap_abs"] = abs(out[name]["gap"])
        out[name]["gap_shrunk_pct"] = (abs(base) - abs(out[name]["gap"])) / abs(base) * 100
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for name, p in {**INPUTS, "mask": MASK}.items():
        if not p.is_file():
            print(f"ERROR input not found: {name} {p}", file=sys.stderr)
            return 2
    res = measure()
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{'Input':26s} {'Patch L*':>8s} {'Ring L*':>8s} {'L* gap':>7s} {'Gap shrunk':>10s}")
        for n, v in res.items():
            print(f"{n:26s} {v['patch_L']:8.1f} {v['around_L']:8.1f} {v['gap_abs']:7.1f} {v['gap_shrunk_pct']:9.1f}%")
    bad = [n for n, e in EXPECTED.items() if abs(res[n]["gap_abs"] - e) > TOL]
    if bad:
        print(f"ERROR numbers outside the expected range (tolerance ±{TOL}): {bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
