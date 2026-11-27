import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import connections  # noqa: E402
import models  # noqa: E402

BUDGET = 11500 * 1024 * 1024
LOADED = [{"name": "qwen3:14b", "size_vram": BUDGET - 1}]
REMOTE = "remote/remote-a/qwen3-14b"


class RemoteModelsTakeNoRoom(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def engine(path, body=None, timeout=30):
            self.calls.append((path, body))
            return {"models": LOADED} if path == "/api/ps" else {}
        patches = [unittest.mock.patch.object(models, "_engine", side_effect=engine),
                   unittest.mock.patch.object(models, "installed", return_value=[]),
                   unittest.mock.patch.object(models, "_wait_until_gone")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_remote_model_never_unloads_the_models_on_the_card(self):
        self.assertEqual(models.make_room(REMOTE, BUDGET), [])
        self.assertEqual(self.calls, [])

    def test_a_local_model_that_does_not_fit_still_makes_room(self):
        self.assertEqual(models.make_room("coder:9b", BUDGET), ["qwen3:14b"])
        self.assertIn(("/api/generate", {"model": "qwen3:14b", "keep_alive": 0}), self.calls)

    def test_remote_names_are_known_by_their_prefix(self):
        self.assertTrue(models.is_remote(REMOTE))
        self.assertFalse(models.is_remote("qwen3:14b"))
        self.assertFalse(models.is_remote("hf.co/owner/repo:q8_0"))


TAGS = {"models": [{"name": "qwen3:14b", "size": 9}, {"name": REMOTE, "size": 0, "remote": True, "available": False}]}
SHOWN = {"capabilities": ["completion", "tools"], "details": {}, "model_info": {}}
OFFLINE = {"name": REMOTE, "billions": 0.0, "tools": True, "vision": False, "embedding": False, "thinking": False,
           "available": False}
ONLINE = {"name": "qwen3:14b", "billions": 14.8, "tools": True, "vision": False, "embedding": False, "thinking": True,
          "available": True}


class AvailabilityFromTheEngine(unittest.TestCase):
    def test_installed_models_say_whether_they_are_available(self):
        def engine(path, body=None, timeout=30):
            return TAGS if path == "/api/tags" else SHOWN
        with unittest.mock.patch.object(models, "_engine", side_effect=engine):
            found = {model["name"]: model for model in models.installed(refresh=True)}
        self.assertFalse(found[REMOTE]["available"])
        self.assertTrue(found["qwen3:14b"]["available"])

    def test_an_offline_model_says_so_in_its_summary(self):
        self.assertTrue(models.summary(OFFLINE).startswith("offline"))
        self.assertNotIn("offline", models.summary(ONLINE))


class OfflineInThePickers(Server):
    def setUp(self):
        super().setUp()
        patches = [unittest.mock.patch.object(models, "installed", return_value=[ONLINE, OFFLINE]),
                   unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b", REMOTE]),
                   unittest.mock.patch.object(models, "toolless_allowed", return_value=False),
                   unittest.mock.patch.object(models, "solo_models", return_value=[]),
                   unittest.mock.patch.object(connections, "load_config", return_value={"model": "qwen3:14b"})]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def listed(self, path):
        status, body = self.request("GET", path)
        self.assertEqual(status, 200)
        return {entry["id"]: entry for entry in json.loads(body)["data"]}

    def test_the_apps_list_names_an_offline_model_as_offline(self):
        entry = self.listed("/v1/models")[f"arty/{REMOTE}"]
        self.assertIn("offline", entry["name"])
        self.assertFalse(entry["available"])
        self.assertTrue(self.listed("/v1/models")["arty/qwen3:14b"]["available"])

    def test_the_full_list_marks_an_offline_model(self):
        entry = self.listed("/v1/models?all=1")[f"arty/{REMOTE}"]
        self.assertFalse(entry["available"])
        self.assertTrue(entry["description"].startswith("offline"))


if __name__ == "__main__":
    unittest.main()
