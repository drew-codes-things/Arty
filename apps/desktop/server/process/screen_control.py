import itertools
import re
import threading
import time
from pathlib import Path

from process import config as cfg
from process import shown_text
from process.tools import input_watch

SETTING_SECTION = "screen_control"
IDLE_LIMIT_SECONDS = 45.0
SESSION_LIMIT_SECONDS = 300.0
INDICATOR_SECONDS = 4.0
SETTLE_SECONDS = 0.8
SETTLE_LIMIT_SECONDS = 6.0
WATCH_EVERY_SECONDS = 0.2
CHUNK_CHARS = 8
INJECTION_GAP_SECONDS = input_watch.INJECTION_GRACE_SECONDS + 0.15
HOTKEY = "CommandOrControl+Alt+End"
HOTKEY_SHOWN = "Ctrl+Alt+End"
STOPPED_BY_INPUT = "input"
STOPPED_BY_BUTTON = "stop"
STOPPED_BY_TIMEOUT = "timeout"
FINISHED = "finished"
STOP_MESSAGES = {
    STOPPED_BY_INPUT: "Stopped: you used the mouse or keyboard, so Arty let go of the screen. Ask again when you want it "
                      "to carry on.",
    STOPPED_BY_BUTTON: "Stopped: you pressed Stop, so Arty let go of the screen.",
    STOPPED_BY_TIMEOUT: "Stopped: screen control timed out. Ask again to carry on.",
    FINISHED: "Screen control has ended.",
}
CARD_DIGITS = re.compile(r"\d(?:[\s.,/\\|_:;-]{0,3}\d)+")
CARD_LENGTHS = range(13, 20)
EXTENSION_HINTS_LINE = re.compile(r"^const PRIVATE_FIELD_HINTS = '([^'\n]*)';$", re.M)


class ControlStopped(RuntimeError):
    pass


def enabled():
    return bool(cfg.setting(SETTING_SECTION, "enabled", False))


def set_enabled(value):
    cfg.set_value(SETTING_SECTION, "enabled", bool(value))
    if not value:
        control.stop(FINISHED)


def _hint_sources():
    return [cfg.REPO_ROOT / "extension" / "background.js",
            cfg.REPO_ROOT.parent.parent / "extensions" / "browser" / "background.js"]


def private_field_pattern(paths=None):
    for path in paths or _hint_sources():
        try:
            found = EXTENSION_HINTS_LINE.search(Path(path).read_text(encoding="utf-8"))
        except OSError:
            continue
        if found:
            return re.compile(found.group(1).replace("\\\\", "\\"), re.I)
    raise ControlStopped("the private field rules weren't found, so Arty won't type on the screen")


def passes_luhn(digits):
    total = 0
    for index, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _has_card_window(digits):
    return any(passes_luhn(digits[start:start + length])
               for length in CARD_LENGTHS for start in range(len(digits) - length + 1))


def looks_like_card_number(text):
    return any(_has_card_window(re.sub(r"\D", "", found.group(0))) for found in CARD_DIGITS.finditer(str(text or "")))


def private_typing_refusal(text, field_words, pattern=None):
    if looks_like_card_number(text):
        return "Didn't type it: it looks like a card number, and Arty never types card or bank details."
    pattern = pattern or private_field_pattern()
    if any(pattern.search(words or "") for words in field_words):
        return ("Didn't type it: it would go into a password, code, card or bank field, and Arty never types into "
                "those. Type it yourself.")
    return None


def is_shown_plainly(*values):
    return all(shown_text.is_shown_exactly(str(value)) for value in values)


class Control:
    def __init__(self, make_watch=input_watch.watch_here, clock=time.monotonic, sleep=time.sleep):
        self._make_watch = make_watch
        self._clock = clock
        self._sleep = sleep
        self._state = threading.Condition()
        self._sessions = itertools.count(1)
        self._session = 0
        self._version = 0
        self._active = False
        self._shown = False
        self._injecting = False
        self._watch = None
        self._started_at = 0.0
        self._last_action_at = 0.0
        self._last_injection_end = 0.0
        self._reason = ""
        self._stopped_this_reply = ""

    def state(self):
        with self._state:
            return {"active": self._active, "session": self._session, "version": self._version, "reason": self._reason,
                    "hotkey": HOTKEY, "hotkey_shown": HOTKEY_SHOWN}

    def wait_for_change(self, after, timeout):
        deadline = self._clock() + timeout
        with self._state:
            while after == self._version and self._clock() < deadline:
                self._state.wait(max(0.0, deadline - self._clock()))
        return self.state()

    def _publish(self):
        self._version += 1
        self._state.notify_all()

    def new_reply(self):
        with self._state:
            self._stopped_this_reply = ""

    def indicator_shown(self, session):
        with self._state:
            if self._active and session == self._session:
                self._shown = True
                self._state.notify_all()
                return True
            return False

    def stop(self, reason):
        with self._state:
            if not self._active:
                return False
            self._active = False
            self._shown = False
            self._reason = reason
            if reason in (STOPPED_BY_INPUT, STOPPED_BY_BUTTON):
                self._stopped_this_reply = reason
            watch, self._watch = self._watch, None
            self._publish()
        if watch is not None:
            watch.close()
        print(f"[screen control] stopped ({reason})", flush=True)
        return True

    def _begin(self):
        if not enabled():
            raise ControlStopped("Screen control is off. It can be turned on in Arty's settings.")
        with self._state:
            if self._stopped_this_reply:
                raise ControlStopped(STOP_MESSAGES[self._stopped_this_reply])
            if self._active:
                return
        try:
            watch = self._make_watch()
        except input_watch.NoInputWatch as error:
            raise ControlStopped(f"Arty won't control the screen: {error}, so it couldn't stop when you take over.") from None
        with self._state:
            self._session = next(self._sessions)
            self._active, self._shown, self._reason, self._watch = True, False, "", watch
            self._started_at = self._last_action_at = self._last_injection_end = self._clock()
            self._publish()
            session = self._session
            deadline = self._clock() + INDICATOR_SECONDS
            while not self._shown and self._active and self._clock() < deadline:
                self._state.wait(max(0.0, deadline - self._clock()))
            shown = self._shown and self._active
        if not shown:
            self.stop(FINISHED)
            raise ControlStopped("Arty won't control the screen: the \"Arty is controlling the screen\" bar couldn't be "
                                 "shown. Is the Arty window running?")
        threading.Thread(target=self._watch_for_input, args=(session,), name="screen-control", daemon=True).start()
        self._settle()

    def _settle(self):
        deadline = self._clock() + SETTLE_LIMIT_SECONDS
        while self._clock() < deadline:
            since = self._clock()
            self._sleep(SETTLE_SECONDS)
            watch = self._current_watch()
            if watch is not None and watch.quiet_since(since):
                with self._state:
                    self._last_injection_end = self._clock()
                return
        self.stop(FINISHED)
        raise ControlStopped("Arty didn't start: keep your hands off the mouse and keyboard while Arty works, and ask again.")

    def _current_watch(self):
        with self._state:
            return self._watch

    def _timed_out(self):
        now = self._clock()
        return now - self._last_action_at > IDLE_LIMIT_SECONDS or now - self._started_at > SESSION_LIMIT_SECONDS

    def _watch_for_input(self, session):
        while True:
            self._sleep(WATCH_EVERY_SECONDS)
            with self._state:
                if not self._active or session != self._session:
                    return
                if self._injecting:
                    continue
                watch, since, timed_out = self._watch, self._last_injection_end, self._timed_out()
            if timed_out:
                self.stop(STOPPED_BY_TIMEOUT)
                return
            if not watch.quiet_since(since):
                with self._state:
                    late = self._injecting or since != self._last_injection_end
                if not late:
                    self.stop(STOPPED_BY_INPUT)
                    return

    def _wait_past_the_last_injection(self):
        with self._state:
            ready_at = self._last_injection_end + INJECTION_GAP_SECONDS
        remaining = ready_at - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def check(self):
        self._wait_past_the_last_injection()
        with self._state:
            if not self._active:
                raise ControlStopped(STOP_MESSAGES.get(self._stopped_this_reply or self._reason, STOP_MESSAGES[FINISHED]))
            if self._timed_out():
                timed_out = True
            else:
                timed_out = False
                watch, since = self._watch, self._last_injection_end
        if timed_out:
            self.stop(STOPPED_BY_TIMEOUT)
            raise ControlStopped(STOP_MESSAGES[STOPPED_BY_TIMEOUT])
        if not watch.quiet_since(since):
            self.stop(STOPPED_BY_INPUT)
            raise ControlStopped(STOP_MESSAGES[STOPPED_BY_INPUT])
        with self._state:
            if not self._active:
                raise ControlStopped(STOP_MESSAGES.get(self._reason, STOP_MESSAGES[FINISHED]))
            self._injecting = True

    def injected(self):
        with self._state:
            self._injecting = False
            self._last_injection_end = self._last_action_at = self._clock()

    def inject(self, action):
        self.check()
        try:
            return action()
        finally:
            self.injected()

    def chunks(self, text):
        return [text[start:start + CHUNK_CHARS] for start in range(0, len(text), CHUNK_CHARS)]

    def run(self, step):
        try:
            self._begin()
            return step()
        except ControlStopped as stopped:
            return str(stopped)


control = Control()
