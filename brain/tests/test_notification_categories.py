import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_scratch = tempfile.mkdtemp(prefix="arty-categories-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))

import announcements  # noqa: E402
import lectures  # noqa: E402
import models  # noqa: E402
import notify  # noqa: E402
import schedules  # noqa: E402
import store  # noqa: E402

EXPECTED_CATEGORIES = ("email", "server", "tasks", "approvals", "reminders", "calendar", "lectures", "arty", "other")


class StopWorker(Exception):
    pass


def captured_sends(target, name="send"):
    sent = []
    return sent, mock.patch.object(target, name, lambda title, message, *rest, **options: sent.append((title, options.get("category"))))


class Categories(unittest.TestCase):
    def test_the_category_list_is_fixed_and_ordered(self):
        self.assertEqual(notify.CATEGORIES, EXPECTED_CATEGORIES)

    def test_an_unknown_or_missing_category_is_other(self):
        for value in ("", None, "spam", 7, "EMAIL "):
            expected = "email" if value == "EMAIL " else "other"
            self.assertEqual(notify.known_category(value), expected, value)
        for category in EXPECTED_CATEGORIES:
            self.assertEqual(notify.known_category(category), category)

    def test_listeners_are_told_the_category(self):
        received = []
        with mock.patch.object(notify, "listeners", [lambda *item: received.append(item)]):
            notify.send("📧 Important email", "Sam: lunch?", category=notify.EMAIL)
            notify.send("Arty", "hi", category="made up")
        self.assertEqual(received, [("📧 Important email", "Sam: lunch?", [], False, "email"), ("Arty", "hi", [], False, "other")])


class Feed(unittest.TestCase):
    def setUp(self):
        store.init()

    def test_the_feed_keeps_the_category_beside_the_old_kind(self):
        announcements.record_announcement("⏰ Reminder", "Stretch.", [], False, "reminders")
        newest = store.list_announcements(0)[-1]
        self.assertEqual((newest["kind"], newest["category"]), ("reminder", "reminders"))

    def test_an_approval_keeps_kind_approval_whatever_its_category(self):
        buttons = [{"label": "Fix", "callback": "t:41:approve"}, {"label": "Ignore", "callback": "t:41:deny"}]
        announcements.record_announcement("🛠 Plex is down", "Restart it?", buttons, True, "server")
        newest = store.list_announcements(0)[-1]
        self.assertEqual((newest["kind"], newest["category"], newest["task_id"], newest["actions"]),
                         ("approval", "server", 41, ["approve", "deny"]))

    def test_an_old_style_client_still_reads_every_field_it_knew(self):
        announcements.record_announcement("Arty: task #3 failed", "boom", [], False, "tasks")
        newest = store.list_announcements(0)[-1]
        self.assertLessEqual({"id", "kind", "title", "message", "task_id", "actions", "created"}, set(newest))
        self.assertEqual(newest["kind"], "message")

    def test_a_database_made_before_categories_gains_the_column(self):
        old_db = os.path.join(tempfile.mkdtemp(prefix="arty-old-db-"), "arty.db")
        with mock.patch.object(store, "DB_FILE", old_db):
            with store.connect() as connection:
                connection.execute("CREATE TABLE announcements (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, "
                                   "message TEXT NOT NULL, task_id INTEGER, actions TEXT, created REAL NOT NULL)")
                connection.execute("INSERT INTO announcements (kind, title, message, created) VALUES ('reminder', 'old', '', 1)")
            store.init()
            store.add_announcement("alert", "new", "", category="server")
            rows = store.list_announcements(0)
        self.assertEqual([(row["title"], row["category"]) for row in rows], [("old", "other"), ("new", "server")])


class Schedules(unittest.TestCase):
    def test_a_reminder_is_a_reminder(self):
        sent, patch = captured_sends(schedules.notify)
        with patch:
            schedules.send_reminder({"id": 1, "cron": None, "message": "drink water", "attempts": 0}, datetime(2026, 11, 13, 9, 0))
        self.assertEqual(sent, [("⏰ Reminder", "reminders")])

    def test_calendar_events_and_deadlines_are_told_apart(self):
        sent, patch = captured_sends(schedules.notify)
        with patch, mock.patch.object(schedules.calendar_feed, "due_reminders", return_value=[{"title": "Dentist"}]), \
                mock.patch.object(schedules.calendar_feed, "reminder_text", return_value="Dentist at 3"), \
                mock.patch.object(schedules.personal, "due_deadline_reminders", return_value=["Essay due Friday"]):
            schedules.send_calendar_and_deadline_reminders()
        self.assertEqual(sent, [("📅 Coming up", "calendar"), ("⏰ Deadline", "reminders")])


class Lectures(unittest.TestCase):
    def run_one(self, outcome):
        sent = []
        lecture = {"id": 5, "title": "Networks 3"}
        with mock.patch.object(lectures, "next_queued", side_effect=[lecture, StopWorker()]), \
                mock.patch.object(lectures, "process", return_value=outcome), mock.patch.object(lectures, "_update"):
            with self.assertRaises(StopWorker):
                lectures.worker(None, lambda title, message, **options: sent.append(options.get("category")))
        return sent

    def test_finished_and_failed_lectures_are_lectures(self):
        self.assertEqual(self.run_one({"status": "done", "cards": 4}), ["lectures"])
        self.assertEqual(self.run_one({"status": "failed", "error": "no audio"}), ["lectures"])


class ModelSwitches(unittest.TestCase):
    def pull(self, found, usable=True):
        sent = []
        with mock.patch.object(models, "_ollama"), mock.patch.object(models, "find", return_value=found), \
                mock.patch.object(models, "installed", return_value=[]), mock.patch.object(models, "usable", return_value=usable), \
                mock.patch.object(models, "_write_main_model"), mock.patch.object(models, "describe", return_value="qwen3:8b"):
            models._pull_then_switch("qwen3:8b", "qwen3:14b", lambda title, message, **options: sent.append(options.get("category")))
        return sent

    def test_every_model_switch_outcome_is_an_arty_notice(self):
        self.assertEqual(self.pull({"name": "qwen3:8b"}), ["arty"])
        self.assertEqual(self.pull({"name": "qwen3:8b"}, usable=False), ["arty"])
        self.assertEqual(self.pull(None), ["arty"])


if __name__ == "__main__":
    unittest.main()
