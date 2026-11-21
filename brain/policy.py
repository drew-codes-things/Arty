import fnmatch
import hashlib
import json
import re
from urllib.parse import urlparse, urlsplit

import installs
import prices

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
LOWERCASE_START = re.compile(r"^(?:sudo\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*[a-z0-9_./~-]")
UNTRUSTED_READS = ["fetch__*", "research__web_search", "research__read_document", "research__search_library", "workspace__browser_*",
                   "workspace__gmail_*", "workspace__read_file", "workspace__read_screen_text", "workspace__get_clipboard",
                   "homelab__nextcloud_get", "homelab__nextcloud_search", "read_attached_file", "spreadsheet",
                   "identify_song", "read_image_text", "look_at_attachment", "analyse_sound", "chrome_*", "computer_read_file",
                   "computer_look", "computer_camera",
                   "computer_search", "computer_find_files", "computer_list_files", "read_clipboard", "desktop_notifications",
                   "desktop_windows", "email_inbox", "email_read", "calendar_app_events", "arty_browser_*",
                   "read_web_page", "check_price"]
AFTER_UNTRUSTED = ["memory__create_*", "memory__add_*", "memory__delete_*", "workspace__run_command", "workspace__type_text",
                   "workspace__open_app", "workspace__press_keys", "workspace__click", "workspace__write_file", "workspace__edit_file",
                   "workspace__set_clipboard", "workspace__email_draft_link", "email_draft", "homelab__nextcloud_upload",
                   "start_background_task", "schedule_task", "add_watcher", "set_daily_briefing", "set_inbox_tidy",
                   "always_allow_inbox_change", "pause_job", "resume_job", "delete_job", "workspace__own_app_request",
                   "workspace__drive_upload", "workspace__gmail_send"]
UNTRUSTED_NOTE = "asked for after reading outside content (a web page, email or file)"
WEB_READS = {"read_web_page": "url", "check_price": "url", "fetch__fetch": "url"}
WEB_LINK = re.compile(r"https?://[^\s<>\"'`\])]+")
LINK_END = ".,;:!?"
NAMED_HOST = re.compile(r"[a-z0-9][a-z0-9.-]*")
FILE_NAME_ENDINGS = frozenset({
    "md", "txt", "py", "js", "mjs", "ts", "tsx", "jsx", "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "csv", "tsv",
    "log", "html", "htm", "css", "xml", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "png", "jpg", "jpeg",
    "gif", "svg", "webp", "zip", "gz", "tgz", "tar", "7z", "rar", "c", "h", "cpp", "hpp", "java", "kt", "kts", "php",
    "sql", "lock", "env", "bak", "tmp", "exe", "dll", "apk", "deb", "mp3", "mp4", "wav", "mov", "mkv", "ipynb", "bat",
    "ps1"})
MOST_NAMED_SITE_PATH_CHARS = 40
MOST_REQUEST_CHARS_SCANNED = 4000
MAX_SEEN_LINKS = 600
SHOWN_LINK_CHARS = 200
UNSEEN_LINK_NOTE = ("reading {url}, a web address that didn't come from you or from a page read for this reply (after "
                    "reading outside content, a made-up address could carry what was read to another site)")


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


def install_checked_text(name: str, checked: str) -> str:
    if name != "workspace__type_text":
        return checked
    return "\n".join(line for line in checked.splitlines() if installs.names_a_known_program(line))


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


def remember_tool_result(context: dict, tool_name: str, result: str, rules: dict | None = None):
    """Track guest names from Proxmox status, and the browser's current page and numbered items."""
    if matches(tool_name, (rules or {}).get("untrusted_reads", UNTRUSTED_READS)):
        context["untrusted"] = True
        remember_links(context, result)
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


def starting_context(has_attachment: bool) -> dict:
    return {"untrusted": True} if has_attachment else {}


def remember_client_results(context: dict, tool_names: list[str], rules: dict | None = None):
    reads = (rules or {}).get("untrusted_reads", UNTRUSTED_READS)
    if any(matches(name, reads) for name in tool_names):
        context["untrusted"] = True


def remember_client_links(context: dict, results: list[tuple[str, str]], rules: dict | None = None):
    reads = (rules or {}).get("untrusted_reads", UNTRUSTED_READS)
    for name, text in results:
        if matches(name, reads):
            remember_links(context, text)


def link_key(url: str) -> str:
    base, hashmark, fragment = str(url or "").strip().partition("#")
    return (base if "?" in base else base.rstrip("/")) + hashmark + fragment


def same_link(url: str) -> str:
    return link_key(str(url or "").strip().rstrip(LINK_END))


def link_keys_in(text) -> list[str]:
    keys = []
    for link in WEB_LINK.findall(str(text or "")):
        key = same_link(link)
        keys += [key, key.partition("#")[0]] if "#" in key else [key]
    return [key for key in keys if key]


def remember_links(context: dict, text) -> None:
    seen = context.setdefault("seen_links", [])
    for key in link_keys_in(text):
        if key not in seen:
            seen.append(key)
    del seen[:-MAX_SEEN_LINKS]


def without_www(host: str) -> str:
    return host[len("www."):] if host.startswith("www.") else host


def named_hosts(request_text: str) -> set[str]:
    said = str(request_text or "")[:MOST_REQUEST_CHARS_SCANNED].lower()
    hosts = set()
    for token in NAMED_HOST.findall(said):
        typed = without_www(token.strip(".-"))
        if "." in typed and (token.startswith("www.") or not is_file_name(typed)):
            hosts |= {typed, prices.registrable_name(f"https://{typed}")}
    return hosts


def is_file_name(typed: str) -> bool:
    return typed.rsplit(".", 1)[-1] in FILE_NAME_ENDINGS


def site_named_in(url: str, request_text: str) -> bool:
    try:
        parts = urlsplit(url)
        host = without_www((parts.hostname or "").lower().rstrip("."))
    except ValueError:
        return False
    is_short_plain_address = not parts.query and not parts.fragment and len(parts.path) <= MOST_NAMED_SITE_PATH_CHARS
    return bool(host) and is_short_plain_address and host in named_hosts(request_text)


def web_read_reason(name: str, arguments: dict, context: dict, request_text: str) -> str | None:
    field = WEB_READS.get(name)
    url = str(arguments.get(field) or "").strip() if field else ""
    if not url or not context.get("untrusted"):
        return None
    known = set(context.get("seen_links", [])) | set(link_keys_in(request_text))
    if link_key(url) in known or site_named_in(url, request_text):
        return None
    return UNSEEN_LINK_NOTE.format(url=url[:SHOWN_LINK_CHARS])


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def untrusted_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    if not context.get("untrusted") or not matches(name, rules.get("after_untrusted", AFTER_UNTRUSTED)):
        return None
    if name in COMMAND_TOOLS:
        verb = "typing" if name == "workspace__type_text" else "running"
        action = f"{verb} `{shown_command(str(arguments.get(COMMAND_TOOLS[name], '')))}`"
    else:
        action = shown_command(describe_action(name, arguments, context))
    return f"{action}, {UNTRUSTED_NOTE}"


def approval_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    """A sentence describing why this call needs approval, or None if it can go ahead."""
    return own_approval_reason(name, arguments, context, rules) or untrusted_reason(name, arguments, context, rules)


def inbox_rule_reason(arguments: dict) -> str | None:
    if arguments.get("remove") is True:
        return None
    action = str(arguments.get("action", "")).strip().lower()
    what = f'label as "{str(arguments.get("label", "")).strip()}"' if action == "label" else "archive"
    return f"always {what} mail from {str(arguments.get('sender', '')).strip()} without asking each time, from now on"


NATIVE_APPROVALS = {"always_allow_inbox_change": inbox_rule_reason}


def native_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    asked = NATIVE_APPROVALS.get(name)
    return (asked(arguments) if asked else None) or untrusted_reason(name, arguments, context, rules)


def own_approval_reason(name: str, arguments: dict, context: dict, rules: dict) -> str | None:
    if name in rules.get("always", []):
        return describe_action(name, arguments, context)
    if name in COMMAND_TOOLS:
        command = str(arguments.get(COMMAND_TOOLS[name], ""))
        checked = checked_text(name, command)
        if installs.installs_software(install_checked_text(name, checked)) or any(re.search(pattern, checked) for pattern in rules.get("command_patterns", [])):
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
QUESTION_START = "Before I do that, I need your OK: "
APPROVAL_ASKED = re.compile(r"^Before I do that, I need your OK: (?P<reason>.+)\. Shall I go ahead\?$", re.DOTALL)
PRETEND_QUESTION_REFUSAL = ("I haven't done that: its details contain the words I use to ask for your OK, so a yes could "
                            "end up approving something else. Nothing was sent or changed.")


def approval_question(reason: str) -> str:
    return APPROVAL_QUESTION.format(reason=reason)


def pretends_to_ask(reason: str) -> bool:
    return same_words(QUESTION_START).lower() in same_words(str(reason or "")).lower()


def approved_reason(messages: list[dict], text_of) -> str | None:
    chat = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(chat) < 2 or chat[-1].get("role") != "user" or chat[-2].get("role") != "assistant":
        return None
    if not is_plain_approval(text_of(chat[-1])):
        return None
    said = text_of(chat[-2]).strip()
    if said.count(QUESTION_START) != 1:
        return None
    asked = APPROVAL_ASKED.match(said[said.find(QUESTION_START):])
    return asked.group("reason") if asked else None


def approval_key(messages: list[dict], text_of) -> str:
    chat = [m for m in messages if m.get("role") in ("user", "assistant")]
    asked = [[m.get("role"), text_of(m)] for m in chat[:-1]]
    return hashlib.sha256(json.dumps([len(chat), asked]).encode("utf-8", "surrogateescape")).hexdigest()


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
