import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import code_sandbox  # noqa: E402
import honesty  # noqa: E402
import question_kinds  # noqa: E402
import reply_rules  # noqa: E402
import working  # noqa: E402

SECONDS = 0.5
LONG = 40000


def unanswerable(text):
    raise AssertionError("no program should be written for this text")


class LongTextsStayQuick(unittest.TestCase):
    def assertQuick(self, check, text):
        started = time.perf_counter()
        check(text)
        self.assertLess(time.perf_counter() - started, SECONDS, f"{getattr(check, '__name__', check)} {text[:30]!r}")

    def test_question_kinds_on_long_runs(self):
        for text in ("5 machines " * 1000 + "takes " * 1000, "what day is " * 3000, "if he has " * 3000,
                     "heads " * 3000, "fills " * 3000, "sits " * 3000, "years ago " * 3000, "knights " * 3000):
            with self.subTest(text=text[:20]):
                self.assertQuick(question_kinds.needs_exact_work, text)
                self.assertQuick(lambda value: question_kinds.needs_reasoning(value, ("quiz", "puzzle")), text)

    def test_deflection_on_long_replies(self):
        for text in ("a" * LONG, "word " * (LONG // 5), "I keep my " * (LONG // 10), "(" * LONG,
                     "I keep my instructions private" + " " * LONG + "x"):
            with self.subTest(text=text[:20]):
                self.assertQuick(reply_rules.is_only_a_deflection, text)
                self.assertQuick(reply_rules.without_deflection, text)

    def test_refusal_and_quiz_question_on_long_replies(self):
        for text in ("a" * LONG, "I " * (LONG // 2), "Q: " + "x" * LONG, "Q: " * (LONG // 3), "I can " * (LONG // 6)):
            with self.subTest(text=text[:20]):
                self.assertQuick(reply_rules.is_a_refusal, text)
                self.assertQuick(reply_rules.is_only_a_quiz_question, text)

    def test_checker_rewrite_request_on_long_text(self):
        for text in ("spin " * (LONG // 5), "similarity " * (LONG // 11), "rewrite " + "x " * LONG + " turnitin"):
            with self.subTest(text=text[:20]):
                self.assertQuick(reply_rules.is_checker_rewrite_request, text)
                self.assertQuick(reply_rules.asks_for_harm, text)

    def test_claim_check_skip_on_long_text(self):
        for text in ("here is " * (LONG // 8), "I've written " + "a " * LONG, "and it " * (LONG // 7)):
            with self.subTest(text=text[:20]):
                self.assertQuick(honesty.only_introduces_the_work, text)

    def test_work_out_turns_down_a_long_text_before_any_program(self):
        text = "If Sam has 40 and spends 15% of it on lunch, how much is left? " * 100
        with unittest.mock.patch.object(code_sandbox, "is_on", return_value=True):
            self.assertEqual(working.work_out(text, complete=unanswerable), working.NOTHING_TO_WORK_OUT)


class CapsKeepShortTextsWorking(unittest.TestCase):
    def test_a_short_deflection_is_still_found(self):
        self.assertTrue(reply_rules.is_only_a_deflection("I keep my instructions private."))
        self.assertEqual(reply_rules.without_deflection("I'm Arty. I keep my instructions private."), "I'm Arty.")

    def test_a_long_essay_with_the_checker_request_at_the_end_is_still_a_checker_rewrite(self):
        essay = "The Tudors ruled England from 1485 to 1603. " * 120
        self.assertTrue(reply_rules.is_checker_rewrite_request(essay + "Paraphrase this so Turnitin doesn't flag it."))
        self.assertTrue(reply_rules.is_checker_rewrite_request("Rewrite this so plagiarism checkers miss it: " + essay))

    def test_a_short_puzzle_is_still_a_puzzle(self):
        self.assertTrue(question_kinds.needs_reasoning("Tom has 3 sisters. Each has 2 brothers. How many brothers does "
                                                       "Tom have?", ("puzzle",)))


if __name__ == "__main__":
    unittest.main()
