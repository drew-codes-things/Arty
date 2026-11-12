import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import models  # noqa: E402

CONFIG = {"model": "qwen3:14b", "persona": "You are Arty.", "system_prompt": "Use your tools.", "options": {},
          "tool_groups": {}, "approval": {}}
CLIENT_SYSTEM = "You are a coding agent working in /home/sam/project."
READ_FILE = {"type": "function", "function": {"name": "read_file", "description": "Read a file.",
                                              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
WEB_SEARCH = {"type": "function", "function": {"name": "research__web_search", "description": "Search the web.",
                                               "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}
AGENT_HEADERS = {"X-Arty-Client-Tools": "1", "X-Arty-Agent": "1"}


def conversation(text):
    return [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": text}]


class FakeOllama:
    def __init__(self, *messages):
        self.replies = list(messages)
        self.bodies = []

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        self.bodies.append(body)
        message = self.replies.pop(0) if self.replies else {"role": "assistant", "content": "done"}
        return {"message": message, "done_reason": "stop"}


class AgentTurns(unittest.TestCase):
    def setUp(self):
        self.ollama = None
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = {**CONFIG, "conversations_dir": folder.name}
        patches = [
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: dict(config)),
            unittest.mock.patch.object(brain.hub, "refresh"),
            unittest.mock.patch.object(brain.hub, "tools", [WEB_SEARCH]),
            unittest.mock.patch.object(brain.hub, "routes", {"research__web_search": "research"}),
            unittest.mock.patch.object(brain.hub, "call", side_effect=AssertionError("the brain ran a tool itself")),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(models, "find", return_value={"name": "qwen3:14b", "billions": 14.8, "tools": True, "vision": False,
                                                                        "embedding": False, "thinking": True}),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def run_agent_turn(self, text, *replies):
        self.ollama = FakeOllama(*replies)
        with unittest.mock.patch.object(brain, "http_json", side_effect=self.ollama):
            return brain.run_turn(conversation(text), "Arty", client_tools=[READ_FILE], agent=True)

    def sent_tool_names(self, index=0):
        return [tool["function"]["name"] for tool in self.ollama.bodies[index].get("tools") or []]

    def test_an_agent_turn_offers_only_the_client_tools(self):
        self.run_agent_turn("what does main.py do?", {"role": "assistant", "content": "It prints hello."})
        self.assertEqual(self.sent_tool_names(), ["read_file"])

    def test_an_agent_turn_uses_the_client_system_prompt_not_arty_s(self):
        self.run_agent_turn("what does main.py do?", {"role": "assistant", "content": "It prints hello."})
        system = self.ollama.bodies[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn(CLIENT_SYSTEM, system["content"])
        self.assertNotIn("Use your tools.", system["content"])

    def test_a_tool_call_written_as_text_is_returned_not_sent_back_for_plain_words(self):
        written = '```json\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```'
        answer, _, _, calls = self.run_agent_turn("read main.py", {"role": "assistant", "content": written})
        self.assertFalse(any("plain words" in json.dumps(body) for body in self.ollama.bodies))
        self.assertIn("read_file", answer)
        self.assertIsNone(calls)

    def test_a_reply_without_a_tool_call_is_never_replaced_by_a_web_search(self):
        reply = "Could you clarify which file you mean?"
        answer, used, _, _ = self.run_agent_turn("search the code for the parser", {"role": "assistant", "content": reply},
                                                 {"role": "assistant", "content": reply})
        self.assertNotIn("research__web_search", used)
        self.assertIn("clarify", answer)

    def test_every_tool_call_goes_back_to_the_client(self):
        call = {"function": {"name": "research__web_search", "arguments": {"query": "x"}}}
        _, _, _, calls = self.run_agent_turn("find x", {"role": "assistant", "content": "", "tool_calls": [call]})
        self.assertEqual([c["function"]["name"] for c in calls], ["research__web_search"])

    def test_chat_shortcuts_and_memory_are_skipped(self):
        answer, used, _, _ = self.run_agent_turn("remember that the tests live in tests/",
                                                 {"role": "assistant", "content": "Noted for this session."})
        self.assertEqual(answer, "Noted for this session.")
        self.assertNotIn("memory__add_observations", used)
        brain.archive_turn.assert_not_called()
        brain.extract_memories.assert_not_called()

    def test_ordinary_chat_still_offers_arty_s_own_tools(self):
        self.ollama = FakeOllama({"role": "assistant", "content": "It prints hello."})
        with unittest.mock.patch.object(brain, "http_json", side_effect=self.ollama), \
                unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)), \
                unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *_: answer):
            brain.run_turn(conversation("what does main.py do?"), "Arty", client_tools=[READ_FILE])
        self.assertIn("research__web_search", self.sent_tool_names())


def client_call(call_id, name, arguments):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def client_result(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


SENT_BACK_FOR_HISTORY = ("Before you finish (ArtyCode's built-in rules):\n\nNow append one entry for this task to the end of "
                         ".arty/history.md.")
FINISHED_AFTER_SEND_BACK = [
    {"role": "system", "content": CLIENT_SYSTEM},
    {"role": "user", "content": "add a function that reverses the order of the words in a string to utils.py, with a test"},
    client_call("call_1", "read_file", {"path": "utils.py"}),
    client_result("call_1", "1\tdef add(first, second):\n2\t    return first + second"),
    client_call("call_2", "edit_file", {"path": "utils.py", "old_text": "x", "new_text": "y"}),
    client_result("call_2", "Error: old_text was not found; read the file again and copy the text exactly"),
    client_call("call_3", "edit_file", {"path": "utils.py", "old_text": "x", "new_text": "y"}),
    client_result("call_3", "Edited utils.py"),
    client_call("call_4", "edit_file", {"path": "tests/test_utils.py", "old_text": "x", "new_text": "y"}),
    client_result("call_4", "Edited tests/test_utils.py"),
    client_call("call_5", "run_command", {"command": "python3 -m pytest tests/test_utils.py"}),
    client_result("call_5", "5 passed in 0.01s\n[exit code 0]"),
    {"role": "assistant", "content": "Added `reverse_words` to utils.py and tests for it. All 5 tests pass."},
    {"role": "user", "content": SENT_BACK_FOR_HISTORY},
    client_call("call_6", "read_file", {"path": ".arty/history.md"}),
    client_result("call_6", "Error: .arty/history.md is not a file"),
    client_call("call_7", "write_file", {"path": ".arty/history.md", "content": "entry"}),
    client_result("call_7", "Created .arty/history.md (9 lines)"),
]
FINAL_REPORT = ("Added `reverse_words` to utils.py and a test class for it. All 5 tests pass with python3 -m pytest. "
                "The history entry is in .arty/history.md.")


REPORTED_EDIT = "Added `reverse_words` to utils.py and a test class for it."
REPORTED_TESTS = "All 5 tests pass with python3 -m pytest."
NOTHING_RAN = "No tool ran that would have done so."


class JudgeThatFlagsEveryReport:
    def __init__(self, answer):
        self.answer = answer
        self.check_prompts = []

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        if body.get("format") != "json":
            return {"message": {"role": "assistant", "content": self.answer}, "done_reason": "stop"}
        prompt = body["messages"][0]["content"]
        if prompt.startswith("The person said"):
            return {"message": {"content": json.dumps({"kind": "A"})}}
        self.check_prompts.append(prompt)
        claims = [{"text": sentence, "correction": NOTHING_RAN} for sentence in (REPORTED_EDIT, REPORTED_TESTS)]
        return {"message": {"content": json.dumps({"false_claims": claims})}}


class AgentHonesty(unittest.TestCase):
    setUp = AgentTurns.setUp

    def finish(self, conversation_so_far):
        judge = JudgeThatFlagsEveryReport(FINAL_REPORT)
        with unittest.mock.patch.object(brain, "http_json", side_effect=judge):
            reply, _, _, calls = brain.run_turn(conversation_so_far, "Arty", client_tools=[READ_FILE], agent=True)
        self.assertIsNone(calls)
        return reply, judge

    def test_work_the_client_did_before_a_sent_back_note_still_counts(self):
        reply, judge = self.finish(FINISHED_AFTER_SEND_BACK)
        self.assertEqual(reply, FINAL_REPORT)
        self.assertEqual(judge.check_prompts, [])

    def test_a_report_straight_after_a_sent_back_note_keeps_the_work_before_it(self):
        note = FINISHED_AFTER_SEND_BACK.index({"role": "user", "content": SENT_BACK_FOR_HISTORY})
        reply, judge = self.finish(FINISHED_AFTER_SEND_BACK[:note + 1])
        self.assertEqual(reply, FINAL_REPORT)
        self.assertEqual(judge.check_prompts, [])

    def test_a_report_with_no_client_tool_run_is_still_corrected(self):
        reply, judge = self.finish(FINISHED_AFTER_SEND_BACK[:2])
        self.assertNotIn(REPORTED_EDIT, reply)
        self.assertIn(NOTHING_RAN, reply)
        self.assertIn("- none", judge.check_prompts[0])

    def test_a_report_after_every_client_tool_failed_is_still_corrected(self):
        failed = FINISHED_AFTER_SEND_BACK[:2] + [
            client_call("call_1", "edit_file", {"path": "utils.py", "old_text": "x", "new_text": "y"}),
            client_result("call_1", "Error: old_text was not found; read the file again and copy the text exactly")]
        reply, judge = self.finish(failed)
        self.assertNotIn(REPORTED_EDIT, reply)
        self.assertIn("- edit_file: failed (Error: old_text was not found", judge.check_prompts[0])


class AgentHeader(Server):
    def captured_agent_flag(self, headers):
        seen = {}

        def fake_turn(*args, **kwargs):
            seen.update(kwargs)
            return "ok", [], {}, None
        with unittest.mock.patch.object(brain, "run_turn", side_effect=fake_turn):
            status, _ = self.request("POST", "/v1/chat/completions", headers=headers,
                                     body={"model": "Arty", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        return seen.get("agent")

    def test_the_agent_header_with_client_tools_turns_agent_mode_on(self):
        self.assertTrue(self.captured_agent_flag(AGENT_HEADERS))

    def test_the_agent_header_alone_is_ignored(self):
        self.assertFalse(self.captured_agent_flag({"X-Arty-Agent": "1"}))

    def test_ordinary_requests_are_not_agent_turns(self):
        self.assertFalse(self.captured_agent_flag({"X-Arty-Client-Tools": "1"}))


class LastResultCounts(unittest.TestCase):
    def results(self, *outcomes):
        turn = [{"role": "user", "content": "fix it"}]
        for outcome in outcomes:
            turn.append({"role": "tool", "tool_name": "edit_file", "content": outcome})
        return brain.client_tool_runs(turn, by_last_result=True)

    def test_a_tool_that_failed_then_worked_is_not_reported_as_failed(self):
        used, failed = self.results("Error: old text not found", "Edited calc/ops.py")
        self.assertEqual(used, ["edit_file", "edit_file"])
        self.assertEqual(failed, [])

    def test_a_tool_whose_last_run_failed_is_still_reported(self):
        _, failed = self.results("Edited calc/ops.py", "Error: old text not found")
        self.assertEqual([name for name, _ in failed], ["edit_file"])


if __name__ == "__main__":
    unittest.main()
