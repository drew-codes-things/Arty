import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402

HELLO = [{"role": "user", "content": "hi"}]


class ChatClientName(Server):
    def turn_client(self, body):
        recorded = {}

        def fake_turn(*_, **named):
            recorded["client"] = named.get("client")
            return "hello", [], {}, []

        with unittest.mock.patch.object(brain, "run_turn", fake_turn):
            status, _ = self.request("POST", "/v1/chat/completions", body={"messages": HELLO, **body})
        self.assertEqual(status, 200)
        return recorded["client"]

    def test_a_chat_passes_on_the_app_that_sent_it(self):
        self.assertEqual(self.turn_client({"client": "desktop"}), "desktop")

    def test_a_streamed_chat_passes_on_the_app_too(self):
        self.assertEqual(self.turn_client({"client": "telegram", "stream": True}), "telegram")

    def test_no_client_is_the_shared_one(self):
        self.assertEqual(self.turn_client({}), "")

    def test_a_strange_client_name_is_cleaned(self):
        self.assertEqual(self.turn_client({"client": " Voice<script> " + "x" * 80}), "voicescriptxxxxxxxxxxxxxxxxxxxxx")
        self.assertEqual(self.turn_client({"client": ["desktop"]}), "")


if __name__ == "__main__":
    unittest.main()
