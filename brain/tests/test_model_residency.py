import json
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import CLIENT_SYSTEM, READ_FILE, FakeOllama  # noqa: E402

import brain  # noqa: E402
import models  # noqa: E402

WINDOW = 24576
MAIN = "qwen3:14b"
CODER = "coder:9b"
CONFIG = {"model": MAIN, "coding_model": CODER, "vision_model": CODER, "keep_alive": -1,
          "persona": "You are Arty.", "system_prompt": "Use your tools.", "tool_groups": {}, "approval": {},
          "options": {"temperature": 0.4, "num_ctx": WINDOW, "num_predict": 2048, "repeat_penalty": 1.1},
          "agent_options": {"temperature": 0.15, "num_predict": 4096, "repeat_penalty": 1.0}}
INSTALLED = [
    {"name": MAIN, "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True, "bytes": 9},
    {"name": CODER, "billions": 9.0, "tools": True, "vision": True, "embedding": False, "thinking": True, "bytes": 6},
]
PICTURE = {"role": "user", "content": [{"type": "text", "text": "what is it?"},
                                       {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}}]}


def found(name):
    return next((model for model in INSTALLED if model["name"] in (name, f"{name}:latest")), None)


def stop_timer(main_return):
    if main_return.timer is not None:
        main_return.timer.cancel()


def keep_alive_minutes(value: str) -> int:
    return int(value.removesuffix("m"))


class Residency(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = {**CONFIG, "conversations_dir": folder.name}
        self.main_return = brain.MainModelReturn()
        self.reload = unittest.mock.Mock()
        self.ollama = FakeOllama()
        patches = [
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: json.loads(json.dumps(self.config))),
            unittest.mock.patch.object(brain, "main_model_return", self.main_return),
            unittest.mock.patch.object(brain, "reload_main_model", self.reload),
            unittest.mock.patch.object(brain, "http_json", side_effect=self.ollama),
            unittest.mock.patch.object(brain.hub, "refresh"),
            unittest.mock.patch.object(brain.hub, "tools", []),
            unittest.mock.patch.object(brain.hub, "routes", {}),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
            unittest.mock.patch.object(brain, "be_honest", side_effect=lambda text, *_: text),
            unittest.mock.patch.object(models, "make_room", return_value=[]),
            unittest.mock.patch.object(models, "find", side_effect=found),
            unittest.mock.patch.object(models, "installed", return_value=INSTALLED),
            unittest.mock.patch.object(models, "arty_models", return_value=[MAIN, CODER]),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)
        self.addCleanup(stop_timer, self.main_return)

    def agent_step(self):
        messages = [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": "fix the parser"}]
        brain.run_turn(messages, "Arty", client_tools=[READ_FILE], agent=True)

    def seconds_until_main_returns(self):
        return self.main_return.due - time.monotonic()

    def test_an_agent_step_keeps_the_coding_model_for_the_session(self):
        self.agent_step()
        self.assertEqual(self.ollama.bodies[0]["model"], CODER)
        self.assertEqual(self.ollama.bodies[0]["keep_alive"], brain.AGENT_KEEP_ALIVE)

    def test_the_main_model_comes_back_only_after_the_session_goes_quiet(self):
        self.agent_step()
        self.assertGreater(self.seconds_until_main_returns(), brain.AGENT_QUIET_SECONDS - 5)
        self.assertIsNotNone(self.main_return.timer)
        self.reload.assert_not_called()

    def test_ollama_never_drops_the_coding_model_before_the_brain_brings_the_main_one_back(self):
        self.assertGreater(keep_alive_minutes(brain.AGENT_KEEP_ALIVE) * 60, brain.AGENT_QUIET_SECONDS)

    def test_each_new_step_pushes_the_return_back(self):
        self.agent_step()
        first = self.main_return.due
        time.sleep(0.05)
        self.agent_step()
        self.assertGreater(self.main_return.due, first)

    def test_a_step_that_fails_still_lets_the_main_model_come_back(self):
        with unittest.mock.patch.object(brain, "http_json", side_effect=OSError("runner gone")):
            with self.assertRaises(OSError):
                self.agent_step()
        self.assertEqual(self.main_return.running, 0)
        self.assertIsNotNone(self.main_return.timer)

    def test_no_return_fires_while_an_agent_step_is_running(self):
        self.main_return.hold(CODER)
        self.main_return.schedule(self.config, 0.05)
        time.sleep(0.2)
        self.reload.assert_not_called()
        self.main_return.release(self.config, 0.05)
        self.main_return.timer.join(2)
        self.reload.assert_called_once_with(self.config)

    def test_a_shorter_return_never_cuts_a_session_short(self):
        self.main_return.hold(CODER)
        self.main_return.release(self.config, brain.AGENT_QUIET_SECONDS)
        self.main_return.schedule(self.config, brain.VISION_MAIN_RETURN_SECONDS)
        self.assertGreater(self.seconds_until_main_returns(), brain.AGENT_QUIET_SECONDS - 5)

    def test_a_coding_chat_on_the_session_model_keeps_its_session_keep_alive(self):
        self.main_return.hold(CODER)
        self.main_return.release(self.config, brain.AGENT_QUIET_SECONDS)
        config = dict(self.config)
        brain.use_requested_model(config, f"arty/{CODER}", False)
        self.assertEqual(config["keep_alive"], brain.AGENT_KEEP_ALIVE)

    def test_a_coding_chat_without_a_session_still_lets_the_model_go_soon(self):
        config = dict(self.config)
        brain.use_requested_model(config, f"arty/{CODER}", False)
        self.assertEqual(config["keep_alive"], brain.OTHER_MODEL_KEEP_ALIVE)

    def direct_vision(self):
        handler = object.__new__(brain.Handler)
        handler.send_json = unittest.mock.Mock()
        handler.vision({}, [PICTURE])

    def test_a_direct_picture_during_a_session_keeps_the_session_keep_alive(self):
        self.main_return.hold(CODER)
        self.main_return.release(self.config, brain.AGENT_QUIET_SECONDS)
        self.direct_vision()
        self.assertEqual(self.ollama.bodies[-1]["keep_alive"], brain.AGENT_KEEP_ALIVE)
        self.assertGreater(self.seconds_until_main_returns(), brain.AGENT_QUIET_SECONDS - 5)

    def test_a_direct_picture_without_a_session_keeps_its_short_keep_alive(self):
        self.direct_vision()
        self.assertEqual(self.ollama.bodies[-1]["keep_alive"], brain.VISION_KEEP_ALIVE)

    def test_looking_at_an_attachment_during_a_session_does_not_unload_the_model(self):
        self.main_return.hold(CODER)
        brain.look(["aW1n"], "what is it?")
        self.assertEqual(self.ollama.bodies[-1]["keep_alive"], brain.AGENT_KEEP_ALIVE)

    def test_handing_a_job_to_the_session_model_does_not_unload_it(self):
        self.main_return.hold(CODER)
        sent = []
        with unittest.mock.patch.object(models, "_ollama", side_effect=lambda path, body, timeout=30: sent.append(body) or {}):
            brain.run_native("ask_other_model", {"model": CODER, "prompt": "write a function"}, self.config, False, None)
        self.assertEqual(sent[0]["keep_alive"], brain.AGENT_KEEP_ALIVE)

    def test_handing_a_job_to_another_model_still_unloads_it_after(self):
        sent = []
        with unittest.mock.patch.object(models, "_ollama", side_effect=lambda path, body, timeout=30: sent.append(body) or {}):
            brain.run_native("ask_other_model", {"model": CODER, "prompt": "write a function"}, self.config, False, None)
        self.assertEqual(sent[0]["keep_alive"], 0)


class OneContextSize(unittest.TestCase):
    def setUp(self):
        self.config = dict(CONFIG)
        self.bodies = []
        patches = [
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: dict(self.config)),
            unittest.mock.patch.object(brain, "http_json", side_effect=self.record),
            unittest.mock.patch.object(models, "make_room", return_value=[]),
            unittest.mock.patch.object(models, "find", side_effect=found),
            unittest.mock.patch.object(models, "installed", return_value=INSTALLED),
            unittest.mock.patch.object(models, "arty_models", return_value=[MAIN, CODER]),
            unittest.mock.patch.object(models, "_ollama", side_effect=lambda path, body, timeout=30: self.record("", body)),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def record(self, url, body=None, **_):
        self.bodies.append(body)
        return {"message": {"role": "assistant", "content": json.dumps({"for_arty": True, "facts": [], "kind": "A"})}}

    def window_of_last_call(self):
        return self.bodies[-1]["options"]["num_ctx"]

    def test_bringing_the_main_model_back_loads_it_at_the_chat_window(self):
        brain.reload_main_model(self.config).join(5)
        self.assertEqual(self.window_of_last_call(), WINDOW)

    def test_every_model_call_uses_the_same_window(self):
        calls = {
            "look": lambda: brain.look(["aW1n"], "what is it?"),
            "judge": lambda: brain.ask_judge("check", 20, CODER, brain.AGENT_KEEP_ALIVE),
            "quiz": lambda: brain.judge_answer(self.config, "q", "a", "a"),
            "complete": lambda: brain.complete_text("write", json_mode=True),
            "addressed": lambda: brain.addressed_to_arty(self.config, "hello", []),
            "memories": lambda: brain.extract_memories(self.config, "I like tea", "Noted.", save=lambda facts: None),
            "ask": lambda: models.ask(CODER, "write a function", self.config),
        }
        for name, call in calls.items():
            call()
            self.assertEqual(self.window_of_last_call(), WINDOW, name)


class ReturnTimer(unittest.TestCase):
    def test_the_return_runs_once_after_the_last_hold_is_released(self):
        main_return = brain.MainModelReturn()
        with unittest.mock.patch.object(brain, "reload_main_model") as reload:
            main_return.hold(CODER)
            main_return.hold(CODER)
            main_return.release(CONFIG, 0.05)
            time.sleep(0.15)
            reload.assert_not_called()
            main_return.release(CONFIG, 0.05)
            main_return.timer.join(2)
        reload.assert_called_once_with(CONFIG)
        self.assertFalse(main_return.keeps(CODER))

    def test_holds_from_two_threads_are_counted(self):
        main_return = brain.MainModelReturn()
        with unittest.mock.patch.object(brain, "reload_main_model"):
            workers = [threading.Thread(target=lambda: (main_return.hold(CODER), main_return.release(CONFIG, 60)))
                       for _ in range(20)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
            self.assertEqual(main_return.running, 0)
            self.assertTrue(main_return.keeps(CODER))
            stop_timer(main_return)


if __name__ == "__main__":
    unittest.main()
