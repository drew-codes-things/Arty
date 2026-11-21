import base64
import hashlib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import app_settings

SETTINGS_FILE = app_settings.settings_file("OWN_APPS_ENV", "/srv/ai/secrets/own-apps.env")
TIMEOUT_SECONDS = 30
DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"
ICS_TYPE = "text/calendar; charset=utf-8"
XML_TYPE = "application/xml; charset=utf-8"
PRODUCT = "-//Arty//Calendar actions//EN"
UTC_STAMP = "%Y%m%dT%H%M%SZ"
DATE_STAMP = "%Y%m%d"
FOLD_AT = 75
REFERENCE_CHARS = 8
TEXT_ESCAPES = (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\r\n", "\\n"), ("\r", "\\n"), ("\n", "\\n"))
CHANGED_ELSEWHERE = 412
QUERY = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
 <D:prop><D:getetag/><C:calendar-data/></D:prop>
 <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">
  <C:time-range start="{start}" end="{end}"/>
 </C:comp-filter></C:comp-filter></C:filter>
</C:calendar-query>"""
LISTING = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>"""


class CalendarAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarSettings:
    url: str
    user: str
    password: str
    zone: ZoneInfo


@dataclass(frozen=True)
class Event:
    href: str
    etag: str
    uid: str
    title: str
    start: datetime | date
    end: datetime | date
    location: str
    repeats: bool
    ics: str

    @property
    def reference(self) -> str:
        return reference_for(self.href)


def reference_for(href: str) -> str:
    return hashlib.sha256(urllib.parse.unquote(href).encode()).hexdigest()[:REFERENCE_CHARS]


def load_settings(values: dict[str, str] | None = None, zone_name: str = "Europe/London") -> CalendarSettings | None:
    values = app_settings.read_settings(SETTINGS_FILE) if values is None else values
    url = values.get("OWN_CALENDAR_URL", "")
    if not url:
        return None
    if not app_settings.is_safe_address(url):
        raise CalendarAppError("OWN_CALENDAR_URL must be https (or http on this machine or the home network)")
    try:
        zone = ZoneInfo(values.get("OWN_CALENDAR_TIME_ZONE") or zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarAppError("OWN_CALENDAR_TIME_ZONE is not a time zone name like Europe/London") from None
    return CalendarSettings(url=url.rstrip("/") + "/", user=values.get("OWN_CALENDAR_USER", ""),
                            password=values.get("OWN_CALENDAR_PASSWORD", ""), zone=zone)


def _request(settings: CalendarSettings, method: str, url: str, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    if settings.user:
        token = base64.b64encode(f"{settings.user}:{settings.password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()
    except (urllib.error.URLError, OSError) as error:
        raise CalendarAppError(f"couldn't reach the Starfire Calendar app ({type(error).__name__})") from None


def _resource_url(settings: CalendarSettings, href: str) -> str:
    absolute = urllib.parse.urljoin(settings.url, urllib.parse.quote(urllib.parse.unquote(href), safe="/:@"))
    if not absolute.startswith(settings.url):
        raise CalendarAppError("that event isn't in the configured calendar")
    return absolute


def _unfolded(ics: str) -> list[str]:
    lines = []
    for line in ics.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def _folded(lines: list[str]) -> str:
    out = []
    for line in lines:
        while len(line.encode()) > FOLD_AT:
            cut = FOLD_AT
            while len(line[:cut].encode()) > FOLD_AT:
                cut -= 1
            out.append(line[:cut])
            line = " " + line[cut:]
        out.append(line)
    return "\r\n".join(out) + "\r\n"


def _split(line: str) -> tuple[str, dict, str]:
    head, _, value = line.partition(":")
    name, *parameters = head.split(";")
    return name.upper(), dict(part.partition("=")[::2] for part in parameters), value


def _text(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _escaped(value: str) -> str:
    for plain, escaped in TEXT_ESCAPES:
        value = value.replace(plain, escaped)
    return value


def _moment(value: str, parameters: dict, zone: ZoneInfo) -> datetime | date:
    if parameters.get("VALUE") == "DATE" or len(value) == len("YYYYMMDD"):
        return datetime.strptime(value, DATE_STAMP).date()
    if value.endswith("Z"):
        return datetime.strptime(value, UTC_STAMP).replace(tzinfo=timezone.utc).astimezone(zone)
    try:
        where = ZoneInfo(parameters["TZID"]) if parameters.get("TZID") else zone
    except (ZoneInfoNotFoundError, ValueError):
        where = zone
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=where).astimezone(zone)


def _event_blocks(lines: list[str]) -> list[list[str]]:
    blocks, current = [], None
    for line in lines:
        if line.upper() == "BEGIN:VEVENT":
            current = []
        elif line.upper() == "END:VEVENT" and current is not None:
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def parse_event(href: str, etag: str, ics: str, zone: ZoneInfo) -> Event | None:
    blocks = _event_blocks(_unfolded(ics))
    master = next((block for block in blocks if not any(_split(line)[0] == "RECURRENCE-ID" for line in block)), None)
    if master is None:
        return None
    fields = {}
    for line in master:
        name, parameters, value = _split(line)
        fields.setdefault(name, (parameters, value))
    if "DTSTART" not in fields:
        return None
    start = _moment(fields["DTSTART"][1], fields["DTSTART"][0], zone)
    if "DTEND" in fields:
        end = _moment(fields["DTEND"][1], fields["DTEND"][0], zone)
    else:
        end = start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(0))
    return Event(href=urllib.parse.unquote(href), etag=etag, uid=fields.get("UID", ({}, ""))[1],
                 title=_text(fields.get("SUMMARY", ({}, ""))[1]) or "(no title)", start=start, end=end,
                 location=_text(fields.get("LOCATION", ({}, ""))[1]), repeats="RRULE" in fields or len(blocks) > 1, ics=ics)


def _responses(body: bytes) -> list[ElementTree.Element]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise CalendarAppError("the Starfire Calendar app sent back something that isn't a calendar listing") from None
    return root.findall(f"{DAV}response")


def _found_text(element: ElementTree.Element, path: str) -> str:
    found = element.find(path)
    return (found.text or "").strip() if found is not None else ""


def events_between(settings: CalendarSettings, start: datetime, end: datetime) -> list[Event]:
    body = QUERY.format(start=start.astimezone(timezone.utc).strftime(UTC_STAMP),
                        end=end.astimezone(timezone.utc).strftime(UTC_STAMP)).encode()
    status, _, answer = _request(settings, "REPORT", settings.url, body, {"Depth": "1", "Content-Type": XML_TYPE})
    if status != 207:
        raise CalendarAppError(f"the Starfire Calendar app answered {status} when asked for events")
    found = []
    for response in _responses(answer):
        ics = _found_text(response, f".//{CALDAV}calendar-data")
        event = parse_event(_found_text(response, f"{DAV}href"), _found_text(response, f".//{DAV}getetag"), ics,
                            settings.zone) if ics else None
        if event is not None:
            found.append(event)
    return sorted(found, key=lambda event: _sort_key(event.start, settings.zone))


def _sort_key(moment: datetime | date, zone: ZoneInfo) -> datetime:
    return moment if isinstance(moment, datetime) else datetime.combine(moment, datetime.min.time(), zone)


def _hrefs(settings: CalendarSettings) -> list[str]:
    status, _, answer = _request(settings, "PROPFIND", settings.url, LISTING.encode(), {"Depth": "1", "Content-Type": XML_TYPE})
    if status != 207:
        raise CalendarAppError(f"the Starfire Calendar app answered {status} when asked for its events")
    return [href for response in _responses(answer) if (href := _found_text(response, f"{DAV}href")).endswith(".ics")]


def find(settings: CalendarSettings, reference: str) -> Event:
    reference = (reference or "").strip().lstrip("#").lower()
    matching = [href for href in _hrefs(settings) if reference_for(href) == reference] if reference else []
    if not matching:
        raise CalendarAppError(f"there's no event {reference or '(none given)'} in the Starfire Calendar app: list them first")
    return fetch(settings, matching[0])


def fetch(settings: CalendarSettings, href: str) -> Event:
    status, headers, answer = _request(settings, "GET", _resource_url(settings, href))
    if status != 200:
        raise CalendarAppError(f"the Starfire Calendar app answered {status} for that event")
    event = parse_event(href, headers.get("ETag", headers.get("Etag", "")), answer.decode("utf-8", "replace"), settings.zone)
    if event is None:
        raise CalendarAppError("that isn't an event Arty can read")
    return event


def _stamp(moment: datetime | date, name: str) -> str:
    if isinstance(moment, datetime):
        return f"{name}:{moment.astimezone(timezone.utc).strftime(UTC_STAMP)}"
    return f"{name};VALUE=DATE:{moment.strftime(DATE_STAMP)}"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime(UTC_STAMP)


def new_event_ics(uid: str, title: str, start: datetime | date, end: datetime | date, location: str = "", notes: str = "") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODUCT}", "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_now_stamp()}",
             _stamp(start, "DTSTART"), _stamp(end, "DTEND"), f"SUMMARY:{_escaped(title)}"]
    if location:
        lines.append(f"LOCATION:{_escaped(location)}")
    if notes:
        lines.append(f"DESCRIPTION:{_escaped(notes)}")
    return _folded(lines + ["END:VEVENT", "END:VCALENDAR"])


def _checked_write(status: int, what: str):
    if status == CHANGED_ELSEWHERE:
        raise CalendarAppError(f"couldn't {what}: it was changed somewhere else just now, look again first")
    if status not in (200, 201, 204):
        raise CalendarAppError(f"couldn't {what}: the Starfire Calendar app answered {status}")


def _etag_of(headers: dict) -> str:
    return headers.get("ETag", headers.get("Etag", ""))


def create(settings: CalendarSettings, title: str, start: datetime | date, end: datetime | date, location: str = "",
           notes: str = "") -> Event:
    uid = f"{uuid.uuid4()}@arty"
    href = urllib.parse.urlsplit(settings.url).path + f"{uuid.uuid4().hex}.ics"
    ics = new_event_ics(uid, title, start, end, location, notes)
    status, headers, _ = _request(settings, "PUT", _resource_url(settings, href), ics.encode(),
                                  {"Content-Type": ICS_TYPE, "If-None-Match": "*"})
    _checked_write(status, "add the event")
    return Event(href=href, etag=_etag_of(headers), uid=uid, title=title, start=start, end=end, location=location,
                 repeats=False, ics=ics)


def with_new_time(event: Event, start: datetime | date, end: datetime | date) -> str:
    lines, inside = [], False
    for line in _unfolded(event.ics):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            inside = True
        elif upper == "END:VEVENT":
            inside = False
        name = _split(line)[0]
        if inside and name in ("DTEND", "DURATION"):
            continue
        if inside and name == "DTSTART":
            lines += [_stamp(start, "DTSTART"), _stamp(end, "DTEND")]
            continue
        if inside and name == "DTSTAMP":
            lines.append(f"DTSTAMP:{_now_stamp()}")
            continue
        lines.append(line)
    return _folded(lines)


def move(settings: CalendarSettings, event: Event, start: datetime | date, end: datetime | date) -> Event:
    if event.repeats:
        raise CalendarAppError("that event repeats: change repeating events in the Starfire Calendar app itself")
    ics = with_new_time(event, start, end)
    status, headers, _ = _request(settings, "PUT", _resource_url(settings, event.href), ics.encode(),
                                  {"Content-Type": ICS_TYPE, "If-Match": event.etag} if event.etag else {"Content-Type": ICS_TYPE})
    _checked_write(status, "move the event")
    return Event(href=event.href, etag=_etag_of(headers), uid=event.uid, title=event.title, start=start, end=end,
                 location=event.location, repeats=False, ics=ics)


def cancel(settings: CalendarSettings, event: Event):
    if event.repeats:
        raise CalendarAppError("that event repeats: cancel repeating events in the Starfire Calendar app itself")
    status, _, _ = _request(settings, "DELETE", _resource_url(settings, event.href),
                            headers={"If-Match": event.etag} if event.etag else None)
    _checked_write(status, "cancel the event")


def put_back(settings: CalendarSettings, href: str, ics: str, current_etag: str = ""):
    headers = {"Content-Type": ICS_TYPE, **({"If-Match": current_etag} if current_etag else {"If-None-Match": "*"})}
    status, _, _ = _request(settings, "PUT", _resource_url(settings, href), ics.encode(), headers)
    _checked_write(status, "put the event back")


def remove(settings: CalendarSettings, href: str, current_etag: str = ""):
    status, _, _ = _request(settings, "DELETE", _resource_url(settings, href),
                            headers={"If-Match": current_etag} if current_etag else None)
    _checked_write(status, "remove the event")


class CaldavCalendar:
    def __init__(self, settings: CalendarSettings):
        self.settings = settings
        self.zone = settings.zone

    def events_between(self, start: datetime, end: datetime) -> list[Event]:
        return events_between(self.settings, start, end)

    def find(self, reference: str) -> Event:
        return find(self.settings, reference)

    def create(self, title: str, start: datetime | date, end: datetime | date, location: str = "", notes: str = "") -> Event:
        return create(self.settings, title, start, end, location, notes)

    def move(self, event: Event, start: datetime | date, end: datetime | date) -> Event:
        return move(self.settings, event, start, end)

    def cancel(self, event: Event):
        cancel(self.settings, event)

    def remove(self, href: str, current_etag: str = ""):
        remove(self.settings, href, current_etag)

    def put_back(self, href: str, before: str, current_etag: str = ""):
        put_back(self.settings, href, before, current_etag)
