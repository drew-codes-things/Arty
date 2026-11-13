import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-reminder-cancel-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import connections  # noqa: E402
import schedules  # noqa: E402
import reply_rules  # noqa: E402
import steering  # noqa: E402
import store  # noqa: E402

CONFIG_FILE = Path(__file__).resolve().parents[1] / "brain.json"
HOUR = 3600


def active_ids():
    return {row["id"] for row in store.list_schedules()}


class Reminders(unittest.TestCase):
    def setUp(self):
        store.init()
        for row in store.list_schedules(include_disabled=True):
            store.update_schedule(row["id"], enabled=0, next_run=None)
        soon = time.time() + HOUR
        self.briefing = store.create_schedule("Morning briefing", "Brief the user", "0 8 * * *", "every day at 08:00", soon)["id"]
        self.plants = store.create_schedule("Water the plants.", "Remind the user: Water the plants.", None,
                                            "once at Sat 14 Nov 09:00", soon, "Water the plants.")["id"]
        self.old = store.create_schedule("Build the desktop app", "Remind the user: Build the desktop app", None,
                                         "once at Tue 03 Nov 15:07", soon, "Build the desktop app")["id"]
        store.update_schedule(self.old, enabled=0, next_run=None)

    def test_a_reminder_named_by_its_words_is_cancelled_and_nothing_else(self):
        reply = schedules.cancel_by_name("cancel the water the plants reminder")
        self.assertIn("Water the plants", reply)
        self.assertEqual(active_ids(), {self.briefing})

    def test_a_partial_name_finds_the_one_reminder(self):
        reply = schedules.cancel_by_name("please delete my plants reminder")
        self.assertIn("Cancelled", reply)
        self.assertEqual(active_ids(), {self.briefing})

    def test_no_active_match_says_so_and_lists_what_is_active(self):
        reply = schedules.cancel_by_name("cancel my reminder to build the desktop app")
        self.assertIn("couldn't find", reply)
        self.assertIn("Morning briefing", reply)
        self.assertEqual(active_ids(), {self.briefing, self.plants})

    def test_more_than_one_match_asks_which_and_cancels_nothing(self):
        other = store.create_schedule("Water the garden plants.", "Remind the user: Water the garden plants.", None,
                                      "once at Sun 15 Nov 09:00", time.time() + HOUR, "Water the garden plants.")["id"]
        reply = schedules.cancel_by_name("cancel the plants reminder")
        self.assertIn("Which", reply)
        self.assertEqual(active_ids(), {self.briefing, self.plants, other})

    def gym(self, title="Go to the gym."):
        return store.create_schedule(title, f"Remind the user: {title}", None, "once at Mon 16 Nov 18:00", time.time() + HOUR, title)["id"]

    def test_stop_sending_reminders_about_a_thing_cancels_that_reminder(self):
        gym = self.gym()
        reply = schedules.cancel_by_name("stop sending me reminders about the gym")
        self.assertIn("Cancelled: Go to the gym.", reply)
        self.assertEqual(active_ids(), {self.briefing, self.plants})
        self.assertNotIn(gym, active_ids())

    def test_all_my_reminders_lists_them_and_cancels_nothing(self):
        gym = self.gym()
        for text in ("cancel all my reminders", "delete all reminders", "please remove every reminder"):
            reply = schedules.cancel_by_name(text)
            self.assertIn("Which ones?", reply, text)
            self.assertIn(f"#{self.plants} Water the plants.", reply, text)
            self.assertIn(f"#{gym} Go to the gym.", reply, text)
            self.assertNotIn("Morning briefing", reply, text)
            self.assertEqual(active_ids(), {self.briefing, self.plants, gym}, text)

    def test_all_with_a_name_lists_only_those_and_cancels_nothing(self):
        gym = self.gym()
        reply = schedules.cancel_by_name("cancel all my gym reminders")
        self.assertIn("Which ones?", reply)
        self.assertIn(f"#{gym} Go to the gym.", reply)
        self.assertNotIn("plants", reply)
        self.assertEqual(active_ids(), {self.briefing, self.plants, gym})

    def test_all_with_no_reminders_says_so(self):
        store.update_schedule(self.plants, enabled=0, next_run=None)
        reply = schedules.cancel_by_name("cancel all my reminders")
        self.assertIn("no active reminders", reply)
        self.assertEqual(active_ids(), {self.briefing})

    def test_a_reminder_request_never_switches_off_a_briefing(self):
        reply = schedules.cancel_by_name("cancel my morning reminder")
        self.assertIn("couldn't find", reply)
        self.assertEqual(active_ids(), {self.briefing, self.plants})

    def test_a_briefing_named_in_full_or_as_a_scheduled_job_can_be_cancelled(self):
        reply = schedules.cancel_by_name("cancel my morning briefing reminder")
        self.assertIn("Cancelled: Morning briefing", reply)
        self.assertEqual(active_ids(), {self.plants})
        store.update_schedule(self.briefing, enabled=1, next_run=time.time() + HOUR)
        reply = schedules.cancel_by_name("cancel the scheduled job for the morning")
        self.assertIn("Cancelled: Morning briefing", reply)
        self.assertEqual(active_ids(), {self.plants})

    def test_other_requests_are_not_cancellations(self):
        for text in ("cancel my dentist appointment", "remind me to cancel the gym", "what reminders have I got?",
                     "cancel that"):
            self.assertIsNone(schedules.cancel_by_name(text), text)

    def test_the_quick_path_cancels_by_name(self):
        with mock.patch.object(connections, "load_config", return_value={}):
            reply, tool = brain.quick_action("cancel the water the plants reminder")
        self.assertEqual(tool, "cancel_scheduled")
        self.assertNotIn(self.plants, active_ids())

    def test_the_tool_will_not_cancel_a_job_that_is_not_active(self):
        result, _ = brain.run_native("cancel_scheduled", {"schedule_id": self.old}, {}, False, None)
        self.assertIn(f"No active scheduled job #{self.old}", result)
        self.assertIn(f"#{self.plants}", result)
        self.assertEqual(active_ids(), {self.briefing, self.plants})

    def test_the_tool_still_cancels_an_active_job(self):
        result, _ = brain.run_native("cancel_scheduled", {"schedule_id": self.plants}, {}, False, None)
        self.assertTrue(result.startswith("Cancelled"))
        self.assertEqual(active_ids(), {self.briefing})


class ListingReminders(unittest.TestCase):
    def rule_tools(self, said):
        rules = json.loads(CONFIG_FILE.read_text())["lookups"]
        return [rule["tool"] for rule in rules if rule["tool"] in steering.LOOKUP_TOOLS and re.search(rule["pattern"], said, re.I)
                and not (rule.get("skip_for_changes") and reply_rules.DO_REQUEST.search(said))]

    def test_asking_what_reminders_there_are_looks_up_the_schedule(self):
        for said in ("what reminders have I got coming up?", "do I have any reminders set?", "show me my reminders",
                     "what's scheduled?", "list my reminders"):
            self.assertIn("list_scheduled", self.rule_tools(said), said)

    def test_setting_or_cancelling_a_reminder_is_not_a_lookup(self):
        for said in ("remind me to stretch in an hour", "cancel the plants reminder", "set a reminder for 5pm"):
            self.assertNotIn("list_scheduled", self.rule_tools(said), said)


if __name__ == "__main__":
    unittest.main()
