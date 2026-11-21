import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_scratch = tempfile.mkdtemp(prefix="arty-jobs-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))
os.environ.setdefault("EMAIL_WATCH_STATE", os.path.join(_scratch, "email-watch.json"))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import personal  # noqa: E402
import store  # noqa: E402
from job_gate import GpuGate  # noqa: E402
from job_types import MailboxError, Message, Services  # noqa: E402

NOON = datetime(2026, 11, 20, 12, 0)
SCRATCH = Path(_scratch)


class FakeClock:
    def __init__(self, start: datetime = NOON):
        self.now = start

    def advance(self, **units) -> datetime:
        self.now += timedelta(**units)
        return self.now


class FakeTools:
    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        answer = self.answers.get(name, f"unknown tool {name}")
        return answer(arguments) if callable(answer) else answer

    def names(self):
        return [name for name, _ in self.calls]


class Sent:
    def __init__(self):
        self.items = []

    def __call__(self, title, message, actions=None, urgent=False, category="other"):
        self.items.append({"title": title, "message": message, "actions": actions or [], "urgent": urgent, "category": category})
        return "sent"

    def titles(self):
        return [item["title"] for item in self.items]


class FakeMailbox:
    def __init__(self, messages=(), failing=()):
        self.messages = list(messages)
        self.failing = set(failing)
        self.labelled = []
        self.archived = []

    def inbox(self, limit):
        return self.messages[:limit]

    def add_label(self, message_id, label):
        if message_id in self.failing:
            raise MailboxError("the fake mailbox refused")
        self.labelled.append((message_id, label))

    def archive(self, message_id):
        if message_id in self.failing:
            raise MailboxError("the fake mailbox refused")
        self.archived.append(message_id)

    def changes(self):
        return len(self.labelled) + len(self.archived)


def message(identifier, address, subject="Hello", snippet="", unread=True, labels=(), sender=""):
    return Message(identifier, address, sender or address.split("@")[0].title(), subject, snippet, unread, tuple(labels))


def services(tools=None, mailbox=None, sent=None, complete=None, settings=None, gate=None, chat=None, read_price=None):
    settings = settings or {}
    return Services(call_tool=tools or FakeTools(), complete=complete or (lambda prompt: ""), send=sent or Sent(),
                    record_chat=(lambda user, answer: chat.append((user, answer))) if chat is not None else (lambda user, answer: None),
                    settings=settings, gate=gate or GpuGate(SCRATCH / "no-lock", (9, 17)), mailbox=mailbox, place=None,
                    read_price=read_price)


def clean_database():
    store.init()
    personal.init()
    with store.connect() as connection:
        connection.execute("DELETE FROM schedules")
        connection.execute("DELETE FROM tasks")
        connection.execute("DELETE FROM announcements")
