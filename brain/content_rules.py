import fnmatch

import models
import safety

UNRESTRICTED_MODELS = "unrestricted_models"
UNRESTRICTED_APPROVALS = "unrestricted_approvals"
REMOTE_TOOLS = "remote_tools"
REMOTE_DESKTOP_TOOLS = "remote_desktop_tools"
ALWAYS_ON_CHECKS = ("self_harm", "minors")
AI_DESKTOP_PREFIX = "workspace__"
TAG_SEPARATOR = ":"
PATH_SEPARATOR = "/"


def without_tag(name: str) -> str:
    head, separator, tag = name.rpartition(TAG_SEPARATOR)
    return head if separator and head and PATH_SEPARATOR not in tag else name


def name_forms(model: str) -> set[str]:
    last_part = str(model or "").strip().lower().rsplit(PATH_SEPARATOR, 1)[-1]
    return {last_part, without_tag(last_part)}


def listed(config: dict, key: str) -> list[str]:
    entries = config.get(key) or []
    if not isinstance(entries, list):
        return []
    return [entry.strip() for entry in entries if isinstance(entry, str) and entry.strip()]


def is_unrestricted(config: dict, model: str) -> bool:
    patterns = [pattern.lower() for pattern in listed(config, UNRESTRICTED_MODELS)]
    return any(fnmatch.fnmatchcase(form, pattern) for form in name_forms(model) for pattern in patterns)


def keeps_safety_rules(config: dict, model: str) -> bool:
    return not is_unrestricted(config, model)


def safety_checks(config: dict, model: str, kinds: tuple[str, ...] = safety.ALL_CHECKS) -> tuple[str, ...]:
    if keeps_safety_rules(config, model):
        return kinds
    return tuple(kind for kind in kinds if kind in ALWAYS_ON_CHECKS)


def safety_prompt(config: dict) -> str:
    return config.get("safety_prompt", "") if keeps_safety_rules(config, config.get("model", "")) else ""


def approvals_apply(config: dict, model: str) -> bool:
    return keeps_safety_rules(config, model) or config.get(UNRESTRICTED_APPROVALS, True) is not False


def is_remote(model: str) -> bool:
    return models.is_remote(str(model or ""))


def remote_tool_patterns(config: dict, model: str) -> list[str]:
    desktop = listed(config, REMOTE_DESKTOP_TOOLS) if keeps_safety_rules(config, model) else []
    return listed(config, REMOTE_TOOLS) + desktop


def may_offer(config: dict, model: str, tool_name: str) -> bool:
    if not is_remote(model):
        return True
    if tool_name.startswith(AI_DESKTOP_PREFIX):
        return False
    return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in remote_tool_patterns(config, model))


def tools_for_model(config: dict, model: str, tools: list[dict]) -> list[dict]:
    return [tool for tool in tools if may_offer(config, model, tool["function"]["name"])]


def turn_config(config: dict) -> dict:
    model = config.get("model", "")
    if not is_remote(model):
        return config
    lookups = [rule for rule in config.get("lookups") or [] if may_offer(config, model, str(rule.get("tool", "")))]
    return {**config, "own_memories": "", "recent_exchanges": 0, "auto_memory": False, "lookups": lookups,
            "writes_in_owner_voice": False}


def client_system_with_rules(config: dict, client_system: str) -> str:
    return "\n\n".join(part for part in (config.get("persona"), safety_prompt(config), client_system) if part)


def client_tools_for_model(model: str, tools: list | None) -> list | None:
    return [] if is_remote(model) and tools else tools
