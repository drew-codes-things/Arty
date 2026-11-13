import os
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import written_calls  # noqa: E402

CHROME_OPEN = {"type": "function", "function": {"name": "chrome_open", "description": "Open a page in the user's browser.",
                                                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}}
WEB_SEARCH = {"type": "function", "function": {"name": "research__web_search", "description": "Search the web.",
                                               "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}
SWALLOWED = {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 23}
YOUTUBE_INFO_CALL = '<tool_call>\n{"name": "youtube_info", "arguments": {"query": "lofi hip hop"}}\n</tool_call>'


def calls(name, arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]},
            "done": True, "eval_count": 30}


def said(text):
    return {"message": {"role": "assistant", "content": text}, "done": True, "eval_count": 9}


class FakeOllama:
    def __init__(self, chats, written=None, render_error=None):
        self.chats = iter(chats)
        self.written = list(written or [])
        self.render_error = render_error
        self.chat_bodies = []
        self.generate_bodies = []
        self.render_bodies = []

    def __call__(self, url, body=None, **kwargs):
        if url.endswith("/api/generate"):
            self.generate_bodies.append(body)
            return {"response": self.written.pop(0), "done": True}
        if body.get("_debug_render_only"):
            self.render_bodies.append(body)
            if self.render_error:
                raise self.render_error
            return {"_debug_info": {"rendered_template": "<|im_start|>assistant\n"}}
        self.chat_bodies.append(body)
        return next(self.chats)


class FakeHub:
    def __init__(self, tools=()):
        self.tools = list(tools)
        self.routes = {tool["function"]["name"]: "x" for tool in self.tools}
        self.calls = []

    def refresh(self):
        pass

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "Lofi hip hop radio, a live stream."


PLAY_REQUEST = "search YouTube for lofi hip hop and play the first result"
QUESTION = "tell me about lofi hip hop"
OPEN_YOUTUBE = calls("chrome_open", {"url": "https://www.youtube.com/results?search_query=lofi+hip+hop"})


def run_turn(ollama, hub=None, client_tools=(CHROME_OPEN,), request=PLAY_REQUEST, steps_taken=()):
    hub = hub or FakeHub()
    state = {"messages": [{"role": "user", "content": request}, *steps_taken], "context": {}}
    with unittest.mock.patch.object(brain, "hub", hub), \
            unittest.mock.patch.object(brain, "http_json", side_effect=ollama), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch.object(brain, "system_prompt", return_value=""), \
            unittest.mock.patch.object(brain, "current_context", return_value=""):
        outcome = brain.run_agent({"model": "m"}, state, client_tools=list(client_tools))
    return outcome, hub


def last_user_note(body):
    return next(message["content"] for message in reversed(body["messages"]) if message["role"] == "user")


class ACallToAToolItWasNotOffered(unittest.TestCase):
    def test_the_model_is_told_which_tool_was_missing_and_what_it_has(self):
        ollama = FakeOllama([SWALLOWED, OPEN_YOUTUBE], written=[YOUTUBE_INFO_CALL])
        outcome, _ = run_turn(ollama)
        self.assertEqual(outcome["status"], "client_tools")
        self.assertEqual(outcome["calls"][0]["function"]["name"], "chrome_open")
        note = last_user_note(ollama.chat_bodies[1])
        self.assertIn("youtube_info", note)
        self.assertIn("chrome_open", note)
        self.assertNotIn("Your reply was empty", note)

    def test_the_replay_asks_for_the_same_prompt_written_out_raw(self):
        ollama = FakeOllama([SWALLOWED, OPEN_YOUTUBE], written=[YOUTUBE_INFO_CALL])
        run_turn(ollama)
        sent = ollama.chat_bodies[0]
        self.assertEqual(ollama.render_bodies[0]["messages"], sent["messages"])
        self.assertEqual(ollama.render_bodies[0]["tools"], sent["tools"])
        replay = ollama.generate_bodies[0]
        self.assertTrue(replay["raw"])
        self.assertEqual(replay["prompt"], "<|im_start|>assistant\n")
        self.assertEqual(replay["options"], sent["options"])
        self.assertEqual(replay["model"], sent["model"])
        self.assertEqual(replay.get("shift"), sent.get("shift"))

    def test_the_call_as_it_was_written_stays_in_the_conversation_for_the_next_round(self):
        ollama = FakeOllama([SWALLOWED, OPEN_YOUTUBE], written=[YOUTUBE_INFO_CALL])
        run_turn(ollama)
        messages = ollama.chat_bodies[1]["messages"]
        self.assertEqual(messages[-2], {"role": "assistant", "content": YOUTUBE_INFO_CALL})

    def test_it_stops_explaining_after_two_tries_and_falls_back_to_the_usual_nudge(self):
        ollama = FakeOllama([SWALLOWED, SWALLOWED, SWALLOWED, said("Here you go.")], written=[YOUTUBE_INFO_CALL, YOUTUBE_INFO_CALL])
        outcome, _ = run_turn(ollama, request=QUESTION)
        self.assertEqual(len(ollama.generate_bodies), 2)
        self.assertIn("Your reply was empty", last_user_note(ollama.chat_bodies[3]))
        self.assertEqual(outcome["answer"], "Here you go.")


class ACallToAnOfferedToolThatOllamaDropped(unittest.TestCase):
    def test_a_client_tool_call_goes_back_to_the_app(self):
        written = '<tool_call>\n{"name": "chrome_open", "arguments": {"url": "https://www.youtube.com"}}\n</tool_call>'
        ollama = FakeOllama([SWALLOWED], written=[written])
        outcome, _ = run_turn(ollama)
        self.assertEqual(outcome["status"], "client_tools")
        self.assertEqual(outcome["calls"][0]["function"], {"name": "chrome_open", "arguments": {"url": "https://www.youtube.com"}})

    def test_a_short_name_for_a_hub_tool_runs_the_hub_tool(self):
        written = '<tool_call>\n{"name": "web_search", "arguments": {"query": "lofi hip hop"}}\n</tool_call>'
        ollama = FakeOllama([SWALLOWED, said("It's a long-running live stream.")], written=[written])
        outcome, hub = run_turn(ollama, hub=FakeHub([WEB_SEARCH]), client_tools=(), request="what is lofi hip hop radio?")
        self.assertEqual(hub.calls, [("research__web_search", {"query": "lofi hip hop"})])
        self.assertEqual(outcome["answer"], "It's a long-running live stream.")


class OtherEmptyReplies(unittest.TestCase):
    def test_text_that_was_only_held_back_comes_through(self):
        ollama = FakeOllama([SWALLOWED], written=["<think>\n\n</think>\n\nLofi hip hop is calm, slow music."])
        outcome, _ = run_turn(ollama, request=QUESTION)
        self.assertEqual(outcome["answer"], "Lofi hip hop is calm, slow music.")

    def test_a_reply_with_nothing_written_is_not_replayed(self):
        ollama = FakeOllama([{**SWALLOWED, "eval_count": 1}, said("Here you go.")])
        outcome, _ = run_turn(ollama, request=QUESTION)
        self.assertEqual(ollama.render_bodies, [])
        self.assertIn("Your reply was empty", last_user_note(ollama.chat_bodies[1]))
        self.assertEqual(outcome["answer"], "Here you go.")

    def test_when_the_replay_fails_the_usual_nudge_still_runs(self):
        ollama = FakeOllama([SWALLOWED, said("Here you go.")], render_error=urllib.error.URLError("gate down"))
        with self.assertLogs(brain.log, "WARNING") as logged:
            outcome, _ = run_turn(ollama, request=QUESTION)
        self.assertIn("gate down", "\n".join(logged.output))
        self.assertIn("Your reply was empty", last_user_note(ollama.chat_bodies[1]))
        self.assertEqual(outcome["answer"], "Here you go.")


def browser_step(name, result):
    return [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": {}}}]},
            {"role": "tool", "tool_name": name, "content": result}]


class AnAnswerAfterStepsOnTheirComputer(unittest.TestCase):
    def test_it_is_kept_rather_than_sent_back_as_if_no_tools_ran(self):
        steps = [*browser_step("chrome_open", "Opened it in tab 1."), *browser_step("chrome_click", "Video: not playing, an ad is showing.")]
        ollama = FakeOllama([said("I clicked the first video, but an ad is showing and the video hasn't started yet.")])
        outcome, _ = run_turn(ollama, steps_taken=steps)
        self.assertEqual(len(ollama.chat_bodies), 1)
        self.assertEqual(outcome["answer"], "I clicked the first video, but an ad is showing and the video hasn't started yet.")

    def test_with_no_steps_yet_an_unbacked_answer_is_still_sent_back(self):
        ollama = FakeOllama([said("I've opened YouTube and it's playing."), OPEN_YOUTUBE])
        outcome, _ = run_turn(ollama)
        self.assertIn("You haven't used any tools", last_user_note(ollama.chat_bodies[1]))
        self.assertEqual(outcome["status"], "client_tools")


class ReadingWrittenCalls(unittest.TestCase):
    def test_calls_are_read_from_the_written_text(self):
        text = ('<tool_call>\n{"name": "chrome_click", "arguments": {"item": 62}}\n</tool_call>\n'
                '<tool_call>\n{"name": "chrome_read", "arguments": "{}"}\n</tool_call>')
        self.assertEqual(written_calls.calls_written_in(text), [{"function": {"name": "chrome_click", "arguments": {"item": 62}}},
                                                                {"function": {"name": "chrome_read", "arguments": {}}}])

    def test_broken_json_is_not_a_call(self):
        self.assertEqual(written_calls.calls_written_in('<tool_call>\n{"name": "chrome_click", "arguments": {\n</tool_call>'), [])
        self.assertEqual(written_calls.calls_written_in('<tool_call>\n{"arguments": {}}\n</tool_call>'), [])

    def test_an_unclosed_call_at_the_end_is_still_read(self):
        self.assertEqual(written_calls.calls_written_in('<tool_call>\n{"name": "chrome_tabs", "arguments": {}}'),
                         [{"function": {"name": "chrome_tabs", "arguments": {}}}])

    def test_offered_names_match_exactly_or_by_a_bare_name(self):
        offered = ["chrome_open", "research__web_search", "workspace__browser_open", "fetch__fetch"]
        self.assertEqual(written_calls.offered_name("chrome_open", offered), "chrome_open")
        self.assertEqual(written_calls.offered_name("web_search", offered), "research__web_search")
        self.assertIsNone(written_calls.offered_name("workspace__web_search", offered))
        self.assertIsNone(written_calls.offered_name("youtube_play", offered))
        self.assertIsNone(written_calls.offered_name("", offered))

    def test_a_short_name_two_servers_share_is_not_guessed(self):
        self.assertIsNone(written_calls.offered_name("search", ["web__search", "files__search"]))

    def test_only_a_reply_with_written_tokens_and_tools_offered_counts_as_swallowed(self):
        self.assertTrue(written_calls.looks_swallowed(SWALLOWED, [CHROME_OPEN]))
        self.assertFalse(written_calls.looks_swallowed(SWALLOWED, []))
        self.assertFalse(written_calls.looks_swallowed({**SWALLOWED, "eval_count": 2}, [CHROME_OPEN]))
        self.assertFalse(written_calls.looks_swallowed(said("Hi."), [CHROME_OPEN]))
        with_calls = {**SWALLOWED, "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x"}}]}}
        self.assertFalse(written_calls.looks_swallowed(with_calls, [CHROME_OPEN]))

    def test_the_note_names_the_missing_tool_and_every_offered_one(self):
        note = written_calls.missing_tool_note(["youtube_play"], ["chrome_open", "chrome_click"])
        for name in ("youtube_play", "chrome_open", "chrome_click"):
            self.assertIn(name, note)


if __name__ == "__main__":
    unittest.main()
