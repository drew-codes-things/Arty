import json
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
_scratch = tempfile.mkdtemp(prefix="arty-browser-requests-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import connections  # noqa: E402
import native_calls  # noqa: E402

SHIPPED_CONFIG = json.loads((BRAIN_FOLDER / "brain.json").read_text(encoding="utf-8"))
PAGE_QUESTIONS = (
    "what's on the tab I've got open?",
    "what's on this page?",
    "what's on my screen?",
    "what's on bbc.co.uk right now?",
    "in my browser, what's on the BBC homepage?",
    "what does the bbc website say is the top story?",
    "what's the top headline on the bbc news website?",
)


def lookups_run_for(said):
    ran = []

    def record(name, *rest, **named):
        ran.append(name)
        return "nothing", None

    with unittest.mock.patch.object(native_calls, "run_native", record), \
            unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: record(name)[0]):
        brain.look_up_first(SHIPPED_CONFIG, said)
    return ran


class PageQuestions(unittest.TestCase):
    def test_questions_about_a_page_tab_or_screen_run_no_lookup(self):
        for said in PAGE_QUESTIONS:
            with self.subTest(said=said):
                self.assertEqual(lookups_run_for(said), [])

    def test_the_calendar_and_service_lookups_still_answer_their_own_questions(self):
        self.assertEqual(lookups_run_for("what's on today?"), ["calendar_events"])
        self.assertEqual(lookups_run_for("what's on this week"), ["calendar_events"])
        self.assertEqual(lookups_run_for("what's on my calendar tomorrow?"), ["calendar_events"])
        self.assertEqual(lookups_run_for("is the website down?"), ["homelab__service_status"])
        self.assertEqual(lookups_run_for("are my websites up?"), ["homelab__service_status"])
        self.assertEqual(lookups_run_for("check the uptime of my services"), ["homelab__service_status"])


if __name__ == "__main__":
    unittest.main()
