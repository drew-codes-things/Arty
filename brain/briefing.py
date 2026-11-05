"""The morning and evening briefings, built by code from the real data. The model used to write them from six tool
calls and filled any empty part with placeholders ("[task name]", "Event 1 (with sender)"), so now each section is
only there when it has something real in it, and anything that couldn't be checked says so.

The briefing schedules carry "BRIEFING:morning" or "BRIEFING:evening" as their instructions; the scheduler hands
those here instead of to the model (see brain.scheduler).
"""
import logging
import re
import time
from datetime import date, datetime, timedelta

import calendar_feed
import email_watch
import store
import weather

PREFIX = "BRIEFING:"
KINDS = ("morning", "evening")
DEADLINE_DAYS = {"morning": 7, "evening": 2}
# Guests that are meant to be off unless being tested ("test-debian", "test-windows10").
OFF_BY_DESIGN = re.compile(r"^test-", re.IGNORECASE)
GUEST = re.compile(r"^(?P<kind>CT|VM) (?P<id>\d+) (?P<name>\S+)(?: \([^)]*\))?: (?P<state>\w+)", re.MULTILINE)
DAY = 86400

log = logging.getLogger("brain.briefing")


def kind_of(instructions: str) -> str | None:
    """"morning" or "evening" for a briefing schedule's instructions, else None."""
    text = (instructions or "").strip()
    kind = text.removeprefix(PREFIX)
    return kind if text.startswith(PREFIX) and kind in KINDS else None


def servers(call_tool) -> list[str]:
    status = str(call_tool("homelab__service_status", {}))
    guests = list(GUEST.finditer(status))
    if not guests:
        return ["I couldn't check your servers just now."]
    down = [f"{g['kind']} {g['id']} {g['name']} ({g['state']})" for g in guests
            if g["state"] != "running" and not OFF_BY_DESIGN.match(g["name"])]
    return [f"Down: {', '.join(down)}."] if down else ["All your servers are up."]


def weather_line(day: date, place: dict | None) -> list[str]:
    wanted = "today" if day == date.today() else "tomorrow"
    line = weather.answer(wanted, place)
    return [line] if line else ["I couldn't get the weather just now."]


def calendar(day: date) -> tuple[list[str], list[str]]:
    """(the day's events, problems reading the calendar)."""
    found = calendar_feed.event_list(2 + (day - date.today()).days)
    lines = []
    for event in found["events"]:
        start = datetime.fromisoformat(event["start"])
        if start.date() != day:
            continue
        when = "All day" if event["all_day"] else f"{start:%H:%M}"
        lines.append(f"- {when}: {event['title']}" + (f" ({event['location']})" if event.get("location") else ""))
    return lines, [f"I couldn't read part of your calendar ({'; '.join(found['problems'])})."] if found["problems"] else []


def deadlines(within_days: int, now: float) -> list[str]:
    with store.connect() as connection:
        rows = connection.execute("SELECT title, due FROM deadlines WHERE done = 0 AND due < ? ORDER BY due",
                                  (now + within_days * DAY,)).fetchall()
    lines = []
    for row in rows:
        left = (row["due"] - now) / DAY
        status = "overdue" if left < 0 else "due today" if left < 1 else f"{int(left)} days left"
        lines.append(f"- {row['title']}: {datetime.fromtimestamp(row['due']):%a %d %b %H:%M} ({status})")
    return lines


def reminders(day: date) -> list[str]:
    return [f"- {datetime.fromtimestamp(s['next_run']):%H:%M}: {s['message']}" for s in store.list_schedules()
            if s.get("message") and s.get("next_run") and datetime.fromtimestamp(s["next_run"]).date() == day]


def email(call_tool, complete) -> tuple[list[str], list[str]]:
    """(the important unread emails, problems checking)."""
    emails, problem = email_watch.fetch(call_tool)
    if problem:
        return [], [f"I couldn't check your email ({problem})."]
    important = email_watch.judge(emails, complete)
    return (email_watch.bullets(important, bold=False).splitlines() if important else []), []


def done_today(now: float) -> list[str]:
    midnight = datetime.combine(date.fromtimestamp(now), datetime.min.time()).timestamp()
    return [f"- {task['title']}" for task in store.list_tasks("done", 50) if (task.get("updated") or 0) >= midnight]


def section(heading: str, lines: list[str]) -> str:
    return f"{heading}\n" + "\n".join(lines) if lines else ""


def build(kind: str, call_tool, complete, place: dict | None = None, now: float | None = None) -> str:
    """The briefing's text. Sections with nothing in them are left out."""
    now = time.time() if now is None else now
    today = date.fromtimestamp(now)
    day = today if kind == "morning" else today + timedelta(days=1)
    events, problems = calendar(day)
    important, email_problems = email(call_tool, complete) if kind == "morning" else ([], [])
    parts = [
        "Good morning. Here's your day." if kind == "morning" else "Good evening. Here's tomorrow.",
        " ".join(servers(call_tool) + weather_line(day, place) + problems + email_problems),
        section("Calendar:", events),
        section("Reminders:", reminders(day)),
        section("Deadlines coming up:", deadlines(DEADLINE_DAYS[kind], now)),
        section("Important email:", important),
        section("Done today:", done_today(now)) if kind == "evening" else "",
    ]
    return "\n\n".join(part for part in parts if part)
