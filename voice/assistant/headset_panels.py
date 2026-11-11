import asyncio
import logging
import math
import re
import time
from datetime import datetime

import aiohttp

REFRESH_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 60
CALENDAR_DAYS = 7
MAX_EVENTS = 6
MAX_REMINDERS = 6
REMINDER_TIME_FORMAT = "%Y-%m-%dT%H:%M"
GUEST_LINE = re.compile(r"^(?:CT|VM) \d+ (?P<name>[\w.-]+)(?: \([\d.]+\))?: (?P<state>\w+)(?:, (?P<detail>.*))?$")
HOST_LINE = re.compile(r"^Host (?P<name>[\w.-]+)(?: \([\d.]+\))?: (?P<state>\w+)(?:, (?P<detail>.*))?$")
HEALTHY = {"running", "online"}

log = logging.getLogger("arty-voice.panels")


def servers_from(status: str) -> list[dict]:
    servers = []
    for line in status.splitlines():
        match = HOST_LINE.match(line.strip()) or GUEST_LINE.match(line.strip())
        if not match:
            continue
        state = match.group("state").lower()
        servers.append({"name": match.group("name"), "state": state, "ok": state in HEALTHY,
                        "detail": (match.group("detail") or "").split(", up ")[0]})
    return servers


def events_from(calendar: dict) -> list[dict]:
    return [{"title": event.get("title", ""), "start": event.get("start", ""), "all_day": bool(event.get("all_day"))}
            for event in (calendar.get("events") or [])[:MAX_EVENTS]]


def is_epoch_seconds(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_reminder_time(next_run) -> datetime | None:
    try:
        if is_epoch_seconds(next_run):
            return datetime.fromtimestamp(next_run)
        return datetime.fromisoformat(str(next_run))
    except (OverflowError, OSError, ValueError):
        return None


def reminder_time(next_run) -> str:
    moment = parse_reminder_time(next_run)
    if moment is None:
        return str(next_run)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime(REMINDER_TIME_FORMAT)


def reminder_sort_key(next_run) -> float:
    moment = parse_reminder_time(next_run)
    if moment is None:
        return math.inf
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return math.inf


def reminders_from(schedules: dict) -> list[dict]:
    active = [s for s in schedules.get("schedules") or [] if s.get("enabled", True) and s.get("next_run")]
    soonest_first = sorted(active, key=lambda s: reminder_sort_key(s["next_run"]))
    return [{"title": s.get("title") or s.get("message") or "Reminder", "next_run": reminder_time(s["next_run"])}
            for s in soonest_first[:MAX_REMINDERS]]


class Dashboard:

    def __init__(self, http: aiohttp.ClientSession, brain_url: str, brain_key: str):
        self.http = http
        self.brain_url = brain_url
        self.headers = {"Authorization": f"Bearer {brain_key}"}
        self.cached: dict | None = None
        self.fetched_at = 0.0
        self.lock = asyncio.Lock()

    async def current(self) -> dict:
        async with self.lock:
            if self.cached is None or time.time() - self.fetched_at > REFRESH_SECONDS:
                self.cached = await self.fetch()
                self.fetched_at = time.time()
            return self.cached

    async def fetch(self) -> dict:
        status, weather, calendar, schedules = await asyncio.gather(
            self.tool("homelab__service_status"), self.tool("weather"),
            self.get(f"/calendar?days={CALENDAR_DAYS}"), self.get("/schedules"))
        return {"type": "panels", "servers": servers_from(status), "weather": weather,
                "events": events_from(calendar), "reminders": reminders_from(schedules)}

    async def tool(self, name: str) -> str:
        try:
            async with self.http.post(f"{self.brain_url}/tools/{name}", json={"arguments": {}}, headers=self.headers,
                                      timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as response:
                if response.status != 200:
                    return f"Couldn't check ({response.status})."
                return str((await response.json()).get("result") or "")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            log.warning("panel tool %s failed: %s", name, error)
            return "Couldn't check just now."

    async def get(self, path: str) -> dict:
        try:
            async with self.http.get(f"{self.brain_url}{path}", headers=self.headers,
                                     timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as response:
                return await response.json() if response.status == 200 else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            log.warning("panel request %s failed: %s", path, error)
            return {}
