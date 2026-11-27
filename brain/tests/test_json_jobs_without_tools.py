import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-json-jobs-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json"),
                  ("ROOM_NOTES_STATE", "room-notes.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import agent_loop  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import engine_guard  # noqa: E402
import honesty  # noqa: E402
import main_calls  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402
import room  # noqa: E402
import room_notes  # noqa: E402
import screen_review  # noqa: E402

BIG = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"
CHAT_URL = f"{connections.ARTY_ENGINE_URL}/api/chat"
GRAMMAR_ERROR = b'{"error": "Failed to initialize samplers: failed to parse grammar"}'
SYSTEM = {"role": "system", "content": "You are Arty. Use your tools."}
TOOLS = [{"type": "function", "function": {"name": "weather", "description": "w", "parameters": {"type": "object", "properties": {}}}}]
PREFIX = {"model": BIG, "messages": [SYSTEM], "tools": TOOLS}
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
CONFIG = {"model": BIG, "options": {"num_ctx": 24576}}
ROOM_ANSWER = "A library by the school."
ROOM_SAID = ("Bob: what should the town build next?\n\n(room) Be brief.\n\nRoom topic: Building a town\nOther AIs here: Bob.\n"
             "Messages from people (not AIs) are marked [person].\n\n(It's your turn, arty. Write your next message only, or PASS.)")
QUICK_SECONDS = 20.0


def refuses_tools_with_a_format(body: dict) -> bool:
    return body.get("format") is not None and bool(body.get("tools"))


def judged_reply(body: dict) -> str:
    prompt = body["messages"][-1]["content"]
    if body.get("format") is None:
        return ROOM_ANSWER
    if prompt.startswith("The person said"):
        return json.dumps({"kind": "A"})
    if "false_claims" in prompt:
        return json.dumps({"false_claims": [{"text": "Done.", "correction": ""}]})
    if "facts" in prompt:
        return json.dumps({"facts": [{"about": "Sam", "fact": "has a sister called Ella"}]})
    return json.dumps({screen_review.VERDICT_FIELD: False})


class BigMainEngine:
    def __init__(self):
        self.bodies = []

    def json(self, url, body=None, headers=None, timeout=600):
        self.bodies.append(body)
        if refuses_tools_with_a_format(body):
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(GRAMMAR_ERROR))
        return {"message": {"role": "assistant", "content": judged_reply(body)}, "done_reason": "stop"}

    def stream(self, body, on_text, timeout=600, on_thinking=None):
        reply = self.json(CHAT_URL, body)
        on_text(reply["message"]["content"])
        return reply


class OnTheBigMain(unittest.TestCase):
    def setUp(self):
        self.engine = BigMainEngine()
        for patch in (unittest.mock.patch.object(main_calls, "read_main", return_value=None),
                      unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)),
                      unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=self.engine.json),
                      unittest.mock.patch.object(connections, "engine_stream", side_effect=self.engine.stream)):
            patch.start()
            self.addCleanup(patch.stop)


class AJsonJobReachesTheEngineWithoutTools(OnTheBigMain):
    def test_a_json_job_keeps_the_chats_messages_and_format_but_sends_no_tools(self):
        connections.http_json(CHAT_URL, body={"model": BIG, "format": "json", "tools": TOOLS,
                                              "messages": [SYSTEM, {"role": "user", "content": "the facts"}]})
        sent = self.engine.bodies[0]
        self.assertNotIn("tools", sent)
        self.assertEqual(sent["format"], "json")
        self.assertEqual(sent["messages"][0], SYSTEM)

    def test_a_schema_format_sends_no_tools_either(self):
        connections.http_json(CHAT_URL, body={"model": BIG, "format": SCHEMA, "tools": TOOLS, "messages": [SYSTEM]})
        self.assertNotIn("tools", self.engine.bodies[0])
        self.assertEqual(self.engine.bodies[0]["format"], SCHEMA)

    def test_a_streamed_json_job_sends_no_tools(self):
        connections.stream_chat({"model": BIG, "format": "json", "tools": TOOLS, "messages": [SYSTEM]}, lambda text: None)
        self.assertNotIn("tools", self.engine.bodies[0])

    def test_a_tool_turn_without_a_format_keeps_its_tools(self):
        connections.http_json(CHAT_URL, body={"model": BIG, "tools": TOOLS, "messages": [SYSTEM]})
        self.assertEqual(self.engine.bodies[0]["tools"], TOOLS)


class TheSideJobsWorkOnTheBigMain(OnTheBigMain):
    def test_the_claim_check_corrects_a_false_claim_with_the_chats_prefix(self):
        reply = brain.be_honest("Done.", [], [], request="Turn the lights off.", model=BIG, prefix=PREFIX)
        self.assertEqual(reply, honesty.HONEST_NOTHING_DONE)
        self.assertEqual(len(self.engine.bodies), 2)
        for body in self.engine.bodies:
            self.assertEqual(body["messages"][0], SYSTEM)
            self.assertNotIn("tools", body)

    def test_memories_are_saved_with_the_chats_prefix(self):
        saved = []
        brain.extract_memories(CONFIG, "my sister is called Ella", "Nice.", save=saved.extend, prefix=PREFIX)
        self.assertEqual(saved, [{"about": "Sam", "fact": "has a sister called Ella"}])
        self.assertEqual(self.engine.bodies[0]["messages"][0], SYSTEM)
        self.assertNotIn("tools", self.engine.bodies[0])

    def test_the_safety_check_gets_a_verdict_and_sends_no_tools(self):
        verdict = screen_review.judged_in_time("a story about two adults", "", time.monotonic() + QUICK_SECONDS)
        self.assertIs(verdict, False)
        self.assertEqual(self.engine.bodies[0]["format"], "json")
        self.assertNotIn("tools", self.engine.bodies[0])


class BigMainServer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.bodies.append(body)
        if refuses_tools_with_a_format(body):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(GRAMMAR_ERROR)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        line = {"message": {"role": "assistant", "content": judged_reply(body)}, "done": True, "done_reason": "stop"}
        self.wfile.write(json.dumps(line).encode() + b"\n")


class FakeHub:
    tools = []
    routes = {}

    def refresh(self):
        pass

    def call(self, name, arguments):
        return ""


def agent_that_used_the_chats_tools(config, state, **kwargs):
    state["prefix"] = {"model": config["model"], "messages": [SYSTEM], "tools": TOOLS}
    return {"status": "done", "answer": ROOM_ANSWER, "tools_used": [], "raw": {"done_reason": "stop"}}


class ARoomTurnOnTheBigMain(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), BigMainServer)
        self.server.daemon_threads = True
        self.server.bodies = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = {"model": BIG, "persona": "p", "system_prompt": "s", "assistant_name": "Arty", "lookups": [],
                  "options": {"num_ctx": 24576}, "hidden_tools": [], "tool_groups": {}}
        for patch in (unittest.mock.patch.object(engine_guard, "ARTY_ENGINE_URL", url),
                      unittest.mock.patch.object(connections, "ARTY_ENGINE_URL", url),
                      unittest.mock.patch.object(agent_loop, "ARTY_ENGINE_URL", url),
                      unittest.mock.patch.object(honesty, "ARTY_ENGINE_URL", url),
                      unittest.mock.patch.object(engine_guard, "outside_work", engine_guard.OutsideWork()),
                      unittest.mock.patch.object(main_calls, "read_main", return_value=None),
                      unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)),
                      unittest.mock.patch.object(connections, "hub", FakeHub()),
                      unittest.mock.patch.object(room.native_calls, "look_up_first", return_value=([], "", None)),
                      unittest.mock.patch.object(room.agent_loop, "run_agent", side_effect=agent_that_used_the_chats_tools),
                      unittest.mock.patch.object(models, "budget_for", return_value=0),
                      unittest.mock.patch.object(models, "find", return_value=None),
                      unittest.mock.patch.object(model_choice.main_model_return, "holds_a_session", return_value=False),
                      unittest.mock.patch.object(room_notes, "STATE_FILE", Path(folder.name) / "room-notes.json")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_claim_check_in_a_room_turn_does_not_make_arty_pass(self):
        reply = room.room_turn([{"role": "user", "content": ROOM_SAID}], room.OwnerActivity(outside=engine_guard.OutsideWork()),
                               room.TurnTime(room.ROOM_TURN_SECONDS))
        self.assertEqual(reply.answer, ROOM_ANSWER)
        judged = [body for body in self.server.bodies if body.get("format") is not None]
        self.assertTrue(judged)
        self.assertTrue(all("tools" not in body for body in judged))


if __name__ == "__main__":
    unittest.main()
