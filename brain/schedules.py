import logging
import re
from datetime import datetime

import calendar_feed
import notify
import personal
import safety
import store
import timing
from archive import message_text
from reply_rules import CHAT_APP, CLAIMED, PROMISE, REMIND_REQUEST, reminder_from, without_app_notes

log = logging.getLogger("brain")


REMINDER_RETRY_SECONDS = 60
REMINDER_MAX_ATTEMPTS = 10


def reminder_wording(text: str) -> str:
    """The reminder as the user should read it: "Reminder: drink some water" -> "Drink some water."."""
    text = re.sub(r"^\s*(⏰\s*)?(reminder|remember|remind me|don't forget)\s*(to\b)?\s*[:\-–]?\s*(to\s+)?", "", str(text), flags=re.I).strip()
    return (text[:1].upper() + text[1:]) if text else "Your reminder."


def create_schedule(arguments, crisis_help: str | None = None) -> tuple[dict | None, str]:
    reminder = str(arguments.get("reminder_text", "") or arguments.get("message", "")).strip() or None
    instructions = str(arguments.get("instructions", "")).strip() or (f"Remind the user: {reminder}" if reminder else "")
    if not instructions:
        return None, "Give reminder_text (for a reminder) or instructions (for a job)."
    if declined := safety.check_each((str(arguments.get("title") or ""), reminder or "", instructions), crisis_help):
        log.info("declined a %s request in a schedule", declined[0])
        return None, declined[1]
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
    request = without_app_notes(message_text(users[-1])) if users else ""
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


CANCEL_REMINDER = re.compile(
    r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you|would you)\s+)*"
    r"(?:cancel|delete|remove|stop|turn off|get rid of|scrap|drop)\s+(?:the\s+|my\s+|that\s+|this\s+)?(?:(?P<before>.+?)\s+)?"
    r"(?P<kind>reminders?|alarms?|scheduled (?:jobs?|tasks?))(?:\s+(?:to|about|for)\s+(?P<after>.+?))?\W*$",
    re.IGNORECASE | re.DOTALL)
EVERY_ONE = {"all", "every", "everything"}
NAME_FILLER = {"the", "my", "a", "an", "to", "about", "for", "that", "this", "one", "me", "i", "set", "please", "of", "them",
               "those", "these", "any", "your", "us", "it", "send", "sending", "give", "giving", "keep", "again", "anymore",
               "now"} | EVERY_ONE


def schedule_line(row: dict) -> str:
    return f"#{row['id']} {row['title']} ({row['description']})"


def active_listing(rows: list[dict]) -> str:
    return "; ".join(schedule_line(row) for row in rows) if rows else "nothing is scheduled"


def name_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9']+", text.lower()) if word not in NAME_FILLER}


def is_reminder(row: dict) -> bool:
    return bool(row.get("message"))


def schedule_words(row: dict) -> set[str]:
    return name_words(" ".join(str(row.get(key) or "") for key in ("title", "message", "instructions")))


def named_in_full(row: dict, wanted: set[str]) -> bool:
    title_words = name_words(str(row.get("title") or ""))
    return bool(title_words) and title_words <= wanted


def cancellable(active: list[dict], asked_for_reminders: bool, wanted: set[str]) -> list[dict]:
    if not asked_for_reminders:
        return active
    return [row for row in active if is_reminder(row) or named_in_full(row, wanted)]


def cancel_by_name(said: str) -> str | None:
    asked = CANCEL_REMINDER.match(said or "")
    if not asked:
        return None
    named = " ".join(part for part in (asked.group("before"), asked.group("after")) if part)
    wanted = name_words(named)
    asks_for_every_one = bool(EVERY_ONE & set(re.findall(r"[a-z0-9']+", named.lower())))
    if not wanted and not asks_for_every_one:
        return None
    active = store.list_schedules()
    asked_for_reminders = not asked.group("kind").lower().startswith("scheduled")
    matching = [row for row in cancellable(active, asked_for_reminders, wanted) if wanted <= schedule_words(row)]
    if not matching and not wanted:
        return f"There are no active reminders to cancel. Active: {active_listing(active)}."
    if not matching:
        return f"I couldn't find an active reminder about {named.strip()}, so nothing was cancelled. Active: {active_listing(active)}."
    if asks_for_every_one:
        return f"Which ones? These match: {active_listing(matching)}. Tell me which to cancel."
    if len(matching) > 1:
        return f"Which one? These match: {active_listing(matching)}. Tell me which to cancel."
    row = matching[0]
    store.update_schedule(row["id"], enabled=0, next_run=None)
    log.info("cancelled schedule %s by name", row["id"])
    return f"Cancelled: {row['title']} ({row['description']})."


def cancel_active(schedule_id: int) -> str:
    active = store.list_schedules()
    if not any(row["id"] == schedule_id for row in active):
        return f"No active scheduled job #{schedule_id}, so nothing was cancelled. Active: {active_listing(active)}."
    store.update_schedule(schedule_id, enabled=0, next_run=None)
    return "Cancelled."


def send_reminder(schedule: dict, now: datetime):
    try:
        notify.send("⏰ Reminder", reminder_wording(schedule["message"]), category=notify.REMINDERS)
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
        notify.send("📅 Coming up", calendar_feed.reminder_text(event), category=notify.CALENDAR)
    for message in personal.due_deadline_reminders():
        notify.send("⏰ Deadline", message, category=notify.REMINDERS)
