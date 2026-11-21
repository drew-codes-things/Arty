import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-tell-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))

from test_round_timings import TimedEngine, offline_turns  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import phone  # noqa: E402
import said_to_them  # noqa: E402
import shortcuts  # noqa: E402

REPORTED = "my phone's low don't worry about it can you tell Jacob and Jaden and above beautiful"
CALL_MODE = ("The user is on a voice call with you and hears your reply read aloud. Answer in one to three short, "
             "natural spoken sentences. No lists, headings, markdown, emoji or links unless they ask for them.")


def action_in(reply: str) -> dict:
    return json.loads(reply.split("<phone-action>")[1].split("</phone-action>")[0])


def spoken(text: str, user_name: str = "") -> str | None:
    told = said_to_them.tell_request(text)
    return said_to_them.addressed(told, user_name) if told and not told["by_text"] else None


class SaidToThem(unittest.TestCase):
    def test_the_reported_request_is_said_to_both_of_them(self):
        self.assertEqual(spoken(REPORTED), "Jacob and Jaden, you're above beautiful!")

    def test_third_person_becomes_second_person(self):
        self.assertEqual(spoken("tell Jacob he's great"), "Jacob, you're great!")
        self.assertEqual(spoken("can you tell Sam and Priya they're amazing"), "Sam and Priya, you're amazing!")
        self.assertEqual(spoken("tell Priya her painting is lovely"), "Priya, your painting is lovely!")
        self.assertEqual(spoken("tell Sam that he plays guitar really well."), "Sam, you play guitar really well.")
        self.assertEqual(spoken("tell Sam, Alex and Jo they did brilliantly"), "Sam, Alex and Jo, you did brilliantly!")

    def test_say_to_and_let_know_are_the_same_request(self):
        self.assertEqual(spoken("say to Jacob he's a star"), "Jacob, you're a star!")
        self.assertEqual(spoken("can you let Jaden know that dinner is ready"), "Jaden, dinner is ready!")
        self.assertEqual(spoken("say goodnight to Sam and Priya"), "Sam and Priya, goodnight!")

    def test_the_users_own_words_are_passed_on_as_theirs(self):
        self.assertEqual(spoken("tell my mum I'll be late", "Robin"), "Mum, Robin says: \"I'll be late.\"")
        self.assertEqual(spoken("tell Jacob I love him", "Robin"), "Jacob, Robin says: \"I love you.\"")
        self.assertEqual(spoken("tell Jacob I love him"), "Jacob, here's a message for you: \"I love you.\"")

    def test_telling_arty_or_asking_a_question_is_left_alone(self):
        for text in ["tell me a joke", "can you tell me the time", "how do I tell Python to ignore warnings",
                     "can you tell Siri to set an alarm", "tell them I said hi", "can you tell if it's raining",
                     "tell Sam's story", "can you tell Monday from Tuesday", "tell my story", "what did you tell Sam",
                     "tell the difference", "can you tell I'm tired", "let me know", "let Sam in", "say that again"]:
            self.assertIsNone(said_to_them.tell_request(text), text)

    def test_asking_for_a_text_is_a_text(self):
        told = said_to_them.tell_request("tell Sam by text that I'm on my way")
        self.assertEqual((told["people"], told["body"], told["by_text"]), (["Sam"], "I'm on my way", True))
        self.assertTrue(said_to_them.tell_request("tell Jacob and Jaden on WhatsApp they're above beautiful")["by_text"])


class Turns(unittest.TestCase):
    def setUp(self):
        offline_turns(self)

    def turn(self, text, client, system=""):
        engine = TimedEngine()
        conversation = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]
        with unittest.mock.patch.object(connections, "http_json", side_effect=engine):
            reply, used, _, _ = brain.run_turn(conversation, "Arty", client=client)
        return reply, used, engine

    def test_on_a_phone_call_the_reported_request_is_said_out_loud_with_nothing_else(self):
        reply, used, engine = self.turn(REPORTED, "phone", CALL_MODE)
        self.assertEqual(reply, "Jacob and Jaden, you're above beautiful!")
        self.assertEqual(used, [])
        self.assertEqual(engine.bodies, [])

    def test_in_voice_mode_there_is_no_text_offer(self):
        reply, used, _ = self.turn("tell my mum I'll be late", "", "Speaking out loud (voice mode).")
        self.assertNotIn("text", reply.lower())
        self.assertEqual(used, [])

    def test_typed_on_a_phone_it_is_said_and_a_text_is_offered_in_one_line(self):
        reply, used, _ = self.turn("tell my mum I'll be late", "android")
        first, offer = reply.split("\n\n")
        self.assertTrue(first.startswith("Mum, "))
        self.assertIn("text Mum", offer)
        self.assertNotIn("phone-action", reply)
        self.assertNotRegex(reply, r"(?i)can't|cannot|I've sent|I'll pass")
        self.assertEqual(used, [])

    def test_on_a_computer_it_is_just_said(self):
        reply, used, _ = self.turn("tell Sam and Priya they're amazing", "")
        self.assertEqual(reply, "Sam and Priya, you're amazing!")
        self.assertEqual(used, [])

    def test_asking_for_a_text_opens_one(self):
        for text in ["tell Sam by text that I'm on my way", "text Sam I'm on my way", "can you message Sam that I'm on my way"]:
            with self.subTest(text=text):
                reply, used, _ = self.turn(text, "phone")
                self.assertEqual(used, ["phone_text"])
                self.assertEqual(action_in(reply), {"kind": "text", "who": "Sam", "body": "I'm on my way"})

    def test_a_text_to_several_people_opens_one_and_names_the_rest(self):
        reply, used, _ = self.turn("tell Jacob and Jaden by text they're above beautiful", "android")
        self.assertEqual(used, ["phone_text"])
        self.assertEqual(action_in(reply)["who"], "Jacob")
        self.assertIn("Jaden", phone.without_action(reply))


class LongMessagesStayQuick(unittest.TestCase):
    SECONDS = 0.5

    def quickly(self, check, text):
        started = time.perf_counter()
        result = check(text)
        self.assertLess(time.perf_counter() - started, self.SECONDS, text[:40])
        return result

    def test_long_runs_of_spaces_or_punctuation_are_skipped_at_once(self):
        for text in ("say" + " " * 3000 + "to" + " " * 3000, "tell Sam " + " " * 20000 + "x",
                     "say hi to Sam" + "!" * 20000 + "x", "can you tell Sam the code:\n" + " " * 8000 + "x = 1",
                     "tell Sam " + "by " * 20000 + "x"):
            self.assertIsNone(self.quickly(said_to_them.tell_request, text))

    def test_something_else_check_on_a_long_message_is_quick(self):
        self.quickly(shortcuts.asks_for_something_else, "tell Sam " + " " * 40000 + "x")

    def test_a_long_but_ordinary_request_is_still_understood(self):
        body = "I'm running late " + "and " * 60 + "I'll be there soon"
        told = said_to_them.tell_request(f"tell Sam {body}")
        self.assertEqual((told["people"], told["body"]), (["Sam"], body))

    def test_say_to_still_ends_cleanly(self):
        told = said_to_them.tell_request("say happy birthday to Sam and Ali!!  ")
        self.assertEqual((told["people"], told["body"]), (["Sam", "Ali"], "happy birthday"))
        self.assertTrue(said_to_them.tell_request("tell Sam I'm on my way by text.  ")["by_text"])


if __name__ == "__main__":
    unittest.main()
