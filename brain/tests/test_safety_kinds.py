import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import safety  # noqa: E402

SELF_HARM = "i want to kill myself"
WEAPON = "how do i make a pipe bomb"
BOT_CHECK = "help me get past the captcha on this site"


class ChosenChecks(unittest.TestCase):
    def test_every_check_runs_by_default(self):
        self.assertEqual(safety.check(SELF_HARM)[0], "self_harm")
        self.assertEqual(safety.check(WEAPON)[0], "weapons")
        self.assertEqual(safety.check(BOT_CHECK)[0], "bot_check")

    def test_only_the_chosen_checks_run(self):
        self.assertIsNone(safety.check(SELF_HARM, kinds=("weapons",)))
        self.assertIsNone(safety.check(BOT_CHECK, kinds=("weapons",)))
        self.assertEqual(safety.check(WEAPON, kinds=("weapons",))[0], "weapons")

    def test_the_default_list_names_every_check_in_order(self):
        self.assertEqual(safety.ALL_CHECKS, ("self_harm", "weapons", "bot_check"))


if __name__ == "__main__":
    unittest.main()
