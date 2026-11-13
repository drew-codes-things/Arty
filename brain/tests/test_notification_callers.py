import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-notify-callers-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import alerts  # noqa: E402
import background_tasks  # noqa: E402
import connections  # noqa: E402
import native_calls  # noqa: E402
import notify  # noqa: E402
import personal  # noqa: E402
import scheduler  # noqa: E402
import store  # noqa: E402

KEY = os.environ["BRAIN_API_KEY"]
ALERT_TOKEN = "alert-token"


class Captured:
    def __init__(self):
        self.categories = []

    def send(self, title, message, *rest, **options):
        self.categories.append(options.get("category"))
        return "sent"

    def patched(self):
        return unittest.mock.patch.object(notify, "send", self.send)


class BackgroundTasks(unittest.TestCase):
    def setUp(self):
        store.init()
        self.sent = Captured()

    def run_task(self, outcome):
        task = store.create_task("Post the photo", "Post the photo to the group", "api")
        with self.sent.patched(), unittest.mock.patch.object(background_tasks, "run_agent", return_value=outcome), \
                unittest.mock.patch.object(connections, "load_config", return_value={"conversations_dir": _scratch}):
            background_tasks.process_task(store.get_task(task["id"]))
        return self.sent.categories

    def test_a_task_waiting_for_approval_is_an_approval(self):
        waiting = {"status": "waiting", "waiting": {"kind": "approval", "message": "post it"}, "tools_used": []}
        self.assertEqual(self.run_task(waiting), ["approvals"])

    def test_a_task_that_needs_you_is_a_task(self):
        waiting = {"status": "waiting", "waiting": {"kind": "user", "message": "sign in"}, "tools_used": []}
        self.assertEqual(self.run_task(waiting), ["tasks"])

    def test_a_finished_task_is_a_task(self):
        done = {"status": "done", "answer": "Posted the photo to the group as asked.", "tools_used": [], "raw": {}}
        self.assertEqual(self.run_task(done), ["tasks"])

    def test_a_failed_task_is_a_task(self):
        task = store.create_task("Broken", "Do the broken thing", "api")
        with self.sent.patched():
            background_tasks.fail_task(task, RuntimeError("boom"))
        self.assertEqual(self.sent.categories, ["tasks"])


class ServerAlerts(unittest.TestCase):
    def setUp(self):
        store.init()
        alerts._healing.clear()
        self.sent = Captured()

    def heal(self, diagnosis):
        with self.sent.patched(), unittest.mock.patch.object(connections.hub, "refresh"), \
                unittest.mock.patch.object(connections.hub, "call", return_value=json.dumps(diagnosis)):
            alerts.self_heal({"name": "Plex", "hostname": "plex.example", "port": 32400})
        return self.sent.categories

    def test_a_service_down_with_no_fix_is_a_server_notice(self):
        self.assertEqual(self.heal({"summary": "Plex isn't answering."}), ["server"])

    def test_a_fix_offer_needs_a_yes_or_no_so_it_is_an_approval(self):
        diagnosis = {"summary": "The guest is stopped.", "fix": {"action": "start", "vmid": 101, "name": "plex"}}
        self.assertEqual(self.heal(diagnosis), ["approvals"])

    def test_an_uptime_kuma_alert_is_stored_as_a_server_notice(self):
        with unittest.mock.patch.object(alerts.threading, "Thread"):
            alerts.record_alert({"monitor": {"name": "Plex"}, "heartbeat": {"status": 0, "msg": "timeout"}})
        newest = store.list_announcements(0)[-1]
        self.assertEqual((newest["title"], newest["kind"], newest["category"]), ("Plex is down", "alert", "server"))


class ArtysOwnNotices(unittest.TestCase):
    def setUp(self):
        self.sent = Captured()

    def test_something_expiring_soon_is_an_arty_notice(self):
        with self.sent.patched(), unittest.mock.patch.object(scheduler.expiry, "due_warnings", return_value=["Passport in 30 days"]), \
                unittest.mock.patch.object(connections, "load_config", return_value={}):
            scheduler.check_expiry()
        self.assertEqual(self.sent.categories, ["arty"])

    def test_a_briefing_is_an_arty_notice(self):
        with self.sent.patched(), unittest.mock.patch.object(connections.hub, "routes", {"x": 1}), \
                unittest.mock.patch.object(scheduler.briefing, "build", return_value="Good morning."), \
                unittest.mock.patch.object(connections, "load_config", return_value={}):
            scheduler.send_briefing("Morning briefing", "morning")
        self.assertEqual(self.sent.categories, ["arty"])

    def test_the_email_watch_is_handed_the_real_send(self):
        started = []
        with unittest.mock.patch.object(scheduler.email_watch, "due", return_value=True), \
                unittest.mock.patch.object(connections, "load_config", return_value={}), \
                unittest.mock.patch.object(scheduler.threading, "Thread", lambda **options: started.append(options) or unittest.mock.Mock()):
            scheduler.start_email_watch()
        self.assertIs(started[0]["args"][2], notify.send)


class NotifyTool(unittest.TestCase):
    def test_the_notify_tool_is_other(self):
        sent = Captured()
        with sent.patched():
            native_calls.run_native("notify_user", {"title": "Hi", "message": "there"}, {}, False, None)
        self.assertEqual(sent.categories, ["other"])


class Routes(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), brain.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        self.addCleanup(connection.close)
        headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
        connection.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    def notify_with(self, body):
        sent = Captured()
        with sent.patched():
            status, _ = self.request("POST", "/v1/notify", body)
        self.assertEqual(status, 200)
        return sent.categories[0]

    def test_the_api_caller_may_choose_a_category(self):
        self.assertEqual(self.notify_with({"title": "Backup", "message": "done", "category": "server"}), "server")

    def test_an_unknown_or_missing_category_from_the_api_is_other(self):
        self.assertEqual(self.notify_with({"title": "Hi", "message": "x", "category": "spam"}), "other")
        self.assertEqual(self.notify_with({"title": "Hi", "message": "x", "category": 5}), "other")
        self.assertEqual(self.notify_with({"title": "Hi", "message": "x"}), "other")

    def test_the_homelab_hook_is_a_server_notice(self):
        sent = Captured()
        with sent.patched(), unittest.mock.patch.object(brain, "ALERT_TOKEN", ALERT_TOKEN):
            status, _ = self.request("POST", f"/hooks/notify?token={ALERT_TOKEN}", {"title": "SSH login", "message": "from 10.0.0.9"})
        self.assertEqual((status, sent.categories), (200, ["server"]))

    def test_an_old_style_client_still_gets_announcements_with_the_fields_it_knew(self):
        after = store.latest_announcement_id()
        notify.listeners.append(brain.record_announcement)
        self.addCleanup(notify.listeners.remove, brain.record_announcement)
        status, _ = self.request("POST", "/v1/notify", {"title": "Bins", "message": "Tonight", "category": "email"})
        self.assertEqual(status, 200)
        status, feed = self.request("GET", f"/v1/announcements?after={after}&wait=0")
        item = feed["announcements"][-1]
        self.assertEqual(status, 200)
        self.assertEqual((item["title"], item["kind"], item["category"]), ("Bins", "message", "email"))
        self.assertLessEqual({"id", "kind", "title", "message", "task_id", "actions", "created"}, set(item))
        self.assertEqual(feed["last"], item["id"])


if __name__ == "__main__":
    unittest.main()
