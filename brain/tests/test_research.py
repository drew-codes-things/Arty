"""Desktop research: search, read the pages, note each, write one sourced answer. Tools and model faked.
Run from brain/: python3 -m unittest discover tests"""
import re
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research  # noqa: E402

SEARCH = """1. Best chocolate cake
   https://www.bbcgoodfood.com/recipes/chocolate-cake
   Rich and easy.
2. Victoria sponge video
   https://www.youtube.com/watch?v=abc
   Watch.
3. Another BBC page
   https://www.bbcgoodfood.com/recipes/other
   Same site.
4. Cake tips
   https://example.org/cake
   Tips.
5. Blocked
   https://blocked.example/cake
   Bot wall."""

LONG = "Cream the butter and sugar. " * 40
SNIPPET_LINE = re.compile(r"^(   (?!https?://).*)$", re.MULTILINE)


def about_the_query(results, query):
    return SNIPPET_LINE.sub(lambda line: f"{line.group(1)} {query}", results)


def fake_tools(calls):
    def call_tool(name, arguments):
        calls.append((name, arguments))
        if name == "research__web_search":
            return about_the_query(SEARCH, arguments["query"])
        url = arguments["url"]
        if "blocked" in url:
            return "Page text:\nPlease verify you are human to continue."
        return f"Title: page\nClickable/typeable items:\n1. Home\n\nPage text:\n{LONG}"
    return call_tool


def fake_model(prompts):
    def complete(prompt):
        prompts.append(prompt)
        if prompt.startswith("Question:") and "Page text:" in prompt:
            return "- Cream butter and sugar first"
        return ("### Direct Answer:\nChocolate cake is the crowd favourite [1].\n\n## Steps\n- Cream butter and sugar [1][2]"
                "\n\n#### Source:\n[1]")
    return complete


class Research(unittest.TestCase):
    def test_recognises_desktop_research_but_not_other_desktop_jobs(self):
        self.assertTrue(research.is_request("use your desktop to find out the best type of cake"))
        self.assertTrue(research.is_request("Can you use your computer to look up how to fix a dripping tap?"))
        self.assertTrue(research.is_request("do some deep research on solar panels"))
        self.assertFalse(research.is_request("use your desktop to open spotify"))
        self.assertFalse(research.is_request("Use your desktop browser to open example.com and tell me the page heading"))
        self.assertFalse(research.is_request("what's the weather"))

    def test_quick_questions_are_web_questions_but_not_about_the_users_own_things(self):
        for text in ["who won the last football world cup?", "what's the best way to learn Python", "how do I make pancakes"]:
            self.assertTrue(research.is_quick_question(text), text)
        for text in ["read my latest email", "what's the latest on my deadlines", "what's 12 times 15", "hi arty"]:
            self.assertFalse(research.is_quick_question(text), text)

    def test_the_query_is_the_question_without_the_asking(self):
        self.assertEqual(research.query_from("can you use your desktop to find out the best type of cake and tell me"),
                         "the best type of cake")
        self.assertEqual(research.query_from("Use your computer to look up how to fix a dripping tap"), "how to fix a dripping tap")
        self.assertEqual(research.query_from("do some deep research on solar panels"), "solar panels")

    def test_the_relevant_part_of_a_long_page_is_read(self):
        history = "\n\n".join(f"In {year} the final was played in a stadium long ago." for year in range(1930, 2000, 4))
        page = history + "\n\nThe 2026 final: Spain beat Argentina 1-0 to win the World Cup."
        excerpt = research.relevant_excerpt(page, "who won the last world cup", 300)
        self.assertIn("2026 final: Spain beat Argentina", excerpt)
        self.assertLessEqual(len(excerpt), 300)

    def test_one_page_per_site_and_no_video_sites(self):
        self.assertEqual(research.result_urls(SEARCH), ["https://www.bbcgoodfood.com/recipes/chocolate-cake",
                                                        "https://example.org/cake", "https://blocked.example/cake"])

    def test_reads_pages_notes_each_and_writes_one_sourced_answer(self):
        calls, prompts = [], []
        answer, used = research.run("use your desktop to find the best cake", fake_tools(calls), fake_model(prompts))
        opened = [args["url"] for name, args in calls if name == "workspace__browser_open"]
        self.assertIn("https://blocked.example/cake", opened)  # tried...
        self.assertTrue(answer.startswith("Chocolate cake is the crowd favourite"))  # the instruction heading is gone
        self.assertEqual(answer.count("Source"), 1)  # only the real source list
        self.assertIn("1. https://www.bbcgoodfood.com/recipes/chocolate-cake", answer)
        self.assertNotIn("blocked.example", answer.split("**Sources**")[1])  # ...but the bot check was skipped
        self.assertEqual(sum(p.startswith("Question:") and "Page text:" in p for p in prompts), 2)
        self.assertIn("workspace__browser_open", used)

    def test_a_false_premise_is_corrected_before_the_answer(self):
        prompts = []

        def complete(prompt):
            prompts.append(prompt)
            if "check the notes against the question" in prompt:
                return "WRONG: There was no replay; the 1923 final was played once, at Wembley."
            return "There was no replay [1]. The 1923 final at Wembley drew 126,047 officially [1]."
        answer, _ = research.run("what was the attendance at the 1923 FA Cup final replay in Dudley?",
                                 fake_tools([]), complete, browser=False)
        answer_prompt = prompts[-1]
        self.assertIn("There was no replay; the 1923 final was played once, at Wembley.", answer_prompt)
        self.assertTrue(answer.startswith("There was no replay"))
        # The check and the answer share the prompt up to the notes, so the model's cache covers both.
        self.assertTrue(prompts[-2].startswith(answer_prompt[:answer_prompt.index("The question gets something wrong")]))

    def test_the_short_checks_go_to_the_quick_model_call_and_the_answer_to_the_full_one(self):
        checked, written = [], []

        def classify(prompt):
            checked.append(prompt)
            if "check the notes against the question" in prompt:
                return "WRONG"
            return "It was at Wembley, not in Dudley."

        def complete(prompt):
            written.append(prompt)
            return "The 1923 final was at Wembley [1]."
        research.run("what was the attendance at the 1923 FA Cup final replay in Dudley?", fake_tools([]), complete,
                     browser=False, classify=classify)
        self.assertEqual(len(checked), 2)
        self.assertIn("check the notes against the question", checked[0])
        self.assertIn("say what's true", checked[1])
        self.assertEqual(len(written), 1)
        self.assertIn("Write the answer for the user", written[0])

    def test_the_subject_for_a_second_search_gets_the_full_thinking_call(self):
        checked, written = [], []

        def classify(prompt):
            checked.append(prompt)
            return "MISSING"

        def complete(prompt):
            written.append(prompt)
            return "1923 FA Cup final"
        research.run("what was the attendance at the 1923 FA Cup final replay in Dudley?", fake_tools([]), complete,
                     browser=False, classify=classify)
        self.assertTrue(any("What is this question about?" in prompt for prompt in written))
        self.assertFalse(any("What is this question about?" in prompt for prompt in checked))

    def test_after_the_subject_search_the_notes_are_checked_again_with_thinking(self):
        quick_checks, written = [], []

        def classify(prompt):
            quick_checks.append(prompt)
            return "MISSING"

        def complete(prompt):
            written.append(prompt)
            if "What is this question about?" in prompt:
                return "1923 FA Cup final"
            if "check the notes against the question" in prompt:
                return "WRONG: It was at Wembley, not in Dudley."
            return "The 1923 final was at Wembley [1]."
        answer, _ = research.run("what was the attendance at the 1923 FA Cup final replay in Dudley?", fake_tools([]),
                                 complete, browser=False, classify=classify)
        self.assertEqual(len(quick_checks), 1)
        self.assertIn("It was at Wembley, not in Dudley.", written[-1])
        self.assertIn("Wembley", answer)

    def test_a_sound_question_gets_no_correction(self):
        prompts = []

        def complete(prompt):
            prompts.append(prompt)
            return "FINE" if "check the notes against the question" in prompt else "Spain won [1]."
        research.run("who won the last world cup?", fake_tools([]), complete, browser=False)
        self.assertNotIn("gets something wrong", prompts[-1])

    def test_when_nothing_answers_it_searches_the_subject_then_says_so_rather_than_guess(self):
        calls, prompts = [], []

        def complete(prompt):
            prompts.append(prompt)
            if "check the notes against the question" in prompt:
                return "FINE\n\nThe notes do not contain any information about the attendance."
            if "What is this question about?" in prompt:
                return "1923 FA Cup final"
            return "25,090 excited fans [3]."
        answer, _ = research.run("what was the attendance at the 1923 FA Cup final replay in Dudley?", fake_tools(calls), complete,
                                 browser=False)
        searches = [args["query"] for name, args in calls if name == "research__web_search"]
        self.assertEqual(searches[-1], "1923 FA Cup final")
        self.assertIn("couldn't find a reliable answer", answer)
        self.assertNotIn("25,090", answer)

    def test_the_subject_search_can_show_the_premise_is_wrong(self):
        checks = []

        def complete(prompt):
            if "check the notes against the question" in prompt:
                checks.append(prompt)
                return "MISSING" if len(checks) == 1 else "WRONG: The 1923 final was at Wembley and had no replay."
            if "What is this question about?" in prompt:
                return "1923 FA Cup final"
            return "There was no replay [1][4]."
        answer, _ = research.run("attendance at the 1923 FA Cup final replay in Dudley?", fake_tools([]), complete, browser=False)
        self.assertTrue(answer.startswith("There was no replay [1]."))  # [4] pointed at no source

    def test_a_correction_must_contradict_something_in_the_question(self):
        self.assertTrue(research.contradicts_question("It was at Wembley, not Dudley.", "the 1923 final replay in Dudley"))
        self.assertTrue(research.contradicts_question("There was no replay in 1923.", "the 1923 FA Cup final replay"))
        self.assertTrue(research.contradicts_question("It was in Moscow, not Paris.", "who won the 2018 world cup final in Paris?"))
        self.assertFalse(research.contradicts_question("The Titanic sank on April 14-15, 1912, not in a single year.",
                                                       "what year did the Titanic sink?"))
        self.assertFalse(research.contradicts_question("The final was at Wembley.", "the 1923 final in Dudley"))

    def test_a_bare_wrong_is_asked_what_and_a_vague_one_ignored(self):
        def complete(prompt):
            if "check the notes against the question" in prompt:
                return "WRONG"
            if "gets a detail wrong" in prompt:
                return "It was in Moscow, not Paris."
            return "France won [1]."
        self.assertEqual(research.check_premise("who won the 2018 final in Paris", "notes", complete), ("wrong", "It was in Moscow, not Paris."))
        self.assertEqual(research.check_premise("what year did the Titanic sink", "notes",
                                                lambda prompt: "WRONG: it sank over two days, not in a single year."), ("fine", ""))

    def test_historical_years_are_web_questions(self):
        self.assertTrue(research.is_quick_question("what was the attendance at the 1923 FA Cup final?"))
        self.assertTrue(research.is_quick_question("what year did the Titanic sink"))

    def test_with_nothing_readable_it_answers_from_the_search_results(self):
        def no_pages(name, arguments):
            return SEARCH if name == "research__web_search" else ""
        answer, _ = research.run("research this properly: cake", no_pages, fake_model([]))
        self.assertNotIn("**Sources**", answer)
        self.assertTrue(answer)


MANY = "\n".join(f"{n}. Result {n}\n   https://site{n}.example/page\n   Snippet {n}." for n in range(1, 7))


def many_pages(calls, slow_urls=(), opened=None):
    def call_tool(name, arguments):
        calls.append((name, arguments))
        if name == "research__web_search":
            return about_the_query(MANY, arguments["query"])
        if name == "workspace__browser_open":
            if opened is not None:
                opened.set()
            if arguments["url"] in slow_urls:
                time.sleep(0.5)
        return f"Title: page\n\nPage text:\n{LONG}"
    return call_tool


def split_model(quick_prompts, full_prompts, verdict="FINE", note_pause=None):
    def classify(prompt):
        quick_prompts.append(prompt)
        if "Page text:" in prompt:
            if note_pause:
                note_pause()
            return "- Boil for 6 to 7 minutes"
        return verdict

    def complete(prompt):
        full_prompts.append(prompt)
        return "About 6 to 7 minutes for a soft yolk [1][2]."
    return classify, complete


def page_notes(prompts):
    return [prompt for prompt in prompts if "Page text:" in prompt]


def premise_checks(prompts):
    return [prompt for prompt in prompts if "check the notes against the question" in prompt]


class FasterDesktopResearch(unittest.TestCase):
    def test_page_notes_are_written_without_thinking_and_the_answer_with_it(self):
        quick, full = [], []
        classify, complete = split_model(quick, full)
        research.run("use your desktop to find out the best way to clean a cast iron pan", many_pages([]), complete, classify=classify)
        self.assertEqual(len(page_notes(quick)), research.PAGES)
        self.assertEqual(page_notes(full), [])
        self.assertIn("Write the answer for the user", full[-1])

    def test_a_fact_question_stops_reading_once_two_sources_answer_it(self):
        calls, quick, full = [], [], []
        classify, complete = split_model(quick, full)
        answer, _ = research.run("use your desktop to find out how long to boil an egg", many_pages(calls), complete, classify=classify)
        self.assertEqual(len(page_notes(quick)), research.ENOUGH_SOURCES)
        self.assertEqual(len(premise_checks(quick + full)), 1)
        self.assertIn("2. https://site2.example/page", answer)
        self.assertNotIn("3. https://site3.example/page", answer)

    def test_a_fact_question_keeps_reading_while_the_notes_do_not_answer_it(self):
        quick, full = [], []
        classify, complete = split_model(quick, full, verdict="MISSING")
        research.run("use your desktop to find out how long to boil an egg", many_pages([]), lambda prompt: (
            "boiled eggs" if "What is this question about?" in prompt else complete(prompt)), classify=classify)
        self.assertGreaterEqual(len(page_notes(quick)), research.PAGES)

    def test_how_to_and_best_questions_read_every_page(self):
        quick, full = [], []
        classify, complete = split_model(quick, full)
        research.run("use your desktop to find out the best way to clean a cast iron pan", many_pages([]), complete, classify=classify)
        self.assertEqual(len(page_notes(quick)), research.PAGES)

    def test_the_next_page_opens_while_the_model_writes_notes(self):
        second_page_opening = threading.Event()
        opened_pages = []

        def call_tool(name, arguments):
            if name == "workspace__browser_open":
                opened_pages.append(arguments["url"])
                if len(opened_pages) == 2:
                    second_page_opening.set()
            return many_pages([])(name, arguments)
        overlapped = []

        def wait_for_the_next_page():
            if not overlapped:
                overlapped.append(second_page_opening.wait(timeout=2))
        classify, complete = split_model([], [], note_pause=wait_for_the_next_page)
        research.run("use your desktop to find out the best way to clean a cast iron pan", call_tool, complete, classify=classify)
        self.assertEqual(overlapped, [True])

    def test_a_closed_reader_never_starts_a_browser_open(self):
        calls = []
        reader = research.PageReader(["https://site1.example/page"], many_pages(calls), browser=True)
        with research.BROWSER:
            reader.open_ahead(0)
            reader.close()
        reader.opener.shutdown(wait=True)
        self.assertNotIn("workspace__browser_open", [name for name, _ in calls])

    def test_only_one_page_is_open_in_the_browser_at_a_time_across_readers(self):
        release_the_hung_page = threading.Event()
        opening, most_at_once, calls = [], [], []

        def call_tool(name, arguments):
            calls.append((name, arguments))
            if name != "workspace__browser_open":
                return f"Title: page\n\nPage text:\n{LONG}"
            opening.append(arguments["url"])
            most_at_once.append(len(opening))
            if arguments["url"].endswith("hung"):
                release_the_hung_page.wait(timeout=2)
            opening.remove(arguments["url"])
            return f"Title: page\n\nPage text:\n{LONG}"
        with unittest.mock.patch.object(research, "PAGE_SECONDS", 0.1):
            first = research.PageReader(["https://site1.example/hung"], call_tool, browser=True)
            self.assertIn("Cream the butter", first.text(0, look_ahead=False))
            first.close()
            second = research.PageReader(["https://site2.example/page"], call_tool, browser=True)
            self.assertIn("Cream the butter", second.text(0, look_ahead=False))
            second.close()
        release_the_hung_page.set()
        second.opener.shutdown(wait=True)
        first.opener.shutdown(wait=True)
        self.assertEqual(max(most_at_once), 1)
        self.assertIn(("fetch__fetch", {"url": "https://site2.example/page", "max_length": research.PAGE_CHARS}), calls)

    def test_a_page_the_browser_is_slow_to_open_is_fetched_plainly_instead(self):
        calls = []
        classify, complete = split_model([], [])
        with unittest.mock.patch.object(research, "PAGE_SECONDS", 0.1):
            answer, _ = research.run("use your desktop to find out the best way to clean a cast iron pan",
                                     many_pages(calls, slow_urls={"https://site1.example/page"}), complete, classify=classify)
        fetched = [args["url"] for name, args in calls if name == "fetch__fetch"]
        self.assertIn("https://site1.example/page", fetched)
        self.assertIn("1. https://site1.example/page", answer)


if __name__ == "__main__":
    unittest.main()
