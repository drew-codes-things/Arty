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
_scratch = tempfile.mkdtemp(prefix="arty-memory-prefix-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import agent_loop  # noqa: E402
import connections  # noqa: E402
import honesty  # noqa: E402
from test_review_tonight import FakeHub, FakeOllama, run_agent, said  # noqa: E402

SYSTEM = {"role": "system", "content": "You are Arty. Use your tools."}
TOOLS = [{"type": "function", "function": {"name": "weather", "description": "w", "parameters": {"type": "object", "properties": {}}}}]
CONFIG = {"model": "qwen3:14b", "options": {"num_ctx": 24576}}


class ExtractionKeepsTheChatCached(unittest.TestCase):
    def test_extraction_sends_the_chats_system_prompt_and_tools_first(self):
        bodies = []

        def ollama(url, body=None, **kwargs):
            bodies.append(body)
            return {"message": {"content": json.dumps({"facts": [{"about": "Sam", "fact": "has a sister called Ella"}]})}}
        saved = []
        with mock.patch.object(connections, "http_json", side_effect=ollama):
            brain.extract_memories(CONFIG, "my sister is called Ella", "Nice.", save=saved.extend,
                                   prefix={"messages": [SYSTEM], "tools": TOOLS})
        self.assertEqual(bodies[0]["messages"][0], SYSTEM)
        self.assertEqual(bodies[0]["messages"][-1]["role"], "user")
        self.assertIn("my sister is called Ella", bodies[0]["messages"][-1]["content"])
        self.assertEqual(bodies[0]["tools"], TOOLS)
        self.assertEqual(bodies[0]["format"], "json")
        self.assertEqual(saved, [{"about": "Sam", "fact": "has a sister called Ella"}])

    def test_without_a_prefix_extraction_is_unchanged(self):
        bodies = []
        with mock.patch.object(connections, "http_json", side_effect=lambda url, body=None, **kwargs: bodies.append(body) or
                               {"message": {"content": "{\"facts\": []}"}}):
            brain.extract_memories(CONFIG, "hello", "Hi.", save=lambda facts: None)
        self.assertEqual([m["role"] for m in bodies[0]["messages"]], ["user"])
        self.assertNotIn("tools", bodies[0])

    def test_the_tool_loop_keeps_the_last_prompts_system_message_and_tools(self):
        ollama = FakeOllama([said("Hello!")])
        state = {"messages": [{"role": "user", "content": "hi"}], "context": {}}
        with mock.patch.object(connections, "hub", FakeHub(("weather_tool",))), \
                mock.patch.object(connections, "http_json", side_effect=ollama), \
                mock.patch.object(brain.models, "budget_for", return_value=0), \
                mock.patch.object(brain.models, "find", return_value=None), \
                mock.patch.object(agent_loop, "system_prompt", return_value="You are Arty."), \
                mock.patch.object(agent_loop, "current_context", return_value=""), \
                mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools):
            brain.run_agent({"model": "m", "approval": {}}, state)
        sent = ollama.chat_bodies[-1]
        self.assertEqual(state["prefix"]["messages"], sent["messages"][:1])
        self.assertEqual(state["prefix"]["messages"][0]["role"], "system")
        self.assertEqual(state["prefix"]["tools"], sent.get("tools") or [])
        self.assertEqual(state["prefix"]["model"], sent["model"])

    def test_a_chat_turn_hands_its_prefix_to_the_extraction(self):
        prefix = {"messages": [SYSTEM], "tools": TOOLS}

        def agent(config, state, **kwargs):
            state["prefix"] = prefix
            return {"status": "done", "answer": "Nice to hear about Ella.", "tools_used": [], "raw": {}}
        started = []

        class Thread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                started.append((target, args, kwargs or {}))

            def start(self):
                pass
        config = {"model": "qwen3:14b", "user_name": "Sam", "options": {}, "auto_memory": True, "lookups": []}
        with mock.patch.object(connections, "load_config", return_value=config), \
                mock.patch.object(brain, "run_agent", side_effect=agent), \
                mock.patch.object(brain, "archive_turn"), \
                mock.patch.object(connections, "hub", FakeHub(())), \
                mock.patch.object(brain, "be_honest", side_effect=lambda answer, *args, **kwargs: answer), \
                mock.patch.object(brain.threading, "Thread", Thread):
            brain.run_turn([{"role": "user", "content": "my sister Ella starts uni next month"}])
        extraction = [entry for entry in started if entry[0] is brain.extract_memories]
        self.assertEqual(len(extraction), 1)
        target, args, kwargs = extraction[0]
        self.assertEqual(kwargs.get("prefix", args[4] if len(args) > 4 else None), prefix)


class ClaimCheckKeepsTheChatCached(unittest.TestCase):
    def setUp(self):
        config = mock.patch.object(connections, "load_config", return_value={"model": "qwen3:14b"})
        config.start()
        self.addCleanup(config.stop)

    def judged(self, bodies):
        def judge(url, body=None, **kwargs):
            bodies.append(body)
            prompt = body["messages"][-1]["content"]
            verdict = {"kind": "A"} if prompt.startswith("The person said") else {"false_claims": [{"text": "Done.", "correction": ""}]}
            return {"message": {"content": json.dumps(verdict)}}
        return mock.patch.object(connections, "http_json", side_effect=judge)

    def test_both_judges_send_the_chats_system_prompt_and_tools_first(self):
        bodies = []
        with self.judged(bodies):
            reply = brain.be_honest("Done.", [], [], request="Turn the lights off.", prefix={"messages": [SYSTEM], "tools": TOOLS})
        self.assertEqual(reply, honesty.HONEST_NOTHING_DONE)
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            self.assertEqual(body["messages"][0], SYSTEM)
            self.assertEqual(body["messages"][-1]["role"], "user")
            self.assertIn("Turn the lights off.", body["messages"][-1]["content"])
            self.assertEqual(body["tools"], TOOLS)
            self.assertEqual(body["format"], "json")

    def test_without_a_prefix_the_judges_send_only_their_prompt(self):
        bodies = []
        with self.judged(bodies):
            brain.be_honest("Done.", [], [], request="Turn the lights off.")
        self.assertEqual([[m["role"] for m in body["messages"]] for body in bodies], [["user"], ["user"]])
        self.assertTrue(all("tools" not in body for body in bodies))

    def test_a_prefix_written_for_another_model_is_not_sent(self):
        bodies = []
        with self.judged(bodies):
            brain.be_honest("Done.", [], [], request="Turn the lights off.", model="qwen3:14b",
                            prefix={"model": "mimo-v2.6:9b", "messages": [SYSTEM], "tools": TOOLS})
        self.assertEqual([[m["role"] for m in body["messages"]] for body in bodies], [["user"], ["user"]])
        self.assertTrue(all("tools" not in body for body in bodies))

    def test_extraction_skips_a_prefix_written_for_another_model(self):
        bodies = []
        with mock.patch.object(connections, "http_json", side_effect=lambda url, body=None, **kwargs: bodies.append(body) or
                               {"message": {"content": "{\"facts\": []}"}}):
            brain.extract_memories(CONFIG, "hello", "Hi.", save=lambda facts: None,
                                   prefix={"model": "mimo-v2.6:9b", "messages": [SYSTEM], "tools": TOOLS})
        self.assertEqual([m["role"] for m in bodies[0]["messages"]], ["user"])
        self.assertNotIn("tools", bodies[0])

    def test_a_chat_turn_hands_its_prefix_to_the_claim_check(self):
        prefix = {"messages": [SYSTEM], "tools": TOOLS}

        def agent(config, state, **kwargs):
            state["prefix"] = prefix
            return {"status": "done", "answer": "The lights are off.", "tools_used": [], "raw": {}}
        checked = []

        def check(answer, *args, **kwargs):
            checked.append(kwargs.get("prefix", args[6] if len(args) > 6 else None))
            return answer
        config = {"model": "qwen3:14b", "user_name": "Sam", "options": {}, "auto_memory": False, "lookups": []}
        with mock.patch.object(connections, "load_config", return_value=config), \
                mock.patch.object(brain, "run_agent", side_effect=agent), \
                mock.patch.object(brain, "archive_turn"), \
                mock.patch.object(connections, "hub", FakeHub(())), \
                mock.patch.object(brain, "be_honest", side_effect=check):
            brain.run_turn([{"role": "user", "content": "how are the lights looking"}])
        self.assertEqual(len(checked), 1)
        self.assertEqual(checked[0], prefix)


if __name__ == "__main__":
    unittest.main()
