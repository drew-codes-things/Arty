import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_coding_chats import CONFIG, run_turns_offline  # noqa: E402

import brain  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402

SHIPPED = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
MAIN, VISION = "qwen3:14b", "mimo-v2.6:9b"
INSTALLED = [{"name": MAIN, "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True},
             {"name": VISION, "billions": 9.0, "tools": True, "vision": True, "embedding": False, "thinking": True}]


def found(name):
    return next((model for model in INSTALLED if model["name"] == name), None)


class CodingRunsOnTheMainModel(unittest.TestCase):
    def setUp(self):
        run_turns_offline(self)
        self.config = {**self.config, "coding_model": SHIPPED["coding_model"], "vision_model": SHIPPED["vision_model"]}
        for name, value in (("find", found), ("installed", lambda: INSTALLED), ("arty_models", lambda: [MAIN, VISION])):
            unittest.mock.patch.object(models, name, side_effect=value).start()

    def test_the_shipped_config_codes_on_the_main_model_and_looks_with_the_vision_model(self):
        self.assertEqual(MAIN, SHIPPED["model"])
        self.assertEqual(MAIN, SHIPPED["coding_model"])
        self.assertEqual(VISION, SHIPPED["vision_model"])

    def test_a_coding_chat_while_the_vision_model_is_loaded_stays_on_the_main_model_and_says_so(self):
        with self.assertLogs("brain", "INFO") as logged:
            brain.run_turn([{"role": "user", "content": "write a python function that reverses a string"}], "Arty")
        self.assertEqual(MAIN, self.ollama.bodies[0]["model"])
        self.make_room.assert_not_called()
        self.assertTrue(any(f"this turn runs on {MAIN}" in line for line in logged.output), logged.output)

    def test_a_picture_still_goes_to_the_vision_model(self):
        self.assertEqual(f"arty/{VISION}", model_choice.chat_model(self.config, "Arty", "what's on this?", True))

    def test_arty_is_described_with_one_model_for_chat_and_code(self):
        main = found(MAIN)
        self.assertEqual(f"Picks the model for each request: {MAIN} for chat and code, {VISION} for pictures",
                         model_choice.arty_description({**CONFIG, "model": MAIN, "coding_model": MAIN, "vision_model": VISION}, main))


if __name__ == "__main__":
    unittest.main()
