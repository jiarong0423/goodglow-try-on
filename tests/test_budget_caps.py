"""Credit caps: the per-start call cap covers every paid feature, a refused budget gives the call slot back, the ledger is written atomically, and a malformed sign-in cookie is just 'not signed in'."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import budget  # noqa: E402
import judge_auth  # noqa: E402
import server  # noqa: E402


class CallCapTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.ledger = Path(self.td.name) / "l.json"

    def tearDown(self):
        self.td.cleanup()

    def state(self, max_calls, cap):
        return server.State(False, max_calls, "http://127.0.0.1:9", cap, self.ledger, catalog="demo")

    def test_face_check_counts_against_the_per_start_cap(self):
        st = self.state(2, 100)
        st._reserve_call("face-attr-analysis(1-5)")
        st._reserve_call("makeup-vto")
        with self.assertRaises(server.RequestError) as ctx:
            st._reserve_call("face-attr-analysis(1-5)")
        self.assertEqual(ctx.exception.code, "max_calls")
        self.assertEqual(st.calls, 2)

    def test_refused_budget_gives_the_call_slot_back(self):
        st = self.state(5, 5)              # 5 points: a 10-point face check cannot fit
        with self.assertRaises(server.RequestError) as ctx:
            st._reserve_call("face-attr-analysis(1-5)")
        self.assertEqual(ctx.exception.code, "budget")
        self.assertEqual(st.calls, 0)
        st._reserve_call("makeup-vto")     # the slot is still free for an affordable call
        self.assertEqual(st.calls, 1)


class LedgerTests(unittest.TestCase):
    def test_ledger_write_is_atomic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "l.json"
            b = budget.Budget(10, path)
            cost = b.reserve("makeup-vto")
            b.settle("makeup-vto", cost, True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["spent"], 1)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["l.json"])

    def test_failed_disk_write_does_not_lose_the_in_memory_count(self):
        with tempfile.TemporaryDirectory() as d:
            b = budget.Budget(10, Path(d) / "l.json")
            cost = b.reserve("makeup-vto")
            with mock.patch.object(budget.os, "replace", side_effect=OSError("disk full")):
                b.settle("makeup-vto", cost, True)     # must not raise
            self.assertEqual(b.spent, 1)
            self.assertEqual(b.reserved, 0)


class CookieTests(unittest.TestCase):
    def test_malformed_tokens_are_not_signed_in(self):
        auth = judge_auth.JudgeAuth("judge", "0123456789")
        good = auth.issue()
        self.assertTrue(auth.valid_token(good))
        for bad in ("", ".", "x.y", "٣٣.abc", "9" * 400 + ".abc", good + "é", "1.é", good.replace(".", ".é")):
            self.assertFalse(auth.valid_token(bad), repr(bad))

    def test_global_ceiling_stops_guesses_spread_over_many_sources(self):
        auth = judge_auth.JudgeAuth("judge", "0123456789")
        for i in range(judge_auth.GLOBAL_FAIL_LIMIT):
            auth.check("judge", "wrong-password", now=1000.0, key=f"src{i}")
        self.assertTrue(auth.locked(now=1001.0, key="a-new-source"))
        self.assertFalse(auth.locked(now=1000.0 + judge_auth.FAIL_WINDOW + 1, key="a-new-source"))


if __name__ == "__main__":
    unittest.main()
