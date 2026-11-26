import unittest

from test_room_review3 import REVIEW_NUDGE, RoomOnFakeEngine, chat_line, said_with
from test_room_review4 import WORK_SECONDS, hub_work

import replies
import room
import room_text
import steering

REVIEW_SECONDS = room.ROOM_TURN_SECONDS_BY_KIND[room_text.REVIEW]


def cut(text):
    return [(0, chat_line(text)), (0, chat_line("", True, done_reason="length", prompt_eval_count=10))]


class VerdictsSurviveACut(RoomOnFakeEngine):
    def test_a_review_verdict_cut_mid_reason_is_still_posted(self):
        said = said_with("(room) Review task #2: the game loop.", REVIEW_NUDGE)
        self.assertEqual(self.reply(said, cut("[approve]\nGood work overall but the"), seconds=REVIEW_SECONDS), "[approve]")

    def test_short_answers_before_a_cut_are_posted(self):
        self.assertEqual(self.reply(said_with("June: shall we add sound?"), cut("Agreed. And furthermore I th"), seconds=200),
                         "Agreed.")

    def test_bracket_answers_keep_their_finished_part(self):
        for answer, kept in (("[approve] Fine. The loop is fi", "[approve] Fine."), ("[vote yes]\nBecause the far", "[vote yes]"),
                             ("[done]\nThe document now cov", "[done]")):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), kept)

    def test_short_sentences_are_kept_and_bare_fragments_pass(self):
        self.assertEqual(room_text.finished_part("No. We should not bu"), "No.")
        self.assertEqual(room_text.finished_part("Good idea. Let us do th"), "Good idea.")
        self.assertEqual(room_text.finished_part("Sure\nthe loop is"), "")


class FileNamesEndSentences(unittest.TestCase):
    def test_file_names_and_web_addresses_end_a_sentence(self):
        for answer, kept in (("I put the loop in game.js. Next I will", "I put the loop in game.js."),
                             ("Open index.html. Then click", "Open index.html."),
                             ("See example.com. It has the do", "See example.com.")):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), kept)

    def test_dotted_short_forms_still_do_not(self):
        for answer in ("The U.S. market is big and we", "We meet at 5 a.m. and then", "She has a Ph.D. in maths and",
                       "Prof. Lee agrees and", "See No. 5 for det"):
            with self.subTest(answer=answer):
                self.assertEqual(room_text.finished_part(answer), "")


class NotesHeaderWords(RoomOnFakeEngine):
    def test_a_file_that_uses_the_header_words_is_kept(self):
        code = '[file path=ui.js]\nconst label = "Your notes for this message";\nshow(label);\n[/file]'
        self.assertEqual(self.reply(hub_work(), [(0, chat_line(code, True, done_reason="stop"))], seconds=WORK_SECONDS), code)

    def test_the_real_header_is_still_cut_anywhere(self):
        history = [{"role": "user", "content": "hi"}]
        steering.with_context(history, "It is late.")
        header = history[-1]["content"].split("\n\nhi")[0]
        answer = f"[file path=game.js]\nfunction loop() {{}}\n\n{header}\nPut it in [file path=x] [/file]."
        self.assertEqual(replies.tidy_answer(answer, strips_owner_name=False, keeps_hand_ins=True),
                         "[file path=game.js]\nfunction loop() {}")


if __name__ == "__main__":
    unittest.main()
