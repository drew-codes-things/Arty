import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import CLIENT_SYSTEM, CONFIG, READ_FILE, FakeOllama  # noqa: E402
from test_any_model import downloaded  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import models  # noqa: E402

SHIPPED = Path(__file__).resolve().parents[1] / "brain.json"
MIMO = "mimo-v2.6:9b"
QWEN = "qwen3:14b"
CARD = {"temperature": 0.6, "top_k": 20, "top_p": 0.95}
AGENT = {
    "num_predict": 4096,
    "repeat_penalty": 1.0,
    "sampling": "card",
    "steady": {"temperature": 0.15},
    "models": {
        "default": {**CARD, "presence_penalty": 0.0},
        MIMO: {**CARD, "presence_penalty": 0.0},
        QWEN: {**CARD, "min_p": 0.0, "without_thinking": {"temperature": 0.7, "top_p": 0.8}},
        "qwen2.5vl:7b": {"temperature": 0.0001, "repeat_penalty": 1.05},
    },
}
CHAT = {"temperature": 0.4, "num_ctx": 24576, "num_predict": 2048, "repeat_penalty": 1.1}
HARD_REQUEST = "debug why the parser fails on empty input"
PLAIN_REQUEST = "what does main.py do?"


def turn_config(model, **agent):
    return {"model": model, "think": "auto", "options": dict(CHAT), "agent_options": {**AGENT, **agent}}


class CardSampling(unittest.TestCase):
    def test_mimo_gets_its_own_generation_config(self):
        self.assertEqual(models.agent_options(turn_config(MIMO), MIMO, thinks=True),
                         {"temperature": 0.6, "top_k": 20, "top_p": 0.95, "presence_penalty": 0.0, "repeat_penalty": 1.0,
                          "num_predict": 4096, "num_ctx": 24576})

    def test_qwen3_follows_its_card_for_thinking_and_for_plain_replies(self):
        thinking = models.agent_options(turn_config(QWEN), QWEN, thinks=True)
        plain = models.agent_options(turn_config(QWEN), QWEN, thinks=False)
        self.assertEqual((thinking["temperature"], thinking["top_p"], thinking["top_k"]), (0.6, 0.95, 20))
        self.assertEqual((plain["temperature"], plain["top_p"], plain["top_k"]), (0.7, 0.8, 20))
        self.assertNotIn("without_thinking", thinking)

    def test_a_model_without_its_own_entry_gets_the_default(self):
        options = models.agent_options(turn_config("other:8b"), "other:8b", thinks=True)
        self.assertEqual((options["temperature"], options["top_k"], options["top_p"]), (0.6, 20, 0.95))

    def test_a_latest_tag_finds_the_entry_for_the_bare_name(self):
        config = turn_config("x", models={"coder": {"temperature": 0.3}, "default": {}})
        self.assertEqual(models.agent_options(config, "coder:latest", thinks=True)["temperature"], 0.3)

    def test_exact_copying_wins_over_a_cards_repeat_penalty(self):
        self.assertEqual(models.agent_options(turn_config("qwen2.5vl:7b"), "qwen2.5vl:7b", thinks=True)["repeat_penalty"], 1.0)

    def test_the_steady_arm_sends_the_old_single_temperature_to_every_model(self):
        for model in (MIMO, QWEN, "other:8b"):
            options = models.agent_options(turn_config(model, sampling="steady"), model, thinks=True)
            self.assertEqual(options["temperature"], 0.15, model)
            self.assertNotIn("top_k", options, model)
            self.assertEqual(options["repeat_penalty"], 1.0, model)

    def test_an_unknown_arm_falls_back_to_the_cards_and_says_so(self):
        with self.assertLogs("brain.models", "WARNING"):
            options = models.agent_options(turn_config(MIMO, sampling="greedy"), MIMO, thinks=True)
        self.assertEqual(options["temperature"], 0.6)

    def test_the_older_flat_block_still_works(self):
        config = {"options": dict(CHAT), "agent_options": {"temperature": 0.15, "num_predict": 4096, "repeat_penalty": 1.0}}
        self.assertEqual(models.agent_options(config, MIMO, thinks=True),
                         {"temperature": 0.15, "num_ctx": 24576, "num_predict": 4096, "repeat_penalty": 1.0})


class SamplingOnTurns(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = {**CONFIG, **turn_config(QWEN), "conversations_dir": folder.name}
        patches = [
            unittest.mock.patch.object(brain, "load_config", side_effect=lambda: json.loads(json.dumps(self.config))),
            unittest.mock.patch.object(brain.hub, "refresh"),
            unittest.mock.patch.object(brain.hub, "tools", []),
            unittest.mock.patch.object(brain.hub, "routes", {}),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
            unittest.mock.patch.object(brain, "be_honest", side_effect=lambda text, *_: text),
            unittest.mock.patch.object(models, "find", return_value={"name": QWEN, "billions": 14.8, "tools": True,
                                                                        "vision": False, "embedding": False, "thinking": True}),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def options(self, text, agent=True):
        ollama = FakeOllama()
        messages = [{"role": "system", "content": CLIENT_SYSTEM}, {"role": "user", "content": text}]
        with unittest.mock.patch.object(brain, "http_json", side_effect=ollama):
            brain.run_turn(messages, "Arty", client_tools=[READ_FILE], agent=agent)
        return ollama.bodies[0]["options"]

    def test_a_thinking_agent_turn_uses_the_thinking_card_values(self):
        options = self.options(HARD_REQUEST)
        self.assertEqual((options["temperature"], options["top_p"], options["top_k"]), (0.6, 0.95, 20))

    def test_a_plain_agent_turn_uses_the_plain_card_values(self):
        options = self.options(PLAIN_REQUEST)
        self.assertEqual((options["temperature"], options["top_p"]), (0.7, 0.8))

    def test_chat_turns_keep_the_chat_options(self):
        self.assertEqual(self.options(PLAIN_REQUEST, agent=False), CHAT)


class SamplingInTheModelList(Server):
    def setUp(self):
        super().setUp()
        for patch in downloaded():
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_each_model_lists_the_sampling_agent_turns_use(self):
        with unittest.mock.patch.object(brain, "load_config", return_value=turn_config(QWEN)):
            status, body = self.request("GET", "/v1/models?all=1")
        self.assertEqual(status, 200)
        listed = {entry["model"]: entry["agent_sampling"] for entry in json.loads(body)["data"]}
        self.assertEqual(listed[QWEN]["arm"], "card")
        self.assertEqual(listed[QWEN]["thinking"], {**CARD, "min_p": 0.0})
        self.assertEqual(listed[QWEN]["without_thinking"], {**CARD, "min_p": 0.0, "temperature": 0.7, "top_p": 0.8})
        self.assertEqual(listed["qwen2.5:72b"]["thinking"], {**CARD, "presence_penalty": 0.0})


class ShippedSampling(unittest.TestCase):
    def setUp(self):
        self.agent = json.loads(SHIPPED.read_text())["agent_options"]

    def test_the_shipped_brain_uses_the_cards(self):
        self.assertEqual(self.agent["sampling"], "card")
        self.assertEqual(self.agent["repeat_penalty"], 1.0)
        self.assertEqual(self.agent["steady"], {"temperature": 0.15})

    def test_the_shipped_values_match_each_models_card(self):
        entries = self.agent["models"]
        self.assertEqual({key: entries[MIMO][key] for key in CARD}, CARD)
        self.assertEqual({key: entries[QWEN][key] for key in CARD}, CARD)
        self.assertEqual(entries[QWEN]["without_thinking"], {"temperature": 0.7, "top_p": 0.8})
        self.assertEqual(entries["qwen2.5vl:7b"]["temperature"], 0.0001)
        self.assertEqual({key: entries["default"][key] for key in CARD}, CARD)

    def test_the_shipped_file_names_only_the_public_models(self):
        self.assertEqual(set(self.agent["models"]), {"default", MIMO, QWEN, "qwen2.5vl:7b"})


if __name__ == "__main__":
    unittest.main()
