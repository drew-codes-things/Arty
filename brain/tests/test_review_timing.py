import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN_DB", os.path.join(tempfile.mkdtemp(prefix="arty-timing-"), "arty.db"))

import calendar_feed  # noqa: E402
import store  # noqa: E402
import timing  # noqa: E402

FRIDAY_3PM = datetime(2026, 10, 23, 15, 0)


def gaps(cron, start, count):
    moments, moment = [], start
    for _ in range(count):
        moment = timing.following_run(cron, moment, moment)
        moments.append(moment)
    return {later - earlier for earlier, later in zip(moments, moments[1:])}


class EvenRepeats(unittest.TestCase):
    def test_every_45_minutes_is_45_minutes_apart(self):
        cron, first, description = timing.parse_when("every 45 minutes", FRIDAY_3PM)
        self.assertEqual(first, FRIDAY_3PM + timedelta(minutes=45))
        self.assertEqual(gaps(cron, first, 10), {timedelta(minutes=45)})
        self.assertEqual(description, "every 45 minutes")

    def test_every_5_hours_crosses_midnight_evenly(self):
        cron, first, _ = timing.parse_when("every 5 hours", FRIDAY_3PM)
        self.assertEqual(gaps(cron, first, 12), {timedelta(hours=5)})

    def test_a_late_tick_does_not_drift_the_repeat(self):
        cron, first, _ = timing.parse_when("every 45 minutes", FRIDAY_3PM)
        late_tick = first + timedelta(seconds=29)
        self.assertEqual(timing.following_run(cron, first, late_tick), first + timedelta(minutes=45))

    def test_a_repeat_missed_while_down_resumes_from_now(self):
        cron, first, _ = timing.parse_when("every 45 minutes", FRIDAY_3PM)
        back_up = first + timedelta(hours=5, minutes=1)
        self.assertGreater(timing.following_run(cron, first, back_up), back_up)

    def test_clock_aligned_repeats_stay_cron(self):
        self.assertEqual(timing.parse_when("every 15 minutes", FRIDAY_3PM)[0], "*/15 * * * *")
        self.assertEqual(timing.parse_when("every 6 hours", FRIDAY_3PM)[0], "0 */6 * * *")


class RelativeTimes(unittest.TestCase):
    def test_huge_amounts_are_a_plain_refusal(self):
        with self.assertRaises(ValueError):
            timing.parse_when("in 99999999999 days", FRIDAY_3PM)

    def test_seconds_weeks_and_months(self):
        self.assertEqual(timing.parse_when("in 90 seconds", FRIDAY_3PM)[1], FRIDAY_3PM + timedelta(seconds=90))
        self.assertEqual(timing.parse_when("in 2 weeks", FRIDAY_3PM)[1], FRIDAY_3PM + timedelta(days=14))
        self.assertEqual(timing.parse_when("in a week", FRIDAY_3PM)[1], FRIDAY_3PM + timedelta(days=7))
        self.assertEqual(timing.parse_when("in a month", FRIDAY_3PM)[1], datetime(2026, 11, 23, 15, 0))
        self.assertEqual(timing.parse_when("in 3 months", datetime(2026, 11, 30, 9, 0))[1], datetime(2027, 2, 28, 9, 0))

    def test_next_friday_on_a_friday_is_a_week_away_in_reminders_and_the_calendar(self):
        friday_morning = datetime(2026, 10, 23, 8, 0)
        reminder = timing.parse_when("next Friday 5pm", friday_morning)[1]
        event = calendar_feed.parse_when("next Friday 5pm", friday_morning)[0]
        self.assertEqual(reminder, datetime(2026, 10, 30, 17, 0))
        self.assertEqual(event, reminder)


class Dates(unittest.TestCase):
    def test_29_february_without_a_year_goes_to_the_next_leap_year(self):
        after_leap_day = datetime(2028, 3, 1, 9, 0)
        self.assertEqual(timing.parse_when("29 Feb at 10am", after_leap_day)[1], datetime(2032, 2, 29, 10, 0))

    def test_impossible_dates_are_a_plain_message(self):
        for when in ["31 Feb", "13/13", "30/02 at 9am"]:
            with self.assertRaises(ValueError) as caught:
                timing.parse_when(when, FRIDAY_3PM)
            self.assertIn("isn't a real date", str(caught.exception), when)

    def test_twenty_four_seven_is_not_a_date(self):
        self.assertIsNone(calendar_feed.written_date("open 24/7", FRIDAY_3PM.date()))


class CalendarEvents(unittest.TestCase):
    def setUp(self):
        store.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM events")

    def test_a_repeating_day_with_no_time_is_all_day_from_midnight(self):
        start, all_day, repeat = calendar_feed.parse_when("every Monday", FRIDAY_3PM)
        self.assertTrue(all_day)
        self.assertEqual(start, datetime(2026, 10, 26, 0, 0))
        self.assertEqual(repeat, "0 0 * * 1")

    def test_yearly_repeats_like_birthdays(self):
        start, all_day, repeat = calendar_feed.parse_when("every year on 25 December", FRIDAY_3PM)
        self.assertEqual((start, all_day, repeat), (datetime(2026, 12, 25, 0, 0), True, "0 0 25 12 *"))
        cron, first, _ = timing.parse_when("every 5 March at 8am", FRIDAY_3PM)
        self.assertEqual((cron, first), ("0 8 5 3 *", datetime(2027, 3, 5, 8, 0)))

    def test_a_negative_length_is_refused(self):
        self.assertIn("Couldn't add it", calendar_feed.add("Gym", "Tuesday 3pm", -30))

    def test_moving_a_timed_event_to_a_day_makes_it_all_day(self):
        calendar_feed.add("Dentist", "27 Oct 15:00")
        event = store.list_events()[0]
        calendar_feed.change(event["id"], when="28 Oct")
        moved = store.get_event(event["id"])
        self.assertEqual(moved["ends"] - moved["starts"], 24 * 3600)

    def test_moving_an_all_day_event_to_a_time_gives_it_a_normal_length(self):
        calendar_feed.add("Holiday", "27 Oct")
        event = store.list_events()[0]
        calendar_feed.change(event["id"], when="28 Oct 3pm")
        moved = store.get_event(event["id"])
        self.assertEqual(moved["ends"] - moved["starts"], calendar_feed.DEFAULT_MINUTES * 60)
        self.assertEqual(moved["all_day"], 0)


if __name__ == "__main__":
    unittest.main()
