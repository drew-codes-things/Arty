import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import test_review_brain  # noqa: E402,F401
import attachments  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import models  # noqa: E402

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"
SEEING_PICK = "seer:9b"
HELPER = "looker:7b"
FOUND = {
    MAIN: {"name": MAIN, "tools": True, "vision": True, "embedding": False},
    SMALL: {"name": SMALL, "tools": True, "vision": False, "embedding": False},
    SEEING_PICK: {"name": SEEING_PICK, "tools": True, "vision": True, "embedding": False},
    HELPER: {"name": HELPER, "tools": False, "vision": True, "embedding": False},
}
CONFIG = {"model": MAIN, "one_model": True, "vision_model": MAIN, "keep_alive": -1, "options": {"num_predict": 2048}}


class PicturesAreDescribedByTheRightModel(unittest.TestCase):
    def setUp(self):
        for patch in (unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)),
                      unittest.mock.patch.object(models, "find", side_effect=FOUND.get),
                      unittest.mock.patch.object(models, "helper_models", return_value=[HELPER]),
                      unittest.mock.patch.object(models, "installed", return_value=list(FOUND.values()))):
            patch.start()
            self.addCleanup(patch.stop)

    def looker_for(self, turn_model):
        with attachments.describing_for_the_turn():
            attachments.turn_runs_on(turn_model)
            return attachments.vision_model()["name"]

    def test_a_turn_on_the_main_has_the_main_describe_its_pictures(self):
        self.assertEqual(self.looker_for(MAIN), MAIN)

    def test_a_picked_model_that_cannot_see_gets_the_picture_helper_not_the_main(self):
        self.assertEqual(self.looker_for(SMALL), HELPER)

    def test_a_picked_model_that_can_see_describes_its_own_pictures(self):
        self.assertEqual(self.looker_for(SEEING_PICK), SEEING_PICK)

    def test_outside_a_turn_the_main_describes_pictures(self):
        self.assertEqual(attachments.vision_model()["name"], MAIN)

    def test_the_turn_model_is_forgotten_when_the_turn_ends(self):
        self.looker_for(SMALL)
        self.assertEqual(attachments.vision_model()["name"], MAIN)

    def test_a_picked_blind_model_without_a_helper_falls_back_to_the_usual_looker(self):
        with unittest.mock.patch.object(models, "helper_models", return_value=[]):
            self.assertEqual(self.looker_for(SMALL), MAIN)


class TheTurnRecordsItsModel(unittest.TestCase):
    def test_a_turn_on_the_small_model_describes_an_attachment_with_the_helper(self):
        seen = []

        def turn_body(messages, requested_model, *rest, **named):
            attachments.turn_runs_on(SMALL)
            seen.append(attachments.vision_model()["name"])
            return "ok", [], {}, None

        with unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)), \
                unittest.mock.patch.object(models, "find", side_effect=FOUND.get), \
                unittest.mock.patch.object(models, "helper_models", return_value=[HELPER]), \
                unittest.mock.patch.object(brain, "turn_answer", side_effect=turn_body):
            brain.run_turn([{"role": "user", "content": "what is this?"}], f"arty/{SMALL}")
            after = attachments.vision_model()["name"]
        self.assertEqual((seen, after), ([HELPER], MAIN))


if __name__ == "__main__":
    unittest.main()
