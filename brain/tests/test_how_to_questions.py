import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeOllama, last_user_note, run_agent, said  # noqa: E402

DESKTOP = ("workspace__run_command", "workspace__read_terminal", "workspace__list_files", "workspace__read_file",
           "fetch__fetch", "research__web_search")
EXPLAIN = "explain how"


class HowToQuestions(unittest.TestCase):
    def sent(self, text):
        ollama = FakeOllama([said("Use pkill -f python.")] * 4)
        run_agent(ollama, [{"role": "user", "content": text}], hub=FakeHub(DESKTOP))
        body = ollama.chat_bodies[0]
        return last_user_note(body), {tool["function"]["name"] for tool in body.get("tools") or []}

    def test_a_how_to_question_is_explained_without_running_anything(self):
        for text in ("how do I kill all python processes on linux?", "How can I see which ports are open?",
                     "hey arty, how to find big files on ubuntu", "can you tell me how to restart nginx",
                     "what's the command to list docker containers?"):
            note, tools = self.sent(text)
            self.assertIn(EXPLAIN, note, text)
            self.assertNotIn("workspace__run_command", tools, text)
            self.assertIn("research__web_search", tools, text)

    def test_running_it_on_the_computer_keeps_the_run_tools(self):
        for text in ("how do I kill all python processes on your computer?",
                     "how do I free up disk space? try it on your desktop"):
            note, tools = self.sent(text)
            self.assertNotIn(EXPLAIN, note, text)
            self.assertIn("workspace__run_command", tools, text)

    def test_a_how_to_about_running_something_is_still_only_explained(self):
        note, tools = self.sent("how do I run a python script in the background?")
        self.assertIn(EXPLAIN, note)
        self.assertNotIn("workspace__run_command", tools)

    def test_requests_to_do_it_are_not_how_to_questions(self):
        for text in ("kill all python processes on your computer", "check how much disk space is free on your desktop"):
            note, tools = self.sent(text)
            self.assertNotIn(EXPLAIN, note, text)
            self.assertIn("workspace__run_command", tools, text)


if __name__ == "__main__":
    unittest.main()
