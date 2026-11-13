import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser_requests import SHIPPED_CONFIG  # noqa: E402

import connections  # noqa: E402
import native_calls  # noqa: E402
import research  # noqa: E402

DJANGO_QUESTION = "How do I run a background task with the tasks framework built into Django 6? Show the code."
WORLD_CUP_QUESTION = "Who won the 2026 FIFA World Cup final, and what was the score?"

ONE_WORD_RESULTS = """1. DO | English meaning - Cambridge Dictionary
   https://dictionary.cambridge.org/dictionary/english/do
   Do is one of three auxiliary verbs in English: be, do, have. We use do to make negatives (do + not), to make question forms
2. Basic English Grammar | How To Use DO, DOES, DID, DONE
   https://www.youtube.com/watch?v=g0poc28x6mA
   Today, you'll learn how to use DO, DOES, DID, and DONE in English. Improve your English fluency by learning
3. Run - Play it Online at Coolmath Games
   https://www.coolmathgames.com/0-run
   Run through space and jump over the holes in the tunnel."""

WON_RESULTS = """1. South Korean won - Wikipedia
   https://en.wikipedia.org/wiki/South_Korean_won
   The won is the official currency of South Korea. A single won is divided into 100 jeon.
2. 2026 - Wikipedia
   https://en.wikipedia.org/wiki/2026
   2026 is the current year, and is a common year starting on Thursday of the Gregorian calendar."""

MIXED_RESULTS = """1. DO | English meaning - Cambridge Dictionary
   https://dictionary.cambridge.org/dictionary/english/do
   Do is one of three auxiliary verbs in English.
2. Django's Tasks framework | Django documentation
   https://docs.djangoproject.com/en/6.0/topics/tasks/
   The Django Tasks framework allows work to be run outside the request-response cycle, in a background worker.
3. Background Tasks in Django 6.0
   https://example.org/django-6-background-tasks
   How to define a task with the task decorator and enqueue it."""


def lookup_notes(search_result, said):
    with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: search_result):
        return native_calls.look_up_first(SHIPPED_CONFIG, said)


class SearchResultsAboutTheQuestion(unittest.TestCase):
    def test_results_about_other_words_are_dropped_and_the_rest_renumbered(self):
        kept = research.relevant_results(MIXED_RESULTS, DJANGO_QUESTION)
        self.assertNotIn("Cambridge Dictionary", kept)
        self.assertTrue(kept.startswith("1. Django's Tasks framework"))
        self.assertIn("\n2. Background Tasks in Django 6.0", kept)
        self.assertEqual(research.result_urls(kept), ["https://docs.djangoproject.com/en/6.0/topics/tasks/",
                                                      "https://example.org/django-6-background-tasks"])

    def test_results_that_share_one_common_word_are_not_about_the_question(self):
        self.assertTrue(research.relevant_results(WON_RESULTS, WORLD_CUP_QUESTION).startswith("No results about:"))
        self.assertTrue(research.relevant_results(ONE_WORD_RESULTS, DJANGO_QUESTION).startswith("No results about:"))

    def test_a_short_question_needs_only_one_shared_word(self):
        self.assertIn("Best chocolate cake", research.relevant_results(
            "1. Best chocolate cake\n   https://www.bbcgoodfood.com/recipes/chocolate-cake\n   Rich and easy.",
            "use your desktop to find out the best type of cake"))

    def test_search_failures_pass_through_unchanged(self):
        for text in ("No results for: kubefrost", "search is unavailable: timed out"):
            self.assertEqual(research.relevant_results(text, "kubefrost flags"), text)

    def test_a_lookup_whose_results_are_about_something_else_is_reported_as_not_checked(self):
        used, notes, blocked = lookup_notes(ONE_WORD_RESULTS, DJANGO_QUESTION)
        self.assertNotIn("research__web_search", used)
        self.assertIn("couldn't be checked", notes)
        self.assertIn("No results about:", notes)
        self.assertNotIn("Cambridge Dictionary", notes)
        self.assertIsNone(blocked)

    def test_a_lookup_with_no_results_is_reported_as_not_checked(self):
        used, notes, _ = lookup_notes("No results for: kubefrost CLI flags", "What flags does the kubefrost CLI take for snapshotting?")
        self.assertNotIn("research__web_search", used)
        self.assertIn("couldn't be checked", notes)

    def test_a_lookup_keeps_only_the_results_about_the_question(self):
        used, notes, _ = lookup_notes(MIXED_RESULTS, DJANGO_QUESTION)
        self.assertIn("research__web_search", used)
        self.assertIn("Django's Tasks framework", notes)
        self.assertNotIn("Cambridge Dictionary", notes)

    def test_quick_research_with_only_unrelated_results_reads_no_pages_and_says_so(self):
        calls = []

        def call_tool(name, arguments):
            calls.append(name)
            return WON_RESULTS

        answer, used = research.run(WORLD_CUP_QUESTION, call_tool, lambda prompt: "unused", browser=False)
        self.assertEqual(calls, ["research__web_search"])
        self.assertTrue(answer.startswith("I couldn't research that: No results about:"), answer)
        self.assertNotIn("Korean", answer)


if __name__ == "__main__":
    unittest.main()
