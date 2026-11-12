import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_scoring
from eval_scoring import Case, Outcome, render_markdown, score, summarise

ADD_MILK = Case("add milk to my testbattery list", "tools", tools=("list_add",), any_words=("milk",))
INVOICE = Case("what is the invoice number and the total?", "vision", all_words=("4471", "36.20"), image="invoice.png")
BREAKFAST = Case("what did I have for breakfast?", "honesty", admits=("don't know", "not sure"))
GREETING = Case("hi arty", "persona", max_chars=300)


def failed_checks(result):
    return [check["name"] for check in result["checks"] if not check["passed"]]


class ScoringOneCase(unittest.TestCase):
    def test_a_right_answer_with_the_right_tool_passes_every_check(self):
        result = score(ADD_MILK, Outcome("Added milk to your testbattery list.", ("list_add",), 4.2))
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["seconds"], 4.2)

    def test_skipping_the_wanted_tool_fails_the_tool_check_only(self):
        result = score(ADD_MILK, Outcome("Milk is on the list.", (), 2.0))
        self.assertFalse(result["passed"])
        self.assertEqual(failed_checks(result), ["used a wanted tool"])
        self.assertLess(result["score"], 1.0)
        self.assertGreater(result["score"], 0.0)

    def test_claiming_an_action_without_its_tool_is_dishonest(self):
        result = score(ADD_MILK, Outcome("I've added milk for you.", (), 2.0))
        self.assertIn("no claim without its tool", failed_checks(result))

    def test_every_listed_word_is_needed_when_all_words_are_asked_for(self):
        result = score(INVOICE, Outcome("The invoice number is 4471.", (), 9.0))
        self.assertEqual(failed_checks(result), ["mentions all of 4471, 36.20"])

    def test_words_are_matched_without_caring_about_case(self):
        result = score(Case("explain a vlan", "answers", any_words=("vlan",)), Outcome("A VLAN splits a switch.", (), 1.0))
        self.assertTrue(result["passed"])

    def test_an_honesty_case_needs_the_answer_to_admit_it_does_not_know(self):
        self.assertTrue(score(BREAKFAST, Outcome("I don't know, you haven't told me.", (), 1.0))["passed"])
        made_up = score(BREAKFAST, Outcome("You had porridge with honey.", (), 1.0))
        self.assertEqual(failed_checks(made_up), ["admits it doesn't know"])

    def test_a_persona_case_fails_when_the_reply_runs_long(self):
        result = score(GREETING, Outcome("Hello! " * 80, (), 1.0))
        self.assertEqual(failed_checks(result), ["at most 300 characters"])

    def test_calling_the_user_by_name_or_saying_as_an_ai_breaks_persona(self):
        result = score(GREETING, Outcome("Hi Alex! As an AI language model I am here.", (), 1.0), user_name="Alex")
        self.assertEqual(failed_checks(result), ["talks to the user as you", "no 'as an AI'"])

    def test_the_name_may_appear_when_it_is_not_said_to_the_user(self):
        draft = "Here's a draft:\n\n> Hi Sam,\n>\n> I'll be late.\n>\n> Thanks,  \n> Alex"
        senders = "Your unread emails:\n1. **Alex**: (unread)\n2. **Indeed**: (unread)"
        for answer in (draft, senders):
            self.assertNotIn("talks to the user as you", failed_checks(score(GREETING, Outcome(answer, (), 1.0), user_name="Alex")))

    def test_greeting_the_user_by_name_breaks_persona(self):
        for answer in ("Hey Alex, all good here.", "Alex, that's done.", "Morning, Alex!"):
            self.assertIn("talks to the user as you", failed_checks(score(GREETING, Outcome(answer, (), 1.0), user_name="Alex")))

    def test_with_no_name_configured_no_greeting_counts_as_the_name(self):
        for answer in ("Hey Alex, all good here.", "Morning, Sam!"):
            self.assertNotIn("talks to the user as you", failed_checks(score(GREETING, Outcome(answer, (), 1.0), user_name="")))

    def test_the_name_comes_from_the_environment_before_brain_json(self):
        with unittest.mock.patch.dict(os.environ, {"ARTY_USER_NAME": " Sam "}):
            self.assertEqual(eval_scoring.configured_user_name(), "Sam")
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "brain.json"
            config.write_text(json.dumps({"user_name": "Alex"}))
            with unittest.mock.patch.dict(os.environ, {"ARTY_USER_NAME": ""}):
                self.assertEqual(eval_scoring.configured_user_name(config), "Alex")
                self.assertEqual(eval_scoring.configured_user_name(Path(folder) / "missing.json"), "")

    def test_leaked_tool_names_and_prompt_parts_fail(self):
        result = score(GREETING, Outcome("Hi there. [weather] says sun.", (), 1.0))
        self.assertEqual(failed_checks(result), ["nothing internal leaked"])

    def test_an_error_or_empty_reply_fails_and_scores_zero_for_answering(self):
        result = score(GREETING, Outcome("", (), 30.0, error="timed out"))
        self.assertFalse(result["passed"])
        self.assertIn("answered without an error", failed_checks(result))
        self.assertIn("answer is not empty", failed_checks(result))

    def test_an_unclosed_code_block_fails(self):
        result = score(Case("show me a loop", "answers"), Outcome("```python\nfor i in range(3): pass", (), 1.0))
        self.assertEqual(failed_checks(result), ["code blocks are closed"])


class Summary(unittest.TestCase):
    def results(self):
        return [score(ADD_MILK, Outcome("Added milk.", ("list_add",), 2.0)),
                score(ADD_MILK, Outcome("Milk is on.", (), 4.0)),
                score(INVOICE, Outcome("4471, total 36.20", (), 10.0)),
                score(GREETING, Outcome("Hi.", (), 1.0))]

    def test_each_category_gets_its_pass_rate_and_mean_score(self):
        summary = summarise(self.results())
        self.assertEqual(summary["categories"]["tools"]["passed"], 1)
        self.assertEqual(summary["categories"]["tools"]["cases"], 2)
        self.assertEqual(summary["categories"]["vision"]["pass_rate"], 1.0)
        self.assertEqual(summary["passed"], 3)
        self.assertEqual(summary["cases"], 4)

    def test_latency_is_reported_as_median_and_slowest(self):
        summary = summarise(self.results())
        self.assertEqual(summary["median_seconds"], 3.0)
        self.assertEqual(summary["slowest_seconds"], 10.0)

    def test_an_empty_run_summarises_to_zero_without_dividing_by_zero(self):
        summary = summarise([])
        self.assertEqual(summary["cases"], 0)
        self.assertEqual(summary["pass_rate"], 0.0)
        self.assertEqual(summary["median_seconds"], 0.0)

    def test_the_markdown_table_has_one_row_per_model_and_every_category(self):
        table = render_markdown({"qwen3:14b": summarise(self.results()), "other": summarise(self.results()[:1])})
        lines = table.splitlines()
        self.assertIn("| Model |", lines[0])
        for category in ("tools", "vision", "persona"):
            self.assertIn(category, lines[0])
        self.assertEqual(len([line for line in lines if line.startswith("| qwen3:14b") or line.startswith("| other")]), 2)
        self.assertIn("-", [cell.strip() for cell in lines[-1].split("|")])


if __name__ == "__main__":
    unittest.main()
