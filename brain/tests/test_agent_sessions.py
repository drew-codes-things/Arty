import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_agent_mode import CLIENT_SYSTEM, CONFIG, READ_FILE, WEB_SEARCH, FakeOllama  # noqa: E402
from test_agent_options import CLIENT_FOLLOW_UPS, HARD_REQUEST, nudged_mid_task  # noqa: E402

import agent_sessions  # noqa: E402
import brain  # noqa: E402
import agent_loop  # noqa: E402
import agent_turns  # noqa: E402
import connections  # noqa: E402
import models  # noqa: E402

PLAIN_JOB = "fix the typo in the README title"
SETUP_SUMMARY = ("Summary: we looked at how to pip install the package and set up the environment, then planned "
                 "to install the docs tools and refactor the build.")
PLAIN_SUMMARY = "Summary: read two files, nothing changed yet."
COMPACTION_WORDINGS = (
    "Carry on with the work from there: the conversation got too long, so this is where it stands.",
    "Context was reset to save room. Pick the task back up.",
    "### Resume",
)
COMMAND_OUTPUT = "(I ran `pytest -q` myself. Output:)\nE   AssertionError: why does this fail to debug\n1 failed"


def compacted(lead, summary, request):
    return [{"role": "system", "content": CLIENT_SYSTEM + "\nRepository map: 3 new files."},
            {"role": "user", "content": f"{lead}\n\n{summary}\n\nThe request:\n{request}"}]


def after_command_output(request):
    return nudged_mid_task(request, "Carry on.") + [{"role": "assistant", "content": "Done."},
                                                   {"role": "user", "content": COMMAND_OUTPUT}]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ThinkingPerSession(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = {**CONFIG, "think": "auto", "conversations_dir": folder.name}
        self.clock = Clock()
        patches = [
            unittest.mock.patch.object(agent_loop, "agent_sessions", agent_sessions.AgentSessions(self.clock)),
            unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(self.config)),
            unittest.mock.patch.object(connections.hub, "refresh"),
            unittest.mock.patch.object(connections.hub, "tools", [WEB_SEARCH]),
            unittest.mock.patch.object(connections.hub, "routes", {"research__web_search": "research"}),
            unittest.mock.patch.object(brain, "archive_turn"),
            unittest.mock.patch.object(brain, "extract_memories"),
            unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
            unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *_: answer),
            unittest.mock.patch.object(agent_turns, "be_honest", side_effect=lambda answer, *_: answer),
            unittest.mock.patch.object(models, "find", return_value={"name": "qwen3:14b", "billions": 14.8, "tools": True,
                                                                        "vision": False, "embedding": False, "thinking": True}),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def thinks(self, messages):
        ollama = FakeOllama({"role": "assistant", "content": "Done."})
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            brain.run_turn(messages, "Arty", client_tools=[READ_FILE], agent=True)
        return ollama.bodies[0]["think"]

    def test_compaction_never_changes_the_choice_whatever_its_wording(self):
        for lead in COMPACTION_WORDINGS:
            for request, summary in ((PLAIN_JOB, SETUP_SUMMARY), (HARD_REQUEST, PLAIN_SUMMARY)):
                with self.subTest(lead=lead, request=request):
                    before = self.thinks(nudged_mid_task(request, "Carry on."))
                    self.assertEqual(self.thinks(compacted(lead, summary, request)), before)

    def test_a_second_compaction_still_keeps_it(self):
        before = self.thinks(nudged_mid_task(PLAIN_JOB, "Carry on."))
        first = compacted(COMPACTION_WORDINGS[1], SETUP_SUMMARY, PLAIN_JOB)
        self.thinks(first)
        second = compacted(COMPACTION_WORDINGS[2], f"{first[1]['content']}\n\nThen: debug the installer.", PLAIN_JOB)
        self.assertEqual(self.thinks(second), before)

    def test_command_output_the_user_ran_never_changes_it(self):
        self.assertFalse(self.thinks(nudged_mid_task(PLAIN_JOB, "Carry on.")))
        self.assertFalse(self.thinks(after_command_output(PLAIN_JOB)))

    def test_a_follow_up_of_any_wording_keeps_it(self):
        self.assertTrue(self.thinks(nudged_mid_task(HARD_REQUEST, "Carry on.")))
        for follow_up in CLIENT_FOLLOW_UPS + ["Okay, go.", "Please also check the other module."]:
            with self.subTest(follow_up=follow_up):
                self.assertTrue(self.thinks(nudged_mid_task(HARD_REQUEST, follow_up)))

    def test_a_later_request_in_the_same_session_keeps_the_sessions_choice(self):
        messages = nudged_mid_task(PLAIN_JOB, "Carry on.") + [{"role": "assistant", "content": "Fixed."},
                                                             {"role": "user", "content": HARD_REQUEST}]
        self.assertFalse(self.thinks(messages))

    def test_a_new_session_decides_for_itself(self):
        self.assertFalse(self.thinks(nudged_mid_task(PLAIN_JOB, "Carry on.")))
        self.assertTrue(self.thinks(nudged_mid_task(HARD_REQUEST, "Carry on.")))

    def test_a_session_idle_too_long_is_forgotten(self):
        self.assertFalse(self.thinks(nudged_mid_task(PLAIN_JOB, "Carry on.")))
        self.clock.now += agent_sessions.IDLE_SECONDS + 1
        self.assertTrue(self.thinks(compacted(COMPACTION_WORDINGS[0], SETUP_SUMMARY, PLAIN_JOB)))

    def test_chat_turns_still_decide_from_their_own_message(self):
        ollama = FakeOllama({"role": "assistant", "content": "Done."})
        with unittest.mock.patch.object(connections, "http_json", side_effect=ollama):
            brain.run_turn([{"role": "user", "content": PLAIN_JOB}, {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": HARD_REQUEST}], "Arty")
        self.assertTrue(ollama.bodies[0]["think"])


class Remembering(unittest.TestCase):
    def test_the_first_user_message_decides(self):
        sessions = agent_sessions.AgentSessions(Clock())
        self.assertEqual(sessions.deciding_request([PLAIN_JOB, COMMAND_OUTPUT, HARD_REQUEST]), PLAIN_JOB)

    def test_nothing_asked_gives_nothing(self):
        self.assertEqual(agent_sessions.AgentSessions(Clock()).deciding_request([]), "")

    def test_short_or_shared_text_does_not_join_unrelated_sessions(self):
        sessions = agent_sessions.AgentSessions(Clock())
        sessions.deciding_request([PLAIN_JOB, "yes", "go on"])
        self.assertEqual(sessions.deciding_request(["yes go on and debug the parser"]), "yes go on and debug the parser")

    def test_it_keeps_a_bounded_number_of_conversations(self):
        clock = Clock()
        sessions = agent_sessions.AgentSessions(clock)
        for index in range(agent_sessions.MAX_CONVERSATIONS + 10):
            clock.now += 1
            sessions.deciding_request([f"request number {index} about something"])
        self.assertEqual(len(sessions.by_conversation), agent_sessions.MAX_CONVERSATIONS)

    def test_a_session_keeps_a_bounded_number_of_fingerprints(self):
        sessions = agent_sessions.AgentSessions(Clock())
        sessions.deciding_request([f"message number {index} in a long session" for index in range(500)])
        session = next(iter(sessions.by_conversation.values())).session
        self.assertEqual(len(session.fingerprints), agent_sessions.MAX_FINGERPRINTS)

    def test_a_compaction_message_never_becomes_a_link_to_its_session(self):
        sessions = agent_sessions.AgentSessions(Clock())
        lead = COMPACTION_WORDINGS[0]
        sessions.deciding_request([PLAIN_JOB])
        sessions.deciding_request([f"{lead}\n\n{SETUP_SUMMARY}\n\n{PLAIN_JOB}", "Carry on now please."])
        sessions.deciding_request([HARD_REQUEST])
        self.assertEqual(sessions.deciding_request([f"{lead}\n\n{PLAIN_SUMMARY}\n\n{HARD_REQUEST}"]), HARD_REQUEST)


if __name__ == "__main__":
    unittest.main()
