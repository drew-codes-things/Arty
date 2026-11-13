import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BRAIN_FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_FOLDER))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-empty-reply-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402


def step(name, result):
    return [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_name": name, "content": result}]


class EmptyReplyTest(unittest.TestCase):
    def test_after_steps_on_their_computer_the_reply_says_how_far_it_got(self):
        conversation = [{"role": "user", "content": "play the first lofi video on youtube"},
                        *step("chrome_open", "Opened it."), *step("chrome_click", "Clicked it."), *step("chrome_click", "Clicked it.")]
        reply = brain.empty_reply(conversation)
        self.assertNotIn("train of thought", reply)
        self.assertIn("3 steps on your computer", reply)

    def test_with_no_steps_taken_it_asks_again(self):
        reply = brain.empty_reply([{"role": "user", "content": "hello"}])
        self.assertEqual(reply, "Sorry, I lost my train of thought there. Could you ask that again?")

    def test_steps_from_an_earlier_message_do_not_count(self):
        conversation = [{"role": "user", "content": "open youtube"}, *step("chrome_open", "Opened it."),
                        {"role": "assistant", "content": "Done."}, {"role": "user", "content": "thanks"}]
        self.assertIn("ask that again", brain.empty_reply(conversation))

    def test_the_claim_check_leaves_the_brains_own_words_alone(self):
        conversation = [{"role": "user", "content": "play the first lofi video on youtube"},
                        *step("chrome_click", "Clicked it."), *step("chrome_click", "Couldn't: item 59 is not on the page any more.")]
        reply = brain.empty_reply(conversation)
        claim = [{"text": reply, "correction": "I did not lose track or perform any actions on your computer."}]
        with unittest.mock.patch.object(brain, "false_claims", return_value=claim) as checked:
            kept = brain.be_honest(reply, ["chrome_click", "chrome_click"], [("chrome_click", "Couldn't: item 59")])
        self.assertEqual(kept, reply)
        checked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
