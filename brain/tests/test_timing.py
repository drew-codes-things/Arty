"""How reminders and calendar events understand times. "Now" is Friday 23 October 2026, 15:00.
Run from brain/: python3 -m unittest discover tests"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calendar_feed  # noqa: E402
import timing  # noqa: E402

NOW = datetime(2026, 10, 23, 15, 0)


def reminder(when):
    cron, first, _ = timing.parse_when(when, NOW)
    return cron, first.strftime("%a %d %b %H:%M")


def event(when):
    start, all_day, repeat = calendar_feed.parse_when(when, NOW)
    return start.strftime("%a %d %b %H:%M"), all_day, repeat


class Reminders(unittest.TestCase):
    def test_one_off_times(self):
        cases = {
            "in 5 minutes": "Fri 23 Oct 15:05",
            "in half an hour": "Fri 23 Oct 15:30",
            "at 6pm": "Fri 23 Oct 18:00",
            "at 2:30pm": "Sat 24 Oct 14:30",  # already past today
            "at 9am tomorrow": "Sat 24 Oct 09:00",
            "tomorrow morning": "Sat 24 Oct 09:00",
            "tonight": "Fri 23 Oct 20:00",
            "on Monday at 10am": "Mon 26 Oct 10:00",
        }
        for when, expected in cases.items():
            with self.subTest(when=when):
                self.assertEqual(reminder(when), (None, expected))

    def test_an_hour_without_am_or_pm(self):
        self.assertEqual(reminder("at 6"), (None, "Fri 23 Oct 18:00"))
        self.assertEqual(reminder("tomorrow at 9"), (None, "Sat 24 Oct 09:00"))
        self.assertEqual(reminder("at 12"), (None, "Sat 24 Oct 12:00"))

    def test_spoken_times_of_day(self):
        self.assertEqual(reminder("at noon"), (None, "Sat 24 Oct 12:00"))
        self.assertEqual(reminder("at midnight"), (None, "Sat 24 Oct 00:00"))
        self.assertEqual(reminder("this evening"), (None, "Fri 23 Oct 18:00"))
        self.assertEqual(reminder("3pm"), (None, "Sat 24 Oct 15:00"))  # it's 15:00 now, so the next 3pm
        self.assertEqual(reminder("5pm"), (None, "Fri 23 Oct 17:00"))

    def test_a_day_without_a_time_means_that_morning(self):
        self.assertEqual(reminder("Monday"), (None, "Mon 26 Oct 09:00"))
        self.assertEqual(reminder("next Friday"), (None, "Fri 30 Oct 09:00"))

    def test_dates(self):
        self.assertEqual(reminder("on the 25th"), (None, "Sun 25 Oct 09:00"))
        self.assertEqual(reminder("25 December at 9am"), (None, "Fri 25 Dec 09:00"))
        self.assertEqual(reminder("24/12 at 6pm"), (None, "Thu 24 Dec 18:00"))
        self.assertEqual(reminder("the 1st"), (None, "Sun 01 Nov 09:00"))

    def test_repeats(self):
        self.assertEqual(reminder("every Monday at 8am"), ("0 8 * * 1", "Mon 26 Oct 08:00"))
        self.assertEqual(reminder("every day at 7:30"), ("30 7 * * *", "Sat 24 Oct 07:30"))
        self.assertEqual(reminder("every weekday at 8"), ("0 8 * * 1-5", "Mon 26 Oct 08:00"))

    def test_nonsense_is_refused(self):
        with self.assertRaises(ValueError):
            reminder("whenever")


class CalendarEvents(unittest.TestCase):
    def test_times_and_dates(self):
        self.assertEqual(event("Tuesday 3pm"), ("Tue 27 Oct 15:00", False, None))
        self.assertEqual(event("25 Dec 14:00"), ("Fri 25 Dec 14:00", False, None))
        self.assertEqual(event("24/12"), ("Thu 24 Dec 00:00", True, None))
        self.assertEqual(event("1st November 10am"), ("Sun 01 Nov 10:00", False, None))
        self.assertEqual(event("November 1st at 10"), ("Sun 01 Nov 10:00", False, None))
        self.assertEqual(event("3pm"), ("Sat 24 Oct 15:00", False, None))

    def test_repeating_events(self):
        self.assertEqual(event("every Monday 6pm"), ("Mon 26 Oct 18:00", False, "0 18 * * 1"))
        self.assertEqual(event("Tuesdays 3pm"), ("Tue 27 Oct 15:00", False, "0 15 * * 2"))


if __name__ == "__main__":
    unittest.main()
