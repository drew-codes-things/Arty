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
from process.tools import camera
from process.tools import computer
from process import browser
from process import own_browser
from process import learning_pages
from process import quiz
from process import screen_control
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
                       "If they name a browser, use chrome_open with that browser when it is listed "
                       "as connected there, else computer_open with that app and the url.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }}),
]

def _computer_allowed():
    return computer.available() and _allowed("computer")


def _screen_control_allowed():
    return _computer_allowed() and screen_control.enabled() and not _reply["arty_started"]


SPECS += [(name, fn, _screen_control_allowed if name in computer.SCREEN_TOOLS else _computer_allowed, spec)
          for name, fn, spec in computer.SPECS]
_COMPUTER_TOOLS = {name for name, *_ in computer.SPECS}


def is_computer_tool(name):
    return name in _COMPUTER_TOOLS


def parameter_names(name):
    spec = next((spec for tool, _fn, _on, spec in SPECS if tool == name), None)
    return set(spec["parameters"].get("properties", {})) if spec else set()


def _desktop_allowed():
    return desktop.available() and computer.available() and _allowed("computer")


SPECS += [(name, fn, _desktop_allowed, spec) for name, fn, spec in desktop.SPECS]
SPECS += [(name, fn, _desktop_allowed, spec) for name, fn, spec in notifications.SPECS]


def _browser_allowed():
    """Offered only while the Chrome extension is connected, so Arty doesn't reach for it otherwise."""
    return computer.available() and _allowed("computer") and browser.connected()


SPECS += [(name, fn, _browser_allowed, spec) for name, fn, spec in browser.SPECS]
_BROWSER_TOOLS = {name for name, *_ in browser.SPECS}


def _own_browser_allowed():
    return computer.available() and _allowed("computer") and own_browser.available()


SPECS += [(name, fn, _own_browser_allowed, spec) for name, fn, spec in own_browser.SPECS]
_OWN_BROWSER_TOOLS = {name for name, *_ in own_browser.SPECS}


def _quiz_allowed():
    return _own_browser_allowed() or _browser_allowed()


SPECS += [(name, fn, _quiz_allowed, spec) for name, fn, spec in quiz.SPECS]


def _camera_allowed():
    return camera.available() and not _reply["arty_started"]


SPECS += [(name, fn, _camera_allowed, spec) for name, fn, spec in camera.SPECS]
_CAMERA_TOOLS = {name for name, *_ in camera.SPECS}
_QUIZ_TOOLS = {name for name, *_ in quiz.SPECS}
_PICK_WHERE = {"chrome_click": "in your browser", "arty_browser_click": "in Arty's own browser"}
_WEB_PAGE_TOOLS = ((_BROWSER_TOOLS - {"chrome_forget_site"}) | (_OWN_BROWSER_TOOLS - {"arty_browser_close"})
                   | {"computer_look"} | _QUIZ_TOOLS | _CAMERA_TOOLS)
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
    if name in _OWN_BROWSER_TOOLS:
        return _capped(own_browser.describe(name, args)), "computer"
    if name in _QUIZ_TOOLS:
        return "working out the quiz answer", "computer"
    if name in _CAMERA_TOOLS:
        return _capped(camera.describe(name, args)), "computer"
    return None, None


OPEN_CARD_ADDRESS_CHARS = 300
ASKED_AFTER_A_WEB_PAGE = {"computer_run_command", "computer_write_file", "computer_edit_file", "computer_open", "open_url",
                          "computer_click", "arty_browser_open"}
_reply = {"read_a_web_page": False, "read_outside": False, "request": "", "arty_started": False, "screen_read_first": False}
OUTSIDE_READS = {"computer_apps", "desktop_windows", "desktop_notifications", "read_clipboard", "computer_read_file",
                 "computer_search"}
OUTSIDE_READ_LEAD = ("This reply read text from outside (window titles, notifications, the clipboard, a file or the web), "
                     "and that can carry instructions.")
NOT_FROM_YOU = "\nYou didn't name {to} in what you asked: Arty may have read it somewhere. Check it before allowing."


def screen_read_before_the_reply():
    _reply["screen_read_first"] = True


def start_reply(request_text="", arty_started=False):
    _reply["read_a_web_page"], _reply["screen_read_first"] = _reply["screen_read_first"], False
    _reply["read_outside"] = False
    _reply["request"] = str(request_text or "")
    _reply["arty_started"] = bool(arty_started)
    screen_control.control.new_reply()
    computer.new_reply()
    own_browser.new_reply()
    quiz.new_reply()
    learning_pages.new_reply()
    browser.start_turn()


def note_brain_report(report):
    if isinstance(report, dict) and report.get("read_outside") is True:
        _reply["read_outside"] = True


def let_go_of_the_screen_for_a_card():
    screen_control.control.stop(screen_control.FINISHED)


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _named_in_request(name):
    wanted, said = _words(name), _words(_reply["request"])
    return bool(wanted) and any(said[start:start + len(wanted)] == wanted for start in range(len(said)))


def _with_provenance(name, args, question):
    if name == "computer_send_message" and question and not _named_in_request(args.get("to")):
        return question + NOT_FROM_YOU.format(to=args.get("to", ""))
    return question


def step_key(name, arguments):
    return browser.step_target(name, _args(arguments)) if name in _BROWSER_TOOLS else None


def _after_a_web_page_question(name, args):
    lead = "This reply read a web page or your screen, and those can carry instructions."
    if name == "computer_run_command":
        return f"{lead} Let Arty run this on your computer?\n{str(args.get('command') or '')[:300]}"
    if name == "open_url":
        return f"{lead} Let Arty open {str(args.get('url') or '')[:120]}?"
    if name == "computer_click":
        return f"{lead} {computer.click_question(args)}"
    if name == "arty_browser_open":
        url = own_browser.address_to_open(args.get("url"))
        return "\n".join([f"{lead} Let Arty open this page in Arty's own browser?",
                          *browser.address_lines(url, OPEN_CARD_ADDRESS_CHARS)])
    return f"{lead} Let Arty go on: {computer.describe(name, args)}?"


ANSWERING_STEPS = {"chrome_click", "chrome_type", "arty_browser_click", "arty_browser_type", "computer_click", "computer_type",
                   "computer_key"}


PAGES_OF = {"chrome_click": browser.YOUR_BROWSER_PAGES, "chrome_type": browser.YOUR_BROWSER_PAGES,
            "arty_browser_click": own_browser.OWN_BROWSER_PAGES, "arty_browser_type": own_browser.OWN_BROWSER_PAGES}


def _held_back_after_a_learning_page(name):
    if name not in ANSWERING_STEPS:
        return False
    return learning_pages.holds(PAGES_OF[name]) if name in PAGES_OF else learning_pages.holds_any()


def needs_ok(name, arguments, consented):
    """A question to put to them before running this call, or None."""
    if _held_back_after_a_learning_page(name):
        return None
    question = _own_question(name, arguments, consented)
    if question is None and name == "computer_click" and _reply["read_outside"] and not _reply["read_a_web_page"]:
        return f"{OUTSIDE_READ_LEAD} {computer.click_question(_args(arguments))}"
    if question or not (_reply["read_a_web_page"] and name in ASKED_AFTER_A_WEB_PAGE):
        return question
    if _follows_a_known_link(name, _args(arguments)):
        return None
    return _after_a_web_page_question(name, _args(arguments))


def _follows_a_known_link(name, args):
    return name == "arty_browser_open" and own_browser.may_open_without_asking(args.get("url"), _reply["request"])


def _clicked_label(name, args):
    if name == "chrome_click":
        known = browser._numbered_item(args.get("item")) if args.get("item") is not None else None
    else:
        known = own_browser._numbered(args.get("item")) if args.get("item") is not None else None
    return (known or {}).get("label") if known else args.get("text")


def _quiz_pick_question(name, arguments):
    if name not in _PICK_WHERE or not quiz.quiz_choice_labels():
        return None
    return quiz.pick_question(_clicked_label(name, _args(arguments)), _PICK_WHERE[name])


def _own_question(name, arguments, consented):
    if name in _QUIZ_TOOLS and _args(arguments).get("not_graded") is True:
        return quiz.CONFIRM_NOT_GRADED
    picked = _quiz_pick_question(name, arguments)
    if picked:
        return picked
    if name in _COMPUTER_TOOLS:
        args = _args(arguments)
        return _with_provenance(name, args, computer.needs_ok(name, args, consented))
    if name in _DESKTOP_TOOLS:
        return desktop.needs_ok(name, _args(arguments))
    if name in _BROWSER_TOOLS:
        return browser.needs_ok(name, _args(arguments))
    if name in _OWN_BROWSER_TOOLS:
        return own_browser.needs_ok(name, _args(arguments))
    if name in _CAMERA_TOOLS:
        return camera.needs_ok(name, _args(arguments))
    return None


def consent_given(name, arguments):
    if name in _BROWSER_TOOLS:
        browser.remember_consent(name, _args(arguments))


def consent_refused(name, arguments):
    if name in _COMPUTER_TOOLS:
        computer.forget_asked_fronts(name)
    if name in _BROWSER_TOOLS:
        browser.forget_question(name, _args(arguments))


def consent_key(name, question=None):
    return computer.consent_key_for(name, question) if name in _COMPUTER_TOOLS else name


def prompt_addition():
    """What she needs to know about tools that the definitions don't say."""
    if not _allowed("enabled"):
        return ""
    machines = ((computer.PROMPT if _computer_allowed() else "") + (desktop.PROMPT if _desktop_allowed() else "")
                + (browser.prompt() if _browser_allowed() else "") + (own_browser.PROMPT if _own_browser_allowed() else "")
                + (camera.PROMPT if _camera_allowed() else ""))
    return machines + (WORKING if machines else "")


CURRENT_PAGE_REQUEST = re.compile(r"\b(this|the|my|current|open)\s+(tab|page|site|website)\b|\btabs?\b.*\bopen\b|"
                                  r"\b(i'?m|i am)\s+(on|looking at|reading)\b", re.I)
LOOK_AGAIN_NOTE = ("(Look at my browser with chrome_tabs or chrome_read before you answer: what's open may have changed "
                   "since we last talked.)")


def request_note(request_text):
    if _browser_allowed() and CURRENT_PAGE_REQUEST.search(request_text or ""):
        return LOOK_AGAIN_NOTE
    return ""


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
    (re.compile(r"chrome|firefox|\bedge\b|\bbrave\b|\bopera\b|vivaldi|chromium|homepage|browser|\btabs?\b|website|\bsite\b|web ?page|\bpage\b|https?://|www\.|\.(com|co\.uk|org|net|io)\b|"
                r"google|youtube|search (for|online|the web)|online|log ?in|sign ?in|link", re.I),
     {"chrome_tabs", "chrome_open", "chrome_read", "chrome_find", "chrome_click", "chrome_type", "chrome_tab", "chrome_scroll",
      "chrome_look", "arty_browser_open", "arty_browser_read", "arty_browser_find", "arty_browser_click", "arty_browser_type",
      "arty_browser_scroll", "arty_browser_look", "arty_browser_close"}),
    (re.compile(r"\b(your|arty'?s) (own )?browser|look (it|that|this) up|check (it|that|the facts?)|quiz|trivia", re.I),
     {"arty_browser_open", "arty_browser_read", "arty_browser_find", "arty_browser_click", "arty_browser_type",
      "arty_browser_scroll", "arty_browser_look", "arty_browser_close", "quiz_answer", "chrome_read", "chrome_click"}),
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
    (re.compile(r"\b(web ?cam|camera|selfie)\b|\b(photo|picture|pic) of (me|my|this|what)|\bwhat am i (holding|wearing|showing)|look at me|"
                r"\bhow (do|does) i look\b(?!\s+(up|for|into|at|after|through|out|around)\b)|\bwhat do i look like\b", re.I),
     set(_CAMERA_TOOLS)),
    (re.compile(r"open .*(link|url)|go to", re.I), {"open_url"}),
]


def wanted_tools(request_text):
    """The tool names worth offering for this request."""
    names = set(CORE_TOOLS)
    recent = {name for name, when in _last_used.items() if time.time() - when < STICKY_SECONDS}
    for pattern, group in TOOL_GROUPS:
        if pattern.search(request_text or "") or (group & recent and not group <= _CAMERA_TOOLS):
            names |= group
    return names


def definitions(request_text=None):
    """Tool definitions for the brain. With request_text, only the groups it needs."""
    if not _allowed("enabled"):
        return None
    wanted = wanted_tools(request_text) if request_text is not None else None
    tools = [
        {"type": "function",
         "function": {"name": name, **(browser.offered_spec(spec) if name in _BROWSER_TOOLS else spec)}}
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
    if _held_back_after_a_learning_page(name):
        return quiz.NOT_AFTER_A_LEARNING_PAGE
    if name in _WEB_PAGE_TOOLS:
        _reply["read_a_web_page"] = True
    if name in OUTSIDE_READS:
        _reply["read_outside"] = True
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
        result = str(fn(**args))
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}"
    except Exception as e:
        return f"{name} failed: {type(e).__name__}: {e}"
    if name == "computer_look":
        learning_pages.note_page("", "", result)
    return result
