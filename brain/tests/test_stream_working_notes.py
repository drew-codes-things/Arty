import json
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import keepalive  # noqa: E402

HELLO = [{"role": "user", "content": "hi"}]
SLOW_TURN_SECONDS = 0.35
TEST_EVERY_SECONDS = 0.05


def slow_turn(*_, **__):
    time.sleep(SLOW_TURN_SECONDS)
    return "hello there", [], {}, []


class StreamedChatWorkingNotes(Server):
    def streamed_reply(self):
        with unittest.mock.patch.object(brain, "run_turn", slow_turn), \
                unittest.mock.patch.object(brain, "STREAM_WORKING_EVERY_SECONDS", TEST_EVERY_SECONDS):
            status, body = self.request("POST", "/v1/chat/completions", body={"messages": HELLO, "stream": True})
        self.assertEqual(status, 200)
        return body

    def test_a_streamed_chat_starts_with_a_working_note(self):
        self.assertTrue(self.streamed_reply().startswith(keepalive.WORKING_NOTE))

    def test_a_slow_streamed_chat_keeps_sending_working_notes(self):
        self.assertGreaterEqual(self.streamed_reply().count(keepalive.WORKING_NOTE), 3)

    def test_the_answer_still_arrives_whole_after_the_notes(self):
        lines = [line for line in self.streamed_reply().decode().split("\n\n") if line.startswith("data: ")]
        self.assertEqual(lines[-1], "data: [DONE]")
        text = "".join((json.loads(line[6:])["choices"][0]["delta"].get("content") or "") for line in lines[:-1])
        self.assertEqual(text, "hello there")

    def test_an_arty_cli_agent_turn_gets_working_notes_too(self):
        cli_headers = {"Accept": "text/event-stream", "X-Arty-Client-Tools": "1", "X-Arty-Agent": "1", "X-Arty-Any-Model": "1"}
        with unittest.mock.patch.object(brain, "run_turn", slow_turn), \
                unittest.mock.patch.object(brain, "chosen_model"), \
                unittest.mock.patch.object(brain, "STREAM_WORKING_EVERY_SECONDS", TEST_EVERY_SECONDS):
            status, body = self.request("POST", "/v1/chat/completions", headers=cli_headers,
                                        body={"model": "arty/qwen3:14b", "messages": HELLO, "stream": True})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(keepalive.WORKING_NOTE))
        self.assertGreaterEqual(body.count(keepalive.WORKING_NOTE), 3)

    def test_a_plain_chat_has_no_notes(self):
        with unittest.mock.patch.object(brain, "run_turn", slow_turn):
            status, body = self.request("POST", "/v1/chat/completions", body={"messages": HELLO})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hello there")


if __name__ == "__main__":
    unittest.main()
