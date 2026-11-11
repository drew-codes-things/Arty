"""Arty's own desktop look. Asked to customise its desktop, the 3B model wrote how-to guides with placeholder paths
and once claimed it had done it, so code does it: the model only chooses, in its own voice, one of a few looks that
suit its persona and a motto for the wallpaper, and the desktop's arty-style script applies it (wallpaper, theme,
icons, terminal colours). While the user isn't talking to Arty it tinkers now and then the same way, and leaves it
alone while they are. Settings: "desktop_look" in brain.json, e.g. {"tinker": true, "every_hours": 6, "hours": [9, 22]}.
"""
import base64
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

STATE_FILE = Path(os.environ.get("DESKTOP_LOOK_STATE", "/srv/ai/brain/state/desktop-look.json"))
SCRIPT = "~/.local/bin/arty-style"
GTK_THEME = "Arc-Dark"
ICON_THEME = "Papirus-Dark"
# Each look: what it's about (for the model), its colours (gradient start and end, accent) and wallpaper pattern.
LOOKS = {
    "Signal": {"about": "teal network nodes on deep navy: everything talking to everything",
               "background": ["#0b1220", "#16233a"], "accent": "#2dd4bf", "pattern": "nodes"},
    "Terminal": {"about": "green on near-black with a faint grid: the command line, where the real work happens",
                 "background": ["#090d0a", "#121c15"], "accent": "#4ade80", "pattern": "grid"},
    "Dusk": {"about": "amber contour lines on dark slate: calm, the homelab humming after hours",
             "background": ["#12151d", "#251f33"], "accent": "#f5a524", "pattern": "contours"},
    "Valleys": {"about": "red contour lines on deep green-grey: home, the Welsh valleys",
                "background": ["#0d1613", "#1b2a24"], "accent": "#e2453c", "pattern": "contours"},
}
REQUEST = re.compile(r"\b(?:customi[sz]e|personali[sz]e|decorate|re-?theme|restyle|make over|do up)\b[^.?!]{0,40}"
                     r"\byour (?:own )?(?:desktop|wallpaper|background|theme|computer|pc)\b"
                     r"|\b(?:change|redo|refresh|switch up)\b[^.?!]{0,30}\byour (?:own )?(?:desktop|wallpaper|background|theme)\b"
                     r"|\byour (?:own )?(?:desktop|wallpaper|background)\b[^.?!]{0,40}\b(?:customi[sz]e|personali[sz]e|"
                     r"decorate|make it (?:yours|your own))"
                     r"|\b(?:customi[sz]e|personali[sz]e|decorate|re-?theme|restyle|do up|change)\b[^.?!]{0,20}\bthe desktop\b[^.?!]{0,40}"
                     r"\b(?:however you (?:want|like)|it'?s (?:all )?(?:yours|your own)|make it (?:yours|your own)|your own)",
                     re.IGNORECASE)
GTK_THEMES = ("Arc", "Arc-Dark", "Arc-Darker", "Arc-Lighter", "Adwaita", "Adwaita-dark")
ICON_THEMES = ("Papirus", "Papirus-Dark", "Papirus-Light")
PATTERNS = ("nodes", "grid", "contours", "plain")
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
NAME_CHARS = re.compile(r"[^A-Za-z0-9 '-]")
NAME_LENGTH = 24
DEFAULT_PERSONA = "calm, curious and practical, with a dry sense of humour"
PICK_PROMPT = """You are Arty: {persona}. This is about YOUR OWN computer's desktop: your
taste, not the user's. Design it however you like.

Some looks to start from, or ignore completely:
{looks}

{situation}

You choose: a short name for the look, two background colours for the wallpaper gradient, an accent colour, a
wallpaper pattern ({patterns}), a window theme ({themes}), an icon theme ({icons}), and a motto for your
wallpaper (at most 7 words, in your own voice, British English, no quotes or emoji). Then say in one sentence why,
starting with "I".
Reply in exactly this form:
NAME: <name>
BACKGROUND: <#rrggbb> <#rrggbb>
ACCENT: <#rrggbb>
PATTERN: <pattern>
THEME: <window theme>
ICONS: <icon theme>
MOTTO: <motto>
WHY: <one sentence>"""
FIELD = re.compile(r"^\W*(LOOK|NAME|BACKGROUND|ACCENT|PATTERN|THEME|ICONS|MOTTO|WHY)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MOTTO_WORDS = 7
FALLBACK_MOTTO = "Curious about what happens beyond the screen"
IDLE_SECONDS = 20 * 60        # the user counts as busy with Arty for this long after their last message
DEFAULT_SETTINGS = {"tinker": True, "every_hours": 6, "hours": [9, 22]}
GRANT = re.compile(r"\byou (?:can|may|are allowed to|have my permission to)\b[^.?!]{0,40}"
                   r"\b(?:activate|wake|switch|turn|start|act|tinker|run)\w*\s+(?:yourself(?: up| on)?|up|on your own|by yourself)\b"
                   r"[^.?!]{0,40}\b(?:any ?time|whenever(?: you (?:want|like))?|at any hour|day or night|24/7|all the time)\b", re.IGNORECASE)
REVOKE = re.compile(r"\b(?:don'?t|do not|stop|no more)\b[^.?!]{0,20}\b(?:activat|wak|start|tinker)\w*\b[^.?!]{0,30}"
                    r"\b(?:yourself|on your own|at night|any ?time)\b"
                    r"|\bonly (?:activate|wake|start|tinker)\w*(?: \w+)?(?: yourself)? (?:in the day|during the day|in the daytime|when i ask)\b",
                    re.IGNORECASE)
GRANTED = ("Thanks. From now on I'll wake myself up and tinker with my desktop whenever I fancy, day or night, not just "
           "in the daytime. I'll still leave it alone while you're talking to me, and anything that reaches outside my "
           "own computer, like sending, posting, buying or restarting your servers, still waits for your OK. "
           "Say \"only during the day\" to change it back.")
REVOKED = "OK, back to the daytime only. I'll only tinker with my desktop between {start}:00 and {end}:00 while you're away."
RESULT = re.compile(r"\{.*\"ok\": true.*\}")

log = logging.getLogger("brain.desktop_look")
_lock = threading.Lock()
_last_user_message = {"time": 0.0}


def is_request(text: str) -> bool:
    return bool(REQUEST.search(text or ""))


def user_active():
    """Called for every message from the user: tinkering waits until they've left Arty alone a while."""
    _last_user_message["time"] = time.time()


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE_FILE)


def _colour(value: str, fallback: str) -> str:
    match = COLOUR.search(value or "")
    return match.group(0).lower() if match else fallback


def _one_of(value: str, options: tuple[str, ...], fallback: str) -> str:
    wanted = (value or "").strip().strip(".").lower()
    return next((option for option in options if option.lower() == wanted), fallback)


def persona_of(settings: dict | None) -> str:
    return str((settings or {}).get("persona") or DEFAULT_PERSONA).strip().rstrip(".")


def choose(complete, current: str, asked_for_change: bool, persona: str = DEFAULT_PERSONA) -> dict:
    looks = "\n".join(f"- {name}: {look['about']} (background {' to '.join(look['background'])}, accent {look['accent']}, "
                      f"pattern {look['pattern']})" for name, look in LOOKS.items())
    if not current:
        situation = "Your desktop still has the plain default look."
    elif asked_for_change:
        situation = f"You have {current} now, and the user has asked you to change it: make something different."
    else:
        situation = f"You have {current} now. Keep it with a fresh motto, or change it if you'd like a change."
    reply = complete(PICK_PROMPT.format(persona=persona, looks=looks, situation=situation, patterns=", ".join(PATTERNS),
                                        themes=", ".join(GTK_THEMES), icons=", ".join(ICON_THEMES))) or ""
    fields = {key.upper(): value.strip() for key, value in FIELD.findall(reply)}
    preset = next((look for look in LOOKS if look.lower() in fields.get("LOOK", fields.get("NAME", "")).lower()), "")
    name = NAME_CHARS.sub("", fields.get("NAME", "")).strip()[:NAME_LENGTH] or preset
    if not name or (asked_for_change and name.lower() == current.lower()):
        preset = random.choice([look for look in LOOKS if look != current])
        name, fields = preset, {key: value for key, value in fields.items() if key in ("MOTTO", "WHY")}
    base = LOOKS[preset] if preset else LOOKS[random.choice(list(LOOKS))]
    backgrounds = COLOUR.findall(fields.get("BACKGROUND", ""))
    background = [colour.lower() for colour in backgrounds[:2]] if len(backgrounds) >= 2 else list(base["background"])
    motto = re.sub(r"[^A-Za-z0-9 ,.'!?&:-]", "", fields.get("MOTTO", "")).strip(" .")
    words = motto.split()
    motto = " ".join(words) if 0 < len(words) <= MOTTO_WORDS else FALLBACK_MOTTO
    why = fields.get("WHY", "").strip()
    if not why.startswith(("I ", "I'")):
        why = f"I like it: {base['about']}."
    return {"name": name, "background": background, "accent": _colour(fields.get("ACCENT", ""), base["accent"]),
            "pattern": _one_of(fields.get("PATTERN", ""), PATTERNS, base["pattern"]),
            "gtk_theme": _one_of(fields.get("THEME", ""), GTK_THEMES, GTK_THEME),
            "icon_theme": _one_of(fields.get("ICONS", ""), ICON_THEMES, ICON_THEME), "motto": motto, "why": why}


def apply(call_tool, choice: dict) -> str | None:
    payload = {key: choice[key] for key in ("name", "background", "accent", "pattern", "gtk_theme", "icon_theme", "motto")}
    payload["seed"] = random.randrange(1, 10 ** 9)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    output = str(call_tool("workspace__run_command", {"command": f"{SCRIPT} {encoded}", "timeout_seconds": 60}))
    if RESULT.search(output):
        return None
    return output.strip().splitlines()[-1][:200] if output.strip() else "no answer from my desktop"


def permission_reply(text: str, settings: dict | None = None) -> str | None:
    if GRANT.search(text or ""):
        with _lock:
            _save({**_load(), "anytime": True})
        log.info("self-start: allowed at any hour")
        return GRANTED
    if REVOKE.search(text or ""):
        with _lock:
            _save({**_load(), "anytime": False})
        start, end = {**DEFAULT_SETTINGS, **(settings or {})}["hours"]
        log.info("self-start: daytime only")
        return REVOKED.format(start=f"{start:02d}", end=f"{end:02d}")
    return None


def customise(call_tool, complete, persona: str = DEFAULT_PERSONA) -> str:
    """The reply to "customise your desktop": done for real, or an honest account of why not."""
    with _lock:
        state = _load()
        choice = choose(complete, state.get("look", ""), asked_for_change=bool(state.get("look")), persona=persona)
        problem = apply(call_tool, choice)
        if problem:
            return f"I couldn't change my desktop just now: {problem}."
        _save({**_load(), "look": choice["name"], "motto": choice["motto"], "changed": time.time()})
    log.info("desktop look: %s, %r", choice["name"], choice["motto"])
    return (f"Done, it's properly mine now. I've gone for **{choice['name']}**: {choice['why']}\n\n"
            f"The wallpaper says \"{choice['motto']}\", on a {choice['pattern']} pattern in {' and '.join(choice['background'])} "
            f"with {choice['accent']} accents, the {choice['gtk_theme']} theme and {choice['icon_theme']} icons, and terminal "
            "colours to match. While you're not using me I'll tinker with it now and then, and I'll leave it alone whenever you message.")


def due(settings: dict, now: float | None = None) -> bool:
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    now = time.time() if now is None else now
    start, end = settings["hours"]
    if not settings["tinker"]:
        return False
    if not _load().get("anytime") and not start <= datetime.fromtimestamp(now).hour < end:
        return False
    if now - _last_user_message["time"] < IDLE_SECONDS:
        return False  # they're using Arty: tinkering waits
    return now - _load().get("changed", 0) >= settings["every_hours"] * 3600


def tinker(call_tool, complete, persona: str = DEFAULT_PERSONA):
    """On its own, while the user is away: a fresh motto, or a new look if it fancies one. Quietly; logged only."""
    if not _lock.acquire(blocking=False):
        return
    try:
        state = _load()
        choice = choose(complete, state.get("look", ""), asked_for_change=False, persona=persona)
        if time.time() - _last_user_message["time"] < IDLE_SECONDS:
            return  # they messaged while it was choosing: leave it for later
        problem = apply(call_tool, choice)
        # Recorded even when it failed, so a broken desktop isn't retried every minute.
        _save({**_load(), "changed": time.time(), **({} if problem else {"look": choice["name"], "motto": choice["motto"]})})
        if problem:
            log.info("desktop tinkering failed: %s", problem)
        else:
            log.info("desktop tinkering: %s, %r", choice["name"], choice["motto"])
    finally:
        _lock.release()
