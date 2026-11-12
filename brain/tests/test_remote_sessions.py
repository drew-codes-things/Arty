import json
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import remote_sessions  # noqa: E402
from remote_sessions import Caller, RemoteError, Sessions  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import store  # noqa: E402

LAPTOP = Caller("device:1", "Laptop", False)
PHONE = Caller("device:2", "Phone", True)
LOCKED_PHONE = Caller("device:3", "Old phone", False)
MAIN = Caller("main", "Arty's own key", True)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def started(sessions, owner=LAPTOP, **details):
    fields = {"name": "Laptop", "folder": "arty-cli", "model": "arty/qwen3:14b", "mode": "default", **details}
    return sessions.create(owner, fields)["id"]


class Starting(unittest.TestCase):
    def test_a_session_lists_its_computer_folder_model_and_mode(self):
        sessions = Sessions()
        session_id = started(sessions)
        listed = sessions.listed(PHONE)
        self.assertEqual([entry["id"] for entry in listed], [session_id])
        self.assertEqual(listed[0]["name"], "Laptop")
        self.assertEqual(listed[0]["folder"], "arty-cli")
        self.assertEqual(listed[0]["model"], "arty/qwen3:14b")
        self.assertEqual(listed[0]["mode"], "default")
        self.assertEqual(listed[0]["computer_key"], "Laptop")

    def test_labels_are_cut_to_size_and_control_characters_removed(self):
        sessions = Sessions()
        started(sessions, name="pc\n\x00" + "x" * 500)
        name = sessions.listed(MAIN)[0]["name"]
        self.assertLessEqual(len(name), remote_sessions.MAX_LABEL_CHARS)
        self.assertNotIn("\n", name)
        self.assertNotIn("\x00", name)

    def test_there_is_a_cap_on_live_sessions_per_key_and_in_total(self):
        sessions = Sessions()
        for _ in range(remote_sessions.MAX_SESSIONS_PER_KEY):
            started(sessions)
        with self.assertRaises(RemoteError) as caught:
            started(sessions)
        self.assertEqual(caught.exception.status, 429)
        with unittest.mock.patch.object(remote_sessions, "MAX_SESSIONS", remote_sessions.MAX_SESSIONS_PER_KEY):
            with self.assertRaises(RemoteError):
                started(sessions, owner=MAIN)

    def test_computers_sharing_the_main_key_each_get_their_own_session_cap(self):
        sessions = Sessions()
        for _ in range(remote_sessions.MAX_SESSIONS_PER_KEY):
            started(sessions, owner=MAIN, name="Desktop")
        started(sessions, owner=MAIN, name="Laptop")
        with self.assertRaises(RemoteError) as caught:
            started(sessions, owner=MAIN, name="Desktop")
        self.assertEqual(caught.exception.status, 429)

    def test_a_device_key_has_one_cap_whatever_its_sessions_are_called(self):
        sessions = Sessions()
        for number in range(remote_sessions.MAX_SESSIONS_PER_KEY):
            started(sessions, name=f"Laptop {number}")
        with self.assertRaises(RemoteError) as caught:
            started(sessions, name="Another name")
        self.assertEqual(caught.exception.status, 429)


class WhoMayControl(unittest.TestCase):
    def setUp(self):
        self.sessions = Sessions()
        self.session_id = started(self.sessions)

    def test_a_device_key_without_remote_control_is_refused_everywhere(self):
        for attempt in (lambda: self.sessions.listed(LOCKED_PHONE),
                        lambda: self.sessions.events(LOCKED_PHONE, self.session_id, 0, 0),
                        lambda: self.sessions.send_message(LOCKED_PHONE, self.session_id, "hi"),
                        lambda: self.sessions.interrupt(LOCKED_PHONE, self.session_id)):
            with self.assertRaises(RemoteError) as caught:
                attempt()
            self.assertEqual(caught.exception.status, 403)

    def test_the_computers_own_key_cannot_control_it_unless_allowed(self):
        with self.assertRaises(RemoteError) as caught:
            self.sessions.send_message(LAPTOP, self.session_id, "hi")
        self.assertEqual(caught.exception.status, 403)

    def test_the_main_key_and_allowed_devices_can_control(self):
        self.sessions.send_message(MAIN, self.session_id, "from starfire")
        self.sessions.send_message(PHONE, self.session_id, "from the phone")
        inbox = self.sessions.inbox(LAPTOP, self.session_id, 0)
        self.assertEqual([(item["text"], item["by"]) for item in inbox],
                         [("from starfire", "Arty's own key"), ("from the phone", "Phone")])

    def test_only_the_key_that_started_a_session_can_feed_or_read_it(self):
        for intruder in (PHONE, MAIN, LOCKED_PHONE):
            with self.assertRaises(RemoteError) as caught:
                self.sessions.inbox(intruder, self.session_id, 0)
            self.assertEqual(caught.exception.status, 403)
            with self.assertRaises(RemoteError):
                self.sessions.add_events(intruder, self.session_id, [{"type": "assistant", "text": "fake"}])
            with self.assertRaises(RemoteError):
                self.sessions.end(intruder, self.session_id)

    def test_an_unknown_session_is_not_found(self):
        with self.assertRaises(RemoteError) as caught:
            self.sessions.send_message(PHONE, "nope", "hi")
        self.assertEqual(caught.exception.status, 404)


class Naming(unittest.TestCase):
    def test_each_terminal_gets_a_readable_title_from_its_folder(self):
        sessions = Sessions()
        started(sessions, folder="greeter")
        started(sessions, folder="notes")
        self.assertEqual([entry["title"] for entry in sessions.listed(PHONE)], ["greeter", "notes"])

    def test_terminals_in_the_same_folder_get_distinct_numbered_titles(self):
        sessions = Sessions()
        first, second, third = (sessions.create(LAPTOP, {"name": "Laptop", "folder": "greeter"}) for _ in range(3))
        self.assertEqual([first["title"], second["title"], third["title"]], ["greeter", "greeter 2", "greeter 3"])
        sessions.end(LAPTOP, second["id"])
        self.assertEqual(sessions.create(MAIN, {"name": "Desk", "folder": "greeter"})["title"], "greeter 2")

    def test_a_terminal_with_no_folder_is_titled_after_its_computer(self):
        sessions = Sessions()
        started(sessions, folder="")
        started(sessions, folder="")
        self.assertEqual([entry["title"] for entry in sessions.listed(PHONE)], ["Laptop", "Laptop 2"])

    def test_a_title_the_computer_asks_for_is_used_and_still_kept_distinct(self):
        sessions = Sessions()
        started(sessions, folder="greeter", title="tests run\x00")
        started(sessions, folder="greeter", title="tests run")
        self.assertEqual([entry["title"] for entry in sessions.listed(PHONE)], ["tests run", "tests run 2"])

    def test_the_owner_can_rename_its_terminal_and_readers_are_told(self):
        sessions = Sessions()
        session_id = started(sessions, folder="greeter")
        other = started(sessions, folder="notes")
        self.assertEqual(sessions.rename(LAPTOP, session_id, "notes"), "notes 2")
        self.assertEqual(sessions.listed(PHONE)[0]["title"], "notes 2")
        self.assertEqual(sessions.events(PHONE, session_id, 0, 0)["events"][-1]["type"], "status")
        with self.assertRaises(RemoteError) as refused:
            sessions.rename(PHONE, other, "mine")
        self.assertEqual(refused.exception.status, 403)
        with self.assertRaises(RemoteError) as empty:
            sessions.rename(LAPTOP, session_id, "  ")
        self.assertEqual(empty.exception.status, 400)


class History(unittest.TestCase):
    def setUp(self):
        self.sessions = Sessions()
        self.session_id = started(self.sessions)

    def test_earlier_conversation_is_kept_marked_as_history(self):
        self.sessions.add_events(LAPTOP, self.session_id, [
            {"type": "user", "text": "fix the test", "history": True},
            {"type": "assistant", "text": "Done.", "history": "yes"},
            {"type": "user", "text": "now the docs"},
        ])
        events = self.sessions.events(PHONE, self.session_id, 0, 0)["events"]
        self.assertEqual([event.get("history", False) for event in events], [True, False, False])

    def test_a_compacted_conversation_reaches_readers_with_its_summary(self):
        self.sessions.add_events(LAPTOP, self.session_id, [{"type": "compacted", "text": "We fixed greet()."}])
        events = self.sessions.events(PHONE, self.session_id, 0, 0)["events"]
        self.assertEqual([(event["type"], event["text"]) for event in events], [("compacted", "We fixed greet().")])

    def test_a_long_history_is_kept_whole_within_the_event_and_size_caps(self):
        self.assertGreaterEqual(remote_sessions.MAX_EVENTS_KEPT, 2000)
        batch = [{"type": "assistant", "text": "x" * 100, "history": True}] * remote_sessions.MAX_EVENTS_PER_POST
        for _ in range(20):
            self.sessions.add_events(LAPTOP, self.session_id, batch)
        read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual(len(read["events"]), 1000)
        self.assertFalse(read["missed"])

    def test_the_oldest_events_go_first_when_the_text_kept_grows_too_big(self):
        with unittest.mock.patch.object(remote_sessions, "MAX_CHARS_KEPT", 250):
            self.sessions.add_events(LAPTOP, self.session_id, [{"type": "assistant", "text": str(n) * 100} for n in range(5)])
            read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual([event["text"][0] for event in read["events"]], ["3", "4"])
        self.assertTrue(read["missed"])

    def test_one_huge_event_is_still_kept(self):
        with unittest.mock.patch.object(remote_sessions, "MAX_CHARS_KEPT", 10):
            self.sessions.add_events(LAPTOP, self.session_id, [{"type": "assistant", "text": "y" * 100}])
            read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual(len(read["events"]), 1)


class Transcript(unittest.TestCase):
    def setUp(self):
        self.sessions = Sessions()
        self.session_id = started(self.sessions)

    def test_events_are_numbered_and_read_after_a_point(self):
        self.sessions.add_events(LAPTOP, self.session_id, [{"type": "user", "text": "fix the test"},
                                                           {"type": "assistant", "text": "On it."}])
        first = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual([(event["seq"], event["type"]) for event in first["events"]], [(1, "user"), (2, "assistant")])
        self.assertEqual(first["next"], 2)
        self.sessions.add_events(LAPTOP, self.session_id, [{"type": "tool_call", "tool": "bash", "text": "pytest"}])
        later = self.sessions.events(PHONE, self.session_id, first["next"], 0)
        self.assertEqual([event["seq"] for event in later["events"]], [3])

    def test_unknown_event_types_and_fields_are_dropped_and_text_is_cut(self):
        self.sessions.add_events(LAPTOP, self.session_id, [
            {"type": "shell", "text": "rm"},
            {"type": "assistant", "text": "x" * (remote_sessions.MAX_TEXT_CHARS + 50), "secret": "s"},
            "not an event",
        ])
        events = self.sessions.events(PHONE, self.session_id, 0, 0)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]["text"]), remote_sessions.MAX_TEXT_CHARS)
        self.assertNotIn("secret", events[0])

    def test_only_the_newest_events_are_kept_and_a_late_reader_is_told(self):
        with unittest.mock.patch.object(remote_sessions, "MAX_EVENTS_KEPT", 3):
            self.sessions.add_events(LAPTOP, self.session_id, [{"type": "assistant", "text": str(n)} for n in range(5)])
            read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual([event["text"] for event in read["events"]], ["2", "3", "4"])
        self.assertTrue(read["missed"])

    def test_too_many_events_in_one_post_are_refused(self):
        with self.assertRaises(RemoteError) as caught:
            self.sessions.add_events(LAPTOP, self.session_id,
                                     [{"type": "assistant", "text": "a"}] * (remote_sessions.MAX_EVENTS_PER_POST + 1))
        self.assertEqual(caught.exception.status, 413)

    def test_a_status_event_updates_the_listed_mode(self):
        self.sessions.add_events(LAPTOP, self.session_id, [{"type": "status", "mode": "plan", "text": "busy"}])
        self.assertEqual(self.sessions.listed(PHONE)[0]["mode"], "plan")

    def test_a_remote_message_shows_in_the_transcript_with_who_sent_it(self):
        self.sessions.send_message(PHONE, self.session_id, "run the tests")
        events = self.sessions.events(MAIN, self.session_id, 0, 0)["events"]
        self.assertEqual((events[-1]["type"], events[-1]["text"], events[-1]["by"]), ("remote_message", "run the tests", "Phone"))

    def test_an_empty_or_huge_message_is_refused(self):
        for text in ("", "   ", "x" * (remote_sessions.MAX_TEXT_CHARS + 1)):
            with self.assertRaises(RemoteError):
                self.sessions.send_message(PHONE, self.session_id, text)

    def test_the_inbox_is_bounded(self):
        for n in range(remote_sessions.MAX_INBOX):
            self.sessions.send_message(PHONE, self.session_id, str(n))
        with self.assertRaises(RemoteError) as caught:
            self.sessions.send_message(PHONE, self.session_id, "one more")
        self.assertEqual(caught.exception.status, 429)

    def test_the_inbox_empties_once_read(self):
        self.sessions.send_message(PHONE, self.session_id, "hi")
        self.assertEqual(len(self.sessions.inbox(LAPTOP, self.session_id, 0)), 1)
        self.assertEqual(self.sessions.inbox(LAPTOP, self.session_id, 0), [])


class Permissions(unittest.TestCase):
    def setUp(self):
        self.sessions = Sessions()
        self.session_id = started(self.sessions)
        self.sessions.add_events(LAPTOP, self.session_id,
                                 [{"type": "permission", "request_id": "r1", "tool": "bash", "text": "rm -rf build"}])

    def test_a_remote_answer_reaches_the_computer(self):
        self.sessions.answer(PHONE, self.session_id, "r1", True)
        self.assertEqual(self.sessions.inbox(LAPTOP, self.session_id, 0),
                         [{"type": "answer", "request_id": "r1", "allow": True, "by": "Phone"}])

    def test_the_first_answer_wins_and_the_second_is_told_who_answered(self):
        self.sessions.answer(PHONE, self.session_id, "r1", False)
        with self.assertRaises(RemoteError) as caught:
            self.sessions.answer(MAIN, self.session_id, "r1", True)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("Phone", str(caught.exception))

    def test_an_answer_given_on_the_computer_closes_the_request(self):
        self.sessions.add_events(LAPTOP, self.session_id,
                                 [{"type": "permission_answered", "request_id": "r1", "allow": True, "by": "computer"}])
        with self.assertRaises(RemoteError) as caught:
            self.sessions.answer(PHONE, self.session_id, "r1", False)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("computer", str(caught.exception))

    def test_a_request_id_the_computer_reuses_can_be_answered_again(self):
        self.sessions.answer(PHONE, self.session_id, "r1", True)
        self.sessions.add_events(LAPTOP, self.session_id,
                                 [{"type": "permission", "request_id": "r1", "tool": "bash", "text": "make clean"}])
        self.sessions.answer(MAIN, self.session_id, "r1", False)
        self.assertEqual(self.sessions.inbox(LAPTOP, self.session_id, 0)[-1],
                         {"type": "answer", "request_id": "r1", "allow": False, "by": "Arty's own key"})

    def test_only_the_newest_answers_are_remembered_and_names_are_cut_to_size(self):
        answered = [event for number in range(remote_sessions.MAX_ANSWERS_KEPT + 10)
                    for event in ({"type": "permission", "request_id": f"q{number}"},
                                  {"type": "permission_answered", "request_id": f"q{number}", "by": "x" * 5000})]
        for start in range(0, len(answered), remote_sessions.MAX_EVENTS_PER_POST):
            self.sessions.add_events(LAPTOP, self.session_id, answered[start:start + remote_sessions.MAX_EVENTS_PER_POST])
        remembered = self.sessions.sessions[self.session_id].answered_by
        self.assertEqual(len(remembered), remote_sessions.MAX_ANSWERS_KEPT)
        self.assertIn(f"q{remote_sessions.MAX_ANSWERS_KEPT + 9}", remembered)
        self.assertTrue(all(len(name) <= remote_sessions.MAX_LABEL_CHARS for name in remembered.values()))

    def test_an_unknown_request_cannot_be_answered(self):
        with self.assertRaises(RemoteError) as caught:
            self.sessions.answer(PHONE, self.session_id, "made-up", True)
        self.assertEqual(caught.exception.status, 404)

    def test_open_requests_are_listed_for_a_controller_that_joins_late(self):
        self.assertEqual([request["request_id"] for request in self.sessions.listed(PHONE)[0]["waiting_for"]], ["r1"])
        self.sessions.answer(PHONE, self.session_id, "r1", True)
        self.assertEqual(self.sessions.listed(PHONE)[0]["waiting_for"], [])

    def test_an_interrupt_reaches_the_computer(self):
        self.sessions.interrupt(PHONE, self.session_id)
        self.assertEqual(self.sessions.inbox(LAPTOP, self.session_id, 0), [{"type": "interrupt", "by": "Phone"}])


class Waiting(unittest.TestCase):
    def setUp(self):
        self.sessions = Sessions()
        self.session_id = started(self.sessions)

    def test_the_computer_wakes_as_soon_as_a_message_arrives(self):
        threading.Timer(0.1, self.sessions.send_message, (PHONE, self.session_id, "hi")).start()
        began = time.monotonic()
        inbox = self.sessions.inbox(LAPTOP, self.session_id, 5)
        self.assertEqual([item["text"] for item in inbox], ["hi"])
        self.assertLess(time.monotonic() - began, 2)

    def test_a_reader_wakes_as_soon_as_an_event_arrives(self):
        threading.Timer(0.1, self.sessions.add_events, (LAPTOP, self.session_id, [{"type": "assistant", "text": "done"}])).start()
        began = time.monotonic()
        read = self.sessions.events(PHONE, self.session_id, 0, 5)
        self.assertEqual([event["text"] for event in read["events"]], ["done"])
        self.assertLess(time.monotonic() - began, 2)

    def test_waits_are_capped(self):
        self.assertEqual(remote_sessions.capped_wait("999"), remote_sessions.MAX_WAIT_SECONDS)
        self.assertEqual(remote_sessions.capped_wait("-3"), 0)
        self.assertEqual(remote_sessions.capped_wait("abc"), 0)
        self.assertEqual(remote_sessions.capped_wait("2.5"), 2.5)


class Ending(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sessions = Sessions(clock=self.clock)
        self.session_id = started(self.sessions)

    def test_a_session_the_computer_stops_polling_expires(self):
        self.clock.now += remote_sessions.IDLE_EXPIRY_SECONDS - 1
        self.assertEqual(len(self.sessions.listed(PHONE)), 1)
        self.clock.now += 2
        self.assertEqual(self.sessions.listed(PHONE), [])
        with self.assertRaises(RemoteError) as caught:
            self.sessions.send_message(PHONE, self.session_id, "hi")
        self.assertEqual(caught.exception.status, 404)

    def test_polling_keeps_a_session_alive(self):
        for _ in range(3):
            self.clock.now += remote_sessions.IDLE_EXPIRY_SECONDS - 10
            self.sessions.inbox(LAPTOP, self.session_id, 0)
        self.assertEqual(len(self.sessions.listed(PHONE)), 1)

    def test_an_ended_session_leaves_the_list_but_readers_see_the_end(self):
        self.sessions.end(LAPTOP, self.session_id)
        self.assertEqual(self.sessions.listed(PHONE), [])
        read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertTrue(read["ended"])
        self.assertEqual(read["events"][-1]["type"], "end")
        with self.assertRaises(RemoteError) as caught:
            self.sessions.send_message(PHONE, self.session_id, "hi")
        self.assertEqual(caught.exception.status, 410)

    def test_events_sent_after_the_end_are_refused_like_messages(self):
        self.sessions.end(LAPTOP, self.session_id)
        last = self.sessions.events(PHONE, self.session_id, 0, 0)["next"]
        with self.assertRaises(RemoteError) as caught:
            self.sessions.add_events(LAPTOP, self.session_id, [{"type": "assistant", "text": "late"}])
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(self.sessions.events(PHONE, self.session_id, 0, 0)["next"], last)

    def test_events_after_an_end_in_the_same_batch_are_dropped(self):
        self.sessions.add_events(LAPTOP, self.session_id, [{"type": "assistant", "text": "bye"}, {"type": "end"},
                                                           {"type": "assistant", "text": "late"}])
        read = self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual([event["type"] for event in read["events"]][-2:], ["assistant", "end"])
        self.assertNotIn("late", [event.get("text") for event in read["events"]])
        self.assertTrue(read["ended"])

    def test_an_ended_session_is_forgotten_after_a_while(self):
        self.sessions.end(LAPTOP, self.session_id)
        self.clock.now += remote_sessions.ENDED_KEPT_SECONDS + 1
        with self.assertRaises(RemoteError) as caught:
            self.sessions.events(PHONE, self.session_id, 0, 0)
        self.assertEqual(caught.exception.status, 404)


class OverHttp(Server):
    def setUp(self):
        super().setUp()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM approved_devices")
        self.addCleanup(unittest.mock.patch.stopall)
        unittest.mock.patch.object(brain, "remote", Sessions()).start()

    def device_key(self, name):
        status, body = self.request("POST", "/v1/devices", body={"name": name})
        made = json.loads(body)
        return made["id"], {"Authorization": f"Bearer {made['api_key']}"}

    def call(self, method, path, body=None, headers=None):
        status, raw = self.request(method, path, body=body, headers=headers, authorised=headers is None)
        return status, json.loads(raw) if raw else None

    def test_a_computer_and_a_phone_talk_through_the_brain(self):
        _, laptop = self.device_key("Laptop")
        phone_id, phone = self.device_key("Phone")
        status, made = self.call("POST", "/v1/remote/sessions", {"name": "Laptop", "folder": "repo"}, laptop)
        self.assertEqual(status, 201)
        session = made["id"]
        self.assertEqual(self.call("GET", "/v1/remote/sessions", headers=phone)[0], 403)
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/messages", {"text": "hi"}, phone)[0], 403)
        status, switched = self.call("POST", f"/v1/devices/{phone_id}/remote-control", {"allow": True})
        self.assertEqual((status, switched), (200, {"ok": True}))
        self.assertTrue(next(d for d in self.call("GET", "/v1/devices")[1]["devices"] if d["id"] == phone_id)["remote_control"])
        status, listed = self.call("GET", "/v1/remote/sessions", headers=phone)
        self.assertEqual((status, [entry["id"] for entry in listed["sessions"]]), (200, [session]))
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/messages", {"text": "run tests"}, phone)[0], 200)
        status, inbox = self.call("GET", f"/v1/remote/sessions/{session}/inbox?wait=0", headers=laptop)
        self.assertEqual((status, inbox["inbox"][0]["text"]), (200, "run tests"))
        events = [{"type": "permission", "request_id": "r1", "tool": "bash", "text": "pytest"}]
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/events", {"events": events}, laptop)[0], 200)
        status, read = self.call("GET", f"/v1/remote/sessions/{session}/events?after=0&wait=0", headers=phone)
        self.assertEqual([event["type"] for event in read["events"]], ["remote_message", "permission"])
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/answers", {"request_id": "r1", "allow": True}, phone)[0], 200)
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/answers", {"request_id": "r1", "allow": False})[0], 409)
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/interrupt", {}, phone)[0], 200)
        status, renamed = self.call("POST", f"/v1/remote/sessions/{session}/title", {"title": "docs work"}, laptop)
        self.assertEqual((status, renamed), (200, {"title": "docs work"}))
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/title", {"title": "x"}, phone)[0], 403)
        self.assertEqual(self.call("GET", "/v1/remote/sessions", headers=phone)[1]["sessions"][0]["title"], "docs work")
        self.assertEqual(self.call("DELETE", f"/v1/remote/sessions/{session}", headers=phone)[0], 403)
        self.assertEqual(self.call("DELETE", f"/v1/remote/sessions/{session}", headers=laptop)[0], 200)
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/messages", {"text": "hi"}, phone)[0], 410)

    def test_only_the_main_key_switches_remote_control(self):
        phone_id, phone = self.device_key("Phone")
        self.assertEqual(self.call("POST", f"/v1/devices/{phone_id}/remote-control", {"allow": True}, phone)[0], 403)
        self.assertEqual(self.call("POST", f"/v1/devices/{phone_id}/remote-control", {"allow": "yes"})[0], 400)
        self.assertEqual(self.call("POST", "/v1/devices/999999/remote-control", {"allow": True})[0], 404)
        self.assertFalse(store.list_devices()[0]["remote_control"])

    def test_remote_control_is_off_for_new_keys(self):
        self.device_key("Phone")
        self.assertFalse(store.list_devices()[0]["remote_control"])

    def test_no_key_means_no_relay(self):
        self.assertEqual(self.request("GET", "/v1/remote/sessions", authorised=False)[0], 401)

    def test_a_bad_answer_body_is_a_400(self):
        session = self.call("POST", "/v1/remote/sessions", {"name": "pc"})[1]["id"]
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/answers", {"request_id": "r1", "allow": "maybe"})[0], 400)
        self.assertEqual(self.call("POST", f"/v1/remote/sessions/{session}/events", {"events": "nope"})[0], 400)
        self.assertEqual(self.call("GET", f"/v1/remote/sessions/{session}/nothing")[0], 404)


if __name__ == "__main__":
    unittest.main()
