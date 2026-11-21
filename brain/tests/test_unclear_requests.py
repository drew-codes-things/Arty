import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-unclear-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))

from test_round_timings import WARM, TimedEngine, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import quick  # noqa: E402
import store  # noqa: E402

BATTERY_ARGUMENTS = {"title": "Battery", "when": "in 2 minutes", "reminder_text": "Check the battery."}


def schedule_call(arguments=BATTERY_ARGUMENTS):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "schedule_task", "arguments": arguments}}]}, WARM


class UnclearRequests(unittest.TestCase):
    def test_a_garbled_request_is_asked_about(self):
        for text in ["can you Sharp", "Can you sharp?", "could you blorp", "hey arty, can you Sharp please", "will you zing zang"]:
            with self.subTest(text=text):
                asked = quick.unclear_request(text)
                self.assertIsNotNone(asked)
                self.assertIn("?", asked)

    def test_the_heard_words_are_repeated_back(self):
        self.assertIn('"Sharp"', quick.unclear_request("can you Sharp"))

    def test_clear_requests_are_left_alone(self):
        for text in ["can you help", "can you hear me", "can you swim?", "can you see this", "can you stop", "can you not",
                     "can you remind me to stretch in 5 minutes", "what's the time", "thanks", "Sharp", "why are you gay",
                     "can you tell me a joke", "can you explain recursion", "can you check the weather tomorrow"]:
            with self.subTest(text=text):
                self.assertIsNone(quick.unclear_request(text))


class GarbledTurns(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        store.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM schedules")

    def turn(self, conversation, *replies, client="phone"):
        engine = TimedEngine(*replies)
        with unittest.mock.patch.object(connections, "http_json", side_effect=engine):
            reply, used, _, _ = brain.run_turn(conversation, "Arty", client=client)
        return reply, used, engine

    def test_a_garbled_request_gets_a_question_and_no_model_or_tools(self):
        reply, used, engine = self.turn([{"role": "user", "content": "can you Sharp"}], schedule_call())
        self.assertIn("?", reply)
        self.assertEqual(used, [])
        self.assertEqual(engine.bodies, [])
        self.assertEqual(store.list_schedules(), [])


if __name__ == "__main__":
    unittest.main()
