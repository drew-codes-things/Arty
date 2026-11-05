"""Prints the user's calendar events for the next N days as JSON, oldest first.

Run by calendar.py with the tools venv's Python (icalendar + recurring-ical-events, which expand
repeating events), so the brain itself keeps to the standard library. The calendars are Google
Calendar "secret address in iCal format" links in CALENDAR_ICS_URLS (comma-separated).

    calendar_events.py <days>
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

LOCAL = ZoneInfo(os.environ.get("CALENDAR_TIMEZONE", "Europe/London"))
FETCH_SECONDS = 30


def when(value):
    """(datetime in local time, all-day?) for an event's start or end."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=LOCAL)).astimezone(LOCAL), False
    return datetime.combine(value, datetime.min.time(), LOCAL), True


def main() -> int:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 1
    urls = [u.strip() for u in os.environ.get("CALENDAR_ICS_URLS", "").split(",") if u.strip()]
    start = datetime.now(LOCAL)
    end = start + timedelta(days=days)
    events, problems = [], []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=FETCH_SECONDS) as response:
                calendar = icalendar.Calendar.from_ical(response.read())
        except Exception as error:  # one broken calendar shouldn't hide the others
            problems.append(f"a calendar couldn't be read ({error.__class__.__name__})")
            continue
        name = str(calendar.get("X-WR-CALNAME", "Calendar"))
        for event in recurring_ical_events.of(calendar).between(start, end):
            begins, all_day = when(event.get("DTSTART").dt)
            finishes, _ = when(event.get("DTEND").dt) if event.get("DTEND") else (begins, all_day)
            if str(event.get("STATUS", "")).upper() == "CANCELLED":
                continue
            events.append({
                "uid": str(event.get("UID", "")), "title": str(event.get("SUMMARY", "(no title)")),
                "start": begins.isoformat(), "end": finishes.isoformat(), "all_day": all_day,
                "location": str(event.get("LOCATION", "")), "calendar": name,
            })
    events.sort(key=lambda e: e["start"])
    print(json.dumps({"events": events, "problems": problems}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
