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
_scratch = tempfile.mkdtemp(prefix="arty-schedule-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))

from test_round_timings import WARM, TimedEngine, answer, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import store  # noqa: E402

PING_ARGUMENTS = {"title": "Oven", "when": "in 5 minutes", "reminder_text": "Check the oven."}


def schedule_call(arguments=PING_ARGUMENTS):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "schedule_task", "arguments": arguments}}]}, WARM


class ScheduleNeedsARequest(unittest.TestCase):
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

    def tool_results(self, engine):
        return [message for body in engine.bodies for message in body["messages"] if message.get("role") == "tool"]

    def test_schedule_task_without_a_request_for_it_is_not_run(self):
        reply, used, engine = self.turn([{"role": "user", "content": "sharp thing over there"}], schedule_call(),
                                        answer("What did you mean?"))
        self.assertNotIn("schedule_task", used)
        self.assertEqual(store.list_schedules(), [])
        results = self.tool_results(engine)
        self.assertTrue(results and results[-1]["content"].startswith("Blocked"))
        self.assertIn("ask", results[-1]["content"].lower())

    def test_schedule_task_runs_when_the_message_asks_for_a_time(self):
        reply, used, _ = self.turn([{"role": "user", "content": "ping me about the oven in 5 minutes"}], schedule_call(),
                                   answer("Done, I'll ping you in 5 minutes."))
        self.assertIn("schedule_task", used)
        self.assertEqual(len(store.list_schedules()), 1)

    def test_schedule_task_runs_when_the_user_says_yes_to_an_offered_reminder(self):
        conversation = [{"role": "user", "content": "the oven is on"},
                        {"role": "assistant", "content": "Want me to set a reminder to check it in 5 minutes?"},
                        {"role": "user", "content": "yes please"}]
        reply, used, _ = self.turn(conversation, schedule_call(), answer("Reminder set."))
        self.assertIn("schedule_task", used)
        self.assertEqual(len(store.list_schedules()), 1)



if __name__ == "__main__":
    unittest.main()
