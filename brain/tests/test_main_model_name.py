import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import models  # noqa: E402


class TheMainModelIsCalledArty(Server):
    def test_the_model_list_names_the_main_model_arty(self):
        with unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b"}), \
                unittest.mock.patch.object(models, "find", return_value=None), \
                unittest.mock.patch.object(models, "chat_models", return_value=[]), \
                unittest.mock.patch.object(models, "solo_entries", return_value=[]):
            status, body = self.request("GET", "/v1/models")
        ids = [entry["id"] for entry in json.loads(body)["data"]]
        self.assertEqual(status, 200)
        self.assertEqual(ids, ["Arty"])

    def listed_arty(self, config):
        main = {"name": "qwen3:14b", "billions": 14.8, "thinking": True, "tools": True, "vision": False}
        with unittest.mock.patch.object(brain, "load_config", return_value=config), \
                unittest.mock.patch.object(models, "find", return_value=main), \
                unittest.mock.patch.object(models, "chat_models", return_value=[]), \
                unittest.mock.patch.object(models, "solo_entries", return_value=[]):
            status, body = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        return json.loads(body)["data"][0]

    def test_arty_is_described_as_picking_the_model_for_each_request(self):
        arty = self.listed_arty({"model": "qwen3:14b", "coding_model": "mimo-v2.6:9b", "vision_model": "mimo-v2.6:9b"})
        self.assertEqual(arty["model"], "qwen3:14b")
        self.assertEqual(arty["description"],
                         "Picks the model for each request: qwen3:14b for chat, mimo-v2.6:9b for code and pictures")

    def test_separate_coding_and_picture_models_are_both_named(self):
        arty = self.listed_arty({"model": "qwen3:14b", "coding_model": "coder:7b", "vision_model": "eyes:7b"})
        self.assertEqual(arty["description"],
                         "Picks the model for each request: qwen3:14b for chat, coder:7b for code, eyes:7b for pictures")

    def test_with_no_other_models_arty_keeps_the_main_models_summary(self):
        arty = self.listed_arty({"model": "qwen3:14b"})
        self.assertEqual(arty["description"], "14.8B · thinks when needed · all tools")

    def test_the_old_name_still_reaches_the_main_model(self):
        self.assertEqual(brain.chosen_model("main:1b", "assistant-core", any_installed=False), "main:1b")
        self.assertEqual(brain.chosen_model("main:1b", "Arty", any_installed=False), "main:1b")

    def test_replies_name_the_model_arty(self):
        with unittest.mock.patch.object(brain, "run_turn", return_value=("hello", [], {}, None)):
            status, body = self.request("POST", "/v1/chat/completions",
                                        body={"model": "assistant-core", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "Arty")


if __name__ == "__main__":
    unittest.main()
