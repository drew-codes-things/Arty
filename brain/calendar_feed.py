"""The user's calendar: Arty's own (events kept in its database, added by asking it) and, optionally,
Google Calendar read through secret iCal links (see calendar_events.py, run as a separate process).

Tools: calendar_events (both calendars together), calendar_add, calendar_change, calendar_remove.
due_reminders() is called by the scheduler and returns events starting within REMIND_MINUTES that
haven't been reminded about yet. Google links live in /srv/ai/secrets/calendar.env (bin/set-calendar).
"""
import json
import logging
import math
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import store
import timing

ENV_FILE = Path(os.environ.get("CALENDAR_ENV", "/srv/ai/secrets/calendar.env"))
TOOLS_PYTHON = os.environ.get("WHISPER_PYTHON", "/srv/ai/media/venv/bin/python")
SCRIPT = Path(__file__).with_name("calendar_events.py")
REMINDED_FILE = Path(os.environ.get("CALENDAR_REMINDED", "/srv/ai/brain/state/calendar-reminded.json"))
REMIND_MINUTES = 15
GOOGLE_CHECK_SECONDS = 300
FETCH_SECONDS = 90
REMINDED_KEPT = 500
DEFAULT_MINUTES = 60
MAX_OCCURRENCES = 200
MAX_DAYS = 366
MAX_EVENT_MINUTES = MAX_DAYS * 24 * 60
SUB_DAILY_REPEAT = ("calendar events repeat daily, weekly, monthly or yearly; for every few minutes or hours, "
                    "set a reminder instead")

MONTHS = {name: number for number, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], 1) for name in names}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH})\b(?:\s+(\d{{4}}))?", re.I)
MONTH_DAY = re.compile(rf"\b({MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
DAY_MONTH_SLASH_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
ROUND_THE_CLOCK = "24/7"
LEAP_YEAR_SEARCH = 8
DAY_OF_MONTH = re.compile(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", re.I)  # "the 25th": the next one
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
REPEATING = re.compile(r"\b(every|daily|weekdays|weekends|yearly|annually|(?:mon|tues|wednes|thurs|fri|satur|sun)days)\b", re.I)

log = logging.getLogger("brain.calendar")
_last_google_check = {"time": 0.0}


# ---------- when ----------

def _slash_date(text: str):
    return next((match for match in DAY_MONTH_SLASH_DATE.finditer(text) if match.group(0) != ROUND_THE_CLOCK), None)


def _real_date(text: str, year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError(f"'{text}' isn't a real date") from None


def _upcoming(text: str, month: int, day: int, today: date) -> date:
    for year in range(today.year, today.year + LEAP_YEAR_SEARCH + 1):
        try:
            found = date(year, month, day)
        except ValueError:
            continue
        if found >= today:
            return found
    raise ValueError(f"'{text}' isn't a real date")


def day_and_month(text: str) -> tuple[int, int] | None:
    for pattern, day_group, month_group in ((DAY_MONTH, 1, 2), (MONTH_DAY, 2, 1)):
        if match := pattern.search(text):
            found = (int(match[day_group]), MONTHS[match[month_group].lower()])
            break
    else:
        match = _slash_date(text)
        if not match:
            return None
        found = (int(match[1]), int(match[2]))
    _upcoming(text, found[1], found[0], date(2000, 1, 1))
    return found


def written_date(text: str, today: date) -> date | None:
    """A date written out in the text (25 Oct, Oct 25, 25/10, 2026-10-25, the 25th); not today/tomorrow/Friday."""
    if match := ISO_DATE.search(text):
        return _real_date(text, int(match[1]), int(match[2]), int(match[3]))
    for pattern, day_group, month_group in ((DAY_MONTH, 1, 2), (MONTH_DAY, 2, 1)):
        if match := pattern.search(text):
            month, day = MONTHS[match[month_group].lower()], int(match[day_group])
            return _real_date(text, int(match[3]), month, day) if match[3] else _upcoming(text, month, day, today)
    if match := _slash_date(text):
        month, day = int(match[2]), int(match[1])
        if match[3]:
            return _real_date(text, int(match[3]) + (2000 if len(match[3]) == 2 else 0), month, day)
        return _upcoming(text, month, day, today)
    if match := DAY_OF_MONTH.search(text):
        day, month, year = int(match[1]), today.month, today.year
        for _ in range(12):  # the next month that has that day, from this month on
            try:
                found = date(year, month, day)
                if found >= today:
                    return found
            except ValueError:
                pass
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
        raise ValueError(f"there's no {day}th in any month")
    return None


def _date_in(text: str, today: date) -> date | None:
    """A calendar date in the text: written out (see written_date), or today, tomorrow, Friday."""
    lowered = text.lower()
    if (found := written_date(text, today)) is not None:
        return found
    if "today" in lowered or "tonight" in lowered:
        return today
    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", lowered):
            ahead = (index - today.weekday()) % 7
            return today + timedelta(days=ahead or (7 if "next" in lowered else 0))
    return None


def parse_when(text: str, now: datetime | None = None) -> tuple[datetime, bool, str | None]:
    """(start, all day?, repeat rule or None) for an event time in the user's words. Raises ValueError.
    "Tuesday 3pm" is the next Tuesday; "every Tuesday 3pm" or "Tuesdays 3pm" repeats."""
    now = now or datetime.now()
    text = text.strip()
    clock = timing._clock(text)  # (hour, minute) when a time of day is given
    if REPEATING.search(text):
        cron, first, _ = timing.parse_when(text, now)
        if timing.repeats_within_a_day(cron):
            raise ValueError(SUB_DAILY_REPEAT)
        if clock is None and cron.count(" ") == 4:
            cron = "0 0 " + cron.split(" ", 2)[2]
            first = timing.next_run(cron, datetime.combine(now.date(), datetime.min.time()) - timedelta(minutes=1))
        return first, clock is None, cron
    try:
        day = _date_in(text, now.date())
    except ValueError:
        raise ValueError(f"'{text}' isn't a real date") from None
    if day is not None:
        if clock is None:
            return datetime.combine(day, datetime.min.time()), True, None
        start = datetime.combine(day, datetime.min.time()).replace(hour=clock[0], minute=clock[1])
        if start < now and day == now.date() and re.search("|".join(WEEKDAYS), text, re.I):
            start += timedelta(days=7)  # "Friday 3pm" said on a Friday evening means next week
        return start, False, None
    try:
        cron, first, _ = timing.parse_when(text, now)
    except ValueError:
        raise ValueError(f"I couldn't tell when '{text}' is. Use a day and time, e.g. 'Tuesday 3pm', "
                         "'25 Dec 14:00', '24/12', 'tomorrow 18:00', 'in 2 hours' or 'every Monday 6pm'.") from None
    if cron and timing.repeats_within_a_day(cron):
        raise ValueError(SUB_DAILY_REPEAT)
    return first, False, None


# ---------- Arty's own calendar ----------

def _occurrences(event: dict, start: float, end: float) -> list[tuple[float, float]]:
    """(start, end) of each time the event happens between start and end."""
    length = event["ends"] - event["starts"]
    if not event["repeat"]:
        return [(event["starts"], event["ends"])] if event["starts"] < end and event["ends"] > start else []
    found, moment = [], datetime.fromtimestamp(max(event["starts"], start - length) - 1)
    for _ in range(MAX_OCCURRENCES):
        try:
            moment = timing.next_run(event["repeat"], moment)
        except ValueError as error:
            log.warning("calendar event %s can't repeat (%s)", event.get("id"), error)
            break
        if moment.timestamp() >= end:
            break
        if moment.timestamp() + length > start:
            found.append((moment.timestamp(), moment.timestamp() + length))
    return found


def _as_item(event: dict, begins: float, finishes: float) -> dict:
    return {"id": event["id"], "uid": f"own-{event['id']}", "title": event["title"],
            "start": datetime.fromtimestamp(begins).astimezone().isoformat(),
            "end": datetime.fromtimestamp(finishes).astimezone().isoformat(),
            "all_day": bool(event["all_day"]), "location": event["location"], "repeats": bool(event["repeat"]), "calendar": "Arty"}


def own_events(days: float) -> list[dict]:
    start = datetime.combine(date.today(), datetime.min.time()).timestamp()  # earlier today counts as today
    end = time.time() + days * 86400
    return [_as_item(event, begins, finishes) for event in store.list_events()
            for begins, finishes in _occurrences(event, start, end)]


def _next(event: dict) -> str:
    """The event's next (or only) time, described."""
    upcoming = _occurrences(event, time.time() - 86400, time.time() + MAX_DAYS * 86400)
    return _describe(_as_item(event, *upcoming[0])) if upcoming else event["title"]


def checked_minutes(minutes: int) -> int:
    if minutes <= 0:
        raise ValueError("the length must be more than 0 minutes")
    if minutes > MAX_EVENT_MINUTES:
        raise ValueError(f"an event can last at most {MAX_DAYS} days")
    return minutes


def add(title, when, duration_minutes=None, location="", notes="") -> str:
    """The calendar_add tool."""
    title = str(title or "").strip()
    if not title:
        return "Give the event a title."
    try:
        start, all_day, repeat = parse_when(str(when or ""))
        minutes = checked_minutes(int(duration_minutes or 0) or (24 * 60 if all_day else DEFAULT_MINUTES))
    except ValueError as error:
        return f"Couldn't add it: {error}"
    event = store.add_event(title, start.timestamp(), start.timestamp() + minutes * 60, all_day, repeat,
                            str(location or ""), str(notes or ""))
    return f"Added to the calendar: {_next(event)}."


def change(event_id, title=None, when=None, duration_minutes=None, location=None) -> str:
    """The calendar_change tool."""
    try:
        event = store.get_event(int(event_id or 0))
    except (TypeError, ValueError):
        event = None
    if event is None:
        return f"There's no event #{event_id} (calendar_events shows the numbers)."
    fields = {}
    if title:
        fields["title"] = str(title)[:200]
    if location is not None:
        fields["location"] = str(location)[:200]
    length = event["ends"] - event["starts"]
    try:
        if when:
            start, all_day, repeat = parse_when(str(when))
            fields.update(starts=start.timestamp(), all_day=int(all_day), repeat=repeat)
            if all_day != bool(event["all_day"]):
                length = 24 * 3600 if all_day else DEFAULT_MINUTES * 60
        if duration_minutes:
            length = checked_minutes(int(duration_minutes)) * 60
    except ValueError as error:
        return f"Couldn't change it: {error}"
    fields["ends"] = fields.get("starts", event["starts"]) + length
    store.update_event(event["id"], **fields)
    return f"Changed: {_next(store.get_event(event['id']))}."


def remove(event_id) -> str:
    """The calendar_remove tool."""
    try:
        event = store.get_event(int(event_id or 0))
    except (TypeError, ValueError):
        event = None
    if event is None:
        return f"There's no event #{event_id}."
    store.delete_event(event["id"])
    return f"Removed #{event['id']} ({event['title']}) from the calendar."


# ---------- Google Calendar (optional) ----------

def _google_urls() -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CALENDAR_ICS_URLS="):
            return line.split("=", 1)[1].strip()
    return ""


def google_events(days: float) -> dict:
    """{"events": [...], "problems": [...]} from Google for the next `days` days. Raises RuntimeError."""
    if not _google_urls():
        return {"events": [], "problems": []}
    try:
        result = subprocess.run([TOOLS_PYTHON, str(SCRIPT), str(days)], capture_output=True, text=True,
                                timeout=FETCH_SECONDS, env={**os.environ, "CALENDAR_ICS_URLS": _google_urls()})
    except subprocess.TimeoutExpired:
        raise RuntimeError("Google Calendar took too long to load") from None
    if result.returncode != 0:
        log.warning("google calendar read failed: %s", result.stderr[-500:])
        raise RuntimeError("Google Calendar couldn't be read")
    return json.loads(result.stdout)


# ---------- both together ----------

def _describe(event: dict) -> str:
    start = datetime.fromisoformat(event["start"])
    when = f"{start:%a %d %b}, all day" if event["all_day"] else f"{start:%a %d %b %H:%M}-{datetime.fromisoformat(event['end']):%H:%M}"
    number = f"#{event['id']} " if event.get("id") else ""
    return (f"{number}{when}: {event['title']}" + (f" ({event['location']})" if event["location"] else "")
            + (" [repeats]" if event.get("repeats") else "") + (f" [{event['calendar']}]" if event["calendar"] != "Arty" else ""))


def events(days=1) -> str:
    """The calendar_events tool: Arty's own events and Google's, soonest first."""
    try:
        days = max(1, min(float(days or 1), MAX_DAYS))
    except (TypeError, ValueError):
        days = 1
    found, problems = own_events(days), []
    try:
        google = google_events(days)
        found += google["events"]
        problems += google["problems"]
    except (RuntimeError, ValueError) as error:
        problems.append(str(error))
    found.sort(key=lambda e: e["start"])
    span = "today" if days <= 1 else f"the next {int(days)} days"
    note = (" (" + "; ".join(problems) + ")") if problems else ""
    if not found:
        return f"Nothing in the calendar for {span}{note}."
    return (f"Calendar for {span}{note}. #numbers are Arty's own events, for calendar_change and calendar_remove:\n"
            + "\n".join(_describe(e) for e in found))


def event_list(days=30) -> dict:
    """Arty's own and Google's events as data, for the apps' calendar views: {"events": [...], "problems": [...]}."""
    days = float(days or 30)
    if not math.isfinite(days):
        raise ValueError("days must be a number")
    days = max(1, min(days, MAX_DAYS))
    found, problems = own_events(days), []
    try:
        google = google_events(days)
        found += google["events"]
        problems += google["problems"]
    except (RuntimeError, ValueError) as error:
        problems.append(str(error))
    found.sort(key=lambda e: e["start"])
    return {"events": found, "problems": problems}


def due_reminders() -> list[dict]:
    """Events starting in the next REMIND_MINUTES not yet reminded about. Google is checked every few minutes."""
    candidates = own_events(1)
    if _google_urls() and time.time() - _last_google_check["time"] >= GOOGLE_CHECK_SECONDS:
        _last_google_check["time"] = time.time()
        try:
            candidates += google_events(1)["events"]
        except (RuntimeError, ValueError) as error:
            log.warning("google calendar reminders skipped: %s", error)
    try:
        reminded = json.loads(REMINDED_FILE.read_text()) if REMINDED_FILE.exists() else []
    except ValueError:
        reminded = []
    now = datetime.now().astimezone()
    due = []
    for event in candidates:
        key = f"{event['uid']}@{event['start']}"
        minutes = (datetime.fromisoformat(event["start"]) - now).total_seconds() / 60
        if not event["all_day"] and 0 <= minutes <= REMIND_MINUTES and key not in reminded:
            due.append({**event, "minutes": round(minutes)})
            reminded.append(key)
    if due:
        REMINDED_FILE.parent.mkdir(parents=True, exist_ok=True)
        REMINDED_FILE.write_text(json.dumps(reminded[-REMINDED_KEPT:]))
    return due


def reminder_text(event: dict) -> str:
    start = datetime.fromisoformat(event["start"])
    soon = "now" if event["minutes"] <= 1 else f"in {event['minutes']} minutes"
    return f"{event['title']} starts {soon} ({start:%H:%M})" + (f", at {event['location']}" if event["location"] else "") + "."
