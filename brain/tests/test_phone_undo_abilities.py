"""Calls and texts through the phone app, "undo that", and "what can you do?".
Run from brain/: python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_scratch = tempfile.mkdtemp(prefix="arty-undo-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import abilities  # noqa: E402
import phone  # noqa: E402
import store  # noqa: E402
import undo  # noqa: E402


class Phone(unittest.TestCase):
    def test_calls_and_texts_are_recognised(self):
        self.assertEqual(phone.from_request("call my mum"), {"kind": "call", "who": "Mum", "body": ""})
        self.assertEqual(phone.from_request("can you ring 07700 900123"), {"kind": "call", "who": "07700 900123", "body": ""})
        self.assertEqual(phone.from_request("text Sam saying i'll be late"), {"kind": "text", "who": "Sam", "body": "I'll be late"})
        self.assertEqual(phone.from_request("message Jake: running 10 mins behind"),
                         {"kind": "text", "who": "Jake", "body": "Running 10 mins behind"})
        self.assertEqual(phone.from_request("text my mum that I'm on my way"), {"kind": "text", "who": "Mum", "body": "I'm on my way"})

    def test_other_requests_are_not_calls(self):
        for text in ["call me Sam", "remind me to call mum at 5", "what's a phone call in French", "who called it the Big Apple",
                     "any messages from college?"]:
            self.assertIsNone(phone.from_request(text), text)

    def test_the_phone_app_gets_the_action_and_others_get_pointed_to_it(self):
        action = {"kind": "text", "who": "Sam", "body": "I'll be late"}
        on_phone = phone.reply(action, "phone")
        tag = on_phone.split("<phone-action>")[1].split("</phone-action>")[0]
        self.assertEqual(json.loads(tag), action)
        self.assertIn("> I'll be late", on_phone)
        self.assertNotIn("phone-action", phone.without_action(on_phone))
        elsewhere = phone.reply(action, "")
        self.assertNotIn("phone-action", elsewhere)
        self.assertIn("phone app", elsewhere)


class Undo(unittest.TestCase):
    def setUp(self):
        store.init()
        import personal
        personal.init()
        with store.write_lock, store.connect() as connection:
            for table in ("schedules", "events", "list_items", "notes", "deadlines", "cards"):
                connection.execute(f"DELETE FROM {table}")

    def test_recognises_undo_only_as_the_whole_message(self):
        for text in ["undo that", "Undo", "can you undo the last thing please", "scrap that!", "take that back"]:
            self.assertTrue(undo.is_request(text), text)
        for text in ["how do I undo a git commit", "undo my last email"]:
            self.assertFalse(undo.is_request(text), text)

    def test_removes_the_newest_thing_and_says_what(self):
        now = time.time()
        with store.write_lock, store.connect() as connection:
            connection.execute("INSERT INTO notes (text, created) VALUES (?, ?)", ("older note", now - 60))
            connection.executemany("INSERT INTO list_items (list, item, created) VALUES (?, ?, ?)",
                                   [("shopping", "eggs", now - 1.5), ("shopping", "bread", now - 1.4), ("shopping", "jam", now - 600)])
        self.assertEqual(undo.undo_last(now), 'Undone: I removed "eggs", "bread" from the shopping list.')
        self.assertEqual(undo.undo_last(now), 'Undone: I removed the note "older note".')
        self.assertEqual(undo.undo_last(now), 'Undone: I removed "jam" from the shopping list.')
        self.assertIn("nothing recent", undo.undo_last(now))

    def test_old_things_are_never_undone(self):
        with store.write_lock, store.connect() as connection:
            connection.execute("INSERT INTO notes (text, created) VALUES (?, ?)", ("last week", time.time() - 7 * 86400))
        self.assertIn("nothing recent", undo.undo_last())


class Abilities(unittest.TestCase):
    GROUPS = {"email": {"tools": ["workspace__gmail_unread"]}, "nextcloud": {"tools": ["homelab__nextcloud_*"]},
              "weather": {"tools": ["weather"], "description": "the weather"}}

    def test_lists_only_groups_whose_tools_are_installed(self):
        text = abilities.describe(self.GROUPS, {"weather", "homelab__nextcloud_list"})
        self.assertIn("Nextcloud", text)
        self.assertIn("weather", text)
        self.assertNotIn("Gmail", text)
        self.assertIn("What I can't do", text)

    def test_recognises_the_question(self):
        for text in ["what can you do?", "What are your capabilities", "help", "what else can you do"]:
            self.assertTrue(abilities.is_question(text), text)
        self.assertFalse(abilities.is_question("can you help me fix this"))


if __name__ == "__main__":
    unittest.main()
