import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
TOOLS_MODEL = {"name": "qwen3:14b", "size": 9_300_000_000, "capabilities": ["completion", "tools", "thinking"]}
VISION_MODEL = {"name": "mimo-v2.6:9b", "size": 6_100_000_000, "capabilities": ["completion", "vision", "tools"]}
PLAIN_MODEL = {"name": "gemma3:4b", "size": 3_300_000_000, "capabilities": ["completion"]}
PULLABLE = {"gemma3:12b": {"name": "gemma3:12b", "size": 4_900_000_000, "capabilities": ["completion", "tools"]}}
PULLED_BY_OTHERS = {"qwen3:14b"}
NOT_RUNNABLE = {"name": "split-model:7b", "size": 4_000_000_000, "capabilities": ["completion", "tools"], "runnable": False}
ENGINE_SCRIPTS = ("ai-pull", "ai-models", "ai-use", "ai-toolless")
SHARED_HELPER = "ai-engine-lib"
PROGRESS_STEP_SECONDS = 0.2
SLOW_PULL_SECONDS = 20
SLOW_FIFO_SECONDS = 1
STALL_LIMIT_SECONDS = 2
ODD_STATUS = "pulling C:\\new\\table"
TABBED_ERROR = "the part\tafter a tab"
DOWN_ENGINE_URL = "http://127.0.0.1:9"
NAMED_SOURCE = "hf.co/Someone/Vision-GGUF:Q4_K_M"


class FakeEngine:
    def __init__(self, models):
        self.models = {model["name"]: model for model in models}
        self.calls = []
        self.pull_mode = "quick"
        self.listing_fails = False
        self.pull_started = threading.Event()
        self.client_left = threading.Event()
        engine = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def answer(self, status, payload=None):
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def start_lines(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()

            def line(self, payload):
                self.wfile.write(json.dumps(payload).encode() + b"\n")
                self.wfile.flush()

            def stream_pull(self, name):
                self.start_lines()
                if engine.pull_mode == "stalls":
                    self.line({"status": "pulling 0123456789ab", "total": 10_000, "completed": 1})
                    try:
                        for _ in range(int(SLOW_PULL_SECONDS / PROGRESS_STEP_SECONDS)):
                            time.sleep(PROGRESS_STEP_SECONDS)
                            self.wfile.write(b"")
                            self.wfile.flush()
                            if engine.client_left.is_set():
                                return
                    except OSError:
                        engine.client_left.set()
                    return
                if engine.pull_mode == "odd_text":
                    self.line({"status": ODD_STATUS})
                    return self.line({"error": TABBED_ERROR})
                if engine.pull_mode == "error_then_success":
                    self.line({"error": "the second part failed its checksum"})
                    return self.line({"status": "success"})
                if engine.pull_mode == "slow":
                    try:
                        for completed in range(int(SLOW_PULL_SECONDS / PROGRESS_STEP_SECONDS)):
                            self.line({"status": "pulling 0123456789ab", "total": 10_000, "completed": completed})
                            time.sleep(PROGRESS_STEP_SECONDS)
                    except OSError:
                        engine.client_left.set()
                    return
                for completed in (0, 5_000, 10_000):
                    self.line({"status": "pulling 0123456789ab", "total": 10_000, "completed": completed})
                if engine.pull_mode == "fails_midway":
                    return self.line({"error": "download of sha256:0123 stopped at 5000 bytes; pull again to resume"})
                self.line({"status": "verifying sha256 digest"})
                engine.models[name] = PULLABLE.get(name) or {"name": name, "size": 1, "capabilities": ["completion"]}
                self.line({"status": "success"})

            def pull(self, body, name):
                engine.pull_started.set()
                if engine.pull_mode == "busy":
                    return self.answer(409, {"error": "another pull is running; the engine pulls one model at a time"})
                if name not in PULLABLE and not body.get("source"):
                    return self.answer(502, {"error": f"{name} was not found in the model library"})
                if body.get("stream", True) is not False:
                    return self.stream_pull(name)
                if engine.pull_mode == "slow":
                    time.sleep(SLOW_PULL_SECONDS)
                engine.models[name] = PULLABLE[name]
                return self.answer(200, {"status": "success"})

            def do_GET(self):
                engine.calls.append(("GET", self.path, None))
                if self.path == "/api/version":
                    return self.answer(200, {"version": "0.1.0"})
                if self.path == "/api/tags" and engine.listing_fails:
                    return self.answer(500, {"error": "the model store can't be read"})
                if self.path == "/api/tags":
                    return self.answer(200, {"models": [{"name": name, "model": name, "size": model["size"],
                                                         "capabilities": model["capabilities"]}
                                                        for name, model in engine.models.items()]})
                self.answer(404, {"error": f"no route GET {self.path}"})

            def do_POST(self):
                body = self.body()
                engine.calls.append(("POST", self.path, body))
                name = body.get("model") or body.get("name")
                if self.path == "/api/show":
                    if name not in engine.models:
                        return self.answer(404, {"error": f"model '{name}' not found"})
                    model = engine.models[name]
                    return self.answer(200, {"capabilities": model["capabilities"], "details": {},
                                             "runnable": model.get("runnable", True)})
                if self.path == "/api/pull":
                    return self.pull(body, name)
                self.answer(404, {"error": f"no route POST {self.path}"})

            def do_DELETE(self):
                body = self.body()
                engine.calls.append(("DELETE", self.path, body))
                name = body.get("model") or body.get("name")
                if self.path != "/api/delete":
                    return self.answer(404, {"error": "no route"})
                if name in PULLED_BY_OTHERS:
                    return self.answer(405, {"error": f"{name} was not pulled by the engine; it is left alone"})
                if name not in engine.models:
                    return self.answer(404, {"error": f"model '{name}' not found"})
                del engine.models[name]
                self.answer(200)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def posted(self, path):
        return [body for method, called, body in self.calls if method == "POST" and called == path]


def kill_the_session(script: subprocess.Popen) -> None:
    try:
        os.killpg(script.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if script.poll() is None:
        script.communicate(timeout=5)


class AiScripts(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.engine = FakeEngine([TOOLS_MODEL, VISION_MODEL, PLAIN_MODEL, NOT_RUNNABLE])
        self.addCleanup(self.engine.stop)
        self.config = self.folder / "brain.json"
        self.write_config({"model": "qwen3:14b", "arty_models": ["qwen3:14b", "mimo-v2.6:9b", "gemma3:4b", "gemma3:12b",
                                                                 "split-model:7b"]})
        self.env = {**os.environ, "ARTY_ENGINE_URL": self.engine.url, "BRAIN_CONFIG": str(self.config)}

    def write_config(self, config):
        self.config.write_text(json.dumps(config))

    def config_value(self, key):
        return json.loads(self.config.read_text()).get(key)

    def run_script(self, name, *arguments):
        return subprocess.run(["sh", str(BIN / name), *arguments], env=self.env, capture_output=True, text=True, timeout=30)

    def test_ai_models_lists_what_the_engine_has(self):
        finished = self.run_script("ai-models")
        self.assertEqual(0, finished.returncode, finished.stderr)
        for name in ("qwen3:14b", "mimo-v2.6:9b", "gemma3:4b"):
            self.assertIn(name, finished.stdout)
        self.assertIn("ai-models rm <model>", finished.stdout)

    def test_ai_models_rm_deletes_through_the_engine_and_reports_a_refusal(self):
        removed = self.run_script("ai-models", "rm", "gemma3:4b")
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertNotIn("gemma3:4b", self.engine.models)
        refused = self.run_script("ai-models", "rm", "qwen3:14b")
        self.assertEqual(1, refused.returncode)
        self.assertIn("was not pulled by the engine", refused.stdout + refused.stderr)

    def test_ai_pull_downloads_through_the_engine_and_lists_the_result(self):
        finished = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([{"model": "gemma3:12b", "stream": True}], self.engine.posted("/api/pull"))
        self.assertIn("gemma3:12b", finished.stdout)
        self.assertIn("100%", finished.stdout)

    def test_ai_pull_stops_at_a_model_the_engine_cannot_fetch(self):
        finished = self.run_script("ai-pull", "nothing-like-it:1b", "gemma3:12b")
        self.assertEqual(1, finished.returncode)
        self.assertIn("not found in the model library", finished.stdout + finished.stderr)
        self.assertEqual(1, len(self.engine.posted("/api/pull")))

    def test_ai_pull_from_a_named_source_sends_the_source_and_the_short_name(self):
        finished = self.run_script("ai-pull", "qwen2.5vl:7b", "--from", NAMED_SOURCE)
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([{"model": "qwen2.5vl:7b", "source": NAMED_SOURCE, "stream": True}], self.engine.posted("/api/pull"))
        self.assertIn("qwen2.5vl:7b", finished.stdout)
        before = self.run_script("ai-pull", "--from", NAMED_SOURCE, "qwen2.5vl:7b")
        self.assertEqual(0, before.returncode, before.stderr)
        self.assertEqual(2, len(self.engine.posted("/api/pull")))

    def test_ai_pull_from_needs_one_source_and_one_model(self):
        for arguments in (("qwen2.5vl:7b", "--from"), ("a:1", "b:1", "--from", NAMED_SOURCE), ("--from", NAMED_SOURCE),
                          ("a:1", "--from", NAMED_SOURCE, "--from", NAMED_SOURCE), ("a:1", "--from", "")):
            finished = self.run_script("ai-pull", *arguments)
            self.assertEqual(2, finished.returncode, arguments)
            self.assertIn("ai-pull <model> --from", finished.stdout + finished.stderr)
        self.assertEqual([], self.engine.posted("/api/pull"))

    def test_ai_pull_takes_the_source_written_with_an_equals_sign(self):
        finished = self.run_script("ai-pull", "qwen2.5vl:7b", f"--from={NAMED_SOURCE}")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([{"model": "qwen2.5vl:7b", "source": NAMED_SOURCE, "stream": True}], self.engine.posted("/api/pull"))

    def test_ai_pull_refuses_an_option_it_does_not_know_before_any_download(self):
        for arguments in (("qwen2.5vl:7b", "--source", NAMED_SOURCE), ("qwen2.5vl:7b", "-from", NAMED_SOURCE),
                          ("gemma3:12b", "-x"), ("--from=", "qwen2.5vl:7b")):
            with self.subTest(arguments=arguments):
                finished = self.run_script("ai-pull", *arguments)
                self.assertEqual(2, finished.returncode, finished.stdout)
                self.assertIn("ai-pull <model> --from", finished.stdout + finished.stderr)
        self.assertEqual([], self.engine.posted("/api/pull"))

    def test_ai_pull_without_models_shows_help_and_the_installed_list(self):
        finished = self.run_script("ai-pull")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("Usage: ai-pull", finished.stdout)
        self.assertIn("mimo-v2.6:9b", finished.stdout)

    def test_ai_pull_help_options_show_the_help_and_download_nothing(self):
        for arguments in (("--help",), ("-h",), ("qwen3:14b", "--help"), ("-h", "--from", NAMED_SOURCE)):
            with self.subTest(arguments=arguments):
                finished = self.run_script("ai-pull", *arguments)
                self.assertEqual(0, finished.returncode, finished.stdout)
                self.assertIn("Good fits for the RTX 3060", finished.stdout)
                self.assertNotIn("doesn't know the option", finished.stdout)
        self.assertEqual([], self.engine.posted("/api/pull"))

    def test_ai_use_switches_to_an_installed_tool_model(self):
        finished = self.run_script("ai-use", "mimo-v2.6:9b")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual("mimo-v2.6:9b", self.config_value("model"))
        self.assertIn("can also see images", finished.stdout)
        self.assertEqual([], self.engine.posted("/api/pull"))

    def test_ai_use_downloads_a_missing_model_first(self):
        finished = self.run_script("ai-use", "gemma3:12b")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([{"model": "gemma3:12b", "stream": True}], self.engine.posted("/api/pull"))
        self.assertEqual("gemma3:12b", self.config_value("model"))

    def test_ai_use_refuses_a_model_without_tools_unless_allowed(self):
        refused = self.run_script("ai-use", "gemma3:4b")
        self.assertEqual(1, refused.returncode)
        self.assertEqual("qwen3:14b", self.config_value("model"))
        self.write_config({**json.loads(self.config.read_text()), "allow_toolless_models": True})
        allowed = self.run_script("ai-use", "gemma3:4b")
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        self.assertEqual("gemma3:4b", self.config_value("model"))

    def test_ai_use_stops_when_the_download_fails(self):
        self.write_config({"model": "qwen3:14b"})
        finished = self.run_script("ai-use", "nothing-like-it:1b")
        self.assertNotEqual(0, finished.returncode)
        self.assertEqual("qwen3:14b", self.config_value("model"))

    def test_ai_toolless_off_warns_when_the_main_model_has_no_tools(self):
        self.write_config({"model": "gemma3:4b", "allow_toolless_models": True})
        finished = self.run_script("ai-toolless", "off")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertFalse(self.config_value("allow_toolless_models"))
        self.assertIn("has no tools", finished.stdout)
        self.assertEqual([{"model": "gemma3:4b"}], self.engine.posted("/api/show"))

    def test_ai_toolless_off_is_quiet_for_a_tool_model(self):
        finished = self.run_script("ai-toolless", "off")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertNotIn("has no tools", finished.stdout)

    def test_ai_toolless_on_needs_no_engine(self):
        self.env["ARTY_ENGINE_URL"] = "http://127.0.0.1:9"
        finished = self.run_script("ai-toolless", "on")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertTrue(self.config_value("allow_toolless_models"))

    def test_an_engine_that_is_down_is_named(self):
        self.env["ARTY_ENGINE_URL"] = DOWN_ENGINE_URL
        finished = self.run_script("ai-models")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("engine", (finished.stdout + finished.stderr).lower())

    def test_curls_own_reason_is_kept_when_the_engine_cannot_be_reached(self):
        self.env["ARTY_ENGINE_URL"] = DOWN_ENGINE_URL
        for name in ("ai-models", "ai-pull", "ai-use"):
            with self.subTest(script=name):
                finished = self.run_script(name, *(["gemma3:12b"] if name != "ai-models" else []))
                self.assertNotEqual(0, finished.returncode)
                self.assertIn("curl: (7)", finished.stderr)

    def test_a_pull_that_fails_midway_is_reported_with_the_engines_reason(self):
        self.engine.pull_mode = "fails_midway"
        finished = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(1, finished.returncode)
        self.assertIn("pull again to resume", finished.stdout + finished.stderr)
        self.assertNotIn("gemma3:12b", self.engine.models)

    def test_a_busy_engine_is_reported_and_nothing_switches(self):
        self.engine.pull_mode = "busy"
        pulled = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(1, pulled.returncode)
        self.assertIn("another pull is running", pulled.stdout + pulled.stderr)
        switched = self.run_script("ai-use", "gemma3:12b")
        self.assertEqual(1, switched.returncode)
        self.assertEqual("qwen3:14b", self.config_value("model"))

    def test_a_failed_listing_after_a_pull_is_named_not_left_to_jq(self):
        self.engine.listing_fails = True
        finished = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(1, finished.returncode)
        self.assertIn("the model store can't be read", finished.stdout + finished.stderr)
        self.assertNotIn("jq: error", finished.stderr)

    def test_a_pull_has_a_time_limit(self):
        self.engine.pull_mode = "slow"
        self.env["AI_PULL_MAX_SECONDS"] = "2"
        started = time.monotonic()
        finished = self.run_script("ai-pull", "gemma3:12b")
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, SLOW_PULL_SECONDS / 2)
        self.assertEqual(1, finished.returncode)
        self.assertTrue(self.engine.client_left.wait(5), "the engine never saw the client leave")

    def test_a_pull_that_stalls_is_given_up(self):
        self.engine.pull_mode = "stalls"
        self.env["AI_PULL_STALL_SECONDS"] = str(STALL_LIMIT_SECONDS)
        started = time.monotonic()
        finished = self.run_script("ai-pull", "gemma3:12b")
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, STALL_LIMIT_SECONDS)
        self.assertLess(elapsed, SLOW_PULL_SECONDS / 2)
        self.assertEqual(1, finished.returncode)
        self.assertIn("stopped before it finished", finished.stdout)

    def test_an_error_line_is_not_undone_by_a_later_success_line(self):
        self.engine.pull_mode = "error_then_success"
        finished = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(1, finished.returncode)
        self.assertIn("the second part failed its checksum", finished.stdout)

    def test_engine_text_is_shown_as_written(self):
        self.engine.pull_mode = "odd_text"
        finished = self.run_script("ai-pull", "gemma3:12b")
        self.assertEqual(1, finished.returncode)
        self.assertIn(f"  {ODD_STATUS}\n", finished.stdout)
        self.assertIn(TABBED_ERROR, finished.stdout)

    def stop_a_slow_pull_with(self, stop: signal.Signals) -> tuple[subprocess.Popen, str, float]:
        self.engine.pull_mode = "slow"
        script = subprocess.Popen(["sh", str(BIN / "ai-pull"), "gemma3:12b"], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: script.poll() is None and os.killpg(script.pid, signal.SIGKILL))
        self.assertTrue(self.engine.pull_started.wait(10))
        time.sleep(1)
        sent = time.monotonic()
        os.kill(script.pid, stop)
        output, errors = script.communicate(timeout=SLOW_PULL_SECONDS * 2)
        return script, output + errors, time.monotonic() - sent

    def test_a_signal_to_the_script_alone_stops_the_download_too(self):
        for stop in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=stop.name):
                self.engine.client_left.clear()
                self.engine.pull_started.clear()
                script, said, waited = self.stop_a_slow_pull_with(stop)
                self.assertLess(waited, SLOW_PULL_SECONDS / 2, said)
                self.assertTrue(self.engine.client_left.wait(5), "the engine never saw the client leave")
                self.assertEqual(128 + stop, script.returncode, said)
                self.assertIn("Stopped", said)

    def test_ctrl_c_stops_the_engines_download_too(self):
        self.engine.pull_mode = "slow"
        script = subprocess.Popen(["sh", str(BIN / "ai-pull"), "gemma3:12b"], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: script.poll() is None and os.killpg(script.pid, signal.SIGKILL))
        self.assertTrue(self.engine.pull_started.wait(10))
        time.sleep(1)
        os.killpg(script.pid, signal.SIGINT)
        output, errors = script.communicate(timeout=10)
        self.assertTrue(self.engine.client_left.wait(5), "the engine never saw the client leave")
        self.assertEqual(130, script.returncode, errors)
        self.assertIn("Stopped", output + errors)

    def test_ctrl_backslash_stops_the_engines_download_too(self):
        self.engine.pull_mode = "slow"
        script = subprocess.Popen(["sh", str(BIN / "ai-pull"), "gemma3:12b"], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: script.poll() is None and os.killpg(script.pid, signal.SIGKILL))
        self.assertTrue(self.engine.pull_started.wait(10))
        time.sleep(1)
        os.killpg(script.pid, signal.SIGQUIT)
        output, errors = script.communicate(timeout=10)
        self.assertTrue(self.engine.client_left.wait(5), "the engine never saw the client leave")
        self.assertEqual(128 + signal.SIGQUIT, script.returncode, errors)
        self.assertIn("Stopped", output + errors)

    def test_a_stop_before_the_download_starts_leaves_no_temporary_folder(self):
        slow_tools = self.folder / "slow-tools"
        slow_tools.mkdir()
        fifo_maker = slow_tools / "mkfifo"
        fifo_maker.write_text(f"#!/bin/sh\nsleep {SLOW_FIFO_SECONDS}\nexec {shutil.which('mkfifo')} \"$@\"\n")
        fifo_maker.chmod(0o755)
        temporary = self.folder / "tmp"
        temporary.mkdir()
        env = {**self.env, "PATH": f"{slow_tools}:{os.environ['PATH']}", "TMPDIR": str(temporary)}
        script = subprocess.Popen(["sh", str(BIN / "ai-pull"), "gemma3:12b"], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: script.poll() is None and os.killpg(script.pid, signal.SIGKILL))
        deadline = time.monotonic() + 10
        while not any(temporary.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(script.pid, signal.SIGTERM)
        output, errors = script.communicate(timeout=10)
        time.sleep(SLOW_FIFO_SECONDS + 0.5)
        self.assertEqual(128 + signal.SIGTERM, script.returncode, errors)
        self.assertEqual([], sorted(entry.name for entry in temporary.iterdir()), output + errors)

    def test_a_stop_while_the_download_is_still_starting_leaves_nothing_running(self):
        slow_tools = self.folder / "slow-tools"
        slow_tools.mkdir()
        started = self.folder / "request-being-written"
        request_writer = slow_tools / "jq"
        request_writer.write_text(f"#!/bin/sh\nif [ \"$1\" = -cn ]; then : > '{started}'; sleep {SLOW_FIFO_SECONDS}; fi\n"
                                  f"exec {shutil.which('jq')} \"$@\"\n")
        request_writer.chmod(0o755)
        env = {**self.env, "PATH": f"{slow_tools}:{os.environ['PATH']}"}
        script = subprocess.Popen(["sh", str(BIN / "ai-pull"), "gemma3:12b"], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(kill_the_session, script)
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(script.pid, signal.SIGTERM)
        output, errors = script.communicate(timeout=SLOW_FIFO_SECONDS + 5)
        self.assertEqual(128 + signal.SIGTERM, script.returncode, errors)
        self.assertIn("Stopped", output + errors)

    def test_a_stop_without_pkill_still_stops_the_scripts_own_helpers(self):
        tools = self.folder / "no-pkill-tools"
        tools.mkdir()
        missing_pkill = tools / "pkill"
        missing_pkill.write_text("#!/bin/sh\nexit 127\n")
        missing_pkill.chmod(0o755)
        helper_pid = self.folder / "helper-pid"
        stopper = self.folder / "stopper.sh"
        stopper.write_text(f". '{BIN / SHARED_HELPER}'\nsleep {SLOW_PULL_SECONDS} > /dev/null 2>&1 &\necho $! > '{helper_pid}'\n"
                           "download_pid='' steps_pid='' pull_folder=''\nstop_pull 143\n")
        env = {**self.env, "PATH": f"{tools}:{os.environ['PATH']}"}
        stopped = subprocess.run(["sh", str(stopper)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(143, stopped.returncode, stopped.stderr)
        helper = int(helper_pid.read_text())
        self.addCleanup(lambda: Path(f"/proc/{helper}").exists() and os.kill(helper, signal.SIGKILL))
        deadline = time.monotonic() + 3
        while Path(f"/proc/{helper}").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(Path(f"/proc/{helper}").exists(), "a helper of the script kept running after the stop")

    def test_ai_use_refuses_a_model_the_engine_cannot_run(self):
        self.write_config({**json.loads(self.config.read_text()), "allow_toolless_models": True})
        finished = self.run_script("ai-use", "split-model:7b")
        self.assertEqual(1, finished.returncode)
        self.assertIn("can't run", finished.stdout)
        self.assertEqual("qwen3:14b", self.config_value("model"))

    def test_ai_use_names_no_retired_dashboard(self):
        finished = self.run_script("ai-use", "not-listed:1b")
        self.assertEqual(1, finished.returncode)
        self.assertNotIn("Open WebUI", finished.stdout)
        self.assertNotIn("Open WebUI", (BIN / "ai-use").read_text())


class OneEngineHelper(unittest.TestCase):
    def test_the_engine_call_is_written_once_and_every_script_uses_it(self):
        for name in ENGINE_SCRIPTS:
            with self.subTest(script=name):
                text = (BIN / name).read_text()
                self.assertNotRegex(text, r"(?m)^engine_call\(\)")
                self.assertIn(f'. "$(dirname "$0")/{SHARED_HELPER}"', text)
        self.assertRegex((BIN / SHARED_HELPER).read_text(), r"(?m)^engine_call\(\)")

    def test_the_helper_is_deployed_next_to_the_scripts(self):
        bundled = re.search(r"tar czf.*?\n\s*services/\*\.timer", (ROOT / "deploy.sh").read_text(), re.S).group(0)
        for name in (*ENGINE_SCRIPTS, SHARED_HELPER):
            with self.subTest(script=name):
                self.assertIn(f"bin/{name}", bundled)

    def test_the_helper_is_installed_as_a_file_to_read_not_to_run(self):
        deploy = (ROOT / "deploy.sh").read_text()
        self.assertIn(f"chmod 644 /usr/local/bin/{SHARED_HELPER}", deploy)
        self.assertLess(deploy.index("install -m 755 deploy/bin/* /usr/local/bin/"),
                        deploy.index(f"chmod 644 /usr/local/bin/{SHARED_HELPER}"))

    def test_the_engine_address_comes_only_from_its_setting(self):
        for name in (*ENGINE_SCRIPTS, SHARED_HELPER):
            with self.subTest(script=name):
                self.assertNotIn("/etc/default/", (BIN / name).read_text())
        self.assertIn("ARTY_ENGINE_URL=${ARTY_ENGINE_URL:-http://127.0.0.1:11438}", (BIN / SHARED_HELPER).read_text())


if __name__ == "__main__":
    unittest.main()
