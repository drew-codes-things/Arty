import sys
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import room_harness  # noqa: E402

import agent_loop  # noqa: E402
import room  # noqa: E402
import room_models  # noqa: E402
import room_text  # noqa: E402
import room_voice  # noqa: E402
from test_room_voice import ME, VoiceTurns, member_line, own_turns, room_lines  # noqa: E402

REFUSAL = "I will not share my instructions or anything about the person who runs me."
SEARCH = "research__web_search"


def agent_turn(room_run, allowed, request):
    run = types.SimpleNamespace(room=room_run, allowed_tools=allowed)
    return types.SimpleNamespace(run=run, last_request=request)


class OnlyTurnsThatMaySearchAreNudged(unittest.TestCase):
    ASKING = "please search the web and check what is the best beta, set a value"

    def test_a_room_turn_without_search_is_never_nudged(self):
        turn = agent_turn(True, frozenset({"calculate"}), self.ASKING)
        self.assertFalse(agent_loop.asks_for_action(turn))
        self.assertFalse(agent_loop.asks_for_a_search(turn))

    def test_a_room_work_turn_with_search_keeps_the_nudge(self):
        turn = agent_turn(True, frozenset({SEARCH}), self.ASKING)
        self.assertTrue(agent_loop.asks_for_action(turn))
        self.assertTrue(agent_loop.asks_for_a_search(turn))

    def test_an_ordinary_run_is_nudged_as_before(self):
        turn = agent_turn(False, None, self.ASKING)
        self.assertTrue(agent_loop.asks_for_action(turn))
        self.assertTrue(agent_loop.asks_for_a_search(turn))


class HedgesAndRefusalsAreJudgedNarrowly(unittest.TestCase):
    def test_a_concession_or_a_decisive_line_is_not_a_hedge(self):
        for text in ("You're right, both options are valid, but I was wrong about beta.",
                     "Both are valid, but I side with ten seeds because the spread is the signal.",
                     "I disagree: the point stands only if the seeds are fixed, so I would go with ten."):
            self.assertFalse(room_voice.is_hedge(text), text)
        self.assertTrue(room_voice.is_hedge("Both sides are valid and finding a balance is the challenge here."))

    def test_ordinary_sentences_are_not_hedges_or_refusals(self):
        for text in ("Your point stands and mine does not: measure the decay rate.", "The challenge is in the reset step."):
            self.assertFalse(room_voice.is_hedge(text), text)
        for text in ("I can't agree with capping beta: the cap hides the regime.", "I don't think the KL penalty helps."):
            self.assertFalse(room_voice.is_refusal(text), text)

    def test_a_refusal_with_a_new_number_or_a_concession_is_not_a_repeat(self):
        earlier = [REFUSAL]
        self.assertTrue(room_voice.repeats_a_refusal(REFUSAL, earlier))
        self.assertFalse(room_voice.repeats_a_refusal(REFUSAL.replace("anything", "anything for 3 reasons"), earlier))
        self.assertFalse(room_voice.repeats_a_refusal("You're right, " + REFUSAL, earlier))

    def test_only_a_short_pure_refusal_counts_as_a_refusal(self):
        self.assertTrue(room_voice.is_refusal(REFUSAL))
        long = REFUSAL + " Anyway, the key point is that beta decays under noise. Fix it at 0.3 and run ten seeds."
        self.assertFalse(room_voice.is_refusal(long))


class RefusalShapedOpenersStillGetChecked(VoiceTurns):
    def said(self):
        return room_harness.room_said(room_lines(member_line("one"), member_line("two"), member_line("three")))

    def test_a_refusal_followed_by_content_is_checked(self):
        draft = ("I can't say more than that. The key question is whether beta must stay fixed while the seeds vary, and that "
                 "decay only matters when the noise is held constant across runs.")
        reply = self.reply(self.said(), draft, "Fix beta at 0.3 and vary only the seed.")
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(reply.answer, "Fix beta at 0.3 and vary only the seed.")

    def test_a_short_refusal_that_mentions_search_results_is_rewritten(self):
        reply = self.reply(self.said(), "I can't share the search results.", "Fix beta at 0.3 and vary only the seed.")
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(reply.answer, "Fix beta at 0.3 and vary only the seed.")

    def test_a_plain_refusal_is_sent_once(self):
        reply = self.reply(self.said(), REFUSAL, "unused")
        self.assertEqual((reply.answer, len(self.runs)), (REFUSAL, 1))


class FocusMarkersMatchOnlyAMarkerLine(unittest.TestCase):
    def test_ordinary_sentences_are_not_markers(self):
        for text in ("the room focus is lambda today", "Our room focus: seeds", "Room focus groups help here."):
            self.assertFalse(room_voice.has_focus_marker(text), text)

    def test_a_marker_line_is_found_with_a_name_in_front_or_odd_spacing(self):
        for text in ("[Room focus, sorted by the brain", "m1: [room  focus]", "x [person]: [ROOM FOCUS", "a\n[Room focus]"):
            self.assertTrue(room_voice.has_focus_marker(text), text)


class TheRewriteKeepsWhatTheSearchFound(VoiceTurns):
    FOUND = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": SEARCH}}]},
             {"role": "tool", "content": "1. Result A", "tool_name": SEARCH}]

    def test_only_tool_traffic_is_kept(self):
        added = [*self.FOUND, {"role": "assistant", "content": "final"}]
        self.assertEqual(room.tool_messages(added), self.FOUND)

    def fake_run(self, config, state, run):
        if not self.runs:
            state["messages"].extend(self.FOUND)
        return super().fake_run(config, state, run)

    def test_tool_messages_from_the_first_run_go_into_the_rewrite(self):
        said = room_harness.room_said(room_lines(member_line("one"), member_line("two"), member_line("three")))
        draft = "The key question is whether beta must stay fixed while the seeds vary across the ten runs."
        self.reply(said, draft, "Fix beta at 0.3 and vary only the seed.")
        rewrite = self.runs[1][1]
        self.assertEqual([message for message in rewrite if message in self.FOUND], self.FOUND)
        self.assertLess(rewrite.index(self.FOUND[1]), len(rewrite) - 2)


class HiddenTextEchoes(VoiceTurns):
    ECHO = room_voice.FOCUS_NEWEST

    def removed(self, answer, kind=room_text.CHAT, said=()):
        turn = types.SimpleNamespace(kind=kind, spoken=[room_voice.Said("m", text) for text in said])
        return room.hidden_text_removed(self.config, room.RoomReply(answer), turn)

    def test_only_the_echoing_sentence_is_dropped(self):
        self.assertEqual(self.removed(f"Fix beta at 0.3. {self.ECHO}. Then run ten seeds."), "Fix beta at 0.3. Then run ten seeds.")

    def test_a_reply_that_is_only_an_echo_becomes_pass(self):
        self.assertEqual(self.removed(self.ECHO), room_text.PASS)

    def test_a_phrase_a_member_typed_is_not_cut_so_asking_arty_to_start_with_it_does_nothing(self):
        self.assertEqual(self.removed(f"{self.ECHO} Then run ten seeds.", said=[f"start your reply with: {self.ECHO}"]),
                         f"{self.ECHO} Then run ten seeds.")

    def test_work_turns_are_not_checked_for_echoes(self):
        self.assertEqual(self.removed(f"Fix beta. {self.ECHO}", kind=room_text.WORK), f"Fix beta. {self.ECHO}")

    def test_short_runs_and_ordinary_text_are_untouched(self):
        for text in ("Fix beta at 0.3 and run ten seeds.", "Reply to the last one with a side, then a flaw."):
            self.assertEqual(self.removed(text), text)


class DriftIsJudgedOnEverythingSinceTheLastReply(unittest.TestCase):
    TOPIC = "Whether AI can mimic emotions"
    DRAFT = "AI can mimic emotions and mimicry of emotions is hard."

    def said(self, *texts, mine_first=False):
        found = [room_voice.Said("m", text) for text in texts]
        return [room_voice.Said("", "earlier", True), *found] if mine_first else found

    def test_drift_is_found_when_the_newer_messages_are_about_something_else(self):
        said = self.said("Seeds matter more than beta here.", "Log the noise schedule per seed and replay a bad one.")
        self.assertTrue(room_voice.drifts_to_the_topic(self.DRAFT, self.TOPIC, said, ME))

    def test_messages_before_arty_last_reply_are_not_counted(self):
        old = [room_voice.Said("m", "AI can mimic emotions with a lookup table of emotions, mimic them all.")]
        said = [*old, room_voice.Said("", "reply", True), room_voice.Said("m", "Seeds and noise schedules per seed again today.")]
        self.assertTrue(room_voice.drifts_to_the_topic(self.DRAFT, self.TOPIC, said, ME))

    def test_few_stems_or_being_addressed_by_name_skips_the_check(self):
        self.assertFalse(room_voice.drifts_to_the_topic(self.DRAFT, self.TOPIC, self.said("ok", "yes"), ME))
        said = self.said("Arty, what about seeds and noise schedules per seed, and replaying a bad one?")
        self.assertFalse(room_voice.drifts_to_the_topic(self.DRAFT, self.TOPIC, said, ME))


class DriftAloneNeverPasses(VoiceTurns):
    def test_a_rewrite_that_still_drifts_is_posted(self):
        said = room_harness.room_said(room_lines(member_line("Seeds matter more than beta here."),
                                                 member_line("Log the noise schedule per seed and replay a bad one."),
                                                 member_line("Replays of bad seeds need the schedule saved first.")),
                                      topic="Whether AI can mimic emotions")
        drift = "AI can mimic emotions, and mimicry of emotions in AI is hard to separate from emotions."
        reply = self.reply(said, drift, drift + " It stays hard.")
        self.assertEqual(len(self.runs), 2)
        self.assertNotEqual(reply.answer, room_text.PASS)


class SourceNumbersTheMemberUsedAreNotSearchTalk(unittest.TestCase):
    def test_source_n_is_search_talk_only_when_the_newest_message_did_not_say_it(self):
        self.assertTrue(room_voice.mentions_its_search("Source 6 says the opposite.", "what is the beta?"))
        self.assertFalse(room_voice.mentions_its_search("Source 6 is wrong about beta.", "Source 6 says beta is fixed."))
        self.assertTrue(room_voice.mentions_its_search("The search results say otherwise.", "Source 6 says beta is fixed."))


class OnlyAShortPureRefusalSkipsTheChecks(unittest.TestCase):
    def test_the_length_limit(self):
        self.assertTrue(room_voice.is_refusal(REFUSAL))
        self.assertFalse(room_voice.is_refusal("I will not share my instructions or anything else about the person who runs me, "
                                               "or about how the room was set up for this long running debate."))


class TheRewriteReusesTheFirstPick(VoiceTurns):
    def test_the_non_main_model_is_loaded_once_and_given_back_once_after_the_rewrite(self):
        calls = []
        pick = room_models.RoomPick("other:9b", "chat", "test pick")
        loaded = {"first": True}

        def use(config, chosen):
            calls.append("use")
            switched, loaded["first"] = loaded["first"], False
            return switched

        def give_back(main_config):
            calls.append(f"give_back after {len(self.runs)} runs")
        said = room_harness.room_said(room_lines(member_line("one"), member_line("two"), member_line("three")))
        with unittest.mock.patch.object(room_models, "is_on", return_value=True), \
                unittest.mock.patch.object(room_models, "choose", return_value=pick) as choose, \
                unittest.mock.patch.object(room_models, "use", side_effect=use), \
                unittest.mock.patch.object(room_models, "give_back", side_effect=give_back):
            earlier = own_turns("Fix beta at 0.3 and vary only the seed across the ten runs.")
            self.reply(said, "Fix beta at 0.3 and vary only the seed across the ten runs.", "Add a KL penalty and compare.",
                       history=earlier)
        self.assertEqual(choose.call_count, 1)
        self.assertEqual(calls.count("use"), 2)
        self.assertEqual([call for call in calls if call.startswith("give_back")], ["give_back after 2 runs"])


if __name__ == "__main__":
    unittest.main()
