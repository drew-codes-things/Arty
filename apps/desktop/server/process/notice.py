import time
from dataclasses import dataclass

AWAY_MINUTES = 20
FOCUS_MINUTES = 120
GAP_MINUTES = 30
SECONDS_PER_MINUTE = 60
MINUTES_SAID_AS_HOURS = 90
IGNORED_APPS = {"", "arty", "electron", "plasmashell", "kscreenlocker_greet", "lockapp", "lockapp.exe", "explorer.exe",
                "searchhost.exe", "shellexperiencehost.exe"}


def spoken_duration(minutes: int) -> str:
    if minutes < MINUTES_SAID_AS_HOURS:
        return f"{minutes} minutes"
    return f"{round(minutes / SECONDS_PER_MINUTE)} hours"


@dataclass(frozen=True)
class Notice:
    reason: str
    minutes: int
    app: str = ""

    def prompt(self) -> str:
        if self.reason == "back":
            situation = (f"The user has just come back to their computer after about {spoken_duration(self.minutes)} away. "
                         "Say a short, natural welcome back in one sentence; you can ask how it went or what's next.")
        else:
            situation = (f"The user has been in {self.app} for about {spoken_duration(self.minutes)} without a break. "
                         "Say one short, friendly line about it, like suggesting a stretch or asking how it's going.")
        return f"\n\n<context>{situation} Don't mention this note.</context>"

    def said(self) -> str:
        return "(back at the computer)" if self.reason == "back" else f"(been in {self.app} a while)"


def is_ignored(app: str) -> bool:
    return app.strip().lower() in IGNORED_APPS


class Noticer:

    def __init__(self, away_minutes=AWAY_MINUTES, focus_minutes=FOCUS_MINUTES, gap_minutes=GAP_MINUTES,
                 clock=time.monotonic):
        self._away_seconds = away_minutes * SECONDS_PER_MINUTE
        self._focus_seconds = focus_minutes * SECONDS_PER_MINUTE
        self._gap_seconds = gap_minutes * SECONDS_PER_MINUTE
        self._clock = clock
        self._longest_away = 0.0
        self._app = ""
        self._app_since = clock()
        self._mentioned = False
        self._last_notice = None
        self._last_notice_before = None
        self._last_away_seconds = 0.0

    def retract(self, notice: Notice) -> None:
        self._last_notice = self._last_notice_before
        if notice.reason == "back":
            self._longest_away = self._last_away_seconds
        else:
            self._mentioned = False

    def observe(self, idle_seconds: float, app: str, holding_back: bool = False) -> Notice | None:
        now = self._clock()
        if idle_seconds >= self._away_seconds:
            self._longest_away = max(self._longest_away, idle_seconds)
            return None
        if self._longest_away:
            away_minutes = int(self._longest_away // SECONDS_PER_MINUTE)
            self._last_away_seconds = self._longest_away
            self._longest_away = 0.0
            self._restart_stretch(app, now)
            return None if holding_back else self._allowed(Notice("back", away_minutes), now)
        if is_ignored(app):
            return None
        if app != self._app:
            self._restart_stretch(app, now)
            return None
        if self._mentioned or holding_back or now - self._app_since < self._focus_seconds:
            return None
        notice = self._allowed(Notice("focus", int((now - self._app_since) // SECONDS_PER_MINUTE), app), now)
        self._mentioned = notice is not None
        return notice

    def _restart_stretch(self, app: str, now: float) -> None:
        self._app = "" if is_ignored(app) else app
        self._app_since = now
        self._mentioned = False

    def _allowed(self, notice: Notice, now: float) -> Notice | None:
        if self._last_notice is not None and now - self._last_notice < self._gap_seconds:
            return None
        self._last_notice_before = self._last_notice
        self._last_notice = now
        return notice
