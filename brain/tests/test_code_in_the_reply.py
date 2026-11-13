import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeOllama, last_user_note, run_agent, said  # noqa: E402

import steering  # noqa: E402

COMPUTER = ("workspace__run_command", "workspace__read_terminal", "workspace__list_files", "workspace__read_file",
            "workspace__write_file", "workspace__edit_file", "fetch__fetch", "research__web_search")
CODE_ONLY = "Reply with the code itself"
RUN_IT = "run it on your computer"
HARNESS_ENDING = " Show the code in your answer, don't write it to a file or run it."
CODE_REQUESTS = (
    "Write a Python function parse_duration(text: str) -> int that converts strings like '1h30m' into seconds." + HARNESS_ENDING,
    "Write a Rust function fn roman_to_int(s: &str) -> Option<u32>. Include a main." + HARNESS_ENDING,
    "Write a Java class Solution with a static method static boolean isBalanced(String s)." + HARNESS_ENDING,
    "I need a Go function that reverses a string by runes",
    "Could you do me a TypeScript function chunk<T>(items: T[], size: number): T[][]?",
    "Write a script that I can run every morning to tidy my downloads folder",
    "write a python function that adds two numbers, plus a unit test for it",
    "Write a SQL query that lists customers whose orders total more than 100. Don't run it, just show it.",
    "write a bash script to install docker on ubuntu",
)
COMPUTER_REQUESTS = (
    "Write a Python script that prints the first 10 primes and run it",
    "write a C program that prints hello, then test it on your computer",
    "Write a bash script that backs up my notes and save it to ~/bin/backup.sh",
    "install rust and write a hello world program",
    "build me a flask app for my todo list",
    "fix the bug in /home/ai/projects/app.py",
)


def first_round(text):
    ollama = FakeOllama([said("```python\nprint(1)\nprint(2)\n```")] * 4)
    run_agent(ollama, [{"role": "user", "content": text}], hub=FakeHub(COMPUTER))
    body = ollama.chat_bodies[0]
    return last_user_note(body), {tool["function"]["name"] for tool in body.get("tools") or []}


class CodeIsAnsweredInTheReply(unittest.TestCase):
    def test_asking_for_code_offers_no_computer_tools(self):
        for text in CODE_REQUESTS:
            note, tools = first_round(text)
            self.assertFalse(tools & steering.RUN_TOOLS, text)
            self.assertIn(CODE_ONLY, note, text)
            self.assertNotIn(RUN_IT, note, text)
            self.assertIn("research__web_search", tools, text)

    def test_asking_to_run_save_install_or_build_keeps_the_computer(self):
        for text in COMPUTER_REQUESTS:
            note, tools = first_round(text)
            self.assertIn("workspace__run_command", tools, text)
            self.assertIn("workspace__write_file", tools, text)
            self.assertNotIn(CODE_ONLY, note, text)

    def test_asking_to_run_it_says_how(self):
        note, _ = first_round("Write a Python script that prints the first 10 primes and run it")
        self.assertIn(RUN_IT, note)

    def test_a_refused_run_is_not_a_request_to_run(self):
        for text in ("don't run it", "Show me the code, do not execute it.", "no need to test it, just write it"):
            self.assertFalse(steering.asks_to_run(text), text)
        for text in ("don't explain, just run it", "write it and then run it", "test it please"):
            self.assertTrue(steering.asks_to_run(text), text)

    def test_ordinary_requests_are_unchanged(self):
        for text in ("list my files", "check how much disk space is free on your desktop"):
            note, tools = first_round(text)
            self.assertIn("workspace__run_command", tools, text)
            self.assertNotIn(CODE_ONLY, note, text)


if __name__ == "__main__":
    unittest.main()
