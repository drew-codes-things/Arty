"""Turning "when" phrases into schedules, and working out when a schedule runs next.

Repeating schedules are stored as 5-field cron expressions (minute hour day month
weekday, weekday 0 = Sunday). One-off times ("in 2 hours", "tomorrow 08:00") have
no cron and run once. All times are the server's local time (Europe/London).
"""
import calendar
import re
from datetime import datetime, timedelta

WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
CRON_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
SEARCH_LIMIT = timedelta(days=366 * 8 + 2)
HELP = (
    "Use e.g. 'daily 08:00', 'weekdays 07:30', 'mondays 09:00', 'every 2 hours', 'hourly', "
    "'in 30 minutes', 'tomorrow 18:00', '2026-12-24 09:00', or a cron expression like '0 8 * * 1-5'."
)


SPOKEN_AMOUNTS = [(r"\bhalf an hour\b", "30 minutes"), (r"\b(a|an|one) (second|minute|hour|day|week|month)\b", r"1 \2"),
                  (r"\ba couple of (seconds|minutes|hours|days|weeks|months)\b", r"2 \1"),
                  (r"\ba few (seconds|minutes|hours|days|weeks|months)\b", r"3 \1")]
RELATIVE = re.compile(r"in (\d+) (seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)")
UNIT_SECONDS = {"sec": 1, "min": 60, "hou": 3600, "hrs": 3600, "hr": 3600, "day": 86400, "wee": 7 * 86400}
MAX_AHEAD = timedelta(days=366 * 10)
TOO_FAR = "That's too far ahead: I can set things up to 10 years away."
INTERVAL = re.compile(r"every (\d+)([mh])")
MINUTES_IN_HOUR = 60
HOURS_IN_DAY = 24
YEARLY = re.compile(r"\b(every|each|yearly|annually)\b")
WEEKDAY_NAMED = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)")
PARTS_OF_DAY = [("morning", (9, 0)), ("afternoon", (14, 0)), ("evening", (18, 0)), ("tonight", (20, 0)), ("night", (20, 0))]


# "at 6" with no am/pm: how people usually mean it (1-6 afternoon/evening, 7-11 morning, 12 midday).
BARE_HOUR = re.compile(r"\bat (\d{1,2})\b(?!\s*(?:[:./\d]|am|pm|st|nd|rd|th)\b)", re.IGNORECASE)
LAST_AFTERNOON_HOUR = 6


def _clock(text: str) -> tuple[int, int] | None:
    """The time of day in the text: the first number with minutes or am/pm (so "25 Oct 14:00" is 14:00),
    else noon / midnight, else a bare hour after "at"."""
    match = next((m for m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
                  if m.group(2) is not None or m.group(3) is not None), None)
    if not match:
        if re.search(r"\b(noon|midday)\b", text, re.IGNORECASE):
            return 12, 0
        if re.search(r"\bmidnight\b", text, re.IGNORECASE):
            return 0, 0
        if bare := BARE_HOUR.search(text):
            hour = int(bare.group(1))
            if 1 <= hour <= LAST_AFTERNOON_HOUR:
                return hour + 12, 0
            return (hour, 0) if hour <= 23 else None
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _months_later(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def _relative(amount: int, unit: str, exact_now: datetime) -> datetime:
    try:
        if unit.startswith("month"):
            if amount > MAX_AHEAD.days // 28:
                raise ValueError(TOO_FAR)
            at = _months_later(exact_now, amount)
        else:
            at = exact_now + timedelta(seconds=amount * UNIT_SECONDS[unit[:3]])
    except (OverflowError, OSError):
        raise ValueError(TOO_FAR) from None
    if at - exact_now > MAX_AHEAD:
        raise ValueError(TOO_FAR)
    return at


def _repeat_every(amount: int, unit: str) -> str:
    if unit == "minutes":
        if not 5 <= amount <= 59:
            raise ValueError("Repeat every 5 to 59 minutes, or use hours.")
        return f"*/{amount} * * * *" if MINUTES_IN_HOUR % amount == 0 else f"every {amount}m"
    if not 1 <= amount <= 23:
        raise ValueError("Repeat every 1 to 23 hours, or use daily.")
    return f"0 */{amount} * * *" if HOURS_IN_DAY % amount == 0 else f"every {amount}h"


def parse_when(when: str, now: datetime | None = None) -> tuple[str | None, datetime, str]:
    """Return (cron or None for one-off, first run time, human description). Raises ValueError."""
    exact_now = (now or datetime.now()).replace(microsecond=0)
    now = exact_now.replace(second=0)
    phrase = " ".join(when.lower().replace(",", " ").split())
    if not phrase:
        raise ValueError(HELP)
    for spoken, plain in SPOKEN_AMOUNTS:
        phrase = re.sub(spoken, plain, phrase)
    phrase = re.sub(r"^(on|at) (?=(mon|tue|wed|thu|fri|sat|sun|today|tonight|tomorrow))", "", phrase)
    phrase = re.sub(r"\bthis (morning|afternoon|evening)\b", r"today \1", phrase)
    said_next = bool(re.match(r"next (mon|tue|wed|thu|fri|sat|sun)", phrase))
    phrase = re.sub(r"^next (?=(mon|tue|wed|thu|fri|sat|sun))", "", phrase)
    if re.fullmatch(r"\d{1,2}(:\d{2})?\s*(am|pm)?|noon|midday|midnight", phrase):
        phrase = f"at {phrase}"  # just a time: the next time it comes round

    if re.fullmatch(r"[\d*/,\-]+( [\d*/,\-]+){4}", phrase):
        _parse_cron(phrase)
        return phrase, next_run(phrase, now), f"cron {phrase}"

    relative = RELATIVE.fullmatch(phrase)
    if relative:
        at = _relative(int(relative.group(1)), relative.group(2), exact_now)
        return None, at, f"once at {at:%a %d %b %H:%M}"

    every = re.fullmatch(r"every (\d+) (minute|hour)s?", phrase)
    if every:
        cron = _repeat_every(int(every.group(1)), every.group(2) + "s")
        return cron, next_run(cron, now), phrase
    if phrase in ("hourly", "every hour"):
        return "0 * * * *", next_run("0 * * * *", now), "every hour"

    clock = _clock(phrase)
    if clock is None:  # "tomorrow morning", "tonight": a sensible time for that part of the day
        clock = next((time for part, time in PARTS_OF_DAY if re.search(rf"\b{part}\b", phrase)), None)
    dated = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", phrase)
    if dated:
        hour, minute = clock or (9, 0)
        at = datetime(int(dated.group(1)), int(dated.group(2)), int(dated.group(3)), hour, minute)
        if at <= now:
            raise ValueError("That time has already passed.")
        return None, at, f"once at {at:%a %d %b %Y %H:%M}"
    import calendar_feed  # imported here because calendar_feed imports this module
    if YEARLY.search(phrase) and not WEEKDAY_NAMED.search(phrase) and (day_month := calendar_feed.day_and_month(phrase)):
        hour, minute = clock or (9, 0)
        cron = f"{minute} {hour} {day_month[0]} {day_month[1]} *"
        first = next_run(cron, now)
        return cron, first, f"every year on {first:%d %b} at {hour:02d}:{minute:02d}"
    if not re.search(r"\b(every|each|daily|weekdays|weekends)\b", phrase):
        day = calendar_feed.written_date(phrase, now.date())  # "25 December", "24/12", "the 25th"
        if day is not None:
            hour, minute = clock or (9, 0)
            at = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
            if at <= now:
                raise ValueError("That time has already passed.")
            return None, at, f"once at {at:%a %d %b %Y %H:%M}"
    if phrase.startswith(("today", "tonight", "tomorrow")) or phrase.startswith("at "):
        if not clock:
            raise ValueError("Say a time too, e.g. 'tomorrow 08:00'.")
        at = now.replace(hour=clock[0], minute=clock[1])
        if phrase.startswith("tomorrow"):
            at += timedelta(days=1)
        elif at <= now and phrase.startswith(("today", "tonight")):
            raise ValueError("That time has already passed today.")
        elif at <= now:
            at += timedelta(days=1)
        return None, at, f"once at {at:%a %d %b %H:%M}"

    if clock is None and re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)", phrase):
        clock = (9, 0)  # "Monday" with no time: the morning
    if clock is None:
        raise ValueError(HELP)
    hour, minute = clock
    if re.match(r"(every day|daily|each day|everyday)\b", phrase):
        days, label = "*", "every day"
    elif re.match(r"(weekdays|every weekday)\b", phrase):
        days, label = "1-5", "weekdays"
    elif re.match(r"(weekends|every weekend)\b", phrase):
        days, label = "0,6", "weekends"
    else:
        named = [index for index, day in enumerate(WEEKDAYS) if re.search(rf"\b({day}s?|{day[:3]})\b", phrase)]
        if not named:
            raise ValueError(HELP)
        repeats = re.search(r"\b(every|each)\b", phrase) or any(re.search(rf"\b{WEEKDAYS[i]}s\b", phrase) for i in named)
        if not repeats and len(named) == 1:
            # "Friday 3pm" means the coming Friday, once; "every Friday" or "Fridays" repeats.
            ahead = (named[0] - int(now.strftime("%w"))) % 7 or (7 if said_next else 0)
            at = (now + timedelta(days=ahead)).replace(hour=hour, minute=minute)
            if at <= now:
                at += timedelta(days=7)
            return None, at, f"once at {at:%a %d %b %H:%M}"
        days, label = ",".join(str(index) for index in named), ", ".join(WEEKDAYS[index].title() + "s" for index in named)
    cron = f"{minute} {hour} * * {days}"
    return cron, next_run(cron, now), f"{label} at {hour:02d}:{minute:02d}"


def _parse_field(field: str, low: int, high: int) -> set[int]:
    values = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step < 1:
                raise ValueError("cron step must be at least 1")
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
        else:
            start = end = int(part)
        if start < low or end > high or start > end:
            raise ValueError(f"cron value out of range {low}-{high}: {part}")
        values.update(range(start, end + 1, step))
    return values


def _parse_cron(cron: str) -> list[set[int]]:
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError("cron needs 5 fields: minute hour day month weekday")
    try:
        return [_parse_field(field, low, high) for field, (low, high) in zip(fields, CRON_RANGES)]
    except ValueError as error:
        raise ValueError(f"bad cron expression: {error}") from None


def following_run(cron: str, due: datetime, now: datetime) -> datetime:
    upcoming = next_run(cron, due)
    return upcoming if upcoming > now else next_run(cron, now)


def repeats_within_a_day(cron: str) -> bool:
    if INTERVAL.fullmatch(cron):
        return True
    minute, hour = cron.split()[:2]
    return not (minute.isdigit() and hour.isdigit())


def next_run(cron: str, after: datetime) -> datetime:
    """The first time strictly after `after` that matches the cron expression."""
    if interval := INTERVAL.fullmatch(cron):
        amount = int(interval.group(1))
        step = timedelta(minutes=amount) if interval.group(2) == "m" else timedelta(hours=amount)
        return after.replace(second=0, microsecond=0) + step
    minutes, hours, days, months, weekdays = _parse_cron(cron)
    any_day, any_weekday = cron.split()[2] == "*", cron.split()[4] == "*"
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + SEARCH_LIMIT
    while candidate < limit:
        if candidate.month not in months:
            candidate = (candidate.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        day_match = candidate.day in days
        weekday_match = (candidate.isoweekday() % 7) in weekdays
        # Standard cron: when both day and weekday are restricted, either may match.
        matches = day_match and weekday_match if any_day or any_weekday else day_match or weekday_match
        if not matches:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if candidate.hour not in hours:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
            continue
        if candidate.minute not in minutes:
            candidate += timedelta(minutes=1)
            continue
        return candidate
    raise ValueError("that schedule never runs")
