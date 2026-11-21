import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import app_actions  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import policy  # noqa: E402
from test_agent_mode import CONFIG  # noqa: E402
from test_round_timings import TimedEngine, answer, offline_turns  # noqa: E402

RESTART = "homelab__restart_guest"
REASON = "restart guest 103"


def restart_call():
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": RESTART, "arguments": {"vmid": 103}}}]}, {}


class ApprovalQuestionsReachTheChatWhole(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        approvals = unittest.mock.patch.object(app_actions, "STATE_FILE", Path(folder.name) / "app-actions.json")
        approvals.start()
        self.addCleanup(approvals.stop)
        config = {**CONFIG, "conversations_dir": folder.name, "approval": {"always": [RESTART]}}
        self.hub_calls = []
        tool = {"type": "function", "function": {"name": RESTART, "description": "restart a guest",
                                                 "parameters": {"type": "object", "properties": {"vmid": {"type": "integer"}}}}}
        for patch in (unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)),
                      unittest.mock.patch.object(connections.hub, "tools", [tool]),
                      unittest.mock.patch.object(connections.hub, "routes", {RESTART: "homelab"}),
                      unittest.mock.patch.object(connections.hub, "call",
                                                 side_effect=lambda name, arguments: self.hub_calls.append(name) or "restarted")):
            patch.start()

    def turn(self, conversation, *replies):
        with unittest.mock.patch.object(connections, "http_json", side_effect=TimedEngine(*replies)):
            return brain.run_turn(conversation)[0]

    def test_the_question_keeps_its_last_sentence_and_a_yes_then_runs_the_action(self):
        conversation = [{"role": "user", "content": "restart CT 103"}]
        question = self.turn(conversation, restart_call())
        self.assertEqual(question, policy.approval_question(REASON))
        self.assertEqual(self.hub_calls, [])
        self.turn(conversation + [{"role": "assistant", "content": question}, {"role": "user", "content": "yes"}],
                  restart_call(), answer("Restarted."))
        self.assertEqual(self.hub_calls, [RESTART])


class QuestionsAfterStreamedWords(unittest.TestCase):
    def test_a_question_after_words_already_streamed_still_counts(self):
        conversation = [{"role": "user", "content": "fix the server"},
                        {"role": "assistant", "content": "On it.\n\n" + policy.approval_question(REASON)},
                        {"role": "user", "content": "yes"}]
        self.assertEqual(policy.approved_reason(conversation, lambda message: message["content"]), REASON)


if __name__ == "__main__":
    unittest.main()
