import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-task-history-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import agent_loop  # noqa: E402
import background_tasks  # noqa: E402
import connections  # noqa: E402
import store  # noqa: E402
from test_review_tonight import RULES, FakeHub, FakeOllama, calls, said  # noqa: E402

PERSONA_MARKER = "You are Arty, and the user's sister is called Ella."
RESTART = ("homelab__restart_guest", {"vmid": 103})
CONFIG = {"model": "m", "approval": RULES, "conversations_dir": _scratch}


class TaskHistoryLeavesOutWhatTheRunRebuilds(unittest.TestCase):
    def setUp(self):
        store.init()
        self.hub = FakeHub(("homelab__restart_guest",))

    def process(self, task, ollama):
        with mock.patch.object(connections, "hub", self.hub), \
                mock.patch.object(connections, "http_json", side_effect=ollama), \
                mock.patch.object(connections, "load_config", return_value=CONFIG), \
                mock.patch.object(brain.models, "budget_for", return_value=0), \
                mock.patch.object(brain.models, "find", return_value=None), \
                mock.patch.object(agent_loop, "system_prompt", return_value=PERSONA_MARKER), \
                mock.patch.object(agent_loop, "current_context", return_value=""), \
                mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools), \
                mock.patch.object(brain.notify, "send"):
            background_tasks.process_task(store.get_task(task["id"]))
        return store.get_task(task["id"])

    def test_a_task_waiting_for_approval_saves_no_prompt_or_tool_list(self):
        task = store.create_task("Fix Plex", "Restart the Plex guest", "api")
        waiting = self.process(task, FakeOllama([calls(RESTART)]))
        self.assertEqual(waiting["status"], "waiting")
        self.assertNotIn("prefix", waiting["history"])
        shown = json.dumps(background_tasks.public_task(waiting, with_history=True))
        self.assertNotIn(PERSONA_MARKER, shown)
        self.assertNotIn("homelab__restart_guest\", \"description", shown)

    def test_an_approved_task_carries_on_with_the_prompt_rebuilt(self):
        task = store.create_task("Fix Plex", "Restart the Plex guest", "api")
        waiting = self.process(task, FakeOllama([calls(RESTART)]))
        store.update_task(task["id"], status="running", waiting={**waiting["waiting"], "decision": "approve", "note": ""})
        resumed = FakeOllama([said("Restarted Plex, and it is answering again.")])
        done = self.process(task, resumed)
        self.assertEqual(done["status"], "done")
        self.assertIn("Restarted Plex", done["result"])
        self.assertIn(("homelab__restart_guest", {"vmid": 103}), self.hub.calls)
        rebuilt = resumed.chat_bodies[0]["messages"][0]
        self.assertEqual(rebuilt["role"], "system")
        self.assertTrue(rebuilt["content"].startswith(PERSONA_MARKER))
        self.assertNotIn("prefix", done["history"])
        self.assertIn("The user approved.", json.dumps(done["history"]["messages"]))

    def test_history_saved_before_the_fix_is_read_back_without_it(self):
        task = store.create_task("Old", "Old task", "api")
        old_history = {"messages": [{"role": "user", "content": "hi"}], "context": {}, "groups": ["homelab"],
                       "prefix": {"model": "m", "messages": [{"role": "system", "content": PERSONA_MARKER}], "tools": []},
                       "rounds": [{"model": "m"}]}
        with store.connect() as connection:
            connection.execute("UPDATE tasks SET history = ? WHERE id = ?", (json.dumps(old_history), task["id"]))
        history = store.get_task(task["id"])["history"]
        self.assertEqual(history, {"messages": [{"role": "user", "content": "hi"}], "context": {}, "groups": ["homelab"]})

    def test_saving_history_keeps_what_a_resumed_run_needs(self):
        task = store.create_task("Keep", "Keep task", "api")
        state = {"messages": [{"role": "user", "content": "hi"}], "context": {"failed": ["x"]}, "groups": ["homelab"],
                 "prefix": {"model": "m", "messages": [], "tools": []}, "rounds": [{"model": "m"}]}
        store.update_task(task["id"], history=state)
        self.assertEqual(store.get_task(task["id"])["history"],
                         {"messages": [{"role": "user", "content": "hi"}], "context": {"failed": ["x"]}, "groups": ["homelab"]})
        self.assertIn("prefix", state)


if __name__ == "__main__":
    unittest.main()
