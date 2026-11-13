import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import tool_guide  # noqa: E402
from test_review_tonight import FakeHub, FakeOllama, last_user_note, run_agent, said  # noqa: E402

READERS = ("fetch__fetch", "research__web_search", "workspace__browser_open")


class GivenLinks(unittest.TestCase):
    def note_for(self, text, names=READERS):
        ollama = FakeOllama([said("It is a placeholder domain.")] * 4)
        run_agent(ollama, [{"role": "user", "content": text}], hub=FakeHub(names))
        return last_user_note(ollama.chat_bodies[0])

    def test_a_link_to_read_is_read_with_the_fetch_tool(self):
        for text in ("Read https://example.org and tell me what that page is for",
                     "summarise this page for me: https://en.wikipedia.org/wiki/Raspberry_Pi",
                     "what does https://example.com/notes.html say?"):
            note = self.note_for(text)
            self.assertIn("read it with fetch__fetch before answering", note, text)

    def test_a_link_without_a_reading_request_gets_no_note(self):
        for text in ("open https://youtube.com on your desktop", "play https://youtu.be/abc123", "what's the weather?"):
            self.assertNotIn("read it with", self.note_for(text), text)

    def test_no_note_when_there_is_no_fetch_tool(self):
        note = self.note_for("Read https://example.org and tell me what it is", names=("research__web_search",))
        self.assertNotIn("read it with", note)

    def test_the_guide_says_fetch_reads_any_link(self):
        text = tool_guide.guide(["fetch__fetch", "research__web_search"])
        self.assertIn("one the user gives you", text)
        self.assertIn("never tell the user you can't open links", text)


if __name__ == "__main__":
    unittest.main()
