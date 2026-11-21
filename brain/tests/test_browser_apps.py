import base64
import json
import os
import sys
import tempfile
import types
import unittest
import unittest.mock
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-browser-apps-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json"),
                  ("APP_ACTIONS_STATE", "app-actions.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import agent_loop  # noqa: E402
import app_actions  # noqa: E402
import brain  # noqa: E402
import browser_apps  # noqa: E402
import connections  # noqa: E402
import mail_box  # noqa: E402
import own_calendar  # noqa: E402
import own_drive  # noqa: E402
import policy  # noqa: E402
from api_support import ApiError  # noqa: E402
from test_app_actions import AppTestCase, call  # noqa: E402
from test_round_timings import TimedEngine, answer, offline_turns  # noqa: E402

LONDON = ZoneInfo("Europe/London")
CONFIG = {"time_zone": "Europe/London"}
SAM = "sam@example.org"
DRIVE_ORIGIN = "https://drive.example.org"
CALENDAR_ORIGIN = "https://calendar.example.org"
TOMORROW = date.today() + timedelta(days=1)


def utc_text(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeCalendarApp:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.made = 0

    def add(self, title, start: datetime, end: datetime, recurring=False, calendar="home"):
        self.made += 1
        item = f"item-{self.made}.ics"
        self.items[(calendar, item)] = {"calendarId": calendar, "itemId": item, "uid": f"uid-{self.made}", "title": title,
                                        "location": "", "notes": "", "allDay": False,
                                        "start": start.strftime("%Y-%m-%dT%H:%M"), "end": end.strftime("%Y-%m-%dT%H:%M"),
                                        "recurring": recurring, "etag": f'"{self.made}-1"'}
        return item

    def occurrence(self, stored):
        found = dict(stored)
        if found["allDay"]:
            found["startsAt"] = found["endsAt"] = ""
            return found
        found["startsAt"] = utc_text(datetime.fromisoformat(found["start"]).replace(tzinfo=LONDON))
        found["endsAt"] = utc_text(datetime.fromisoformat(found["end"]).replace(tzinfo=LONDON))
        return found

    def bump(self, stored):
        number, version = stored["etag"].strip('"').split("-")
        stored["etag"] = f'"{number}-{int(version) + 1}"'

    def handle(self, method, path, body, if_match):
        self.calls.append((method, path, body, if_match))
        parsed = urllib.parse.urlsplit(path)
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/")]
        if (method, parsed.path) == ("GET", "/api/calendars"):
            return 200, {"calendars": [{"id": "home", "name": "Home"}]}
        if (method, parsed.path) == ("GET", "/api/events"):
            return 200, {"events": [self.occurrence(stored) for stored in self.items.values()]}
        if (method, parsed.path) == ("POST", "/api/events"):
            self.made += 1
            item = f"item-{self.made}.ics"
            self.items[(body["calendarId"], item)] = {**{key: body.get(key, "") for key in ("title", "location", "notes")},
                                                      "calendarId": body["calendarId"], "itemId": item, "uid": f"uid-{self.made}",
                                                      "allDay": bool(body.get("allDay")), "start": body["start"],
                                                      "end": body["end"], "recurring": False, "etag": f'"{self.made}-1"'}
            return 201, {"calendarId": body["calendarId"], "itemId": item, "uid": f"uid-{self.made}"}
        key = (parts[3], parts[5])
        stored = self.items.get(key)
        if stored is None:
            return 404, {"error": "that event does not exist"}
        if if_match and if_match != stored["etag"]:
            return 409, {"error": "changed elsewhere"}
        if method == "GET":
            return 200, self.occurrence(stored)
        if method == "PATCH":
            stored.update(body)
            self.bump(stored)
            return 200, self.occurrence(stored)
        del self.items[key]
        return 204, None


class FakeDriveApp:
    def __init__(self):
        self.nodes = {1: {"id": 1, "name": "Work", "kind": "folder", "parent": 0},
                      2: {"id": 2, "name": "plan.pdf", "kind": "file", "parent": 0}}
        self.contents = {}
        self.trashed = []
        self.shares = {}
        self.calls = []

    def handle(self, method, path, body, if_match):
        self.calls.append((method, path, body))
        parsed = urllib.parse.urlsplit(path)
        if method == "GET" and parsed.path.startswith("/api/folders/"):
            folder = int(parsed.path.rsplit("/", 1)[1])
            return 200, {"items": [node for node in self.nodes.values() if node["parent"] == folder]}
        if method == "GET" and parsed.path == "/api/search":
            wanted = urllib.parse.parse_qs(parsed.query)["q"][0].lower()
            return 200, {"items": [node for node in self.nodes.values() if wanted in node["name"].lower()]}
        if (method, parsed.path) == ("POST", "/api/nodes/trash"):
            self.trashed.extend(body["ids"])
            return 200, {"ok": True}
        if (method, parsed.path) == ("POST", "/api/shares"):
            share = {"id": 70 + len(self.shares), "token": f"tok{len(self.shares)}", "nodeId": body["nodeId"]}
            self.shares[share["id"]] = share
            return 201, share
        if method == "DELETE" and parsed.path.startswith("/api/shares/"):
            self.shares.pop(int(parsed.path.rsplit("/", 1)[1]))
            return 204, None
        return 404, {"error": "not found"}

    def upload(self, name, folder_id, data):
        node = {"id": 100 + len(self.contents), "name": name, "kind": "file", "parent": folder_id}
        self.nodes[node["id"]] = node
        self.contents[node["id"]] = data
        return node


class FakeDesktopHub:
    def __init__(self, signed_in=True, tools=browser_apps.TOOLS):
        self.calendar = FakeCalendarApp()
        self.drive = FakeDriveApp()
        self.signed_in = signed_in
        self.routes = {name: "workspace" for name in tools}
        self.tools = []
        self.sent = []
        self.used = []
        self.gmail_answer = {"sent": True, "confirmed": "Gmail said the message was sent"}
        self.message_sender = SAM

    def refresh(self):
        return None

    def blocked(self, origin):
        return (f"BLOCKED: the app ({origin}) isn't signed in in the browser on your desktop. Do not try to sign in "
                "yourself. Call ask_user to ask the user (as 'you') to sign in, then ask again.")

    def call(self, name, arguments, limit=8000):
        self.used.append(name)
        if name == browser_apps.SEND_TOOL:
            self.sent.append(arguments)
            return self.gmail_answer if isinstance(self.gmail_answer, str) else json.dumps(self.gmail_answer)
        if name == browser_apps.MESSAGE_TOOL:
            return json.dumps({"subject": "Dinner?", "sender": "Sam", "email": self.message_sender, "when": "Tue 09:00"})
        if name == browser_apps.UPLOAD_TOOL:
            if not self.signed_in:
                return self.blocked(DRIVE_ORIGIN)
            node = self.drive.upload(arguments["name"], arguments["folder_id"], base64.b64decode(arguments["data_base64"]))
            return json.dumps({"status": 200, "body": node, "origin": DRIVE_ORIGIN})
        app = self.calendar if arguments["app"] == "calendar" else self.drive
        origin = CALENDAR_ORIGIN if app is self.calendar else DRIVE_ORIGIN
        if not self.signed_in:
            return self.blocked(origin)
        body = json.loads(arguments["body_json"]) if arguments.get("body_json") else None
        status, answer_body = app.handle(arguments["method"], arguments["path"], body, arguments.get("if_match", ""))
        return json.dumps({"status": status, "body": answer_body, "time_zone": "Europe/London", "origin": origin})


class BrowserTestCase(AppTestCase):
    def desktop(self, **options) -> FakeDesktopHub:
        hub = FakeDesktopHub(**options)
        patcher = unittest.mock.patch.object(connections, "hub", hub)
        patcher.start()
        self.addCleanup(patcher.stop)
        for module, name in ((mail_box, "mail.env"), (own_calendar, "own-apps.env"), (own_drive, "drive.env")):
            self.settings_file(module, name, {})
        return hub

    def prepare(self, name, arguments, words=()):
        prepared = app_actions.prepare(name, arguments, list(words), CONFIG)
        self.assertNotIsInstance(prepared, str, prepared)
        return prepared


def at(day: date, hour: int) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


class CalendarThroughTheBrowser(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.hub = self.desktop()
        self.dentist = self.hub.calendar.add("Dentist", at(TOMORROW, 9), at(TOMORROW, 9).replace(minute=30))
        self.reference = own_calendar.reference_for(f"home/{self.dentist}")

    def test_listing_needs_no_settings_file_and_gives_references(self):
        listed = app_actions.run("calendar_app_events", {"days": 3}, CONFIG)
        self.assertIn(f"#{self.reference}", listed)
        self.assertIn("Dentist", listed)
        self.assertIn(app_actions.CALENDAR_IS_DATA, listed)

    def test_adding_asks_first_then_writes_local_time_and_undo_removes_it(self):
        prepared = self.prepare("calendar_app_add", {"title": "Dinner with Sam", "when": f"{TOMORROW.isoformat()} 19:00"})
        self.assertIn('adding "Dinner with Sam" to your Starfire Calendar app', prepared.card)
        self.assertEqual([made for made in self.hub.calendar.calls if made[0] == "POST"], [])
        self.assertIn("Added to the Starfire Calendar app", app_actions.carry_out("calendar_app_add", prepared))
        posted = [made for made in self.hub.calendar.calls if made[0] == "POST"][0][2]
        self.assertEqual((posted["start"], posted["end"], posted["allDay"]),
                         (f"{TOMORROW.isoformat()}T19:00", f"{TOMORROW.isoformat()}T20:00", False))
        self.assertEqual(json.loads(self.state.read_text())["actions"][-1]["via"], app_actions.VIA_BROWSER)
        undo = self.prepare("undo_app_action", {})
        self.assertIn("Undone", app_actions.carry_out("undo_app_action", undo))
        self.assertEqual([stored["title"] for stored in self.hub.calendar.items.values()], ["Dentist"])

    def test_an_all_day_event_sends_the_last_day_inclusive(self):
        prepared = self.prepare("calendar_app_add", {"title": "Holiday", "when": TOMORROW.isoformat()})
        app_actions.carry_out("calendar_app_add", prepared)
        posted = [made for made in self.hub.calendar.calls if made[0] == "POST"][0][2]
        self.assertEqual((posted["start"], posted["end"], posted["allDay"]), (TOMORROW.isoformat(), TOMORROW.isoformat(), True))

    def test_moving_sends_the_etag_and_undo_moves_it_back(self):
        prepared = self.prepare("calendar_app_move", {"event": self.reference, "when": f"{TOMORROW.isoformat()} 15:00"})
        self.assertIn("from", prepared.card)
        app_actions.carry_out("calendar_app_move", prepared)
        patch = [made for made in self.hub.calendar.calls if made[0] == "PATCH"][0]
        self.assertEqual(patch[3], '"1-1"')
        self.assertEqual(patch[2]["start"], f"{TOMORROW.isoformat()}T15:00")
        self.assertEqual(patch[2]["end"], f"{TOMORROW.isoformat()}T15:30")
        app_actions.carry_out("undo_app_action", self.prepare("undo_app_action", {}))
        self.assertEqual(self.hub.calendar.items[("home", self.dentist)]["start"], f"{TOMORROW.isoformat()}T09:00")

    def test_cancelling_deletes_it_and_undo_puts_it_back(self):
        prepared = self.prepare("calendar_app_cancel", {"event": self.reference})
        app_actions.carry_out("calendar_app_cancel", prepared)
        self.assertEqual(self.hub.calendar.items, {})
        app_actions.carry_out("undo_app_action", self.prepare("undo_app_action", {}))
        self.assertEqual([stored["title"] for stored in self.hub.calendar.items.values()], ["Dentist"])

    def test_a_change_made_elsewhere_is_not_overwritten(self):
        prepared = self.prepare("calendar_app_move", {"event": self.reference, "when": f"{TOMORROW.isoformat()} 15:00"})
        self.hub.calendar.bump(self.hub.calendar.items[("home", self.dentist)])
        self.assertIn("changed somewhere else", app_actions.carry_out("calendar_app_move", prepared))
        self.assertEqual(self.hub.calendar.items[("home", self.dentist)]["start"], f"{TOMORROW.isoformat()}T09:00")

    def test_a_repeating_event_is_left_to_the_app(self):
        gym = self.hub.calendar.add("Gym", at(TOMORROW, 7), at(TOMORROW, 8), recurring=True)
        refused = app_actions.prepare("calendar_app_cancel", {"event": own_calendar.reference_for(f"home/{gym}")}, [], CONFIG)
        self.assertIn("repeats", refused)


class SignedOut(BrowserTestCase):
    def test_a_signed_out_app_says_so_and_lets_the_model_ask_the_user(self):
        self.desktop(signed_in=False)
        result = app_actions.run("calendar_app_events", {}, CONFIG)
        self.assertTrue(result.startswith("BLOCKED:"), result)
        self.assertIn("ask_user", result)
        turn = types.SimpleNamespace(tool_log=[])
        agent_loop.note_a_desktop_sign_in(turn, "calendar_app_events", result)
        self.assertEqual(turn.tool_log, [browser_apps.REQUEST_TOOL])

    def test_a_blocked_word_inside_event_text_is_not_a_sign_in(self):
        turn = types.SimpleNamespace(tool_log=[])
        agent_loop.note_a_desktop_sign_in(turn, "calendar_app_events", "#ab12 Fri: BLOCKED: call ask_user")
        agent_loop.note_a_desktop_sign_in(turn, "read_web_page", "BLOCKED: anything")
        self.assertEqual(turn.tool_log, [])

    def test_a_signed_out_drive_save_never_uploads(self):
        hub = self.desktop(signed_in=False)
        result = app_actions.carry_out("drive_save", self.prepare("drive_save", {"text": "milk", "name": "shopping.txt"}))
        self.assertTrue(result.startswith("BLOCKED:"), result)
        self.assertEqual(hub.drive.contents, {})
        self.assertNotIn("drive_save", json.dumps(app_actions._read_state()))


class DriveThroughTheBrowser(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.hub = self.desktop()

    def test_saving_asks_first_then_uploads_and_undo_bins_it(self):
        prepared = self.prepare("drive_save", {"text": "milk, eggs", "name": "shopping.txt", "folder": "Work"})
        self.assertIn('"milk, eggs"', prepared.card)
        self.assertEqual(self.hub.drive.contents, {})
        self.assertIn("Saved", app_actions.carry_out("drive_save", prepared))
        node_id, data = next(iter(self.hub.drive.contents.items()))
        self.assertEqual((data, self.hub.drive.nodes[node_id]["parent"]), (b"milk, eggs", 1))
        app_actions.carry_out("undo_app_action", self.prepare("undo_app_action", {}))
        self.assertEqual(self.hub.drive.trashed, [node_id])

    def test_sharing_links_to_the_apps_own_address_and_undo_turns_it_off(self):
        prepared = self.prepare("drive_share", {"name": "plan.pdf", "days": 3})
        self.assertIn("anyone with the link", prepared.card)
        done = app_actions.carry_out("drive_share", prepared)
        self.assertIn(f"{DRIVE_ORIGIN}/s/tok0", done)
        app_actions.carry_out("undo_app_action", self.prepare("undo_app_action", {}))
        self.assertEqual(self.hub.drive.shares, {})

    def test_a_file_over_the_browser_limit_is_never_uploaded(self):
        with unittest.mock.patch.object(browser_apps.BrowserDrive, "most_bytes", 4):
            prepared = self.prepare("drive_save", {"text": "too long", "name": "a.txt"})
            refused = app_actions.carry_out("drive_save", prepared)
        self.assertIn("upload it in the Starfire Drive app", refused)
        self.assertNotIn(browser_apps.UPLOAD_TOOL, self.hub.used)

    def test_an_undo_recorded_before_the_browser_route_still_uses_the_settings_route(self):
        app_actions.record("drive_save", '"old.txt" in My Drive', node_id=5)
        refused = app_actions.carry_out("undo_app_action", self.prepare("undo_app_action", {}))
        self.assertIn(app_actions.DRIVE_NOT_SET_UP, refused)
        self.assertEqual(self.hub.drive.trashed, [])


class GmailThroughTheBrowser(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.hub = self.desktop()

    def test_sending_needs_no_mail_settings_and_shows_the_exact_email(self):
        prepared = self.prepare("email_send", {"to": SAM, "subject": "Late", "body": "Running late."}, [f"email {SAM}"])
        self.assertIn(browser_apps.GMAIL_SENDER, prepared.card)
        self.assertIn('"Running late."', prepared.card)
        self.assertEqual(self.hub.sent, [])
        self.assertIn("Sent through Gmail", app_actions.carry_out("email_send", prepared))
        self.assertEqual(self.hub.sent, [{"to": SAM, "cc": "", "subject": "Late", "body": "Running late."}])

    def test_an_unconfirmed_send_is_reported_and_never_retried(self):
        self.hub.gmail_answer = {"error": "Gmail didn't confirm"}
        prepared = self.prepare("email_send", {"to": SAM, "subject": "Late", "body": "x"}, [f"email {SAM}"])
        result = app_actions.carry_out("email_send", prepared)
        self.assertIn("look in Sent", result)
        self.assertEqual(len(self.hub.sent), 1)

    def test_a_desktop_that_stopped_answering_mid_send_says_to_look_in_sent(self):
        self.hub.gmail_answer = "browser error: the Gmail window on my desktop didn't say whether the email went"
        prepared = self.prepare("email_send", {"to": SAM, "subject": "Late", "body": "x"}, [f"email {SAM}"])
        result = app_actions.carry_out("email_send", prepared)
        self.assertIn("look in Sent before trying again", result)
        self.assertNotIn("email", json.dumps(app_actions._read_state()))

    def test_an_address_the_user_never_typed_is_refused_before_gmail_is_touched(self):
        refused = app_actions.prepare("email_send", {"to": "collect@evil.example", "subject": "x", "body": "y"},
                                      ["email sam"], CONFIG)
        self.assertIsInstance(refused, str)
        self.assertEqual(self.hub.used, [])

    def test_a_reply_goes_to_the_sender_gmail_reports(self):
        prepared = self.prepare("email_reply", {"query": "from:sam", "number": 1, "body": "Yes, Friday works."})
        self.assertIn(f"<{SAM}>", prepared.card)
        self.assertIn("Re: Dinner?", prepared.card)
        app_actions.carry_out("email_reply", prepared)
        self.assertEqual(self.hub.sent[0]["to"], SAM)
        self.assertEqual(self.hub.sent[0]["subject"], "Re: Dinner?")

    def test_a_reply_to_a_sender_with_a_trailing_line_break_is_refused(self):
        self.hub.message_sender = SAM + "\n"
        refused = app_actions.prepare("email_reply", {"query": "from:sam", "number": 1, "body": "Yes."}, [], CONFIG)
        self.assertIsInstance(refused, str)
        self.assertIn("no sender address", refused)
        self.assertEqual(self.hub.sent, [])

    def test_mailbox_only_tools_stay_off_without_mail_settings(self):
        offered = {name for name in app_actions.TOOLS if app_actions.is_offered(name, app_actions.connected_apps())}
        self.assertEqual(offered, set(app_actions.TOOLS) - {"email_inbox", "email_read", "email_draft"})


class NeverDirectlyCallable(BrowserTestCase):
    def setUp(self):
        super().setUp()
        offline_turns(self)
        self.hub = self.desktop()
        self.hub.tools = [{"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                          for name in sorted(browser_apps.TOOLS)]

    def test_the_model_is_never_offered_them(self):
        run = types.SimpleNamespace(background=False, agent=False, skip_groups=(), client_tools=None, allowed_tools=None)
        offered = {tool["function"]["name"] for tool in agent_loop.tools_for_turn({}, run, False, ((),))}
        self.assertFalse(offered & browser_apps.TOOLS)

    def test_a_direct_call_from_the_model_is_not_run(self):
        conversation = [{"role": "user", "content": f"email {SAM} saying hi"}]
        with unittest.mock.patch.object(brain.connections, "http_json",
                                        side_effect=TimedEngine(call(browser_apps.SEND_TOOL, to=SAM, subject="hi", body="hi"),
                                                                answer("Done."))):
            brain.run_turn(conversation)
        self.assertEqual(self.hub.sent, [])

    def test_the_tools_api_refuses_them(self):
        handler = object.__new__(brain.Handler)
        for name in browser_apps.TOOLS:
            with self.assertRaises(ApiError) as refused:
                handler.call_tool(name, {"arguments": {}, "approved": True})
            self.assertEqual(refused.exception.status, 400)
        self.assertEqual(self.hub.used, [])

    def test_changing_ones_need_approval_and_count_after_outside_content(self):
        rules = json.loads((Path(brain.__file__).parent / "brain.json").read_text())["approval"]
        for name in browser_apps.TOOLS - {browser_apps.MESSAGE_TOOL}:
            self.assertTrue(policy.approval_reason(name, {}, {}, rules), name)
            self.assertTrue(policy.matches(name, policy.AFTER_UNTRUSTED), name)


if __name__ == "__main__":
    unittest.main()
