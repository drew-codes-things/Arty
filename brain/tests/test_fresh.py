import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-fresh-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import steering  # noqa: E402
import fresh  # noqa: E402


def chat(*replies, said="hello"):
    conversation = []
    for reply in replies:
        conversation.append({"role": "user", "content": said})
        conversation.append({"role": "assistant", "content": reply})
    return conversation


LOOPING = chat(
    "Oh nice one, that sounds like a solid plan for the homelab tonight lol.",
    "Oh nice one, the backup finished and the homelab looks happy lol.",
    "Oh nice one, I checked the homelab and the disks are fine lol.",
)
VARIED = chat(
    "The backup finished at four.",
    "Your calendar is clear on Friday.",
    "It'll rain in Newport after lunch.",
)


class RepeatedOpeners(unittest.TestCase):
    def test_an_opener_used_again_and_again_is_named(self):
        self.assertIn("oh nice one", fresh.openers(fresh.recent_replies(LOOPING)))

    def test_one_line_replies_have_no_opener(self):
        self.assertEqual(fresh.openers(["Done.", "Yes."]), [])


class RepeatedPhrases(unittest.TestCase):
    def test_a_phrase_in_two_replies_is_named_once(self):
        replies = ["I set it up and the backup finished without errors today.",
                   "Good news, the backup finished without errors again."]
        self.assertEqual(fresh.repeated_phrases(replies), ["backup finished without errors"])

    def test_grammar_alone_is_not_a_habit(self):
        replies = ["I think that is the one you want.", "Yes, I think that is right."]
        self.assertEqual(fresh.repeated_phrases(replies), [])


class RepeatedTopics(unittest.TestCase):
    def test_a_subject_in_most_replies_is_named(self):
        self.assertIn("homelab", fresh.topics(fresh.recent_replies(LOOPING)))

    def test_a_subject_the_user_raised_is_fair_game(self):
        self.assertNotIn("homelab", fresh.topics(fresh.recent_replies(LOOPING), "how is the homelab"))


class Fillers(unittest.TestCase):
    def test_a_filler_in_most_replies_is_a_tic(self):
        self.assertIn("lol", fresh.fillers(fresh.recent_replies(LOOPING)))

    def test_a_filler_used_once_is_not(self):
        self.assertEqual(fresh.fillers(["ok lol", "Sure.", "Done.", "Yes."]), [])


class Note(unittest.TestCase):
    def test_a_looping_conversation_gets_a_note(self):
        note = fresh.repetition_note(LOOPING, "what now")
        self.assertIn("oh nice one", note)
        self.assertIn("lol", note)
        self.assertIn("the user didn't write this", note)

    def test_a_varied_conversation_gets_none(self):
        self.assertEqual(fresh.repetition_note(VARIED, "what now"), "")

    def test_a_new_conversation_gets_none(self):
        self.assertEqual(fresh.repetition_note(chat("Hi."), "hello"), "")

    def test_replies_with_parts_are_read(self):
        parts = [{"role": "assistant", "content": [{"type": "text", "text": "Oh nice one, that sounds great lol."}]}] * 3
        self.assertIn("oh nice one", fresh.repetition_note(parts, ""))

    def test_only_the_last_eight_replies_count(self):
        old_habit = chat(*["Oh nice one, the homelab is fine lol."] * 3)
        eight_different = chat("The backup finished at four.", "Your calendar is clear on Friday.",
                               "It'll rain in Newport after lunch.", "Bins go out on Thursday.",
                               "The printer needs paper.", "Train leaves at nine.",
                               "Your parcel arrives tomorrow.", "Minecraft server is up.")
        self.assertEqual(fresh.repetition_note(old_habit + eight_different, ""), "")


class InTheChat(unittest.TestCase):
    def history_for(self, background):
        history = [{"role": "system", "content": "persona"}] + LOOPING + [{"role": "user", "content": "what now"}]
        steering.steer_away_from_repeats(history, history[1:], "what now", background)
        return history

    def test_a_chat_gets_the_note_on_the_newest_message(self):
        history = self.history_for(background=False)
        self.assertIn("getting samey", history[-1]["content"])
        self.assertEqual(history[0]["content"], "persona")

    def test_a_background_task_does_not(self):
        self.assertEqual(self.history_for(background=True)[-1]["content"], "what now")


if __name__ == "__main__":
    unittest.main()
