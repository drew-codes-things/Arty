import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import READ_FILE, FakeOllama  # noqa: E402
from test_round_timings import offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import window_fit  # noqa: E402

WINDOW = 24576
LONG_TEXT = "word " * 400
CONFIG = {"options": {"num_ctx": WINDOW}}


def long_chat(pairs, newest="what should I cook tonight?"):
    messages = []
    for index in range(pairs):
        messages.append({"role": "user", "content": f"question {index}: {LONG_TEXT}"})
        messages.append({"role": "assistant", "content": f"reply {index}: {LONG_TEXT}"})
    return messages + [{"role": "user", "content": newest}]


def tool_round(index):
    call = {"function": {"name": "workspace__read_file", "arguments": {"path": f"file{index}.txt"}}}
    return [{"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "content": f"contents {index}: {LONG_TEXT}"}]


def texts(messages):
    return [str(message.get("content")) for message in messages]


def contains(messages, start):
    return any(text.startswith(start) or f"\n\n{start}" in text for text in texts(messages))


class FittingMessages(unittest.TestCase):
    def body(self, messages, num_predict=2048, tools=()):
        return {"model": "qwen3:14b", "messages": messages, "options": {"num_predict": num_predict}, "tools": list(tools)}

    def test_a_conversation_that_fits_is_sent_unchanged(self):
        body = self.body([{"role": "system", "content": "be kind"}] + long_chat(3))
        self.assertEqual(window_fit.fit_to_window(CONFIG, body), (body, 0))

    def test_a_long_conversation_keeps_the_system_message_the_newest_request_and_the_newest_turns(self):
        body = self.body([{"role": "system", "content": "be kind"}] + long_chat(120))
        fitted, left_out = window_fit.fit_to_window(CONFIG, body)
        sent = fitted["messages"]
        self.assertGreater(left_out, 0)
        self.assertEqual(sent[0], {"role": "system", "content": "be kind"})
        self.assertEqual(sent[1], {"role": "user", "content": window_fit.LEFT_OUT_NOTE})
        self.assertTrue(sent[-1]["content"].startswith("what should I cook tonight?"))
        self.assertTrue(contains(sent, "reply 119:"))
        self.assertFalse(contains(sent, "question 0:"))
        self.assertEqual(len(sent), len(body["messages"]) - left_out + 1)

    def test_the_newest_request_says_not_to_guess_at_what_was_left_out(self):
        body = self.body([{"role": "system", "content": "be kind"}] + long_chat(120, newest="what was my locker number?"))
        sent = window_fit.fit_to_window(CONFIG, body)[0]["messages"]
        self.assertEqual(sent[-1]["content"], "what was my locker number?" + window_fit.LEFT_OUT_ASK)
        self.assertIn("don't guess", window_fit.LEFT_OUT_ASK)
        self.assertEqual(body["messages"][-1]["content"], "what was my locker number?")

    def test_a_chat_that_fits_gets_no_left_out_note(self):
        body = self.body(long_chat(2, newest="what was my locker number?"))
        sent = window_fit.fit_to_window(CONFIG, body)[0]["messages"]
        self.assertEqual(sent[-1]["content"], "what was my locker number?")

    def test_what_is_sent_leaves_the_reply_its_room(self):
        body = self.body([{"role": "system", "content": "be kind"}] + long_chat(120), num_predict=6144, tools=[READ_FILE])
        fitted, _ = window_fit.fit_to_window(CONFIG, body)
        sent_tokens = sum(window_fit.message_tokens(message) for message in fitted["messages"])
        tools_tokens = window_fit.text_tokens(str(fitted["tools"]))
        self.assertLessEqual(sent_tokens + tools_tokens + 6144, WINDOW)

    def test_a_bigger_reply_leaves_less_room_for_old_messages(self):
        messages = [{"role": "system", "content": "be kind"}] + long_chat(120)
        plain, _ = window_fit.fit_to_window(CONFIG, self.body(messages, num_predict=2048))
        thinking, _ = window_fit.fit_to_window(CONFIG, self.body(messages, num_predict=6144))
        self.assertLess(len(thinking["messages"]), len(plain["messages"]))

    def test_tool_results_stay_with_the_call_they_answer(self):
        rounds = [message for index in range(80) for message in tool_round(index)]
        body = self.body([{"role": "system", "content": "work alone"}, {"role": "user", "content": "tidy the files"}] + rounds)
        sent = window_fit.fit_to_window(CONFIG, body)[0]["messages"]
        self.assertEqual(sent[2]["content"], "tidy the files" + window_fit.LEFT_OUT_ASK)
        for index, message in enumerate(sent):
            if message["role"] == "tool":
                self.assertTrue(sent[index - 1].get("tool_calls") or sent[index - 1]["role"] == "tool")
        self.assertTrue(contains(sent, "contents 79:"))
        self.assertFalse(contains(sent, "contents 0:"))

    def test_the_newest_request_is_kept_even_when_it_alone_is_too_long(self):
        huge = "word " * 60000
        body = self.body([{"role": "system", "content": "be kind"}] + long_chat(2, newest=huge))
        sent = window_fit.fit_to_window(CONFIG, body)[0]["messages"]
        self.assertEqual(sent[-1]["content"], huge + window_fit.LEFT_OUT_ASK)
        self.assertFalse(contains(sent, "question 0:"))

    def test_pictures_count_by_their_token_cost_not_their_encoded_size(self):
        picture = {"role": "user", "content": "what is this?", "images": ["A" * 200000]}
        self.assertLess(window_fit.message_tokens(picture), window_fit.IMAGE_TOKENS + 100)


class ChatTurnsFitTheWindow(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        patched = {**connections.load_config(), "options": {"num_ctx": WINDOW, "num_predict": 2048}}
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(patched)).start()

    def sent(self, messages, **turn):
        ollama = FakeOllama()
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            brain.run_turn(messages, "Arty", **turn)
        return ollama.bodies[0]

    def test_a_long_chat_is_trimmed_by_the_brain_before_ollama_sees_it(self):
        body = self.sent(long_chat(120))
        sent = body["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn(window_fit.LEFT_OUT_NOTE, texts(sent))
        self.assertIn("what should I cook tonight?", sent[-1]["content"])
        self.assertTrue(contains(sent, "reply 119:"))
        self.assertFalse(contains(sent, "question 0:"))

    def test_a_short_chat_goes_whole(self):
        body = self.sent(long_chat(2))
        self.assertNotIn(window_fit.LEFT_OUT_NOTE, texts(body["messages"]))
        self.assertTrue(contains(body["messages"], "question 0:"))

    def test_an_agent_conversation_is_never_trimmed_so_the_client_can_compact_it(self):
        messages = [{"role": "system", "content": "you are a coding agent"}] + long_chat(120)
        body = self.sent(messages, client_tools=[READ_FILE], agent=True)
        self.assertNotIn(window_fit.LEFT_OUT_NOTE, texts(body["messages"]))
        self.assertTrue(contains(body["messages"], "question 0:"))
        self.assertIs(body["truncate"], False)


if __name__ == "__main__":
    unittest.main()
