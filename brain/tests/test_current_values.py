import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser_requests import SHIPPED_CONFIG, lookups_run_for  # noqa: E402

import research  # noqa: E402
from test_research import fake_model, fake_tools  # noqa: E402
import steering  # noqa: E402

CURRENT_VALUES = (
    "What is the Bank of England base rate right now?",
    "what's the UK inflation rate at the moment?",
    "what are mortgage rates like at the moment",
    "what's the price of gold today",
    "how much is bitcoin worth right now?",
    "what is the euro to pound exchange rate",
    "what's the current interest rate in the US",
    "how much does a PS5 cost now",
)
ORDINARY_CHAT = (
    "hi",
    "hello arty",
    "good morning!",
    "thanks, that's great",
    "how are you right now?",
    "what should I eat right now?",
    "I'm so tired right now",
    "what's a normal resting heart rate",
    "rate my poem: roses are red",
    "what time is it right now",
    "Explain the difference between TCP and UDP in a few sentences.",
    "What does Python's enumerate() function do? Give a short example.",
    "what are you doing at the moment?",
    "my heart rate is high today",
)


class CurrentValueQuestions(unittest.TestCase):
    def test_rates_prices_and_values_right_now_are_looked_up_on_the_web(self):
        for said in CURRENT_VALUES:
            self.assertTrue(research.is_quick_question(said), said)
            self.assertFalse(steering.matches_lookup(SHIPPED_CONFIG, said), said)

    def test_ordinary_chat_is_not_searched(self):
        for said in ORDINARY_CHAT:
            self.assertFalse(research.is_quick_question(said), said)
            self.assertNotIn("research__web_search", lookups_run_for(said), said)


class TodayInTheAnswer(unittest.TestCase):
    def test_the_researched_answer_knows_todays_date_so_a_recent_fact_is_given_as_current(self):
        prompts, calls = [], []
        research.run("What is the Bank of England base rate right now?", fake_tools(calls), fake_model(prompts), browser=False)
        answer_prompt = prompts[-1]
        self.assertIn(f"Today is {research.today_text()}", answer_prompt)
        self.assertIn("give it as the current answer with its date", answer_prompt)
        premise_prompt = next(prompt for prompt in prompts if "Reply with WRONG, MISSING or FINE first." in prompt)
        self.assertIn(f"Today is {research.today_text()}", premise_prompt)
        self.assertLess(premise_prompt.index("notes from each source"), premise_prompt.index("Today is"))


if __name__ == "__main__":
    unittest.main()
