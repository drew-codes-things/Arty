import os
import re
import sys
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import greetings  # noqa: E402
import shortcuts  # noqa: E402
from reply_rules import OFFER_SENTENCE  # noqa: E402

EMOJI_START = 0x1F300
BARE = ("hi arty", "hello.", "hey", "Hey Arty!", "good morning arty", "evening!", "hiya 👋", "morning", "hello there")
NOT_BARE = ("hi arty, what's the weather?", "hello can you help me with python", "goodnight", "say hello to my mum",
            "hey what time is it", "good morning! remind me to call the bank at 10", "", "alright", "alright arty?", "yo")
ASKED = [{"role": "user", "content": "remind me to take the bins out"},
         {"role": "assistant", "content": "Sure. Do you want it in the morning or the evening?"},
         {"role": "user", "content": "evening"}]


def every_reply(hour):
    return [greetings.reply(datetime(2026, 11, 13, hour, 5), choose=lambda options, index=index: options[index])
            for index in range(len(greetings.BY_TIME_OF_DAY[greetings.time_of_day(hour)]))]


class Greetings(unittest.TestCase):
    def test_bare_greetings_are_recognised(self):
        for text in BARE:
            with self.subTest(text=text):
                self.assertTrue(greetings.is_bare_greeting(text))

    def test_greetings_with_a_request_or_farewells_are_left_to_the_model(self):
        for text in NOT_BARE:
            with self.subTest(text=text):
                self.assertFalse(greetings.is_bare_greeting(text))

    def test_the_reply_fits_the_time_of_day(self):
        self.assertTrue(all(reply.startswith("Morning") for reply in every_reply(8)))
        self.assertTrue(all("fternoon" in reply for reply in every_reply(14)))
        self.assertTrue(all("vening" in reply for reply in every_reply(20)))
        self.assertTrue(all("late" in reply or "midnight" in reply or "morning now" in reply for reply in every_reply(1)))

    def test_every_reply_is_warm_short_capitalised_and_in_arty_s_voice(self):
        for hour in range(24):
            for reply in every_reply(hour):
                with self.subTest(hour=hour, reply=reply):
                    self.assertTrue(reply[0].isupper())
                    self.assertLessEqual(len(reply), 90)
                    self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", reply)), 3)
                    self.assertNotRegex(reply, r"(?i)\bdrew\b|assist|how can i help")
                    self.assertTrue(all(ord(char) < EMOJI_START for char in reply))
                    self.assertIsNone(OFFER_SENTENCE.search(reply))


class GreetingsInChat(unittest.TestCase):
    def test_a_bare_greeting_is_answered_by_code_without_the_model(self):
        every_greeting = {reply for options in greetings.BY_TIME_OF_DAY.values() for reply in options}
        with unittest.mock.patch.object(shortcuts.connections, "http_json", side_effect=AssertionError("asked the model")):
            for text in ("hi arty", "hello.", "good evening arty"):
                with self.subTest(text=text):
                    reply, tool = shortcuts.quick_action(text)
                    self.assertIn(reply, every_greeting)
                    self.assertEqual(tool, "greeting")

    def test_a_one_word_answer_to_artys_question_is_not_a_greeting(self):
        done = shortcuts.quick_action("evening", after_a_question=True)
        self.assertTrue(done is None or done[1] != "greeting")

    def test_arty_asked_a_question_when_its_last_reply_ends_on_one(self):
        self.assertTrue(greetings.answers_a_question(ASKED))
        self.assertTrue(greetings.answers_a_question([{"role": "assistant", "content": [{"type": "text", "text": "Which one?\n"}]},
                                                      {"role": "user", "content": "morning"}]))

    def test_a_new_chat_or_a_statement_before_is_not_a_question(self):
        self.assertFalse(greetings.answers_a_question([{"role": "user", "content": "hi"}]))
        self.assertFalse(greetings.answers_a_question([{"role": "assistant", "content": "Done. The bins reminder is set."},
                                                       {"role": "user", "content": "hello"}]))
        self.assertFalse(greetings.answers_a_question([]))

    def test_the_chat_turn_tells_quick_actions_that_arty_just_asked(self):
        config = {"user_name": "Sam", "model": "main", "system_prompt": "", "persona": ""}
        with unittest.mock.patch.object(brain.connections, "load_config", return_value=config), \
                unittest.mock.patch.object(brain, "archive_turn"), \
                unittest.mock.patch.object(brain, "quick_action", return_value=("Set for 6 pm.", "schedule_task")) as quick:
            brain.run_turn(ASKED)
        quick.assert_called_once_with("evening", after_a_question=True)

    def test_a_greeting_with_a_request_is_not_a_quick_greeting(self):
        done = shortcuts.quick_action("hi arty, can you explain what a VLAN is?")
        self.assertTrue(done is None or done[1] != "greeting")


if __name__ == "__main__":
    unittest.main()
