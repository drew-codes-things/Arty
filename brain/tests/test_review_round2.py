import io
import os
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import background_tasks  # noqa: E402
import connections  # noqa: E402
import native_calls  # noqa: E402
import calendar_feed  # noqa: E402
import email_watch  # noqa: E402
import expiry  # noqa: E402
import media  # noqa: E402
import personal  # noqa: E402
import phone  # noqa: E402
import policy  # noqa: E402
import research  # noqa: E402
import schedules  # noqa: E402
import sharing  # noqa: E402
import store  # noqa: E402
import timing  # noqa: E402

RULES = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())["approval"]
LINKEDIN = {"url": "https://www.linkedin.com/feed/"}


class ReminderRetries(unittest.TestCase):
    def failing_send(self, attempts):
        schedule = {"id": 7, "cron": None, "message": "drink water", "attempts": attempts}
        with unittest.mock.patch.object(schedules.notify, "send", side_effect=RuntimeError("listener down")), \
                unittest.mock.patch.object(schedules.store, "update_schedule") as update:
            schedules.send_reminder(schedule, datetime(2026, 11, 11, 9, 0))
        return update

    def test_a_failing_reminder_is_tried_again_a_few_times(self):
        update = self.failing_send(0)
        update.assert_called_once()
        self.assertEqual(update.call_args.kwargs["enabled"], 1)
        self.assertEqual(update.call_args.kwargs["attempts"], 1)

    def test_then_it_stops(self):
        update = self.failing_send(schedules.REMINDER_MAX_ATTEMPTS - 1)
        self.assertFalse(any(call.kwargs.get("enabled") == 1 for call in update.call_args_list))

    def test_the_schedules_table_counts_attempts(self):
        store.init()
        with store.connect() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(schedules)")}
        self.assertIn("attempts", columns)


class BrokenDatabase(Server):
    def test_a_task_link_with_a_locked_database_gets_an_error_page(self):
        with unittest.mock.patch.object(brain.store, "get_task", side_effect=sqlite3.OperationalError("database is locked")):
            status, body = self.request("GET", "/hooks/tasks/1/approve?token=x", authorised=False)
        self.assertEqual(status, 503)
        self.assertIn(b"try again", body)

    def test_the_identity_check_with_a_locked_database_is_an_error(self):
        with unittest.mock.patch.object(brain.store, "active_device_hashes", side_effect=sqlite3.OperationalError("locked")):
            status, _ = self.request("GET", "/v1/identity?nonce=" + "ab" * 16, authorised=False)
        self.assertEqual(status, 503)

    def test_calendar_days_must_be_a_real_number(self):
        for days in ("inf", "nan", "-inf"):
            status, _ = self.request("GET", f"/v1/calendar?days={days}")
            self.assertEqual(status, 400, days)


class KeylessModules(unittest.TestCase):
    def test_the_archive_and_models_load_without_any_keys(self):
        code = ("import os\nfor key in ('BRAIN_API_KEY', 'MCPO_API_KEY'):\n    os.environ.pop(key, None)\n"
                "import archive, models, settings\nprint(models.OLLAMA_URL == settings.OLLAMA_URL)")
        folder = str(Path(__file__).resolve().parents[1])
        done = subprocess.run([sys.executable, "-c", code], cwd=folder, capture_output=True, text=True, timeout=60,
                              env={"PATH": "/usr/bin:/bin", "PYTHONPATH": folder})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "True")


class LongNotes(Server):
    def test_a_long_lecture_note_is_shortened_in_search(self):
        personal.note_save("lecture on routing " + "x" * 150_000, 200_000)
        found = personal.note_search("lecture")
        self.assertLess(len(found), personal.NOTE_RESULTS * (personal.NOTE_SEARCH_CHARS + 100))

    def test_native_tool_results_are_capped(self):
        with unittest.mock.patch.object(native_calls, "run_native", return_value=("y" * 50_000, None)):
            result, _ = native_calls.run_native_safely("note_search", {}, {}, False, None)
        self.assertLessEqual(len(result), connections.TOOL_RESULT_CHARS + 200)
        self.assertIn("cut", result[-80:])


class DesktopInputOnSensitivePages(unittest.TestCase):
    def test_keys_and_clicks_on_a_sensitive_page_ask_first(self):
        for name, arguments in [("workspace__press_keys", {"keys": "ctrl+Return"}), ("workspace__click", {"x": 10, "y": 20}),
                                ("workspace__browser_download", {"target": "7"}), ("workspace__browser_press", {"keys": "Space"})]:
            self.assertIsNotNone(policy.approval_reason(name, arguments, dict(LINKEDIN), RULES), name)

    def test_they_still_go_ahead_elsewhere(self):
        news = {"url": "https://news.example.org/story"}
        for name, arguments in [("workspace__press_keys", {"keys": "ctrl+l"}), ("workspace__click", {"x": 10, "y": 20}),
                                ("workspace__browser_press", {"keys": "PageDown"})]:
            self.assertIsNone(policy.approval_reason(name, arguments, dict(news), RULES), name)
        self.assertIsNone(policy.approval_reason("workspace__press_keys", {"keys": "Tab"}, dict(LINKEDIN), RULES))

    def test_a_download_on_an_unread_page_asks(self):
        self.assertIsNotNone(policy.approval_reason("workspace__browser_download", {"target": "3"}, {}, RULES))


class TypedProse(unittest.TestCase):
    def test_prose_that_mentions_mail_or_ssh_is_typed_without_asking(self):
        for text in ["I will mail you the form tomorrow", "Thanks, I'll ssh in later and check.", "Please git push when ready."]:
            self.assertIsNone(policy.approval_reason("workspace__type_text", {"text": text}, {}, RULES), text)

    def test_commands_typed_into_a_terminal_still_ask(self):
        for text in ["git push origin main\n", "cd site && git push", "ssh root@example.org", "echo hi | mail bob@example.org"]:
            self.assertIsNotNone(policy.approval_reason("workspace__type_text", {"text": text}, {}, RULES), text)


class ApprovalWording(unittest.TestCase):
    def test_only_a_plain_yes_approves(self):
        for text in ["ok forget it", "ok scrap that", "sure, actually nah", "ok leave it", "okay skip it", "yeah right",
                     "yes?", "yes and also email it to Bob"]:
            self.assertFalse(policy.is_plain_approval(text), text)
        for text in ["yes", "ok go ahead", "Sure, do it", "yes please", "send it!", "Yep, go for it.", "okay thanks"]:
            self.assertTrue(policy.is_plain_approval(text), text)

    def test_two_long_commands_that_differ_late_are_not_covered_by_one_yes(self):
        first = "git push origin main " + "a" * 300 + " --force"
        second = "git push origin main " + "a" * 300 + " --tags"
        reason = policy.approval_reason("workspace__run_command", {"command": first}, {}, RULES)
        other = policy.approval_reason("workspace__run_command", {"command": second}, {}, RULES)
        self.assertTrue(policy.covers(reason, reason))
        self.assertFalse(policy.covers(reason, other))


class CalendarRepeats(unittest.TestCase):
    def test_events_cannot_repeat_every_few_minutes_or_hours(self):
        for when in ("every 45 minutes", "every 15 minutes", "every 2 hours", "hourly"):
            with self.assertRaises(ValueError, msg=when):
                calendar_feed.parse_when(when)

    def test_daily_and_weekly_events_still_repeat(self):
        _, _, repeat = calendar_feed.parse_when("every monday 6pm")
        self.assertEqual(repeat, "0 18 * * 1")

    def test_29_february_is_found_after_a_leap_year(self):
        self.assertEqual(timing.next_run("0 9 29 2 *", datetime(2028, 3, 1)), datetime(2032, 2, 29, 9, 0))

    def test_one_broken_repeat_does_not_hide_the_other_events(self):
        event = {"starts": time.time(), "ends": time.time() + 3600, "repeat": "0 9 31 2 *"}
        self.assertEqual(calendar_feed._occurrences(event, time.time(), time.time() + 86400), [])

    def test_absurd_lengths_are_refused(self):
        self.assertIn("Couldn't add it", calendar_feed.add("big", "tomorrow 3pm", duration_minutes=10 ** 13))
        with unittest.mock.patch.object(calendar_feed.store, "get_event",
                                        return_value={"id": 1, "starts": 0.0, "ends": 3600.0, "all_day": 0}), \
                unittest.mock.patch.object(calendar_feed.store, "update_event") as update:
            self.assertIn("Couldn't change it", calendar_feed.change(1, duration_minutes=10 ** 13))
        update.assert_not_called()

    def test_singular_intervals_are_understood(self):
        self.assertEqual(timing.parse_when("every 1 hour", datetime(2026, 11, 11, 9, 30))[0], "0 */1 * * *")
        with self.assertRaises(ValueError) as raised:
            timing.parse_when("every 1 minute")
        self.assertIn("5 to 59 minutes", str(raised.exception))


class EmailRepeats(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="arty-email-round2-")
        patch = unittest.mock.patch.object(email_watch, "STATE_FILE", Path(folder) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []

    def check(self):
        listing = "1. 10:42: Bank <alerts@bank.example>: New sign-in to your account | A new device signed in."
        email_watch.check_new(lambda name, arguments: listing, lambda prompt: '{"important": [1]}',
                              lambda title, message, **options: self.sent.append(message))

    def test_the_same_alert_a_week_later_is_judged_again(self):
        self.check()
        self.check()
        self.assertEqual(len(self.sent), 1)
        state = json.loads(email_watch.STATE_FILE.read_text())
        state["times"] = {key: time.time() - 7 * 86400 for key in state["times"]}
        email_watch.STATE_FILE.write_text(json.dumps(state))
        self.check()
        self.assertEqual(len(self.sent), 2)


class CertificateProblems(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="arty-expiry-round2-")
        patch = unittest.mock.patch.object(expiry, "STATE_FILE", Path(folder) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)

    def run_check(self, outcome):
        expiry._last_check["time"] = 0.0
        effect = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with unittest.mock.patch.object(expiry, "certificate_expiry", **effect):
            return expiry.due_warnings({"certificates": ["site.example"]})

    def test_a_problem_that_comes_back_after_a_renewal_is_reported_again(self):
        self.assertEqual(len(self.run_check(expiry.CertificateProblem("it has expired"))), 1)
        self.assertEqual(self.run_check(expiry.CertificateProblem("it has expired")), [])
        self.assertEqual(self.run_check(time.time() + 90 * 86400), [])
        self.assertEqual(len(self.run_check(expiry.CertificateProblem("it has expired"))), 1)


class ScriptedPictures(unittest.TestCase):
    def test_pdf_and_binary_eps_are_refused(self):
        folder = Path(tempfile.mkdtemp(prefix="arty-scripted-"))
        for number, start in enumerate((b"%PDF-1.7\n", b"\xc5\xd0\xd3\xc6\x00\x00")):
            picture = folder / f"picture-{number}"
            picture.write_bytes(start + b"\x00" * 64)
            with self.assertRaises(media.MediaError):
                media.refuse_scripted_image(str(picture))

    def test_web_pictures_are_checked_before_shrinking(self):
        big_svg = b"<svg xmlns='http://www.w3.org/2000/svg'>" + b" " * (sharing.MAX_PICTURE_BYTES + 10)
        with unittest.mock.patch.object(media, "_run") as run:
            with self.assertRaises(sharing.ShareError):
                sharing.phone_sized(big_svg, "image/png", "x.png")
        run.assert_not_called()

    def test_raw_attributes_are_off_in_pandoc(self):
        self.assertIn("-raw_attribute", sharing.PANDOC_READER)

    def test_browser_profiles_are_private(self):
        for path in ("/home/ai/.gmail-chrome/Default/Cookies", "/home/ai/.browser-profile/Default/Login Data",
                     "/home/ai/.local/browser-agent/token"):
            self.assertTrue(sharing.is_private(path), path)


class UploadRace(unittest.TestCase):
    def test_the_same_file_uploaded_at_once_from_several_places_is_stored_once(self):
        content = b"z" * (3 * 1024 * 1024)
        results, errors = [], []

        def upload():
            try:
                results.append(media.save_stream(io.BytesIO(content), len(content), "same.bin", "", "upload")["id"])
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=upload) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.assertIsNotNone(media.get(results[0]))

    def test_meta_is_replaced_whole(self):
        meta = media.save_bytes(b"round two meta", "m.txt", "text/plain", "upload")
        with unittest.mock.patch.object(media.os, "replace", wraps=os.replace) as replaced:
            self.assertTrue(media.touch(meta["id"]))
        self.assertEqual(Path(replaced.call_args.args[1]).name, "meta.json")
        self.assertIsNotNone(media.get(meta["id"]))


class UploadFailures(Server):
    def test_a_disk_problem_is_a_clear_503(self):
        with unittest.mock.patch.object(brain.media, "save_stream", side_effect=OSError(28, "No space left on device")):
            status, body = self.request("POST", "/v1/files", body={"x": 1})
        self.assertEqual(status, 503)
        self.assertNotIn(b"No space", body)


class DownloadLinksInTheLog(unittest.TestCase):
    def test_the_token_in_a_download_path_is_not_logged(self):
        line = brain.logged_request_line('"GET /arty-files/0123456789abcdef01234567/SeCrEtToKeN123/cat.jpg HTTP/1.1" 200 -')
        self.assertNotIn("SeCrEtToKeN123", line)
        self.assertIn("cat.jpg", line)
        self.assertNotIn("abc123", brain.logged_request_line('"POST /hooks/notify?token=abc123 HTTP/1.1" 200 -'))


class BusyChats(Server):
    def test_too_many_direct_chats_at_once_get_503(self):
        taken = 0
        while brain.direct_chat_slots.acquire(blocking=False):
            taken += 1
        try:
            status, _ = self.request("POST", "/v1/chat/completions",
                                     body={"model": "Arty", "messages": [{"role": "user", "content": "hi"}]})
        finally:
            for _ in range(taken):
                brain.direct_chat_slots.release()
        self.assertEqual(taken, brain.MAX_DIRECT_CHATS)
        self.assertEqual(status, 503)


class QuizClients(unittest.TestCase):
    def test_the_number_of_remembered_quizzes_is_capped(self):
        for number in range(personal.MAX_QUIZ_CLIENTS + 20):
            personal._quiz_for(f"client-{number}")
        self.assertLessEqual(len(personal._quizzes), personal.MAX_QUIZ_CLIENTS)


class CallsNeedAPerson(unittest.TestCase):
    def test_things_that_are_not_people_are_not_called(self):
        for text in ["phone number", "call stack", "Ring topology", "message queue", "text editor", "text file",
                     "text to speech", "ring road", "call centre", "phone plan comparison", "message format for syslog",
                     "Call stack"]:
            self.assertIsNone(phone.from_request(text), text)

    def test_people_still_are(self):
        for text, who in [("call mum", "Mum"), ("ring my brother", "Brother"), ("call Sam", "Sam"),
                          ("ring 07700 900123", "07700 900123"), ("call the dentist", "Dentist"), ("ring mum and dad", "Mum and dad")]:
            self.assertEqual(phone.from_request(text)["who"], who, text)

    def test_a_reference_to_earlier_is_not_a_call(self):
        self.assertIsNone(brain.phone_action("call the first one"))
        self.assertIsNone(brain.phone_action("text them that I'm late"))
        self.assertEqual(brain.phone_action("call mum")["who"], "Mum")


class FollowUpQuestions(unittest.TestCase):
    def test_follow_ups_do_not_go_to_a_fresh_web_search(self):
        for text in ["is that the latest version", "the latest one please", "and the best one?", "so which is the best",
                     "what about the reviews", "best practice for that?", "what is the best approach here",
                     "what do you recommend"]:
            self.assertFalse(research.is_quick_question(text), text)

    def test_fresh_questions_still_do(self):
        for text in ["what is the best laptop this year", "who won the world cup in 2022", "is the iphone 17 worth it",
                     "best pizza recipe", "which is the best budget laptop", "what laptop would you recommend for coding"]:
            self.assertTrue(research.is_quick_question(text), text)


class ApplyingMarker(unittest.TestCase):
    def run_task(self, kind, decision):
        task = {"id": 3, "history": None, "title": "t", "instructions": "i", "tools_used": [],
                "waiting": {"kind": kind, "decision": decision, "call": {"name": "workspace__browser_click"}}}
        with unittest.mock.patch.object(connections, "load_config", return_value={}), \
                unittest.mock.patch.object(brain.store, "update_task") as update, \
                unittest.mock.patch.object(brain.store, "get_task", return_value={"status": "cancelled"}), \
                unittest.mock.patch.object(background_tasks, "apply_decision"), \
                unittest.mock.patch.object(connections.hub, "refresh"), \
                unittest.mock.patch.object(background_tasks, "run_agent", return_value={"status": "cancelled", "tools_used": []}):
            background_tasks.process_task(task)
        return any((call.kwargs.get("waiting") or {}).get("applying") for call in update.call_args_list)

    def test_only_an_approval_being_carried_out_is_marked(self):
        self.assertTrue(self.run_task("approval", "approve"))
        self.assertFalse(self.run_task("approval", "deny"))
        self.assertFalse(self.run_task("question", "done"))


if __name__ == "__main__":
    unittest.main()
