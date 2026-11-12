import json
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import models  # noqa: E402

EXTRA = {"id": "extra/one", "name": "One", "object": "model", "owned_by": "home"}
SIDE = {"name": "side:9b", "embedding": False, "tools": True, "vision": False}
MAIN = {"name": "qwen3:14b", "embedding": False, "tools": True, "vision": False}


class FakeAddon:
    def __init__(self):
        self.attached, self.warmed, self.turns = [], [], []

    def attach(self, host):
        self.attached.append(host)

    def turn(self, messages, requested_model, client_tools=None, skip_groups=(), on_text=None):
        self.turns.append(requested_model)
        return ("handled", [], {}, None) if requested_model == EXTRA["id"] else None

    def listed_models(self):
        return [EXTRA]

    def warm(self, model):
        self.warmed.append(model)
        return model == EXTRA["id"]


class Loading(unittest.TestCase):
    def test_no_addon_installed_means_none(self):
        with unittest.mock.patch.object(brain, "LOCAL_ADDON", "no_such_addon_here"):
            self.assertIsNone(brain.load_local_addon())

    def test_a_broken_addon_is_not_hidden(self):
        with unittest.mock.patch.object(brain.importlib, "import_module",
                                        side_effect=ModuleNotFoundError("No module named 'gone'", name="gone")):
            with self.assertRaises(ModuleNotFoundError):
                brain.load_local_addon()

    def test_a_folder_without_its_code_counts_as_no_addon(self):
        with unittest.mock.patch.object(brain.importlib, "import_module", return_value=types.ModuleType(brain.LOCAL_ADDON)):
            self.assertIsNone(brain.load_local_addon())

    def test_an_installed_addon_is_attached_to_the_brain(self):
        fake = FakeAddon()
        with unittest.mock.patch.object(brain.importlib, "import_module", return_value=fake):
            self.assertIs(brain.load_local_addon(), fake)
        self.assertEqual(fake.attached, [brain])


class Turns(unittest.TestCase):
    def test_the_addon_answers_only_the_turns_it_takes(self):
        fake = FakeAddon()
        with unittest.mock.patch.object(brain, "local_addon", fake):
            self.assertEqual(brain.run_turn([{"role": "user", "content": "hi"}], EXTRA["id"]), ("handled", [], {}, None))
            with unittest.mock.patch.object(brain, "load_config", side_effect=RuntimeError("normal path")):
                with self.assertRaisesRegex(RuntimeError, "normal path"):
                    brain.run_turn([{"role": "user", "content": "hi"}], "Arty")
        self.assertEqual(fake.turns, [EXTRA["id"], "Arty"])


class Routes(Server):
    def listed_ids(self):
        with unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b"}), \
                unittest.mock.patch.object(models, "find", return_value=None), \
                unittest.mock.patch.object(models, "chat_models", return_value=[]), \
                unittest.mock.patch.object(models, "solo_entries", return_value=[]):
            status, body = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        return [entry["id"] for entry in json.loads(body)["data"]]

    def test_without_an_addon_nothing_extra_is_listed_or_warmed(self):
        with unittest.mock.patch.object(brain, "local_addon", None):
            self.assertEqual(self.listed_ids(), ["Arty"])
            status, body = self.request("POST", "/v1/warm", {"model": EXTRA["id"]})
        self.assertEqual((status, json.loads(body)), (200, {"warming": False}))

    def test_an_addon_adds_its_models_and_warms_its_own(self):
        fake = FakeAddon()
        with unittest.mock.patch.object(brain, "local_addon", fake):
            self.assertEqual(self.listed_ids(), ["Arty", EXTRA["id"]])
            status, body = self.request("POST", "/v1/warm", {"model": EXTRA["id"]})
            self.assertEqual((status, json.loads(body)), (202, {"warming": True}))
            status, body = self.request("POST", "/v1/warm", {"model": "Arty"})
            self.assertEqual((status, json.loads(body)), (200, {"warming": False}))

    def test_warming_needs_the_api_key(self):
        status, _ = self.request("POST", "/v1/warm", {"model": EXTRA["id"]}, authorised=False)
        self.assertEqual(status, 401)


class SeparateModels(unittest.TestCase):
    def test_models_kept_separate_are_left_out_of_arty_unless_arty_also_uses_them(self):
        with unittest.mock.patch.object(models, "separate_models", return_value={"side:9b"}), \
                unittest.mock.patch.object(models, "installed", return_value=[SIDE, MAIN]), \
                unittest.mock.patch.object(models, "solo_models", return_value=[]), \
                unittest.mock.patch.object(models, "toolless_allowed", return_value=False):
            with unittest.mock.patch.object(models, "arty_models", return_value=[]):
                self.assertEqual([m["name"] for m in models.chat_models()], ["qwen3:14b"])
                self.assertFalse(models.usable(SIDE))
            with unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b", "side:9b"]):
                self.assertEqual([m["name"] for m in models.chat_models()], ["side:9b", "qwen3:14b"])
                self.assertTrue(models.usable(SIDE))


if __name__ == "__main__":
    unittest.main()
