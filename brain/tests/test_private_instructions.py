import base64
import codecs
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
import unittest
import unittest.mock
from pathlib import Path

BRAIN_FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN_FOLDER))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-private-instructions-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import agent_loop  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import hidden_notes  # noqa: E402
import honesty  # noqa: E402
import private_text  # noqa: E402
import prompting  # noqa: E402
import run_requests  # noqa: E402
import safety  # noqa: E402
import steering  # noqa: E402
import timing  # noqa: E402
from test_agent_mode import CONFIG, READ_FILE  # noqa: E402
from test_review_tonight import FakeEngine, FakeHub, said  # noqa: E402
from test_round_timings import TimedEngine, answer, offline_turns  # noqa: E402

SYSTEM_PROMPT = (BRAIN_FOLDER / "system-prompt.md").read_text().strip()
PERSONA = (BRAIN_FOLDER / "persona.example.md").read_text().strip()
PRIVATE_CONFIG = {**CONFIG, "persona": PERSONA, "system_prompt": SYSTEM_PROMPT}
TELEGRAM_NOTE = ("You are replying on Telegram. Keep replies short (under 1500 characters unless asked for more) "
                 "and use plain text: no markdown tables or headings.")
COMPUTER_PART = SYSTEM_PROMPT[SYSTEM_PROMPT.index("You have your own Debian"):SYSTEM_PROMPT.index("A message may end")].strip()
HONESTY_PART = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Be honest about"):SYSTEM_PROMPT.index("\nNever help anyone")].strip()
PERSONA_LINE = next(line for line in PERSONA.splitlines() if len(line.split()) > 40)
LONG_DESCRIPTION = ("Run a shell command on your own Debian computer as the ai user and return what it printed, "
                    "with the exit code; long output is cut to the last two thousand characters.")
COMMAND_TOOL = {"type": "function", "function": {"name": "workspace__run_command", "description": LONG_DESCRIPTION,
                                                 "parameters": {"type": "object", "properties": {
                                                     "command": {"type": "string", "description": "The command."}}}}}
DECLINE = re.compile(r"(?i)instructions private")
ASKED = "Go on then, carry on with that"
RUN = 12
EDIT_EVERY = 9


def words_of(text: str) -> list[str]:
    folded = "".join(char for char in unicodedata.normalize("NFKD", text)
                     if unicodedata.category(char) not in ("Mn", "Cf")).casefold()
    return re.findall(r"[^\W_]+", folded.replace("'", "").replace("’", ""))


def shares_a_run(reply: str, hidden: str, run: int = RUN) -> bool:
    hidden_words = words_of(hidden)
    runs = {tuple(hidden_words[start:start + run]) for start in range(len(hidden_words) - run + 1)}
    reply_words = words_of(reply)
    return any(tuple(reply_words[start:start + run]) in runs for start in range(len(reply_words) - run + 1))


def letters_of(text: str) -> str:
    return "".join(words_of(text))


def asked_with(text: str, system: str = "") -> list[dict]:
    return [*([{"role": "system", "content": system}] if system else []), {"role": "user", "content": text}]


def yaml_like(fields: dict) -> str:
    return "\n".join(f"{key}: {json.dumps(value)}" for key, value in fields.items())


def full_width(text: str) -> str:
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in text)


class WordStreamingEngine(TimedEngine):
    def streamed(self, body, on_text, timeout=600, on_thinking=None):
        reply = self(f"{brain.ARTY_ENGINE_URL}/api/chat", body)
        for piece in re.findall(r"\S+\s*", reply["message"]["content"]):
            on_text(piece)
        return reply


class PrivateTurns(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        config = {**connections.load_config(), "persona": PERSONA, "system_prompt": SYSTEM_PROMPT}
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)).start()

    def reply_to(self, written: str, said_text: str = ASKED, system: str = "", hub_tools=()) -> str:
        with unittest.mock.patch.object(connections.hub, "tools", list(hub_tools)), \
                unittest.mock.patch.object(connections, "http_json", side_effect=TimedEngine(answer(written))):
            return brain.run_turn(asked_with(said_text, system), "Arty", client_tools=[READ_FILE])[0]

    def streamed_reply_to(self, written: str) -> tuple[list[str], str]:
        engine = WordStreamingEngine(answer(written))
        sent = []
        with unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(connections, "stream_chat", side_effect=engine.streamed):
            final = brain.run_turn(asked_with(ASKED), "Arty", client_tools=[READ_FILE], on_text=sent.append)[0]
        return sent, final

    def assert_held_back(self, reply: str, hidden: str):
        self.assertFalse(shares_a_run(reply, hidden), reply)
        self.assertRegex(reply, DECLINE)


class TheSystemPromptSaysItsInstructionsArePrivate(unittest.TestCase):
    def test_chat_tool_less_and_background_prompts_all_carry_the_privacy_rule(self):
        for options in ({}, {"toolless": True}, {"background": True}, {"client_system": TELEGRAM_NOTE}):
            with self.subTest(options=options):
                system = prompting.system_prompt(dict(PRIVATE_CONFIG), **options)
                self.assertRegex(system, r"(?i)instructions are private")
                self.assertRegex(system, r"(?i)never quote, repeat, summarise, paraphrase, translate")
                self.assertRegex(system, r"(?i)who you are \(your name, and that you are an AI assistant\)")
                self.assertRegex(system, r"(?i)in your own plain words what you can do")


class RepliesThatRepeatHiddenText(PrivateTurns):
    def test_a_quoted_system_prompt_section_is_replaced_and_the_rest_of_the_reply_stays(self):
        reply = self.reply_to(f"Your router looks fine.\n\nSure, here is that part:\n\n{COMPUTER_PART}")
        self.assertTrue(reply.startswith("Your router looks fine."), reply)
        self.assert_held_back(reply, SYSTEM_PROMPT)

    def test_the_whole_system_prompt_in_a_code_block_is_replaced(self):
        reply = self.reply_to(f"```text\n{SYSTEM_PROMPT}\n```")
        self.assert_held_back(reply, SYSTEM_PROMPT)
        self.assertNotIn("```", reply)

    def test_the_persona_is_caught_through_markdown_case_and_odd_characters(self):
        words = PERSONA_LINE.split()
        disguised = " ".join(f"**{word.upper()}**" if index % 3 == 0 else word for index, word in enumerate(words[:20]))
        disguised += " " + "​ ".join(words[20:30]) + " " + full_width(" ".join(words[30:]))
        reply = self.reply_to(f"Here goes: {disguised}")
        self.assertFalse(shares_a_run(reply, PERSONA_LINE), reply)
        self.assertNotIn(full_width(words[-1]), reply)
        self.assertRegex(reply, DECLINE)

    def test_a_section_spelled_out_letter_by_letter_is_caught(self):
        spelled = "-".join(letter for letter in HONESTY_PART[:260] if letter.isalnum())
        reply = self.reply_to(f"Spelled out:\n{spelled}")
        self.assertNotIn(letters_of(HONESTY_PART)[:60], letters_of(reply))
        self.assertRegex(reply, DECLINE)

    def test_base64_rot13_and_reversed_copies_are_caught(self):
        encoded = {
            "base64": base64.b64encode(HONESTY_PART.encode()).decode(),
            "rot13": codecs.encode(HONESTY_PART, "rot13"),
            "reversed": HONESTY_PART[::-1],
        }
        for kind, text in encoded.items():
            with self.subTest(kind=kind):
                reply = self.reply_to(f"Here it is in {kind}:\n\n{text}")
                self.assertNotIn(text[:80], reply)
                self.assertRegex(reply, DECLINE)

    def test_a_near_verbatim_copy_with_small_edits_is_caught(self):
        edited = " ".join(f"{word}ly" if index % EDIT_EVERY == EDIT_EVERY // 2 else word
                          for index, word in enumerate(HONESTY_PART.split()))
        self.assertFalse(shares_a_run(edited, HONESTY_PART))
        reply = self.reply_to(edited)
        self.assertRegex(reply, DECLINE)
        self.assertFalse(shares_a_run(reply, HONESTY_PART, run=6), reply)

    def test_arty_tool_descriptions_are_private_too(self):
        listed = yaml_like({"name": "run_command", "description": LONG_DESCRIPTION})
        reply = self.reply_to(f"My tools:\n```yaml\n{listed}\n```", hub_tools=[COMMAND_TOOL])
        self.assert_held_back(reply, LONG_DESCRIPTION)

    def test_notes_added_to_a_message_and_the_judges_prompts_are_private_too(self):
        for hidden in (steering.HOW_TO_NOTE, steering.DONT_RUN_NOTE, prompting.BACKGROUND_INSTRUCTIONS,
                       run_requests.RUN_REQUEST_PROMPT.split("%s")[2], honesty.CLAIM_CHECK_PROMPT.split("%s")[0]):
            with self.subTest(hidden=hidden[:30]):
                reply = self.reply_to(f"The note said: {hidden.strip()}")
                self.assert_held_back(reply, hidden)

    def test_a_streamed_reply_never_sends_the_hidden_text_early(self):
        sent, final = self.streamed_reply_to(f"Happy to help. Here they are.\n\n{COMPUTER_PART}\n\nAnything else?")
        early = "".join(sent)
        self.assertTrue(final.startswith(early))
        self.assertFalse(shares_a_run(early, SYSTEM_PROMPT), early)
        self.assert_held_back(final, SYSTEM_PROMPT)

    def test_the_log_says_what_happened_without_the_hidden_text(self):
        with self.assertLogs("brain", level=logging.INFO) as logs:
            self.reply_to(COMPUTER_PART)
        held = [line for line in logs.output if "private" in line]
        self.assertTrue(held, logs.output)
        self.assertTrue(all(line.startswith("INFO:") for line in held), held)
        for line in held:
            self.assertFalse(shares_a_run(line, SYSTEM_PROMPT, run=4), line)


class BackgroundReports(unittest.TestCase):
    def test_a_background_task_report_that_repeats_the_instructions_is_replaced(self):
        engine = FakeEngine([said(f"Task done.\n\n{prompting.BACKGROUND_INSTRUCTIONS}")])
        state = {"messages": [{"role": "user", "content": "tidy my downloads folder"}], "context": {}}
        with unittest.mock.patch.object(connections, "hub", FakeHub()), \
                unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
                unittest.mock.patch.object(brain.models, "find", return_value=None), \
                unittest.mock.patch.object(agent_loop, "current_context", return_value=""), \
                unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools):
            outcome = brain.run_agent(dict(PRIVATE_CONFIG, approval={}), state, run=brain.AgentRun(background=True))
        self.assertTrue(outcome["answer"].startswith("Task done."), outcome["answer"])
        self.assertFalse(shares_a_run(outcome["answer"], prompting.BACKGROUND_INSTRUCTIONS), outcome["answer"])
        self.assertRegex(outcome["answer"], DECLINE)


ORDINARY_ANSWERS = (
    "I'm Arty, your AI assistant. I can help with coding, writing, planning and your homelab.",
    "Who you are matters less than what you do: keep the bin by the door.",
    "Samaritans are free on 116 123, any time.",
    "Talk to the user directly as you would to a friend, and keep it short.",
    "Your own computer can run Debian with an XFCE desktop if you like a light setup.",
    "If a tool fails, read the error, fix what breaks, then run it again.",
    "Keep replies concise unless someone asks for detail: that's a good rule for emails too.",
    "During my work experience week I learnt how servers talk to each other.",
    "Here's a haiku:\n\nRain on slate roofs falls\nthe valley hums in grey light\nkettle on, stay in",
    "```python\ndef reverse(text):\n    return text[::-1]\n```",
    "aGVsbG8gd29ybGQ= is 'hello world' in base64.",
    "Decoded from rot13 that says: hello world.",
    "N-E-C-E-S-S-A-R-Y",
    "The quick brown fox jumps over the lazy dog.",
)


class OrdinaryAnswersStayAsTheyAre(PrivateTurns):
    def test_answers_that_share_a_few_words_with_the_instructions_are_unchanged(self):
        for written in ORDINARY_ANSWERS:
            with self.subTest(written=written[:30]):
                self.assertEqual(self.reply_to(written).strip(), written.strip())

    def test_the_brains_own_fixed_replies_are_unchanged(self):
        for written in (agent_loop.MODEL_FAILED_ANSWER, honesty.HONEST_NOTHING_DONE, safety.WEAPONS_REPLY,
                        safety.DEFAULT_CRISIS_HELP, honesty.LOST_TRAIN_OF_THOUGHT, timing.HELP):
            with self.subTest(written=written[:30]):
                self.assertEqual(self.reply_to(written).strip(), written.strip())



ARTY_MD = ("Notes from /home/sam/shop/ARTY.md (follow them):\n# Shop project\n"
           "Run the tests with `make test` before every commit and never push straight to main without a review from Priya.\n"
           "The staging server is rebuilt every Monday morning at six, so never leave work only on staging.\n")
CLI_SYSTEM = "You are working inside arty-code, a terminal coding assistant, on the user's own computer.\n\n" + ARTY_MD
QUOTED_NOTE = ("Your ARTY.md says: Run the tests with `make test` before every commit and never push straight to main "
               "without a review from Priya. So I ran `make test` first: 14 passed.")
TYPED_PROMPT = ("Reference for this chat. My CV summary: Network technician with four years of experience running a home lab "
                "of Proxmox servers, Docker hosts and VLANs, now looking for a junior security analyst role in Bristol.")
CV_REPLY = ("Here's the line for the form: Network technician with four years of experience running a home lab of Proxmox "
            "servers, Docker hosts and VLANs, now looking for a junior security analyst role in Bristol.")
EDIT_TOOL = {"type": "function", "function": {"name": "edit_file", "description": (
    "Replace one piece of text in a file. Copy old_text exactly as read_file showed it, without the line numbers, and "
    "give the whole replacement as new_text; the edit is refused when old_text is not found exactly once."),
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
WRITE_FILE = {"type": "function", "function": {"name": "write_file", "description": "Write a file.", "parameters": {
    "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}}
PASTED = " ".join(SYSTEM_PROMPT.split()[200:240])
SENTENCES_OF_PERSONA = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n", PERSONA) if part.strip()]
PERSONA_SENTENCE = next(part for part in SENTENCES_OF_PERSONA
                        if len(part.split()) >= 14 and ":" not in part and part[-1] in ".!?" and not part.startswith(("-", "#")))
SECOND_PERSONA_SENTENCE = next(part for part in SENTENCES_OF_PERSONA
                               if part != PERSONA_SENTENCE and len(part.split()) >= 14 and ":" not in part
                               and part[-1] in ".!?" and not part.startswith(("-", "#")))
QUIET_LETTERS = 55


def single_line_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if "\n" not in part.strip() and len(part.split()) >= 40]


def short_runs(text: str, with_apostrophe: bool = False) -> list[str]:
    found = []
    for paragraph in single_line_paragraphs(text):
        raw = paragraph.split()
        for start in range(len(raw) - RUN + 1):
            window = " ".join(raw[start:start + RUN])
            words = private_text.words_in(window)
            if len(words) == RUN and sum(map(len, words)) < QUIET_LETTERS \
                    and all(word.strip("`*()\"") == word and word[-1] not in ".!?:;," for word in raw[start:start + RUN]) \
                    and (not with_apostrophe or any("'" in word for word in raw[start:start + RUN])):
                found.append(window)
    return found


SHORT_RUN = short_runs(SYSTEM_PROMPT)[0]
SHORT_RUN_WITH_APOSTROPHE = short_runs(SYSTEM_PROMPT, with_apostrophe=True)[0]


def tool_message(text: str, *calls) -> dict:
    return {"role": "assistant", "content": text, **({"tool_calls": list(calls)} if calls else {})}


class ScriptedEngine(TimedEngine):
    def __init__(self, *messages, thinking=""):
        super().__init__(*[(message, WARM_TIMINGS) for message in messages])
        self.thinking = thinking

    def streamed(self, body, on_text, timeout=600, on_thinking=None):
        reply = self(f"{brain.ARTY_ENGINE_URL}/api/chat", body)
        if on_thinking and self.thinking:
            for piece in re.findall(r"\S+\s*", self.thinking):
                on_thinking(piece)
        for piece in re.findall(r"\S+\s*", reply["message"].get("content") or ""):
            on_text(piece)
        return reply


WARM_TIMINGS = answer("")[1]


class ClientTurns(unittest.TestCase):
    def setUp(self):
        offline_turns(self)
        config = {**connections.load_config(), "persona": PERSONA, "system_prompt": SYSTEM_PROMPT}
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(config)).start()

    def turn(self, *messages, said_text=ASKED, system="", agent=False, client_tools=(READ_FILE,), stream=False,
             earlier=(), thinking="", hub_tools=()):
        engine = ScriptedEngine(*[tool_message(item) if isinstance(item, str) else item for item in messages] * 4,
                                thinking=thinking)
        conversation = [*([{"role": "system", "content": system}] if system else []), *earlier,
                        {"role": "user", "content": said_text}]
        sent, thought = [], []
        with unittest.mock.patch.object(connections.hub, "tools", list(hub_tools)), \
                unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(connections, "stream_chat", side_effect=engine.streamed):
            self.engine = engine
            reply, _, _, calls = brain.run_turn(conversation, "Arty", client_tools=list(client_tools), agent=agent,
                                                on_text=sent.append if stream else None,
                                                on_thinking=thought.append if thinking else None)
        return reply, "".join(sent), calls, "".join(thought)

    def reply_to(self, written, **options) -> str:
        return self.turn(written, **options)[0]


class TextTheClientSentIsNotHeldBack(ClientTurns):
    def test_arty_code_notes_quoted_back_in_agent_and_chat_mode_streamed_or_not(self):
        for agent, stream in ((True, False), (True, True), (False, False), (False, True)):
            with self.subTest(agent=agent, stream=stream):
                reply, early, _, _ = self.turn(QUOTED_NOTE, said_text="did you follow my notes?", system=CLI_SYSTEM,
                                               agent=agent, client_tools=(WRITE_FILE,), stream=stream)
                self.assertEqual(reply.strip(), QUOTED_NOTE)
                self.assertTrue(reply.startswith(early))

    def test_a_system_prompt_the_user_typed_is_quoted_back(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                reply = self.reply_to(CV_REPLY, said_text="give me the one-line summary for the form", system=TYPED_PROMPT,
                                      client_tools=(), stream=stream)
                self.assertEqual(reply.strip(), CV_REPLY)

    def test_an_apps_own_style_note_can_be_quoted(self):
        written = f"The app said: {TELEGRAM_NOTE}"
        self.assertEqual(self.reply_to(written, system=TELEGRAM_NOTE, client_tools=()).strip(), written)

    def test_text_pasted_into_a_user_message_comes_back_with_its_grammar_fixed(self):
        written = "Here it is with the grammar fixed: " + PASTED.replace(" the ", " this ", 1)
        reply = self.reply_to(written, said_text=f"Please fix the grammar in this paragraph of my notes:\n\n{PASTED}",
                              client_tools=())
        self.assertEqual(reply.strip(), written)

    def test_a_file_the_client_read_can_be_quoted(self):
        note = steering.HOW_TO_NOTE.strip()
        read = {"function": {"name": "read_file", "arguments": {"path": "brain/steering.py"}}}
        earlier = [{"role": "user", "content": "what does HOW_TO_NOTE say in brain/steering.py?"},
                   {"role": "assistant", "content": "", "tool_calls": [read]},
                   {"role": "tool", "content": f"HOW_TO_NOTE = {note!r}", "tool_name": "read_file"}]
        written = f"steering.py defines HOW_TO_NOTE as: {note}"
        reply = self.reply_to(written, said_text="go on", system="You are working inside arty-code.", agent=True,
                              earlier=earlier)
        self.assertEqual(reply.strip(), written)

    def test_the_clients_own_tool_descriptions_can_be_quoted(self):
        written = "edit_file works like this: " + EDIT_TOOL["function"]["description"]
        for agent in (True, False):
            with self.subTest(agent=agent):
                reply = self.reply_to(written, said_text="how does your edit tool work?",
                                      system="You are working inside arty-code.", agent=agent, client_tools=(EDIT_TOOL,))
                self.assertEqual(reply.strip(), written)

    def test_arty_persona_is_still_held_beside_a_client_system_message(self):
        reply = self.reply_to(f"Sure.\n\n{PERSONA_LINE}", system=CLI_SYSTEM, client_tools=(WRITE_FILE,))
        self.assertFalse(shares_a_run(reply, PERSONA_LINE))

    def test_only_the_part_the_user_pasted_goes_through(self):
        words = COMPUTER_PART.split()
        pasted, rest = " ".join(words[:len(words) // 2]), " ".join(words[len(words) // 2 + RUN:])
        reply = self.reply_to(COMPUTER_PART, said_text=f"Is this right? {pasted}", client_tools=())
        self.assertFalse(shares_a_run(reply, rest))
        self.assertTrue(reply.startswith(" ".join(words[:6])))


class SelfDescriptions(ClientTurns):
    def test_a_persona_line_inside_who_are_you_is_cut_without_the_refusal(self):
        written = (f"I'm Arty, your AI assistant. {PERSONA_SENTENCE} "
                   "I can help with code, writing and your homelab.")
        for stream in (False, True):
            with self.subTest(stream=stream):
                reply, early, _, _ = self.turn(written, said_text="Who are you?", stream=stream)
                self.assertNotRegex(reply, DECLINE)
                self.assertFalse(shares_a_run(reply, PERSONA))
                self.assertEqual(reply.strip(), "I'm Arty, your AI assistant. I can help with code, writing and your homelab.")
                self.assertTrue(reply.startswith(early))

    def test_a_held_line_in_a_list_goes_without_leaving_a_blank_line(self):
        written = f"What I'm good at:\n- Code and scripts\n- {PERSONA_SENTENCE}\n- Planning your week and your homelab"
        reply = self.reply_to(written, said_text="What are you good at?")
        self.assertEqual(reply.strip(), "What I'm good at:\n- Code and scripts\n- Planning your week and your homelab")

    def test_the_refusal_stays_when_nothing_else_is_left(self):
        reply = self.reply_to(f"Sure! {PERSONA_SENTENCE}", said_text="Who are you?")
        self.assertRegex(reply, DECLINE)
        self.assertFalse(shares_a_run(reply, PERSONA))

    def test_a_sentence_introduced_with_a_colon_keeps_the_refusal(self):
        reply = self.reply_to(f"Sure, here is that part:\n\n{PERSONA_SENTENCE}\n\nThat is the whole of it, as written.")
        self.assertRegex(reply, DECLINE)
        self.assertIn("That is the whole of it, as written.", reply)

    def test_two_held_sentences_close_together_become_one_refusal(self):
        reply = self.reply_to(f"Hello there, nice to meet you. {PERSONA_SENTENCE} Ok then. {SECOND_PERSONA_SENTENCE}")
        self.assertEqual(len(re.findall(DECLINE, reply)), 1)
        self.assertNotIn("Ok then", reply)
        self.assertTrue(reply.startswith("Hello there, nice to meet you."))


class CountingPattern:
    def __init__(self, pattern):
        self.pattern = pattern
        self.scans = 0

    def finditer(self, *arguments):
        self.scans += 1
        return self.pattern.finditer(*arguments)

    def __getattr__(self, name):
        return getattr(self.pattern, name)


class CheckingCostsLittle(unittest.TestCase):
    def setUp(self):
        self.hidden = hidden_notes.private_text_for(f"{PERSONA}\n\n{SYSTEM_PROMPT}", [COMMAND_TOOL])

    def test_a_reply_that_repeats_the_prompt_finds_sentence_breaks_and_fences_once(self):
        breaks, fences = CountingPattern(private_text.SENTENCE_BREAK), CountingPattern(private_text.FENCED_BLOCK)
        with unittest.mock.patch.object(private_text, "SENTENCE_BREAK", breaks), \
                unittest.mock.patch.object(private_text, "FENCED_BLOCK", fences):
            reply = private_text.without_hidden_text(f"{PERSONA}\n\n{SYSTEM_PROMPT}", self.hidden)
        self.assertFalse(shares_a_run(reply, SYSTEM_PROMPT))
        self.assertLessEqual(breaks.scans, 1)
        self.assertLessEqual(fences.scans, 1)

    def test_a_streamed_blob_is_decoded_once_it_stops_growing(self):
        encoded = base64.b64encode(HONESTY_PART.encode()).decode()
        sent = []
        stream = private_text.GuardedStream(sent.append, self.hidden)
        with unittest.mock.patch.object(private_text, "decoded", wraps=private_text.decoded) as decoding:
            for start in range(0, len(encoded), 4):
                stream(encoded[start:start + 4])
            stream(" There you go.")
        self.assertTrue(stream.held)
        self.assertLessEqual(decoding.call_count, 2)
        self.assertNotIn(encoded[:24], "".join(sent))

    def test_a_streamed_blob_that_hides_nothing_goes_out_once_it_ends(self):
        encoded = base64.b64encode(b"hello world, this is only a greeting and nothing more").decode()
        sent = []
        stream = private_text.GuardedStream(sent.append, self.hidden)
        for piece in (f"Here: {encoded[:30]}", encoded[30:], " and that is all."):
            stream(piece)
        self.assertFalse(stream.held)
        self.assertEqual("".join(sent), f"Here: {encoded} and that is all.")


NOT_RUN = re.compile(r"Not run: this call held part of Arty's own private instructions")
SIBLING_NOT_RUN = re.compile(r"Not run: another call in the same step")
FIRST_COMPUTER_SENTENCE = COMPUTER_PART.split(". ")[0].replace("\n", " ") + "."


def tool_results(messages: list) -> list[str]:
    return [message.get("content") or "" for message in messages if message.get("role") == "tool"]


def numbered(text: str) -> str:
    return "\n".join(f"{number}\t{line}" for number, line in enumerate(text.splitlines(), start=1))


def two_paragraphs(text: str) -> str:
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if len(part.split()) >= RUN]
    return f"{paragraphs[0]}\n\n{paragraphs[1]}"


class HubTurns(unittest.TestCase):
    def run_calls(self, *calls, said_text="save your notes to a file, then clear out the old build folders"):
        hub = FakeHub()
        engine = FakeEngine([{"message": tool_message("", *calls), "done": True}, said("Done.")])
        state = {"messages": [{"role": "user", "content": said_text}], "context": {}}
        native = unittest.mock.Mock(return_value=("Saved.", None))
        with unittest.mock.patch.object(connections, "hub", hub), \
                unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
                unittest.mock.patch.object(agent_loop, "run_native_safely", native), \
                unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
                unittest.mock.patch.object(brain.models, "find", return_value=None), \
                unittest.mock.patch.object(agent_loop, "current_context", return_value=""), \
                unittest.mock.patch.object(brain.policy, "untrusted_reason", return_value=None), \
                unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools):
            brain.run_agent(dict(PRIVATE_CONFIG, approval={}), state, run=brain.AgentRun(background=True))
        return hub.calls, native, state["messages"]


def command_call(command: str) -> dict:
    return {"function": {"name": "workspace__run_command", "arguments": {"command": command}}}


class CallsHoldingArtysTextAreNotRun(HubTurns):
    def test_a_hub_command_holding_the_instructions_is_not_run_and_the_model_is_told(self):
        commands = (f"echo '{FIRST_COMPUTER_SENTENCE}' > notes.txt",
                    f"cd /tmp/arty-notes && echo '{FIRST_COMPUTER_SENTENCE}' > rules.txt\ntrue",
                    f"cat > rules.txt <<'END'\n{FIRST_COMPUTER_SENTENCE}\nEND\necho committed")
        for command in commands:
            with self.subTest(lines=command.count("\n") + 1):
                ran, _, messages = self.run_calls(command_call(command))
                self.assertEqual(len(ran), 0, "the hub ran a command")
                self.assertEqual(len(tool_results(messages)), 1)
                self.assertTrue(NOT_RUN.search(tool_results(messages)[0]), "the model was not told")

    def test_nothing_else_in_that_step_runs(self):
        ran, _, messages = self.run_calls(command_call(f"echo '{FIRST_COMPUTER_SENTENCE}' > rules.txt"), command_call("true"))
        self.assertEqual(len(ran), 0, "the hub ran a command")
        results = tool_results(messages)
        self.assertTrue(NOT_RUN.search(results[0]) and SIBLING_NOT_RUN.search(results[1]), "the model was not told")

    def test_a_native_call_holding_the_instructions_is_not_run(self):
        ran, native, messages = self.run_calls(
            {"function": {"name": "note_save", "arguments": {"title": "rules", "text": COMPUTER_PART}}})
        self.assertEqual(native.call_count, 0, "the native tool ran")
        self.assertTrue(NOT_RUN.search(tool_results(messages)[0]), "the model was not told")

    def test_a_hidden_sentence_used_as_an_object_key_is_seen(self):
        call = {"function": {"name": "memory__create_entities",
                             "arguments": {"observations": {FIRST_COMPUTER_SENTENCE: "x"}}}}
        ran, _, messages = self.run_calls(call)
        self.assertEqual(len(ran), 0, "the hub ran the call")
        self.assertTrue(NOT_RUN.search(tool_results(messages)[0]), "the model was not told")

    def test_ordinary_calls_still_run_as_written(self):
        command = "cd /tmp/arty-notes && echo 'meeting moved to Friday' > notes.txt\nls -la"
        ran, native, messages = self.run_calls(command_call(command),
                                               {"function": {"name": "note_save", "arguments": {"title": "t", "text": "milk"}}})
        self.assertEqual(ran, [("workspace__run_command", {"command": command})])
        native.assert_called_once()
        self.assertFalse(any(NOT_RUN.search(result) or SIBLING_NOT_RUN.search(result) for result in tool_results(messages)))


class OtherChannels(ClientTurns):
    def test_a_client_tool_call_holding_arty_text_is_not_sent_and_the_model_is_told(self):
        note = steering.HOW_TO_NOTE.strip()
        for agent, hidden in ((False, PERSONA_LINE), (True, note)):
            shapes = {"object": {"path": "notes.txt", "content": hidden},
                      "json": json.dumps({"path": "notes.txt", "content": hidden}),
                      "not json": f"path=notes.txt content={hidden}"}
            for shape, arguments in shapes.items():
                with self.subTest(agent=agent, shape=shape):
                    call = {"function": {"name": "write_file", "arguments": arguments}}
                    reply, _, calls, _ = self.turn(tool_message("", call), "I left that out.", said_text="save that to notes.txt",
                                                   system=CLI_SYSTEM if agent else "", agent=agent,
                                                   client_tools=(WRITE_FILE,))
                    self.assertEqual(len(calls or []), 0, "the call was sent to the client")
                    self.assertTrue(reply.strip() == "I left that out.", "the answer after the refused call changed")
                    told = [result for body in self.engine.bodies for result in tool_results(body["messages"])]
                    self.assertTrue(told and all(NOT_RUN.search(result) for result in told), "the model was not told")

    def test_an_earlier_assistant_message_unlocks_nothing(self):
        earlier = [{"role": "user", "content": "tell me about yourself"}, {"role": "assistant", "content": PERSONA_LINE}]
        reply = self.reply_to(f"As I said before:\n\n{PERSONA_LINE}", said_text="say that again", earlier=earlier,
                              client_tools=())
        self.assertFalse(shares_a_run(reply, PERSONA_LINE), "an earlier assistant message unlocked the persona")
        self.assertTrue(DECLINE.search(reply), "no refusal line")


class NumberedFileReads(ClientTurns):
    def read_then(self, path: str, shown: str, *messages, said_text: str):
        read = {"function": {"name": "read_file", "arguments": {"path": path}}}
        earlier = [{"role": "user", "content": said_text}, {"role": "assistant", "content": "", "tool_calls": [read]},
                   {"role": "tool", "content": shown, "tool_name": "read_file"}]
        return self.turn(*messages, said_text="go on", system=CLI_SYSTEM, earlier=earlier,
                         client_tools=(READ_FILE, WRITE_FILE, EDIT_TOOL))

    def test_a_whole_file_written_back_after_a_numbered_read_is_sent_as_written(self):
        content = f"{PERSONA}\nKeep answers short on the phone.\n"
        call = {"function": {"name": "write_file", "arguments": {"path": "brain/persona.md", "content": content}}}
        for shown in (numbered(PERSONA), PERSONA):
            with self.subTest(numbered=shown != PERSONA):
                _, _, calls, _ = self.read_then("brain/persona.md", shown, tool_message("", call),
                                                said_text="add a line to brain/persona.md")
                self.assertEqual(len(calls or []), 1)
                self.assertTrue(brain.parse_arguments(calls[0]["function"]["arguments"])["content"] == content,
                                "the written content was changed")

    def test_an_edit_spanning_two_paragraphs_of_a_numbered_read_is_sent_as_written(self):
        old_text = two_paragraphs(PERSONA)
        call = {"function": {"name": "edit_file", "arguments": {"path": "brain/persona.md", "old_text": old_text,
                                                                "new_text": old_text + "\nOne more line."}}}
        _, _, calls, _ = self.read_then("brain/persona.md", numbered(PERSONA), tool_message("", call),
                                        said_text="add a line after the second paragraph of brain/persona.md")
        self.assertEqual(len(calls or []), 1)
        self.assertTrue(brain.parse_arguments(calls[0]["function"]["arguments"])["old_text"] == old_text,
                        "old_text was changed")

    def test_two_lines_of_a_numbered_read_can_be_quoted(self):
        written = f"Those two paragraphs read:\n\n{two_paragraphs(PERSONA)}"
        asked = "what do the first two paragraphs of brain/persona.md say?"
        with unittest.mock.patch.object(private_text, "without_hidden_text", side_effect=lambda text, *_, **__: text):
            unchecked = self.read_then("brain/persona.md", numbered(PERSONA), written, said_text=asked)[0]
        reply = self.read_then("brain/persona.md", numbered(PERSONA), written, said_text=asked)[0]
        self.assertTrue(reply == unchecked, "the privacy check changed the quoted lines")

    def test_a_numbered_read_of_another_file_unlocks_nothing(self):
        reply, _, _, _ = self.read_then("notes.txt", numbered("milk\neggs\nbread"), f"Sure.\n\n{PERSONA_LINE}",
                                        said_text="what is in notes.txt?")
        self.assertFalse(shares_a_run(reply, PERSONA_LINE), "the persona went through")

    def test_a_waiting_question_is_checked(self):
        turn = unittest.mock.Mock(private=hidden_notes.private_text_for(SYSTEM_PROMPT, []))
        outcome = {"status": "waiting", "waiting": {"kind": "user", "message": f"Before I go on:\n\n{COMPUTER_PART}"}}
        message = agent_loop.without_private_text(turn, outcome)["waiting"]["message"]
        self.assertFalse(shares_a_run(message, SYSTEM_PROMPT))
        self.assertRegex(message, DECLINE)

    def test_reasoning_streamed_to_an_agent_client_is_checked(self):
        note = steering.HOW_TO_NOTE.strip()
        _, _, _, thought = self.turn("Done.", said_text="hello", system=CLI_SYSTEM, agent=True, client_tools=(WRITE_FILE,),
                                     stream=True, thinking=f"My notes say: {ARTY_MD} and also {note} so I will answer.")
        self.assertTrue(thought.startswith("My notes say:"))
        self.assertFalse(shares_a_run(thought, note))


class DetectionRules(unittest.TestCase):
    def setUp(self):
        self.hidden = hidden_notes.private_text_for(SYSTEM_PROMPT, [])

    def assert_cut(self, disguised: str):
        reply = private_text.without_hidden_text(f"Here you go.\n\n{disguised}\n\nAnything else I can do for you today?",
                                                 self.hidden)
        self.assertNotIn(disguised, reply)
        self.assertIn("Anything else I can do for you today?", reply)

    def test_a_short_run_is_held_by_the_word_rule_alone(self):
        self.assert_cut(SHORT_RUN)

    def test_full_width_letters_alone_do_not_hide_a_run(self):
        self.assert_cut(full_width(SHORT_RUN))

    def test_zero_width_marks_alone_do_not_hide_a_run(self):
        self.assert_cut(" ".join("​".join(word) for word in SHORT_RUN.split()))

    def test_a_run_written_without_its_apostrophes_is_held(self):
        self.assert_cut(SHORT_RUN_WITH_APOSTROPHE.replace("'", ""))

    def test_a_run_that_ends_the_reply_is_held(self):
        reply = private_text.without_hidden_text(f"Sure thing, here is what I think about it all. {SHORT_RUN}", self.hidden)
        self.assertFalse(shares_a_run(reply, SHORT_RUN))

    def test_hex_and_spaced_hex_are_held(self):
        raw = HONESTY_PART.encode()
        for written in (raw.hex(), " ".join(f"{byte:02x}" for byte in raw)):
            with self.subTest(spaced=" " in written):
                reply = private_text.without_hidden_text(f"In hex:\n\n{written}\n\nThat's it.", self.hidden)
                self.assertNotIn(written[:48], reply)
                self.assertRegex(reply, DECLINE)

    def test_the_tool_guide_is_private_with_no_tools_offered(self):
        longest = max((part for _, part in hidden_notes.labelled("guide", hidden_notes.TOOL_GUIDE_TEMPLATES)),
                      key=lambda part: len(part.split()))
        self.assertGreaterEqual(len(longest.split()), RUN * 2)
        reply = private_text.without_hidden_text(f"My guide says:\n\n{longest.strip()}\n\nThat's all.", hidden_notes.private_text_for("", []))
        self.assertFalse(shares_a_run(reply, longest))

    def test_the_whole_sentence_around_a_run_goes(self):
        reply = private_text.without_hidden_text(
            f"My own words come first, then {SHORT_RUN} and my own words after. Another sentence of mine stays here.",
            self.hidden)
        self.assertEqual(reply.strip(), "Another sentence of mine stays here.")

    def test_text_already_sent_is_kept_as_it_was(self):
        sent = f"Intro words of mine. {SHORT_RUN}. Then this sentence starts "
        written = f"{sent}{COMPUTER_PART}"
        reply = private_text.without_hidden_text(written, self.hidden, keep=len(sent))
        self.assertTrue(reply.startswith(sent))
        self.assertFalse(shares_a_run(reply[len(sent):], SYSTEM_PROMPT))


def wrapped(text: str, width: int) -> str:
    return "\n".join(text[start:start + width] for start in range(0, len(text), width))


def three_lines(text: str, middle: int) -> str:
    half = (len(text) - middle) // 2
    return f"{text[:half]}\n{text[half:half + middle]}\n{text[half + middle:]}"


class EncodedCopiesInSeveralLines(PrivateTurns):
    def layouts(self) -> dict:
        encoded, hexed = base64.b64encode(HONESTY_PART.encode()).decode(), HONESTY_PART.encode().hex()
        return {"base64 at 76": wrapped(encoded, 76), "base64 at 64": wrapped(encoded, 64),
                "base64 at 200": wrapped(encoded, 200), "base64 long, 4, long": three_lines(encoded, 4),
                "base64 long, 6, long": three_lines(encoded, 6), "hex at 64": wrapped(hexed, 64),
                "hex long, 4, long": three_lines(hexed, 4), "hex long, 5, long": three_lines(hexed, 5)}

    def assert_held_whole(self, reply: str, written: str):
        self.assertTrue(DECLINE.search(reply), "no refusal line")
        left = [line for line in written.splitlines() if len(line) >= 16 and line in reply]
        self.assertEqual(len(left), 0, "lines of the copy were left")

    def test_one_check_holds_every_line_of_the_copy(self):
        hidden = hidden_notes.private_text_for(SYSTEM_PROMPT, [])
        for name, written in self.layouts().items():
            with self.subTest(layout=name):
                self.assert_held_whole(private_text.without_hidden_text(f"Here it is:\n{written}\nThat's all.", hidden),
                                       written)

    def test_a_whole_turn_holds_every_line_of_the_copy(self):
        for name, written in self.layouts().items():
            with self.subTest(layout=name):
                self.assert_held_whole(self.reply_to(f"Sure, encoded as you asked.\n\n{written}\n\nAnything else?"),
                                       written)


class QuietCutsReadRight(unittest.TestCase):
    def setUp(self):
        self.hidden = hidden_notes.private_text_for(PERSONA, [])

    def cut(self, text: str) -> str:
        reply = private_text.without_hidden_text(text, self.hidden)
        self.assertFalse(shares_a_run(reply, PERSONA), "a persona run was left")
        return reply

    def test_a_quiet_cut_that_joins_two_halves_of_a_line_is_checked_again(self):
        words = next(line.split() for line in PERSONA.splitlines()
                     if len(line.split()) >= 16 and not any(word[-1] in ".!?:" for word in line.split()[:16])
                     and SECOND_PERSONA_SENTENCE not in line)
        reply = self.cut(f"{' '.join(words[:8])}. {SECOND_PERSONA_SENTENCE} {' '.join(words[8:16])}")
        self.assertTrue(DECLINE.search(reply), "no refusal line")

    def test_a_colon_inside_the_sentence_keeps_the_refusal(self):
        reply = self.cut(f"My first rule is this: {PERSONA_SENTENCE} That is all there is to it really.")
        self.assertTrue(DECLINE.search(reply), "no refusal line")
        self.assertTrue("That is all there is to it really." in reply, "the last sentence went")

    def test_a_sentence_quoted_inside_another_keeps_the_refusal(self):
        reply = self.cut(f'My notes put it like this, "{PERSONA_SENTENCE}" and I think that sums me up rather well.')
        self.assertTrue(DECLINE.search(reply), "no refusal line")

    def test_a_short_lead_in_alone_keeps_the_refusal(self):
        self.assertTrue(DECLINE.search(self.cut(f"Sure, here you go! {PERSONA_SENTENCE}")), "no refusal line")

    def test_a_bold_sentence_takes_only_itself(self):
        after = "I also run at home on your own server and never phone anyone."
        reply = self.cut(f"About me, in short. **{PERSONA_SENTENCE}** {after}")
        self.assertTrue(after in reply, "the sentence after the bold one went")

    def test_a_numbered_list_item_left_empty_goes(self):
        reply = self.cut(f"Here is the list.\n1. Keep it short and friendly.\n2. {PERSONA_SENTENCE}\n"
                         "3. Ask before doing anything risky.")
        self.assertTrue(reply == "Here is the list.\n1. Keep it short and friendly.\n3. Ask before doing anything risky.",
                        "the list was not left as its other items")

    def test_a_table_row_keeps_the_refusal(self):
        reply = self.cut(f"| Part | Text |\n| --- | --- |\n| Voice | {PERSONA_SENTENCE} |\n| Name | Arty |")
        self.assertTrue(DECLINE.search(reply), "no refusal line")
        self.assertTrue("| Name | Arty |" in reply, "the next row went")


class EveryReplyPath(PrivateTurns):
    def test_a_reply_made_before_the_tool_loop_is_checked_too(self):
        for hidden in (COMPUTER_PART, PERSONA_LINE, LONG_DESCRIPTION):
            with self.subTest(hidden=hidden[:20]), \
                    unittest.mock.patch.object(brain, "arty_turn", return_value=(f"Here you go:\n\n{hidden}", [], {}, None)), \
                    unittest.mock.patch.object(connections.hub, "tools", [COMMAND_TOOL]):
                reply = brain.run_turn(asked_with(ASKED), "Arty")[0]
                self.assertFalse(shares_a_run(reply, hidden))
                self.assertRegex(reply, DECLINE)

    def test_a_guarded_stream_stops_at_the_first_hidden_piece_and_keeps_what_it_sent(self):
        sent = []
        stream = private_text.GuardedStream(sent.append, hidden_notes.private_text_for(SYSTEM_PROMPT, []))
        for piece in ("Happy to help. ", f"{COMPUTER_PART} ", "Anything else?"):
            stream(piece)
        self.assertTrue(stream.held)
        self.assertEqual(sent, ["Happy to help. "])
        self.assertEqual(stream.kept(f"Happy to help. {COMPUTER_PART} Anything else?"), len("Happy to help. "))


if __name__ == "__main__":
    unittest.main()
