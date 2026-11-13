import os
import sys
import tempfile
import unittest
from pathlib import Path

BRAIN_FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_FOLDER))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-tools-note-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import drafts  # noqa: E402

OLD_NOTE = "[Tools you used or tried on their computer for that reply: opening youtube.com in your browser; clicking item 62 in your browser]"
NEW_NOTE = ("<context>For your reply above, you used or tried these tools on their computer: reading the page "
            "(a note for you, not something to repeat)</context>")


class ToolsNoteEchoTest(unittest.TestCase):
    def test_an_echoed_tools_note_is_cut_from_the_reply(self):
        for note in (OLD_NOTE, NEW_NOTE):
            with self.subTest(note=note[:30]):
                self.assertEqual(brain.tidy_answer(f"It's playing now.\n\n{note}").strip(), "It's playing now.")
                self.assertEqual(brain.tidy_answer(f"{note}\nIt's playing now.").strip(), "It's playing now.")

    def test_words_about_tools_that_are_not_the_note_stay(self):
        reply = "The tools you used in the garden are in the shed."
        self.assertEqual(brain.tidy_answer(reply), reply)

    def test_a_streamed_reply_never_sends_the_note(self):
        sent = []
        draft = drafts.Draft(sent.append, brain.tidy_answer, lambda sentence: False)
        draft.start_round(allowed=True)
        reply = f"Opened it. It's playing now.\n\n{OLD_NOTE}"
        for piece in reply.split(" "):
            draft.feed(piece + " ", lambda text: False)
        final = draft.finish(brain.tidy_answer(reply))
        self.assertNotIn("Tools you used", "".join(sent) + final)


if __name__ == "__main__":
    unittest.main()
