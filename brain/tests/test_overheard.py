import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain


class OverheardTest(unittest.TestCase):
    def test_marker_variants(self):
        self.assertTrue(brain.is_overheard("(not for me)"))
        self.assertTrue(brain.is_overheard(" (Not for me). "))
        self.assertFalse(brain.is_overheard("That's not for me to say, but it's 3pm."))
        self.assertFalse(brain.is_overheard(""))


class AddressedTest(unittest.TestCase):
    CONFIG = {"model": "qwen3:14b"}

    def reply(self, content):
        return unittest.mock.patch.object(brain, "http_json", return_value={"message": {"content": content}})

    def test_overheard_and_addressed(self):
        with self.reply('{"for_arty": false}') as call:
            self.assertFalse(brain.addressed_to_arty(self.CONFIG, "That's my point.", []))
        prompt = call.call_args.kwargs["body"]["messages"][0]["content"]
        self.assertIn("What Arty last said: (nothing yet)", prompt)
        with self.reply('{"for_arty": true}'):
            self.assertTrue(brain.addressed_to_arty(self.CONFIG, "Yes please", [{"role": "assistant", "content": "Shall I?"}]))

    def test_unsure_or_failed_checks_answer(self):
        with self.reply("not json"):
            self.assertTrue(brain.addressed_to_arty(self.CONFIG, "what time is it", []))
        with unittest.mock.patch.object(brain, "http_json", side_effect=OSError("ollama down")):
            self.assertTrue(brain.addressed_to_arty(self.CONFIG, "what time is it", []))


if __name__ == "__main__":
    unittest.main()
