import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_code_sandbox import BROKEN, FAILED, PASSED, run_coding_turn  # noqa: E402
from test_review_tonight import last_user_note, said  # noqa: E402

import code_check  # noqa: E402
import code_sandbox  # noqa: E402
import shown_output  # noqa: E402

CODE = "```python\ndef is_palindrome(text):\n    cleaned = text.replace(\" \", \"\").lower()\n    return cleaned == cleaned[::-1]\n```"
LEAKED = f"Here is the corrected code that addresses the issues you encountered:\n\n{CODE}\n\nIt ignores case and spaces."
FRESH_LEAD = "Here's the code"
REWRITE_TALK = ("corrected", "addresses the issues", "you encountered", "previous version", "error message", "The fix is")


def assert_reads_fresh(test, answer):
    for words in REWRITE_TALK:
        test.assertNotIn(words, answer)


class RewriteNotesAskForAFreshAnswer(unittest.TestCase):
    def test_every_rewrite_note_asks_for_a_first_time_answer(self):
        notes = {
            "sandbox error": code_sandbox.rewrite_note([(code_sandbox.Unit("python", "x", "", "Python"), FAILED)]),
            "wrong output": shown_output.rewrite_note([("1\n2", "1\n3")]),
            "unconfirmed call": code_check.rewrite_note("https://docs.python.org/3/", ["substitute"]),
        }
        for kind, note in notes.items():
            with self.subTest(kind=kind):
                self.assertIn("as if it were your first reply", note)
                self.assertIn("corrected, fixed or updated", note)


class RewriteTalkIsTakenOut(unittest.TestCase):
    def test_a_corrected_code_opening_becomes_a_plain_one(self):
        outcome, ollama, _ = run_coding_turn([said(BROKEN), said(LEAKED)], [FAILED, PASSED])
        self.assertIn("as if it were your first reply", last_user_note(ollama.chat_bodies[1]))
        self.assertTrue(outcome["answer"].startswith(f"{FRESH_LEAD}:\n\n{CODE}\n\nIt ignores case and spaces."), outcome["answer"])
        assert_reads_fresh(self, outcome["answer"])

    def test_sentences_about_the_error_or_the_old_version_are_dropped(self):
        leaked = ("The closure parameter needs an explicit type: `char` (the error message suggests it). "
                  f"Here's the corrected version:\n\n{CODE}\n\nTwo changes from the previous version:\n\n"
                  "The fix is to call `cleaned[::-1]`. It ignores case and spaces.")
        outcome, _, _ = run_coding_turn([said(BROKEN), said(leaked)], [FAILED, PASSED])
        self.assertTrue(outcome["answer"].startswith(f"{FRESH_LEAD}:\n\n{CODE}\n\nIt ignores case and spaces."), outcome["answer"])
        assert_reads_fresh(self, outcome["answer"])

    def test_code_blocks_are_left_alone(self):
        code = "```c\n// the previous version of this line was the corrected code\nint main(void) { return 0; }\n```"
        outcome, _, _ = run_coding_turn([said(BROKEN), said(f"Here is the fixed program:\n\n{code}")], [FAILED, PASSED])
        self.assertTrue(outcome["answer"].startswith(f"{FRESH_LEAD}:\n\n{code}"), outcome["answer"])

    def test_an_answer_that_was_never_rewritten_is_untouched(self):
        outcome, _, _ = run_coding_turn([said(LEAKED)], [PASSED])
        self.assertTrue(outcome["answer"].startswith(LEAKED))

    def test_when_they_asked_for_a_fix_the_wording_stays(self):
        question = "fix my python palindrome function, it throws AttributeError"
        outcome, _, _ = run_coding_turn([said(BROKEN), said(LEAKED)], [FAILED, PASSED], question=question)
        self.assertTrue(outcome["answer"].startswith(LEAKED))


if __name__ == "__main__":
    unittest.main()
