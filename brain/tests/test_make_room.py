import json
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models  # noqa: E402

GB = 1024 ** 3
MAIN = {"name": "qwen3:14b", "size_vram": int(9.7 * GB)}
SMALL = {"name": "tiny:1b", "size_vram": int(0.8 * GB)}
INSTALLED = [{"name": "qwen3:14b", "bytes": int(9.3 * GB)}, {"name": "qwen2.5vl:7b", "bytes": 6 * GB},
             {"name": "tiny:1b", "bytes": int(0.8 * GB)}]
BUDGET = int(11.2 * GB)
MB = 1024 ** 2
REPO = Path(__file__).resolve().parents[2]


class FakeOllama:
    def __init__(self, loaded, vanish_after_polls=0):
        self.loaded = [dict(m) for m in loaded]
        self.unloaded = []
        self.polls_until_gone = vanish_after_polls

    def __call__(self, path, body=None, timeout=30):
        if path == "/api/ps":
            if self.unloaded and self.polls_until_gone <= 0:
                self.loaded = [m for m in self.loaded if m["name"] not in self.unloaded]
            self.polls_until_gone -= 1
            return {"models": self.loaded}
        if path == "/api/generate" and body.get("keep_alive") == 0:
            self.unloaded.append(body["model"])
            return {}
        raise AssertionError(f"unexpected call {path} {body}")


class MakeRoom(unittest.TestCase):
    def make_room(self, fake, name):
        with unittest.mock.patch.object(models, "_ollama", fake), \
                unittest.mock.patch.object(models, "installed", return_value=INSTALLED), \
                unittest.mock.patch.object(models, "UNLOAD_POLL_SECONDS", 0):
            return models.make_room(name, BUDGET)

    def test_a_model_that_does_not_fit_beside_the_main_one_gets_it_unloaded_first(self):
        fake = FakeOllama([MAIN], vanish_after_polls=2)
        self.assertEqual(self.make_room(fake, "qwen2.5vl:7b"), ["qwen3:14b"])
        self.assertEqual(fake.unloaded, ["qwen3:14b"])
        self.assertEqual(fake.loaded, [])

    def test_a_model_that_fits_beside_what_is_loaded_unloads_nothing(self):
        fake = FakeOllama([SMALL])
        self.assertEqual(self.make_room(fake, "qwen2.5vl:7b"), [])
        self.assertEqual(fake.unloaded, [])

    def test_a_model_already_loaded_needs_no_room(self):
        fake = FakeOllama([MAIN])
        self.assertEqual(self.make_room(fake, "qwen3:14b"), [])
        self.assertEqual(fake.unloaded, [])

    def test_an_unknown_model_size_still_makes_room(self):
        fake = FakeOllama([MAIN])
        self.assertEqual(self.make_room(fake, "brand-new:9b"), ["qwen3:14b"])

    def test_an_unreachable_ollama_never_blocks_the_request(self):
        def down(*_, **__):
            raise urllib.error.URLError("refused")
        with self.assertLogs("brain.models", level="WARNING"):
            self.assertEqual(self.make_room(down, "qwen2.5vl:7b"), [])

    def test_a_model_that_never_goes_is_reported_not_waited_on_forever(self):
        fake = FakeOllama([MAIN], vanish_after_polls=10 ** 6)
        with unittest.mock.patch.object(models, "UNLOAD_WAIT_SECONDS", 0.01), self.assertLogs("brain.models", level="WARNING"):
            self.assertEqual(self.make_room(fake, "qwen2.5vl:7b"), ["qwen3:14b"])


class VramBudget(unittest.TestCase):
    def test_the_budget_comes_from_brain_json(self):
        self.assertEqual(models.vram_budget({"vram_budget_mb": 23000}), 23000 * MB)

    def test_without_a_setting_the_budget_is_the_12_gb_default(self):
        self.assertEqual(models.vram_budget({}), models.VRAM_BUDGET_MB * MB)

    def test_a_setting_that_is_not_a_positive_number_falls_back_to_the_default_with_a_warning(self):
        for wrong in ("lots", None, 0, -500, True, [12000], float("nan"), float("inf")):
            with self.subTest(wrong=wrong), self.assertLogs("brain.models", "WARNING"):
                self.assertEqual(models.vram_budget({"vram_budget_mb": wrong}), models.VRAM_BUDGET_MB * MB)

    def test_the_setting_is_in_brain_json_and_documented(self):
        config = json.loads((REPO / "brain" / "brain.json").read_text())
        self.assertEqual(config["vram_budget_mb"], models.VRAM_BUDGET_MB)
        self.assertTrue("`vram_budget_mb`" in (REPO / "README.md").read_text(), "README.md does not document vram_budget_mb")


if __name__ == "__main__":
    unittest.main()
