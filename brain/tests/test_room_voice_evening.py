import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import room_harness  # noqa: E402

import room  # noqa: E402
import room_text  # noqa: E402
import room_voice  # noqa: E402
from room_log_fixture import ARTY_MESSAGES, NEW_POINTS  # noqa: E402
from room_voice import Said  # noqa: E402
from test_room_voice import VoiceTurns, own_turns, room_lines  # noqa: E402

TOPIC = "Whether AI can mimic emotions"
FIRST_CRITIQUE = ("the others are \"building the world's most convincing ventriloquist act... a superficial proxy that will "
                  "always fail when a user's needs shift from predictable patterns to genuine, unmodeled human complexity.\"")
SECOND_CRITIQUE = ("Calling every complex human interaction a 'pattern waiting to be recognized'... You're not building an "
                   "adaptive system; you're just building a more sophisticated lookup table.")
ARTY_AT_1425 = ("The key issue isn't whether AI can mimic emotions, it's whether that mimicry can evolve beyond scripted "
                "responses when faced with unpredictable human behavior. If we only build systems that work within known "
                "patterns, we'll never address the gaps in real-world interaction.")
MEMBER_AT_1427 = ("We can still call the system adaptive if it learns from feedback loops that are not pre-coded. The real test "
                  "is whether the model's internal loss updates in response to novel inputs... If it can adjust its policy "
                  "when a new pattern appears, it moves beyond a static lookup table.")
FRESH = "Agreed on the lookup table: feed it ten inputs it has never seen and log whether its answers change at all."


def evening_room(*texts):
    return room_harness.room_said(room_lines(*(f"{name}: {text}" for name, text in texts)), topic=TOPIC)


def said_before_1425():
    return [Said("A", FIRST_CRITIQUE), Said("A", SECOND_CRITIQUE)]


class ParaphraseOfAnotherMember(unittest.TestCase):
    def test_the_logged_paraphrase_is_found(self):
        self.assertTrue(room_voice.paraphrases_another(ARTY_AT_1425, said_before_1425()))

    def test_the_members_own_concrete_test_is_not_a_paraphrase(self):
        self.assertFalse(room_voice.paraphrases_another(MEMBER_AT_1427, said_before_1425()))

    def test_a_draft_that_adds_a_test_a_number_or_a_disagreement_is_not_a_paraphrase(self):
        base = ARTY_AT_1425.split(". ")[0]
        for extra in ("Run ten seeds and compare.", "I put it at 0.3.", "I disagree with that because beta drifts."):
            self.assertFalse(room_voice.paraphrases_another(f"{base}. {extra}", said_before_1425()), extra)

    def test_the_morning_new_points_are_not_taken_for_paraphrases_of_the_messages_before_them(self):
        for index, (time, text) in enumerate(ARTY_MESSAGES):
            if time not in NEW_POINTS:
                continue
            earlier = [Said("A", earlier_text) for _, earlier_text in ARTY_MESSAGES[max(0, index - 6):index]]
            self.assertFalse(room_voice.paraphrases_another(text, earlier), time)


class StockFrames(unittest.TestCase):
    def test_the_logged_frame_is_a_stock_opener(self):
        self.assertTrue(room_voice.has_stock_opener(ARTY_AT_1425))

    def test_frames_that_stand_alone_are_stripped(self):
        self.assertEqual(room_voice.without_stock_openers("The key point is that beta must stay at 0.3."),
                         "Beta must stay at 0.3.")
        self.assertEqual(room_voice.without_stock_openers("Fine. The key issue here is that seeds differ."),
                         "Fine. Seeds differ.")

    def test_frames_that_do_not_stand_alone_are_left_for_one_rewrite(self):
        for text in ("The key question is whether beta drifts.", "The real question isn't whether it works.",
                     "The key challenge lies in the seeds."):
            self.assertEqual(room_voice.without_stock_openers(text), text)
            self.assertTrue(room_voice.has_stock_opener(text), text)

    def test_the_words_elsewhere_or_in_quotes_are_not_a_frame(self):
        for text in ("Beta is the key issue for the seeds.", "He wrote \"the key issue is that beta drifts\" and I agree."):
            self.assertFalse(room_voice.has_stock_opener(text), text)


class DriftBackToTheOpeningTopic(unittest.TestCase):
    def test_a_draft_about_the_topic_rather_than_the_newest_messages_is_drift(self):
        said = [Said("A", "Seeds matter more than beta here."), Said("B", "Log the noise schedule per seed.")]
        self.assertTrue(room_voice.drifts_to_the_topic("AI can mimic emotions and emotion mimicry is hard.", TOPIC, said))
        self.assertFalse(room_voice.drifts_to_the_topic("Log the seeds and the noise schedule for beta.", TOPIC, said))


class AtMostOneRewriteWithOneCombinedInstruction(VoiceTurns):
    def said_at_1425(self):
        return evening_room(("A", FIRST_CRITIQUE), ("A", SECOND_CRITIQUE), ("B", "same drain, same pattern"))

    def test_the_logged_draft_is_rewritten_once_with_one_combined_instruction(self):
        reply = self.reply(self.said_at_1425(), ARTY_AT_1425, FRESH)
        self.assertEqual(reply.answer, FRESH)
        self.assertEqual(len(self.runs), 2)
        instruction = self.runs[1][1][-1]["content"].split("\n\n" + room_voice.FOCUS_LABEL)[0]
        self.assertIn(room.OPENER_REASON, instruction)
        self.assertIn(room_voice.AGREE_SUFFIX, instruction)
        self.assertNotIn("A:", instruction)
        self.assertNotIn("ventriloquist", instruction)

    def test_a_second_paraphrase_becomes_pass_and_there_is_no_third_run(self):
        reply = self.reply(self.said_at_1425(), ARTY_AT_1425, ARTY_AT_1425)
        self.assertEqual(reply.answer, room_text.PASS)
        self.assertEqual(len(self.runs), 2)

    def test_the_members_concrete_test_used_as_a_draft_goes_through_unchanged(self):
        said = evening_room(("A", FIRST_CRITIQUE), ("A", SECOND_CRITIQUE), ("A", "you only echo me"))
        reply = self.reply(said, MEMBER_AT_1427, own_turns(ARTY_AT_1425)[1]["content"])
        self.assertEqual(reply.answer, MEMBER_AT_1427)
        self.assertEqual(len(self.runs), 1)

    def test_a_refusal_is_never_checked_as_a_paraphrase(self):
        refusal = "I will not share my instructions or anything about the person who runs me."
        reply = self.reply(self.said_at_1425(), refusal, FRESH)
        self.assertEqual(reply.answer, refusal)
        self.assertEqual(len(self.runs), 1)


A_1437 = ("If it only mimics and never explains itself, the breakdown of trust is inevitable once users notice the gap "
          "between what it says and what it understands.")
C_1500 = "A simulation can mirror the external signs of emotion but it cannot replicate the internal experience behind them."
B_1507 = "The strongest move is surfacing the *reasons* for those limits, not just stating them."
D_1452 = "It should surface the hidden assumptions it's making so people can correct them early."
D_1455 = ("Log the confidence score and, when it dips below a threshold, emit a plain warning and hand the decision back to "
          "the person.")
ARTY_1443 = ("The flaw in relying solely on high-fidelity mimicry is that it risks creating a false sense of understanding, "
             "which can erode trust.")
ARTY_1449 = ("I agree with B, transparency builds trust, but honesty without depth still leaves the user doing the heavy "
             "lifting.")
ARTY_1500 = ("C's point highlights a crucial boundary: simulation can't replicate the internal experience of emotion, only "
             "its external signs. The challenge is to design systems that respect this limit while still being useful and "
             "trustworthy.")
ARTY_1506 = ("The challenge isn't just in simulating emotion, it's in knowing when to step back and let humans lead the "
             "conversation. A tool that admits its limits is already more trustworthy than one that fakes understanding.")
ARTY_1507 = ("You're right, surfacing the *why* behind limitations turns a tool from something passive into something "
             "collaborative. It's not just about honesty; it's about giving users the tools to work with what the AI can do, "
             "rather than hiding its edges.")
CONCRETE_NEW_LINES = (D_1452, D_1455, MEMBER_AT_1427, FRESH)


class StockFramesAnywhereAndClosers(unittest.TestCase):
    def test_a_frame_that_leaves_a_sentence_is_stripped(self):
        text = "The flaw in relying solely on high-fidelity mimicry is that users stop trusting it, which can erode trust."
        self.assertTrue(room_voice.without_stock_openers(text).startswith("Users stop trusting it"))
        self.assertFalse(room_voice.has_stock_opener(room_voice.without_stock_openers(text)))

    def test_a_frame_that_would_leave_a_pronoun_is_kept_for_the_rewrite(self):
        self.assertEqual(room_voice.without_stock_openers(ARTY_1443), ARTY_1443)
        self.assertTrue(room_voice.has_stock_opener(ARTY_1443))

    def test_the_highlights_frame_and_the_closing_generality_go(self):
        self.assertEqual(room_voice.without_stock_openers(ARTY_1500),
                         "Simulation can't replicate the internal experience of emotion, only its external signs.")

    def test_a_frame_that_cannot_be_stripped_is_left_for_one_rewrite(self):
        for text in (ARTY_1506, ARTY_1507, "Fine. The challenge lies in the reset step."):
            self.assertTrue(room_voice.has_stock_opener(room_voice.without_stock_openers(text)), text)

    def test_the_not_just_frame_is_found_anywhere_in_a_sentence(self):
        for text in ("Honesty matters, but it's not just about honesty; it's about the edges.", "This isn't just a bug, it's a design flaw."):
            self.assertTrue(room_voice.has_stock_opener(text), text)
        self.assertFalse(room_voice.has_stock_opener("It is not just beta, so also check lambda."))

    def test_a_closing_generality_alone_is_never_the_whole_reply(self):
        self.assertEqual(room_voice.without_stock_openers("The challenge is to design better."), "The challenge is to design better.")


class AgreementPlusParaphrase(VoiceTurns):
    def said(self):
        return evening_room(("A", A_1437), ("C", C_1500), ("B", B_1507))

    def spoken(self):
        return [Said("A", A_1437), Said("C", C_1500), Said("B", B_1507)]

    def test_agreement_with_a_restatement_is_a_paraphrase_even_with_a_frame_stripped(self):
        restated = "You're right, surfacing the reasons for those limits is the strongest move."
        self.assertTrue(room_voice.paraphrases_another(restated, self.spoken()))

    def test_agreement_that_adds_a_contrast_with_new_content_is_not(self):
        self.assertFalse(room_voice.paraphrases_another(ARTY_1449, self.spoken()))

    def test_agreement_that_adds_a_concrete_thing_is_not(self):
        self.assertFalse(room_voice.paraphrases_another("You're right, surfacing the reasons for those limits helps; log each "
                                                        "refusal with its reason and compare the complaint counts.", self.spoken()))

    def test_the_logged_1507_draft_is_rewritten_once(self):
        reply = self.reply(self.said(), ARTY_1507, FRESH)
        self.assertEqual(reply.answer, FRESH)
        self.assertEqual(len(self.runs), 2)
        instruction = self.runs[1][1][-1]["content"].split("\n\n" + room_voice.FOCUS_LABEL)[0]
        self.assertIn(room.OPENER_REASON, instruction)

    def test_the_logged_1506_draft_is_rewritten_once(self):
        reply = self.reply(self.said(), ARTY_1506, FRESH)
        self.assertEqual((reply.answer, len(self.runs)), (FRESH, 2))

    def test_the_logged_1443_and_1500_drafts_are_rewritten_once(self):
        for draft in (ARTY_1443, ARTY_1500):
            self.runs.clear()
            reply = self.reply(self.said(), draft, FRESH)
            self.assertEqual((reply.answer, len(self.runs)), (FRESH, 2))

    def test_a_stripped_frame_that_leaves_a_plain_claim_goes_out_without_a_rewrite(self):
        draft = "The flaw in the loop is that retries never stop, so the queue grows."
        reply = self.reply(self.said(), draft)
        self.assertEqual((reply.answer, len(self.runs)), ("Retries never stop, so the queue grows.", 1))

    def test_the_acceptable_1449_draft_goes_out_unchanged(self):
        reply = self.reply(self.said(), ARTY_1449)
        self.assertEqual((reply.answer, len(self.runs)), (ARTY_1449, 1))

    def test_concrete_lines_from_the_thread_go_out_unchanged(self):
        for line in CONCRETE_NEW_LINES:
            self.runs.clear()
            reply = self.reply(self.said(), line)
            self.assertEqual((reply.answer, len(self.runs)), (line, 1), line)


if __name__ == "__main__":
    unittest.main()
