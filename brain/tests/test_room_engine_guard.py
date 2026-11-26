import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-engine-guard-")
os.environ.setdefault("ROOM_NOTES_STATE", os.path.join(_scratch, "room-notes.json"))

import agent_loop  # noqa: E402
import brain  # noqa: E402,F401
import connections  # noqa: E402
import engine_guard  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402
import room  # noqa: E402
import room_notes  # noqa: E402

SLOW_SECONDS = 10.0
QUICK_SECONDS = 3.0
ROOM_SAID = ("Bob: what should the town build next?\n\n(room) Be brief.\n\nRoom topic: Building a town\nOther AIs here: Bob.\n"
             "Messages from people (not AIs) are marked [person].\n\n(It's your turn, arty. Write your next message only, or PASS.)")


def chat_line(text, done=False):
    return {"message": {"role": "assistant", "content": text}, "done": done, "eval_count": 3}


class FakeEngine(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        engine = self.server.engine
        engine.bodies.append(body)
        engine.outside_busy.append(engine_guard.outside_work.is_busy())
        script = engine.scripts.pop(0) if engine.scripts else [(0, chat_line("ok", True))]
        if script == "fail":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "broken"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for delay, event in script:
                time.sleep(delay)
                self.wfile.write(json.dumps(event).encode() + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            engine.hung_up.set()


class Engine:
    def __init__(self):
        self.bodies, self.outside_busy, self.scripts = [], [], []
        self.hung_up = threading.Event()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
        self.server.daemon_threads = True
        self.server.engine = self
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def dripping(lines=20, every=0.5):
    return [(every, chat_line(f"word{number} ")) for number in range(lines)] + [(every, chat_line("", True))]


class GuardedCalls(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.addCleanup(self.engine.stop)
        self.outside = engine_guard.OutsideWork()
        for patch in (unittest.mock.patch.object(engine_guard, "ARTY_ENGINE_URL", self.engine.url),
                      unittest.mock.patch.object(connections, "ARTY_ENGINE_URL", self.engine.url),
                      unittest.mock.patch.object(engine_guard, "outside_work", self.outside)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, guard, path="/api/chat", body=None):
        with engine_guard.guarded(guard):
            return connections.http_json(f"{self.engine.url}{path}", body=body or {"model": "m", "messages": []})

    def test_a_streamed_reply_is_joined_like_a_plain_one(self):
        self.engine.scripts.append([(0, chat_line("Hello ")), (0, chat_line("room.", True))])
        reply = self.call(engine_guard.Guard(time.monotonic() + QUICK_SECONDS))
        self.assertEqual(reply["message"]["content"], "Hello room.")
        self.assertTrue(reply["done"])
        self.assertIs(self.engine.bodies[0]["stream"], True)

    def test_the_room_deadline_bounds_the_engine_call_and_hangs_up(self):
        self.engine.scripts.append(dripping())
        started = time.monotonic()
        with self.assertRaises(engine_guard.OutOfTime):
            self.call(engine_guard.Guard(started + 1.0))
        self.assertLess(time.monotonic() - started, QUICK_SECONDS)
        self.assertTrue(self.engine.hung_up.wait(QUICK_SECONDS))

    def test_outside_work_pre_empts_the_room_call_mid_reply(self):
        self.engine.scripts.append(dripping())
        flipped = time.monotonic() + 1.0
        guard = engine_guard.Guard(time.monotonic() + SLOW_SECONDS, lambda: time.monotonic() > flipped)
        started = time.monotonic()
        with self.assertRaises(engine_guard.Preempted):
            self.call(guard)
        self.assertLess(time.monotonic() - started, QUICK_SECONDS)
        self.assertTrue(guard.interrupted)
        self.assertTrue(self.engine.hung_up.wait(QUICK_SECONDS))

    def test_an_engine_error_is_raised_as_a_model_error(self):
        self.engine.scripts.append("fail")
        with self.assertRaisesRegex(RuntimeError, "model error"):
            self.call(engine_guard.Guard(time.monotonic() + QUICK_SECONDS))

    def test_an_engine_call_outside_the_room_counts_as_outside_work(self):
        connections.http_json(f"{self.engine.url}/api/chat", body={"model": "m", "messages": [], "stream": False})
        self.assertEqual(self.engine.outside_busy, [True])
        self.assertFalse(self.outside.is_busy())
        self.assertLess(self.outside.quiet_for(), QUICK_SECONDS)

    def test_room_calls_and_housekeeping_loads_are_not_outside_work(self):
        self.call(engine_guard.Guard(time.monotonic() + QUICK_SECONDS))
        with engine_guard.housekeeping():
            connections.http_json(f"{self.engine.url}/api/generate", body={"model": "m", "keep_alive": -1})
        self.assertEqual(self.engine.outside_busy, [False, False])
        self.assertEqual(self.outside.quiet_for(), float("inf"))

    def test_other_addresses_are_not_outside_work(self):
        self.assertFalse(engine_guard.is_generation("http://elsewhere.example/api/chat"))
        self.assertFalse(engine_guard.is_generation(f"{self.engine.url}/api/ps"))
        self.assertTrue(engine_guard.is_generation(f"{self.engine.url}/api/generate"))


class FakeHub:
    tools = []
    routes = {}

    def refresh(self):
        pass

    def call(self, name, arguments):
        return ""


class PreemptedRoomTurn(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.addCleanup(self.engine.stop)
        self.outside = engine_guard.OutsideWork()
        self.owner = room.OwnerActivity(outside=self.outside)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = {"model": "qwen3:14b", "persona": "p", "system_prompt": "s", "assistant_name": "Arty", "lookups": [],
                  "options": {"num_ctx": 24576}, "hidden_tools": [], "tool_groups": {}}
        for patch in (unittest.mock.patch.object(engine_guard, "ARTY_ENGINE_URL", self.engine.url),
                      unittest.mock.patch.object(connections, "ARTY_ENGINE_URL", self.engine.url),
                      unittest.mock.patch.object(agent_loop, "ARTY_ENGINE_URL", self.engine.url),
                      unittest.mock.patch.object(engine_guard, "outside_work", self.outside),
                      unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)),
                      unittest.mock.patch.object(connections, "hub", FakeHub()),
                      unittest.mock.patch.object(room.native_calls, "look_up_first", return_value=([], "", None)),
                      unittest.mock.patch.object(room.honesty, "be_honest", side_effect=lambda answer, *rest, **named: answer),
                      unittest.mock.patch.object(models, "budget_for", return_value=0),
                      unittest.mock.patch.object(models, "find", return_value=None),
                      unittest.mock.patch.object(agent_loop, "current_context", return_value=""),
                      unittest.mock.patch.object(model_choice.main_model_return, "holds_a_session", return_value=False),
                      unittest.mock.patch.object(room, "RESUME_QUIET_SECONDS", 0.5),
                      unittest.mock.patch.object(room, "RESUME_POLL_SECONDS", 0.1),
                      unittest.mock.patch.object(room_notes, "STATE_FILE", Path(folder.name) / "room-notes.json")):
            patch.start()
            self.addCleanup(patch.stop)

    def outside_request_after(self, seconds, lasting):
        def owner_asks():
            time.sleep(seconds)
            with self.outside.working():
                connections.http_json(f"{self.engine.url}/api/chat", body={"model": "qwen3:14b", "messages": [],
                                                                            "stream": False})
                time.sleep(lasting)
        thread = threading.Thread(target=owner_asks)
        thread.start()
        return thread

    def test_an_outside_request_mid_turn_pre_empts_the_room_and_the_reply_is_finished_after(self):
        self.engine.scripts += [dripping(), [(0, chat_line("Owner answer.", True))],
                                [(0, chat_line("A library ")), (0, chat_line("by the school.", True))]]
        owner = self.outside_request_after(1.0, 1.0)
        started = time.monotonic()
        reply = room.room_turn([{"role": "user", "content": ROOM_SAID}], self.owner,
                               room.TurnTime(room.ROOM_TURN_SECONDS))
        owner.join()
        self.assertTrue(self.engine.hung_up.wait(QUICK_SECONDS))
        self.assertEqual(reply.answer, "A library by the school.")
        self.assertEqual(len(self.engine.bodies), 3)
        self.assertEqual(self.engine.outside_busy, [False, True, False])
        self.assertLess(time.monotonic() - started, SLOW_SECONDS)
        self.assertEqual(reply.note[0], room.room_text.app_turn(ROOM_SAID).scope)


if __name__ == "__main__":
    unittest.main()
