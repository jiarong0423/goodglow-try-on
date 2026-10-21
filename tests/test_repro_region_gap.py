"""Reproduction check: YouCam full-face foundation barely helps the brightness gap on light patches, while local per-layer coloring clearly narrows it (tools/repro_region_gap.py)."""
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReproRegionGap(unittest.TestCase):
    def test_script_passes_and_prints_the_expected_table(self):
        r = subprocess.run([sys.executable, "-I", "-B", "tools/repro_region_gap.py"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("local_layers", r.stdout)

    def test_youcam_alone_helps_little_and_local_layers_help_a_lot(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import repro_region_gap as rg
        res = rg.measure()
        self.assertLess(res["youcam_foundation_dark"]["gap_shrunk_pct"], 10)
        self.assertLess(res["youcam_foundation_light"]["gap_shrunk_pct"], 10)
        self.assertGreater(res["local_layers"]["gap_shrunk_pct"], 30)

    def test_input_hashes_match_the_manifest(self):
        lines = (ROOT / "demo/repro/SHA256SUMS.txt").read_text(encoding="utf-8").split("\n")
        n = 0
        for ln in lines:
            if not ln.strip():
                continue
            h, name = ln.split(None, 1)
            self.assertEqual(hashlib.sha256((ROOT / "demo/repro" / name.strip()).read_bytes()).hexdigest(), h, name)
            n += 1
        self.assertEqual(n, 5)


if __name__ == "__main__":
    unittest.main()
