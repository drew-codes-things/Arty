import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BRAIN_FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_FOLDER))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-reply-markup-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import written_calls  # noqa: E402
from test_agent_mode import READ_FILE  # noqa: E402
import test_review_tonight  # noqa: E402
from test_review_tonight import FakeOllama, last_user_note, run_agent, said, tool  # noqa: E402
from test_round_timings import TimedOllama, answer, asked, offline_turns  # noqa: E402

MARKUP = test_review_tonight.RawMarkupIsNeverTheReply.MARKUP
CALL_MARKUP_ONLY = ("mistral style call", "broken call fragment", "xml style call")
CALL = '<tool_call>\n{"name": "chrome_open", "arguments": {"url": "https://www.youtube.com"}}\n</tool_call>'
SHOWN_IN_CODE = (f"Mistral writes a call like this:\n\n```\n{MARKUP['mistral style call']}\n```\n\n"
                 "and Qwen thinks first:\n\n```\n<think>\nThe user wants music.\n</think>\n```")
SHOWN_INLINE = "Qwen puts its reasoning between `<think>` and `</think>`, and Mistral starts calls with `[TOOL_CALLS]`."
EMPTY_NUDGE = "Your reply was empty"


def calls_with_text(text):
    return {"message": {"role": "assistant", "content": text,
                        "tool_calls": [{"function": {"name": "chrome_tabs", "arguments": {}}}]},
            "done": True, "eval_count": 30}


class NormalRepliesNeverSpeakCallMarkup(unittest.TestCase):
    def test_a_reply_of_only_markup_gets_the_empty_reply_nudge_and_then_no_answer(self):
        for name, text in MARKUP.items():
            ollama = FakeOllama([said(text), said(text)])
            outcome, _ = run_agent(ollama, [{"role": "user", "content": "tell me about lofi"}],
                                   client_tools=[tool("chrome_open")])
            self.assertIn(EMPTY_NUDGE, last_user_note(ollama.chat_bodies[1]), name)
            self.assertEqual(outcome["answer"], "", name)

    def test_after_the_nudge_a_clean_answer_is_given(self):
        ollama = FakeOllama([said(MARKUP["mistral style call"]), said("Lofi is calm music.")])
        outcome, _ = run_agent(ollama, [{"role": "user", "content": "tell me about lofi"}],
                               client_tools=[tool("chrome_open")])
        self.assertEqual(outcome["answer"], "Lofi is calm music.")

    def test_words_after_a_stray_closing_think_tag_are_kept(self):
        ollama = FakeOllama([said("The user wants a summary.\n</think>\n\nLofi is calm music.")])
        outcome, _ = run_agent(ollama, [{"role": "user", "content": "tell me about lofi"}])
        self.assertEqual(outcome["answer"], "Lofi is calm music.")

    def test_markup_shown_in_code_is_part_of_the_answer(self):
        for text in (SHOWN_IN_CODE, SHOWN_INLINE):
            outcome, _ = run_agent(FakeOllama([said(text)]), [{"role": "user", "content": "how do models write tool calls?"}])
            self.assertEqual(outcome["answer"], text)

    def test_words_sent_with_client_steps_carry_no_markup(self):
        outcome, _ = run_agent(FakeOllama([calls_with_text("[TOOL_CALLS]")]), [{"role": "user", "content": "what tabs are open?"}],
                               client_tools=[tool("chrome_tabs")])
        self.assertEqual(outcome["status"], "client_tools")
        self.assertEqual(outcome["content"], "")


class TheTurnFallsBackWhenOnlyMarkupIsLeft(unittest.TestCase):
    def setUp(self):
        offline_turns(self)

    def test_markup_twice_gives_the_brains_own_fallback(self):
        ollama = TimedOllama(answer(MARKUP["mistral style call"]), answer(MARKUP["broken call fragment"]))
        with unittest.mock.patch.object(brain, "http_json", side_effect=ollama):
            reply = brain.run_turn(asked("tell me about lofi"), "Arty", client_tools=[READ_FILE])[0]
        self.assertEqual(reply, brain.LOST_TRAIN_OF_THOUGHT)

    def test_streamed_pieces_never_carry_markup(self):
        sent = []
        ollama = TimedOllama(answer(f"{CALL}\nMore words here.\n"), answer("Lofi is calm music."))
        with unittest.mock.patch.object(brain, "http_json", side_effect=ollama), \
                unittest.mock.patch.object(brain, "stream_chat", side_effect=ollama.streamed):
            reply = brain.run_turn(asked("tell me about lofi"), "Arty", client_tools=[READ_FILE], on_text=sent.append)[0]
        streamed = "".join(sent)
        self.assertIsNone(written_calls.CALL_MARKUP.search(streamed), streamed)
        self.assertIsNone(written_calls.CALL_MARKUP.search(reply), reply)


class MarkupOutsideCode(unittest.TestCase):
    def test_markup_only_in_code_is_left_alone(self):
        self.assertEqual(written_calls.without_call_markup(SHOWN_IN_CODE), SHOWN_IN_CODE)
        self.assertEqual(written_calls.without_call_markup(SHOWN_INLINE), SHOWN_INLINE)

    def test_markup_outside_code_is_never_kept(self):
        for name in CALL_MARKUP_ONLY:
            self.assertEqual(written_calls.without_call_markup(f"{MARKUP[name]}\n\n```\nprint(1)\n```"), "", name)

    def test_plain_text_is_unchanged(self):
        self.assertEqual(written_calls.without_call_markup("Lofi is calm music."), "Lofi is calm music.")


if __name__ == "__main__":
    unittest.main()
