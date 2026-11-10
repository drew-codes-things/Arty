import threading
import time

CALL_STYLE = (
    "\n\n<context>This is a live phone-style call: the user only hears you, they can't see the screen. Keep each "
    "turn to a couple of plain spoken sentences. Nothing they'd need to read: no bullet points, formatting, URLs, "
    "code or emoji. Turn things back to them with a question when it fits.</context>"
)
PICK_UP = (
    "\n\n<context>The user is ringing you and you've just answered. Open the call in a sentence or two, the way "
    "you'd answer a friend, without repeating a hello you've already given in this conversation.</context>"
)
PICK_UP_SAID = "(calling you)"


class Call:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._started_at = None

    def _seconds(self) -> int:
        return round(self._clock() - self._started_at) if self._started_at is not None else 0

    def start(self) -> dict:
        with self._lock:
            if self._started_at is None:
                self._started_at = self._clock()
        return self.status()

    def end(self) -> dict:
        with self._lock:
            seconds = self._seconds()
            self._started_at = None
        return {"on": False, "seconds": seconds}

    def is_on(self) -> bool:
        return self._started_at is not None

    def status(self) -> dict:
        with self._lock:
            return {"on": self._started_at is not None, "seconds": self._seconds()}

    def note_for(self, note: str) -> str:
        return note + CALL_STYLE if self.is_on() else note
