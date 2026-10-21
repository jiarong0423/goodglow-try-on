"""重現檢查：「YouCam 整臉粉底」和「本機逐層填色」對淺色區塊與周圍膚色的亮度差各縮小多少。

  python3 -B tools/repro_region_gap.py            # 印表；數字不在容許範圍就 exit 1
  python3 -B tools/repro_region_gap.py --json     # 輸出 JSON

輸入（都在 demo/，公開照片與其結果；sha256 見 demo/repro/SHA256SUMS.txt）：
- demo/photos/vitiligo_20551205.jpg           原照（Pexels）
- demo/repro/vto_foundation_dark.jpg／light.jpg  YouCam Makeup VTO 全臉粉底（遮蓋力 80、磨皮 0），配深色／淺色那一側
- demo/repro/local_layers.jpg                 本機逐層填色的結果
- demo/repro/patch_mask.png                   淺色區塊遮罩（多尺度規則運算，app/patch_detect.py，沒有模型判定）
量法（全部寫死、可重跑）：
- 淺色區塊＝遮罩內；周圍膚色＝遮罩外 14–70 px 的環帶中、屬於皮膚色的像素（a*>6、25<L*<75）。
- 亮度差＝環帶平均 L*（周圍）− 遮罩內平均 L*；比較原照與各結果的亮度差縮小多少（%）。
- 誠實註記：本機逐層填色就是朝縮小這個差設計的，所以這是「確認它有做到」，不是獨立盲測；
  YouCam 沒有區域參數，這裡只是量它整臉粉底的實際效果。
失敗路徑印 ERROR 並回非零。
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
# 預期值（用本檔量法對上面檔案重跑的結果）與容許誤差
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
            print(f"ERROR 找不到輸入：{name} {p}", file=sys.stderr)
            return 2
    res = measure()
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{'輸入':26s} {'區塊 L*':>8s} {'周圍 L*':>8s} {'亮度差':>7s} {'比原照縮小':>10s}")
        for n, v in res.items():
            print(f"{n:26s} {v['patch_L']:8.1f} {v['around_L']:8.1f} {v['gap_abs']:7.1f} {v['gap_shrunk_pct']:9.1f}%")
    bad = [n for n, e in EXPECTED.items() if abs(res[n]["gap_abs"] - e) > TOL]
    if bad:
        print(f"ERROR 數字超出預期（容許 ±{TOL}）：{bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
