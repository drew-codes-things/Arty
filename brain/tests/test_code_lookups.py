import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser_requests import lookups_run_for  # noqa: E402

SEARCH = "research__web_search"
SEARCHED_FIRST = (
    "How do I use requests.get_json_safely() in Python to fetch JSON from a URL with a 5 second timeout?",
    "How do I use Python 3.14 template strings (t-strings) to build an HTML snippet with the user's name escaped?",
    "How do I run a background task with the tasks framework built into Django 6? Show the code.",
    "What flags does the kubefrost CLI take for snapshotting a Kubernetes namespace?",
    "My Python script fails at client = OpenAI() with TypeError: Client.__init__() got an unexpected keyword "
    "argument 'proxies'. How do I fix it?",
    "why do I get ModuleNotFoundError: No module named 'distutils' on Node 22?",
)
ANSWERED_FROM_KNOWLEDGE = (
    "Explain the difference between TCP and UDP in a few sentences.",
    "What does Python's enumerate() function do? Give a short example.",
    "write a python function that checks whether a string is a palindrome, ignoring case and spaces",
    "what does `git rebase -i HEAD~3` do?",
    "how do I kill all python processes on linux?",
    "explain what DNS is",
    "I'm going to Edinburgh in March for a few days.",
    "my python3 script is slow, any general tips?",
)


class CodeLookups(unittest.TestCase):
    def test_new_versions_named_functions_cli_flags_and_errors_are_searched_before_answering(self):
        for said in SEARCHED_FIRST:
            self.assertIn(SEARCH, lookups_run_for(said), said)

    def test_ordinary_coding_and_chat_questions_are_not_searched(self):
        for said in ANSWERED_FROM_KNOWLEDGE:
            self.assertNotIn(SEARCH, lookups_run_for(said), said)


if __name__ == "__main__":
    unittest.main()
