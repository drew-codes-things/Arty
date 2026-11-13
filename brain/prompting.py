import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tool_guide

from archive import recent_exchanges
from reply_rules import TOOLLESS_NOTE
from memory_facts import recalled_memories

log = logging.getLogger("brain")


DEFAULT_TIME_ZONE = "Europe/London"
DEFAULT_TIME_ZONE_LABEL = "UK time"


BACKGROUND_INSTRUCTIONS = """BACKGROUND TASK MODE
You are working on a task by yourself; the user is not watching the chat. Work through it fully with your tools, checking each step.
- If you need a human (a website login, a CAPTCHA or bot check), ask the user with your tools: the task pauses until the user has done it, then you continue.
- Outward-facing actions (posting, sending, connecting, buying) are held for the user's approval automatically; just carry on calling the tool.
- When finished, reply with a short report of what you did and found, written to the user as "you". It is sent to his phone."""


def system_prompt(config, client_system="", background=False, toolless=False):
    """The instructions, which stay the same from one message to the next, so Ollama can reuse
    its cache of them (and of the tool list after them) instead of reading ~4000 tokens again."""
    # Tool-less models get no tool instructions: they can't use them, and small ones have short context windows.
    instructions = TOOLLESS_NOTE if toolless else config["system_prompt"]
    system = (config["persona"] + "\n\n" if config["persona"] else "") + instructions
    if background:
        system += "\n\n" + BACKGROUND_INSTRUCTIONS
    if client_system:
        system += "\n\nInstructions from the app you are talking through:\n" + client_system
    return system


def with_tool_guide(instructions: str, tools: list) -> str:
    guide = tool_guide.guide(tool_guide.offered_names(tools))
    return f"{instructions}\n\n{guide}" if guide else instructions


def current_context(config, request: str = ""):
    """What changes every message: memories, recent conversations and the time. It rides at the start of
    the latest user message (see with_context), after everything that can be cached."""
    memories = config["own_memories"] if "own_memories" in config else recalled_memories(request)
    log.info("loaded %d memory lines", memories.count("\n") + 1 if memories else 0)
    if memories:
        known = (
            "FACTS YOU ALREADY KNOW ABOUT THE USER (from your long-term memory). "
            "Answer questions about the user from these facts first; only search memory if the answer is not here.\n"
            + memories
        )
    else:
        known = "You have no saved memories about the user yet."
    recent = recent_exchanges(config)
    if recent:
        known += "\n\nBACKGROUND ONLY, do not continue these: your most recent earlier conversations with the user (any app), oldest first:\n" + recent
    return known + "\n\n" + time_line(config)


def local_zone(config) -> ZoneInfo:
    try:
        return ZoneInfo(str(config.get("time_zone") or DEFAULT_TIME_ZONE))
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time_zone %r in brain.json; using %s", config.get("time_zone"), DEFAULT_TIME_ZONE)
        return ZoneInfo(DEFAULT_TIME_ZONE)


def time_line(config, moment: datetime | None = None) -> str:
    now = (moment or datetime.now().astimezone()).astimezone(local_zone(config))
    offset = now.strftime("%z")
    label = str(config.get("time_zone_label") or DEFAULT_TIME_ZONE_LABEL)
    return f"It is now {now:%A %d %B %Y, %H:%M} ({label}, {now:%Z}, UTC{offset[:3]}:{offset[3:]})."
