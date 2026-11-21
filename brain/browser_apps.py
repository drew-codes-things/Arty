import base64
import json
import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import connections
import own_calendar
import own_drive

REQUEST_TOOL = "workspace__own_app_request"
UPLOAD_TOOL = "workspace__drive_upload"
SEND_TOOL = "workspace__gmail_send"
MESSAGE_TOOL = "workspace__gmail_message"
TOOLS = frozenset({REQUEST_TOOL, UPLOAD_TOOL, SEND_TOOL, MESSAGE_TOOL})
SIGN_IN_MARK = "BLOCKED:"
RESULT_CHARS = 500_000
DRIVE = "drive"
CALENDAR = "calendar"
MOST_SAVED_BYTES = 25 * 1024 * 1024
CHANGED_ELSEWHERE = (409, 412)
LOCAL_MINUTE = "%Y-%m-%dT%H:%M"
LOOKED_BACK_DAYS = 7
LOOKED_AHEAD_DAYS = 370
SHOWN_ERROR_CHARS = 200
GMAIL_SENDER = "the Gmail signed in on my desktop"


class BrowserAppError(RuntimeError):
    pass


class SignInNeeded(BrowserAppError):
    pass


def available(*tools: str) -> bool:
    return all(tool in connections.hub.routes for tool in tools)


def needs_sign_in(result: str) -> bool:
    return str(result or "").startswith(SIGN_IN_MARK)


def _answer(tool: str, arguments: dict) -> dict:
    result = connections.hub.call(tool, arguments, limit=RESULT_CHARS)
    if needs_sign_in(result):
        raise SignInNeeded(result)
    try:
        answer = json.loads(result)
    except ValueError:
        raise BrowserAppError(str(result)[:SHOWN_ERROR_CHARS * 2]) from None
    if not isinstance(answer, dict):
        raise BrowserAppError(f"{tool} answered something that isn't an object")
    return answer


def request(app: str, method: str, path: str, payload=None, if_match: str = "") -> dict:
    return _answer(REQUEST_TOOL, {"app": app, "method": method, "path": path, "if_match": if_match,
                                  "body_json": json.dumps(payload) if payload is not None else ""})


def _error_text(body) -> str:
    return str(body.get("error", "") if isinstance(body, dict) else "")[:SHOWN_ERROR_CHARS] or "no details"


def _succeeded(answer: dict) -> bool:
    return 200 <= int(answer.get("status") or 0) < 300


class BrowserDrive:
    most_bytes = MOST_SAVED_BYTES

    def __init__(self):
        self.origin = ""

    def _checked(self, answer: dict):
        self.origin = str(answer.get("origin") or self.origin)
        if not _succeeded(answer):
            raise own_drive.DriveAppError(f"the Drive app answered {answer.get('status')}: {_error_text(answer.get('body'))}")
        return answer.get("body")

    def json(self, method: str, path: str, payload=None):
        return self._checked(request(DRIVE, method, path, payload))

    def upload(self, name: str, data: bytes, folder_id: int) -> dict:
        return self._checked(_answer(UPLOAD_TOOL, {"name": name, "folder_id": int(folder_id),
                                                   "data_base64": base64.b64encode(data).decode()}))

    def share_link(self, token: str) -> str:
        return f"{self.origin}/s/{token}"


UNKNOWN_SEND = "Gmail didn't say it was sent; look in Sent before trying again"


def send_email(to: list[str], cc: list[str], subject: str, body: str) -> str:
    try:
        answer = _answer(SEND_TOOL, {"to": ", ".join(to), "cc": ", ".join(cc), "subject": subject, "body": body})
    except SignInNeeded:
        raise
    except BrowserAppError as error:
        raise BrowserAppError(f"{error} ({UNKNOWN_SEND})") from None
    if answer.get("sent") is not True:
        raise BrowserAppError(UNKNOWN_SEND)
    return str(answer.get("confirmed") or "")


def message_facts(query: str, number: int) -> dict:
    return _answer(MESSAGE_TOOL, {"query": query, "number": int(number)})


def _segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _zone(name: str, fallback: ZoneInfo) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else fallback
    except (ZoneInfoNotFoundError, ValueError):
        return fallback


def _instant(text: str, zone: ZoneInfo) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(zone)


class BrowserCalendar:
    def __init__(self, zone: ZoneInfo):
        self.zone = zone
        self.app_zone = zone

    def _call(self, method: str, path: str, payload=None, if_match: str = "", what: str = "do that"):
        answer = request(CALENDAR, method, path, payload, if_match)
        self.app_zone = _zone(str(answer.get("time_zone") or ""), self.app_zone)
        status = int(answer.get("status") or 0)
        if status in CHANGED_ELSEWHERE:
            raise own_calendar.CalendarAppError(f"couldn't {what}: it was changed somewhere else just now, look again first")
        if not _succeeded(answer):
            raise own_calendar.CalendarAppError(f"couldn't {what}: the Calendar app answered {status}: "
                                                f"{_error_text(answer.get('body'))}")
        return answer.get("body")

    def _event(self, item: dict) -> own_calendar.Event:
        all_day = bool(item.get("allDay"))
        if all_day:
            start = date.fromisoformat(item["start"])
            end = date.fromisoformat(item["end"]) + timedelta(days=1)
        else:
            start, end = _instant(item["startsAt"], self.zone), _instant(item["endsAt"], self.zone)
        before = {key: item.get(key) for key in ("calendarId", "title", "location", "notes", "allDay", "start", "end")}
        return own_calendar.Event(href=f"{item['calendarId']}/{item['itemId']}", etag=str(item.get("etag") or ""),
                                  uid=str(item.get("uid") or ""), title=str(item.get("title") or "") or "(no title)",
                                  start=start, end=end, location=str(item.get("location") or ""),
                                  repeats=bool(item.get("recurring")), ics=json.dumps(before))

    def _times(self, start: datetime | date, end: datetime | date) -> dict:
        if isinstance(start, datetime):
            return {"allDay": False, "start": start.astimezone(self.app_zone).strftime(LOCAL_MINUTE),
                    "end": end.astimezone(self.app_zone).strftime(LOCAL_MINUTE)}
        last = max(start, end - timedelta(days=1))
        return {"allDay": True, "start": start.isoformat(), "end": last.isoformat()}

    def _listing(self, first: date, last: date) -> list[own_calendar.Event]:
        found = self._call("GET", f"/api/events?from={first.isoformat()}&to={last.isoformat()}", what="list the events")
        return [self._event(item) for item in (found or {}).get("events", [])]

    def _path(self, href: str) -> str:
        calendar, _, item = href.partition("/")
        if not calendar or not item:
            raise own_calendar.CalendarAppError("that event isn't one Arty can find in the Calendar app")
        return f"/api/calendars/{_segment(calendar)}/events/{_segment(item)}"

    def _fetch(self, href: str) -> own_calendar.Event:
        return self._event(self._call("GET", self._path(href), what="read the event back"))

    def events_between(self, start: datetime, end: datetime) -> list[own_calendar.Event]:
        return self._listing(start.date(), max(end.date(), start.date() + timedelta(days=1)))

    def find(self, reference: str) -> own_calendar.Event:
        wanted = (reference or "").strip().lstrip("#").lower()
        today = datetime.now(self.zone).date()
        listed = self._listing(today - timedelta(days=LOOKED_BACK_DAYS), today + timedelta(days=LOOKED_AHEAD_DAYS)) if wanted else []
        matching = [event for event in listed if event.reference == wanted]
        if not matching:
            raise own_calendar.CalendarAppError(f"there's no event {wanted or '(none given)'} in the Calendar app: list them first")
        return matching[0]

    def create(self, title: str, start, end, location: str = "", notes: str = "") -> own_calendar.Event:
        calendars = (self._call("GET", "/api/calendars", what="find your calendar") or {}).get("calendars") or []
        if not calendars:
            raise own_calendar.CalendarAppError("the Calendar app has no calendar to add the event to")
        made = self._call("POST", "/api/events", {"calendarId": calendars[0]["id"], "title": title, "location": location,
                                                   "notes": notes, **self._times(start, end)}, what="add the event")
        return self._fetch(f"{made['calendarId']}/{made['itemId']}")

    def move(self, event: own_calendar.Event, start, end) -> own_calendar.Event:
        if event.repeats:
            raise own_calendar.CalendarAppError("that event repeats: change repeating events in the Calendar app itself")
        self._call("PATCH", self._path(event.href), self._times(start, end), event.etag, what="move the event")
        return self._fetch(event.href)

    def cancel(self, event: own_calendar.Event):
        if event.repeats:
            raise own_calendar.CalendarAppError("that event repeats: cancel repeating events in the Calendar app itself")
        self._call("DELETE", self._path(event.href), if_match=event.etag, what="cancel the event")

    def remove(self, href: str, current_etag: str = ""):
        self._call("DELETE", self._path(href), if_match=current_etag, what="remove the event")

    def put_back(self, href: str, before: str, current_etag: str = ""):
        fields = json.loads(before)
        kept = {key: fields.get(key) for key in ("title", "location", "notes", "allDay", "start", "end")}
        if current_etag:
            self._call("PATCH", self._path(href), kept, current_etag, what="put the event back")
            return
        self._call("POST", "/api/events", {"calendarId": fields["calendarId"], **kept}, what="put the event back")
