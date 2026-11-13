import json
import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import tool_guide  # noqa: E402
from native_tools import NATIVE_TOOLS  # noqa: E402

BRAIN_DIR = Path(__file__).resolve().parents[1]
SHIPPED_PROMPT = (BRAIN_DIR / "system-prompt.md").read_text()
SHIPPED_CONFIG = json.loads((BRAIN_DIR / "brain.json").read_text())
NAMED_LIVE = ("web_search", "youtube_info", "youtube_play", "youtube_search", "browser_open", "chrome_open", "convert_time")


def tool(name):
    return {"type": "function", "function": {"name": name, "description": name,
                                             "parameters": {"type": "object", "properties": {}}}}


def said(text):
    return {"message": {"role": "assistant", "content": text}, "done": True, "eval_count": 9}


def every_known_tool_name():
    grouped = [pattern for group in SHIPPED_CONFIG["tool_groups"].values() for pattern in group.get("tools", [])]
    names = set(NATIVE_TOOLS) | set(SHIPPED_CONFIG.get("always_tools", [])) | set(grouped) | set(NAMED_LIVE)
    bare = {name.rsplit("__", 1)[-1].rstrip("*").rstrip("_") for name in names}
    return sorted(name for name in bare if "_" in name or name in {"git", "fetch", "calculate"})


def names_in(text):
    return [name for name in every_known_tool_name() if re.search(rf"(?<![\w-]){re.escape(name)}(?!\w)", text)]


class FakeHub:
    def __init__(self, names=()):
        self.tools = [tool(name) for name in names]
        self.routes = {name: "x" for name in names}
        self.calls = []

    def refresh(self):
        pass

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "results"


class FakeOllama:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.bodies = []

    def __call__(self, url, body=None, **kwargs):
        self.bodies.append(body)
        return next(self.replies)


def run_turn(replies, request, hub_names=(), client_names=(), client_system="", agent=False):
    ollama = FakeOllama(replies)
    state = {"messages": [{"role": "user", "content": request}], "context": {}}
    config = {"model": "m", "persona": "", "system_prompt": SHIPPED_PROMPT}
    with unittest.mock.patch.object(brain, "hub", FakeHub(hub_names)), \
            unittest.mock.patch.object(brain, "http_json", side_effect=ollama), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch.object(brain, "NATIVE_TOOLS", {}), \
            unittest.mock.patch.object(brain, "current_context", return_value=""):
        outcome = brain.run_agent(config, state, client_tools=[tool(name) for name in client_names],
                                  client_system=client_system, agent=agent)
    return outcome, ollama.bodies


def system_text(body):
    return body["messages"][0]["content"]


def offered(body):
    return {entry["function"]["name"] for entry in body.get("tools") or []}


def unoffered_names(body):
    text = "\n".join(message.get("content") or "" for message in body["messages"] if message["role"] in ("system", "user"))
    real = offered(body)
    return [name for name in names_in(text) if not any(candidate.rsplit("__", 1)[-1] == name for candidate in real)]


DESKTOP_TOOLS = ("chrome_open", "chrome_read", "chrome_click", "chrome_find")
PLAY_REQUEST = "search YouTube for lofi hip hop and play the first result"


class TheShippedTextNamesNoTools(unittest.TestCase):
    def test_the_system_prompt_file_names_no_tool(self):
        self.assertEqual(names_in(SHIPPED_PROMPT), [])

    def test_background_instructions_name_no_tool(self):
        self.assertEqual(names_in(brain.BACKGROUND_INSTRUCTIONS), [])

    def test_the_time_line_names_no_tool(self):
        self.assertEqual(names_in(brain.time_line({})), [])


class TheDesktopBrowserRequest(unittest.TestCase):
    def setUp(self):
        _, self.bodies = run_turn([said("Playing it now.")] + [said("Done.")] * 3, PLAY_REQUEST,
                                  hub_names=("research__web_search", "memory__create_entities"), client_names=DESKTOP_TOOLS)

    def test_the_prompt_names_only_offered_tools(self):
        self.assertEqual(unoffered_names(self.bodies[0]), [])

    def test_search_is_named_with_its_server_prefix(self):
        self.assertIn("research__web_search", system_text(self.bodies[0]))
        self.assertNotRegex(system_text(self.bodies[0]), r"(?<!__)\bweb_search\b")

    def test_it_is_told_to_do_only_what_was_asked_on_websites(self):
        text = system_text(self.bodies[0])
        self.assertIn("Never click subscribe, like, follow", text)
        self.assertIn("one or two sentences", text)


class ARequestWithoutBrowserTools(unittest.TestCase):
    def test_no_browser_conduct_or_browser_names(self):
        _, bodies = run_turn([said("Hello.")], "hello there", hub_names=("research__web_search",))
        text = system_text(bodies[0])
        self.assertNotIn("Never click subscribe", text)
        self.assertEqual(unoffered_names(bodies[0]), [])

    def test_the_workspace_browser_gets_its_real_names(self):
        names = ("workspace__browser_open", "workspace__browser_click", "workspace__browser_type", "workspace__browser_read")
        _, bodies = run_turn([said("Done.")], "open the BBC website", hub_names=names)
        text = system_text(bodies[0])
        self.assertIn("workspace__browser_open, then workspace__browser_click", text)
        self.assertIn("Never click subscribe", text)


class TheNoToolsNudge(unittest.TestCase):
    REMINDER = "remind me to call mum at 5pm"

    def test_it_names_the_offered_search_tool_by_its_real_name(self):
        _, bodies = run_turn([said("I've set a reminder for 5pm."), said("Done.")], self.REMINDER,
                             hub_names=("research__web_search",))
        note = bodies[1]["messages"][-1]["content"]
        self.assertIn("You haven't used any tools", note)
        self.assertIn("research__web_search", note)
        self.assertEqual(unoffered_names(bodies[1]), [])

    def test_it_names_no_tool_when_none_of_its_tools_are_offered(self):
        _, bodies = run_turn([said("I've set a reminder for 5pm."), said("Done.")], self.REMINDER,
                             client_names=DESKTOP_TOOLS)
        note = bodies[1]["messages"][-1]["content"]
        self.assertIn("You haven't used any tools", note)
        self.assertEqual(names_in(note), [])


class AgentSessions(unittest.TestCase):
    def test_an_agent_session_keeps_its_own_prompt_exactly(self):
        own = "You are ArtyCode. Use read_file and run_command."
        with unittest.mock.patch.object(brain.models, "agent_options", return_value={}):
            _, bodies = run_turn([said("Done.")], "fix the bug", client_names=("read_file", "run_command"),
                                 client_system=own, agent=True)
        self.assertEqual(system_text(bodies[0]), own)


class TheGuide(unittest.TestCase):
    def test_nothing_offered_means_no_guide(self):
        self.assertEqual(tool_guide.guide([]), "")

    def test_a_list_line_names_only_the_offered_ones(self):
        text = tool_guide.guide(["calculate", "convert_units"])
        self.assertIn("use calculate, convert_units.", text)
        self.assertNotIn("date_maths", text)

    def test_a_line_needing_two_tools_waits_for_both(self):
        self.assertNotIn("create_entities", tool_guide.guide(["memory__create_entities"]))


if __name__ == "__main__":
    unittest.main()
