import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import AGENT_HEADERS, READ_FILE  # noqa: E402
from test_review_brain import Server  # noqa: E402
from test_round_timings import asked  # noqa: E402
from test_window_overflow import WINDOW, with_window  # noqa: E402

import brain  # noqa: E402
import agent_loop  # noqa: E402
import connections  # noqa: E402

REPLY_CAP_NOTE = "I hit my reply length limit"
CUT_TEXT = "Here is the first part of the answer"
AT_WINDOW_END = {"prompt_eval_count": WINDOW - 600, "eval_count": 599}
AT_REPLY_CAP = {"prompt_eval_count": 9000, "eval_count": 2048}


class CutOllama:
    def __init__(self, counts):
        self.counts = counts

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        return {"message": {"role": "assistant", "content": CUT_TEXT}, "done_reason": "length", **self.counts}


def cut_turn(counts, agent=False):
    with unittest.mock.patch.object(connections, "http_json", side_effect=CutOllama(counts)):
        return brain.run_turn(asked("explain the whole codebase"), "Arty", client_tools=[READ_FILE], agent=agent)[0]


class ChatReplies(unittest.TestCase):
    def setUp(self):
        with_window(self)

    def test_a_reply_that_reached_the_reply_cap_says_so(self):
        answer = cut_turn(AT_REPLY_CAP)
        self.assertIn(REPLY_CAP_NOTE, answer)
        self.assertNotIn(agent_loop.WINDOW_END_NOTE.strip(), answer)

    def test_a_reply_that_ran_into_the_end_of_the_window_says_that_instead(self):
        answer = cut_turn(AT_WINDOW_END)
        self.assertTrue(answer.startswith(CUT_TEXT))
        self.assertIn(agent_loop.WINDOW_END_NOTE.strip(), answer)
        self.assertNotIn(REPLY_CAP_NOTE, answer)

    def test_without_ollamas_counts_it_stays_the_reply_cap(self):
        self.assertIn(REPLY_CAP_NOTE, cut_turn({}))


class AgentReplies(unittest.TestCase):
    def setUp(self):
        with_window(self)

    def test_an_agent_reply_at_the_reply_cap_keeps_the_note_its_client_answers(self):
        self.assertIn(REPLY_CAP_NOTE, cut_turn(AT_REPLY_CAP, agent=True))

    def test_an_agent_reply_that_filled_the_window_is_a_window_overflow_so_the_client_compacts(self):
        with self.assertRaises(brain.WindowOverflow) as raised:
            cut_turn(AT_WINDOW_END, agent=True)
        details = raised.exception.details()
        self.assertEqual((details["type"], details["context_tokens"]), ("context_length_exceeded", WINDOW))
        self.assertIn("before the reply finished", details["message"])
        self.assertNotIn("did not read it", details["message"])


class AgentRepliesOverHttp(Server):
    def setUp(self):
        super().setUp()
        with_window(self)

    def test_unstreamed_it_is_a_413(self):
        body = {"model": "Arty", "messages": asked("explain the whole codebase"), "tools": [READ_FILE]}
        with unittest.mock.patch.object(connections, "http_json", side_effect=CutOllama(AT_WINDOW_END)):
            status, raw = self.request("POST", "/v1/chat/completions", headers=AGENT_HEADERS, body=body)
        self.assertEqual((status, json.loads(raw)["error"]["type"]), (413, "context_length_exceeded"))

    def test_streamed_the_cut_text_is_followed_by_the_window_error(self):
        body = {"model": "Arty", "stream": True, "messages": asked("explain the whole codebase"), "tools": [READ_FILE]}
        ollama = CutOllama(AT_WINDOW_END)

        def stream(chat_body, on_text, timeout=600, on_thinking=None):
            reply = ollama(f"{brain.OLLAMA_URL}/api/chat", chat_body)
            on_text(reply["message"]["content"])
            return reply
        with unittest.mock.patch.object(connections, "stream_chat", side_effect=stream), \
                unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            status, raw = self.request("POST", "/v1/chat/completions", headers=AGENT_HEADERS, body=body)
        chunks = [json.loads(line[6:]) for line in raw.decode().split("\n\n") if line.startswith("data: {")]
        said = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)
        self.assertEqual(status, 200)
        self.assertNotIn(REPLY_CAP_NOTE, said)
        self.assertIn("before the reply finished", said)
        self.assertEqual([chunk["error"]["type"] for chunk in chunks if "error" in chunk], ["context_length_exceeded"])
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))


if __name__ == "__main__":
    unittest.main()
