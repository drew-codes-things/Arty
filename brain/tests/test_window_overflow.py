import io
import json
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import AGENT_HEADERS, READ_FILE, FakeOllama  # noqa: E402
from test_review_brain import Server  # noqa: E402
from test_round_timings import asked, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import keepalive  # noqa: E402
import models  # noqa: E402

WINDOW = 24576
LLAMA_SERVER_OVERFLOW = "request (30120 tokens) exceeds the available context size (24576 tokens), try increasing it"
OLLAMA_OVERFLOW = ("the prompt is longer than the context length currently available to the model; shorten the prompt, "
                   "adjust the context length in settings, or use a model with a longer context length")


def ollama_refusal(message, status=400):
    body = io.BytesIO(json.dumps({"error": message}).encode())
    return urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", status, "Bad Request", {}, body)


def with_window(case):
    offline_turns(case)
    patched = {**connections.load_config(), "options": {"num_ctx": WINDOW}}
    unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(patched)).start()


class RequestFields(unittest.TestCase):
    def setUp(self):
        with_window(self)

    def first_body(self, agent):
        ollama = FakeOllama()
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            brain.run_turn(asked("what does main.py do?"), "Arty", client_tools=[READ_FILE], agent=agent)
        return ollama.bodies[0]

    def test_an_agent_turn_asks_ollama_not_to_drop_old_messages_or_shift_the_window(self):
        body = self.first_body(agent=True)
        self.assertIs(body["truncate"], False)
        self.assertIs(body["shift"], False)

    def test_a_chat_turn_keeps_ollamas_message_trimming_but_never_shifts(self):
        body = self.first_body(agent=False)
        self.assertNotIn("truncate", body)
        self.assertIs(body["shift"], False)

    def test_every_load_asks_for_the_same_shift_setting_so_ollama_never_reloads_for_it(self):
        self.assertIs(models.for_runner({"options": {"num_ctx": WINDOW}}, {"model": "qwen3:14b"})["shift"], False)
        sent = []
        with unittest.mock.patch.object(connections, "http_json", side_effect=lambda url, body=None, **_: sent.append(body) or {}), \
                unittest.mock.patch.object(models, "make_room", return_value=[]):
            model_choice.reload_main_model(connections.load_config()).join(5)
        self.assertIs(sent[0]["shift"], False)

    def test_a_solo_model_is_loaded_with_the_same_window_and_shift(self):
        sent = []
        with unittest.mock.patch.object(connections, "http_json",
                                        side_effect=lambda url, body=None, **_: sent.append(body) or {"message": {"content": "hi"}}):
            answer = brain.solo_turn("solo-model:7b", [{"role": "user", "content": "hello"}])[0]
        self.assertEqual(answer, "hi")
        self.assertEqual((sent[0]["model"], sent[0]["options"]["num_ctx"], sent[0]["shift"]), ("solo-model:7b", WINDOW, False))


class OverflowOverHttp(Server):
    def setUp(self):
        super().setUp()
        with_window(self)

    def post(self, error, headers=AGENT_HEADERS, stream=False):
        body = {"model": "Arty", "stream": stream, "messages": asked("what does main.py do?"), "tools": [READ_FILE]}
        refuse = unittest.mock.Mock(side_effect=error)
        with unittest.mock.patch.object(connections, "http_json", refuse), unittest.mock.patch.object(connections, "stream_chat", refuse):
            return self.request("POST", "/v1/chat/completions", headers=headers, body=body)

    def test_an_agent_conversation_longer_than_the_window_is_a_413_naming_the_window(self):
        status, raw = self.post(ollama_refusal(LLAMA_SERVER_OVERFLOW))
        error = json.loads(raw)["error"]
        self.assertEqual(status, 413)
        self.assertEqual(error["type"], "context_length_exceeded")
        self.assertEqual((error["code"], error["context_tokens"], error["model"]), (413, WINDOW, "qwen3:14b"))
        self.assertIn(str(WINDOW), error["message"])

    def test_ollamas_own_wording_counts_too(self):
        status, raw = self.post(ollama_refusal(OLLAMA_OVERFLOW))
        self.assertEqual((status, json.loads(raw)["error"]["type"]), (413, "context_length_exceeded"))

    def test_a_streamed_agent_turn_says_so_in_the_stream_and_ends_it_properly(self):
        status, raw = self.post(ollama_refusal(LLAMA_SERVER_OVERFLOW), stream=True)
        self.assertEqual(status, 200)
        chunks = [json.loads(line[6:]) for line in raw.decode().split("\n\n") if line.startswith("data: {")]
        said = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)
        errors = [chunk["error"] for chunk in chunks if "error" in chunk]
        self.assertIn(str(WINDOW), said)
        self.assertNotIn(brain.GENERIC_ERROR, said)
        self.assertEqual([(e["type"], e["context_tokens"]) for e in errors], [("context_length_exceeded", WINDOW)])
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(raw.startswith(keepalive.WORKING_NOTE))
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))

    def test_other_ollama_refusals_stay_ordinary_errors(self):
        status, raw = self.post(ollama_refusal("model 'nope' not found", status=404))
        self.assertEqual(status, 500)
        self.assertNotIn("context_length_exceeded", raw.decode())


if __name__ == "__main__":
    unittest.main()
