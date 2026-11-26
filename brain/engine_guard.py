import contextlib
import contextvars
import http.client
import json
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from settings import ARTY_ENGINE_URL

GENERATION_PATHS = ("/api/chat", "/api/generate")
WATCH_SECONDS = 0.25
NOT_STREAMED = "_debug_render_only"
STREAM_CUT = "model error: the engine stream ended before the reply was finished"


class TurnStopped(Exception):
    pass


class Preempted(TurnStopped):
    pass


class OutOfTime(TurnStopped):
    pass


@dataclass
class Guard:
    deadline: float
    is_interrupted: object = None
    clock: object = time.monotonic
    interrupted: bool = field(default=False)

    def seconds_left(self) -> float:
        return self.deadline - self.clock()

    def check(self) -> None:
        if self.is_interrupted and self.is_interrupted():
            self.interrupted = True
            raise Preempted("outside work needs the model")
        if self.seconds_left() <= 0:
            raise OutOfTime("the room turn ran out of time")


class OutsideWork:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.running = 0
        self.last_seen: float | None = None

    @contextlib.contextmanager
    def working(self):
        with self.lock:
            self.running += 1
            self.last_seen = self.clock()
        try:
            yield
        finally:
            with self.lock:
                self.running -= 1
                self.last_seen = self.clock()

    def is_busy(self) -> bool:
        with self.lock:
            return self.running > 0

    def quiet_for(self) -> float:
        with self.lock:
            if self.running:
                return 0.0
            return float("inf") if self.last_seen is None else self.clock() - self.last_seen


outside_work = OutsideWork()
_guard: contextvars.ContextVar = contextvars.ContextVar("engine_guard", default=None)
_housekeeping: contextvars.ContextVar = contextvars.ContextVar("engine_housekeeping", default=False)


@contextlib.contextmanager
def guarded(guard: Guard):
    token = _guard.set(guard)
    try:
        yield guard
    finally:
        _guard.reset(token)


@contextlib.contextmanager
def housekeeping():
    token = _housekeeping.set(True)
    try:
        yield
    finally:
        _housekeeping.reset(token)


def current() -> Guard | None:
    return _guard.get()


def is_generation(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    engine = urllib.parse.urlsplit(ARTY_ENGINE_URL)
    return parts.netloc == engine.netloc and parts.path in GENERATION_PATHS


def outside_use(url: str):
    if current() is not None or _housekeeping.get() or not is_generation(url):
        return contextlib.nullcontext()
    return outside_work.working()


def streamed_body(body: dict) -> dict:
    return body if body.get(NOT_STREAMED) else {**body, "stream": True}


def joined_reply(path: str, events: list[dict], on_text=None, on_thinking=None) -> dict:
    last, content, thinking, calls = {}, [], [], []
    for event in events:
        if event.get("error"):
            raise RuntimeError(f"model error: {event['error']}")
        last = event
        piece = event.get("message") or {}
        calls += piece.get("tool_calls") or []
        text = piece.get("content") or event.get("response") or ""
        thought = piece.get("thinking") or event.get("thinking") or ""
        content.append(text)
        thinking.append(thought)
        if on_text and text:
            on_text(text)
        if on_thinking and thought:
            on_thinking(thought)
    if not last.get("done"):
        raise RuntimeError(STREAM_CUT)
    if path == "/api/generate":
        return {**last, "response": "".join(content), "thinking": "".join(thinking)}
    message = {"role": "assistant", "content": "".join(content), "tool_calls": calls}
    if any(thinking):
        message["thinking"] = "".join(thinking)
    return {**last, "message": message}


def read_events(response) -> list[dict]:
    return [json.loads(line) for line in response if line.strip()]


class EngineCall:
    def __init__(self, url: str, seconds: float):
        parts = urllib.parse.urlsplit(url)
        self.path = parts.path
        self.connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=seconds)
        self.lock = threading.Lock()
        self.sock = None
        self.stopped = False

    def ask(self, body: dict, on_text, on_thinking):
        self.connection.connect()
        self.connection.auto_open = 0
        with self.lock:
            self.sock = self.connection.sock
            if self.stopped:
                raise TurnStopped("the room call was stopped before it was sent")
            self.connection.request("POST", self.path, body=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json"})
        response = self.connection.getresponse()
        if response.status >= 400:
            raise RuntimeError(f"model error: the engine answered {response.status} {response.read()[:200]!r}")
        if body.get(NOT_STREAMED):
            raw = response.read()
            return json.loads(raw) if raw else None
        return joined_reply(self.path, read_events(response), on_text, on_thinking)

    def hang_up(self) -> None:
        with self.lock:
            self.stopped = True
            sock = self.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        self.connection.close()


def guarded_call(url: str, body: dict, guard: Guard, timeout: float = 600, on_text=None, on_thinking=None):
    guard.check()
    call = EngineCall(url, max(0.1, min(timeout, guard.seconds_left())))
    outcome: dict = {}

    def run():
        try:
            outcome["reply"] = call.ask(streamed_body(body), on_text, on_thinking)
        except Exception as error:  # noqa: BLE001
            outcome["error"] = error

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(WATCH_SECONDS)
            if worker.is_alive():
                guard.check()
    except TurnStopped:
        call.hang_up()
        raise
    if "error" in outcome:
        raise outcome["error"]
    return outcome["reply"]
