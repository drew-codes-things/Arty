import logging
import re
import urllib.error

import coding
import media
import model_choice
import models
import policy

from attachments import is_picture_part

log = logging.getLogger("brain")

AUTO_ID = "auto"
AUTO_NAME = "Arty Auto"
VISION, CODE, AGENT, CHAT = "vision", "code", "agent", "chat"
ROLES = (VISION, CODE, AGENT, CHAT)
SEEN_KINDS = frozenset({"image", "video"})
HEARD_KINDS = frozenset({"audio"})
NANOSECONDS = 1_000_000_000
SPEED_DECIMALS = 1

ASKS_ARTY_TO_ACT = re.compile(
    r"\b(?:remind(?:er|ers)? me|reminders?|set (?:an? |my )?(?:alarm|timer|reminder)|alarms?|timers?|"
    r"calendar|diary|appointments?|what'?s on (?:today|tomorrow|this week)|my (?:schedule|day|week|phone|inbox|emails?)|"
    r"(?:call|ring|phone|text|message|whatsapp) (?:me|him|her|them|mum|mom|dad|my \w+)|send (?:a |an )?(?:text|message|email)|"
    r"lights?|lamps?|heating|thermostat|smart plug|turn (?:on|off|up|down)|switch (?:on|off)|"
    r"music|songs?|playlist|album|spotify|play (?:some|me|my|a|the)|"
    r"weather|forecast|raining|rain (?:today|tomorrow)|news|headlines)\b",
    re.IGNORECASE)


def asked_for(requested: str) -> bool:
    return requested == AUTO_ID


def stored_kind(part) -> str:
    if not isinstance(part, dict):
        return ""
    if is_picture_part(part):
        return "image"
    if part.get("type") == "input_audio":
        return "audio"
    if part.get("type") == "file":
        stored = media.get(str((part.get("file") or {}).get("file_id", ""))) or {}
        return str(stored.get("kind") or "")
    return ""


def latest_kinds(messages: list[dict]) -> set[str]:
    latest = next((message for message in reversed(messages) if message.get("role") == "user"), None)
    content = latest.get("content") if latest else None
    if not isinstance(content, list):
        return set()
    return {kind for kind in map(stored_kind, content) if kind}


def asks_for_code(said: str) -> bool:
    return coding.is_request(coding.classified_part(said))


def role_for(said: str, messages: list[dict], code_asked: bool) -> str:
    kinds = latest_kinds(messages)
    if kinds & SEEN_KINDS:
        return VISION
    if kinds & HEARD_KINDS:
        return AGENT
    if code_asked:
        return CODE
    if ASKS_ARTY_TO_ACT.search(coding.classified_part(said)):
        return AGENT
    return CHAT


def choose(config: dict, said: str, messages: list[dict]) -> tuple[str, str]:
    code_asked = asks_for_code(said)
    role = role_for(said, messages, code_asked)
    return role, model_choice.chat_model(config, model_choice.MODEL_ID, said, role == VISION, code_asked)


def trained_window(name: str) -> int:
    try:
        found = models.find(name)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log.warning("could not read %s's trained window: %s", name, error)
        return 0
    size = (found or {}).get("context_length")
    return size if models.is_token_count(size) else 0


def context_window(config: dict, name: str) -> int:
    asked = models.context_tokens(config, name)
    trained = trained_window(name)
    return min(asked, trained) if trained else asked


def picked(config: dict, role: str) -> dict:
    return {"model": config["model"], "role": role or None, "auto": bool(role),
            "context_window": context_window(config, config["model"])}


def usage_numbers(raw: dict | None) -> dict:
    raw = raw or {}
    prompt, written = int(raw.get("prompt_eval_count") or 0), int(raw.get("eval_count") or 0)
    if not prompt and not written:
        return {}
    numbers = {"prompt_tokens": prompt, "completion_tokens": written, "context_tokens": prompt + written}
    if raw.get("prompt_eval_cached_count") is not None:
        numbers["cached_tokens"] = int(raw["prompt_eval_cached_count"] or 0)
    writing_time = int(raw.get("eval_duration") or 0)
    if written and writing_time > 0:
        numbers["tokens_per_second"] = round(written * NANOSECONDS / writing_time, SPEED_DECIMALS)
    return numbers


def turn_report(chosen: dict | None, raw: dict | None, tools_used: list | None) -> dict:
    report = {**(chosen or {}), "steps": len(tools_used or [])}
    if any(policy.matches(name, policy.UNTRUSTED_READS) for name in tools_used or []):
        report["read_outside"] = True
    if usage := usage_numbers(raw):
        report["usage"] = usage
    return report


def listing(config: dict) -> dict:
    main = config.get("model", "")
    looker = config.get("vision_model", "") if model_choice.routes_picture_chats(config) else ""
    coder = config.get("coding_model", "") if config.get("route_coding_chats", True) else ""
    jobs = [f"pictures and videos on {looker or main}", f"code on {coder or main}", f"everything else on {main} with Arty's tools"]
    return {"id": AUTO_ID, "name": AUTO_NAME, "description": "Picks the model for each message: " + ", ".join(jobs),
            "roles": list(ROLES)}
