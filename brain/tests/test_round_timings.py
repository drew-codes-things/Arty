import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import AGENT_HEADERS, CLIENT_SYSTEM, CONFIG, READ_FILE  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import agent_turns  # noqa: E402
import connections  # noqa: E402
import models  # noqa: E402

WARM = {"load_duration": 4_000_000, "prompt_eval_count": 9000, "prompt_eval_duration": 3_000_000_000,
        "eval_count": 120, "eval_duration": 2_500_000_000}
COLD = {**WARM, "load_duration": 6_000_000_000}
CACHED = {**WARM, "prompt_eval_cached_count": 8500}
QWEN = {"name": "qwen3:14b", "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True}


class TimedOllama:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        self.bodies.append(body)
        message, timings = self.replies.pop(0) if self.replies else ({"role": "assistant", "content": "done"}, WARM)
        return {"message": message, "done_reason": "stop", **timings}

    def streamed(self, body, on_text, timeout=600, on_thinking=None):
        reply = self(f"{brain.OLLAMA_URL}/api/chat", body)
        if reply["message"]["content"]:
            on_text(reply["message"]["content"])
        return reply


def answer(text, timings=WARM):
    return {"role": "assistant", "content": text}, timings


def asked(text):
    return [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": text}]


def offline_turns(case):
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    config = {**CONFIG, "conversations_dir": folder.name}
    patches = [
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)),
        unittest.mock.patch.object(connections.hub, "refresh"),
        unittest.mock.patch.object(connections.hub, "tools", []),
        unittest.mock.patch.object(connections.hub, "routes", {}),
        unittest.mock.patch.object(brain, "archive_turn"),
        unittest.mock.patch.object(brain, "extract_memories"),
        unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
        unittest.mock.patch.object(brain, "be_honest", side_effect=lambda text, *_: text),
        unittest.mock.patch.object(agent_turns, "be_honest", side_effect=lambda text, *_: text),
        unittest.mock.patch.object(models, "find", return_value=QWEN),
    ]
    for patch in patches:
        patch.start()
    case.addCleanup(unittest.mock.patch.stopall)


class RoundTimings(unittest.TestCase):
    def setUp(self):
        offline_turns(self)

    def rounds(self, *replies, agent=True):
        ollama = TimedOllama(*replies)
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            _, _, raw, _ = brain.run_turn(asked("what does main.py do?"), "Arty", client_tools=[READ_FILE], agent=agent)
        return brain.turn_rounds(raw)

    def test_each_model_round_of_an_agent_turn_reports_its_ollama_timings(self):
        rounds = self.rounds(answer("It prints hello."))
        self.assertEqual(rounds, [{"model": "qwen3:14b", "load_duration": 4_000_000, "prompt_eval_count": 9000,
                                   "prompt_eval_duration": 3_000_000_000, "eval_count": 120,
                                   "eval_duration": 2_500_000_000, "loaded_fresh": False, "think": False}])

    def test_each_round_says_whether_the_brain_asked_the_model_to_think(self):
        for decided in (True, False):
            with self.subTest(decided=decided), unittest.mock.patch("agent_loop.thinking_for", return_value=decided):
                self.assertEqual([entry["think"] for entry in self.rounds(answer("", COLD), answer("It prints hello."))],
                                 [decided, decided])

    def test_the_thinking_decision_stays_with_the_job_after_command_output_and_after_compaction(self):
        job = "Debug why the parser drops the last token, step by step."
        shapes = ([job], [job, "(I ran `pytest -q` myself. Output:)\nwhy does this fail\n1 failed"],
                  [f"The conversation was squeezed, so here is where it stands.\n\nSummary: read two files.\n\nThe request:\n{job}"])
        decisions = []
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        thinking_config = {**CONFIG, "think": "auto", "conversations_dir": folder.name}
        for texts in shapes:
            ollama = TimedOllama(answer("Found it."))
            messages = [{"role": "system", "content": CLIENT_SYSTEM}] + [{"role": "user", "content": text} for text in texts]
            with unittest.mock.patch.object(connections, "http_json", side_effect=ollama), \
                    unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(thinking_config)):
                _, _, raw, _ = brain.run_turn(messages, "Arty", client_tools=[READ_FILE], agent=True)
            decisions.append(brain.turn_rounds(raw)[0]["think"])
        self.assertEqual(decisions, [True, True, True])

    def test_a_round_that_waited_for_a_load_is_marked_fresh(self):
        self.assertTrue(self.rounds(answer("It prints hello.", COLD))[0]["loaded_fresh"])

    def test_every_round_is_listed_in_order_when_the_brain_sends_the_model_back(self):
        rounds = self.rounds(answer("", COLD), answer("It prints hello."))
        self.assertEqual([entry["loaded_fresh"] for entry in rounds], [True, False])

    def test_the_cached_part_of_the_prompt_is_passed_on_when_ollama_reports_it(self):
        self.assertEqual(self.rounds(answer("It prints hello.", CACHED))[0]["prompt_eval_cached_count"], 8500)

    def test_missing_timings_count_as_zero(self):
        rounds = self.rounds(answer("It prints hello.", {}))
        self.assertEqual((rounds[0]["load_duration"], rounds[0]["eval_count"], rounds[0]["loaded_fresh"]), (0, 0, False))

    def test_chat_turns_record_no_rounds(self):
        self.assertEqual(self.rounds(answer("It prints hello."), agent=False), [])


class RoundTimingsOverHttp(Server):
    def setUp(self):
        super().setUp()
        offline_turns(self)

    def usage(self, headers, stream=False):
        ollama = TimedOllama(answer("", COLD), answer("It prints hello."))
        body = {"model": "Arty", "stream": stream, "messages": asked("what does main.py do?"), "tools": [READ_FILE]}
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama), \
                unittest.mock.patch.object(connections, "stream_chat", side_effect=ollama.streamed):
            status, raw = self.request("POST", "/v1/chat/completions", headers=headers, body=body)
        self.assertEqual(status, 200)
        if not stream:
            return json.loads(raw)["usage"]
        chunks = [json.loads(line[6:]) for line in raw.decode().split("\n\n") if line.startswith("data: {")]
        return next(chunk["usage"] for chunk in chunks if "usage" in chunk)

    def test_an_agent_reply_carries_its_rounds_beside_the_usual_counts(self):
        usage = self.usage(AGENT_HEADERS)
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]), (9000, 120, 9120))
        self.assertEqual([entry["loaded_fresh"] for entry in usage["rounds"]], [True, False])

    def test_a_streamed_agent_reply_carries_them_in_its_last_chunk(self):
        usage = self.usage(AGENT_HEADERS, stream=True)
        self.assertEqual(len(usage["rounds"]), 2)
        self.assertEqual(usage["rounds"][0]["load_duration"], 6_000_000_000)

    def test_an_ordinary_chat_reply_keeps_the_usual_counts_only(self):
        usage = self.usage({})
        self.assertNotIn("rounds", usage)


if __name__ == "__main__":
    unittest.main()
