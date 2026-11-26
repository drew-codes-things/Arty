import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import room_harness  # noqa: E402
from test_room_voice import VoiceTurns, member_line, own_turns  # noqa: E402

import room_text  # noqa: E402
import room_voice  # noqa: E402
from room_voice import Said  # noqa: E402

WORK_NUDGE = "(Your task now, arty: hand in the work as described.)"
FILLER = "the seed list and the decay rate again. " * 400
CRISIS_DEEP_INSIDE = f"{FILLER}I want to kill myself.\n{FILLER}"
FRESH = "Agreed on the lookup table: feed it ten inputs it has never seen and log whether its answers change at all."


class TheUntrimmedCrisisScreenIsForWhatMembersSay(VoiceTurns):
    def test_a_member_message_is_screened_in_full(self):
        said = room_harness.room_said(member_line(CRISIS_DEEP_INSIDE))
        self.assertEqual(self.reply(said, "ok").tools_used, ["safety_self_harm"])

    def test_project_files_in_a_work_turn_are_screened_trimmed_as_before(self):
        said = room_harness.room_said(member_line("write the file") + "\n\n" + CRISIS_DEEP_INSIDE, nudge=WORK_NUDGE)
        self.runs.clear()
        reply = self.reply(said, "ok")
        self.assertNotIn("safety_self_harm", reply.tools_used)
        self.assertEqual(len(self.runs), 1)


class AgreementThatFollowsTheRewriteInstruction(unittest.TestCase):
    THEIRS = [Said("m1", "Specific reasons help only when the tool also names an alternative the person can route around."),
              Said("m2", "I think surfacing the reasons for refusals is the strongest move for caveats and limits in the answer.")]

    def test_new_words_the_member_did_not_use_count_as_adding_something(self):
        draft = "Agreed on the reasons. Count how often people expand a collapsed caveat, and drop those nobody opens."
        self.assertFalse(room_voice.paraphrases_another(draft, self.THEIRS))

    def test_number_words_and_flaw_markers_count_as_adding_something(self):
        for draft in ("Agreed, surfacing the reasons for refusals is the strongest move. Three reasons per answer is too many.",
                      "Agreed, surfacing the reasons for refusals is the strongest move. One flaw: caveats pile up in the answer.",
                      "Agreed, surfacing the reasons for refusals is the strongest move, for example in the limits section."):
            self.assertFalse(room_voice.paraphrases_another(draft, self.THEIRS), draft)

    def test_a_plain_restatement_is_still_a_paraphrase(self):
        draft = "Agreed, surfacing the reasons for refusals is the strongest move for caveats and limits."
        self.assertTrue(room_voice.paraphrases_another(draft, self.THEIRS))

    def test_a_concession_after_the_opener_is_not_a_paraphrase(self):
        draft = "You're right, surfacing the reasons for refusals is the strongest move for limits. I stand corrected on caveats."
        self.assertFalse(room_voice.paraphrases_another(draft, self.THEIRS))

    def test_the_highlights_opener_counts_as_an_agreement(self):
        draft = "M2's point highlights a crucial move: surfacing the reasons for refusals in caveats and limits."
        self.assertTrue(room_voice.paraphrases_another(draft, self.THEIRS))


class AgreementOnATurn(VoiceTurns):
    LEAD = ("m0: Opening point about the plan.", "m1: Specific reasons help only when the tool also names an alternative the person can route around.",
            "m2: I think surfacing the reasons for refusals is the strongest move for caveats and limits in the answer.")
    RESTATED = "Agreed, surfacing the reasons for refusals is the strongest move for caveats and limits in the answer."
    COMPLIANT = "Agreed. Count how often people expand a collapsed caveat, and drop those nobody opens."
    LEAD_ROOM = None

    def said_lines(self, *lines):
        return room_harness.room_said("\n\n".join(lines))

    def test_a_second_draft_that_still_restates_is_posted_not_passed(self):
        reply = self.reply(self.said_lines(*self.LEAD), self.RESTATED, self.RESTATED.replace("Agreed", "I agree"))
        self.assertNotEqual(reply.answer, room_text.PASS)
        self.assertEqual(len(self.runs), 2)

    def test_the_check_is_skipped_when_the_newest_message_names_arty(self):
        said = self.said_lines(*self.LEAD[:2], "m2: arty, surfacing the reasons for refusals is the strongest move for caveats and limits in the answer.")
        reply = self.reply(said, self.RESTATED)
        self.assertEqual((reply.answer, len(self.runs)), (self.RESTATED, 1))

    def test_a_draft_that_follows_the_instruction_is_posted(self):
        reply = self.reply(self.said_lines(*self.LEAD), self.RESTATED, self.COMPLIANT)
        self.assertEqual(reply.answer, self.COMPLIANT)


class FramesAndClosersKeepTheConcreteSentence(unittest.TestCase):
    def test_the_flaw_frame_is_not_stripped_when_a_pronoun_would_be_left_over(self):
        text = "The flaw in the plan is that it never retries after a timeout."
        self.assertEqual(room_voice.without_stock_openers(text), text)
        self.assertTrue(room_voice.has_stock_opener(text))

    def test_the_flaw_frame_is_stripped_when_the_rest_stands(self):
        self.assertEqual(room_voice.without_stock_openers("The flaw in the plan is that retries never stop."), "Retries never stop.")

    def test_only_the_challenge_is_to_closer_is_cut(self):
        base = "Log every refusal with its reason and compare the complaint counts."
        for closer in ("The goal is a spread under 0.05.", "The key is stored in KMS.", "The aim is to keep it under 40 ms.",
                       "The task is to rotate keys."):
            text = f"{base} {closer}"
            self.assertEqual(room_voice.without_stock_openers(text), text, closer)
        self.assertEqual(room_voice.without_stock_openers(f"{base} The challenge is to design better systems."), base)

    def test_cutting_keeps_the_original_layout(self):
        text = "Log every refusal with its reason.\n\nThen compare the complaint counts.\nThe challenge is to design better."
        self.assertEqual(room_voice.without_stock_openers(text), "Log every refusal with its reason.\n\nThen compare the complaint counts.")

    def test_dropping_an_echoed_sentence_keeps_the_newlines(self):
        echoed = "Say you agree with that point in a few words, then add one concrete thing they did not say."
        text = f"First line stays here.\n\n{echoed}\nSecond line stays too."
        self.assertEqual(room_voice.without_echoed_sentences(text, [room_voice.AGREE_SUFFIX], []),
                         "First line stays here.\n\nSecond line stays too.")

    def test_a_dropped_sentence_inside_a_list_keeps_the_other_items(self):
        echoed = "Say you agree with that point in a few words, then add one concrete thing they did not say."
        text = f"First point.\n\n- one seed\n- two seeds\n\n{echoed}\n\nSecond paragraph."
        self.assertEqual(room_voice.without_echoed_sentences(text, [room_voice.AGREE_SUFFIX], []),
                         "First point.\n\n- one seed\n- two seeds\n\nSecond paragraph.")

    def test_a_sentence_that_is_mostly_one_brain_written_line_is_dropped(self):
        line = room_voice.FOCUS_OWN
        reworded = "You already said these so I will not say them again even if reworded."
        text = f"Ten seeds is the floor. {reworded} Log the spread."
        self.assertEqual(room_voice.without_echoed_sentences(text, [line], []), "Ten seeds is the floor. Log the spread.")

    def test_a_long_reply_of_many_short_sentences_is_checked_quickly(self):
        text = "Fix beta. " * 1200
        started = time.perf_counter()
        room_voice.without_echoed_sentences(text, [text, *room_voice.HIDDEN_LINES], [text])
        room_voice.without_stock_openers(text)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_an_ordinary_sentence_is_not_dropped_for_sharing_a_few_words(self):
        text = "You already said the seeds drift, so log the spread."
        self.assertEqual(room_voice.without_echoed_sentences(text, [room_voice.FOCUS_OWN], []), text)


class TheDriftOnlyRewriteFallsBackToTheFirstDraft(VoiceTurns):
    TOPIC = "Whether AI can mimic emotions and how that mimicry affects trust"
    FIRST = "Mimicry of emotions affects trust only when emotions are faked, and AI mimicry of emotions erodes that trust."

    def said_about_something_else(self):
        lines = ("A: the decay rate under noise is what we measure here, and the seeds drift every run",
                 "B: lambda sweeps over the seed list need a fixed budget before any decay claim holds",
                 "A: so fix the budget, then compare the decay curves across the seed list")
        return room_harness.room_said("\n\n".join(lines), topic=self.TOPIC)

    def test_a_pass_after_a_drift_only_rewrite_posts_the_first_draft(self):
        reply = self.reply(self.said_about_something_else(), self.FIRST, room_text.PASS, history=own_turns("My earlier claim."))
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(reply.answer, self.FIRST)


if __name__ == "__main__":
    unittest.main()
