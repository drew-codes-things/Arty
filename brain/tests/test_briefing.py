"""The briefings: built by code from the real data, with empty sections left out and anything that couldn't be
checked said plainly. Run from brain/: python3 -m unittest discover tests"""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

_scratch = tempfile.mkdtemp(prefix="arty-briefing-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))
os.environ.setdefault("EMAIL_WATCH_STATE", os.path.join(_scratch, "email-watch.json"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import briefing  # noqa: E402
import personal  # noqa: E402
import store  # noqa: E402

STATUS = """Host pve1 (192.0.2.253): online, CPU 17% of 8 cores
CT 100 web (192.0.2.10): running, CPU 5%
CT 103 minecraft (192.0.2.40): stopped
VM 106 test-debian: stopped"""
NO_EMAIL = "No emails match: in:inbox is:unread category:primary newer_than:3d"


def tools(status=STATUS, mail=NO_EMAIL):
    return lambda name, arguments: status if name == "homelab__service_status" else mail


class Briefing(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        with store.connect() as connection:
            connection.execute("DELETE FROM deadlines")
            connection.execute("DELETE FROM schedules")
        patches = [mock.patch.object(briefing.weather, "answer", lambda wanted, place: f"{wanted.title()}: dry, 7 to 13°C."),
                   mock.patch.object(briefing.calendar_feed, "event_list", lambda days: {"events": [], "problems": []})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_an_empty_day_has_no_made_up_sections(self):
        text = briefing.build("morning", tools(), lambda prompt: "")
        self.assertEqual(text, "Good morning. Here's your day.\n\nDown: CT 103 minecraft (stopped). Today: dry, 7 to 13°C.")
        for placeholder in ("Event 1", "[task", "Calendar:", "Deadlines", "Reminders:", "Important email:"):
            self.assertNotIn(placeholder, text)

    def test_real_events_deadlines_and_reminders_are_listed(self):
        today = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
        events = {"events": [{"start": today.isoformat(), "end": today.isoformat(), "all_day": False, "title": "Dentist", "location": ""}],
                  "problems": []}
        with mock.patch.object(briefing.calendar_feed, "event_list", lambda days: events):
            with store.connect() as connection:
                connection.execute("INSERT INTO deadlines (title, due, created) VALUES (?, ?, ?)", ("Essay", time.time() + 3 * 86400, time.time()))
            store.create_schedule("Stretch", "", "", "once", today.replace(hour=23, minute=0).timestamp(), "Time to stretch.")
            text = briefing.build("morning", tools(status=STATUS.replace("stopped", "running")), lambda prompt: "")
        self.assertIn("All your servers are up.", text)
        self.assertIn("Calendar:\n- 15:00: Dentist", text)
        self.assertIn("Reminders:\n- 23:00: Time to stretch.", text)
        self.assertIn("Deadlines coming up:\n- Essay:", text)

    def test_what_couldnt_be_checked_is_said_plainly(self):
        text = briefing.build("morning", tools(status="error: Proxmox didn't answer", mail="BLOCKED: the Gmail window isn't signed in. Do not retry"),
                              lambda prompt: "")
        self.assertIn("I couldn't check your servers just now.", text)
        self.assertIn("I couldn't check your email (the Gmail window isn't signed in).", text)
        self.assertNotIn("Important email:", text)

    def test_the_evening_looks_at_tomorrow_and_skips_email(self):
        asked = []
        text = briefing.build("evening", lambda name, arguments: asked.append(name) or STATUS, lambda prompt: "")
        self.assertTrue(text.startswith("Good evening. Here's tomorrow."))
        self.assertIn("Tomorrow: dry", text)
        self.assertEqual(asked, ["homelab__service_status"])

    def test_briefing_schedules_are_recognised(self):
        self.assertEqual(briefing.kind_of("BRIEFING:morning"), "morning")
        self.assertIsNone(briefing.kind_of("Write the user's morning briefing."))
        self.assertIsNone(briefing.kind_of("BRIEFING:lunch"))


if __name__ == "__main__":
    unittest.main()
