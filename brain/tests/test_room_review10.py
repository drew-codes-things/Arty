import unittest

from test_room_review3 import RoomOnFakeEngine
from test_room_review4 import WORK_SECONDS, hub_work
from test_room_review7 import REVIEW_SECONDS, cut
from test_room_review9 import REVIEW_SAID

import room_text


class LoneVerdictOnlyOnReviews(RoomOnFakeEngine):
    def test_a_hand_in_with_a_lone_verdict_passes_instead_of_document_text(self):
        for answer in ("[done] The document now cov", "[approve] The loop is fi"):
            with self.subTest(answer=answer):
                self.assertEqual(self.reply(hub_work(), cut(answer), seconds=WORK_SECONDS), room_text.PASS)

    def test_a_review_with_a_lone_done_passes_instead_of_approving(self):
        for answer in ("[done] The loop never stops and the", "[DONE] The loop never stops and the"):
            with self.subTest(answer=answer):
                self.assertEqual(self.reply(REVIEW_SAID, cut(answer), seconds=REVIEW_SECONDS), room_text.PASS)

    def test_a_review_keeps_a_lone_approve_in_any_case(self):
        for answer in ("[approve] Good work overall but the", "[Approve] Good work overall but the"):
            with self.subTest(answer=answer):
                self.assertEqual(self.reply(REVIEW_SAID, cut(answer), seconds=REVIEW_SECONDS), answer.split()[0])


class LoneVerdictRule(unittest.TestCase):
    def test_done_is_never_kept_alone(self):
        for kind in (room_text.CHAT, room_text.COUNCIL, room_text.PLAN, room_text.WORK, room_text.REVIEW):
            with self.subTest(kind=kind):
                self.assertEqual(room_text.finished_part("[done] The document now cov", kind), "")

    def test_approve_is_kept_alone_only_on_reviews(self):
        for kind in (room_text.CHAT, room_text.COUNCIL, room_text.PLAN, room_text.WORK):
            with self.subTest(kind=kind):
                self.assertEqual(room_text.finished_part("[approve] Good work overall but the", kind), "")
        self.assertEqual(room_text.finished_part("[approve] Good work overall but the", room_text.REVIEW), "[approve]")


if __name__ == "__main__":
    unittest.main()
