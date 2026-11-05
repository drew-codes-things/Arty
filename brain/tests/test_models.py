import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models


class Summary(unittest.TestCase):
    def test_a_thinking_tool_model(self):
        self.assertEqual(models.summary({"billions": 14.8, "thinking": True, "tools": True, "vision": False}),
                         "14.8B · thinks when needed · all tools")

    def test_a_chat_only_vision_model(self):
        self.assertEqual(models.summary({"billions": 7, "tools": False, "vision": True}), "7B · chat only, no tools · sees images")

    def test_unknown_size(self):
        self.assertEqual(models.summary({"billions": 0.0, "tools": True}), "all tools")


class AskOtherModel(unittest.TestCase):
    def test_a_model_outside_arty_is_refused_without_calling_it(self):
        standalone = {"name": "roleplay-model:12b", "embedding": False, "tools": True}
        with mock.patch.object(models, "find", return_value=standalone), \
                mock.patch.object(models, "arty_models", return_value=["qwen3:14b"]), \
                mock.patch.object(models, "_ollama") as ollama:
            reply = models.ask("roleplay-model:12b", "write a poem", {"model": "qwen3:14b"})
        ollama.assert_not_called()
        self.assertIn("isn't one of my models", reply)

    def test_an_arty_model_is_asked(self):
        allowed = {"name": "qwen3:14b", "embedding": False, "tools": True}
        with mock.patch.object(models, "find", return_value=allowed), \
                mock.patch.object(models, "arty_models", return_value=["qwen3:14b"]), \
                mock.patch.object(models, "_ollama", return_value={"message": {"content": "done"}}) as ollama:
            reply = models.ask("qwen3:14b", "write a poem", {"model": "qwen3:14b"})
        ollama.assert_called_once()
        self.assertEqual(reply, "qwen3:14b says:\ndone")


if __name__ == "__main__":
    unittest.main()
