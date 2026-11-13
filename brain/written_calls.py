import json
import re

SERVER_SEPARATOR = "__"
MIN_WRITTEN_TOKENS = 8
REPLAY_LIMIT = 2
CALL_OPENING = "<tool_call>"
CALL_CLOSING = "</tool_call>"
THINK_OPENING = "<think>"
THINK_CLOSING = "</think>"
THINKING = re.compile(r"<think>.*?</think>", re.S)
CALL_MARKUP = re.compile(r"</?tool_call>|\[TOOL_CALLS\]|</?think>", re.I)
SAFE_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]+")
CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)
JSON_READER = json.JSONDecoder()


def looks_swallowed(raw: dict, tools: list) -> bool:
    message = raw.get("message") or {}
    wrote_something = int(raw.get("eval_count") or 0) >= MIN_WRITTEN_TOKENS
    return bool(tools) and wrote_something and not message.get("tool_calls") and not (message.get("content") or "").strip()


def arguments_object(arguments) -> dict | None:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return None
    return arguments if isinstance(arguments, dict) else None


def without_thinking(text: str) -> str:
    text = THINKING.sub("", text or "")
    if THINK_CLOSING in text:
        text = text.rsplit(THINK_CLOSING, 1)[1]
    return text.split(THINK_OPENING, 1)[0]


def call_from(written) -> dict | None:
    if not isinstance(written, dict):
        return None
    name = written.get("name")
    arguments = arguments_object(written.get("arguments", {}))
    if not isinstance(name, str) or not name or arguments is None:
        return None
    return {"function": {"name": name, "arguments": arguments}}


def after_spaces(text: str, index: int) -> int:
    return index + len(text[index:]) - len(text[index:].lstrip())


def call_at(text: str, opening: int) -> tuple[dict | None, int]:
    json_start = after_spaces(text, opening + len(CALL_OPENING))
    try:
        written, json_end = JSON_READER.raw_decode(text, json_start)
    except ValueError:
        return None, json_start
    after = after_spaces(text, json_end)
    if text.startswith(CALL_CLOSING, after):
        return call_from(written), after + len(CALL_CLOSING)
    if after == len(text):
        return call_from(written), after
    return None, json_start


def call_spans(text: str) -> list[tuple[dict, int, int]]:
    found = []
    opening = text.find(CALL_OPENING)
    while opening != -1:
        call, end = call_at(text, opening)
        if call:
            found.append((call, opening, end))
        opening = text.find(CALL_OPENING, end)
    return found


def calls_written_in(text: str) -> list[dict]:
    return [call for call, _, _ in call_spans(without_thinking(text))]


def spoken_text(text: str) -> str:
    visible = without_thinking(text)
    for _, start, end in reversed(call_spans(visible)):
        visible = visible[:start] + visible[end:]
    visible = visible.strip()
    return "" if CALL_MARKUP.search(visible) else visible


def has_call_markup(text: str) -> bool:
    return bool(CALL_MARKUP.search(CODE.sub("", text or "")))


def without_call_markup(text: str) -> str:
    return spoken_text(text) if has_call_markup(text) else text


def bare_name(name: str) -> str:
    return name.rsplit(SERVER_SEPARATOR, 1)[-1]


def offered_name(name: str, offered: list[str]) -> str | None:
    if not name:
        return None
    if name in offered:
        return name
    if SERVER_SEPARATOR in name:
        return None
    same_tool = [candidate for candidate in offered if bare_name(candidate) == name]
    return same_tool[0] if len(same_tool) == 1 else None


def safe_names(names: list[str]) -> list[str]:
    return [name for name in names if isinstance(name, str) and SAFE_TOOL_NAME.fullmatch(name)]


def missing_tool_note(missing: list[str], offered: list[str]) -> str:
    return (f"(Nothing happened: you have no tool called {', '.join(safe_names(missing)) or 'that'} here. The tools you can "
            f"use right now are: {', '.join(safe_names(offered))}. Use one of those, or answer in words.)")
