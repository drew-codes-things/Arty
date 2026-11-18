import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reply_rules import trim_offers  # noqa: E402

NUMBERED_LIST = ("What I can do for you:\n1. Look things up on the web.\n2. Keep notes and lists.\n"
                 "3. Set reminders and timers.")
BULLETED_LIST = "Packed so far:\n- Tent.\n- Stove.\n- Sleeping bag."
OFFER = "Let me know if you need anything else."


class TrailingOffers(unittest.TestCase):
    def test_a_list_in_the_last_paragraph_keeps_its_lines(self):
        for listed in (NUMBERED_LIST, BULLETED_LIST):
            with self.subTest(listed=listed.splitlines()[0]):
                self.assertEqual(trim_offers(listed), listed)

    def test_an_offer_after_a_list_goes_and_the_list_keeps_its_lines(self):
        for gap in ("\n", " "):
            with self.subTest(gap=repr(gap)):
                self.assertEqual(trim_offers(f"{NUMBERED_LIST}{gap}{OFFER}"), NUMBERED_LIST)

    def test_offers_ending_a_paragraph_of_sentences_still_go(self):
        self.assertEqual(trim_offers(f"It's 14 degrees and dry. Would you like the week ahead? {OFFER}"),
                         "It's 14 degrees and dry.")

    def test_an_offer_earlier_in_the_paragraph_stays(self):
        answer = "It's ready. Would you like tea? I made a pot anyway, it's on the table."
        self.assertEqual(trim_offers(answer), answer)

    def test_an_answer_that_is_only_an_offer_is_left_alone(self):
        self.assertEqual(trim_offers(f"  {OFFER}  "), OFFER)

    def test_a_last_paragraph_of_offers_goes_whole(self):
        self.assertEqual(trim_offers(f"{BULLETED_LIST}\n\n{OFFER} Would you like more?"), BULLETED_LIST)


if __name__ == "__main__":
    unittest.main()
