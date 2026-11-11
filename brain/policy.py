"""Which tool calls need Drew's OK first, and which tools a small model gets to see.

Approval: anything that acts *as Drew* on the outside world (posting, sending,
connecting, buying, submitting forms on social or mail sites, pushing code) is
held until he approves. Reading and browsing never are. In chat he approves by
saying yes; in a background task the task pauses and his phone gets
Approve/Deny buttons.

Tool selection: a 3B model picks tools badly from a list of 50, so it gets the
always-on tools plus only the groups that match the request (or that it asks for
with use_tools). A bigger model (tool_budget 0) simply gets everything.
"""
import fnmatch
import hashlib
import re
from urllib.parse import urlparse

URL_LINE = re.compile(r"^(?P<title>.*) \| (?P<url>https?://\S+)$", re.MULTILINE)
ELEMENT_LINE = re.compile(r'^\[(\d+)\] \S+ "(.*)"$', re.MULTILINE)
GUEST_LINE = re.compile(r"^(?P<kind>VM|CT) (?P<vmid>\d+) (?P<name>[^\s(:]*)", re.MULTILINE)
ACTION_WORDS = {
    "homelab__start_guest": "start",
    "homelab__restart_guest": "restart",
    "homelab__pufferpanel_power": "{action}",
}
APPROVAL_START = (r"yes|yep|yeah|yup|ok|okay|sure|go ahead|go for it|do it|approved?|confirm(?:ed)?|send it|post it|"
                  r"publish it|please do")
APPROVAL_MORE = APPROVAL_START + r"|please|thanks|thank you|and|now|fine|sounds good|absolutely|definitely"
PLAIN_APPROVAL = re.compile(rf"\s*(?:{APPROVAL_START})(?:[\s,]+(?:{APPROVAL_MORE}))*[\s.!]*", re.IGNORECASE)
COMMAND_TOOLS = {"workspace__run_command": "command", "workspace__open_app": "command", "workspace__type_text": "text"}
PAGE_ACTIONS = {"workspace__browser_click", "workspace__browser_type", "workspace__browser_press", "workspace__browser_upload",
                "workspace__browser_download"}
DESKTOP_INPUT = {"workspace__press_keys", "workspace__click"}
ACTIVATING_KEYS = re.compile(r"\b(enter|return|space|kp_enter)\b", re.IGNORECASE)
SHOWN_COMMAND_CHARS = 200
SHELL_OPERATORS = re.compile(r"&&|\|\||[|;`]|\$\(|>")
LOWERCASE_START = re.compile(r"^(?:sudo\s+)?[a-z0-9_./~-]")


def is_plain_approval(text: str) -> bool:
    return bool(PLAIN_APPROVAL.fullmatch(text or ""))


def shown_command(command: str) -> str:
    if len(command) <= SHOWN_COMMAND_CHARS:
        return command
    fingerprint = hashlib.sha256(command.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return f"{command[:SHOWN_COMMAND_CHARS]}... (full command {fingerprint})"


def looks_like_a_command(line: str) -> bool:
    line = line.strip()
    return bool(LOWERCASE_START.match(line) or SHELL_OPERATORS.search(line))


def checked_text(name: str, text: str) -> str:
    if name != "workspace__type_text":
        return text
    return "\n".join(line for line in text.splitlines() if looks_like_a_command(line))


def is_sensitive_host(context: dict, rules: dict) -> bool:
    host = (urlparse(context.get("url", "")).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in rules.get("domains", []))


def desktop_input_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    if not is_sensitive_host(context, rules):
        return None
    host = urlparse(context.get("url", "")).hostname
    if name == "workspace__click":
        return f"clicking at {arguments.get('x')}, {arguments.get('y')} while {host} is open"
    keys = str(arguments.get("keys", ""))
    return f"pressing {keys} while {host} is open" if ACTIVATING_KEYS.search(keys) else None


def page_unknown(name: str, context: dict) -> bool:
    return name in PAGE_ACTIONS and not context.get("url")


def remember_tool_result(context: dict, tool_name: str, result: str):
    """Track guest names from Proxmox status, and the browser's current page and numbered items."""
    if tool_name == "homelab__proxmox_status":
        context["guests"] = {match["vmid"]: f'{match["kind"]} {match["vmid"]} ({match["name"]})' if match["name"]
                             else f'{match["kind"]} {match["vmid"]}' for match in GUEST_LINE.finditer(result)}
        return
    if not tool_name.startswith("workspace__browser_"):
        return
    page = URL_LINE.search(result)
    if page:
        context["url"] = page.group("url")
    # Remember an unresolved login wall / bot check, so a background task can't just give up on it.
    if result.startswith("BLOCKED:"):
        context["blocked"] = urlparse(context.get("url", "")).hostname or "the website"
    else:
        context.pop("blocked", None)
    elements = dict(ELEMENT_LINE.findall(result))
    if elements:
        context["elements"] = elements


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def approval_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    """A sentence describing why this call needs approval, or None if it can go ahead."""
    if name in rules.get("always", []):
        return describe_action(name, arguments, context)
    if name in COMMAND_TOOLS:
        command = str(arguments.get(COMMAND_TOOLS[name], ""))
        checked = checked_text(name, command)
        for pattern in rules.get("command_patterns", []):
            if re.search(pattern, checked):
                verb = "typing" if name == "workspace__type_text" else "running"
                return f"{verb} `{shown_command(command)}`"
        return None
    if name in DESKTOP_INPUT:
        return desktop_input_reason(name, arguments, context, rules)
    if not name.startswith("workspace__browser_"):
        return None

    host = (urlparse(context.get("url", "")).hostname or "").lower()
    sensitive = page_unknown(name, context) or is_sensitive_host(context, rules)
    commit_words = set(rules.get("commit_words", []))
    money_words = set(rules.get("money_words", []))
    target = str(arguments.get("target", ""))
    elements = context.get("elements", {})
    label = elements.get(target, target)
    label_known = target in elements or not target.isdigit()
    label_words = _words(label)
    where = host or "a page I haven't read this turn"

    if name == "workspace__browser_click":
        opens_editor = bool(label_words & set(rules.get("draft_words", [])))
        if label_words & money_words or (sensitive and (not label_known or label_words & commit_words and not opens_editor)):
            return f'clicking "{label}" on {where}'
    elif name == "workspace__browser_type":
        if sensitive and arguments.get("submit") and "search" not in label_words:
            return f'typing and submitting "{str(arguments.get("text", ""))[:300]}" into "{label}" on {where}'
    elif name == "workspace__browser_press":
        if sensitive and ACTIVATING_KEYS.search(str(arguments.get("keys", ""))):
            return f"pressing {arguments.get('keys')} on {where}"
    elif name == "workspace__browser_download":
        if sensitive:
            return f'downloading from "{label}" on {where}'
    elif name == "workspace__browser_upload":
        return f"uploading {arguments.get('path')} on {where}"
    return None


def describe_action(name: str, arguments: dict, context: dict) -> str:
    if "vmid" in arguments:
        vmid = str(arguments["vmid"])
        target = context.get("guests", {}).get(vmid, f"guest {vmid}")
    else:
        target = str(arguments.get("server") or arguments.get("name") or "")
    verb = ACTION_WORDS.get(name, name.split("__")[-1].replace("_", " ")).format(action=arguments.get("action", ""))
    if target:
        return f"{verb} {target}"
    details = ", ".join(f"{key} {value}" for key, value in arguments.items())
    return f"{verb} ({details})" if details else verb


APPROVAL_QUESTION = "Before I do that, I need your OK: {reason}. Shall I go ahead?"
APPROVAL_ASKED = re.compile(r"^Before I do that, I need your OK: (?P<reason>.+)\. Shall I go ahead\?$", re.DOTALL)


def approval_question(reason: str) -> str:
    return APPROVAL_QUESTION.format(reason=reason)


def approved_reason(messages: list[dict], text_of) -> str | None:
    chat = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(chat) < 2 or chat[-1].get("role") != "user" or chat[-2].get("role") != "assistant":
        return None
    if not is_plain_approval(text_of(chat[-1])):
        return None
    asked = APPROVAL_ASKED.match(text_of(chat[-2]).strip())
    return asked.group("reason") if asked else None


def same_words(text: str) -> str:
    return " ".join(text.split())


def covers(approved: str | None, reason: str) -> bool:
    return approved is not None and same_words(approved) == same_words(reason)


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def select_tools(all_tools: list[dict], config: dict, request_text: str, requested_groups: set[str]) -> list[dict]:
    budget = int(config.get("tool_budget", 0))
    if budget <= 0 or len(all_tools) <= budget:
        return all_tools
    groups = config.get("tool_groups", {})
    lowered = request_text.lower()
    scored = []
    for group, spec in groups.items():
        hits = sum(1 for keyword in spec.get("keywords", []) if keyword in lowered)
        if group in requested_groups:
            hits += 100
        if hits:
            scored.append((hits, group))
    always = [tool for tool in all_tools if matches(tool["function"]["name"], config.get("always_tools", []))]
    chosen_names = {tool["function"]["name"] for tool in always}
    order = list(groups)
    picked = {}
    for _, group in sorted(scored, key=lambda item: (-item[0], order.index(item[1]))):
        members = [tool for tool in all_tools if tool["function"]["name"] not in chosen_names and matches(tool["function"]["name"], groups[group].get("tools", []))]
        if group not in requested_groups and len(always) + sum(map(len, picked.values())) + len(members) > budget:
            continue
        picked[group] = members
        chosen_names.update(tool["function"]["name"] for tool in members)
    # Score decides which groups fit; the list itself keeps one fixed order, so the model's cached
    # prompt (the tools are near its start) stays valid up to the first group that differs.
    return always + [tool for group in order for tool in picked.get(group, [])]


def use_tools_definition(config: dict) -> dict:
    groups = config.get("tool_groups", {})
    listing = "; ".join(f"{name}: {spec.get('description', '')}" for name, spec in groups.items())
    return {
        "type": "function",
        "function": {
            "name": "use_tools",
            "description": f"ONLY if none of the tools you already have can do the job: unlock another group of tools. Groups: {listing}",
            "parameters": {
                "type": "object",
                "properties": {"groups": {"type": "array", "items": {"type": "string", "enum": list(groups)}, "description": "the groups you need"}},
                "required": ["groups"],
            },
        },
    }
