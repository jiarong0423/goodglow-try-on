import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import care_rules_albinism as albinism_rules  # noqa: E402
import care_rules_vitiligo as vitiligo_rules  # noqa: E402
import color_engine as ce  # noqa: E402


class CareRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handoff = ROOT / "tests" / "fixtures"
        cls.skin = json.loads((handoff / "skin_vitiligo_20551205.json").read_text(encoding="utf-8"))
        cls.expected = json.loads((handoff / "verify_expected.json").read_text(encoding="utf-8"))

    def test_both_entry_modules_import_independently(self):
        self.assertTrue(callable(albinism_rules.personalize))
        self.assertTrue(callable(vitiligo_rules.personalize))

    def test_albinism_with_skin_is_unchanged(self):
        baseline = ce.care_plan("albinism")
        self.assertEqual(ce.care_plan("albinism", skin=self.skin), baseline)
        steps = [(item["text"], item["why"]) for item in baseline]
        self.assertIs(albinism_rules.personalize(steps, self.skin), steps)

    def test_vitiligo_dispatch_matches_approved_steps(self):
        self.assertEqual(
            ce.care_plan("vitiligo", skin=self.skin),
            self.expected["vitiligo_personalized_steps"],
        )


if __name__ == "__main__":
    unittest.main()
