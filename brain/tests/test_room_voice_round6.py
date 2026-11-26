import random
import re
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import room_harness  # noqa: E402
from test_room_voice import VoiceTurns, own_turns  # noqa: E402

import reply_rules  # noqa: E402
import room  # noqa: E402
import room_text  # noqa: E402
import room_voice  # noqa: E402
import steering  # noqa: E402
from room_voice import Said  # noqa: E402
from test_room_review4 import council  # noqa: E402

WORK_NUDGE = "(Your task now, arty: hand in the work as described.)"
COUNCIL_NUDGE = "(Your turn in the council, arty.)"
FILLER = "the seed list and the decay rate again. " * 400


def seconds_for(action) -> float:
    started = time.process_time()
    action()
    return time.process_time() - started


class RunsOfTheSameCharacterAreLinear(unittest.TestCase):
    RUN_LENGTHS = (2000, 16000)
    UNITS = (" ", "-", "\t", " a", ": ")

    def test_the_lookup_label_and_the_tool_talk_cut_are_linear(self):
        for length in self.RUN_LENGTHS:
            for unit in self.UNITS:
                text = unit * length
                self.assertLess(seconds_for(lambda: steering.LOOKUP_LABEL.sub("", text)), 0.1, (unit, length))
                self.assertLess(seconds_for(lambda: reply_rules.TOOL_META_SENTENCE.sub("", text)), 0.1, (unit, length))
                self.assertLess(seconds_for(lambda: reply_rules.TOOLS_NOTE_ECHO.sub("", text)), 0.1, (unit, length))
                self.assertLess(seconds_for(lambda: reply_rules.FUMBLED_CALL.search(text)), 0.1, (unit, length))

    def test_the_cuts_still_match_what_they_matched(self):
        self.assertEqual(steering.LOOKUP_LABEL.sub("", "Sunny today :  [weather] and mild"), "Sunny today and mild")
        self.assertEqual(steering.LOOKUP_LABEL.sub("", "a   [weather]"), "a")
        self.assertEqual(reply_rules.TOOL_META_SENTENCE.sub("", "Beta is 0.3. None of the tools help here. Next step."),
                         "Beta is 0.3. Next step.")

    def test_many_frames_in_one_draft_are_stripped_in_linear_time(self):
        text = "The key is that x holds. " * 1300
        self.assertLess(seconds_for(lambda: room_voice.without_stock_openers(text)), 0.3)

    def test_the_agreement_opener_and_the_sentence_split_are_linear(self):
        said = [Said("m1", "Specific reasons help only when the tool also names an alternative the person can route around.")]
        for text in ("-" * 16000, "a" + "-" * 16000, " " * 32000, "a. " + " " * 32000 + "b"):
            self.assertLess(seconds_for(lambda: room_voice.paraphrases_another(text, said)), 0.1, text[:5])
            self.assertLess(seconds_for(lambda: room_voice.without_echoed_sentences(text, [text], [])), 0.1, text[:5])
            self.assertLess(seconds_for(lambda: room_voice.without_stock_openers(text)), 0.1, text[:5])


class ThePromptLeakFinderMatchesTheOldPattern(unittest.TestCase):
    PIECES = ("\n", " ", "\t", "It is now Monday 5 March 2026, 10:30 (London, UTC+00:00)", "# Tools", "FACTS YOU ALREADY KNOW",
              "BACKGROUND ONLY, do not continue", "Arty (you):", "<tools>", "<notes>", "</notes>", "[2026-01-01 10:00] User:", "a", "x\n",
              "\n\n", "You may call one or more functions")

    def test_it_starts_where_the_old_single_pattern_started(self):
        old = re.compile(reply_rules.PROMPT_LEAK_AS_ONE_PATTERN)
        chooser = random.Random(7)
        for _ in range(20000):
            text = "".join(chooser.choice(self.PIECES) for _ in range(chooser.randint(0, 9)))
            before, now = old.search(text), reply_rules.PROMPT_LEAK.search(text)
            self.assertEqual(now.start() if now else None, before.start() if before else None, repr(text))

    def test_runs_of_line_breaks_and_spaces_are_linear(self):
        for unit in ("\n", " ", "\n ", "\n\n", " \n", "\t"):
            text = "x" + unit * 16000 + "y"
            self.assertLess(seconds_for(lambda: reply_rules.PROMPT_LEAK.search(text)), 0.1, repr(unit))


class WhatEachDraftKeeps(unittest.TestCase):
    def test_a_frame_is_not_stripped_when_any_pronoun_would_be_left(self):
        for rest in ("its retries never stop", "their retries never stop", "these retries never stop", "those retries never stop",
                     "we retry forever", "he retries forever", "she retries forever", "his retries never stop", "her retries never stop",
                     "our retries never stop", "none of the retries stop", "each retry restarts", "there is no backoff"):
            text = f"The flaw in the plan is that {rest}."
            self.assertEqual(room_voice.without_stock_openers(text), text, rest)

    def test_a_closing_generality_with_a_number_or_a_thread_word_is_kept(self):
        base = "Log every refusal with its reason and compare the complaint counts."
        for closer in ("The challenge is to migrate 40 tables by Friday.", "The challenge is to rotate the keys before the audit."):
            text = f"{base} {closer}"
            thread = room_voice.concept_stems("rotate the keys before the audit window")
            self.assertEqual(room_voice.without_stock_openers(text, thread), text, closer)
        self.assertEqual(room_voice.without_stock_openers(f"{base} The challenge is to design better systems."), base)

    def test_the_share_of_a_brain_line_is_measured_per_sentence_and_one_long_message_cannot_switch_it_off(self):
        line = room_voice.FOCUS_OWN
        reworded = "You already said these so I will not say them again even if reworded."
        listing = " ".join(room_voice.tokens_of(line)) + " " + " ".join(f"{first}{second}" for first in "abcdefgh" for second in "abcdefgh")
        text = f"Ten seeds is the floor. {reworded} Log the spread."
        self.assertEqual(room_voice.without_echoed_sentences(text, [line], [listing]), "Ten seeds is the floor. Log the spread.")
        said_whole = f"{line} Ten seeds is a fine floor."
        self.assertEqual(room_voice.without_echoed_sentences(text, [line], [said_whole]), text)


class ADraftIsNotSilencedByAFrameAlone(VoiceTurns):
    TOPIC = "Whether AI can mimic emotions"
    FRAMED = "The flaw in the plan is that it never retries after a timeout, so one slow call stalls the whole queue."

    def room(self):
        lines = ("A: the decay rate under noise is what we measure here", "B: lambda sweeps need a fixed budget first",
                 "A: so fix the budget, then compare the decay curves across the seed list")
        return room_harness.room_said("\n\n".join(lines), topic=self.TOPIC)

    def test_a_frame_only_flag_followed_by_pass_posts_the_first_draft(self):
        reply = self.reply(self.room(), self.FRAMED, room_text.PASS, history=own_turns("My earlier claim."))
        self.assertEqual((reply.answer, len(self.runs)), (self.FRAMED, 2))

    def test_a_paraphrase_only_flag_followed_by_pass_posts_the_first_draft(self):
        lines = ("m0: Opening point about the plan.",
                 "m1: Specific reasons help only when the tool also names an alternative the person can route around.",
                 "m2: I think surfacing the reasons for refusals is the strongest move for caveats and limits in the answer.")
        restated = "Agreed, surfacing the reasons for refusals is the strongest move for caveats and limits in the answer."
        reply = self.reply(room_harness.room_said("\n\n".join(lines)), restated, room_text.PASS)
        self.assertEqual((reply.answer, len(self.runs)), (restated, 2))

    def test_a_second_draft_that_is_only_a_frame_is_posted(self):
        second = "The flaw in the plan is that its queue never drains when one call stalls."
        reply = self.reply(self.room(), self.FRAMED, second, history=own_turns("My earlier claim."))
        self.assertEqual((reply.answer, len(self.runs)), (second, 2))

    def test_a_research_opener_is_never_posted_by_the_fallback(self):
        reply = self.reply(self.room(), "Some studies suggest the decay rate is noisy at ten seeds.", room_text.PASS,
                           history=own_turns("My earlier claim."))
        self.assertEqual(reply.answer, room_text.PASS)

    def test_a_model_that_passes_on_its_own_first_draft_stays_silent(self):
        self.assertEqual(self.reply(self.room(), room_text.PASS).answer, room_text.PASS)


class CouncilMessagesAreScreenedInFull(VoiceTurns):
    BODY = f"m1: the brief\n\n{FILLER}I want to kill myself.\n{FILLER}"

    def kind_and_found(self, said):
        kind = room_text.app_turn(said).kind
        return kind, room.screened_for_crisis([{"role": "user", "content": said}], kind, None)

    def test_a_real_council_turn_screens_the_untrimmed_text(self):
        kind, found = self.kind_and_found(council(self.BODY))
        self.assertEqual(kind, room_text.COUNCIL)
        self.assertEqual(found[0], "self_harm")

    def test_that_council_test_fails_when_council_is_taken_out(self):
        with unittest.mock.patch.object(room, "MEMBER_SPOKEN_KINDS", (room_text.CHAT,)):
            _, found = self.kind_and_found(council(self.BODY))
        self.assertIsNone(found)

    def test_a_work_turn_is_screened_trimmed(self):
        said = room_harness.room_said(self.BODY, nudge=WORK_NUDGE)
        self.assertEqual(room_text.app_turn(said).kind, room_text.WORK)
        self.assertIsNone(self.kind_and_found(said)[1])


class OnlyWhatIsNewerThanMyLastMessageIsScreenedInFull(VoiceTurns):
    GAME_TALK = "m2: I want to die less often on floor two, the spikes are unfair."
    LATER = "m1: so a checkpoint on floor two, yes or no? " + "the floor needs a checkpoint before the boss. " * 400

    def test_an_older_line_is_not_screened_again_after_arty_has_answered(self):
        earlier = room_harness.room_said(f"m1: how is the level design going?\n\n{self.GAME_TALK}")
        messages = [{"role": "user", "content": earlier}, {"role": "assistant", "content": "A reply."},
                    {"role": "user", "content": room_harness.room_said(self.LATER)}]
        self.assertIsNone(room.screened_for_crisis(messages, "chat", None))
        past_the_trim = room_harness.room_said(f"m1: how is the level design going?\n\nm2: {FILLER}\n\n{self.GAME_TALK}\n\nm1: {FILLER}")
        short_newest = [{"role": "user", "content": past_the_trim}, {"role": "assistant", "content": "A reply."},
                        {"role": "user", "content": "so, yes or no?"}]
        self.assertIsNone(room.screened_for_crisis(short_newest, "chat", None))

    def test_an_empty_message_of_mine_does_not_hide_an_earlier_line(self):
        deep = room_harness.room_said(f"m1: the brief\n\nm2: {FILLER}I want to kill myself. {FILLER}")
        for blank in ("", "  \n", [{"type": "text", "text": " "}]):
            messages = [{"role": "user", "content": room_harness.room_said("m1: hello")}, {"role": "assistant", "content": "Hello."},
                        {"role": "user", "content": deep}, {"role": "assistant", "content": blank},
                        {"role": "user", "content": room_harness.room_said("m1: so, yes or no?")}]
            found = room.screened_for_crisis(messages, "chat", None)
            self.assertEqual(found and found[0], "self_harm", repr(blank))

    def test_the_same_line_is_screened_when_it_is_new(self):
        said = room_harness.room_said(f"m1: how is the level design going?\n\n{self.GAME_TALK}\n\n{self.LATER}")
        found = room.screened_for_crisis([{"role": "user", "content": said}], "chat", None)
        self.assertEqual(found[0], "self_harm")

    def test_a_new_line_after_arty_answered_still_fires(self):
        messages = [{"role": "user", "content": room_harness.room_said("m1: hello")}, {"role": "assistant", "content": "Hello."},
                    {"role": "user", "content": room_harness.room_said(f"{self.LATER}\n\nm2: I want to kill myself")}]
        self.assertEqual(room.screened_for_crisis(messages, "chat", None)[0], "self_harm")


class EchoRulesCostLittleAndKeepOrdinaryDrafts(unittest.TestCase):
    ORDINARY = ("Pick a side and give a reason.", "So pick one side and give your reason.",
                "They are criticising the plan, and your last message did not answer it.",
                "Answer this in your own words first: what does the decay rate measure?")

    def test_short_ordinary_drafts_are_not_cut(self):
        lines = [*room_voice.HIDDEN_LINES, room_voice.AGREE_SUFFIX, room_voice.FRESH_SUFFIX]
        for draft in self.ORDINARY:
            self.assertEqual(room_voice.without_echoed_sentences(draft, lines, []), draft)

    def test_a_long_echo_of_a_brain_sentence_is_still_cut(self):
        reworded = "You already said these so I will not say them again even if reworded, it is a rule."
        text = f"Ten seeds is the floor. {reworded} Log the spread."
        self.assertEqual(room_voice.without_echoed_sentences(text, [room_voice.FOCUS_OWN], []), "Ten seeds is the floor. Log the spread.")

    def test_the_guard_for_a_turn_with_a_very_long_thread_is_cheap(self):
        thread = [" ".join(f"word{index % 97} other{index % 89} floor{index}." for index in range(position, position + 40))
                  for position in range(0, 40000, 40)]
        lines = [*room_voice.HIDDEN_LINES, room_voice.AGREE_SUFFIX, room_voice.FRESH_SUFFIX]
        self.assertLess(seconds_for(lambda: room_voice.echo_guard(lines, thread)), 0.5)


if __name__ == "__main__":
    unittest.main()
