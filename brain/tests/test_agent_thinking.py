import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import AGENT_HEADERS, CLIENT_SYSTEM, CONFIG, READ_FILE, WEB_SEARCH  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import keepalive  # noqa: E402
import models  # noqa: E402

PLAN = "The user wants main.py explained, so I should read it first."
ASKED = [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": "what does main.py do?"}]


def model_lines(*pieces):
    lines = [json.dumps({"message": piece}) for piece in pieces] + [json.dumps({"done": True, "done_reason": "stop"})]
    return io.BytesIO("\n".join(lines).encode())


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, request, timeout=None):
        self.bodies.append(json.loads(request.data))
        return self.replies.pop(0)


class ReadingTheModel(unittest.TestCase):
    def test_thinking_is_handed_over_piece_by_piece_and_kept_out_of_the_reply(self):
        model = FakeModel(model_lines({"thinking": "The user "}, {"thinking": "wants a file."}, {"content": "It prints "},
                                      {"content": "hello."}))
        thought, written = [], []
        with unittest.mock.patch.object(brain.urllib.request, "urlopen", side_effect=model):
            raw = brain.stream_chat({"model": "m", "messages": []}, written.append, on_thinking=thought.append)
        self.assertEqual(thought, ["The user ", "wants a file."])
        self.assertEqual(written, ["It prints ", "hello."])
        self.assertEqual(raw["message"], {"role": "assistant", "content": "It prints hello.", "tool_calls": []})

    def test_thinking_is_dropped_when_nobody_asked_for_it(self):
        model = FakeModel(model_lines({"thinking": "hidden"}, {"content": "hello"}))
        written = []
        with unittest.mock.patch.object(brain.urllib.request, "urlopen", side_effect=model):
            raw = brain.stream_chat({"model": "m", "messages": []}, written.append)
        self.assertEqual(written, ["hello"])
        self.assertNotIn("hidden", json.dumps(raw))


class AgentTurnsThatThink(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = {**CONFIG, "conversations_dir": folder.name}
        found = {"name": "qwen3:14b", "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True}
        for patch in [
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: dict(config)),
            unittest.mock.patch.object(brain.hub, "refresh"),
            unittest.mock.patch.object(brain.hub, "tools", [WEB_SEARCH]),
            unittest.mock.patch.object(brain.hub, "routes", {"research__web_search": "research"}),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *rest: answer),
            unittest.mock.patch.object(models, "find", return_value=found),
        ]:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def turn(self, model, messages=ASKED, **wanted):
        with unittest.mock.patch.object(brain.urllib.request, "urlopen", side_effect=model):
            return brain.run_turn(messages, "Arty", client_tools=[READ_FILE], on_text=lambda text: None, agent=True, **wanted)

    def test_an_agent_turn_passes_on_the_reasoning_and_answers_without_it(self):
        thought = []
        model = FakeModel(model_lines({"thinking": PLAN}, {"content": "It prints hello."}))
        answer, _, _, calls = self.turn(model, on_thinking=thought.append)
        self.assertEqual(thought, [PLAN])
        self.assertEqual(answer, "It prints hello.")
        self.assertIsNone(calls)

    def test_reasoning_before_a_tool_call_is_passed_on_and_the_call_still_goes_to_the_client(self):
        thought = []
        call = {"function": {"name": "read_file", "arguments": {"path": "main.py"}}}
        model = FakeModel(model_lines({"thinking": PLAN}, {"tool_calls": [call]}))
        answer, _, _, calls = self.turn(model, on_thinking=thought.append)
        self.assertEqual(thought, [PLAN])
        self.assertEqual([made["function"]["name"] for made in calls], ["read_file"])
        self.assertNotIn(PLAN, answer)

    def test_reasoning_never_goes_back_to_the_model_on_the_next_round(self):
        model = FakeModel(model_lines({"thinking": PLAN}, {"content": ""}), model_lines({"content": "It prints hello."}))
        answer, _, _, _ = self.turn(model, on_thinking=lambda text: None)
        self.assertEqual(answer, "It prints hello.")
        self.assertEqual(len(model.bodies), 2)
        self.assertNotIn(PLAN, json.dumps(model.bodies[1]))

    def test_without_the_clients_own_instructions_reasoning_stays_inside(self):
        thought = []
        model = FakeModel(model_lines({"thinking": "My instructions say: Use your tools."}, {"content": "Hello."}))
        self.turn(model, messages=[{"role": "user", "content": "hi"}], on_thinking=thought.append)
        self.assertEqual(thought, [])

    def test_a_turn_that_did_not_ask_gets_no_reasoning_and_still_answers(self):
        model = FakeModel(model_lines({"thinking": PLAN}, {"content": "It prints hello."}))
        answer, _, _, _ = self.turn(model)
        self.assertEqual(answer, "It prints hello.")


class ReasoningOverHttp(Server):
    def streamed(self, headers, extra=None):
        seen = {}

        def fake_turn(*args, on_text=None, on_thinking=None, **kwargs):
            seen["asked"] = on_thinking is not None
            if on_thinking:
                on_thinking("Let me ")
                on_thinking("look.")
            on_text("It prints hello.")
            return "It prints hello.", [], {}, None
        body = {"model": "Arty", "stream": True, "messages": [{"role": "user", "content": "hi"}], **(extra or {})}
        with unittest.mock.patch.object(brain, "run_turn", side_effect=fake_turn):
            status, raw = self.request("POST", "/v1/chat/completions", headers=headers, body=body)
        self.assertEqual(status, 200)
        chunks = [json.loads(line[6:]) for line in raw.decode().split("\n\n") if line.startswith("data: {")]
        return seen["asked"], [chunk["choices"][0]["delta"] for chunk in chunks], raw

    def test_an_agent_that_asks_gets_reasoning_as_its_own_delta_field_before_the_answer(self):
        asked, deltas, raw = self.streamed({**AGENT_HEADERS, "X-Arty-Reasoning": "1"})
        self.assertTrue(asked)
        self.assertEqual(deltas[:3], [{"reasoning_content": "Let me "}, {"reasoning_content": "look."},
                                      {"role": "assistant", "content": "It prints hello."}])
        self.assertTrue(all("reasoning_content" not in delta or "content" not in delta for delta in deltas))
        self.assertEqual("".join(delta.get("content", "") for delta in deltas), "It prints hello.")
        self.assertTrue(raw.startswith(keepalive.WORKING_NOTE))
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))

    def test_the_request_field_asks_for_it_too(self):
        asked, deltas, _ = self.streamed(AGENT_HEADERS, {"arty_reasoning": True})
        self.assertTrue(asked)
        self.assertIn({"reasoning_content": "look."}, deltas)

    def test_an_agent_that_does_not_ask_gets_none(self):
        asked, deltas, _ = self.streamed(AGENT_HEADERS)
        self.assertFalse(asked)
        self.assertFalse(any("reasoning_content" in delta for delta in deltas))

    def test_asking_outside_agent_mode_gets_none(self):
        for headers in ({"X-Arty-Reasoning": "1"}, {"X-Arty-Client-Tools": "1", "X-Arty-Reasoning": "1"}):
            asked, deltas, _ = self.streamed(headers, {"arty_reasoning": True})
            self.assertFalse(asked)
            self.assertFalse(any("reasoning_content" in delta for delta in deltas))


if __name__ == "__main__":
    unittest.main()
