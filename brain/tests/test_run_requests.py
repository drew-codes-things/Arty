import http.client
import os
import socket
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_code_in_the_reply import CODE_ONLY, COMPUTER, first_round  # noqa: E402
from test_review_tonight import FakeEngine, FakeHub, last_user_note, run_agent, said  # noqa: E402

import connections  # noqa: E402
import honesty  # noqa: E402
import run_requests  # noqa: E402
import steering  # noqa: E402

PRIMES = "Write a python script that prints the first 10 primes"
LOGS = "Write a bash script that deletes logs older than 30 days"
ASKED_IN_NEW_WORDS = (f"{PRIMES}. Also, can you run it?", f"{PRIMES}. And can you run it for me?",
                      f"{PRIMES}. Then could you run it?", f"{PRIMES}. Now can you run it?",
                      f"{PRIMES}. Thanks, can you run it too?", f"{PRIMES}. When you're done, can you run it?",
                      f"{PRIMES}. It should be run on your computer", f"{PRIMES}. Would you mind running it?")
NOT_ASKED_IN_NEW_WORDS = ("Write a Dockerfile to build and run the app", "Write a bash script to compile and run my C program",
                          f"{LOGS}. Run it only after I say so", f"{LOGS}. I'll copy it to my server and run it there")
CONFIG = {"model": "judge-model", "keep_alive": "17m"}
DEFERRED = (f"{LOGS}. When I say go, run it", f"{LOGS}. Once I confirm, run it", f"{LOGS}. After I've looked at it, run it",
            f"{LOGS}. Check with me, then run it", f"{LOGS}. Could you run it on a Raspberry Pi?",
            f"{LOGS}. Can you run it with Python 2?", f"{LOGS}. Not yet, don't run it", f"{LOGS}. Wait for my OK before running it",
            f"{LOGS}. I'll test it on another machine")
PASTED = "\n".join(f"send_report(person_{number})" for number in range(40))
SAVED_BUT_NOT_RUN = (f"{LOGS} and save it to ~/bin/clean.sh. Don't run it until I say.",
                     f"{LOGS} in /srv/app/logs. Do not run it, I want to read it first.",
                     f"{LOGS}. Put it in your workspace but never run it.",
                     f"Clean up this python script:\n{PASTED}\ndo not run it, it emails people")
BUILDS = ("Write a python program that tests whether a number is prime",
          "Write a Flask app in python with a start page and a login page",
          "Write a python program that tries three passwords and then locks the account",
          "write a javascript game in code where the player starts with three lives",
          "Write the code for a python bot that launches a poll every morning")
CODE_REPLY = "Here it is:\n```python\nprint(2)\nprint(3)\n```"
EXPLICIT_RUN_REQUESTS = (
    "Write a python function that returns the square of a number when I give it a number, then run it with 5",
    "Write a python function that returns True when I give it a palindrome, and test it",
    "Write a python script that prints 'not yet' until a counter reaches 3. Run it and show me the output",
    f"{LOGS}. I am not yet sure about the 30 days, but run it and show me what it would delete",
    f"{PRIMES}. Last time, after I checked your code by hand it was wrong, so run it this time",
    f"{PRIMES}. When we looked at the last one it crashed, so please run it",
    f"{PRIMES}. After I read your last answer I trust nothing, run it",
)
EXPLICIT_RUN_FOLLOW_UPS = ("not yet convinced, run it", "when I checked it printed nothing. run it yourself and show me",
                           "once I gave it 5 it crashed. run it with 5",
                           "after I looked at it I think line 3 is wrong, run it and show me the output",
                           "I said run it. Not yet done? run it now", "when we said sorted we meant descending. fix it and run it")
ELSEWHERE_DESPITE_FIRST_OR_NOW = (f"{PRIMES}. I'll run it on my laptop", f"{PRIMES}. I'll try it on my own machine first",
                                  f"Now w{PRIMES[1:]}. I will run it on my server",
                                  f"{PRIMES}. I'm going to test it on my server right away",
                                  f"{PRIMES}. I'll run it on my server immediately after you send it")
CLEARLY_CONDITIONAL = (f"{LOGS}. Don't run it before I've had a look", f"{LOGS}. Run it only if I approve",
                       f"{LOGS}. I'll tell you when to run it", f"{LOGS}. Wait for the go-ahead before you run it",
                       f"{LOGS}. Hold off running it", f"{LOGS}. Run it after my review", f"{LOGS}. Don't run it unless I say so",
                       f"{LOGS}. Pending my approval, run it",
                       f"{LOGS}. You may run it, but only after we have talked about the path",
                       f"{LOGS}. Show me first, then I decide whether you run it", f"{LOGS}. Hold on, let me review it first",
                       f"{LOGS}. Don't run it yet")
SQUARE = "Write a python function that returns the square of a number"
RUN_REQUESTS_WITH_CONDITION_WORDS = (
    "Write a python script that exits if I say bye, and run it",
    "Write a python quiz that prints correct when I say yes, then run it with yes",
    "Write a script that prints done once I confirm with y, and test it with y",
    "Write a python function that returns False when I check an empty list, and test it",
    f"{SQUARE} and run it before I look at it",
    f"{SQUARE}. Run it now, before I decide to scrap it",
    f"{SQUARE}. Run it, because when I've run it before it crashed",
    f"{SQUARE}. After we've tested it by hand it gave 26 for 5, so run it with 5",
    f"{SQUARE}. Run it now and I'll run it on my laptop later",
    f"{SQUARE}. Run it now so I can try it on my server afterwards",
    f"{SQUARE}. Please run it here since I can't run it on my phone",
    f"{SQUARE}. Run it, don't hold off",
    "Write a bash script that should wait until port 8080 is open and run it",
    f"{SQUARE}. Run it without my review, I trust you",
    f"{SQUARE}. I can't test it on my machine, so you run it",
    f"{SQUARE}. Run it, and tell me if I look at the right output",
    f"{SQUARE}. Run it, then we decide whether to keep it",
    f"{SQUARE}. I'll decide whether to use it once you run it and show me the output",
    f"{SQUARE}. Run it if I am right that 5 gives 25",
    f"{SQUARE}. Test it with python 2 syntax in mind, run it using python 2.7 rules? no: just run it",
)
RUN_FOLLOW_UPS_WITH_CONDITION_WORDS = ("run it before I look at it", "run it now and I'll run it on my laptop later",
                                       "I can't test it on my machine, you run it",
                                       "when I've run it before it crashed, so run it and show me", "run it, don't hold off",
                                       "if I say run it, do you run it? run it")
PLAIN_REFUSALS = tuple(f"{LOGS}. {refusal}" for refusal in (
    "Let me run it", "I'll test it at work tomorrow", "I will run it myself", "I'll run it myself later", "No need to run it",
    "You don't have to run it", "Do not run it", "Never run it on your computer", "Don't execute anything",
    "Just the code, I'll try it out later", "Running it is my job", "Leave the running to me",
    "It runs on the NAS, not on your computer", "I'm going to run this at work",
    "Do a dry run in your head only, don't actually run it", "Run nothing", "Don't test it")) + (
    "write a script and I will test it", f"{LOGS}. Let me run it")
INSTALL_BUT_WAIT = f"{LOGS} and install shellcheck first. Don't run the script until I say."


class FakeJudgeEngine:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, url, body=None, **kwargs):
        self.calls.append((url, body, kwargs))
        if isinstance(self.content, BaseException):
            raise self.content
        return {"message": {"role": "assistant", "content": self.content}}


def judged(content, text=f"{PRIMES}. Can you run it?"):
    engine = FakeJudgeEngine(content)
    with unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)), \
            unittest.mock.patch.object(connections, "http_json", side_effect=engine):
        return run_requests.judge_says_run(text), engine


class RunRequestsAreJudged(unittest.TestCase):
    def test_the_judge_is_asked_once_with_the_claim_check_settings(self):
        answer, engine = judged('{"run": "yes"}')
        self.assertTrue(answer)
        self.assertEqual(1, len(engine.calls))
        url, body, kwargs = engine.calls[0]
        self.assertTrue(url.endswith("/api/chat"))
        self.assertEqual(("judge-model", "json", False, False, "17m"),
                         (body["model"], body["format"], body["think"], body["stream"], body["keep_alive"]))
        self.assertEqual((0, run_requests.RUN_REQUEST_TOKENS), (body["options"]["temperature"], body["options"]["num_predict"]))
        self.assertEqual(run_requests.RUN_REQUEST_SECONDS, kwargs["timeout"])
        self.assertIn(f"{PRIMES}. Can you run it?", body["messages"][-1]["content"])

    def test_a_no_from_the_judge_is_not_a_run_request(self):
        self.assertFalse(judged('{"run": "no"}')[0])
        self.assertTrue(judged('{"run": " YES "}')[0])

    def test_a_message_without_a_run_word_is_judged_too(self):
        answer, engine = judged('{"run": "yes"}', f"{PRIMES} and show me the output")
        self.assertTrue(answer)
        self.assertEqual(1, len(engine.calls))

    def test_every_judge_failure_gives_code_only_and_is_logged(self):
        failures = (socket.timeout("timed out"), TimeoutError("timed out"), urllib.error.URLError("refused"),
                    ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), http.client.BadStatusLine("x"),
                    http.client.RemoteDisconnected("gone"), RuntimeError("odd"), MemoryError(),
                    "not json", '{"run": "maybe"}', '{"run": 1}', '["yes"]', "{}")
        for failure in failures:
            with self.subTest(failure=failure), self.assertLogs("brain", "WARNING") as logged:
                try:
                    answer = judged(failure)[0]
                except Exception as error:
                    self.fail(f"the judge failure escaped: {error!r}")
                self.assertFalse(answer)
                self.assertIn("run request check", logged.output[0])

    def test_a_failed_judge_leaves_the_code_only_note_and_no_run_tools(self):
        engine = FakeEngine([said("```python\nprint(1)\n```")] * 4)
        with unittest.mock.patch.object(honesty, "ask_judge", side_effect=TimeoutError("timed out")) as judge, \
                self.assertLogs("brain", "WARNING"):
            run_agent(engine, [{"role": "user", "content": f"{PRIMES}. Can you run it?"}], hub=FakeHub(COMPUTER))
        judge.assert_called_once()
        body = engine.chat_bodies[0]
        self.assertFalse({tool["function"]["name"] for tool in body.get("tools") or []} & steering.RUN_TOOLS)
        self.assertIn(CODE_ONLY, last_user_note(body))

    def test_new_wordings_get_the_run_tools_when_the_judge_says_yes(self):
        for text in ASKED_IN_NEW_WORDS:
            with self.subTest(text=text):
                note, tools = first_round(text, judge_says=True)
                self.assertIn("workspace__run_command", tools)
                self.assertIn(steering.WRITE_AND_RUN_NOTE, note)

    def test_new_wordings_never_get_run_command_when_the_judge_says_no(self):
        for text in NOT_ASKED_IN_NEW_WORDS:
            with self.subTest(text=text):
                note, tools = first_round(text, judge_says=False)
                self.assertNotIn("workspace__run_command", tools)
                if not steering.asks_for_the_computer(text):
                    self.assertFalse(tools & steering.RUN_TOOLS)
                    self.assertIn(CODE_ONLY, note)

    def test_a_long_message_shows_the_judge_a_run_sentence_from_its_middle(self):
        text = "Write a python script.\n" + "x = 1\n" * 1000 + "Do NOT run it, it emails people.\n" + "y = 2\n" * 1000 + "Thanks."
        self.assertIn("Do NOT run it", run_requests.judged_part(text))

    def test_a_long_message_keeps_its_start_and_its_end_for_the_judge(self):
        text = "Write a python script.\n" + "x = 1\n" * 2000 + "Then run it."
        part = run_requests.judged_part(text)
        self.assertTrue(part.startswith("Write a python script."))
        self.assertTrue(part.endswith("Then run it."))
        self.assertLessEqual(len(part), run_requests.RUN_REQUEST_HEAD_CHARS + run_requests.RUN_REQUEST_TAIL_CHARS
                             + len(run_requests.CUT_MARK))


def turn_after(messages, judge_says):
    engine = FakeEngine([said("```python\nprint(1)\n```")] * 4)
    with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=judge_says) as judge:
        run_agent(engine, messages, hub=FakeHub(COMPUTER))
    body = engine.chat_bodies[0]
    notes = "\n".join(message["content"] for message in body["messages"] if message["role"] == "user")
    return notes, {tool["function"]["name"] for tool in body.get("tools") or []}, judge


def follow_up(text, judge_says, earlier_reply=CODE_REPLY):
    return turn_after([{"role": "user", "content": PRIMES}, {"role": "assistant", "content": earlier_reply},
                       {"role": "user", "content": text}], judge_says)


class RunRequestsAreSteered(unittest.TestCase):
    def test_a_deferred_run_is_code_only_without_asking_the_judge(self):
        for text in DEFERRED:
            with self.subTest(text=text):
                note, tools, judge = turn_after([{"role": "user", "content": text}], True)
                judge.assert_not_called()
                self.assertFalse(tools & steering.RUN_TOOLS)
                self.assertIn(CODE_ONLY, note)

    def test_running_here_first_is_still_judged_beside_a_later_run_elsewhere(self):
        text = f"I want to run it on my server later but first run it on your computer: {PRIMES}"
        note, tools, judge = turn_after([{"role": "user", "content": text}], True)
        judge.assert_called_once()
        self.assertIn("workspace__run_command", tools)
        self.assertIn(steering.WRITE_AND_RUN_NOTE, note)

    def test_a_judged_no_takes_run_command_away_when_the_computer_is_kept(self):
        for text in SAVED_BUT_NOT_RUN:
            with self.subTest(text=text[:60]):
                note, tools, _ = turn_after([{"role": "user", "content": text}], False)
                self.assertNotIn("workspace__run_command", tools)
                self.assertIn("workspace__write_file", tools)

    def test_a_build_request_keeps_the_computer_when_the_judge_says_no(self):
        for text in BUILDS:
            with self.subTest(text=text):
                note, tools, _ = turn_after([{"role": "user", "content": text}], False)
                self.assertIn("workspace__write_file", tools)
                self.assertNotIn("workspace__run_command", tools)
                self.assertNotIn(CODE_ONLY, note)

    def test_a_follow_up_after_code_asks_the_judge_and_a_yes_runs_it(self):
        note, tools, judge = follow_up("run it", True)
        judge.assert_called_once()
        self.assertEqual("run it", judge.call_args.args[0])
        self.assertIn("workspace__run_command", tools)
        self.assertIn("run_command", note)

    def test_a_deferred_follow_up_takes_run_command_away(self):
        note, tools, judge = follow_up("don't run it until I say", True)
        judge.assert_not_called()
        self.assertNotIn("workspace__run_command", tools)

    def test_a_follow_up_judged_no_keeps_run_command_for_other_commands(self):
        _, tools, _ = follow_up("check how much disk space is free on your desktop", False)
        self.assertIn("workspace__run_command", tools)

    def test_a_follow_up_judged_no_that_speaks_of_running_takes_run_command_away(self):
        _, tools, _ = follow_up("nah, leave it as it is, I'll run it myself", False)
        self.assertNotIn("workspace__run_command", tools)

    def test_an_explicit_run_request_is_always_judged(self):
        for text in EXPLICIT_RUN_REQUESTS:
            with self.subTest(text=text):
                note, tools, judge = turn_after([{"role": "user", "content": text}], True)
                judge.assert_called_once()
                self.assertIn("workspace__run_command", tools)
                self.assertIn(steering.WRITE_AND_RUN_NOTE, note)

    def test_an_explicit_run_follow_up_is_always_judged(self):
        for text in EXPLICIT_RUN_FOLLOW_UPS:
            with self.subTest(text=text):
                note, tools, judge = follow_up(text, True)
                judge.assert_called_once()
                self.assertIn("workspace__run_command", tools)
                self.assertNotIn("didn't ask you to run it", note)

    def test_first_or_now_elsewhere_in_the_message_keeps_a_run_on_another_device_deferred(self):
        for text in ELSEWHERE_DESPITE_FIRST_OR_NOW:
            with self.subTest(text=text):
                note, tools, judge = turn_after([{"role": "user", "content": text}], True)
                judge.assert_not_called()
                self.assertFalse(tools & steering.RUN_TOOLS)

    def test_a_clearly_conditional_run_waits_without_asking_the_judge(self):
        for text in CLEARLY_CONDITIONAL:
            with self.subTest(text=text):
                note, tools, judge = turn_after([{"role": "user", "content": text}], True)
                judge.assert_not_called()
                self.assertNotIn("workspace__run_command", tools)

    def test_condition_words_beside_a_plain_run_request_never_defer_it(self):
        for text in RUN_REQUESTS_WITH_CONDITION_WORDS + RUN_FOLLOW_UPS_WITH_CONDITION_WORDS:
            with self.subTest(text=text):
                self.assertFalse(run_requests.defers_running(text))

    def test_a_plain_refusal_or_running_it_myself_is_decided_by_a_rule(self):
        for text in PLAIN_REFUSALS:
            with self.subTest(text=text):
                note, tools, judge = turn_after([{"role": "user", "content": text}], True)
                judge.assert_not_called()
                self.assertNotIn("workspace__run_command", tools)

    def test_an_install_request_that_defers_the_run_gets_the_dont_run_note(self):
        note, tools, judge = turn_after([{"role": "user", "content": INSTALL_BUT_WAIT}], True)
        judge.assert_not_called()
        self.assertIn(steering.DONT_RUN_NOTE.strip(), note)

    def test_a_failed_judge_on_a_follow_up_says_nothing_untrue(self):
        engine = FakeEngine([said("```python\nprint(1)\n```")] * 4)
        messages = [{"role": "user", "content": PRIMES}, {"role": "assistant", "content": CODE_REPLY},
                    {"role": "user", "content": "run it"}]
        with unittest.mock.patch.object(honesty, "ask_judge", side_effect=TimeoutError("timed out")), \
                self.assertLogs("brain", "WARNING"):
            run_agent(engine, messages, hub=FakeHub(COMPUTER))
        body = engine.chat_bodies[0]
        self.assertNotIn("workspace__run_command", {tool["function"]["name"] for tool in body.get("tools") or []})
        self.assertNotIn("didn't ask you to run it", last_user_note(body))
        self.assertIn("couldn't run it", last_user_note(body))

    def test_open_app_is_withheld_with_run_command(self):
        for messages in ([{"role": "user", "content": PRIMES}],
                         [{"role": "user", "content": PRIMES}, {"role": "assistant", "content": CODE_REPLY},
                          {"role": "user", "content": "don't run it until I say"}]):
            with self.subTest(request=messages[-1]["content"]):
                engine = FakeEngine([said("```python\nprint(1)\n```")] * 4)
                with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=False):
                    run_agent(engine, messages, hub=FakeHub(COMPUTER + ("workspace__open_app",)))
                offered = {tool["function"]["name"] for tool in engine.chat_bodies[0].get("tools") or []}
                self.assertNotIn("workspace__open_app", offered)

    def test_a_reply_without_code_is_not_a_follow_up(self):
        _, tools, judge = follow_up("run it", True, earlier_reply="Sure, what should the script do?")
        judge.assert_not_called()
        self.assertIn("workspace__run_command", tools)


if __name__ == "__main__":
    unittest.main()
