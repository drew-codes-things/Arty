import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_browser_apps import (SAM, BrowserTestCase, FakeDesktopHub, TimedEngine, answer, app_actions,  # noqa: E402
                               brain, browser_apps, call, offline_turns)

import jobs  # noqa: E402

UPLOAD = "workspace__browser_upload"
REPORT = "~/projects/report.pdf"


class UploadingHub(FakeDesktopHub):
    def __init__(self):
        super().__init__(tools=(*browser_apps.TOOLS, UPLOAD))
        self.uploads = []

    def call(self, name, arguments, limit=8000):
        if name != UPLOAD:
            return super().call(name, arguments, limit)
        self.used.append(name)
        self.uploads.append(arguments)
        return "Uploaded report.pdf | https://jobs.example/apply"


def offered(names):
    return [{"type": "function", "function": {"name": name, "description": "", "parameters": {}}} for name in names]


class ApprovalsAreSpentOnce(BrowserTestCase):
    def setUp(self):
        super().setUp()
        offline_turns(self)

    def turn(self, conversation, *replies):
        engine = TimedEngine(*replies)
        with unittest.mock.patch.object(brain.connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(brain.connections, "http_stream", side_effect=engine.streamed, create=True):
            return brain.run_turn(list(conversation))

    def approved_email(self, ask):
        send = call("email_send", to=SAM, subject="hi", body="hi")
        question = self.turn(ask, send, answer("x"))[0]
        return ask + [{"role": "assistant", "content": question}, {"role": "user", "content": "yes"}], send

    def test_the_same_yes_posted_again_never_sends_the_email_twice(self):
        hub = self.desktop()
        approved, send = self.approved_email([{"role": "user", "content": f"email {SAM} saying hi"}])
        first = self.turn(approved, send, answer("Sent."))
        self.assertEqual(len(hub.sent), 1, first[0])
        again = self.turn(approved, send, answer("Sent."))
        self.assertEqual(len(hub.sent), 1)
        self.assertIn("I already did that", again[0])
        self.assertIn("Look in Sent", again[0])

    def test_a_yes_that_cannot_be_noted_as_used_carries_nothing_out(self):
        hub = self.desktop()
        approved, send = self.approved_email([{"role": "user", "content": f"email {SAM} saying hi"}])
        with unittest.mock.patch.object(app_actions, "_write_file", side_effect=PermissionError("read-only")):
            said = self.turn(approved, send, answer("Sent."))
        self.assertEqual(hub.sent, [])
        self.assertEqual(said[0], app_actions.CANT_NOTE_APPROVAL)

    def test_asking_for_the_same_email_again_later_is_a_new_approval(self):
        hub = self.desktop()
        approved, send = self.approved_email([{"role": "user", "content": f"email {SAM} saying hi"}])
        done = self.turn(approved, send, answer("Sent."))
        later = approved + [{"role": "assistant", "content": done[0]}, {"role": "user", "content": f"email {SAM} saying hi"}]
        approved_again, _ = self.approved_email(later)
        self.turn(approved_again, send, answer("Sent."))
        self.assertEqual(len(hub.sent), 2)

    def test_an_approved_desktop_step_posted_again_is_not_done_twice(self):
        hub = UploadingHub()
        patcher = unittest.mock.patch.object(brain.connections, "hub", hub)
        patcher.start()
        self.addCleanup(patcher.stop)
        hub.tools = offered([UPLOAD])
        upload = call(UPLOAD, target="Upload CV", path=REPORT)
        ask = [{"role": "user", "content": "upload my report on the jobs page"}]
        question = self.turn(ask, upload, answer("x"))[0]
        self.assertIn("uploading", question)
        approved = ask + [{"role": "assistant", "content": question}, {"role": "user", "content": "yes"}]
        self.turn(approved, upload, answer("Uploaded."))
        again = self.turn(approved, upload, answer("Uploaded."))
        self.assertEqual(len(hub.uploads), 1)
        self.assertIn("I already did that", again[0])

    def test_a_record_damaged_after_the_yes_fails_closed_instead_of_sending_again(self):
        hub = self.desktop()
        approved, send = self.approved_email([{"role": "user", "content": f"email {SAM} saying hi"}])
        self.turn(approved, send, answer("Sent."))
        self.state.write_text("{ not json")
        again = self.turn(approved, send, answer("Sent."))
        self.assertEqual(len(hub.sent), 1)
        self.assertEqual(again[0], app_actions.CANT_NOTE_APPROVAL)
        self.assertEqual(self.state.read_text(), "{ not json")

    def test_an_approved_inbox_rule_posted_again_is_not_added_twice(self):
        self.desktop()
        rule = call("always_allow_inbox_change", sender="news@shop.example", action="archive")
        ask = [{"role": "user", "content": "stop asking me about mail from news@shop.example, just archive it"}]
        with unittest.mock.patch.object(jobs, "trust_rule", return_value="Rule added.") as trust_rule, \
                unittest.mock.patch.object(app_actions, "connected_apps", return_value={"email"}):
            question = self.turn(ask, rule, answer("x"))[0]
            self.assertIn("without asking each time", question)
            approved = ask + [{"role": "assistant", "content": question}, {"role": "user", "content": "yes"}]
            self.turn(approved, rule, answer("Done."))
            again = self.turn(approved, rule, answer("Done."))
        self.assertEqual(trust_rule.call_count, 1)
        self.assertIn("I already did that", again[0])


class SpentRecord(BrowserTestCase):
    def test_a_spent_approval_is_kept_with_its_time_next_to_the_undo_list(self):
        key = app_actions.spent_key("sending an email", "conversation")
        self.assertIsNone(app_actions.spend_approval(key))
        app_actions.record("email", "hi to Sam")
        first = app_actions.spend_approval(key)
        self.assertIsNotNone(first)
        stored = json.loads(self.state.read_text())
        self.assertEqual([entry["key"] for entry in stored["spent"]], [key])
        self.assertEqual([action["label"] for action in stored["actions"]], ["hi to Sam"])
        self.assertEqual(app_actions.spend_approval(key), first)

    def test_a_damaged_record_refuses_to_spend_and_is_left_as_it_was(self):
        for damaged in ("{ not json", "[]", '{"spent": "key"}', '{"spent": ['):
            with self.subTest(damaged=damaged):
                self.state.write_text(damaged)
                with self.assertRaises(OSError):
                    app_actions.spend_approval(app_actions.spent_key("sending an email", "conversation"))
                self.assertEqual(self.state.read_text(), damaged)

    def test_the_answer_gives_the_time_it_was_done(self):
        said = app_actions.already_done(1_700_000_000.0, {"time_zone": "UTC"})
        self.assertIn("at 22:13", said)

    def test_the_record_stays_bounded(self):
        for number in range(app_actions.KEPT_SPENT_APPROVALS + 5):
            app_actions.spend_approval(f"key-{number}")
        stored = json.loads(self.state.read_text())["spent"]
        self.assertEqual(len(stored), app_actions.KEPT_SPENT_APPROVALS)
        self.assertEqual(stored[-1]["key"], f"key-{app_actions.KEPT_SPENT_APPROVALS + 4}")


if __name__ == "__main__":
    unittest.main()
