import itertools
import json
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "brain"))
import web_app  # noqa: E402

TEST_KEY = "fake-brain-test-key"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
POLLS_BEFORE_ANSWER = 2
MAX_FAKE_WAIT_SECONDS = 3
ANNOUNCEMENT_CHECK_SECONDS = 0.2
PNG_PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                          "1f15c4890000000d4944415478da63f8cfc0f01f0005000201a5f1b3e70000000049454e44ae426082")
FILE_TOKEN = "t0k"
ANSWER = ("Here's what I found:\n\n- **Weather:** dry, 12C\n- Bins tonight\n\n"
          f"[report.txt](http://192.168.1.13:8443/arty-files/f1/{FILE_TOKEN}/report.txt)\n\n"
          f"![chart](http://192.168.1.13:8443/arty-files/f2/{FILE_TOKEN}/chart.png)\n\n<script>alert(1)</script>")

ids = itertools.count(100)
lock = threading.Lock()
jobs: dict[str, dict] = {}
requests_seen: list[dict] = []
tasks = [
    {"id": 1, "title": "Delete old backups", "instructions": "Delete backups older than 30 days", "status": "waiting",
     "waiting": {"kind": "approval", "message": "Delete 3 backups from October?"}, "result": None, "updated": time.time()},
    {"id": 2, "title": "Summarise Proxmox guides", "instructions": "Find guides", "status": "done",
     "waiting": None, "result": "Guide one says " + "x" * 200, "updated": time.time() - 3600},
]
schedules = [{"id": 1, "title": "Morning briefing", "instructions": "Brief me", "description": "weekdays 08:00",
              "next_run": time.time() + 7200, "enabled": 1}]
events = [
    {"id": 5, "title": "Dentist", "start": "2026-11-05T15:00:00+00:00", "end": "2026-11-05T15:30:00+00:00", "all_day": False,
     "location": "Town hall", "repeats": False, "calendar": "Arty"},
    {"id": None, "title": "Bank holiday", "start": "2026-11-06T00:00:00+00:00", "end": "", "all_day": True,
     "location": "", "repeats": False, "calendar": "Google"},
]
announcements = [
    {"id": 1, "kind": "reminder", "title": "Bins", "message": "Put the bins out", "task_id": None, "actions": [], "created": time.time() - 600},
    {"id": 2, "kind": "approval", "title": "Task #1 needs you", "message": "Delete 3 backups from October?", "task_id": 1,
     "actions": ["approve", "deny"], "created": time.time() - 60},
]
lectures = [{"id": 1, "title": "Networking week 6", "status": "done", "duration": 3000, "cards": 12, "error": None,
             "created": time.time() - 86400, "notes": "## Subnetting\n- /24 has 254 hosts", "transcript": "..."}]
memory = {"entities": [{"name": "Sam", "entityType": "person", "observations": ["Plays five-a-side on Thursdays", "Lives nearby"]}]}


class FakeBrain(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("fake-brain %s\n" % (fmt % args))

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, content_type, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        parts = urllib.parse.urlsplit(self.path)
        return [s for s in parts.path.split("/") if s], urllib.parse.parse_qs(parts.query)

    def authorised(self):
        if secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TEST_KEY}"):
            return True
        self.send_json(401, {"error": {"message": "invalid API key"}})
        return False

    def json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        segments, query = self.route()
        if segments[:1] == [web_app.PREFIX]:
            if urllib.parse.urlsplit(self.path).path == "/ios":
                self.send_response(301)
                self.send_header("Location", "/ios/")
                self.end_headers()
                return
            found = web_app.find(segments)
            if found is None:
                return self.send_json(404, {"error": {"message": "not found"}})
            return self.send_bytes(found.read_bytes(), web_app.content_type(found), web_app.SECURITY_HEADERS)
        if not self.authorised():
            return
        if segments[:1] == ["arty-files"]:
            if segments[-1] == "chart.png":
                return self.send_bytes(PNG_PIXEL, "image/png")
            return self.send_bytes(b"report body", "text/plain")
        if segments == ["v1", "models"]:
            return self.send_json(200, {"data": [
                {"id": "assistant-core", "object": "model", "model": "qwen3:14b"},
                {"id": "arty/qwen3:14b", "name": "Arty (qwen3:14b)", "object": "model"},
                {"id": "solo/roleplay-model:12b", "name": "roleplay-model:12b (on its own: no Arty, no memory)", "object": "model"},
            ]})
        if len(segments) == 4 and segments[:3] == ["v1", "chat", "jobs"]:
            with lock:
                job = jobs.get(segments[3])
                if job is None:
                    return self.send_json(404, {"error": {"message": "no such chat job"}})
                job["polls"] += 1
                if job["polls"] <= POLLS_BEFORE_ANSWER:
                    return self.send_json(200, {"status": "running", "partial": "Here's what I found:"})
                return self.send_json(200, {"status": "done", "answer": ANSWER, "tools_used": ["weather", "calendar"]})
        if segments == ["v1", "announcements"]:
            after = int(query.get("after", ["0"])[0])
            deadline = time.time() + min(float(query.get("wait", ["0"])[0]), MAX_FAKE_WAIT_SECONDS)
            found = [a for a in announcements if a["id"] > after]
            while not found and time.time() < deadline:
                time.sleep(ANNOUNCEMENT_CHECK_SECONDS)
                found = [a for a in announcements if a["id"] > after]
            last = found[-1]["id"] if found else max([after] + [a["id"] for a in announcements])
            return self.send_json(200, {"announcements": found[-50:], "last": last})
        if segments == ["v1", "tasks"]:
            return self.send_json(200, {"tasks": tasks})
        if segments == ["v1", "schedules"]:
            return self.send_json(200, {"schedules": [s for s in schedules if s["enabled"]]})
        if segments == ["v1", "calendar"]:
            return self.send_json(200, {"events": events})
        if segments == ["v1", "memory"]:
            return self.send_json(200, memory)
        if segments == ["v1", "lectures"]:
            return self.send_json(200, {"lectures": lectures})
        if len(segments) == 3 and segments[:2] == ["v1", "lectures"]:
            return self.send_json(200, next(l for l in lectures if l["id"] == int(segments[2])))
        if segments == ["test", "requests"]:
            return self.send_json(200, {"requests": requests_seen})
        return self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        segments, _ = self.route()
        if not self.authorised():
            return
        if segments == ["v1", "files"]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            self.rfile.read(length)
            name = urllib.parse.unquote_plus(self.headers.get("X-Filename", "upload"))
            return self.send_json(201, {"id": f"file{next(ids)}", "name": name, "mime": self.headers.get("Content-Type"), "kind": "other", "size": length})
        body = self.json_body()
        requests_seen.append({"path": self.path, "body": body})
        if segments == ["test", "announce"]:
            announcements.append({"id": announcements[-1]["id"] + 1, "kind": "alert", "title": body.get("title", "Alert"),
                                  "message": body.get("message", ""), "task_id": None, "actions": [], "created": time.time()})
            return self.send_json(201, {"ok": True})
        if segments == ["v1", "chat", "jobs"]:
            job_id = f"job{next(ids)}"
            with lock:
                jobs[job_id] = {"polls": 0}
            return self.send_json(202, {"id": job_id})
        if segments == ["v1", "tasks"]:
            task = {"id": next(ids), "title": body["instructions"][:40], "instructions": body["instructions"], "status": "queued",
                    "waiting": None, "result": None, "updated": time.time()}
            tasks.insert(0, task)
            return self.send_json(201, task)
        if len(segments) == 4 and segments[:2] == ["v1", "tasks"]:
            task = next(t for t in tasks if t["id"] == int(segments[2]))
            task["status"] = {"approve": "running", "deny": "cancelled", "done": "running", "cancel": "cancelled"}[segments[3]]
            task["waiting"] = None
            return self.send_json(200, {"message": f"Task #{task['id']}: {segments[3]} recorded."})
        if segments == ["v1", "schedules"]:
            schedules.append({"id": next(ids), "title": body["instructions"], "instructions": body["instructions"],
                              "description": body["when"], "next_run": time.time() + 3600, "enabled": 1})
            return self.send_json(201, {"message": f"Scheduled: {body['instructions']} ({body['when']})."})
        if segments == ["v1", "calendar"]:
            events.append({"id": next(ids), "title": body["title"], "start": "2026-11-07T10:00:00+00:00", "end": "", "all_day": False,
                           "location": body.get("location", ""), "repeats": False, "calendar": "Arty"})
            return self.send_json(201, {"message": f"Added {body['title']}."})
        if segments == ["v1", "lectures"]:
            lecture = {"id": next(ids), "title": body["title"], "status": "queued", "duration": None, "cards": 0, "error": None, "created": time.time()}
            lectures.insert(0, lecture)
            return self.send_json(202, lecture)
        return self.send_json(404, {"error": {"message": "not found"}})

    def do_DELETE(self):
        segments, _ = self.route()
        if not self.authorised():
            return
        if len(segments) == 3 and segments[:2] == ["v1", "schedules"]:
            for schedule in schedules:
                if schedule["id"] == int(segments[2]):
                    schedule["enabled"] = 0
            return self.send_json(200, {"ok": True})
        if len(segments) == 3 and segments[:2] == ["v1", "calendar"]:
            events[:] = [e for e in events if e["id"] != int(segments[2])]
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"error": {"message": "not found"}})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), FakeBrain).serve_forever()
