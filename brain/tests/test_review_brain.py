import hashlib
import hmac
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-review-brain-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import agent_loop  # noqa: E402
import alerts  # noqa: E402
import archive  # noqa: E402
import background_tasks  # noqa: E402
import connections  # noqa: E402
import native_calls  # noqa: E402
import scheduler  # noqa: E402
import personal  # noqa: E402
import policy  # noqa: E402
import store  # noqa: E402

KEY = os.environ["BRAIN_API_KEY"]
RULES = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())["approval"]
NEWS_PAGE = "News | https://news.example.org/a\n[3] link \"Read more\""
LINKEDIN_PAGE = "Feed | https://www.linkedin.com/feed/\n[3] button \"Post\""


class Server(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), brain.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, authorised=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        self.addCleanup(connection.close)
        sent = {"Authorization": f"Bearer {KEY}"} if authorised else {}
        sent.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        return response.status, response.read()


class Identity(Server):
    def test_proves_it_holds_the_key_without_showing_it(self):
        nonce = "0123456789abcdef0123"
        status, body = self.request("GET", f"/v1/identity?nonce={nonce}", authorised=False)
        expected = hmac.new(KEY.encode(), f"arty-identity:{nonce}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(status, 200)
        self.assertIn(expected, json.loads(body)["proofs"])
        self.assertNotIn(KEY.encode(), body)

    def test_a_bad_nonce_is_refused(self):
        for nonce in ["", "abc", "zz" * 10, "a" * 130]:
            status, _ = self.request("GET", f"/v1/identity?nonce={nonce}", authorised=False)
            self.assertEqual(status, 400, nonce)


class Secrets(Server):
    def test_a_non_ascii_key_is_a_plain_401(self):
        status, _ = self.request("GET", "/v1/tasks", headers={"Authorization": "Bearer tést"}, authorised=False)
        self.assertEqual(status, 401)

    def test_a_non_ascii_alert_token_is_a_plain_403(self):
        with unittest.mock.patch.object(brain, "ALERT_TOKEN", "right"):
            status, _ = self.request("POST", "/hooks/alert?token=%C3%A9", body={})
        self.assertEqual(status, 403)

    def test_tokens_in_links_are_not_written_to_the_log(self):
        with unittest.mock.patch.object(brain, "ALERT_TOKEN", "right"), self.assertLogs("brain", "INFO") as logged:
            self.request("POST", "/hooks/alert?token=very-secret-value", body={})
        self.assertFalse(any("very-secret-value" in line for line in logged.output))


class BadInput(Server):
    def test_a_negative_limit_is_refused(self):
        status, _ = self.request("GET", "/v1/tasks?limit=-1")
        self.assertEqual(status, 400)

    def test_messages_must_be_a_list_of_messages(self):
        for messages in ["hi", [1], [{"content": "no role"}], []]:
            status, _ = self.request("POST", "/v1/chat/completions", body={"messages": messages})
            self.assertEqual(status, 400, messages)
            status, _ = self.request("POST", "/v1/chat/jobs", body={"messages": messages})
            self.assertEqual(status, 400, messages)

    def test_a_bad_max_tokens_is_a_400(self):
        with unittest.mock.patch.object(brain, "vision_model", return_value={"name": "v"}):
            status, _ = self.request("POST", "/v1/chat/completions", headers={"X-Arty-Direct-Vision": "1"},
                                     body={"messages": [{"role": "user", "content": "hi"}], "max_tokens": "lots"})
        self.assertEqual(status, 400)

    def test_errors_do_not_show_internal_details(self):
        with unittest.mock.patch.object(store, "list_schedules", side_effect=RuntimeError("secret internals")):
            status, body = self.request("GET", "/v1/schedules")
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret internals", body)

    def test_a_delete_that_fails_still_answers(self):
        with unittest.mock.patch.object(store, "update_schedule", side_effect=sqlite3.OperationalError("locked")):
            status, body = self.request("DELETE", "/v1/schedules/1")
        self.assertEqual(status, 500)
        self.assertNotIn(b"locked", body)

    def test_a_file_that_vanished_after_lookup_is_a_plain_404(self):
        gone = {"id": "a" * 24, "token": "t", "name": "x.txt", "mime": "text/plain", "path": "/nonexistent/arty/file"}
        with unittest.mock.patch("media.for_download", return_value=gone):
            status, _ = self.request("GET", "/arty-files/" + "a" * 24 + "/t/x.txt", authorised=False)
        self.assertEqual(status, 404)


class IdleClients(Server):
    def test_a_client_that_sends_nothing_is_dropped(self):
        with unittest.mock.patch.object(brain.Handler, "timeout", 0.5):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            connection.connect()
            started = time.time()
            self.assertEqual(connection.sock.recv(1), b"")
            self.assertLess(time.time() - started, 4)
            connection.close()


class ChatJobs(Server):
    def test_a_pruned_job_says_expired_and_an_unknown_one_404(self):
        jobs = brain.ChatJobs()
        jobs.jobs["old"] = {"status": "done", "created": time.time() - jobs.KEEP_SECONDS - 5}
        with unittest.mock.patch.object(brain, "chat_jobs", jobs):
            self.assertTrue(jobs.expired("old"))
            status, body = self.request("GET", "/v1/chat/jobs/old")
            self.assertEqual((status, json.loads(body)), (410, {"error": "expired"}))
            status, _ = self.request("GET", "/v1/chat/jobs/never-existed")
            self.assertEqual(status, 404)

    def test_too_many_answers_at_once_are_refused(self):
        release = threading.Event()

        def slow_turn(*args, **kwargs):
            release.wait(5)
            return "done", [], {}, None

        self.addCleanup(release.set)
        jobs = brain.ChatJobs()
        with unittest.mock.patch.object(brain, "chat_jobs", jobs), unittest.mock.patch.object(brain, "run_turn", slow_turn):
            message = {"messages": [{"role": "user", "content": "hi"}]}
            statuses = [self.request("POST", "/v1/chat/jobs", body=message)[0] for _ in range(brain.MAX_RUNNING_CHAT_JOBS + 1)]
        self.assertEqual(statuses[:-1], [202] * brain.MAX_RUNNING_CHAT_JOBS)
        self.assertEqual(statuses[-1], 503)

    def test_a_failed_job_hides_the_python_error(self):
        jobs = brain.ChatJobs()
        with unittest.mock.patch.object(brain, "run_turn", side_effect=RuntimeError("secret internals")):
            job_id = jobs.start([{"role": "user", "content": "hi"}], "")
            for _ in range(50):
                if jobs.get(job_id)["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertNotIn("secret internals", json.dumps(jobs.get(job_id)))


class FakeHub:
    def __init__(self, page):
        self.page = page
        self.calls = []
        self.routes = {name: "x" for name in ("workspace__browser_click", "workspace__browser_read", "workspace__browser_upload")}
        self.tools = []

    def refresh(self):
        pass

    def call(self, name, arguments):
        self.calls.append(name)
        return self.page if name == "workspace__browser_read" else "done"


class ApiTools(Server):
    def call_tool(self, page, name, arguments, approved=False):
        hub = FakeHub(page)
        with unittest.mock.patch.object(connections, "hub", hub), \
                unittest.mock.patch.object(connections, "load_config", return_value={"approval": RULES}):
            status, body = self.request("POST", f"/v1/tools/{name}", body={"arguments": arguments, "approved": approved})
        return status, hub.calls

    def test_an_api_click_reads_the_page_first_and_goes_ahead_on_an_ordinary_site(self):
        status, calls = self.call_tool(NEWS_PAGE, "workspace__browser_click", {"target": "3"})
        self.assertEqual((status, calls), (200, ["workspace__browser_read", "workspace__browser_click"]))

    def test_an_api_click_on_a_sensitive_site_needs_approval(self):
        status, calls = self.call_tool(LINKEDIN_PAGE, "workspace__browser_click", {"target": "3"})
        self.assertEqual((status, calls), (409, ["workspace__browser_read"]))

    def test_one_request_s_page_is_not_used_for_the_next(self):
        self.call_tool(NEWS_PAGE, "workspace__browser_click", {"target": "3"})
        status, _ = self.call_tool(LINKEDIN_PAGE, "workspace__browser_click", {"target": "3"})
        self.assertEqual(status, 409)

    def test_private_files_are_never_uploaded_even_when_approved(self):
        status, calls = self.call_tool(NEWS_PAGE, "workspace__browser_upload", {"target": "1", "path": "~/.ssh/id_ed25519"},
                                       approved=True)
        self.assertEqual(status, 403)
        self.assertNotIn("workspace__browser_upload", calls)

    def test_a_bad_number_for_a_native_tool_is_a_400(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={"approval": RULES}), \
                unittest.mock.patch.object(native_calls, "week_recap", side_effect=KeyError("time")):
            status, _ = self.request("POST", "/v1/tools/week_recap", body={"arguments": {}})
        self.assertEqual(status, 400)


class NativeTools(unittest.TestCase):
    def test_a_failing_native_tool_is_a_tool_error_the_model_can_read(self):
        with unittest.mock.patch.object(native_calls, "week_recap", side_effect=KeyError("time")):
            result, waiting = native_calls.run_native_safely("week_recap", {}, {}, False, None)
        self.assertTrue(result.startswith("tool error"))
        self.assertIsNone(waiting)

    def test_cancel_with_a_word_for_a_number(self):
        store.init()
        self.assertTrue(native_calls.run_native_safely("cancel_scheduled", {"schedule_id": "abc"}, {}, False, None)[0]
                        .startswith("No active scheduled job #0, so nothing was cancelled."))


class ChatApproval(unittest.TestCase):
    def test_the_held_call_gets_a_result_so_the_conversation_stays_valid(self):
        hub = FakeHub(NEWS_PAGE)
        hub.routes["workspace__run_command"] = "x"
        reply = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "workspace__run_command", "arguments": {"command": "git push"}}}]}}
        state = {"messages": [{"role": "user", "content": "push it"}], "context": {}}
        with unittest.mock.patch.object(connections, "hub", hub), \
                unittest.mock.patch.object(connections, "http_json", return_value=reply), \
                unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
                unittest.mock.patch.object(brain.models, "find", return_value=None), \
                unittest.mock.patch.object(agent_loop, "system_prompt", return_value=""), \
                unittest.mock.patch.object(agent_loop, "current_context", return_value=""):
            outcome = brain.run_agent({"model": "m", "approval": RULES}, state)
        self.assertIn("need your OK", outcome["answer"])
        self.assertEqual(state["messages"][-1]["role"], "tool")
        self.assertEqual(state["messages"][-1]["tool_name"], "workspace__run_command")
        self.assertNotIn("workspace__run_command", hub.calls)


class Scheduler(unittest.TestCase):
    def setUp(self):
        store.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM schedules")
        self.sent = []
        patches = [unittest.mock.patch.object(brain.notify, "send", lambda title, message, *a, **k: self.sent.append(message)),
                   unittest.mock.patch.object(brain.calendar_feed, "due_reminders", return_value=[]),
                   unittest.mock.patch.object(brain.personal, "due_deadline_reminders", return_value=[]),
                   unittest.mock.patch.object(scheduler, "start_expiry_check", lambda: None),
                   unittest.mock.patch.object(brain.email_watch, "due", return_value=False),
                   unittest.mock.patch.object(brain.desktop_look, "due", return_value=False),
                   unittest.mock.patch.object(connections, "load_config", return_value={})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_one_broken_schedule_does_not_stop_the_others(self):
        now = datetime.now()
        store.create_schedule("broken", "x", "not a cron", "?", now.timestamp() - 10, "broken one")
        store.create_schedule("fine", "x", None, "once", now.timestamp() - 5, "drink some water")
        scheduler.scheduler_tick(now)
        self.assertIn("Drink some water", self.sent)
        broken = [s for s in store.list_schedules(include_disabled=True) if s["title"] == "broken"][0]
        self.assertEqual(broken["enabled"], 0)

    def test_a_crash_in_a_background_check_does_not_kill_the_tick(self):
        with unittest.mock.patch.object(brain.style, "due", side_effect=OSError("prompt file missing")):
            scheduler.scheduler_tick(datetime.now())

    def test_a_reminder_that_could_not_be_sent_is_tried_again(self):
        now = datetime.now()
        created = store.create_schedule("water", "x", None, "once", now.timestamp() - 5, "drink some water")
        with unittest.mock.patch.object(brain.notify, "send", side_effect=OSError("phone push down")):
            scheduler.scheduler_tick(now)
        again = [s for s in store.list_schedules() if s["id"] == created["id"]]
        self.assertEqual(len(again), 1)
        self.assertGreater(again[0]["next_run"], now.timestamp())

    def test_a_repeat_is_counted_from_when_it_was_due(self):
        due = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=1)
        created = store.create_schedule("stretch", "x", "every 45m", "every 45 minutes", due.timestamp(), "stretch")
        scheduler.scheduler_tick(due + timedelta(seconds=20))
        moved = [s for s in store.list_schedules() if s["id"] == created["id"]][0]
        self.assertEqual(moved["next_run"], (due + timedelta(minutes=45)).timestamp())


class Worker(unittest.TestCase):
    def test_a_database_error_does_not_kill_the_worker(self):
        with unittest.mock.patch.object(store, "claim_next_task", side_effect=sqlite3.OperationalError("locked")):
            self.assertFalse(background_tasks.worker_step())

    def test_a_failure_while_recording_a_failure_does_not_kill_it(self):
        task = {"id": 1, "title": "t"}
        with unittest.mock.patch.object(store, "claim_next_task", return_value=task), \
                unittest.mock.patch.object(background_tasks, "process_task", side_effect=RuntimeError("boom")), \
                unittest.mock.patch.object(store, "update_task", side_effect=sqlite3.OperationalError("locked")):
            self.assertTrue(background_tasks.worker_step())


class ApprovedOnce(unittest.TestCase):
    def setUp(self):
        store.init()

    def waiting_task(self, **extra):
        task = store.create_task("t", "do it", "api")
        waiting = {"kind": "approval", "message": "restart x", "decision": "approve", "note": "",
                   "call": {"name": "homelab__restart_guest", "arguments": {"vmid": 103}}, **extra}
        store.update_task(task["id"], status="running", waiting=waiting,
                          history={"messages": [{"role": "user", "content": "do it"}]})
        return store.get_task(task["id"])

    def run_task(self, task, seen):
        def call(name, arguments):
            seen.append((name, (store.get_task(task["id"])["waiting"] or {}).get("applying")))
            return "Restarted."

        done = {"status": "done", "answer": "Finished the job and reported back properly.", "tools_used": [], "raw": {}}
        with unittest.mock.patch.object(connections.hub, "call", call), unittest.mock.patch.object(connections.hub, "refresh"), \
                unittest.mock.patch.object(background_tasks, "run_agent", return_value=done), \
                unittest.mock.patch.object(connections, "load_config", return_value={"conversations_dir": _scratch}), \
                unittest.mock.patch.object(brain.notify, "send"):
            background_tasks.process_task(task)

    def test_the_decision_is_marked_as_being_carried_out_before_the_tool_runs(self):
        seen = []
        self.run_task(self.waiting_task(), seen)
        self.assertEqual(seen, [("homelab__restart_guest", True)])

    def test_after_a_restart_mid_action_it_is_not_run_again(self):
        seen = []
        task = self.waiting_task(applying=True)
        self.run_task(task, seen)
        self.assertEqual(seen, [])
        history = store.get_task(task["id"])["history"]["messages"]
        self.assertIn("may or may not have happened", history[-1]["content"])


class Archive(unittest.TestCase):
    def test_odd_lines_in_the_archive_are_skipped(self):
        folder = Path(tempfile.mkdtemp(prefix="arty-archive-"))
        good = {"time": "2026-11-11T10:00:00", "user": "hello", "assistant": "hi", "tools": []}
        (folder / "2026-11.jsonl").write_text("\n".join([json.dumps(good), "[1, 2]", json.dumps({"time": "x"}),
                                                          "not json", json.dumps(good)]) + "\n")
        config = {"conversations_dir": str(folder)}
        self.assertEqual(len(list(brain.archive_records(config))), 2)
        self.assertIn("hello", archive.recent_exchanges(config))
        with unittest.mock.patch.object(store, "list_tasks", return_value=[]):
            self.assertIn("hello", native_calls.week_recap(config))

    def test_a_long_archive_is_read_newest_first(self):
        folder = Path(tempfile.mkdtemp(prefix="arty-archive-"))
        lines = [json.dumps({"time": f"2026-11-11T10:{n % 60:02d}:00", "user": f"message {n}", "assistant": "ok"})
                 for n in range(3000)]
        (folder / "2026-11.jsonl").write_text("\n".join(lines) + "\n")
        newest = [record["user"] for _, record in zip(range(3), brain.archive_records({"conversations_dir": str(folder)}))]
        self.assertEqual(newest, ["message 2999", "message 2998", "message 2997"])


class SelfHeal(unittest.TestCase):
    def setUp(self):
        alerts._healing.clear()

    def test_a_partial_diagnosis_does_not_crash(self):
        diagnosis = json.dumps({"fix": {"action": "start"}})
        with unittest.mock.patch.object(connections.hub, "refresh"), \
                unittest.mock.patch.object(connections.hub, "call", return_value=diagnosis), \
                unittest.mock.patch.object(brain.notify, "send") as sent:
            alerts.self_heal({"name": "web", "hostname": "thing.example", "port": "not a port"})
        sent.assert_called_once()

    def test_the_cooldown_is_claimed_once(self):
        self.assertTrue(alerts.claim_healing("web"))
        self.assertFalse(alerts.claim_healing("web"))


class Quiz(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        personal.card_add("networking", "Which port does SSH use?", "22")
        personal._quizzes.clear()

    def judge(self, question, expected, given):
        return given.strip() == expected, ""

    def test_one_app_s_quiz_does_not_capture_another_app_s_messages(self):
        personal.quiz_turn("quiz me", self.judge, "desktop")
        self.assertIsNone(personal.quiz_turn("remind me to put the bins out in 10 minutes", self.judge, "phone"))

    def test_a_request_during_a_quiz_ends_it_instead_of_being_marked(self):
        personal.quiz_turn("quiz me", self.judge, "phone")
        said = "remind me to put the bins out in 10 minutes"
        self.assertIsNone(personal.quiz_turn(said, self.judge, "phone", brain.asks_for_something_else(said)))

    def test_answers_are_still_marked(self):
        personal.quiz_turn("quiz me on networking", self.judge, "phone")
        self.assertIsNotNone(personal.quiz_turn("22", self.judge, "phone", brain.asks_for_something_else("22")))


class IPhoneCalls(unittest.TestCase):
    def test_the_iphone_app_gets_the_same_phone_action_as_android(self):
        action = brain.phone.from_request("call mum")
        on_iphone = brain.phone.reply(action, "ios")
        self.assertEqual(on_iphone, brain.phone.reply(action, "phone"))
        self.assertIn("<phone-action>", on_iphone)


if __name__ == "__main__":
    unittest.main()
