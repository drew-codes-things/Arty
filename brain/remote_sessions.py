import collections
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

MAX_WAIT_SECONDS = 25
IDLE_EXPIRY_SECONDS = 600
ENDED_KEPT_SECONDS = 300
MAX_SESSIONS = 20
MAX_SESSIONS_PER_KEY = 5
MAX_EVENTS_KEPT = 2000
MAX_CHARS_KEPT = 2_000_000
MAX_EVENTS_PER_POST = 50
MAX_INBOX = 50
MAX_OPEN_REQUESTS = 20
MAX_ANSWERS_KEPT = 100
MAX_TEXT_CHARS = 8000
MAX_LABEL_CHARS = 120
EVENT_TYPES = frozenset({"user", "assistant", "tool_call", "tool_result", "permission", "permission_answered",
                         "status", "error", "compacted", "end"})
TEXT_FIELDS = ("text", "tool", "request_id", "by", "mode")
COMPUTER = "the computer"
MAIN_KEY_ID = "main"
NOT_PRINTABLE = re.compile(r"[\x00-\x1f\x7f]+")


class RemoteError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Caller:
    key_id: str
    name: str
    may_control: bool


@dataclass
class Session:
    id: str
    owner: Caller
    name: str
    folder: str
    model: str
    mode: str
    last_seen: float
    title: str = ""
    events: collections.deque = field(default_factory=collections.deque)
    kept_chars: int = 0
    last_seq: int = 0
    inbox: list = field(default_factory=list)
    waiting_for: dict = field(default_factory=dict)
    answered_by: dict = field(default_factory=dict)
    ended_at: float | None = None


def label(value, fallback: str = "") -> str:
    text = NOT_PRINTABLE.sub(" ", str(value or "")).strip()
    return text[:MAX_LABEL_CHARS] or fallback


def capped_wait(raw) -> float:
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return 0
    if seconds != seconds:
        return 0
    return max(0, min(seconds, MAX_WAIT_SECONDS))


def cleaned_event(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("type") not in EVENT_TYPES:
        return None
    event = {"type": raw["type"]}
    for name in TEXT_FIELDS:
        if raw.get(name) is not None:
            event[name] = str(raw[name])[:MAX_TEXT_CHARS]
    if isinstance(raw.get("allow"), bool):
        event["allow"] = raw["allow"]
    if raw.get("history") is True:
        event["history"] = True
    return event


def text_size(event: dict) -> int:
    return sum(len(event[name]) for name in TEXT_FIELDS if name in event)


def distinct_title(wanted: str, taken: set[str]) -> str:
    if wanted not in taken:
        return wanted
    number = 2
    while f"{wanted} {number}" in taken:
        number += 1
    return f"{wanted} {number}"


def shares_cap(session: Session, caller: Caller, name: str) -> bool:
    if session.owner.key_id != caller.key_id:
        return False
    return caller.key_id != MAIN_KEY_ID or session.name == name


def summary(session: Session) -> dict:
    return {"id": session.id, "title": session.title, "name": session.name, "folder": session.folder,
            "model": session.model, "mode": session.mode, "computer_key": session.owner.name,
            "last_seq": session.last_seq,
            "waiting_for": [dict(request, request_id=request_id) for request_id, request in session.waiting_for.items()]}


class Sessions:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.changed = threading.Condition()

    def prune(self) -> None:
        now = self.clock()
        for session_id, session in list(self.sessions.items()):
            idle = now - session.last_seen > IDLE_EXPIRY_SECONDS
            forgotten = session.ended_at is not None and now - session.ended_at > ENDED_KEPT_SECONDS
            if forgotten or (session.ended_at is None and idle):
                del self.sessions[session_id]

    def live(self) -> list[Session]:
        return [session for session in self.sessions.values() if session.ended_at is None]

    def found(self, session_id: str) -> Session:
        self.prune()
        session = self.sessions.get(session_id)
        if session is None:
            raise RemoteError(404, "that session has ended or never existed")
        return session

    def owned(self, caller: Caller, session_id: str) -> Session:
        session = self.found(session_id)
        if session.owner.key_id != caller.key_id:
            raise RemoteError(403, "only the computer that started this session can do that")
        session.last_seen = self.clock()
        return session

    def controlled(self, caller: Caller, session_id: str) -> Session:
        if not caller.may_control:
            raise RemoteError(403, "this key isn't allowed to control Arty CLI; turn on remote control for it first")
        session = self.found(session_id)
        if session.ended_at is not None:
            raise RemoteError(410, "that session has ended")
        return session

    def record(self, session: Session, event: dict) -> None:
        session.last_seq += 1
        session.events.append({**event, "seq": session.last_seq})
        session.kept_chars += text_size(event)
        while len(session.events) > 1 and self.holds_too_much(session):
            session.kept_chars -= text_size(session.events.popleft())

    def holds_too_much(self, session: Session) -> bool:
        return len(session.events) > MAX_EVENTS_KEPT or session.kept_chars > MAX_CHARS_KEPT

    def free_title(self, wanted: str, renaming: Session | None = None) -> str:
        return distinct_title(wanted, {session.title for session in self.live() if session is not renaming})

    def create(self, caller: Caller, details: dict) -> dict:
        with self.changed:
            self.prune()
            live = self.live()
            if len(live) >= MAX_SESSIONS:
                raise RemoteError(429, "too many Arty CLI sessions are being shared; end one first")
            name = label(details.get("name"), "A computer")
            if sum(1 for session in live if shares_cap(session, caller, name)) >= MAX_SESSIONS_PER_KEY:
                raise RemoteError(429, "this key already shares the most sessions it may; end one first")
            folder = label(details.get("folder"))
            title = self.free_title(label(details.get("title"), folder or name))
            session = Session(id=secrets.token_hex(8), owner=caller, name=name, folder=folder, title=title,
                              model=label(details.get("model")), mode=label(details.get("mode"), "default"),
                              last_seen=self.clock())
            self.sessions[session.id] = session
            self.changed.notify_all()
            return summary(session)

    def add_events(self, caller: Caller, session_id: str, raw_events) -> int:
        if not isinstance(raw_events, list):
            raise RemoteError(400, "events must be a list")
        if len(raw_events) > MAX_EVENTS_PER_POST:
            raise RemoteError(413, f"send at most {MAX_EVENTS_PER_POST} events at a time")
        with self.changed:
            session = self.owned(caller, session_id)
            if session.ended_at is not None:
                raise RemoteError(410, "that session has ended")
            for event in filter(None, map(cleaned_event, raw_events)):
                if session.ended_at is not None:
                    break
                self.apply(session, event)
            self.changed.notify_all()
            return session.last_seq

    def apply(self, session: Session, event: dict) -> None:
        request_id = event.get("request_id", "")
        if event["type"] == "permission" and request_id:
            if len(session.waiting_for) >= MAX_OPEN_REQUESTS:
                session.waiting_for.pop(next(iter(session.waiting_for)))
            session.waiting_for[request_id] = {"tool": event.get("tool", ""), "text": event.get("text", "")}
            session.answered_by.pop(request_id, None)
        if event["type"] == "permission_answered" and request_id:
            if session.waiting_for.pop(request_id, None) is not None:
                self.remember_answer(session, request_id, event.get("by") or COMPUTER)
        if event["type"] == "status" and event.get("mode"):
            session.mode = label(event["mode"], session.mode)
        if event["type"] == "end":
            session.ended_at = self.clock()
        self.record(session, event)

    def remember_answer(self, session: Session, request_id: str, by: str) -> None:
        session.answered_by[request_id] = label(by, COMPUTER)
        while len(session.answered_by) > MAX_ANSWERS_KEPT:
            session.answered_by.pop(next(iter(session.answered_by)))

    def inbox(self, caller: Caller, session_id: str, wait: float) -> list[dict]:
        with self.changed:
            session = self.owned(caller, session_id)
            self.changed.wait_for(lambda: session.inbox or session.ended_at is not None, timeout=wait)
            items, session.inbox = session.inbox, []
            session.last_seen = self.clock()
            return items

    def end(self, caller: Caller, session_id: str) -> None:
        with self.changed:
            session = self.owned(caller, session_id)
            if session.ended_at is None:
                self.apply(session, {"type": "end"})
            self.changed.notify_all()

    def rename(self, caller: Caller, session_id: str, wanted) -> str:
        with self.changed:
            session = self.owned(caller, session_id)
            if session.ended_at is not None:
                raise RemoteError(410, "that session has ended")
            title = label(wanted)
            if not title:
                raise RemoteError(400, "the new name is empty")
            session.title = self.free_title(title, renaming=session)
            self.record(session, {"type": "status", "text": f"Renamed to {session.title}"})
            self.changed.notify_all()
            return session.title

    def listed(self, caller: Caller) -> list[dict]:
        if not caller.may_control:
            raise RemoteError(403, "this key isn't allowed to control Arty CLI; turn on remote control for it first")
        with self.changed:
            self.prune()
            return [summary(session) for session in self.live()]

    def events(self, caller: Caller, session_id: str, after: int, wait: float) -> dict:
        if not caller.may_control:
            raise RemoteError(403, "this key isn't allowed to control Arty CLI; turn on remote control for it first")
        with self.changed:
            session = self.found(session_id)
            self.changed.wait_for(lambda: session.last_seq > after or session.ended_at is not None, timeout=wait)
            fresh = [event for event in session.events if event["seq"] > after]
            oldest = session.events[0]["seq"] if session.events else session.last_seq + 1
            return {"events": fresh, "next": session.last_seq, "missed": oldest > after + 1,
                    "ended": session.ended_at is not None, "session": summary(session)}

    def queue(self, session: Session, item: dict) -> None:
        if len(session.inbox) >= MAX_INBOX:
            raise RemoteError(429, "the computer hasn't caught up yet; try again in a moment")
        session.inbox.append(item)
        self.changed.notify_all()

    def send_message(self, caller: Caller, session_id: str, text) -> None:
        message = str(text or "")
        if not message.strip():
            raise RemoteError(400, "the message is empty")
        if len(message) > MAX_TEXT_CHARS:
            raise RemoteError(413, f"keep messages under {MAX_TEXT_CHARS} characters")
        with self.changed:
            session = self.controlled(caller, session_id)
            self.queue(session, {"type": "message", "text": message, "by": caller.name})
            self.record(session, {"type": "remote_message", "text": message, "by": caller.name})

    def answer(self, caller: Caller, session_id: str, request_id: str, allow: bool) -> None:
        with self.changed:
            session = self.controlled(caller, session_id)
            if request_id in session.answered_by:
                raise RemoteError(409, f"already answered by {session.answered_by[request_id]}")
            if request_id not in session.waiting_for:
                raise RemoteError(404, "that request isn't waiting for an answer")
            self.queue(session, {"type": "answer", "request_id": request_id, "allow": allow, "by": caller.name})
            session.waiting_for.pop(request_id)
            self.remember_answer(session, request_id, caller.name)

    def interrupt(self, caller: Caller, session_id: str) -> None:
        with self.changed:
            session = self.controlled(caller, session_id)
            self.queue(session, {"type": "interrupt", "by": caller.name})


def handle(sessions: Sessions, caller: Caller, method: str, parts: list[str], query: dict, body: dict) -> tuple[int, dict]:
    wait = capped_wait((query.get("wait") or ["0"])[0])
    if parts == ["sessions"] and method == "GET":
        return 200, {"sessions": sessions.listed(caller)}
    if parts == ["sessions"] and method == "POST":
        return 201, sessions.create(caller, body)
    if len(parts) == 2 and parts[0] == "sessions" and method == "DELETE":
        sessions.end(caller, parts[1])
        return 200, {"ok": True}
    if len(parts) != 3 or parts[0] != "sessions":
        raise RemoteError(404, "not found")
    session_id, action = parts[1], parts[2]
    if (method, action) == ("POST", "events"):
        return 200, {"next": sessions.add_events(caller, session_id, body.get("events"))}
    if (method, action) == ("POST", "title"):
        return 200, {"title": sessions.rename(caller, session_id, body.get("title"))}
    if (method, action) == ("GET", "inbox"):
        return 200, {"inbox": sessions.inbox(caller, session_id, wait)}
    if (method, action) == ("GET", "events"):
        after = int((query.get("after") or ["0"])[0] or 0)
        return 200, sessions.events(caller, session_id, after, wait)
    if (method, action) == ("POST", "messages"):
        sessions.send_message(caller, session_id, body.get("text"))
        return 200, {"ok": True}
    if (method, action) == ("POST", "answers"):
        if not isinstance(body.get("allow"), bool) or not isinstance(body.get("request_id"), str):
            raise RemoteError(400, "send request_id and allow (true or false)")
        sessions.answer(caller, session_id, body["request_id"], body["allow"])
        return 200, {"ok": True}
    if (method, action) == ("POST", "interrupt"):
        sessions.interrupt(caller, session_id)
        return 200, {"ok": True}
    raise RemoteError(404, "not found")
