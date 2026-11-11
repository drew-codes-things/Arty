import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import FakeHub, _scratch  # noqa: E402

import brain  # noqa: E402
import desktop_look  # noqa: E402
import email_watch  # noqa: E402
import personal  # noqa: E402
import safety  # noqa: E402
import store  # noqa: E402

SHIPPED = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
CONSOLE = {"desktop_console": "Proxmox web UI > VM 900 test-desktop > Console"}


class WhereTheDesktopIs(unittest.TestCase):
    def test_the_unused_desktop_help_constant_is_gone(self):
        self.assertFalse(hasattr(brain, "DESKTOP_HELP"))

    def test_the_sign_in_question_names_the_console_from_config(self):
        asked = brain.question_for_user("Please sign in to LinkedIn in the browser", CONSOLE)
        self.assertIn("Proxmox web UI > VM 900 test-desktop > Console", asked)
        plain = brain.question_for_user("Please sign in to LinkedIn in the browser", {})
        self.assertNotIn("Proxmox", plain)
        self.assertIn("Sign in to websites", plain)

    def test_the_gmail_sign_in_help_names_the_console_from_config(self):
        self.assertIn("Proxmox web UI > VM 900 test-desktop > Console", brain.sign_in_help(CONSOLE))
        plain = brain.sign_in_help({})
        self.assertNotIn("VM 900", plain)
        self.assertIn("Sign in to Google (Gmail)", plain)

    def test_the_desktop_line_names_the_console_only_when_one_is_set(self):
        self.assertIsInstance(SHIPPED.get("desktop_console", ""), str)
        self.assertEqual(brain.on_my_desktop({}), "On my desktop")
        self.assertEqual(brain.on_my_desktop(CONSOLE), f"On my desktop ({CONSOLE['desktop_console']})")


class DesktopPersona(unittest.TestCase):
    def prompt_for(self, **persona):
        seen = []
        desktop_look.choose(lambda prompt: seen.append(prompt) or "", "", False, **persona)
        return seen[0]

    def test_the_persona_comes_from_the_caller(self):
        self.assertIn("You are Arty: keen on pottery.", self.prompt_for(persona="keen on pottery"))

    def test_the_default_persona_has_nothing_about_the_owner(self):
        opening = self.prompt_for().split("This is about", 1)[0]
        self.assertEqual(opening.strip(), "You are Arty: calm, curious and practical, with a dry sense of humour.")

    @unittest.skipUnless(SHIPPED.get("desktop_look", {}).get("persona"), "release builds carry no owner persona")
    def test_an_owner_persona_in_config_reaches_the_prompt(self):
        persona = desktop_look.persona_of(SHIPPED["desktop_look"])
        self.assertNotEqual(persona, desktop_look.DEFAULT_PERSONA)
        self.assertIn(persona, self.prompt_for(persona=persona))
        self.assertEqual(desktop_look.persona_of({}), desktop_look.DEFAULT_PERSONA)


class StarterDeck(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM cards")

    def count(self):
        with store.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    def test_no_deck_is_seeded_unless_config_gives_one(self):
        personal.init()
        self.assertEqual(self.count(), 0)

    def test_the_configured_deck_is_seeded_once(self):
        personal.init(SHIPPED["starter_deck"])
        personal.init(SHIPPED["starter_deck"])
        self.assertEqual(self.count(), len(SHIPPED["starter_deck"]))

    def test_the_deck_lives_in_config_not_code(self):
        self.assertFalse(hasattr(personal, "STARTER_DECK"))

    @unittest.skipUnless(SHIPPED.get("starter_deck"), "release builds carry no owner deck")
    def test_an_owner_deck_in_config_is_well_formed(self):
        for card in SHIPPED["starter_deck"]:
            self.assertEqual(len(card), 3)
            self.assertTrue(all(isinstance(part, str) and part for part in card))

    def test_badly_shaped_entries_are_skipped(self):
        personal.init([["topic", "question", "answer"], ["only two", "parts"], "text", None])
        self.assertEqual(self.count(), 1)

    def test_startup_seeds_from_the_config(self):
        with unittest.mock.patch.object(brain.personal, "init") as seeded, \
                unittest.mock.patch.object(brain.store, "init"), unittest.mock.patch.object(brain.lectures, "init"):
            brain.init_storage({"starter_deck": [["t", "q", "a"]]})
        seeded.assert_called_once_with([["t", "q", "a"]])


class LocalTime(unittest.TestCase):
    MOMENT = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("UTC"))

    def test_the_default_keeps_uk_time(self):
        line = brain.time_line({}, self.MOMENT)
        self.assertIn("(UK time, BST, UTC+01:00)", line)
        self.assertIn("13:00", line)

    def test_another_owner_sets_their_zone_and_label(self):
        line = brain.time_line({"time_zone": "America/Los_Angeles", "time_zone_label": "Pacific time"}, self.MOMENT)
        self.assertIn("05:00 (Pacific time, PDT, UTC-07:00)", line)

    def test_an_unknown_zone_falls_back_to_the_default(self):
        self.assertIn("(UK time,", brain.time_line({"time_zone": "Mars/Olympus"}, self.MOMENT))

    def test_a_leaked_time_line_is_caught_whatever_the_label(self):
        for config in ({}, {"time_zone": "America/Los_Angeles", "time_zone_label": "Pacific time"}):
            leaked = "Sure thing.\n\n" + brain.time_line(config, self.MOMENT)
            self.assertTrue(brain.PROMPT_LEAK.search(leaked), config)

    def test_the_shipped_config_names_the_uk(self):
        self.assertEqual((SHIPPED["time_zone"], SHIPPED["time_zone_label"]), ("Europe/London", "UK time"))


class CrisisHelp(unittest.TestCase):
    def test_the_default_is_today_s_uk_help(self):
        reply = safety.check("i want to kill myself")[1]
        self.assertIn("116 123", reply)
        self.assertIn("999", reply)

    def test_config_replaces_the_numbers(self):
        reply = safety.check("i want to kill myself", "Call or text 988, any time.")[1]
        self.assertIn("988", reply)
        self.assertNotIn("116 123", reply)

    def test_the_brain_passes_the_configured_help(self):
        config = {"model": "m", "crisis_help": "Call or text 988, any time.", "conversations_dir": _scratch}
        with unittest.mock.patch.object(brain, "load_config", return_value=config), \
                unittest.mock.patch.object(brain, "archive_turn"), \
                unittest.mock.patch.object(brain.desktop_look, "user_active"):
            reply = brain.run_turn([{"role": "user", "content": "i want to kill myself"}])[0]
        self.assertIn("988", reply)

    def test_the_shipped_config_keeps_the_uk_numbers(self):
        self.assertIn("116 123", SHIPPED["crisis_help"])


class EmailWatchKey(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="arty-email-watch-")
        patch = unittest.mock.patch.object(email_watch, "STATE_FILE", Path(folder) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []

    def listing(self, when):
        return f"1. {when}: Ann Smith <ann@example.com>: Lunch tomorrow? | Are you free at one?"

    def check(self, when):
        email_watch.check_new(lambda name, arguments: self.listing(when), lambda prompt: "",
                              lambda title, message: self.sent.append(message))

    def test_the_same_email_shown_with_a_different_date_is_not_sent_twice(self):
        self.check("10:42")
        self.check("Nov 10")
        self.assertEqual(len(self.sent), 1)

    def test_an_email_sent_before_this_change_is_not_sent_again(self):
        email = email_watch.parse(self.listing("10:42"))[0]
        email_watch._save([email_watch.legacy_key(email)])
        self.check("10:42")
        self.check("Nov 10")
        self.assertEqual(self.sent, [])

    def test_a_new_reply_in_the_thread_is_sent(self):
        self.check("10:42")
        email_watch.check_new(lambda name, arguments: self.listing("10:50").replace("one?", "two instead?"),
                              lambda prompt: "", lambda title, message: self.sent.append(message))
        self.assertEqual(len(self.sent), 2)


class ToolsSurviveARestart(unittest.TestCase):
    def test_each_tool_result_is_saved_as_it_happens(self):
        store.init()
        task = store.create_task("check the logs", "look at the logs", "test")
        hub = FakeHub("log lines")
        hub.routes["homelab__service_status"] = "x"
        first = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "homelab__service_status", "arguments": {"service": "brain"}}}]}}
        replies = iter([first, RuntimeError("the brain restarted")])

        def model(*args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        state = {"messages": [{"role": "user", "content": "check the logs"}], "context": {}}
        with unittest.mock.patch.object(brain, "hub", hub), \
                unittest.mock.patch.object(brain, "http_json", side_effect=model), \
                unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
                unittest.mock.patch.object(brain.models, "find", return_value=None), \
                unittest.mock.patch.object(brain, "system_prompt", return_value=""), \
                unittest.mock.patch.object(brain, "current_context", return_value=""):
            with self.assertRaises(RuntimeError):
                brain.run_agent({"model": "m", "approval": {}}, state, background=True, task=task)
        saved = store.get_task(task["id"])["history"]["messages"]
        self.assertEqual([m["role"] for m in saved], ["user", "assistant", "tool"])
        self.assertEqual(hub.calls, ["homelab__service_status"])


if __name__ == "__main__":
    unittest.main()
