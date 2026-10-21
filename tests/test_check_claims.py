"""The documentation's numbers must match what the code and data produce (tools/check_claims.py exits 1 otherwise)."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ClaimsTests(unittest.TestCase):
    def test_every_documented_number_matches(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "check_claims.py")], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("all claims match", r.stdout)


if __name__ == "__main__":
    unittest.main()
