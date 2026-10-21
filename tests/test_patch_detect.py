"""多尺度淺色區塊偵測（app/patch_detect.py）：用合成圖確認抓得到淺色、不誤抓正常皮膚與毛髮。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import patch_detect as pd  # noqa: E402


def lab_to_rgb(L, a, b):
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    inv = lambda t: t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)  # noqa: E731
    x, y, z = inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883
    M = np.array([[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]])
    lin = M @ np.array([x, y, z])
    s = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.clip(lin, 0, None) ** (1 / 2.4) - 0.055)
    return np.clip(s * 255, 0, 255)


class PatchDetectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 800×1000 的「皮膚」：正常膚色 Lab(50, 14, 14)，中間一個淺色圓（缺色素：較亮、彩度低）Lab(60, 9, 6)，
        # 左上一塊深色「眉毛」Lab(20, 3, 3)；加一點雜訊
        H, W = 1000, 800
        rng = np.random.default_rng(20261017)
        img = np.tile(lab_to_rgb(50, 14, 14), (H, W, 1))
        yy, xx = np.mgrid[:H, :W]
        cls.patch = (yy - 500) ** 2 + (xx - 400) ** 2 < 120 ** 2
        img[cls.patch] = lab_to_rgb(60, 9, 6)
        cls.brow = (yy > 150) & (yy < 190) & (xx > 150) & (xx < 350)
        img[cls.brow] = lab_to_rgb(20, 3, 3)
        cls.rgb = np.clip(img + rng.normal(0, 1.5, img.shape), 0, 255).astype(np.uint8)
        cls.region = np.ones((H, W), bool)
        cls.mask = pd.detect(cls.rgb, cls.region)

    def test_finds_pale_patch(self):
        hit = (self.mask & self.patch).sum() / self.patch.sum()
        self.assertGreater(hit, 0.8, f"淺色圓只抓到 {hit:.2f}")

    def test_normal_skin_mostly_clear(self):
        far = ~self.patch & ~self.brow
        far &= ~np.pad(self.patch, 0)                      # 圓外
        false = (self.mask & far).sum() / far.sum()
        self.assertLess(false, 0.05, f"正常皮膚誤抓 {false:.3f}")

    def test_dark_hair_not_marked(self):
        self.assertEqual(int((self.mask & self.brow).sum()), 0)

    def test_mask_png_size_and_ratio(self):
        png, ratio = pd.mask_png(self.rgb, self.region, (200, 250))
        self.assertTrue(png.startswith(b"\x89PNG"))
        from PIL import Image
        import io
        self.assertEqual(Image.open(io.BytesIO(png)).size, (200, 250))
        self.assertGreater(ratio, 0.03)
        self.assertLess(ratio, 0.2)


if __name__ == "__main__":
    unittest.main()
