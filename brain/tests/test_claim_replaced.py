import json
import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_round_timings import WARM, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import honesty  # noqa: E402

PASSED_ALONG = "I'll pass along your message to Sam. Let him know you think he's great."
PIZZA = "Your pizza is on its way. Enjoy!"
PIZZA_CLAIM = {"text": "Your pizza is on its way.", "correction": "I can't order food, so nothing is on its way."}


class JudgedEngine:
    def __init__(self, answer, claims):
        self.answer = answer
        self.claims = claims
        self.model_calls = 0

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        prompt = str(body["messages"][-1].get("content") or "")
        if prompt.startswith("The person said"):
            return {"message": {"content": json.dumps({"kind": "A"})}}
        if prompt.startswith("You check an assistant's reply"):
            return {"message": {"content": json.dumps({"false_claims": self.claims})}}
        self.model_calls += 1
        return {"message": {"role": "assistant", "content": self.answer}, "done_reason": "stop", **WARM}

    def streamed(self, body, on_text, timeout=600, on_thinking=None):
        reply = self(f"{brain.ARTY_ENGINE_URL}/api/chat", body)
        for piece in re.findall(r"\S+\s*", reply["message"].get("content") or ""):
            on_text(piece)
        return reply


class ClaimsAreReplaced(unittest.TestCase):
    def setUp(self):
        offline_turns(self)

    def turn(self, answer, claims, said="what do you think of Sam?", **kwargs):
        engine, sent = JudgedEngine(answer, claims), []
        with unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(connections, "stream_chat", side_effect=engine.streamed), \
                unittest.mock.patch.object(brain, "be_honest", honesty.be_honest):
            reply, _, _, _ = brain.run_turn([{"role": "user", "content": said}], "Arty", on_text=sent.append, **kwargs)
        return reply, "".join(sent)

    def test_a_sentence_claiming_an_action_is_not_sent_early(self):
        reply, sent = self.turn(PASSED_ALONG, [{"text": "I'll pass along your message to Sam.",
                                                "correction": "I can't pass messages on from here."}])
        self.assertNotIn("pass along", sent)
        self.assertNotIn("pass along", reply)
        self.assertIn("I can't pass messages on from here.", reply)

    def test_a_plain_answer_still_goes_out_early(self):
        reply, sent = self.turn("Sam sounds lovely. Tell me more about him.", [])
        self.assertTrue(sent.startswith("Sam sounds lovely."))
        self.assertTrue(reply.startswith("Sam sounds lovely."))

    def test_a_job_reply_replaces_a_claim_already_shown_instead_of_contradicting_it(self):
        reply, sent = self.turn(PIZZA, [PIZZA_CLAIM], early_text_replaced=True)
        self.assertIn("Your pizza is on its way.", sent)
        self.assertNotIn("Your pizza is on its way.", reply)
        self.assertIn("nothing is on its way.", reply)

    def test_a_stream_that_cannot_take_text_back_still_corrects_it(self):
        reply, sent = self.turn(PIZZA, [PIZZA_CLAIM])
        self.assertTrue(reply.startswith(sent.strip()))
        self.assertTrue(reply.endswith("nothing is on its way."))


class ChatJobsTakeBackEarlyText(unittest.TestCase):
    def test_a_chat_job_says_its_early_text_is_replaced_by_the_answer(self):
        jobs, seen = brain.ChatJobs(), {}

        def fake_turn(messages, model, **kwargs):
            seen.update(kwargs)
            return "Hello.", [], {}, None
        with unittest.mock.patch.object(brain, "run_turn", side_effect=fake_turn):
            jobs._run("job", [{"role": "user", "content": "hi"}], "Arty", "phone")
        self.assertIs(seen.get("early_text_replaced"), True)


if __name__ == "__main__":
    unittest.main()
