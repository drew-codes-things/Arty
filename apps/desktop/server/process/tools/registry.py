"""Tools Arty's brain can use on this computer: the computer_* tools (see
process.tools.computer), the clipboard and opening links. Reminders and memory
live in the brain itself.

open_url only accepts http and https. The clipboard (wl-clipboard, xclip or
xsel) and open_url (xdg-open) need a desktop session, so a headless bridge
leaves them out.
"""
import json
import re
import time
from urllib.parse import urlparse

from process.config import load_config, setting
from process.tools import computer
from process import browser
from process import notifications
from process.tools import desktop
from process.tools import session
from process.tools import shell
from process.tools import win_desktop

_config = load_config()
_cfg = _config.get("tools") or {}



def _allowed(key):
    return bool(setting("tools", key, True))


def _clipboard_command():
    """The command that prints the clipboard on this desktop, or None."""
    readers = [(["wl-paste", "--no-newline"], "wl-paste"),
               (["xclip", "-selection", "clipboard", "-o"], "xclip"),
               (["xsel", "--clipboard", "--output"], "xsel")]
    if not session.kind():
        return None
    if session.kind() == "x11":  # X11 tools first; wl-paste can't see an X11 clipboard
        readers.append(readers.pop(0))
    return next((command for command, tool in readers if session.have(tool)), None)


def _clipboard_available():
    return _allowed("clipboard") and (shell.is_windows() or _clipboard_command() is not None)


def _open_url_available():
    return _allowed("open_url") and (shell.is_windows() or (bool(session.kind()) and session.have("xdg-open")))


def _clipboard_text():
    if not shell.is_windows():
        return session.run(_clipboard_command(), timeout=2)
    try:
        return win_desktop.get_clipboard_text()
    except RuntimeError:
        return None



CLIPBOARD_LIMIT = int(_cfg.get("clipboard_chars", 2000))

def read_clipboard():
    if not _allowed("clipboard"):
        return "The clipboard is off limits."
    if not shell.is_windows() and _clipboard_command() is None:
        return "No clipboard tool is installed (wl-clipboard on Wayland, xclip or xsel on X11)."
    text = _clipboard_text()
    if text is None:
        return "Could not read the clipboard (it may be empty)."
    text = text.strip()
    if not text:
        return "The clipboard is empty."
    if len(text) > CLIPBOARD_LIMIT:
        text = text[:CLIPBOARD_LIMIT] + "… (truncated)"
    print(f"[tool] read clipboard ({len(text)} chars)", flush=True)
    return f"The clipboard contains:\n{text}"


def open_url(url):
    if not _allowed("open_url"):
        return "Opening links is switched off."
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "Only http and https links can be opened."
    if not computer.open_default(url):
        return "Could not open it: no program here opens links."
    print(f"[tool] opened {url}", flush=True)
    return f"Opened {parsed.netloc}."


SPECS = [
    ("read_clipboard", read_clipboard, _clipboard_available, {
        "description": "Read what is on the clipboard. You cannot see it any "
                       "other way, so call this whenever they mention "
                       "something they copied or pasted, or refer to 'this' "
                       "error, link, message or snippet. Never guess at what "
                       "they copied.",
        "parameters": {"type": "object", "properties": {}},
        }),
    ("open_url", open_url, _open_url_available, {
        "description": "Open a web link in their default browser. Only http and https. "
                       "If they name a browser, use computer_open with that app and the url "
                       "instead.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }}),
]

def _computer_allowed():
    return computer.available() and _allowed("computer")


SPECS += [(name, fn, _computer_allowed, spec) for name, fn, spec in computer.SPECS]
_COMPUTER_TOOLS = {name for name, *_ in computer.SPECS}


def _desktop_allowed():
    return desktop.available() and computer.available() and _allowed("computer")


SPECS += [(name, fn, _desktop_allowed, spec) for name, fn, spec in desktop.SPECS]
SPECS += [(name, fn, _desktop_allowed, spec) for name, fn, spec in notifications.SPECS]


def _browser_allowed():
    """Offered only while the Chrome extension is connected, so Arty doesn't reach for it otherwise."""
    return computer.available() and _allowed("computer") and browser.connected()


SPECS += [(name, fn, _browser_allowed, spec) for name, fn, spec in browser.SPECS]
_BROWSER_TOOLS = {name for name, *_ in browser.SPECS}
_DESKTOP_TOOLS = {name for name, *_ in desktop.SPECS + notifications.SPECS}

_HANDLERS = {name: (fn, on) for name, fn, on, _ in SPECS}


def _args(arguments):
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


MAX_LABEL_CHARS = computer.MAX_LABEL_CHARS


def _capped(label):
    label = " ".join(str(label).split())
    return label if len(label) <= MAX_LABEL_CHARS else label[:MAX_LABEL_CHARS - 1] + "…"


def describe(name, arguments):
    """(label, where) for a tool call the app should show, else (None, None).
    where is "computer" (theirs)."""
    args = _args(arguments)
    if name in _COMPUTER_TOOLS:
        return _capped(computer.describe(name, args)), "computer"
    if name in _DESKTOP_TOOLS:
        return _capped(desktop.describe(name, args)), "computer"
    if name in _BROWSER_TOOLS:
        return _capped(browser.describe(name, args)), "computer"
    return None, None


def needs_ok(name, arguments, consented):
    """A question to put to them before running this call, or None."""
    if name in _COMPUTER_TOOLS:
        return computer.needs_ok(name, _args(arguments), consented)
    if name in _DESKTOP_TOOLS:
        return desktop.needs_ok(name, _args(arguments))
    if name in _BROWSER_TOOLS:
        return browser.needs_ok(name, _args(arguments))
    return None


def consent_given(name, arguments):
    if name in _BROWSER_TOOLS:
        browser.remember_consent(name, _args(arguments))


def consent_refused(name, arguments):
    if name in _BROWSER_TOOLS:
        browser.forget_question(name, _args(arguments))


def consent_key(name, question=None):
    return computer.consent_key_for(name, question) if name in _COMPUTER_TOOLS else name


def prompt_addition():
    """What she needs to know about tools that the definitions don't say."""
    if not _allowed("enabled"):
        return ""
    machines = ((computer.PROMPT if _computer_allowed() else "") + (desktop.PROMPT if _desktop_allowed() else "")
                + (browser.PROMPT if _browser_allowed() else ""))
    return machines + (WORKING if machines else "")


WORKING = """

While you're working on a machine, between one tool call and the next, you
don't need to perform: say nothing, or a few plain words about what you're
doing. Save your voice and personality for when you're done and talk to
them about how it went, which should sound like you."""


# Small models pick badly from ~30 tools, so each request is offered the groups its words
# point at, plus a few that are always useful. Nothing matched means the core set only.
CORE_TOOLS = {"computer_look", "computer_open", "computer_apps"}
WORK_TOOLS = {"computer_run_command", "computer_read_file", "computer_write_file", "computer_edit_file", "computer_list_files",
              "computer_search", "computer_find_files"}
STICKY_SECONDS = 1800
_last_used = {}
TOOL_GROUPS = [
    (re.compile(r"chrome|browser|\btabs?\b|website|\bsite\b|web ?page|\bpage\b|https?://|www\.|\.(com|co\.uk|org|net|io)\b|"
                r"google|youtube|search (for|online|the web)|online|log ?in|sign ?in|link", re.I),
     {"chrome_tabs", "chrome_open", "chrome_read", "chrome_click", "chrome_type", "chrome_tab"}),
    (re.compile(r"window|minimi|maximi|snap|full ?screen|focus|switch|desktop|move|resize|close|bring", re.I),
     {"desktop_windows", "desktop_window", "desktop_switch"}),
    (re.compile(r"volume|mute|louder|quieter|sound|music|song|spotify|play|pause|skip|next track|previous|brightness|"
                r"dim|bright|wi-?fi|internet|bluetooth|headphones|lock|disturb|dnd|battery|charge|cpu|memory|ram|disk|"
                r"space|status|how('s| is) (my|the|this) (pc|computer|laptop)", re.I),
     {"system_volume", "system_media", "system_brightness", "system_wifi", "system_bluetooth", "system_lock",
      "system_do_not_disturb", "system_status"}),
    (re.compile(r"notif|messag|missed|anyone|email|ping|alert|remind me when|let me know when", re.I),
     {"desktop_notifications", "desktop_notify"}),
    (re.compile(r"click|type|typing|write (in|into)|press|scroll|key|button|menu|shortcut", re.I),
     {"computer_click", "computer_type", "computer_key", "computer_scroll"}),
    (re.compile(r"file|folder|director|document|run |command|terminal|script|install|code|program|\.(py|txt|md|js|json|sh)\b|"
                r"download|save|edit|read|bug|fix|build|compil|test|debug|error|crash|refactor|repo|project|git|commit|"
                r"branch|function|class|method|variable|config|deploy|package|depend|log|grep|search (in|through|for)", re.I),
     WORK_TOOLS),
    (re.compile(r"clipboard|copied|paste|this (error|link|text|message|snippet)", re.I), {"read_clipboard"}),
    (re.compile(r"open .*(link|url)|go to", re.I), {"open_url"}),
]


def wanted_tools(request_text):
    """The tool names worth offering for this request."""
    names = set(CORE_TOOLS)
    recent = {name for name, when in _last_used.items() if time.time() - when < STICKY_SECONDS}
    for pattern, group in TOOL_GROUPS:
        if pattern.search(request_text or "") or group & recent:
            names |= group
    return names


def definitions(request_text=None):
    """Tool definitions for the brain. With request_text, only the groups it needs."""
    if not _allowed("enabled"):
        return None
    wanted = wanted_tools(request_text) if request_text is not None else None
    tools = [
        {"type": "function",
         "function": {"name": name, **spec}}
        for name, _fn, on, spec in SPECS if on() and (wanted is None or name in wanted)
    ]
    # The core tools first: they're offered every time, so the brain's cached prompt stays valid past them.
    tools.sort(key=lambda tool: tool["function"]["name"] not in CORE_TOOLS)
    return tools or None


_GARBLED = re.compile(r"<tool_call>|</?function[=>]|</?parameter[=>]")


def run(name, arguments):
    """Run one tool call. Errors are returned as text for the model."""
    fn, on = _HANDLERS.get(name, (None, None))
    if fn is None or not on():
        return f"There is no tool called {name}."
    _last_used[name] = time.time()
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except json.JSONDecodeError:
        return "Those arguments were not valid JSON."
    if not isinstance(args, dict):
        return "Those arguments were not an object."
    if any(isinstance(v, str) and _GARBLED.search(v) for v in args.values()):
        # The model wrote its next call (or several) inside this one's
        # arguments. Running it would open "LibreOffice <tool_call>..." or
        # type the markup into their document.
        return (f"Didn't run {name}: its arguments had another tool call written inside "
                "them. Make one tool call at a time, with only the plain value in each "
                "argument.")
    try:
        return str(fn(**args))
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}"
    except Exception as e:
        return f"{name} failed: {type(e).__name__}: {e}"
