"""Talks to Arty's brain (on the homelab; the root of this project) and runs its requests for this computer.

The brain does the thinking, holds the persona and long-term memory, and has its own
tools (web, homelab, its own desktop). This app adds the user's computer: the
computer_* tools in process.tools are offered as client tools (header
X-Arty-Client-Tools: 1), and when the brain calls one it runs here, after asking
the user first for anything risky, and the result goes back for the next step.

The local conversation (with tool calls) lives in history_file, so a reply can be
cut short and the next turn still knows what happened.
"""
import json
import re
import threading
import time

import requests

from process import confirm
from process import route
from process.config import load_config, resolve_data, write_private
from process.tools import context
from process.tools import registry as tools

_config = load_config()
_brain = _config.get("brain") or {}
API_KEY = str(_brain.get("api_key") or "")
MODEL = str(_brain.get("model") or "assistant-core")
TIMEOUT_SECONDS = float(_brain.get("timeout_seconds", 600))
# The conversation sent with each message: HISTORY_TURNS to HISTORY_TURNS + HISTORY_STEP_TURNS exchanges.
HISTORY_TURNS = int(_brain.get("history_turns", 8))
HISTORY_STEP_TURNS = max(1, HISTORY_TURNS // 2)
MAX_TOOL_ROUNDS = int(_brain.get("max_tool_rounds", 60))
TOOL_RESULT_CHARS = 6000
STUCK_REPEATS = 3
MEDIA_PATH = re.compile(r"/arty-files/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[^/?#]+$")
MEDIA_TIMEOUT_SECONDS = 120

DESKTOP_NOTE = """

You are talking through Arty Desktop, the app on the user's own Linux PC (KDE Plasma).
The computer_* tools act on THEIR computer, the one in front of them, not on your own
desktop VM: use them when they ask you to do something on "my computer", "my screen",
or in an app they have open. Look at the screen (computer_look) before clicking, and
check it again after acting. Your reply is read aloud: keep it short and spoken, with no
markdown, lists or links unless they ask to see them."""

_history_lock = threading.Lock()


class BrainError(RuntimeError):
    """The brain couldn't be reached or refused the request; the message is for the user."""


class Activity(str):
    """Yielded by llm_stream around tool use. It is an empty string, so a caller that only
    joins the words is unaffected; one that wants to show progress reads `label` and
    `where`. With `confirm` set it is a question for the user, answered at POST /confirm."""

    def __new__(cls, label, where, confirm=None):
        piece = super().__new__(cls, "")
        piece.label, piece.where, piece.confirm = label, where, confirm
        return piece


# ---------- history ----------

def _history_path():
    return resolve_data(_config.get("history_file") or "chat_history.json")


def load_history():
    with _history_lock:
        path = _history_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []


def _save_history(messages):
    with _history_lock:
        path = _history_path()
        tmp = path.with_suffix(".tmp")
        write_private(tmp, json.dumps(messages, ensure_ascii=False))
        tmp.replace(path)


def save_turn(user_text, reply):
    """Keeps the exchange as the user heard it (a cut-off reply is saved cut off)."""
    if not user_text:
        return
    history = load_history() + [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply or "(no reply)"}]
    _save_history(trimmed(history))


def trimmed(history):
    """Cuts the conversation back to HISTORY_TURNS exchanges in one go once it's grown by HISTORY_STEP_TURNS.
    Its start then stays put for several messages, so the brain's cached prompt stays valid; dropping the
    oldest exchange every time would make the model re-read the whole conversation on every message."""
    if len(history) > (HISTORY_TURNS + HISTORY_STEP_TURNS) * 2:
        return history[-HISTORY_TURNS * 2:]
    return history


def reset_history():
    _save_history([])


# ---------- the brain ----------

def _base():
    return route.base()


def _headers(**extra):
    return {"Authorization": f"Bearer {API_KEY}", **extra, **route.access_headers()}


def _stream(messages, offer_tools, lead=""):
    """Asks the brain, yielding the reply's text as it arrives (the brain sends the first sentences while
    the model is still writing), with `lead` in front of the first piece. Returns the whole message."""
    body = {"model": MODEL, "messages": messages, "stream": True}
    if offer_tools:
        body["tools"] = offer_tools
    try:
        response = requests.post(
            f"{_base()}/chat/completions", json=body, timeout=(10, TIMEOUT_SECONDS), stream=True,
            # This app drives the user's own browser, screen and windows; the brain's versions act on its VM.
            headers=_headers(**{"X-Arty-Client-Tools": "1", "X-Arty-Skip-Groups": "browser,screen,windows"}),
        )
    except requests.ConnectionError:
        raise BrainError(f"Can't reach Arty's brain at {route.describe()}. Is the AI container (CT 104) on, and is this PC online?") from None
    except requests.Timeout:
        raise BrainError("Arty's brain took too long to answer.") from None
    with response:
        if response.status_code == 401:
            raise BrainError("Arty's brain refused the API key. Run setup-linux.sh again to fetch it.")
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = response.text[:200]
            raise BrainError(f"Arty's brain hit an error: {detail or response.status_code}")
        content, calls = [], {}
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                if line == "data: [DONE]":
                    break
                delta = ((json.loads(line[len("data: "):]).get("choices") or [{}])[0]).get("delta") or {}
                if delta.get("content"):
                    yield (lead if not content else "") + delta["content"]
                    content.append(delta["content"])
                for tool_call in delta.get("tool_calls") or []:
                    index = tool_call.pop("index", len(calls))
                    calls[index] = tool_call  # the brain sends each call whole
        except requests.RequestException:
            raise BrainError("Lost the connection to Arty's brain partway through the answer.") from None
        except ValueError as error:
            raise BrainError(f"Arty's brain sent something unreadable: {error}") from None
    return {"role": "assistant", "content": "".join(content), "tool_calls": [calls[i] for i in sorted(calls)]}


ANNOUNCEMENT_WAIT_SECONDS = 55
ANNOUNCEMENT_RETRY_SECONDS = 30


def watch_announcements(on_announcement):
    """Follows the brain's feed of what Arty tells the user (reminders, briefings, task results,
    homelab alerts), from now on, calling on_announcement(item) with each feed item."""
    def follow():
        last = None
        while True:
            try:
                wait = 0 if last is None else ANNOUNCEMENT_WAIT_SECONDS
                response = requests.get(f"{_base().removesuffix('/v1')}/v1/announcements", timeout=wait + 15,
                                        params={"after": last or 0, "wait": wait}, headers=_headers())
                response.raise_for_status()
                feed = response.json()
                if last is not None:  # the first call only learns where "now" is
                    for item in feed["announcements"]:
                        on_announcement(item)
                last = feed["last"]
            except (requests.RequestException, ValueError, KeyError) as error:
                print(f"[brain] announcements unavailable ({error}); retrying", flush=True)
                time.sleep(ANNOUNCEMENT_RETRY_SECONDS)
    threading.Thread(target=follow, name="announcements", daemon=True).start()


def decide_task(task_id, action):
    """Answers a paused background task (approve, deny, done or cancel); returns the brain's reply."""
    try:
        response = requests.post(f"{_base()}/tasks/{int(task_id)}/{action}", json={}, timeout=30, headers=_headers())
    except requests.RequestException as error:
        raise BrainError(f"Couldn't reach Arty's brain: {error}") from None
    try:
        return response.json().get("message") or response.json().get("error", {}).get("message", "") or "Done."
    except ValueError:
        return f"Arty's brain answered {response.status_code}."


def media(link):
    found = MEDIA_PATH.search(link.split("?", 1)[0])
    if not found or ".." in found.group(0):
        raise ValueError("That isn't a file from Arty.")
    try:
        response = requests.get(f"{_base().removesuffix('/v1')}{found.group(0)}", headers=_headers(),
                                timeout=(10, MEDIA_TIMEOUT_SECONDS), stream=True)
    except requests.RequestException as error:
        raise BrainError(f"Couldn't fetch that file from Arty's brain: {error}") from None
    if response.status_code != 200:
        response.close()
        raise BrainError(f"Arty's brain answered {response.status_code} for that file; it may have expired.")
    return response


def updates():
    """(the latest things Arty has sent, oldest first, the ids of tasks still waiting for an answer), for the
    window's Updates view."""
    headers = _headers()
    try:
        feed = requests.get(f"{_base()}/announcements", params={"after": 0, "wait": 0}, timeout=15, headers=headers)
        feed.raise_for_status()
        waiting = requests.get(f"{_base()}/tasks", params={"status": "waiting", "limit": 30}, timeout=15, headers=headers)
        waiting.raise_for_status()
        return feed.json()["announcements"], [task["id"] for task in waiting.json().get("tasks", [])]
    except (requests.RequestException, ValueError, KeyError) as error:
        raise BrainError(f"Couldn't reach Arty's updates: {error}") from None


def calendar_call(method, path="", body=None):
    """Arty's calendar on the brain (GET events, POST an event, DELETE /<id>), for the window's calendar view."""
    try:
        response = requests.request(method, f"{_base()}/calendar{path}", json=body, timeout=60, headers=_headers())
        return response.status_code, response.json()
    except (requests.RequestException, ValueError) as error:
        raise BrainError(f"Couldn't reach Arty's calendar: {error}") from None


def describe_endpoint():
    return _base()


def current_model():
    return MODEL


def set_model(model_id):
    """Uses this brain model from the next message on ("assistant-core" is Arty's main model)."""
    global MODEL
    MODEL = model_id


def list_models():
    """The brain's models as [{id, name}], or raises BrainError."""
    try:
        response = requests.get(f"{_base()}/models", timeout=10, headers=_headers())
        response.raise_for_status()
    except requests.RequestException as error:
        raise BrainError(f"Couldn't list Arty's models: {error}") from None
    return [{"id": m["id"], "name": "Arty (main model)" if m["id"] == "assistant-core" else m.get("name", m["id"]),
             "model": m.get("model", ""), "description": m.get("description", "")}
            for m in response.json().get("data", [])]


def _clip(text):
    text = str(text)
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + "\n…(cut short)"


def llm_stream(user_input, extra_system=None, note=""):
    """Yields the reply text (and Activity markers while tools run). The caller saves the
    turn with save_turn, since only it knows how much was actually heard. `note` rides on
    this message only (small models heed it there far more than in the system prompt)."""
    system = DESKTOP_NOTE + tools.prompt_addition() + context.NOTE_EXPLAINED + (extra_system or "")
    messages = [{"role": "system", "content": system.strip()}]
    messages += trimmed(load_history())
    messages.append({"role": "user", "content": user_input + (f"\n\n{note}" if note else "") + context.as_note()})
    offer = tools.definitions(user_input)
    consented = set()  # things they've said yes to during this reply
    recent_calls = []
    lead = ""  # sets a later step's words apart from what was said before its tools ran
    for _round in range(MAX_TOOL_ROUNDS + 1):
        reply = yield from _stream(messages, offer, lead)
        calls = reply.get("tool_calls") or []
        content = reply.get("content") or ""
        if content.strip():
            lead = "\n\n"
        if not calls:
            return
        messages.append({"role": "assistant", "content": content or None, "tool_calls": calls})
        for call in calls:
            function = call.get("function") or {}
            name, arguments = function.get("name", ""), function.get("arguments") or "{}"
            label, where = tools.describe(name, arguments)
            if label:
                yield Activity(label, where)
            question = tools.needs_ok(name, arguments, consented)
            if question:
                ask = confirm.request(question)
                yield Activity(question, where, confirm=ask.id)
                if confirm.wait(ask):
                    consented.add(tools.consent_key(name))
                    result = tools.run(name, arguments)
                else:
                    result = "They didn't allow that, so it wasn't done. Don't try another way round it; say so and ask what they'd like."
            else:
                result = tools.run(name, arguments)
            print(f"[tool] {name} -> {len(str(result))} chars", flush=True)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": _clip(result)})
        # Lets the caller notice a hang-up between steps of a long job.
        yield ""
        signature = json.dumps([(c.get("function") or {}).get("name") for c in calls] + [(c.get("function") or {}).get("arguments") for c in calls])
        recent_calls = (recent_calls + [signature])[-STUCK_REPEATS:]
        if len(recent_calls) == STUCK_REPEATS and len(set(recent_calls)) == 1:
            # The same call over and over: stop offering tools so it has to answer.
            offer = None
    yield "I stopped after a lot of steps. Tell me if you want me to keep going."
