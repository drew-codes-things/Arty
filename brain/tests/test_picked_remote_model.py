import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"
REMOTE = "remote/starfire/big-remote-q4"
REMOTE_EMBEDDER = "remote/starfire/embedder"
INSTALLED = [
    {"name": MAIN, "billions": 34.7, "tools": True, "vision": True, "embedding": False, "thinking": True},
    {"name": SMALL, "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True},
    {"name": REMOTE, "billions": 0, "tools": True, "vision": False, "embedding": False, "thinking": False},
    {"name": REMOTE_EMBEDDER, "billions": 0, "tools": False, "vision": False, "embedding": True, "thinking": False},
]
CONFIG = {"model": MAIN, "arty_models": [MAIN, SMALL], "keep_alive": -1, "options": {"num_ctx": 24576}}


def engine_lists_everything():
    by_name = {model["name"]: model for model in INSTALLED}
    return (unittest.mock.patch.object(models, "installed", return_value=INSTALLED),
            unittest.mock.patch.object(models, "find", side_effect=by_name.get),
            unittest.mock.patch.object(models, "arty_models", return_value=[MAIN, SMALL]),
            unittest.mock.patch.object(models, "solo_models", return_value=[]),
            unittest.mock.patch.object(models, "helper_models", return_value=[]),
            unittest.mock.patch.object(models, "toolless_allowed", return_value=False),
            unittest.mock.patch.object(models, "model_labels", return_value={}))


class APickedRemoteModelIsUsed(unittest.TestCase):
    def setUp(self):
        for patch in engine_lists_everything():
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_an_app_that_picks_a_starfire_model_gets_that_model_not_the_main(self):
        self.assertEqual(brain.chosen_model(MAIN, f"arty/{REMOTE}", any_installed=False), REMOTE)

    def test_a_turn_on_a_picked_starfire_model_runs_there(self):
        config = dict(CONFIG)
        with unittest.mock.patch.object(brain.model_choice, "schedule_main_reload"):
            brain.use_requested_model(config, f"arty/{REMOTE}", any_installed=False)
        self.assertEqual(config["model"], REMOTE)

    def test_a_remote_embedding_model_is_still_refused(self):
        with self.assertRaises(model_choice.PickedModelGone):
            brain.chosen_model(MAIN, f"arty/{REMOTE_EMBEDDER}", any_installed=False)
        self.assertEqual(brain.chosen_model(MAIN, f"arty/{REMOTE_EMBEDDER}", any_installed=False, by_hand=False), MAIN)

    def test_the_small_model_runs_only_when_it_is_picked(self):
        self.assertEqual(brain.chosen_model(MAIN, f"arty/{SMALL}", any_installed=False), SMALL)
        self.assertEqual(brain.chosen_model(MAIN, "Arty", any_installed=False), MAIN)


class AppsCanListTheRemoteModels(Server):
    def listed(self, path):
        with unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)), \
                unittest.mock.patch.object(brain, "local_addon", None):
            for patch in engine_lists_everything():
                patch.start()
            try:
                status, body = self.request("GET", path)
            finally:
                unittest.mock.patch.stopall()
        self.assertEqual(status, 200)
        return [entry["id"] for entry in json.loads(body)["data"]]

    def test_the_remote_listing_adds_the_starfire_models_for_the_app_picker(self):
        self.assertEqual(self.listed("/v1/models?remote=1"), ["Arty", f"arty/{MAIN}", f"arty/{SMALL}", f"arty/{REMOTE}"])

    def test_the_plain_listing_is_unchanged(self):
        self.assertEqual(self.listed("/v1/models"), ["Arty", f"arty/{MAIN}", f"arty/{SMALL}"])


if __name__ == "__main__":
    unittest.main()
