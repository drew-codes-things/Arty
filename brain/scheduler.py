import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import briefing
import connections
import desktop_look
import email_watch
import expiry
import memory_tidy
import notify
import store
import style
import timing

from reply_rules import APP_NOTES
from schedules import send_reminder, guarded, due_schedules_now, send_calendar_and_deadline_reminders
from connections import complete_text
from memory_facts import memory_hub

log = logging.getLogger("brain")


SCHEDULER_TICK_SECONDS = 30


def gather_writing() -> tuple[list[str], list[str]]:
    """The user's own writing, for style.py: their messages from the archive (every app) and their LinkedIn
    posts (read in the desktop browser; skipped if it isn't signed in)."""
    config = connections.load_config()
    messages = []
    for path in sorted(Path(config["conversations_dir"]).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                messages.append(APP_NOTES.sub("", str(json.loads(line).get("user") or "")).strip())
            except ValueError:
                continue
    connections.hub.refresh()
    posts_text = connections.hub.call("workspace__linkedin_posts", {"profile": "me", "max_posts": style.MAX_POSTS})
    posts = [] if posts_text.startswith(("BLOCKED", "browser error", "unknown tool", "tool error")) else style.split_posts(posts_text)
    return messages, posts


STYLE_RETRY_SECONDS = 24 * 3600
STYLE_STARTUP_GRACE_SECONDS = 300  # let the tool servers finish starting (they restart with the brain on a deploy)
_style_attempt = {"time": time.time() - STYLE_RETRY_SECONDS + STYLE_STARTUP_GRACE_SECONDS}


def run_schedule(schedule: dict, now: datetime):
    # Move it on before acting, so a failure below can't make it fire again every tick.
    if schedule["cron"]:
        due = datetime.fromtimestamp(schedule["next_run"]) if schedule.get("next_run") else now
        try:
            following = timing.following_run(schedule["cron"], due, now).timestamp()
        except ValueError as error:
            log.warning("schedule %s can't run again (%s); switched off", schedule["id"], error)
            store.update_schedule(schedule["id"], enabled=0, next_run=None, last_run=now.timestamp())
            following = None
        if following is not None:
            store.update_schedule(schedule["id"], next_run=following, last_run=now.timestamp())
    else:
        store.update_schedule(schedule["id"], enabled=0, next_run=None, last_run=now.timestamp())
    if schedule.get("message"):
        # A plain reminder: send the exact text, no model needed (and nothing to get wrong).
        send_reminder(schedule, now)
        return
    if kind := briefing.kind_of(schedule["instructions"]):
        # Built by code from the real data (the model made up placeholder events); in the background.
        threading.Thread(target=send_briefing, args=(schedule["title"], kind), name="briefing", daemon=True).start()
        return
    created = store.create_task(schedule["title"], schedule["instructions"], "schedule", schedule["id"])
    store.update_schedule(schedule["id"], last_task_id=created["id"])
    log.info("schedule %s queued task %s", schedule["id"], created["id"])


def start_style_refresh():
    if style.due() and time.time() - _style_attempt["time"] > STYLE_RETRY_SECONDS:
        # Weekly: relearn how the user writes, from what they've written since (in the background).
        _style_attempt["time"] = time.time()
        threading.Thread(target=style.refresh, args=(gather_writing, complete_text), name="style", daemon=True).start()


def start_email_watch():
    if email_watch.due(connections.load_config().get("email_watch", {})):
        # In the background: reading Gmail and sorting it can take a minute, and reminders mustn't wait.
        threading.Thread(target=email_watch.check_new, name="email-watch", daemon=True,
                         args=(connections.hub.call, complete_text, notify.send)).start()


def start_desktop_look():
    settings = connections.load_config().get("desktop_look", {})
    if desktop_look.due(settings):
        threading.Thread(target=desktop_look.tinker, args=(connections.hub.call, complete_text, desktop_look.persona_of(settings)),
                         name="desktop-look", daemon=True).start()


_expiry_running = threading.Lock()


def check_expiry():
    if not _expiry_running.acquire(blocking=False):
        return
    try:
        for message in expiry.due_warnings(connections.load_config().get("expiry_watch", {})):
            notify.send("⌛ Expiring soon", message, category=notify.ARTY)
    except Exception:
        log.exception("expiry watch failed")
    finally:
        _expiry_running.release()


def start_expiry_check():
    threading.Thread(target=check_expiry, name="expiry", daemon=True).start()


def tidy_completion(prompt: str) -> str:
    return complete_text(prompt, json_mode=True, think=False)


def start_memory_tidy():
    if memory_tidy.due(connections.load_config().get("memory_tidy", {})):
        threading.Thread(target=memory_tidy.run_in_background, args=(memory_hub, tidy_completion), name="memory-tidy",
                         daemon=True).start()


def scheduler_tick(now: datetime):
    for schedule in due_schedules_now(now):
        guarded(f"schedule {schedule['id']}", lambda schedule=schedule: run_schedule(schedule, now))
    guarded("calendar or deadline reminders", send_calendar_and_deadline_reminders)
    guarded("style refresh", start_style_refresh)
    guarded("email watch", start_email_watch)
    guarded("desktop look", start_desktop_look)
    guarded("expiry watch", start_expiry_check)
    guarded("memory tidy", start_memory_tidy)


def scheduler_loop():
    while True:
        guarded("scheduler tick", lambda: scheduler_tick(datetime.now()))
        time.sleep(SCHEDULER_TICK_SECONDS)


def send_briefing(title: str, kind: str):
    try:
        if not connections.hub.routes:
            connections.hub.refresh()
        text = briefing.build(kind, connections.hub.call, complete_text, connections.load_config().get("weather"))
    except Exception as error:  # a broken briefing must still say something, never vanish
        log.exception("%s briefing failed", kind)
        text = f"I couldn't put your {kind} briefing together ({error})."
    notify.send(title, text, category=notify.ARTY)
