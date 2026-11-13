import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import tool_guide  # noqa: E402

TOOLS = ["research__web_search", "fetch__fetch"]


class LookUpCode(unittest.TestCase):
    def test_unfamiliar_code_names_and_errors_are_searched_first(self):
        text = tool_guide.guide(TOOLS)
        self.assertIn("library function, framework feature, command-line tool, flag or a library's error message", text)
        self.assertIn("call research__web_search FIRST", text)
        self.assertIn("Python 3.14 or Django 6", text)

    def test_a_name_that_cannot_be_confirmed_is_called_out_not_described(self):
        text = tool_guide.guide(TOOLS)
        self.assertIn("Never describe a function, flag or tool you haven't confirmed exists", text)
        self.assertIn("say plainly that it doesn't exist", text)

    def test_no_code_lookup_line_without_a_search_tool(self):
        self.assertNotIn("library function", tool_guide.guide(["fetch__fetch"]))


if __name__ == "__main__":
    unittest.main()
