import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import CLIENT_SYSTEM, CONFIG, READ_FILE, WEB_SEARCH, FakeOllama, client_call, client_result  # noqa: E402

import agent_sessions  # noqa: E402
import brain  # noqa: E402
import models  # noqa: E402
import steering  # noqa: E402

CHAT_OPTIONS = {"temperature": 0.4, "num_ctx": 24576, "num_predict": 2048, "repeat_penalty": 1.1}
AGENT_OPTIONS = {"temperature": 0.15, "repeat_penalty": 1.0, "num_predict": 4096}
HARD_REQUEST = "debug why the parser fails on empty input"
PLAIN_REQUEST = "what does main.py do?"
DO_IT_NOW = "Do that now with your tools rather than describing it."
CUT_SHORT = ("Your last reply was cut off at the reply length limit, so nothing in it was used. Carry on from "
             "where you were in smaller steps: write a long file in parts (create it with the first part, "
             "then add the rest with edit_file).")
CLIENT_FOLLOW_UPS = [
    DO_IT_NOW,
    CUT_SHORT,
    "Yes, carry on until the whole job is done; you don't need to ask me before each step.",
    "You said you haven't done that yet. Do it now with your tools, then report what it showed.",
    "Your checklist still has unfinished items:\n[ ] write tests\nCarry on with them now.",
    "A Stop hook asked you to carry on: tests are failing",
    "A SubagentStop hook asked you to carry on: not finished",
    "You haven't changed anything since the plan was approved; the earlier attempts were refused.",
    "You have made the same tool calls several times in a row with the same results.",
    "Before you finish (ArtyCode's built-in rules):\n\nRun the project's tests now.",
    "Carry on with the work from there.",
]


def nudged_mid_task(request, nudge):
    return [{"role": "system", "content": CLIENT_SYSTEM},
            {"role": "user", "content": request},
            client_call("call_1", "read_file", {"path": "parser.py"}),
            client_result("call_1", "1\tdef parse(text):\n2\t    return text.split()"),
            {"role": "assistant", "content": "Next I will fix the parser."},
            {"role": "user", "content": nudge}]


class AgentTurnOptions(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = {**CONFIG, "think": "auto", "conversations_dir": folder.name, "options": dict(CHAT_OPTIONS),
                       "agent_options": dict(AGENT_OPTIONS)}
        patches = [
            unittest.mock.patch.object(brain, "agent_sessions", agent_sessions.AgentSessions()),
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: dict(self.config)),
            unittest.mock.patch.object(brain.hub, "refresh"),
            unittest.mock.patch.object(brain.hub, "tools", [WEB_SEARCH]),
            unittest.mock.patch.object(brain.hub, "routes", {"research__web_search": "research"}),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
            unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *_: answer),
            unittest.mock.patch.object(models, "find", return_value={"name": "qwen3:14b", "billions": 14.8, "tools": True,
                                                                        "vision": False, "embedding": False, "thinking": True}),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def sent(self, messages, agent=True):
        ollama = FakeOllama({"role": "assistant", "content": "Done."})
        with unittest.mock.patch.object(brain, "http_json", side_effect=ollama):
            brain.run_turn(messages, "Arty", client_tools=[READ_FILE], agent=agent)
        return ollama.bodies[0]

    def asked(self, text):
        return [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": text}]

    def test_an_agent_turn_uses_the_agent_options_over_the_chat_options(self):
        options = self.sent(self.asked(PLAIN_REQUEST))["options"]
        self.assertEqual(options, {"temperature": 0.15, "num_ctx": 24576, "num_predict": 4096, "repeat_penalty": 1.0})

    def test_an_agent_turn_keeps_the_reported_context_window(self):
        self.config["agent_options"] = {**AGENT_OPTIONS, "num_ctx": 8192}
        self.assertEqual(self.sent(self.asked(PLAIN_REQUEST))["options"]["num_ctx"], 24576)

    def test_a_chat_turn_keeps_the_chat_options(self):
        self.assertEqual(self.sent(self.asked(PLAIN_REQUEST), agent=False)["options"], CHAT_OPTIONS)

    def test_without_an_agent_block_an_agent_turn_uses_the_chat_options(self):
        del self.config["agent_options"]
        self.assertEqual(self.sent(self.asked(PLAIN_REQUEST))["options"], CHAT_OPTIONS)

    def test_an_agent_turn_always_caps_its_reply_at_the_reported_size(self):
        self.config["options"] = {"num_ctx": 24576}
        del self.config["agent_options"]
        self.assertEqual(self.sent(self.asked(PLAIN_REQUEST))["options"]["num_predict"], models.DEFAULT_REPLY_TOKENS)

    def test_thinking_on_an_agent_turn_never_raises_the_reply_size(self):
        body = self.sent(self.asked(HARD_REQUEST))
        self.assertTrue(body["think"])
        self.assertEqual(body["options"]["num_predict"], 4096)

    def test_thinking_on_a_chat_turn_still_gets_the_thinking_budget(self):
        body = self.sent(self.asked(HARD_REQUEST), agent=False)
        self.assertTrue(body["think"])
        self.assertEqual(body["options"]["num_predict"], steering.THINKING_MIN_TOKENS)

    def test_a_nudge_mid_task_keeps_thinking_on_for_a_hard_job(self):
        self.assertTrue(self.sent(nudged_mid_task(HARD_REQUEST, DO_IT_NOW))["think"])

    def test_a_nudge_mid_task_keeps_thinking_off_for_a_plain_job(self):
        self.assertFalse(self.sent(nudged_mid_task(PLAIN_REQUEST, CUT_SHORT))["think"])

    def test_a_later_request_in_the_same_session_keeps_the_sessions_choice(self):
        messages = nudged_mid_task(HARD_REQUEST, DO_IT_NOW) + [{"role": "assistant", "content": "Fixed."},
                                                               {"role": "user", "content": PLAIN_REQUEST}]
        self.assertTrue(self.sent(messages)["think"])


class ReplySize(unittest.TestCase):
    def test_the_agent_reply_size_comes_from_the_agent_block(self):
        self.assertEqual(models.reply_tokens({"options": CHAT_OPTIONS, "agent_options": AGENT_OPTIONS}), 4096)

    def test_without_an_agent_block_it_is_the_chat_reply_size(self):
        self.assertEqual(models.reply_tokens({"options": CHAT_OPTIONS}), 2048)

    def test_without_any_reply_size_it_falls_back_to_the_brain_default(self):
        self.assertEqual(models.reply_tokens({}), models.DEFAULT_REPLY_TOKENS)
        self.assertEqual(models.reply_tokens({"agent_options": {"num_predict": "lots"}}), models.DEFAULT_REPLY_TOKENS)


if __name__ == "__main__":
    unittest.main()
