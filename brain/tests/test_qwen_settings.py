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

import test_review_brain  # noqa: E402,F401
import agent_loop  # noqa: E402
import connections  # noqa: E402
import main_calls  # noqa: E402
import models  # noqa: E402
import steering  # noqa: E402

CHAT_URL = f"{connections.ARTY_ENGINE_URL}/api/chat"
HUB_URL = "http://127.0.0.1:8000/tools/search"
BIG = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"
OVERFLOW = json.dumps({"error": {"code": 400, "message": "the request exceeds the available context size, try increasing it",
                                 "type": "exceed_context_size_error"}}).encode()


def answer(content="Done.", reason="stop"):
    return {"message": {"role": "assistant", "content": content}, "done_reason": reason}


class Recorder:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def json(self, url, body=None, headers=None, timeout=600):
        self.bodies.append(body)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def stream(self, body, on_text, timeout=600, on_thinking=None):
        reply = self.json(CHAT_URL, body)
        if reply["message"]["content"]:
            on_text(reply["message"]["content"])
        return reply


class Settled(unittest.TestCase):
    def setUp(self):
        patch = unittest.mock.patch.object(main_calls, "read_main", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def sent(self, body, *replies, url=CHAT_URL):
        engine = Recorder(*(replies or (answer(),)))
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            reply = connections.http_json(url, body=body)
        return engine.bodies, reply


class ThinkingIsOffUnlessAsked(Settled):
    def test_a_call_that_says_nothing_about_thinking_has_it_off(self):
        bodies, _ = self.sent({"model": BIG, "messages": []})
        self.assertIs(bodies[0]["think"], False)

    def test_a_call_that_asks_for_thinking_keeps_it(self):
        bodies, _ = self.sent({"model": BIG, "messages": [], "think": True})
        self.assertIs(bodies[0]["think"], True)

    def test_other_calls_are_left_as_they_are(self):
        bodies, _ = self.sent({"query": "x"}, {"results": []}, url=HUB_URL)
        self.assertEqual(bodies[0], {"query": "x"})

    def test_thinking_for_a_hard_question_leaves_room_for_the_answer(self):
        self.assertGreaterEqual(steering.THINKING_MIN_TOKENS, 4000)
        body = steering.with_thinking({"options": {"num_predict": 1500}}, True)
        self.assertGreaterEqual(body["options"]["num_predict"], 4000)


class AThinkingReplyThatRanOutIsWrittenAgain(Settled):
    def test_an_empty_reply_cut_off_while_thinking_is_asked_again_without_thinking(self):
        bodies, reply = self.sent({"model": BIG, "messages": [], "think": True}, answer("", "length"), answer("Four."))
        self.assertEqual([body["think"] for body in bodies], [True, False])
        self.assertEqual(reply["message"]["content"], "Four.")

    def test_a_streamed_reply_that_ran_out_while_thinking_is_asked_again(self):
        engine, heard = Recorder(answer("", "length"), answer("Four.")), []
        with unittest.mock.patch.object(connections, "engine_stream", side_effect=engine.stream):
            reply = connections.stream_chat({"model": BIG, "messages": [], "think": True}, heard.append)
        self.assertEqual([body["think"] for body in engine.bodies], [True, False])
        self.assertEqual((heard, reply["message"]["content"]), (["Four."], "Four."))

    def test_a_cut_off_reply_with_words_or_without_thinking_is_kept(self):
        for body, reply in (({"think": True}, answer("Half an answer", "length")), ({"think": False}, answer("", "length")),
                            ({"think": True}, {**answer(""), "message": {"role": "assistant", "content": "",
                                                                         "tool_calls": [{"function": {"name": "x"}}]},
                                               "done_reason": "length"})):
            with self.subTest(body=body):
                bodies, _ = self.sent({"model": BIG, "messages": [], **body}, reply)
                self.assertEqual(len(bodies), 1)


class JsonJobsRunCool(Settled):
    def test_a_json_job_without_a_temperature_gets_a_low_one(self):
        bodies, _ = self.sent({"model": BIG, "messages": [], "format": "json", "options": {"num_predict": 64}})
        self.assertEqual(bodies[0]["options"], {"num_predict": 64, "temperature": 0.2})

    def test_a_json_job_s_own_temperature_is_kept(self):
        bodies, _ = self.sent({"model": BIG, "messages": [], "format": "json", "options": {"temperature": 0}})
        self.assertEqual(bodies[0]["options"]["temperature"], 0)

    def test_tool_turns_on_the_big_main_run_at_a_low_temperature(self):
        shipped = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
        self.assertEqual(shipped["agent_options"]["models"][BIG]["temperature"], 0.3)


class TheBigMainGetsARelevantToolSubset(unittest.TestCase):
    def test_the_shipped_settings_give_a_35b_main_a_tool_budget(self):
        shipped = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
        found = {"name": BIG, "billions": 34.7, "tools": True}
        with unittest.mock.patch.object(models, "find", return_value=found):
            self.assertEqual(models.budget_for(shipped, BIG), 40)


class ALongConversationIsTrimmed(unittest.TestCase):
    def overflow(self):
        return urllib.error.HTTPError(CHAT_URL, 400, "Bad Request", {}, io.BytesIO(OVERFLOW))

    def test_the_engine_s_window_error_is_read_as_too_long(self):
        self.assertTrue(main_calls.WINDOW_OVERFLOW.search("exceed_context_size_error"))

    def test_a_chat_that_outgrew_the_window_is_sent_again_without_its_oldest_turns(self):
        messages = [{"role": "system", "content": "rules"}]
        for number in range(6):
            messages += [{"role": "user", "content": f"q{number}"}, {"role": "assistant", "content": f"a{number}"}]
        messages.append({"role": "user", "content": "newest"})
        sent = []

        def engine(body, draft, risky, on_thinking):
            sent.append(body["messages"])
            if len(sent) == 1:
                raise self.overflow()
            return answer("Fine.")
        with unittest.mock.patch.object(agent_loop, "engine_reply", side_effect=engine):
            reply = agent_loop.model_reply({"model": BIG, "messages": messages}, {"model": BIG}, None, None, None, False, [])
        self.assertEqual(reply["message"]["content"], "Fine.")
        self.assertEqual(sent[1][0], messages[0])
        self.assertEqual(sent[1][-1], messages[-1])
        self.assertLess(len(sent[1]), len(messages))
        self.assertEqual(sent[1][1]["role"], "user")

    def test_a_chat_that_still_does_not_fit_says_it_is_too_long(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "q"},
                    {"role": "assistant", "content": "a"}, {"role": "user", "content": "newest"}]
        def engine(body, draft, risky, on_thinking):
            raise self.overflow()
        with unittest.mock.patch.object(agent_loop, "engine_reply", side_effect=engine), \
                unittest.mock.patch.object(agent_loop.models, "context_tokens", return_value=24576):
            with self.assertRaises(agent_loop.MessageTooLong):
                agent_loop.model_reply({"model": BIG, "messages": messages}, {"model": BIG}, None, None, None, False, [])


if __name__ == "__main__":
    unittest.main()
