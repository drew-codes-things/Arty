import json
import re

SERVER_SEPARATOR = "__"
MIN_WRITTEN_TOKENS = 8
REPLAY_LIMIT = 2
WRITTEN_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.S)
THINKING = re.compile(r"<think>.*?</think>", re.S)


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


def calls_written_in(text: str) -> list[dict]:
    calls = []
    for written in WRITTEN_CALL.findall(text or ""):
        try:
            call = json.loads(written)
        except ValueError:
            continue
        name = call.get("name") if isinstance(call, dict) else None
        arguments = arguments_object(call.get("arguments", {})) if isinstance(call, dict) else None
        if isinstance(name, str) and name and arguments is not None:
            calls.append({"function": {"name": name, "arguments": arguments}})
    return calls


def text_outside_calls(text: str) -> str:
    return WRITTEN_CALL.sub("", THINKING.sub("", text or "")).strip()


def bare_name(name: str) -> str:
    return name.rsplit(SERVER_SEPARATOR, 1)[-1]


def offered_name(name: str, offered: list[str]) -> str | None:
    if not name:
        return None
    if name in offered:
        return name
    same_tool = [candidate for candidate in offered if bare_name(candidate) == bare_name(name)]
    return same_tool[0] if len(same_tool) == 1 else None


def missing_tool_note(missing: list[str], offered: list[str]) -> str:
    return (f"(Nothing happened: you have no tool called {', '.join(missing)} here. The tools you can use right now are: "
            f"{', '.join(offered)}. Use one of those, or answer in words.)")
