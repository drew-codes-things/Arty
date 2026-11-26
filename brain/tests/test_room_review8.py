import unittest

from test_room_review3 import REVIEW_NUDGE, RoomOnFakeEngine, said_with
from test_room_review7 import REVIEW_SECONDS, cut

import room_text


class TwoLetterFileNamesEndSentences(RoomOnFakeEngine):
    def test_a_two_letter_file_name_ends_the_sentence_in_a_posted_reply(self):
        self.assertEqual(self.reply(said_with("June: where does the game start?"), cut("The entry point is ui.js. Then we lo"),
                                    seconds=200), "The entry point is ui.js.")

    def test_two_letter_file_names_after_a_folder_end_a_sentence(self):
        for answer, kept in (("Styles live in src/db.py. After th", "Styles live in src/db.py."),
                             ("The tests are in io.rs. Then we ru", "The tests are in io.rs.")):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), kept)

    def test_single_letter_dotted_short_forms_and_ph_d_do_not(self):
        for answer in ("The U.S. market is big and we", "We meet at 5 a.m. and then", "She has a Ph.D. in physics and"):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), "")
        self.assertEqual(room_text.finished_part("Use e.g. a grid. Then pl"), "Use e.g. a grid.")


class CompanyAndStreetShortForms(RoomOnFakeEngine):
    def test_saint_before_a_name_is_not_posted_as_a_fragment(self):
        self.assertEqual(self.reply(said_with("June: when shall we meet?"), cut("Meet on St. Patrick's day and"), seconds=200),
                         room_text.PASS)

    def test_company_short_forms_do_not_end_a_sentence(self):
        for answer in ("Acme Inc. makes the en", "Turn at Main St. Then go le", "Bolt Ltd. sells the pa",
                       "Smith and Co. built the br"):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), "")

    def test_an_ordinal_before_a_full_stop_still_ends_a_sentence(self):
        self.assertEqual(room_text.finished_part("Go to the 1st. Then the 2nd st"), "Go to the 1st.")


class VerdictOnTheSameLine(RoomOnFakeEngine):
    def test_a_verdict_followed_by_one_cut_sentence_keeps_the_verdict(self):
        said = said_with("(room) Review task #2: the game loop.", REVIEW_NUDGE)
        self.assertEqual(self.reply(said, cut("[approve] Good work overall but the"), seconds=REVIEW_SECONDS), "[approve]")

    def test_a_vote_with_a_cut_reason_passes(self):
        self.assertEqual(room_text.finished_part("[vote yes] Because the far", room_text.COUNCIL), "")

    def test_links_and_open_blocks_are_not_verdicts(self):
        for answer in ("[see here](https://example.com) and th", "[file path=a.js] let x", "Sure [approve] and the"):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), "")


if __name__ == "__main__":
    unittest.main()
