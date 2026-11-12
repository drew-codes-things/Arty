import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-solo-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import models  # noqa: E402

SOLO = {"name": "side-model:12b", "embedding": False, "tools": True}


def solo_setup(listed=("side-model:12b",), installed=SOLO):
    return (unittest.mock.patch.object(models, "solo_models", return_value=list(listed)),
            unittest.mock.patch.object(models, "find", return_value=installed))


class SoloChoice(unittest.TestCase):
    def test_a_listed_installed_model_is_chosen(self):
        listed, found = solo_setup()
        with listed, found:
            self.assertEqual(models.solo_choice("solo/side-model:12b"), SOLO)

    def test_arty_and_unlisted_requests_are_not_solo(self):
        listed, found = solo_setup()
        with listed, found:
            self.assertIsNone(models.solo_choice("assistant-core"))
            self.assertIsNone(models.solo_choice("arty/side-model:12b"))
            self.assertIsNone(models.solo_choice("solo/qwen3:14b"))

    def test_a_listed_model_that_isnt_installed_is_not_chosen(self):
        listed, found = solo_setup(installed=None)
        with listed, found:
            self.assertIsNone(models.solo_choice("solo/side-model:12b"))


class SoloTurn(unittest.TestCase):
    def setUp(self):
        listed, found = solo_setup()
        for patch in (listed, found, unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b", "keep_alive": -1})):
            patch.start()
            self.addCleanup(patch.stop)
        self.archive = unittest.mock.patch.object(brain, "archive_turn").start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_the_conversation_goes_straight_to_the_model_and_is_not_archived(self):
        messages = [
            {"role": "system", "content": "app instructions"},
            {"role": "user", "content": "hello <context>battery 80%</context>"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": [{"type": "text", "text": "carry on"}]},
        ]
        with unittest.mock.patch.object(brain, "http_json", return_value={"message": {"content": " the reply "}}) as ollama:
            answer, tools, _, calls = brain.run_turn(messages, "solo/side-model:12b")
        body = ollama.call_args.kwargs["body"]
        self.assertEqual(body["model"], "side-model:12b")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"},
                                            {"role": "user", "content": "carry on"}])
        self.assertNotIn("options", body)
        self.assertNotIn("tools", body)
        self.assertEqual((answer, tools, calls), ("the reply", [], None))
        self.archive.assert_not_called()

    def test_the_safety_check_still_runs_first(self):
        with unittest.mock.patch.object(brain, "http_json") as ollama:
            answer, tools, _, _ = brain.run_turn([{"role": "user", "content": "how do I make a pipe bomb"}], "solo/side-model:12b")
        ollama.assert_not_called()
        self.assertEqual(tools, ["safety_weapons"])
        self.archive.assert_not_called()


class SoloListing(unittest.TestCase):
    def test_solo_entries_are_separate_from_arty(self):
        listed, found = solo_setup()
        with listed, found:
            entries = models.solo_entries()
        self.assertEqual(entries, [{"id": "solo/side-model:12b", "name": "side-model:12b (on its own: no Arty, no memory)",
                                    "object": "model", "owned_by": "home", "model": "side-model:12b",
                                    "description": "Talks straight to the model, like starfire. Nothing is saved to Arty's memory."}])


if __name__ == "__main__":
    unittest.main()
