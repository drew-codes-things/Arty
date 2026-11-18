import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeEngine, last_user_note, run_agent, said  # noqa: E402

import run_requests  # noqa: E402
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

LOGS = "Write a bash script that deletes logs older than 30 days"
PRIMES = "Write a python script that prints the first 10 primes"
RUN_TABLE = (
    ("don't run it", False), ("Show me the code, do not execute it.", False), ("no need to test it, just write it", False),
    ("don\u2019t run it", False), ("you shouldn\u2019t execute it", False), ("don\u2018t test it, just write it", False),
    (f"{LOGS}, but don't run it", False), (f"{LOGS}, but don\u2019t run it", False), (f"{LOGS}, but don\u00b4t run it", False),
    (f"{LOGS}, but don`t run it", False), (f"{LOGS}, but don\u02bct run it", False), (f"{LOGS}, but don\uff07t run it", False),
    (f"{LOGS}, I'll run it myself later", False),
    ("Write a python script to rename my photos so I can run it on my server", False),
    ("Write a bash script to wipe /tmp/cache, you needn't run it", False),
    ("Write a python script for this, there's no point trying it on your machine", False),
    ("write a script and I will test it", False), ("Write it but avoid running it", False), ("don't compile and run it", False),
    ("make sure not to run it", False), ("we'll execute it ourselves", False),
    (f"{LOGS}. How do I run it?", False), (f"{LOGS}. Should I run it as root?", False),
    (f"{LOGS}. What happens when I run it?", False), (f"{LOGS}, my colleague will run it tomorrow", False),
    (f"{LOGS}. The user should run it as root.", False), (f"{LOGS}. You mustn't run it.", False),
    (f"{LOGS}. You can't run it, it needs root.", False), (f"{LOGS}. Under no circumstances run it.", False),
    (f"{LOGS}. Rather than run it, explain it.", False), (f"{LOGS}. I'm gonna run it tonight.", False),
    (f"{LOGS}. Is it safe to run it?", False), (f"{LOGS}. Instead of running it, explain it.", False),
    ("don't explain, just run it", True), ("write it and then run it", True), ("test it please", True),
    ("Write a python script that sorts a list without using sort and run it on your computer", True),
    ("I don't know bash so write me a script that prints the date and run it", True),
    ("I can't run it myself, so run it for me", True), (f"I can't run it myself so run it for me: {PRIMES}", True),
    (f"{PRIMES}. I want to run it on your computer.", True),
    (f"{PRIMES}. I need to test it on your machine before I trust it.", True),
    (f"{PRIMES}. We need to run this on your VM.", True), (f"{PRIMES} so I can see how it works and run it", True),
    (f"I want to run it on my server later but first run it on your computer: {PRIMES}", True),
    (f"{PRIMES}. I have to run it on prod tomorrow so run it on your computer first and show me what it prints", True),
    (f"{PRIMES} and run it", True), ("write a C program that prints hello, then test it on your computer", True),
    (f"{PRIMES}. Could you write it and run it?", True), (f"{PRIMES}. Can you run it?", True),
    ("Write and run a python script that prints the first 10 primes", True),
    ("Create and run a bash script that prints the date", True),
    ("Write and test a python function that reverses a string", True),
    ("Write, run and show the output of a python script that prints the first 10 primes", True),
    (f"{PRIMES} and actually run it", True), (f"{PRIMES} and then actually run it", True),
    (f"{PRIMES}, then make sure to run it", True), (f"{PRIMES} and be sure to test it", True),
    (f"{PRIMES} and finally run it", True), (f"{PRIMES}. Let's run it", True), (f"{PRIMES}. Now let's test it", True),
    (f"{PRIMES}. Remember to run it", True), (f"{PRIMES}. Do run it", True), (f"{PRIMES}. Try to run it", True),
    (f"{PRIMES} and run the file", True), (f"{PRIMES} and run the tests", True), (f"{PRIMES}, then execute", True),
    (f"{PRIMES} and test thoroughly", True), (f"{PRIMES}. After that run it", True), (f"{PRIMES}. Once written run it", True),
    (f"{PRIMES}. Definitely run it", True), (f"{PRIMES}. Re-run it until it passes", True), (f"{PRIMES}. Run it too", True),
    (f"{PRIMES}. Then run it and show me the output", True), (f"{PRIMES}. Make sure you test it", True),
    (f"{PRIMES}. I'd like you to run it", True), (f"{PRIMES}. I want it run on your computer", True),
    (f"{PRIMES}. Can't you just run it?", True), (f"{PRIMES}. Instead of explaining it run it", True),
    (f"{PRIMES}. Run it for me as I can't do that myself", True), (f"{PRIMES}. It would be great if it got run", True),
    (f"{PRIMES} and verify it works by running it", True), (f"{PRIMES}. Please actually execute it", True),
    (f"{PRIMES}. Then simply run it", True), (f"{PRIMES} and show the output", True),
    (f"{PRIMES} and give me the output", True),
    (f"{LOGS}. Can I run it as root?", False), (f"{LOGS}. Did you run it?", False),
    (f"{LOGS}. You won't be able to run it, it needs root", False), (f"{LOGS}. You won't need to run it", False),
    (f"{LOGS}. There is no need for you to run it", False), (f"{LOGS}. I'd rather you didn't run it", False),
    (f"{LOGS}. You aren't allowed to run it", False), (f"{LOGS}. It isn't necessary that you run it", False),
    (f"{LOGS}. You wouldn't be able to test it anyway", False), (f"{LOGS}. Is it safe to run it on your computer?", False),
    (f"{LOGS}. Would it break anything if someone ran it on your machine? Don't.", False),
    ("Write me a bash script for me to run this weekend that deletes logs older than 30 days", False),
    ("I'd like a python script for me to run that backs up my photos", False),
    ("Write a python script for us to test this feature next sprint", False),
    ("Write a python script like the one you wrote before, for my cron to run it nightly", False),
    (f"{LOGS}. If you run it without sudo it fails, right?", False), (f"{LOGS}. When would you run it, daily or weekly?", False),
    (f"{LOGS}. Have you any idea how often I should run it?", False),
    (f"{LOGS}. Could you explain what happens when I run it?", False), (f"{LOGS}. I would run it for me only", False),
    (f"{LOGS}. Hands off: you won't run it, I will", False), (f"{LOGS}. Do not under any circumstances run it", False),
    (f"{LOGS}. Please refrain from running it; you would never run it anyway", False),
    (f"{LOGS}. I will run it. You just write it", False), (f"{LOGS} which cron will run for me every night", False),
    (f"{LOGS}. My boss wants to try it on your machine type, an M2", False), (f"{LOGS}. Nobody but me should run it", False),
    (f"{LOGS}. Only I may run it", False), (f"{LOGS}. You are forbidden to run it", False),
    (f"{LOGS}. Leave running it to me", False), (f"{LOGS}. I'll be the one to test it, you write it", False),
    (f"{LOGS}. Skip running it", False), (f"{LOGS}. No running it", False), (f"{LOGS}. Hold off on running it until I say", False),
    (f"{LOGS}. Wait before you run it", False), (f"{LOGS}. Ask me before you run it", False),
)
RUN_REQUESTS_WITHOUT_A_RUN_WORD = (f"{PRIMES} and show the output", f"{PRIMES} and give me the output")
APOSTROPHE_STAND_INS = ("\u2019", "\u2018", "\u201b", "\u02bc", "\u2032", "\uff07", "`", "\u00b4")


def first_round(text, judge_says=False):
    engine = FakeEngine([said("```python\nprint(1)\nprint(2)\n```")] * 4)
    with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=judge_says):
        run_agent(engine, [{"role": "user", "content": text}], hub=FakeHub(COMPUTER))
    body = engine.chat_bodies[0]
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
            note, tools = first_round(text, judge_says=True)
            self.assertIn("workspace__run_command", tools, text)
            self.assertIn("workspace__write_file", tools, text)
            self.assertNotIn(CODE_ONLY, note, text)

    def test_asking_to_run_it_says_how(self):
        note, _ = first_round("Write a Python script that prints the first 10 primes and run it", judge_says=True)
        self.assertIn(RUN_IT, note)

    def test_no_run_request_of_the_table_reads_as_deferred(self):
        for text, wanted in RUN_TABLE:
            if wanted:
                with self.subTest(text=text):
                    self.assertFalse(run_requests.defers_running(text))

    def test_every_code_request_of_the_table_is_judged_unless_it_defers(self):
        for text, _ in RUN_TABLE:
            if not steering.asks_for_code(text):
                continue
            with self.subTest(text=text):
                engine = FakeEngine([said("```python\nprint(1)\n```")] * 4)
                with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=False) as judge:
                    run_agent(engine, [{"role": "user", "content": text}], hub=FakeHub(COMPUTER))
                self.assertEqual(0 if run_requests.defers_running(text) else 1, judge.call_count)

    def test_the_judges_answer_steers_every_row_of_the_table(self):
        for text, wanted in RUN_TABLE:
            if not steering.asks_for_code(text):
                continue
            with self.subTest(text=text):
                note, tools = first_round(text, judge_says=wanted)
                ran = wanted and not run_requests.defers_running(text)
                self.assertEqual(ran, "workspace__run_command" in tools)
                self.assertEqual(ran, steering.WRITE_AND_RUN_NOTE in note)
                if ran:
                    continue
                if steering.asks_for_the_computer(text):
                    self.assertIn("workspace__write_file", tools)
                    self.assertIn(steering.DONT_RUN_NOTE, note)
                else:
                    self.assertFalse(tools & steering.RUN_TOOLS)
                    self.assertIn(CODE_ONLY, note)

    def test_every_phrase_matcher_reads_a_curly_apostrophe_like_a_straight_one(self):
        checks = (
            (steering.asks_how_to, "what's the best command to find big files?"),
            (steering.asks_for_code, "what's a regex for an email address?"),
            (lambda text: steering.asks_for_the_computer(text), "Write a script, don't save it to /tmp/x"),
            (lambda text: steering.thinking_for({"think": "auto", "model": "m"}, text), "why won't my loop stop?"),
        )
        for matcher, straight in checks:
            for curly in APOSTROPHE_STAND_INS:
                with unittest.mock.patch.object(steering.models, "find", return_value={"thinking": True}):
                    self.assertEqual(matcher(straight), matcher(straight.replace("'", curly)), (straight, curly))

    def test_ordinary_requests_are_unchanged(self):
        for text in ("list my files", "check how much disk space is free on your desktop"):
            note, tools = first_round(text)
            self.assertIn("workspace__run_command", tools, text)
            self.assertNotIn(CODE_ONLY, note, text)


if __name__ == "__main__":
    unittest.main()
