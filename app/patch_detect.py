"""多尺度淺色區塊偵測（伺服器端，本機計算，不呼叫 YouCam、不扣點）。


「網頁打開是連到電腦做運算」→ 網頁把照片（或上完妝的模擬圖）和上妝範圍送到 /api/patch-mask，這裡算出遮罩傳回。

做法（隔離區原型 <本機紀錄> 的加速版）：
- 同一張照片看三種尺度：細（幾個像素的淺點）、中（一般淺色區塊與交界）、粗（整片）。
- 每個像素和「同一尺度、排除候選後的周圍皮膚」比：分數＝(亮度差 ÷ 4)＋(彩度差 ÷ 3)，超過門檻、且比周圍亮才算。
- 排除：深色毛髮（L* < 35）、太小的雜點、皮膚反光（比附近亮很多、彩度很低的小亮點）。
- 加速：每個尺度在「剛好夠用」的縮小解析度上算；周圍皮膚的平均（很平滑）再縮更小算，放大回來。
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, gaussian_filter, label

# 名稱: (取值模糊 σ, 周圍參考 σ, 門檻, 最小面積 px²)，以原尺寸 2284 寬（臉寬約 1250 px）為準；k 依實際寬度縮放
SCALES = {
    "細": (1.5, 25, 1.4, 40),
    "中": (5.0, 70, 1.0, 400),
    "粗": (14.0, 180, 1.0, 4000),
}
REF_WIDTH = 2284
WORK_SIGMA = 1.6          # 每個尺度縮到「取值模糊約 1.6 px」的解析度
REF_SIGMA = 3.0           # 周圍參考縮到「σ 約 3 px」的解析度


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = lin @ M.T / np.array([0.95047, 1, 1.08883], np.float32)
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _resize(a: np.ndarray, size: tuple[int, int], resample) -> np.ndarray:
    return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize(size, resample), np.float32)


def _nblur_at(v: np.ndarray, known: np.ndarray, sigma: float) -> np.ndarray:
    """只用已知像素的模糊（不讓頭髮、範圍外混進來）；在縮小解析度上算再放大回原大小。"""
    h, w = v.shape
    f = min(1.0, REF_SIGMA / max(sigma, 1e-3))
    sw, sh = max(4, round(w * f)), max(4, round(h * f))
    k = known.astype(np.float32)
    num = _resize(v * k, (sw, sh), Image.BOX)
    den = _resize(k, (sw, sh), Image.BOX)
    s = sigma * sw / w
    num, den = gaussian_filter(num, s), gaussian_filter(den, s)
    out = num / np.maximum(den, 1e-4)
    return _resize(out, (w, h), Image.BILINEAR) if (sw, sh) != (w, h) else out


def _scale_detect(lab: np.ndarray, known0: np.ndarray, s: float, R: float, th: float, amin: float) -> np.ndarray:
    H, W = known0.shape
    f = min(1.0, WORK_SIGMA / s)
    w, h = max(8, round(W * f)), max(8, round(H * f))
    kn = _resize(known0.astype(np.float32), (w, h), Image.BOX) > 0.5
    ch = [_resize(lab[..., c], (w, h), Image.BOX) for c in range(3)]
    sl = s * w / W
    L, A, B = (gaussian_filter(c * kn, sl) / np.maximum(gaussian_filter(kn.astype(np.float32), sl), 1e-4) for c in ch)
    C = np.hypot(A, B)
    Rl = R * w / W
    known = kn.copy()
    for _ in range(2):                                         # 第二輪扣掉候選再估周圍
        refL, refC = _nblur_at(L, known, Rl), _nblur_at(C, known, Rl)
        dl = L - refL
        score = dl / 4 + (refC - C) / 3
        known = kn & ~(score > th)
    cand = kn & (score > th) & (dl > 1.0)
    shine = cand & (dl > 12) & (C < 0.35 * refC)
    lab_c, _ = label(cand & ~shine)
    sizes = np.bincount(lab_c.ravel())
    keep = sizes >= max(2, amin * (w / W) ** 2)
    keep[0] = False
    m = keep[lab_c].astype(np.float32)
    return _resize(m, (W, H), Image.BILINEAR) > 0.5 if (w, h) != (W, H) else m > 0.5


def shine_map(lab: np.ndarray, known: np.ndarray, k: float) -> np.ndarray:
    """皮膚反光（油光小亮點）：細尺度比附近亮 10 以上、彩度不到附近 60%，往外擴一點。反光是光線，不是缺色素。"""
    s = max(0.6, 1.5 * k)
    L = gaussian_filter(lab[..., 0], s)
    C = np.hypot(gaussian_filter(lab[..., 1], s), gaussian_filter(lab[..., 2], s))
    refL, refC = _nblur_at(L, known, 25 * k), _nblur_at(C, known, 25 * k)
    m = known & (L - refL > 10) & (C < 0.6 * refC)
    return binary_dilation(m, iterations=max(1, int(round(4 * k))))


def detect(rgb: np.ndarray, region: np.ndarray) -> np.ndarray:
    """rgb：H×W×3（uint8）；region：H×W 布林（上妝範圍：臉＋脖子，已扣眼眉唇）。回傳 H×W 布林遮罩。"""
    lab = srgb_to_lab(rgb)
    k = rgb.shape[1] / REF_WIDTH
    known0 = region & (lab[..., 0] >= 35)
    union = np.zeros(region.shape, bool)
    for s, R, th, amin in SCALES.values():
        union |= _scale_detect(lab, known0, max(0.6, s * k), R * k, th, amin * k * k)
    return union & ~shine_map(lab, known0, k)


def mask_png(rgb: np.ndarray, region: np.ndarray, out_size: tuple[int, int]) -> tuple[bytes, float]:
    """偵測後縮到網頁工作圖大小（面積平均＝柔邊），回傳 PNG 位元組與遮罩佔範圍比例。"""
    import io
    m = detect(rgb, region)
    ratio = float(m.sum() / max(1, region.sum()))
    img = Image.fromarray((m * 255).astype(np.uint8)).resize(out_size, Image.BOX)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), ratio
