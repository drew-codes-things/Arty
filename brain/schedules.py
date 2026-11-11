import logging
import re
from datetime import datetime

import calendar_feed
import notify
import personal
import store
import timing
from archive import message_text
from reply_rules import APP_NOTES, CHAT_APP, CLAIMED, PROMISE, REMIND_REQUEST, reminder_from

log = logging.getLogger("brain")


REMINDER_RETRY_SECONDS = 60
REMINDER_MAX_ATTEMPTS = 10


def reminder_wording(text: str) -> str:
    """The reminder as the user should read it: "Reminder: drink some water" -> "Drink some water."."""
    text = re.sub(r"^\s*(⏰\s*)?(reminder|remember|remind me|don't forget)\s*(to\b)?\s*[:\-–]?\s*(to\s+)?", "", str(text), flags=re.I).strip()
    return (text[:1].upper() + text[1:]) if text else "Your reminder."


def create_schedule(arguments) -> tuple[dict | None, str]:
    reminder = str(arguments.get("reminder_text", "") or arguments.get("message", "")).strip() or None
    instructions = str(arguments.get("instructions", "")).strip() or (f"Remind the user: {reminder}" if reminder else "")
    if not instructions:
        return None, "Give reminder_text (for a reminder) or instructions (for a job)."
    try:
        cron, first, description = timing.parse_when(str(arguments.get("when", "")))
    except ValueError as error:
        return None, f"Couldn't schedule it: {error}"
    title = str(arguments.get("title") or (reminder or instructions)[:60])
    created = store.create_schedule(title, instructions, cron, description, first.timestamp(), reminder)
    minutes = max(1, round((first - datetime.now()).total_seconds() / 60))
    away = f"in {minutes} minutes" if minutes < 120 else f"in about {round(minutes / 60)} hours"
    return created, f"Scheduled #{created['id']} ({description}). First run {away}, at {first:%H:%M on %a %d %b}."


def ensure_reminder(conversation: list[dict], tools_used: list[str], answer: str) -> str:
    """"Remind me ..." must never end with a reminder that was promised but not set: if the model didn't
    call schedule_task, set it here from the user's own words, or say plainly that it wasn't set."""
    users = [m for m in conversation if m.get("role") == "user"]
    request = APP_NOTES.sub("", message_text(users[-1])) if users else ""
    if not REMIND_REQUEST.search(request) or "schedule_task" in tools_used:
        return answer
    parts = reminder_from(request)
    if parts is None:
        if PROMISE.search(answer) or CLAIMED.search(answer):
            return "I didn't manage to set that reminder. Tell me what and when, e.g. 'remind me to drink water in 5 minutes'."
        return answer  # e.g. Arty asked when
    log.info("setting a reminder the model only described")
    return set_reminder(parts, bool(CHAT_APP.search(request)))


def set_reminder(parts: tuple[str, str], asked_for_a_chat_app: bool = False) -> str:
    """Creates a reminder from (what, when) and says what happened. Reminders only go to Updates in Arty's apps,
    so one asked for "on Telegram" says so rather than pretending."""
    created, message = create_schedule({"reminder_text": parts[0], "when": parts[1]})
    if created is None:
        return f"I couldn't set that reminder: {message}"
    elsewhere = " (Not Telegram or Discord: everything I send you goes to Updates in the apps now.)" if asked_for_a_chat_app else ""
    return (f"Reminder set: \"{parts[0]}\" ({message.split('. ', 1)[0].split(' (', 1)[-1].rstrip(')')}). "
            f"It'll come to Updates on your phone and the desktop app.{elsewhere}")


def send_reminder(schedule: dict, now: datetime):
    try:
        notify.send("⏰ Reminder", reminder_wording(schedule["message"]))
    except Exception:
        attempts = (schedule.get("attempts") or 0) + 1
        if schedule["cron"] or attempts >= REMINDER_MAX_ATTEMPTS:
            log.exception("reminder %s couldn't be sent (attempt %d); not trying again", schedule["id"], attempts)
            return
        log.exception("reminder %s couldn't be sent; trying again in a minute", schedule["id"])
        store.update_schedule(schedule["id"], enabled=1, next_run=now.timestamp() + REMINDER_RETRY_SECONDS, attempts=attempts)


def guarded(job: str, action):
    try:
        action()
    except Exception:
        log.exception("%s failed", job)


def due_schedules_now(now: datetime) -> list[dict]:
    try:
        return store.due_schedules(now.timestamp())
    except Exception:
        log.exception("couldn't read the schedules")
        return []


def send_calendar_and_deadline_reminders():
    for event in calendar_feed.due_reminders():
        notify.send("📅 Coming up", calendar_feed.reminder_text(event))
    for message in personal.due_deadline_reminders():
        notify.send("⏰ Deadline", message)
