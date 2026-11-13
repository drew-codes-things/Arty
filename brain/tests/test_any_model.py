import json
import sys
import urllib.error
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402

DOWNLOADED = [
    {"name": "qwen3:14b", "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True},
    {"name": "deepseek-coder:33b", "billions": 33.0, "tools": False, "vision": False, "embedding": False, "thinking": False},
    {"name": "qwen2.5:72b", "billions": 72.7, "tools": True, "vision": False, "embedding": False, "thinking": False},
    {"name": "nomic-embed-text:latest", "billions": 0.1, "tools": False, "vision": False, "embedding": True, "thinking": False},
]
ANY_MODEL = {"X-Arty-Client-Tools": "1", "X-Arty-Any-Model": "1"}


def downloaded():
    return (unittest.mock.patch.object(models, "installed", return_value=DOWNLOADED),
            unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b"]),
            unittest.mock.patch.object(models, "toolless_allowed", return_value=False),
            unittest.mock.patch.object(models, "solo_models", return_value=["deepseek-coder:33b"]))


class ChosenModel(unittest.TestCase):
    def setUp(self):
        for patch in downloaded():
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_without_opting_in_only_arty_models_are_used(self):
        self.assertEqual(brain.chosen_model("main:1b", "arty/qwen2.5:72b", any_installed=False), "main:1b")
        self.assertEqual(brain.chosen_model("main:1b", "arty/qwen3:14b", any_installed=False), "qwen3:14b")

    def test_opting_in_allows_anything_downloaded_except_embedding_models(self):
        self.assertEqual(brain.chosen_model("main:1b", "arty/qwen2.5:72b", any_installed=True), "qwen2.5:72b")
        with self.assertRaises(brain.ApiError) as raised:
            brain.chosen_model("main:1b", "arty/nomic-embed-text", any_installed=True)
        self.assertEqual(raised.exception.status, 400)

    def test_opting_in_with_a_model_that_is_not_downloaded_is_an_error_not_a_quiet_swap(self):
        with self.assertRaises(brain.ApiError) as raised:
            brain.chosen_model("main:1b", "arty/never-pulled:7b", any_installed=True)
        self.assertIn("never-pulled:7b", str(raised.exception))

    def test_a_model_kept_for_plain_chats_cannot_be_given_arty_s_tools(self):
        with self.assertRaises(brain.ApiError) as raised:
            brain.chosen_model("main:1b", "arty/deepseek-coder:33b", any_installed=True)
        self.assertEqual(raised.exception.status, 400)

    def test_another_model_is_loaded_briefly_and_the_main_one_comes_back(self):
        config = {"model": "qwen3:14b", "keep_alive": -1}
        with unittest.mock.patch.object(model_choice, "schedule_main_reload") as reload:
            other = brain.use_requested_model(config, "arty/qwen2.5:72b", any_installed=True)
        self.assertTrue(other)
        self.assertEqual((config["model"], config["keep_alive"]), ("qwen2.5:72b", model_choice.OTHER_MODEL_KEEP_ALIVE))
        reload.assert_called_once()
        config = {"model": "qwen3:14b", "keep_alive": -1}
        with unittest.mock.patch.object(model_choice, "schedule_main_reload") as reload:
            self.assertFalse(brain.use_requested_model(config, "arty/qwen3:14b", any_installed=True))
        self.assertEqual(config["keep_alive"], -1)
        reload.assert_not_called()

    def test_plain_model_names_keep_the_main_model(self):
        self.assertEqual(brain.chosen_model("main:1b", "assistant-core", any_installed=True), "main:1b")


class ListingEverything(Server):
    def setUp(self):
        super().setUp()
        for patch in downloaded():
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def ids(self, path):
        status, body = self.request("GET", path)
        self.assertEqual(status, 200)
        return [entry["id"] for entry in json.loads(body)["data"]]

    def test_the_usual_list_is_unchanged(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={"model": "qwen3:14b"}), \
                unittest.mock.patch.object(models, "solo_models", return_value=[]):
            listed = self.ids("/v1/models")
        self.assertIn("arty/qwen3:14b", listed)
        self.assertNotIn("arty/qwen2.5:72b", listed)

    def test_asking_for_all_lists_every_downloaded_chat_model(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={"coding_model": "qwen2.5:72b"}):
            status, body = self.request("GET", "/v1/models?all=1")
        listed = {entry["id"]: entry for entry in json.loads(body)["data"]}
        self.assertEqual(status, 200)
        self.assertEqual(sorted(listed), ["arty/qwen2.5:72b", "arty/qwen3:14b"])
        self.assertIn("72.7B", listed["arty/qwen2.5:72b"]["description"])
        self.assertFalse(listed["arty/qwen3:14b"]["coding"])
        self.assertTrue(listed["arty/qwen2.5:72b"]["coding"])

    def test_the_full_list_says_how_much_context_each_model_gets(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={"options": {"num_ctx": 24576}}):
            status, body = self.request("GET", "/v1/models?all=1")
        self.assertEqual(status, 200)
        self.assertEqual({entry["context_tokens"] for entry in json.loads(body)["data"]}, {24576})

    def test_the_full_list_says_how_long_an_agent_reply_can_be(self):
        config = {"options": {"num_ctx": 24576, "num_predict": 2048}, "agent_options": {"num_predict": 4096}}
        with unittest.mock.patch.object(connections, "load_config", return_value=config):
            status, body = self.request("GET", "/v1/models?all=1")
        self.assertEqual(status, 200)
        self.assertEqual({entry["reply_tokens"] for entry in json.loads(body)["data"]}, {4096})

    def test_the_context_size_falls_back_to_the_brain_default(self):
        self.assertEqual(models.context_tokens({}), models.DEFAULT_CONTEXT_TOKENS)
        self.assertEqual(models.context_tokens({"options": {"num_ctx": "lots"}}), models.DEFAULT_CONTEXT_TOKENS)

    def test_the_full_list_marks_the_coding_model_and_coder_models(self):
        self.assertTrue(models.is_for_coding("mimo-v2.6:9b", "mimo-v2.6:9b"))
        self.assertTrue(models.is_for_coding("qwen2.5-coder:14b", ""))
        self.assertFalse(models.is_for_coding("qwen3:14b", "mimo-v2.6:9b"))
        self.assertFalse(models.is_for_coding("qwen3:14b", ""))

    def test_ollama_being_down_is_a_clear_503(self):
        with unittest.mock.patch.object(models, "installed", side_effect=urllib.error.URLError("refused")):
            status, body = self.request("GET", "/v1/models?all=1")
            self.assertEqual(status, 503)
            status, _ = self.request("POST", "/v1/chat/completions", headers=ANY_MODEL,
                                     body={"model": "arty/qwen2.5:72b", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(status, 503)

    def test_a_chat_naming_a_model_that_is_not_downloaded_is_refused(self):
        status, body = self.request("POST", "/v1/chat/completions", headers=ANY_MODEL,
                                    body={"model": "arty/never-pulled:7b", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 400)
        self.assertIn(b"never-pulled:7b", body)


if __name__ == "__main__":
    unittest.main()
