import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-approvals-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import background_tasks  # noqa: E402
import connections  # noqa: E402
import policy  # noqa: E402

RESTART = "restarting CT 103 games"


def text_of(message):
    return message.get("content") or ""


def asked(reason, answer):
    return [{"role": "user", "content": "the minecraft server is down, can you fix it"},
            {"role": "assistant", "content": policy.approval_question(reason)},
            {"role": "user", "content": answer}]


class ChatYes(unittest.TestCase):
    def test_a_yes_to_the_question_approves_that_action(self):
        self.assertEqual(policy.approved_reason(asked(RESTART, "yes go ahead"), text_of), RESTART)

    def test_a_yes_to_anything_else_approves_nothing(self):
        conversation = [{"role": "user", "content": "what's the weather"},
                        {"role": "assistant", "content": "Sunny all day."},
                        {"role": "user", "content": "ok thanks, now restart CT 103"}]
        self.assertIsNone(policy.approved_reason(conversation, text_of))

    def test_anything_but_a_go_ahead_approves_nothing(self):
        self.assertIsNone(policy.approved_reason(asked(RESTART, "wait, which server?"), text_of))

    def test_a_question_asked_two_turns_ago_is_stale(self):
        conversation = asked(RESTART, "hang on") + [{"role": "assistant", "content": "Sure, take your time."},
                                                      {"role": "user", "content": "yes"}]
        self.assertIsNone(policy.approved_reason(conversation, text_of))

    def test_the_approval_only_covers_the_same_action(self):
        self.assertTrue(policy.covers(RESTART, RESTART))
        self.assertFalse(policy.covers(RESTART, "restarting VM 205 ai-desktop"))
        self.assertFalse(policy.covers(None, RESTART))

    def test_a_question_saved_with_its_line_breaks_flattened_is_still_the_same_action(self):
        reason = 'typing and submitting "Hi Sam,\n\nSee you at 5" into the Gmail window'
        saved_by_the_desktop = " ".join(policy.approval_question(reason).split())
        conversation = [{"role": "user", "content": "email Sam"},
                        {"role": "assistant", "content": saved_by_the_desktop},
                        {"role": "user", "content": "yes"}]
        self.assertTrue(policy.covers(policy.approved_reason(conversation, text_of), reason))
        self.assertFalse(policy.covers(policy.approved_reason(conversation, text_of), reason.replace("5", "6")))


SELF_HEAL = {"kind": "approval", "message": "restart games",
             "call": {"name": "homelab__restart_guest", "arguments": {"vmid": 103}},
             "recheck": {"address": "192.0.2.12", "port": 25565}}


def diagnosis(fix, summary="games is running but not answering. Restarting it should fix it."):
    return json.dumps({"summary": summary, "fix": fix})


class SelfHealApprovals(unittest.TestCase):
    def decide(self, diagnose_result):
        calls = []

        def call(name, arguments):
            calls.append(name)
            return diagnose_result if name == "homelab__diagnose" else "Restarted CT 103."

        state = {"messages": [], "context": {}}
        with unittest.mock.patch.object(connections.hub, "call", call):
            background_tasks.apply_decision(state, SELF_HEAL, "approve", "")
        return calls, state["messages"][-1]["content"]

    def test_still_broken_runs_the_approved_fix(self):
        calls, result = self.decide(diagnosis({"action": "restart", "vmid": 103, "name": "games"}))
        self.assertEqual(calls, ["homelab__diagnose", "homelab__restart_guest"])
        self.assertIn("Restarted CT 103.", result)

    def test_answering_again_by_then_leaves_it_alone(self):
        calls, result = self.decide(diagnosis(None, "192.0.2.12:25565 (games) is answering again now."))
        self.assertEqual(calls, ["homelab__diagnose"])
        self.assertIn("answering again", result)
        self.assertIn("not done", result.lower())

    def test_a_different_problem_by_then_needs_a_fresh_ok(self):
        calls, result = self.decide(diagnosis({"action": "start", "vmid": 103, "name": "games"}))
        self.assertEqual(calls, ["homelab__diagnose"])
        self.assertIn("ask again", result.lower())

    def test_an_unknown_state_is_said_and_the_approved_fix_still_runs(self):
        calls, result = self.decide(diagnosis(None, "Couldn't read Proxmox: timed out"))
        self.assertEqual(calls, ["homelab__diagnose", "homelab__restart_guest"])
        self.assertIn("couldn't check", result.lower())

    def test_approvals_without_a_recheck_run_as_before(self):
        calls = []
        state = {"messages": [], "context": {}}
        waiting = {key: value for key, value in SELF_HEAL.items() if key != "recheck"}
        with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: calls.append(name) or "done"):
            background_tasks.apply_decision(state, waiting, "approve", "")
        self.assertEqual(calls, ["homelab__restart_guest"])


if __name__ == "__main__":
    unittest.main()
