import re

import code_check
import code_sandbox
import desktop_look
import docs_reading
import email_watch
import example_checks
import fresh_answer
import honesty
import lectures
import memory_facts
import memory_tidy
import private_check
import prompting
import reply_rules
import research
import run_requests
import sharing
import shortcuts
import shown_output
import steering
import style
import timing
import tool_guide
import window_fit
import written_calls
from archive import message_text
from private_text import NOTHING_KNOWN, HiddenText, KnownText, hidden_text

FORMAT_FIELD = re.compile(r"\{[^{}]*\}|%[sd]")
CLIENT_ROLES = ("system", "user", "tool")
SHOWN_TO_USERS = (timing.HELP,)

INSTRUCTION_TEMPLATES = (
    prompting.BACKGROUND_INSTRUCTIONS, prompting.PRIVATE_INSTRUCTIONS, prompting.MEMORY_HEADING, prompting.RECENT_HEADING,
    reply_rules.TOOLLESS_NOTE,
)
NOTE_TEMPLATES = (
    steering.WRITE_ONLY_NOTE, steering.WRITE_AND_RUN_NOTE, steering.RUN_THE_CODE_NOTE, steering.DONT_RUN_NOTE,
    steering.WRITE_ONLY_UNCHECKED_NOTE, steering.DONT_RUN_UNCHECKED_NOTE, steering.HOW_TO_NOTE, steering.READ_LINK_NOTE,
    steering.NOTHING_TO_SIGN_IN, steering.SEARCH_RESULTS_NOTE, steering.NOTHING_DONE_NOTE, steering.PRETEND_TOOLS_NOTE,
    steering.HALF_WRITTEN_CALL_NOTE, steering.EMPTY_REPLY_NOTE, steering.COURSEWORK_NOTE, steering.UNASKED_PRIVACY_NOTE,
    steering.HARMFUL_REWRITE_NOTE,
    reply_rules.FAILED_NOTE, docs_reading.DOCS_NOTE,
    docs_reading.UNCONFIRMED_NOTE, sharing.SENT_NOTE, window_fit.LEFT_OUT_ASK, fresh_answer.FRESH_ANSWER,
    code_check.REWRITE_NOTE, example_checks.REWRITE_NOTE, research.CORRECTION_NOTE,
    code_sandbox.rewrite_note([]), shown_output.rewrite_note([]), written_calls.missing_tool_note([], []),
)
SIDE_PROMPT_TEMPLATES = (
    honesty.CLAIM_CHECK_PROMPT, honesty.CLAIM_CONFIRM_PROMPT, run_requests.RUN_REQUEST_PROMPT, reply_rules.ADDRESSED_PROMPT,
    research.PAGE_PROMPT, research.PREMISE_PROMPT, research.CORRECTION_PROMPT, research.SUBJECT_PROMPT,
    research.ANSWER_PROMPT, shortcuts.RECAP_PROMPT, shortcuts.QUIZ_JUDGE_PROMPT, style.PROFILE_PROMPT, style.DRAFT_PROMPT,
    style.CHECK_PROMPT, lectures.SECTION_PROMPT, lectures.COMBINE_PROMPT, lectures.CARDS_PROMPT,
    memory_tidy.REPLACED_PROMPT, memory_facts.EXTRACT_PROMPT, email_watch.JUDGE_PROMPT, desktop_look.PICK_PROMPT,
    example_checks.WRITER_PROMPT, private_check.CHECK_PROMPT, private_check.CHECK_SYSTEM,
)
TOOL_GUIDE_TEMPLATES = (tool_guide.HEADING, *(rule.template for rule in tool_guide.GUIDE), *tool_guide.BROWSER_CONDUCT)


def literal_parts(template: str) -> list[str]:
    return [part for part in FORMAT_FIELD.split(template) if part.strip()]


def labelled(label: str, templates: tuple) -> tuple[tuple[str, str], ...]:
    return tuple((label, part) for template in templates for part in literal_parts(template))


FIXED_TEXTS = (labelled("instructions", INSTRUCTION_TEMPLATES) + labelled("notes", NOTE_TEMPLATES)
               + labelled("side prompts", SIDE_PROMPT_TEMPLATES) + labelled("tool guide", TOOL_GUIDE_TEMPLATES))


def without_user_help(description: str) -> str:
    for shown in SHOWN_TO_USERS:
        description = description.replace(shown, "\n\n")
    return description


def descriptions(node) -> list[str]:
    if isinstance(node, list):
        return [text for item in node for text in descriptions(item)]
    if not isinstance(node, dict):
        return []
    own = [without_user_help(node["description"])] if isinstance(node.get("description"), str) else []
    return own + [text for value in node.values() for text in descriptions(value)]


def known_to_the_client(messages: list, client_tools) -> KnownText:
    sent = [message_text(message) for message in messages if message.get("role") in CLIENT_ROLES]
    return KnownText(tuple(text for text in [*sent, *descriptions(client_tools or [])] if text.strip()))


def private_text_for(instructions: str, tools: list, known: KnownText = NOTHING_KNOWN) -> HiddenText:
    tool_texts = tuple(("tool descriptions", text) for text in descriptions(tools))
    return hidden_text((("instructions", instructions),), tool_texts, FIXED_TEXTS, known=known)
