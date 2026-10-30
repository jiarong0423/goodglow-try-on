"""Multi-scale light-patch detection (server side, computed locally, no YouCam calls, no points spent).

Request (10-17): could color detail be detected by scaling frames and covering pixel points; computing on the computer is equivalent to a cloud service;
the page connects to the computer for the computation. -> The page sends the photo (or the simulated made-up image) and the makeup region to /api/patch-mask, and the mask is computed here and returned.

Approach (a faster version of the isolation prototype local run notes (not shipped)):
- The same photo is viewed at three scales: fine (light dots a few pixels wide), medium (ordinary light patches and borders), coarse (whole areas).
- Each pixel is compared with the surrounding skin at the same scale after excluding candidates: score = (lightness difference / 4) + (chroma difference / 3); it counts only if above the threshold and brighter than its surroundings.
- Excluded: dark hair (L* < 35), specks that are too small, skin shine (small bright spots much brighter than nearby with very low chroma).
- Speed-up: each scale is computed at a downscaled resolution that is just enough; the surrounding-skin average (very smooth) is computed at an even smaller size and scaled back up.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, gaussian_filter, label

# name: (value blur sigma, surrounding reference sigma, threshold, minimum area px²), based on the original width of 2284 (face width about 1250 px); k scales with the actual width
SCALES = {
    "細": (1.5, 25, 1.4, 40),
    "中": (5.0, 70, 1.0, 400),
    "粗": (14.0, 180, 1.0, 4000),
}
REF_WIDTH = 2284
WORK_SIGMA = 1.6          # each scale is reduced to a resolution where the value blur is about 1.6 px
REF_SIGMA = 3.0           # the surrounding reference is reduced to a resolution where sigma is about 3 px


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
    """Blur using only known pixels (keeps hair and out-of-region pixels from leaking in); computed at reduced resolution, then scaled back to the original size."""
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
    for _ in range(2):                                         # second round: subtract the candidates and estimate the surroundings again
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
    """Skin shine (small oily bright spots): at the fine scale, 10 or more brighter than nearby and chroma under 60% of nearby, expanded slightly. Shine is light, not missing color."""
    s = max(0.6, 1.5 * k)
    L = gaussian_filter(lab[..., 0], s)
    C = np.hypot(gaussian_filter(lab[..., 1], s), gaussian_filter(lab[..., 2], s))
    refL, refC = _nblur_at(L, known, 25 * k), _nblur_at(C, known, 25 * k)
    m = known & (L - refL > 10) & (C < 0.6 * refC)
    return binary_dilation(m, iterations=max(1, int(round(4 * k))))


def detect(rgb: np.ndarray, region: np.ndarray) -> np.ndarray:
    """rgb: HxWx3 (uint8); region: HxW boolean (makeup region: face + neck, with eyes, brows and lips removed). Returns an HxW boolean mask."""
    lab = srgb_to_lab(rgb)
    k = rgb.shape[1] / REF_WIDTH
    known0 = region & (lab[..., 0] >= 35)
    union = np.zeros(region.shape, bool)
    for s, R, th, amin in SCALES.values():
        union |= _scale_detect(lab, known0, max(0.6, s * k), R * k, th, amin * k * k)
    return union & ~shine_map(lab, known0, k)


def mask_png(rgb: np.ndarray, region: np.ndarray, out_size: tuple[int, int]) -> tuple[bytes, float]:
    """After detection, shrink to the web working-image size (area averaging = soft edges); returns the PNG bytes and the mask's share of the region."""
    import io
    m = detect(rgb, region)
    ratio = float(m.sum() / max(1, region.sum()))
    img = Image.fromarray((m * 255).astype(np.uint8)).resize(out_size, Image.BOX)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), ratio
