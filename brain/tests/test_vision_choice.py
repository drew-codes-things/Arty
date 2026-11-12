import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain

NEWEST_FIRST = [
    {"name": "qwen3.5:9b", "vision": True},
    {"name": "qwen3:14b", "vision": False},
    {"name": "qwen2.5vl:7b", "vision": True},
]


def installed_by_name(name):
    return next((model for model in NEWEST_FIRST if model["name"] == name), None)


class VisionChoice(unittest.TestCase):
    def use(self, config):
        replacements = ((brain, "load_config", lambda: config),
                        (brain.models, "installed", lambda refresh=False: NEWEST_FIRST),
                        (brain.models, "find", installed_by_name),
                        (brain.models, "arty_models", lambda: []),
                        (brain.models, "solo_models", lambda: []),
                        (brain.models, "is_separate", lambda model: False))
        for target, name, replacement in replacements:
            patcher = unittest.mock.patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_configured_vision_model_wins_over_a_newer_download(self):
        self.use({"model": "qwen3:14b", "vision_model": "qwen2.5vl:7b"})
        self.assertEqual(brain.vision_model()["name"], "qwen2.5vl:7b")

    def test_a_model_asked_for_by_name_still_wins(self):
        self.use({"model": "qwen3:14b", "vision_model": "qwen2.5vl:7b"})
        self.assertEqual(brain.vision_model("qwen3.5:9b")["name"], "qwen3.5:9b")

    def test_a_configured_model_that_cannot_see_falls_back_to_one_that_can(self):
        self.use({"model": "qwen3:14b", "vision_model": "qwen3:14b"})
        self.assertTrue(brain.vision_model()["vision"])

    def test_a_configured_model_that_is_not_downloaded_falls_back_to_one_that_can_see(self):
        self.use({"model": "qwen3:14b", "vision_model": "gone:7b"})
        self.assertTrue(brain.vision_model()["vision"])

    def test_without_a_setting_any_installed_vision_model_is_used(self):
        self.use({"model": "qwen3:14b"})
        self.assertTrue(brain.vision_model()["vision"])


if __name__ == "__main__":
    unittest.main()
