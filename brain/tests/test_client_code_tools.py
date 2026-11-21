import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_code_in_the_reply import COMPUTER, CODE_REQUESTS, COMPUTER_REQUESTS  # noqa: E402
from test_review_tonight import FakeHub, FakeEngine, calls, run_agent, said, tool  # noqa: E402

import run_requests  # noqa: E402

DEPLOYED_WINDOW = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())["options"]["num_ctx"]

DESKTOP_WORK_TOOLS = {"computer_run_command", "computer_read_file", "computer_write_file", "computer_edit_file",
                      "computer_list_files", "computer_search", "computer_find_files"}
DESKTOP_OTHER_TOOLS = {"computer_look", "computer_open", "chrome_open"}
DESKTOP_TOOLS = [tool(name) for name in sorted(DESKTOP_WORK_TOOLS | DESKTOP_OTHER_TOOLS)]
HOW_TO_REQUEST = "how do I list the biggest files in a folder on linux?"


def offered_on_the_first_round(text, replies=None, judge_says=False):
    engine = FakeEngine(replies or [said("```python\nprint(1)\nprint(2)\n```")] * 4)
    with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=judge_says):
        outcome, _ = run_agent(engine, [{"role": "user", "content": text}], hub=FakeHub(COMPUTER), client_tools=DESKTOP_TOOLS)
    return {tool["function"]["name"] for tool in engine.chat_bodies[0].get("tools") or []}, outcome


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
            offered, _ = offered_on_the_first_round(text, judge_says=True)
            self.assertTrue(DESKTOP_WORK_TOOLS <= offered, text)

    def test_a_withheld_desktop_tool_called_anyway_is_not_sent_to_the_desktop(self):
        replies = [calls(("computer_write_file", {"path": "a.py", "content": "print(1)"})),
                   said("```python\nprint(1)\nprint(2)\n```")] + [said("```python\nprint(1)\nprint(2)\n```")] * 3
        _, outcome = offered_on_the_first_round(CODE_REQUESTS[0], replies)
        self.assertNotEqual(outcome["status"], "client_tools")

    def test_a_withheld_hub_tool_called_anyway_is_not_run(self):
        replies = [calls(("workspace__run_command", {"command": "rm -rf ~/projects/old-logs"})),
                   said("```go\nfunc Reverse(s string) string { return s }\n```")] * 2
        engine = FakeEngine(replies)
        outcome, hub = run_agent(engine, [{"role": "user", "content": CODE_REQUESTS[3]}], hub=FakeHub(COMPUTER),
                                 options={"num_ctx": DEPLOYED_WINDOW})
        self.assertEqual([], hub.calls)
        self.assertIn("no tool called workspace__run_command", engine.chat_bodies[1]["messages"][-1]["content"])

    def test_an_offered_hub_tool_still_runs(self):
        replies = [calls(("workspace__run_command", {"command": "python3 primes.py"})), said("It printed 2 3 5 7.")]
        with unittest.mock.patch.object(run_requests, "judge_says_run", return_value=True):
            _, hub = run_agent(FakeEngine(replies), [{"role": "user", "content": COMPUTER_REQUESTS[0]}], hub=FakeHub(COMPUTER))
        self.assertEqual([("workspace__run_command", {"command": "python3 primes.py"})], hub.calls)

    def test_an_offered_desktop_tool_still_goes_to_the_desktop(self):
        replies = [calls(("computer_write_file", {"path": "~/bin/backup.sh", "content": "echo hi"}))]
        _, outcome = offered_on_the_first_round(COMPUTER_REQUESTS[2], replies)
        self.assertEqual(outcome["status"], "client_tools")


if __name__ == "__main__":
    unittest.main()
