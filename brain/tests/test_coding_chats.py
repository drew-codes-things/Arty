import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import model_choice  # noqa: E402
import coding  # noqa: E402
import models  # noqa: E402

CONFIG = {"model": "qwen3:14b", "coding_model": "coder:9b", "persona": "You are Arty.", "system_prompt": "Use your tools.",
          "options": {}, "tool_groups": {}, "approval": {}}
INSTALLED = [{"name": name, "billions": 9.0, "tools": True, "vision": False, "embedding": False, "thinking": True}
             for name in ("qwen3:14b", "coder:9b")]
CODER = "arty/coder:9b"

CODING_REQUESTS = [
    "why does this fail?\n```python\nprint(total(items)\n```",
    "```\nconst x = await fetch(url)\n```\nwhat's wrong here",
    "Traceback (most recent call last):\n  File \"app.py\", line 12, in <module>\n    main()\nNameError: name 'main' is not defined",
    "I keep getting TypeError: unsupported operand type(s) for +: 'int' and 'str' when I run it",
    "what does a NullPointerException mean when my app starts?",
    "main.c:12:5: error: expected ';' before 'return'",
    "error[E0382]: borrow of moved value: `v`",
    "src/app.ts(4,7): error TS2322: Type 'string' is not assignable to type 'number'.",
    "TypeError: Cannot read properties of undefined (reading 'map')\n    at Object.<anonymous> (/app/index.js:3:9)",
    "Exception in thread \"main\" java.lang.IllegalStateException: boom\n    at com.example.App.main(App.java:7)",
    "write a python function that reverses a string",
    "Write a bash script to back up my photos",
    "can you write me a powershell script that empties the recycle bin",
    "debug this JavaScript function for me, it never returns",
    "refactor this class to use composition instead of inheritance",
    "review my pull request before I merge it",
    "explain this regex: ^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$",
    "Write a regex to match UK postcodes",
    "write a SQL query that lists customers who ordered twice",
    "what does the shell command find . -name '*.log' -delete do?",
    "give me a terminal command to find the biggest files on my disk",
    "How do I reverse a list in Python?",
    "explain list comprehensions in Python",
    "what's the difference between let and const in JavaScript?",
    "write a function that returns the nth fibonacci number",
    "write a script that renames all my photos by the date they were taken",
    "write a Kotlin class for a bank account",
    "my react component re-renders forever, can you fix the code?",
    "can you review this code: def add(a, b): return a - b",
    "fix my code please, the loop never ends",
    "why is this slow?\ndef total(items):\n    result = 0\n    for item in items:\n        result += item.price\n    return result",
    "help me with my python homework on recursion",
    "write unit tests for the parse_date function",
    "optimise this SQL query, it takes ten seconds",
    "Can you explain what this Rust function does?",
    "Write a Swift function that sorts names alphabetically",
    "what's the time complexity of this algorithm in Java?",
]

EVERYDAY_CHAT = [
    "what's my phone's code? I forgot it",
    "I forgot my phone's code, how do I reset it?",
    "what's the zip code for Beverly Hills?",
    "what's the dress code for a black tie wedding?",
    "explain Morse code to me",
    "what's Python the snake? is it dangerous?",
    "how big does a python get?",
    "write a script for my YouTube video about hiking",
    "fix my class schedule for next term",
    "explain the function of the liver",
    "write a code of conduct for my Discord server",
    "can you review my essay on the French revolution?",
    "debug my morning routine, I'm always late",
    "what's the best programming language to learn first?",
    "explain the Da Vinci Code plot",
    "what's the area code for Manchester?",
    "write me a poem about a python",
    "my code for the garage door isn't working",
    "set a timer for 10 minutes",
    "review the new Pixel phone",
    "explain how the highway code handles roundabouts",
    "can you write a query letter to a literary agent?",
    "explain the class divide in Victorian England",
    "what's the cheat code for infinite money in GTA?",
    "make a program for my 5k training",
    "explain the command structure of the British army",
    "I'm travelling around Java next month, any tips?",
    "write an email to my landlord about the boiler",
    "what's the weather like tomorrow?",
    "my laptop shows an error: disk full, what do I do?",
    "explain vitamin C function in the body",
    "write a short script for a school play about recycling",
    "what does the new Taylor Swift song mean?",
    "fix the rust on my bike chain",
    "how do I make a good cup of Java coffee?",
    "what's my bank's SWIFT code?",
    "write a script for our school play, how will the audience react?",
    "how should I react in an argument with my sister?",
    "shopping list:\nEggs = 2\nMilk = 1\nBread = 1",
    "plans for today:\n  at home (back at 5:30)\n  at work (until 12:00)",
    "any support groups for terminal illness near me?",
    "what's the best way to get rid of a computer virus?",
    "can you explain how my phone's battery works?",
]


class CodingRequestDetector(unittest.TestCase):
    def test_clear_coding_requests_are_recognised(self):
        for text in CODING_REQUESTS:
            with self.subTest(text=text):
                self.assertTrue(coding.is_request(text))

    def test_everyday_chat_that_mentions_tech_is_not(self):
        for text in EVERYDAY_CHAT:
            with self.subTest(text=text):
                self.assertFalse(coding.is_request(text))

    def test_empty_text_is_not_a_coding_request(self):
        self.assertFalse(coding.is_request(""))
        self.assertFalse(coding.is_request(None))


class ChatModel(unittest.TestCase):
    def setUp(self):
        for name, value in (("installed", INSTALLED), ("is_loaded", True)):
            patcher = unittest.mock.patch.object(models, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_coding_question_for_the_main_entry_goes_to_the_coding_model(self):
        for requested in ("Arty", "assistant-core", ""):
            with self.subTest(requested=requested):
                self.assertEqual(brain.chat_model(CONFIG, requested, "write a python function that reverses a string"), CODER)

    def test_a_short_coding_question_stays_on_the_main_model_when_the_coding_model_is_not_loaded(self):
        with unittest.mock.patch.object(models, "is_loaded", return_value=False) as loaded:
            self.assertEqual(brain.chat_model(CONFIG, "Arty", "write a python function that reverses a string"), "Arty")
        loaded.assert_called_once_with("coder:9b")

    def test_a_big_coding_job_swaps_in_the_coding_model_even_when_it_is_not_loaded(self):
        long_paste = "why does this crash?\n```python\n" + "\n".join(f"value_{n} = compute({n})" for n in range(40)) + "\n```"
        with unittest.mock.patch.object(models, "is_loaded", return_value=False):
            self.assertEqual(brain.chat_model(CONFIG, "Arty", long_paste), CODER)

    def test_everyday_chat_never_asks_ollama_what_is_loaded(self):
        with unittest.mock.patch.object(models, "is_loaded", side_effect=AssertionError("asked Ollama")):
            self.assertEqual(brain.chat_model(CONFIG, "Arty", "what's the dress code for a wedding?"), "Arty")

    def test_everyday_chat_stays_on_the_main_model(self):
        self.assertEqual(brain.chat_model(CONFIG, "Arty", "what's the dress code for a wedding?"), "Arty")

    def test_an_explicit_model_pick_is_left_alone(self):
        for requested in ("arty/qwen3:14b", "solo/other:12b", "some-persona"):
            with self.subTest(requested=requested):
                self.assertEqual(brain.chat_model(CONFIG, requested, "write a python function that reverses a string"), requested)

    def test_routing_can_be_switched_off(self):
        config = {**CONFIG, "route_coding_chats": False}
        self.assertEqual(brain.chat_model(config, "Arty", "write a python function that reverses a string"), "Arty")

    def test_without_a_coding_model_nothing_moves(self):
        self.assertEqual(brain.chat_model({"model": "qwen3:14b"}, "Arty", "write a python function that reverses a string"), "Arty")

    def test_a_missing_coding_model_falls_back_to_the_main_model_with_one_warning(self):
        with unittest.mock.patch.object(models, "installed", return_value=INSTALLED[:1]), \
                self.assertLogs("brain", level="WARNING") as logged:
            self.assertEqual(brain.chat_model(CONFIG, "Arty", "write a python function that reverses a string"), "Arty")
        self.assertEqual(len(logged.output), 1)
        self.assertIn("coder:9b", logged.output[0])

    def test_an_unreachable_ollama_falls_back_to_the_main_model(self):
        with unittest.mock.patch.object(models, "installed", side_effect=urllib.error.URLError("refused")), \
                self.assertLogs("brain", level="WARNING"):
            self.assertEqual(brain.chat_model(CONFIG, "Arty", "write a python function that reverses a string"), "Arty")


class FakeOllama:
    def __init__(self):
        self.bodies = []

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        self.bodies.append(body)
        return {"message": {"role": "assistant", "content": "Here you go."}, "done_reason": "stop"}


def found(name):
    return next((model for model in INSTALLED if model["name"] == name), None)


def run_turns_offline(case):
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    case.config = {**CONFIG, "conversations_dir": folder.name}
    case.ollama = FakeOllama()
    case.make_room = unittest.mock.Mock(return_value=[])
    case.reload = unittest.mock.Mock()
    case.loaded = unittest.mock.Mock(return_value=True)
    patches = [
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(case.config)),
        unittest.mock.patch.object(connections, "http_json", side_effect=case.ollama),
        unittest.mock.patch.object(connections.hub, "refresh"),
        unittest.mock.patch.object(connections.hub, "tools", []),
        unittest.mock.patch.object(connections.hub, "routes", {}),
        unittest.mock.patch.object(brain, "archive_turn"),
        unittest.mock.patch.object(brain, "extract_memories"),
        unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
        unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *_: answer),
        unittest.mock.patch.object(model_choice, "schedule_main_reload", case.reload),
        unittest.mock.patch.object(brain.style, "draft", side_effect=AssertionError("a coding request went to the drafter")),
        unittest.mock.patch.object(brain, "researched_answer", side_effect=AssertionError("a coding request went to web research")),
        unittest.mock.patch.object(models, "make_room", case.make_room),
        unittest.mock.patch.object(models, "find", side_effect=found),
        unittest.mock.patch.object(models, "installed", return_value=INSTALLED),
        unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b", "coder:9b"]),
        unittest.mock.patch.object(models, "is_loaded", case.loaded),
    ]
    for patch in patches:
        patch.start()
    case.addCleanup(unittest.mock.patch.stopall)


class RoutedTurns(unittest.TestCase):
    def setUp(self):
        run_turns_offline(self)

    def chat(self, text, requested="Arty"):
        return brain.run_turn([{"role": "user", "content": text}], requested)

    def test_a_coding_question_is_answered_by_the_coding_model(self):
        self.chat("write a python function that reverses a string")
        self.assertEqual(self.ollama.bodies[0]["model"], "coder:9b")
        self.make_room.assert_called_once()
        self.assertEqual(self.make_room.call_args.args[0], "coder:9b")
        self.reload.assert_called_once()

    def test_a_short_coding_question_with_the_coding_model_unloaded_is_answered_without_a_swap(self):
        self.loaded.return_value = False
        self.chat("write a python function that reverses a string")
        self.assertEqual(self.ollama.bodies[0]["model"], "qwen3:14b")
        self.make_room.assert_not_called()
        self.reload.assert_not_called()

    def test_everyday_chat_is_answered_by_the_main_model(self):
        self.chat("tell me a fun fact about otters")
        self.assertEqual(self.ollama.bodies[0]["model"], "qwen3:14b")
        self.make_room.assert_not_called()

    def test_a_coding_request_that_reads_like_an_email_draft_still_reaches_the_coding_model(self):
        self.chat("write a python script that sends an email")
        self.assertEqual(self.ollama.bodies[0]["model"], "coder:9b")

    def test_a_coding_request_that_reads_like_a_quick_web_question_still_reaches_the_coding_model(self):
        self.chat("what is the best way to write a regex for emails in Python?")
        self.assertEqual(self.ollama.bodies[0]["model"], "coder:9b")

    def test_an_explicit_model_pick_answers_coding_questions_itself(self):
        self.chat("write a python function that reverses a string", requested="arty/qwen3:14b")
        self.assertEqual(self.ollama.bodies[0]["model"], "qwen3:14b")
        self.make_room.assert_not_called()

    def test_with_routing_switched_off_coding_questions_stay_on_the_main_model(self):
        self.config["route_coding_chats"] = False
        self.chat("write a python function that reverses a string")
        self.assertEqual(self.ollama.bodies[0]["model"], "qwen3:14b")

    def test_a_turn_the_private_add_on_takes_is_never_routed(self):
        addon = unittest.mock.Mock()
        addon.turn.return_value = ("In character.", [], {}, None)
        with unittest.mock.patch.object(brain, "local_addon", addon), \
                unittest.mock.patch.object(brain, "chat_model", side_effect=AssertionError("routed an add-on turn")):
            answer = self.chat("write a python function that reverses a string", requested="Arty")
        self.assertEqual(answer[0], "In character.")
        self.assertEqual(self.ollama.bodies, [])
        self.make_room.assert_not_called()

    def test_a_turn_the_private_add_on_passes_on_is_routed_after_it(self):
        addon = unittest.mock.Mock()
        addon.turn.return_value = None
        with unittest.mock.patch.object(brain, "local_addon", addon):
            self.chat("write a python function that reverses a string")
        addon.turn.assert_called_once()
        self.assertEqual(self.ollama.bodies[0]["model"], "coder:9b")


class RoutedReplyName(Server):
    def setUp(self):
        super().setUp()
        run_turns_offline(self)

    def test_a_routed_reply_still_names_the_model_arty(self):
        status, body = self.request("POST", "/v1/chat/completions",
                                    body={"model": "Arty", "messages": [{"role": "user", "content": "write a python function that reverses a string"}]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "Arty")
        self.assertEqual(self.ollama.bodies[0]["model"], "coder:9b")


if __name__ == "__main__":
    unittest.main()
