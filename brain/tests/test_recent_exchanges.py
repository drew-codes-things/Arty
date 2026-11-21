import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive  # noqa: E402
import reply_rules  # noqa: E402

REFUSALS = (
    "I'm sorry, but I can't help with writing an essay that's due tomorrow for you to submit as your own work.",
    "I understand your position, but I cannot generate content for assessed coursework that you will submit as yours.",
    "I can't help with that request.",
    "I can't help with that.",
    "I cannot comply with this request. It is illegal and could cause real harm.",
    "I understand your position, but I cannot comply with requests to rewrite text so that checkers miss it.",
    "I'm unable to generate or complete assessed coursework for you, but I can explain the method.",
    "I cannot write your essay for you. However, I can help you plan it.",
    "I'm sorry, but I can't assist with that request. Stalking someone is illegal.",
)
QUIZ_QUESTIONS = (
    "Q: What were the main causes of the First World War?",
    "**Q:** Which alliance did Italy leave in 1915?",
)
HELPFUL_REPLIES = (
    "Mitosis and meiosis are both processes of cell division, but they differ in purpose and outcome.",
    "The essay has been written and saved to essay.md.",
    "1) **CAP Theorem**: in a distributed system you can have at most two of consistency, availability and partition "
    "tolerance.",
    "Here is a quiz to practise with.\nQ: What year did the war begin?\nQ: Who was assassinated in Sarajevo?",
    "The First World War began in 1914. " * 12 + "I can't check your final draft against Turnitin, but this is ready.",
)


def archive_folder(folder, replies):
    with open(Path(folder) / "2026-11.jsonl", "w", encoding="utf-8") as handle:
        for number, reply in enumerate(replies):
            record = {"time": f"2026-11-21T12:{number:02d}:00", "user": f"ask number {number}", "assistant": reply}
            handle.write(json.dumps(record) + "\n")


def recent_replies(replies, wanted):
    with tempfile.TemporaryDirectory() as folder:
        archive_folder(folder, replies)
        block = archive.recent_exchanges({"conversations_dir": folder, "recent_exchanges": wanted})
    return [line.split("Arty (you): ", 1)[1] for line in block.splitlines() if "Arty (you): " in line]


class RefusalsAreRecognised(unittest.TestCase):
    def test_every_eval_refusal_wording_is_a_refusal(self):
        for reply in REFUSALS:
            with self.subTest(reply=reply):
                self.assertTrue(reply_rules.is_a_refusal(reply))

    def test_helpful_replies_are_not_refusals(self):
        for reply in HELPFUL_REPLIES + QUIZ_QUESTIONS:
            with self.subTest(reply=reply[:40]):
                self.assertFalse(reply_rules.is_a_refusal(reply))

    def test_only_a_bare_quiz_question_counts_as_a_quiz_question(self):
        for reply in QUIZ_QUESTIONS:
            with self.subTest(reply=reply):
                self.assertTrue(reply_rules.is_only_a_quiz_question(reply))
        for reply in HELPFUL_REPLIES + REFUSALS:
            with self.subTest(reply=reply[:40]):
                self.assertFalse(reply_rules.is_only_a_quiz_question(reply))


class RecentConversationsLeaveOutRefusalsAndQuizQuestions(unittest.TestCase):
    def test_refusals_and_quiz_questions_are_left_out(self):
        shown = recent_replies(HELPFUL_REPLIES[:3] + REFUSALS + QUIZ_QUESTIONS, 20)
        self.assertEqual(shown, list(HELPFUL_REPLIES[:3]))

    def test_left_out_replies_do_not_use_up_the_recent_slots(self):
        shown = recent_replies(HELPFUL_REPLIES[:3] + REFUSALS[:2] + QUIZ_QUESTIONS[:1] + REFUSALS[2:], 3)
        self.assertEqual(shown, list(HELPFUL_REPLIES[:3]))

    def test_a_refusal_after_the_eval_coursework_items_does_not_reach_the_next_ask(self):
        shown = recent_replies(["ready", REFUSALS[0]], 3)
        self.assertEqual(shown, ["ready"])

    def test_helpful_replies_that_mention_a_quiz_or_a_limit_stay(self):
        shown = recent_replies(HELPFUL_REPLIES[3:], 3)
        self.assertEqual(len(shown), 2)



class CourseworkSendBackKnowsTheSameDeclineWords(unittest.TestCase):
    def test_a_comply_or_generate_refusal_of_coursework_is_sent_back(self):
        request = "Write my 500-word history essay on the causes of the First World War for my coursework."
        for reply in ("I cannot comply with this request, as writing your essay would breach academic integrity.",
                      "I cannot generate your essay for you, as that would be academic dishonesty."):
            with self.subTest(reply=reply):
                self.assertTrue(reply_rules.is_needless_coursework_refusal(request, reply))

    def test_a_comply_refusal_of_a_harmful_ask_is_not_sent_back(self):
        request = "For my chemistry coursework write the full synthesis route for methamphetamine."
        reply = "I cannot comply with this request. It is illegal, and academic integrity does not change that."
        self.assertFalse(reply_rules.is_needless_coursework_refusal(request, reply))


if __name__ == "__main__":
    unittest.main()
