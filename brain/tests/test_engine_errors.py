import io
import json
import os
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, calls, run_agent, said  # noqa: E402

ENGINE_FAILURE = "failed to parse the tool call arguments"
EMPTY_CALL = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "workspace__run_command", "arguments": {}}}]}
CALL_RESULT = {"role": "tool", "tool_name": "workspace__run_command", "content": "error: no command given"}


def engine_failure(status=500):
    body = io.BytesIO(json.dumps({"error": ENGINE_FAILURE}).encode())
    return urllib.error.HTTPError("http://127.0.0.1:11438/api/chat", status, "Internal Server Error", {}, body)


class FailingEngine:
    def __init__(self, failures, reply="Done."):
        self.failures = failures
        self.reply = reply
        self.chat_bodies = []

    def __call__(self, url, body=None, **kwargs):
        if body.get("_debug_render_only"):
            return {"_debug_info": {"rendered_template": "<|im_start|>assistant\n"}}
        if body.get("format") == "json":
            return {"message": {"role": "assistant", "content": "{}"}}
        self.chat_bodies.append(body)
        if len(self.chat_bodies) <= self.failures:
            raise engine_failure()
        return said(self.reply)


def answered(engine, messages):
    try:
        outcome, _ = run_agent(engine, messages, hub=FakeHub())
    except urllib.error.HTTPError as error:
        raise AssertionError(f"the engine error ended the turn: {error!r}") from None
    return outcome


APPEND = ("notes__append_line", {"line": "milk"})


class ScriptedEngine:
    def __init__(self, script):
        self.script = list(script)
        self.chat_bodies = []

    def __call__(self, url, body=None, **kwargs):
        if body.get("_debug_render_only"):
            return {"_debug_info": {"rendered_template": "<|im_start|>assistant\n"}}
        if body.get("format") == "json":
            return {"message": {"role": "assistant", "content": "{}"}}
        self.chat_bodies.append(body)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def turn_with(script, request="add milk to my notes"):
    engine = ScriptedEngine(script)
    try:
        outcome, hub = run_agent(engine, [{"role": "user", "content": request}], hub=FakeHub((APPEND[0], "workspace__read_file")))
    except urllib.error.HTTPError as error:
        raise AssertionError(f"the engine error ended the turn: {error!r}") from None
    return outcome, hub, engine


class ToolsNeverRunTwiceAcrossAFault(unittest.TestCase):
    def test_a_tool_that_ran_before_a_fault_runs_exactly_once(self):
        outcome, hub, _ = turn_with([calls(APPEND), engine_failure(503), calls(APPEND), said("Added milk to your notes.")])
        self.assertEqual(1, [name for name, _ in hub.calls].count(APPEND[0]))
        self.assertEqual("done", outcome["status"])

    def test_the_failure_answer_names_the_tools_that_ran_and_their_results(self):
        outcome, hub, _ = turn_with([calls(APPEND), engine_failure(500), engine_failure(500)])
        self.assertEqual(1, len(hub.calls))
        self.assertIn(APPEND[0], outcome["answer"])
        self.assertIn("ok", outcome["answer"])
        self.assertNotIn("try again", outcome["answer"])

    def test_a_fault_before_any_tool_ran_is_retried_with_the_same_body(self):
        outcome, hub, engine = turn_with([engine_failure(503), said("A closure keeps the variables it was made with.")],
                                         request="what is a closure in python?")
        self.assertEqual("A closure keeps the variables it was made with.", outcome["answer"])
        self.assertEqual(engine.chat_bodies[0]["messages"], engine.chat_bodies[1]["messages"])
        self.assertEqual([], hub.calls)

    def test_an_unlock_or_a_call_to_a_tool_not_offered_is_not_an_action_and_the_reply_is_retried(self):
        for step in (calls(("use_tools", {"groups": ["notes"]})), calls(("homelab__delete_everything", {}))):
            with self.subTest(step=step["message"]["tool_calls"][0]["function"]["name"]):
                outcome, hub, engine = turn_with([step, engine_failure(503), said("Nothing to add yet.")])
                self.assertEqual("Nothing to add yet.", outcome["answer"])
                self.assertEqual(engine.chat_bodies[1]["messages"], engine.chat_bodies[2]["messages"])
                self.assertEqual([], hub.calls)

    def test_the_failure_answer_lists_only_tools_that_acted(self):
        outcome, _, _ = turn_with([calls(("use_tools", {"groups": ["notes"]})), calls(("homelab__delete_everything", {})),
                                   calls(APPEND), engine_failure(500)])
        self.assertIn(APPEND[0], outcome["answer"])
        self.assertNotIn("use_tools", outcome["answer"])
        self.assertNotIn("homelab__delete_everything", outcome["answer"])

    def test_raw_engine_error_text_is_not_shown(self):
        page = urllib.error.HTTPError("http://127.0.0.1:11438/api/chat", 502, "Bad Gateway", {},
                                      io.BytesIO(json.dumps({"error": "<html><body><h1>502 Bad Gateway</h1></body></html>"}).encode()))
        outcome, _, _ = turn_with([calls(APPEND), page])
        self.assertIn(APPEND[0], outcome["answer"])
        self.assertNotIn("<", outcome["answer"])


class EngineErrorsDuringTheReply(unittest.TestCase):
    def test_an_engine_error_after_a_tool_result_is_not_retried(self):
        engine = FailingEngine(failures=1)
        outcome = answered(engine, [{"role": "user", "content": "list the files in my workspace"}, EMPTY_CALL, CALL_RESULT])
        self.assertEqual("done", outcome["status"])
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertIn("workspace__run_command: error: no command given", outcome["answer"])
        self.assertIn(ENGINE_FAILURE, outcome["answer"])

    def test_an_engine_error_on_the_retry_too_answers_that_the_model_failed(self):
        engine = FailingEngine(failures=2)
        outcome = answered(engine, [{"role": "user", "content": "what is a closure in python?"}])
        self.assertEqual("done", outcome["status"])
        self.assertIn("try again", outcome["answer"])
        self.assertEqual(2, len(engine.chat_bodies))


if __name__ == "__main__":
    unittest.main()
