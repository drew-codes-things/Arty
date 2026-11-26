import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_room  # noqa: E402
import room_harness  # noqa: E402

import agent_loop  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402
import room  # noqa: E402
import room_models  # noqa: E402
import room_notes  # noqa: E402
import room_voice  # noqa: E402
from test_room_voice import member_line, own_turns, room_lines  # noqa: E402

THREE_MEMBERS = ("The lambda threshold should decay with a KL penalty.", "I would fix beta at 0.3 first.",
                 "Ten seeds, then we compare the spread.")
FACT_ASKED = (*THREE_MEMBERS[:2], "How many seeds did the first paper use?")
PLAIN = "Fix beta at 0.3 and run ten seeds before changing lambda."
SUMMARY = "AI can simulate emotion through affective computing and pattern analysis of beta and seeds."
SEARCH = "research__web_search"


class RealAgentLoop(unittest.TestCase):
    def run_turn(self, members, replies, history=()):
        engine = test_room.FakeEngine([test_room.said(text) for text in [*replies, *(["PASS"] * 8)]])
        hub = test_room.FakeHub("1. Emotion AI overview -> https://example.org/a")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = {"model": room_harness.MAIN, "persona": "p", "system_prompt": "s", "assistant_name": "Arty", "lookups": [],
                  "options": {"num_ctx": 24576}, "coding_model": room_harness.MAIN, "arty_models": [room_harness.MAIN],
                  "hidden_tools": [], "tool_groups": {}}
        native = {"calculate": brain.NATIVE_TOOLS["calculate"]}
        patches = [
            unittest.mock.patch.object(models, "installed", return_value=room_harness.ENGINE_MODELS),
            unittest.mock.patch.object(models, "is_loaded", return_value=True),
            unittest.mock.patch.object(room_notes, "STATE_FILE", Path(folder.name) / "room-notes.json"),
            unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)),
            unittest.mock.patch.object(connections, "hub", hub),
            unittest.mock.patch.object(connections, "http_json", side_effect=engine),
            unittest.mock.patch.object(room.honesty, "be_honest", side_effect=lambda answer, *rest, **named: answer),
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0),
            unittest.mock.patch.object(brain.models, "find", return_value=None),
            unittest.mock.patch.object(models, "make_room", return_value=[], create=True),
            unittest.mock.patch.object(model_choice, "reload_main_model"),
            unittest.mock.patch.object(agent_loop, "NATIVE_TOOLS", native),
            unittest.mock.patch.object(agent_loop, "current_context", return_value=""),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        said = room_harness.room_said(room_lines(*(member_line(text) for text in members)))
        reply = room.room_turn([*history, {"role": "user", "content": said}])
        return reply, engine, hub

    def test_a_mid_discussion_turn_never_makes_the_brain_search(self):
        for members in (THREE_MEMBERS, FACT_ASKED):
            reply, engine, hub = self.run_turn(members, [PLAIN, PLAIN, SUMMARY, "PASS", "PASS", "PASS"])
            self.assertEqual(hub.calls, [], members)
            self.assertEqual(reply.tools_used, [], members)

    def test_a_member_saying_search_or_research_does_not_make_the_brain_search_for_a_turn_without_the_tool(self):
        asking = (*THREE_MEMBERS[:2], "Did you search or research what is the best beta, set a value?")
        reply, engine, hub = self.run_turn(asking, ["I would not move beta.", "I would not move beta.", SUMMARY])
        self.assertEqual(hub.calls, [])

    def test_a_mid_discussion_turn_without_a_fact_is_not_offered_the_search(self):
        reply, engine, hub = self.run_turn(THREE_MEMBERS, [PLAIN])
        offered = [tool["function"]["name"] for tool in engine.bodies[0].get("tools") or []]
        self.assertNotIn(SEARCH, offered)

    def test_the_block_wording_never_asks_the_agent_loop_for_a_search(self):
        reply, engine, hub = self.run_turn(THREE_MEMBERS, [SUMMARY, "PASS", "PASS", "PASS"])
        self.assertEqual(hub.calls, [])
        self.assertNotIn(SEARCH, [tool["function"]["name"] for body in engine.bodies for tool in body.get("tools") or []])

    def test_the_rewrite_reuses_the_first_model_pick(self):
        history = own_turns(SUMMARY)
        with unittest.mock.patch.object(room_models, "choose", wraps=room_models.choose) as choose:
            self.run_turn(THREE_MEMBERS, [SUMMARY, PLAIN], history)
        self.assertEqual(choose.call_count, 1)


class MidDiscussionCount(unittest.TestCase):
    def test_the_count_is_room_messages_not_speaker_paragraphs(self):
        self.assertFalse(room_voice.is_mid_discussion([room_voice.Said("a", "x")], 1))
        self.assertTrue(room_voice.is_mid_discussion([room_voice.Said("a", "x")], 3))


if __name__ == "__main__":
    unittest.main()
