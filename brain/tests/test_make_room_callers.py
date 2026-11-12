import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import models  # noqa: E402

VISION = {"name": "qwen2.5vl:7b", "vision": True}
CODER = {"name": "coder:9b", "tools": True, "vision": False, "embedding": False, "billions": 9}


class CallsInOrder:
    def __init__(self):
        self.calls = []

    def make_room(self, name, _budget):
        self.calls.append(("make_room", name))
        return []

    def chat(self, label):
        def record(*_, **__):
            self.calls.append(("chat", label))
            return {"message": {"content": "ok"}}
        return record


class MakeRoomBeforeAnotherModel(unittest.TestCase):
    def test_looking_at_a_picture_makes_room_for_the_vision_model_first(self):
        order = CallsInOrder()
        with unittest.mock.patch.object(models, "make_room", order.make_room), \
                unittest.mock.patch.object(brain, "vision_model", return_value=VISION), \
                unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b", "options": {}}), \
                unittest.mock.patch.object(brain, "http_json", order.chat("vision")):
            brain.look(["aGk="], "what is this?")
        self.assertEqual(order.calls, [("make_room", "qwen2.5vl:7b"), ("chat", "vision")])

    def test_handing_a_job_to_another_model_makes_room_first(self):
        order = CallsInOrder()
        config = {"model": "qwen3:14b", "options": {}}
        with unittest.mock.patch.object(models, "make_room", order.make_room), \
                unittest.mock.patch.object(models, "find", return_value=CODER), \
                unittest.mock.patch.object(models, "in_arty", return_value=True), \
                unittest.mock.patch.object(models, "_ollama", order.chat("ask")):
            models.ask("coder:9b", "write a function", config)
        self.assertEqual(order.calls, [("make_room", "coder:9b"), ("chat", "ask")])

    def test_a_turn_on_another_model_makes_room_for_it(self):
        order = CallsInOrder()
        config = {"model": "qwen3:14b"}
        with unittest.mock.patch.object(models, "make_room", order.make_room), \
                unittest.mock.patch.object(brain, "chosen_model", return_value="coder:9b"), \
                unittest.mock.patch.object(brain, "schedule_main_reload"):
            self.assertTrue(brain.use_requested_model(config, "arty/coder:9b", False))
        self.assertEqual(order.calls, [("make_room", "coder:9b")])

    def test_a_turn_on_the_main_model_needs_no_room(self):
        order = CallsInOrder()
        with unittest.mock.patch.object(models, "make_room", order.make_room), \
                unittest.mock.patch.object(brain, "chosen_model", return_value="qwen3:14b"):
            self.assertFalse(brain.use_requested_model({"model": "qwen3:14b"}, "Arty", False))
        self.assertEqual(order.calls, [])


if __name__ == "__main__":
    unittest.main()
