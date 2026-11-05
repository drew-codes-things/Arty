"""Desktop research: search, read the pages, note each, write one sourced answer. Tools and model faked.
Run from brain/: python3 -m unittest discover tests"""
import sys
import unittest
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


def fake_tools(calls):
    def call_tool(name, arguments):
        calls.append((name, arguments))
        if name == "research__web_search":
            return SEARCH
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


if __name__ == "__main__":
    unittest.main()
