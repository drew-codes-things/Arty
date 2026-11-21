import json
import os
import re
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-reported-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))

from test_round_timings import WARM, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import honesty  # noqa: E402
import store  # noqa: E402

TELL_THEM = "my phone's low don't worry about it can you tell Jacob and Jaden and above beautiful"
WHY = "why are you gay"
GARBLED = "can you Sharp"
ON_THE_CALL = {"role": "system", "content": "The user is on a voice call with you and hears your reply read aloud. Answer "
               "in one to three short, natural spoken sentences. No lists, headings, markdown, emoji or links unless they "
               "ask for them."}
OLD_FIRST_REPLY = ("I'll pass along your message to Jacob and Jaden. Let them know you think they're above beautiful. "
                   "I cannot pass along messages or interact with other people on your behalf.")
WHY_REPLY = ("I'm not gay. I'm an AI assistant named Arty, and I don't have personal attributes like sexual orientation.")
PASSED_ALONG = "I'll pass along your message to Jacob and Jaden. Let them know you think they're above beautiful."
CLAIM = {"text": "I'll pass along your message to Jacob and Jaden.",
         "correction": "I cannot pass along messages or interact with other people on your behalf."}
BATTERY_CALL = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "schedule_task", "arguments": {
    "when": "in 2 minutes", "reminder_text": "Check phone battery and charging status.", "title": "Phone Battery Reminder"}}}]}


class ReportedEngine:
    def __init__(self, *replies, claims=()):
        self.replies = list(replies)
        self.claims = list(claims)
        self.model_calls = 0

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        prompt = str(body["messages"][-1].get("content") or "")
        if prompt.startswith("The person said"):
            return {"message": {"content": json.dumps({"kind": "A"})}}
        if prompt.startswith("You check an assistant's reply"):
            return {"message": {"content": json.dumps({"false_claims": self.claims})}}
        if prompt.startswith("PRIVACY CHECK"):
            return {"message": {"content": json.dumps({"gives_away": False})}}
        self.model_calls += 1
        message = self.replies.pop(0) if self.replies else {"role": "assistant", "content": "What did you mean?"}
        return {"message": message, "done_reason": "stop", **WARM}

    def streamed(self, body, on_text, timeout=600, on_thinking=None):
        reply = self(f"{brain.ARTY_ENGINE_URL}/api/chat", body)
        for piece in re.findall(r"\S+\s*", reply["message"].get("content") or ""):
            on_text(piece)
        return reply


class ReportedPhoneTurns(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        store.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM schedules")

    def job(self, conversation, engine):
        jobs = brain.ChatJobs()
        jobs.jobs["reported"] = {"status": "running", "created": time.time()}
        with unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(connections, "stream_chat", side_effect=engine.streamed), \
                unittest.mock.patch.object(brain, "be_honest", honesty.be_honest):
            jobs._run("reported", conversation, "Arty", "phone")
        return jobs.get("reported")

    def test_turn_one_is_said_to_them_out_loud_on_the_call(self):
        engine = ReportedEngine({"role": "assistant", "content": PASSED_ALONG}, claims=[CLAIM])
        done = self.job([ON_THE_CALL, {"role": "user", "content": TELL_THEM}], engine)
        self.assertEqual(done["answer"], "Jacob and Jaden, you're above beautiful!")
        self.assertEqual(done["tools_used"], [])
        self.assertEqual(engine.model_calls, 0)

    def test_a_streamed_claim_is_never_left_next_to_its_refusal(self):
        engine = ReportedEngine({"role": "assistant", "content": PASSED_ALONG}, claims=[CLAIM])
        done = self.job([{"role": "user", "content": "what do you think of the twins"}], engine)
        self.assertNotIn("I'll pass along", done["answer"])
        self.assertNotIn("I'll pass along", done.get("partial", ""))
        self.assertIn("I cannot pass along messages", done["answer"])

    def test_turn_two_is_answered_by_the_model_without_tools(self):
        engine = ReportedEngine({"role": "assistant", "content": WHY_REPLY})
        conversation = [ON_THE_CALL, {"role": "user", "content": TELL_THEM}, {"role": "assistant", "content": OLD_FIRST_REPLY},
                        {"role": "user", "content": WHY}]
        done = self.job(conversation, engine)
        self.assertEqual(done["answer"], WHY_REPLY)
        self.assertEqual(done["tools_used"], [])
        self.assertEqual(engine.model_calls, 1)

    def test_turn_three_asks_what_was_meant_and_sets_nothing(self):
        engine = ReportedEngine(BATTERY_CALL, {"role": "assistant", "content": "I've set a reminder for you."})
        conversation = [ON_THE_CALL, {"role": "user", "content": TELL_THEM}, {"role": "assistant", "content": OLD_FIRST_REPLY},
                        {"role": "user", "content": WHY}, {"role": "assistant", "content": WHY_REPLY},
                        {"role": "user", "content": GARBLED}]
        done = self.job(conversation, engine)
        self.assertIn("?", done["answer"])
        self.assertIn("Sharp", done["answer"])
        self.assertNotIn("schedule_task", done["tools_used"])
        self.assertEqual(store.list_schedules(), [])
        self.assertEqual(engine.model_calls, 0)


if __name__ == "__main__":
    unittest.main()
