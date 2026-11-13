import os
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402
from native_tools import NATIVE_TOOLS  # noqa: E402

CONFIG = {
    "model": "qwen3:14b",
    "coding_model": "coder:9b",
    "model_strengths": {"coder:9b": "code changes across several files", "qwen2.5vl:7b": "reading pictures and screenshots"},
}


INSTALLED = [{"name": name, "vision": name == "qwen2.5vl:7b", "tools": True, "embedding": False}
             for name in ("qwen3:14b", "coder:9b", "qwen2.5vl:7b")]


def installed_named(*names):
    return [model for model in INSTALLED if model["name"] in names]


class HandOffTool(unittest.TestCase):
    def describe(self, allowed, installed=INSTALLED):
        with unittest.mock.patch.object(models, "arty_models", return_value=allowed), \
                unittest.mock.patch.object(models, "installed", return_value=installed):
            return models.handoff_tool(CONFIG, NATIVE_TOOLS["ask_other_model"])["function"]["description"]

    def test_an_unreachable_ollama_leaves_the_plain_description(self):
        with unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b", "coder:9b"]), \
                unittest.mock.patch.object(models, "installed", side_effect=urllib.error.URLError("refused")):
            definition = models.handoff_tool(CONFIG, NATIVE_TOOLS["ask_other_model"])
        self.assertEqual(definition, NATIVE_TOOLS["ask_other_model"])

    def test_a_model_that_is_not_downloaded_is_never_offered(self):
        description = self.describe(["qwen3:14b", "coder:9b", "qwen2.5vl:7b"], installed_named("qwen3:14b", "qwen2.5vl:7b"))
        self.assertNotIn("coder:9b", description)
        self.assertIn("qwen2.5vl:7b", description)

    def test_the_tool_names_each_other_model_and_what_it_is_best_at(self):
        description = self.describe(["qwen3:14b", "coder:9b", "qwen2.5vl:7b"])
        self.assertIn("coder:9b: code changes across several files", description)
        self.assertIn("qwen2.5vl:7b: reading pictures and screenshots", description)

    def test_the_main_model_is_not_offered_to_itself(self):
        self.assertNotIn("qwen3:14b:", self.describe(["qwen3:14b", "coder:9b"]))

    def test_only_allowed_models_are_offered(self):
        self.assertNotIn("qwen2.5vl:7b", self.describe(["qwen3:14b", "coder:9b"]))

    def test_with_no_other_model_the_plain_description_is_kept(self):
        self.assertEqual(self.describe(["qwen3:14b"]), NATIVE_TOOLS["ask_other_model"]["function"]["description"])

    def test_the_shared_definition_is_never_changed(self):
        before = NATIVE_TOOLS["ask_other_model"]["function"]["description"]
        self.describe(["qwen3:14b", "coder:9b"])
        self.assertEqual(NATIVE_TOOLS["ask_other_model"]["function"]["description"], before)


class AgentModel(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(models, "installed", return_value=INSTALLED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_agent_asking_for_arty_gets_the_coding_model(self):
        self.assertEqual(model_choice.agent_model(CONFIG, "Arty"), "arty/coder:9b")
        self.assertEqual(model_choice.agent_model(CONFIG, ""), "arty/coder:9b")

    def test_a_coding_model_that_is_not_downloaded_falls_back_to_the_main_model_and_says_so(self):
        with unittest.mock.patch.object(models, "installed", return_value=installed_named("qwen3:14b")), \
                self.assertLogs("brain", level="WARNING") as logged:
            self.assertEqual(model_choice.agent_model(CONFIG, "Arty"), "Arty")
        self.assertIn("coder:9b", "\n".join(logged.output))

    def test_an_agent_naming_a_model_that_is_gone_gets_the_coding_model_and_a_warning(self):
        with self.assertLogs("brain", level="WARNING") as logged:
            self.assertEqual(model_choice.agent_model(CONFIG, "arty/qwen2.5-coder:14b"), "arty/coder:9b")
        self.assertIn("qwen2.5-coder:14b", "\n".join(logged.output))

    def test_an_agent_naming_a_gone_model_with_no_coding_model_gets_the_main_model(self):
        with self.assertLogs("brain", level="WARNING"):
            self.assertEqual(model_choice.agent_model({"model": "qwen3:14b"}, "arty/qwen2.5-coder:14b"), "Arty")

    def test_an_unreachable_ollama_falls_back_to_the_main_model(self):
        with unittest.mock.patch.object(models, "installed", side_effect=urllib.error.URLError("refused")), \
                self.assertLogs("brain", level="WARNING"):
            self.assertEqual(model_choice.agent_model(CONFIG, "Arty"), "Arty")

    def test_an_agent_that_names_a_model_keeps_it(self):
        self.assertEqual(model_choice.agent_model(CONFIG, "arty/qwen3:14b"), "arty/qwen3:14b")

    def test_without_a_coding_model_the_request_is_unchanged(self):
        self.assertEqual(model_choice.agent_model({"model": "qwen3:14b"}, "Arty"), "Arty")


class VisionFallback(unittest.TestCase):
    def pick(self, installed, configured="gone-vl:7b"):
        with unittest.mock.patch.object(models, "installed", return_value=installed), \
                unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b", "twin:9b"]), \
                unittest.mock.patch.object(models, "solo_models", return_value=["other:9b"]), \
                unittest.mock.patch.object(connections, "load_config", return_value={"vision_model": configured}):
            return brain.vision_model()["name"]

    def test_a_missing_vision_model_falls_back_only_to_an_arty_model(self):
        installed = [{"name": "other:9b", "vision": True, "tools": True, "embedding": False},
                     {"name": "twin:9b", "vision": True, "tools": True, "embedding": False}]
        self.assertEqual(self.pick(installed), "twin:9b")

    def test_a_model_outside_arty_is_never_the_fallback(self):
        installed = [{"name": "other:9b", "vision": True, "tools": True, "embedding": False}]
        with self.assertRaises(brain.ApiError):
            self.pick(installed)

    def test_the_configured_vision_model_is_used_when_it_is_downloaded(self):
        installed = [{"name": "qwen2.5vl:7b", "vision": True, "tools": False, "embedding": False}]
        self.assertEqual(self.pick(installed, "qwen2.5vl:7b"), "qwen2.5vl:7b")


if __name__ == "__main__":
    unittest.main()
