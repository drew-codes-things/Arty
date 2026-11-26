import unittest

from test_room_review3 import CHAT_NUDGE, REVIEW_NUDGE, RoomOnFakeEngine, said_with
from test_room_review4 import WORK_SECONDS, council, hub_work
from test_room_review7 import REVIEW_SECONDS, cut

import room_text

REVIEW_SAID = said_with("(room) Review task #2: the game loop.", REVIEW_NUDGE)


class OnlyApproveSurvivesAlone(RoomOnFakeEngine):
    def test_changes_with_a_cut_reason_passes_instead_of_empty_notes(self):
        self.assertEqual(self.reply(REVIEW_SAID, cut("[changes] The loop never stops and the"), seconds=REVIEW_SECONDS),
                         room_text.PASS)

    def test_approve_on_a_review_is_still_kept(self):
        self.assertEqual(self.reply(REVIEW_SAID, cut("[approve] Good work overall but the"), seconds=REVIEW_SECONDS), "[approve]")

    def test_done_on_a_hand_in_passes(self):
        self.assertEqual(self.reply(hub_work(), cut("[done] The document now cov"), seconds=WORK_SECONDS), room_text.PASS)

    def test_a_checkbox_or_aside_in_chat_passes(self):
        for answer in ("[x] buy milk and then the", "[laughs] that is a great point and", "[approve] Good work overall but the"):
            with self.subTest(answer=answer):
                self.assertEqual(self.reply(said_with("June: what next?", CHAT_NUDGE), cut(answer), seconds=200), room_text.PASS)

    def test_a_council_vote_with_a_cut_reason_passes(self):
        self.assertEqual(self.reply(council("Bob: shall we build the farm?"), cut("[vote yes] Because the far"), seconds=200),
                         room_text.PASS)


class LeadingVerdictRule(unittest.TestCase):
    def test_only_approve_on_review_turns(self):
        cases = (("[approve] Good work overall but the", room_text.REVIEW, "[approve]"),
                 ("[done] The document now cov", room_text.WORK, ""),
                 ("[changes] The loop never stops and the", room_text.REVIEW, ""),
                 ("[vote yes] I", room_text.REVIEW, ""),
                 ("[Note] the loop is slow and", room_text.WORK, ""),
                 ("[approve] Good work overall but the", room_text.PLAN, ""),
                 ("[done] The document now cov", room_text.COUNCIL, ""),
                 ("[approve] Good work overall but the", room_text.CHAT, ""))
        for answer, kind, kept in cases:
            with self.subTest(answer=answer, kind=kind):
                self.assertEqual(room_text.finished_part(answer, kind), kept)

    def test_line_ends_still_keep_a_verdict_line(self):
        self.assertEqual(room_text.finished_part("[changes]\nThe loop never stops and the", room_text.REVIEW), "[changes]")


if __name__ == "__main__":
    unittest.main()
