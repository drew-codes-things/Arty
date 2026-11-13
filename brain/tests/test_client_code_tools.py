import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_code_in_the_reply import COMPUTER, CODE_REQUESTS, COMPUTER_REQUESTS  # noqa: E402
from test_review_tonight import FakeHub, FakeOllama, calls, run_agent, said, tool  # noqa: E402

DESKTOP_WORK_TOOLS = {"computer_run_command", "computer_read_file", "computer_write_file", "computer_edit_file",
                      "computer_list_files", "computer_search", "computer_find_files"}
DESKTOP_OTHER_TOOLS = {"computer_look", "computer_open", "chrome_open"}
DESKTOP_TOOLS = [tool(name) for name in sorted(DESKTOP_WORK_TOOLS | DESKTOP_OTHER_TOOLS)]
HOW_TO_REQUEST = "how do I list the biggest files in a folder on linux?"


def offered_on_the_first_round(text, replies=None):
    ollama = FakeOllama(replies or [said("```python\nprint(1)\nprint(2)\n```")] * 4)
    outcome, _ = run_agent(ollama, [{"role": "user", "content": text}], hub=FakeHub(COMPUTER), client_tools=DESKTOP_TOOLS)
    return {tool["function"]["name"] for tool in ollama.chat_bodies[0].get("tools") or []}, outcome


class DesktopToolsFollowTheCodeRule(unittest.TestCase):
    def test_asking_for_code_withholds_the_desktop_work_tools(self):
        for text in CODE_REQUESTS:
            offered, _ = offered_on_the_first_round(text)
            self.assertFalse(offered & DESKTOP_WORK_TOOLS, text)
            self.assertTrue(DESKTOP_OTHER_TOOLS <= offered, text)

    def test_asking_how_to_withholds_the_desktop_work_tools(self):
        offered, _ = offered_on_the_first_round(HOW_TO_REQUEST)
        self.assertFalse(offered & DESKTOP_WORK_TOOLS)

    def test_asking_to_run_save_install_or_build_still_offers_them(self):
        for text in COMPUTER_REQUESTS:
            offered, _ = offered_on_the_first_round(text)
            self.assertTrue(DESKTOP_WORK_TOOLS <= offered, text)

    def test_a_withheld_desktop_tool_called_anyway_is_not_sent_to_the_desktop(self):
        replies = [calls(("computer_write_file", {"path": "a.py", "content": "print(1)"})),
                   said("```python\nprint(1)\nprint(2)\n```")] + [said("```python\nprint(1)\nprint(2)\n```")] * 3
        _, outcome = offered_on_the_first_round(CODE_REQUESTS[0], replies)
        self.assertNotEqual(outcome["status"], "client_tools")

    def test_an_offered_desktop_tool_still_goes_to_the_desktop(self):
        replies = [calls(("computer_write_file", {"path": "~/bin/backup.sh", "content": "echo hi"}))]
        _, outcome = offered_on_the_first_round(COMPUTER_REQUESTS[2], replies)
        self.assertEqual(outcome["status"], "client_tools")


if __name__ == "__main__":
    unittest.main()
