import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-room-harness-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json"),
                  ("ROOM_NOTES_STATE", "room-notes.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402,F401
import models  # noqa: E402

MAIN = "qwen3:14b"
CHAT_NUDGE = "(It's your turn, arty. Write your next message only, or PASS.)"
GUIDELINES = "(room) You are arty, one of several AIs. Be brief."
CONTEXT = ("{guidelines}\n\nRoom topic: {topic}\nOther AIs here: June.\n"
           "Messages from people (not AIs) are marked [person].")
ENGINE_MODELS = [{"name": MAIN, "bytes": 1, "billions": 9.0, "tools": True, "vision": False, "audio": False,
                  "embedding": False, "thinking": True, "context_length": 40960, "available": True}]


def room_said(text, nudge=CHAT_NUDGE, topic="Making a game", guidelines=GUIDELINES):
    return f"{text}\n\n{CONTEXT.format(topic=topic, guidelines=guidelines)}\n\n{nudge}"


class Engine(unittest.TestCase):
    def setUp(self):
        self.loaded = {MAIN}
        patches = [unittest.mock.patch.object(models, "installed", return_value=ENGINE_MODELS),
                   unittest.mock.patch.object(models, "is_loaded", side_effect=lambda name: name in self.loaded)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
