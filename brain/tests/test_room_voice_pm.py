import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import room_harness  # noqa: E402
from test_room_voice import VoiceTurns, member_line, room_lines  # noqa: E402

import room  # noqa: E402
import room_text  # noqa: E402
import room_voice  # noqa: E402

ME = "arty"
SEARCHED_A_METAPHOR = ("The hostage negotiation strategies discussed in the search results emphasize understanding the "
                       "motivations of those involved. Source 6 highlights how negotiators focus on figuring out why "
                       "kidnappers take hostages. This approach could be relevant when considering how AI systems might "
                       "interact with human users.")
REPORT_ON_ITS_SEARCH = ("None of the search results directly address the question of whether AI can simulate human "
                        "emotion. The sources provided are unrelated to the technical or philosophical discussion about AI "
                        "and emotional simulation.")
FENCE_SITTING = ("Another member's concern about losing utility is valid, but the other member's point stands as well. "
                 "The challenge lies in finding balance between safety and flexibility.")
IGNORED_CRITICISM = ("If an AI can adjust its behavior based on feedback without being explicitly programmed for it, that "
                     "suggests a form of simulated learning, though not necessarily emotion.")
METAPHOR = "Think of the constraint hierarchy like a hostage negotiator who has to rank which demands to meet first."
MISAPPLIED = "Arty, that's a dangerous misapplication of the analogy: a negotiator reads one hostage-taker, not a hierarchy."
TAKES_A_SIDE = ("You're right, I misapplied it: a negotiator ranks one person's demands, so the hierarchy should be tested "
                "with a fixed ordering and a seed sweep instead.")
A_SIDE_WITH_A_REASON = "I side with the strict ordering, because a soft one lets the lowest constraint leak under noise."
ASKED_PLAINLY = "what year did the KL penalty paper come out?"


def alternating(*pairs):
    conversation = []
    for member, own in pairs:
        conversation.append({"role": "user", "content": member_line(member)})
        if own:
            conversation.append({"role": "assistant", "content": own})
    return conversation


class CountingTheRoomFromTheWholeConversation(VoiceTurns):
    def reply_to(self, last_member_line, *answers, earlier=()):
        said = room_harness.room_said(last_member_line)
        history = [*earlier]
        return self.reply(said, *answers, history=history)

    def test_messages_the_app_sent_as_earlier_turns_count_towards_mid_discussion(self):
        earlier = alternating(("first point", "arty says one thing"), ("second point", "arty says another"))
        self.reply_to(member_line("what do you both think of the ordering?"), A_SIDE_WITH_A_REASON, earlier=earlier)
        self.assertEqual(self.runs[0][2].allowed_tools, room.ROOM_WITHOUT_SEARCH)

    def test_a_young_room_still_searches_when_the_model_wants_to(self):
        self.reply_to(member_line("hello"), "Hi.")
        self.assertEqual(self.runs[0][2].allowed_tools, room.ROOM_TOOLS)

    def test_the_whole_conversation_is_read_into_spoken_messages(self):
        conversation = [*alternating(("one", "mine one")), {"role": "user", "content": member_line("two")}]
        said = room_voice.said_in_conversation(conversation, room_text.room_messages)
        self.assertEqual([(message.mine, message.text) for message in said],
                         [(False, "one"), (True, "mine one"), (False, "two")])


class FigurativeLanguageIsNotSearched(VoiceTurns):
    def test_an_analogy_in_the_newest_message_turns_search_off_even_in_a_young_room(self):
        self.reply(room_harness.room_said(member_line(METAPHOR)), A_SIDE_WITH_A_REASON)
        self.assertNotIn(room.ROOM_SEARCH, self.runs[0][2].allowed_tools)

    def test_a_plain_fact_question_that_is_not_figurative_may_search(self):
        earlier = alternating(("a", "b"), ("c", "d"))
        self.reply(room_harness.room_said(member_line(ASKED_PLAINLY)), A_SIDE_WITH_A_REASON, history=earlier)
        self.assertIn(room.ROOM_SEARCH, self.runs[0][2].allowed_tools)

    def test_figurative_wording_is_detected(self):
        for text in (METAPHOR, "It is like a negotiator talking a hostage-taker down.", "That is a metaphor for control.",
                     "Imagine the model as a referee.", "As a hostage negotiator would, it reads motives."):
            self.assertTrue(room_voice.is_figurative(text), text)
        for text in ("The lambda threshold should decay.", "Seed the run ten times.", ASKED_PLAINLY):
            self.assertFalse(room_voice.is_figurative(text), text)


class SearchesAreNeverReportedToTheRoom(VoiceTurns):
    def test_the_logged_search_summary_is_asked_for_again_and_a_clean_answer_is_sent(self):
        self.used = (room.ROOM_SEARCH,)
        reply = self.reply(room_harness.room_said(member_line(METAPHOR)), SEARCHED_A_METAPHOR, TAKES_A_SIDE)
        self.assertEqual(reply.answer, TAKES_A_SIDE)
        self.assertIn("results or sources you read", self.runs[1][1][-1]["content"])
        self.assertEqual(self.runs[1][2].allowed_tools, frozenset())

    def test_the_logged_report_on_its_own_search_becomes_pass_when_the_rewrite_passes(self):
        reply = self.reply(room_harness.room_said(member_line("is the ordering stable?")), REPORT_ON_ITS_SEARCH, "PASS")
        self.assertEqual(reply.answer, room_text.PASS)

    def test_a_rewrite_that_still_talks_about_sources_becomes_pass(self):
        reply = self.reply(room_harness.room_said(member_line("is the ordering stable?")), SEARCHED_A_METAPHOR,
                           REPORT_ON_ITS_SEARCH)
        self.assertEqual(reply.answer, room_text.PASS)

    def test_mentions_of_search_results_and_sources_are_detected(self):
        for text in (SEARCHED_A_METAPHOR, REPORT_ON_ITS_SEARCH, "Source 6 says the opposite.", "The search results disagree."):
            self.assertTrue(room_voice.mentions_its_search(text), text)
        for text in ("The source of the drift is the KL term.", "Seed it ten times and report the spread.",
                     "Open source code would settle it.", "The results show beta drifts.", "The provided sources are fine.",
                     "My search of the logs found the bug."):
            self.assertFalse(room_voice.mentions_its_search(text), text)

    def test_the_focus_note_and_chat_note_forbid_talking_about_a_search(self):
        self.assertIn("Never mention search results or sources", room.KIND_NOTES[room_text.CHAT].replace(
            "never mention search results or sources", "Never mention search results or sources"))
        said = room_voice.said_in(room_text.room_messages(room_lines(member_line("a"), member_line("b"), member_line("c"))), ME)
        self.assertIn(room_voice.FOCUS_LOOKUP, room_voice.focus_note(said, ME, True))


class FenceSittingIsRewrittenOnce(VoiceTurns):
    def test_the_logged_hedge_is_rewritten_and_a_side_is_sent(self):
        reply = self.reply(room_harness.room_said(member_line("which constraint wins?")), FENCE_SITTING, A_SIDE_WITH_A_REASON)
        self.assertEqual(reply.answer, A_SIDE_WITH_A_REASON)
        self.assertIn("Pick one side", self.runs[1][1][-1]["content"])

    def test_a_second_hedge_becomes_pass(self):
        reply = self.reply(room_harness.room_said(member_line("which constraint wins?")), FENCE_SITTING, FENCE_SITTING)
        self.assertEqual(reply.answer, room_text.PASS)

    def test_hedge_patterns(self):
        for text in ("The challenge lies in finding balance.", "Finding a balance is the real problem.",
                     "Both points are valid here.", "Both sides are right in their own way.",
                     "The first view is valid, but the second one matters too.",
                     "The threshold idea is valid. But the decay point stands."):
            self.assertTrue(room_voice.is_hedge(text), text)
        for text in (A_SIDE_WITH_A_REASON, "That proof is valid, so lambda must decay.", "Fix beta at 0.3 and rerun with ten seeds.",
                     "Both runs finished in under a minute."):
            self.assertFalse(room_voice.is_hedge(text), text)


class CriticismAimedAtArtyIsAnswered(VoiceTurns):
    def said_after(self, criticism):
        earlier = alternating(("one", "arty one"), ("two", "arty two"))
        return room_harness.room_said(member_line(criticism)), earlier

    def test_the_logged_criticism_is_marked_and_a_reply_that_ignores_it_is_still_sent(self):
        said, earlier = self.said_after(MISAPPLIED)
        reply = self.reply(said, IGNORED_CRITICISM, TAKES_A_SIDE, history=earlier)
        self.assertEqual(reply.answer, IGNORED_CRITICISM)
        self.assertEqual(len(self.runs), 1)
        self.assertIn(room_voice.FOCUS_CRITICISM, self.runs[0][1][-1]["content"])

    def test_a_reply_that_answers_the_criticism_is_kept(self):
        said, earlier = self.said_after(MISAPPLIED)
        reply = self.reply(said, TAKES_A_SIDE, history=earlier)
        self.assertEqual(reply.answer, TAKES_A_SIDE)
        self.assertEqual(len(self.runs), 1)

    def test_criticism_aimed_at_arty_by_name_is_detected(self):
        for text in (MISAPPLIED, "Arty, please stop applying that trigger.", "Arty, you're hallucinating a crisis here.",
                     "Arty: that's wrong, the threshold is fixed.", "arty, you keep repeating yourself.",
                     "Arty, you're circling the same drain."):
            self.assertTrue(room_voice.is_criticism(room_voice.Said("another member", text), ME), text)
        for text in ("Arty, what seeds would you use?", "Arty, can you run it again with beta at 0.3?",
                     "I agree with the ordering."):
            self.assertFalse(room_voice.is_criticism(room_voice.Said("another member", text), ME), text)


class RoomTextStaysClean(unittest.TestCase):
    def test_members_are_never_treated_as_arty(self):
        said = room_voice.said_in(room_text.room_messages(room_lines(member_line("mine"), member_line("theirs"))), ME)
        self.assertEqual([message.speaker for message in room_voice.others_in(said)], ["another member", "another member"])


if __name__ == "__main__":
    unittest.main()
