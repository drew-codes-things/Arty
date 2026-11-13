import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coding  # noqa: E402
import models  # noqa: E402

LONG_PASTE = "why does this crash?\n```python\n" + "\n".join(f"value_{n} = compute({n})" for n in range(40)) + "\n```"
BIG_JOBS = [
    "build me a discord bot that posts the weather every morning",
    "create a flask app with login and a dashboard",
    "write a website for my homelab with a status page",
    "make a game in python with pygame",
    "refactor this project so the database code is in its own module",
    "port my bash scripts to python across the whole project",
    "split this across several files and add tests",
    LONG_PASTE,
]
SMALL_QUESTIONS = [
    "write a python function that checks whether a string is a palindrome, ignoring case and spaces",
    "fix this bug:\n```python\ndef add(a, b):\n    return a - b\n```",
    "Write a regex to match UK postcodes",
    "How do I reverse a list in Python?",
    "write a SQL query that lists customers who ordered twice",
    "explain list comprehensions in Python",
    "write a script that renames all my photos by the date they were taken",
]


class CodingJobSize(unittest.TestCase):
    def test_big_coding_jobs_are_recognised(self):
        for text in BIG_JOBS:
            with self.subTest(text=text[:60]):
                self.assertTrue(coding.is_big_job(text))

    def test_short_coding_questions_are_not_big_jobs(self):
        for text in SMALL_QUESTIONS:
            with self.subTest(text=text[:60]):
                self.assertFalse(coding.is_big_job(text))


class LoadedModels(unittest.TestCase):
    def test_a_model_ollama_holds_is_loaded(self):
        running = {"models": [{"name": "mimo-v2.6:9b", "model": "mimo-v2.6:9b", "size_vram": 1}]}
        with unittest.mock.patch.object(models, "_ollama", return_value=running):
            self.assertTrue(models.is_loaded("mimo-v2.6:9b"))
            self.assertFalse(models.is_loaded("qwen3:14b"))

    def test_a_latest_tag_matches_the_short_name(self):
        running = {"models": [{"name": "coder:latest", "model": "coder:latest"}]}
        with unittest.mock.patch.object(models, "_ollama", return_value=running):
            self.assertTrue(models.is_loaded("coder"))

    def test_an_unreachable_ollama_counts_as_not_loaded(self):
        with unittest.mock.patch.object(models, "_ollama", side_effect=urllib.error.URLError("down")):
            self.assertFalse(models.is_loaded("mimo-v2.6:9b"))


if __name__ == "__main__":
    unittest.main()
