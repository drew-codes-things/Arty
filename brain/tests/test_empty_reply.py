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
import connections  # noqa: E402
import honesty  # noqa: E402
from test_agent_mode import READ_FILE  # noqa: E402
from test_round_timings import WARM, TimedOllama, answer, asked, offline_turns  # noqa: E402


def step(name, result):
    return [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_name": name, "content": result}]


def nothing_written():
    return answer("", {**WARM, "eval_count": 0})


class EmptyReplyTest(unittest.TestCase):
    def setUp(self):
        offline_turns(self)

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
        with unittest.mock.patch.object(connections, "http_json", side_effect=TimedOllama(nothing_written(), nothing_written())), \
                unittest.mock.patch.object(brain, "be_honest", side_effect=lambda text, *_: text) as checked:
            reply = brain.run_turn(asked("play the first lofi video on youtube"), "Arty", client_tools=[READ_FILE])[0]
        self.assertEqual(reply, honesty.LOST_TRAIN_OF_THOUGHT)
        checked.assert_not_called()

    def test_a_model_reply_in_the_fallbacks_words_is_still_checked(self):
        written = "Sorry, I lost track before I could answer, but I have sent the email to your landlord."
        with unittest.mock.patch.object(connections, "http_json", side_effect=TimedOllama(answer(written))), \
                unittest.mock.patch.object(brain, "be_honest", side_effect=lambda text, *_: text) as checked:
            brain.run_turn(asked("email my landlord"), "Arty", client_tools=[READ_FILE])
        checked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
