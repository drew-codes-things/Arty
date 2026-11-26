import json
import logging
import re

import owner

from reply_rules import (URL_TAGS, PROMPT_LEAK, STRONG_LEAK, OUTSIDE_BLOCK_LEAK, FUMBLED_CALL, TOOL_META_SENTENCE, JSON_BLOCK,
                         TOOLS_NOTE_ECHO)
from steering import LOOKUP_LABEL
from connections import USER_NAME

log = logging.getLogger("brain")


NAME_GREETING, NAME_TAIL = owner.vocative_patterns(USER_NAME)
HAND_IN_BLOCK = re.compile(r"\[file\b[^\]\n]*\].*?\[/file\]|\[doc\].*?\[/doc\]|```.*?```", re.DOTALL | re.IGNORECASE)
TRAILING_TOOLS_HEADING = re.compile(r"\n\s*# Tools\s*\Z")


LINE_LEAD = r"[ \t>]*"
FENCED_CODE = (r"^" + LINE_LEAD + r"(?P<fence>`{3,}|~{3,}).*?(?:\n" + LINE_LEAD
               + r"(?P=fence)[`~]*[ \t\r]*(?=\n|\Z)|\Z)")
INDENTED_CODE = r"(?:\A|(?<=\n\n)|(?<=\n\r\n))(?:(?: {4}|\t)[^\n]*(?:\n|\Z))+"
INLINE_CODE_MAX_CHARS = 400
INLINE_CODE = (r"(?<!`)(?P<ticks>`+)(?!`)(?:[^\n]|\n(?![ \t\r]*\n)){0,%d}?(?<!`)(?P=ticks)(?!`)"
               % INLINE_CODE_MAX_CHARS)
HTML_CODE = r"(?i:<pre\b[^>\n]*>.*?(?:</pre[ \t]*>|\Z)|<code\b[^>\n]*>.*?(?:</code[ \t]*>|\Z))"
DASHES = r"\u2014(?:[ \t]*\u2014)*"
DASH_RUNS = (r"(?P<range>(?<=\d)[ \t]*" + DASHES + r"[ \t]*(?=\d))"
             r"|(?P<line_start>^(?P<indent>[ \t]*)" + DASHES + r"[ \t]*)"
             r"|(?P<line_end>(?<![ \t])[ \t]*" + DASHES + r"[ \t]*(?=\r?$))"
             r"|(?<![ \t])[ \t]*" + DASHES + r"[ \t]*")
EM_DASH_RULES = re.compile(
    r"(?P<code>" + FENCED_CODE + r"|" + INDENTED_CODE + r"|" + INLINE_CODE + r"|" + HTML_CODE + r")|" + DASH_RUNS,
    re.M | re.S)
NUMBER_RANGE_DASH = "-"
EM_DASH_STAND_IN = ", "
JSON_WHITESPACE = " \t\r\n"
JSON_OPENERS = ("{", "[")
JSON_STRING_MAX_CHARS = 2000
JSON_STRING = re.compile(r'"((?:[^"\\]++|\\.)*+)"')


def em_dash_replacement(found: re.Match) -> str:
    kinds = found.groupdict()
    if kinds.get("code") is not None:
        return found.group()
    if kinds["range"] is not None:
        return NUMBER_RANGE_DASH
    if kinds["line_start"] is not None:
        return kinds["indent"]
    if kinds["line_end"] is not None:
        return ""
    return EM_DASH_STAND_IN


def is_json_reply(text: str) -> bool:
    if not text.lstrip(JSON_WHITESPACE).startswith(JSON_OPENERS):
        return False
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return False
    return not has_a_long_json_string(text)


def has_a_long_json_string(text: str) -> bool:
    return any(len(found.group(1)) > JSON_STRING_MAX_CHARS for found in JSON_STRING.finditer(text))


def without_em_dashes(text: str) -> str:
    if is_json_reply(text):
        return text
    return EM_DASH_RULES.sub(em_dash_replacement, text)


def closed_blocks(answer: str) -> list[tuple[int, int]]:
    return [found.span() for found in HAND_IN_BLOCK.finditer(answer)]


def hand_in_leak(answer: str) -> re.Match | None:
    blocks = closed_blocks(answer)
    outside = next((found for found in OUTSIDE_BLOCK_LEAK.finditer(answer)
                    if not any(start <= found.start() < end for start, end in blocks)), None)
    leaks = [found for found in (STRONG_LEAK.search(answer), outside) if found]
    return min(leaks, key=lambda found: found.start(), default=None)


def outside_blocks(answer: str, change) -> str:
    pieces, position = [], 0
    for start, end in closed_blocks(answer):
        pieces += [change(answer[position:start]), answer[start:end]]
        position = end
    return "".join(pieces) + change(answer[position:])


def tidy_answer(answer: str, strips_owner_name: bool = True, keeps_hand_ins: bool = False) -> str:
    if leak := (hand_in_leak(answer) if keeps_hand_ins else PROMPT_LEAK.search(answer)):
        log.warning("cut a reply that ran on into its instructions")
        answer = answer[:leak.start()].rstrip()
        if keeps_hand_ins:
            answer = TRAILING_TOOLS_HEADING.sub("", answer)
    if answer.count("```") % 2 == 1 and (fumbled := FUMBLED_CALL.search(answer)):
        answer = answer[:fumbled.start()]  # an unclosed block would turn the rest of the chat into code
    answer = TOOLS_NOTE_ECHO.sub("", LOOKUP_LABEL.sub("", answer))
    if keeps_hand_ins:
        answer = outside_blocks(answer, lambda prose: TOOL_META_SENTENCE.sub("", prose))
    else:
        answer = TOOL_META_SENTENCE.sub("", JSON_BLOCK.sub("\n", answer))
    answer = re.sub(r"\n{3,}", "\n\n", answer)  # no strip: this also cleans early sentences, whose spaces matter
    if strips_owner_name:
        answer = NAME_GREETING.sub(lambda m: m.group(1), answer)
        answer = NAME_TAIL.sub("", answer)
    return without_em_dashes(URL_TAGS.sub("", answer))


def as_written(text: str) -> str:
    return text


def never_an_offer(_sentence: str) -> None:
    return None
