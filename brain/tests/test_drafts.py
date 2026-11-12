"""Faster first words: early sentences must never be contradicted by the final answer.
Run from brain/: python3 -m unittest discover tests"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drafts import Draft, remainder  # noqa: E402

OFFER = re.compile(r"let me know|would you like", re.I).search


def never_risky(_text):
    return False


def make_draft():
    sent = []
    draft = Draft(sent.append, lambda text: text.replace(", Alex!", "!"), OFFER)
    return draft, sent


def write(draft, text, risky=never_risky, size=3):
    for start in range(0, len(text), size):
        draft.feed(text[start:start + size], risky)


class SendingEarly(unittest.TestCase):
    def test_whole_sentences_go_out_as_they_finish_and_the_rest_comes_with_the_answer(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Paris is the capital. It has about two million people. It's on the Sei")
        self.assertEqual("".join(sent), "Paris is the capital. It has about two million people.")
        answer = "Paris is the capital. It has about two million people. It's on the Seine."
        self.assertEqual(draft.finish(answer), answer)

    def test_a_colon_mid_line_is_not_the_end_of_a_sentence(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, 'The error "TypeError: x is undefined" means x has no value')
        self.assertEqual(sent, [])

    def test_offers_wait_until_something_follows_them(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Done. Let me know if you need more. ")
        self.assertEqual("".join(sent), "Done.")
        write(draft, "Also, it's raining. ")
        self.assertEqual("".join(sent), "Done. Let me know if you need more. Also, it's raining.")

    def test_sent_text_gets_the_same_clean_up_as_the_answer(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Night, Alex! Sleep well. ")
        self.assertEqual("".join(sent), "Night! Sleep well.")

    def test_a_held_round_sends_nothing(self):
        draft, sent = make_draft()
        draft.start_round(allowed=False)
        write(draft, "I've set a reminder. All done. ")
        self.assertEqual(sent, [])
        self.assertEqual(draft.finish("Reminder set."), "Reminder set.")

    def test_a_risky_round_stops_sending_where_the_risk_starts(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Good question. I've searched and found it. ", risky=lambda text: "searched" in text)
        self.assertEqual("".join(sent), "Good question.")


class TheAnswerKeepsWhatWasSent(unittest.TestCase):
    def test_whitespace_differences_dont_count(self):
        self.assertEqual(remainder("One.\nTwo.", "One. Two. Three."), " Three.")
        self.assertIsNone(remainder("One.", "Uno."))

    def test_a_replaced_answer_comes_after_what_was_already_said(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Hmm, good question. I think it's ")
        draft.start_round(allowed=True)  # sent back to use its tools
        write(draft, "It's a cat meme. ")
        self.assertEqual("".join(sent), "Hmm, good question.\n\nIt's a cat meme.")
        self.assertEqual(draft.finish("It's a cat meme. From 2019."), "Hmm, good question.\n\nIt's a cat meme. From 2019.")

    def test_text_before_a_tool_call_stays_in_front_of_the_answer(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Let me check. ")
        draft.start_round(allowed=True)
        self.assertEqual(draft.finish("It's 12°C."), "Let me check.\n\nIt's 12°C.")

    def test_a_round_that_ends_in_a_tool_call_sends_its_last_sentence(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Checking the weather now")
        draft.before_tools()
        draft.start_round(allowed=True)
        self.assertEqual("".join(sent), "Checking the weather now")

    def test_a_tool_call_begun_as_a_code_block_is_not_sent(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Let's check the service status:\n\n```json\n")
        draft.before_tools()
        self.assertEqual("".join(sent), "Let's check the service status:")

    def test_code_goes_out_only_once_its_block_is_closed(self):
        draft, sent = make_draft()
        draft.start_round(allowed=True)
        write(draft, "Run this:\n```\nls -la\n")
        self.assertEqual("".join(sent), "Run this:")
        write(draft, "```\nThat lists files. ")
        self.assertEqual("".join(sent), "Run this:\n```\nls -la\n```\nThat lists files.")

    def test_nothing_sent_leaves_the_answer_alone(self):
        draft, _ = make_draft()
        draft.start_round(allowed=True)
        write(draft, "No full stop yet")
        self.assertEqual(draft.finish("No full stop yet, but done."), "No full stop yet, but done.")


if __name__ == "__main__":
    unittest.main()
