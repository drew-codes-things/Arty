"""Arty's own desktop look: chosen by the model in its own voice, applied by code, tinkered with only while the user
is away. The model and the desktop are faked. Run from brain/: python3 -m unittest discover tests"""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DESKTOP_LOOK_STATE", os.path.join(tempfile.mkdtemp(prefix="arty-look-"), "desktop-look.json"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import desktop_look  # noqa: E402

GOOD_CHOICE = "LOOK: Dusk\nMOTTO: Packets by day, pings by night\nWHY: I like how calm it is after hours."
APPLIED = '$ arty-style ...\n{"ok": true, "look": "Dusk", "wallpaper": "/home/ai/Pictures/arty/arty-1.png", "applied": []}'


def fake_desktop(output=APPLIED):
    calls = []

    def call_tool(name, arguments):
        calls.append((name, arguments))
        return output
    call_tool.calls = calls
    return call_tool


def sent_look(call_tool) -> dict:
    command = call_tool.calls[0][1]["command"]
    return json.loads(base64.b64decode(command.split()[-1]))


class DesktopLook(unittest.TestCase):
    def setUp(self):
        Path(os.environ["DESKTOP_LOOK_STATE"]).unlink(missing_ok=True)
        desktop_look._last_user_message["time"] = 0.0

    def test_recognises_drews_requests_and_nothing_else(self):
        for text in ["Go on, you can do it, its your desktop, customise it however you want",
                     "customise your desktop to your own personality", "change your wallpaper", "personalise your computer",
                     "customise the desktop however you want, it's your own"]:
            self.assertTrue(desktop_look.is_request(text), text)
        for text in ["update your computer", "can you make your computer open firefox", "change your screen resolution",
                     "what's on my desktop?", "customise my desktop", "customise the desktop app's settings"]:
            self.assertFalse(desktop_look.is_request(text), text)

    def test_the_model_chooses_and_code_applies_it(self):
        desktop = fake_desktop()
        reply = desktop_look.customise(desktop, lambda prompt: GOOD_CHOICE)
        look = sent_look(desktop)
        self.assertEqual(desktop.calls[0][0], "workspace__run_command")
        self.assertEqual((look["name"], look["accent"], look["motto"]), ("Dusk", "#f5a524", "Packets by day, pings by night"))
        self.assertIn("**Dusk**: I like how calm it is after hours.", reply)
        self.assertEqual(json.loads(Path(os.environ["DESKTOP_LOOK_STATE"]).read_text())["look"], "Dusk")

    def test_a_garbled_choice_still_gets_a_real_look(self):
        choice = desktop_look.choose(lambda prompt: "Here's how to change a wallpaper: feh --bg-scale /path/to/image.jpg", "", False)
        self.assertIn(choice["name"], desktop_look.LOOKS)
        self.assertEqual(choice["motto"], desktop_look.FALLBACK_MOTTO)
        self.assertTrue(choice["why"].startswith("I "))

    def test_asked_again_it_changes_to_a_different_look(self):
        choice = desktop_look.choose(lambda prompt: GOOD_CHOICE, "Dusk", asked_for_change=True)
        self.assertNotEqual(choice["name"], "Dusk")

    def test_a_failure_is_reported_never_claimed(self):
        reply = desktop_look.customise(fake_desktop("exit code 1\nerror: xfconf-query not found"), lambda prompt: GOOD_CHOICE)
        self.assertTrue(reply.startswith("I couldn't change my desktop just now"), reply)
        self.assertNotIn("Done", reply)

    def test_tinkering_waits_while_the_user_is_talking_and_outside_its_hours(self):
        midday = datetime(2026, 10, 25, 13, 0).timestamp()
        self.assertTrue(desktop_look.due({}, midday))
        self.assertFalse(desktop_look.due({}, datetime(2026, 10, 25, 23, 0).timestamp()))
        self.assertFalse(desktop_look.due({"tinker": False}, midday))
        desktop_look._last_user_message["time"] = midday - 60
        self.assertFalse(desktop_look.due({}, midday))

    def test_tinkering_stops_if_the_user_messages_while_it_chooses(self):
        desktop = fake_desktop()

        def slow_model(prompt):
            desktop_look.user_active()
            return GOOD_CHOICE
        desktop_look.tinker(desktop, slow_model)
        self.assertEqual(desktop.calls, [])

    def test_tinkering_applies_quietly_and_waits_its_turn_after(self):
        desktop = fake_desktop()
        desktop_look.tinker(desktop, lambda prompt: GOOD_CHOICE)
        self.assertEqual(len(desktop.calls), 1)
        self.assertFalse(desktop_look.due({}, time.time()))


    def test_the_model_designs_its_own_look(self):
        design = ("NAME: Valley Night\nBACKGROUND: #101820 #2A1B3D\nACCENT: #FFB000\nPATTERN: grid\nTHEME: Arc-Darker\n"
                  "ICONS: Papirus-Dark\nMOTTO: Quiet hills, loud packets\nWHY: I wanted home after dark.")
        desktop = fake_desktop()
        reply = desktop_look.customise(desktop, lambda prompt: design)
        look = sent_look(desktop)
        self.assertEqual((look["name"], look["background"], look["accent"], look["pattern"], look["gtk_theme"], look["icon_theme"]),
                         ("Valley Night", ["#101820", "#2a1b3d"], "#ffb000", "grid", "Arc-Darker", "Papirus-Dark"))
        self.assertIn("**Valley Night**: I wanted home after dark.", reply)

    def test_a_bad_design_falls_back_part_by_part(self):
        design = "NAME: Chaos; rm -rf\nBACKGROUND: red blue\nACCENT: #12345\nPATTERN: spirals\nTHEME: Windows-XP\nICONS: ../../etc"
        choice = desktop_look.choose(lambda prompt: design, "", False)
        self.assertEqual(choice["name"], "Chaos rm -rf")
        self.assertEqual(len(choice["background"]), 2)
        self.assertRegex(choice["accent"], r"^#[0-9a-f]{6}$")
        self.assertIn(choice["pattern"], desktop_look.PATTERNS)
        self.assertEqual((choice["gtk_theme"], choice["icon_theme"]), (desktop_look.GTK_THEME, desktop_look.ICON_THEME))

    def test_any_time_permission_is_granted_and_taken_back_in_chat(self):
        late = datetime(2026, 10, 25, 23, 0).timestamp()
        self.assertFalse(desktop_look.due({}, late))
        for text in ["you can activate yourself at any time", "You're allowed... no wait, you can wake yourself up whenever you want",
                     "you have my permission to act on your own any time"]:
            self.assertEqual(desktop_look.permission_reply(text), desktop_look.GRANTED, text)
        self.assertTrue(desktop_look.due({}, late))
        self.assertIn("between 09:00 and 22:00", desktop_look.permission_reply("only activate yourself during the day"))
        self.assertFalse(desktop_look.due({}, late))
        self.assertIsNone(desktop_look.permission_reply("you can start the backup any time"))
        self.assertIsNone(desktop_look.permission_reply("you can run the tests whenever"))
        self.assertIsNotNone(desktop_look.permission_reply("stop waking yourself up at night"))

    def test_the_permission_survives_a_desktop_change(self):
        desktop_look.permission_reply("you can activate yourself any time")
        desktop_look.customise(fake_desktop(), lambda prompt: GOOD_CHOICE)
        self.assertTrue(json.loads(Path(os.environ["DESKTOP_LOOK_STATE"]).read_text())["anytime"])


if __name__ == "__main__":
    unittest.main()
