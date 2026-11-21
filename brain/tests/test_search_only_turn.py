import unittest
import unittest.mock

import job_fakes

import agent_loop
import auto_choice
import connections


def hub_tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


HUB_TOOLS = [hub_tool(name) for name in ("research__web_search", "fetch__fetch", "memory__create_entities", "homelab__restart_guest",
                                         "workspace__run_command", "some_new_server__do_anything")]


class SearchOnlyTurn(unittest.TestCase):
    def setUp(self):
        job_fakes.clean_database()

    def offered(self, skip_groups):
        with unittest.mock.patch.object(connections.hub, "tools", HUB_TOOLS):
            tools = agent_loop.tools_for_turn({"hidden_tools": [], "tool_groups": {}}, agent_loop.AgentRun(skip_groups=skip_groups),
                                              False, ([], "", None))
        return {tool["function"]["name"] for tool in tools}

    def test_a_search_only_turn_is_offered_the_web_search_and_nothing_else(self):
        self.assertEqual(self.offered(("browser", agent_loop.SEARCH_ONLY)), {"research__web_search"})

    def test_an_ordinary_turn_still_gets_the_native_and_hub_tools(self):
        names = self.offered(("browser",))
        self.assertIn("weather", names)
        self.assertIn("some_new_server__do_anything", names)


class OutsideReadReport(unittest.TestCase):
    def test_the_turn_report_says_when_the_brain_read_outside_text(self):
        for used in (["research__web_search"], ["fetch__fetch"], ["read_web_page"], ["email_read"], ["weather", "check_price"]):
            self.assertIs(auto_choice.turn_report({}, None, used).get("read_outside"), True, used)

    def test_the_turn_report_stays_quiet_when_nothing_outside_was_read(self):
        for used in ([], ["weather"], ["calculate", "list_jobs"]):
            self.assertNotIn("read_outside", auto_choice.turn_report({}, None, used), used)


if __name__ == "__main__":
    unittest.main()
