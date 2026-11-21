from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

import notify
from job_gate import GpuGate

KEEP_MESSAGE_CHARS = 4000


class JobFailed(Exception):
    pass


class Mailbox(Protocol):
    def inbox(self, limit: int) -> list["Message"]: ...

    def add_label(self, message_id: str, label: str) -> None: ...

    def archive(self, message_id: str) -> None: ...


class MailboxError(Exception):
    pass


@dataclass(frozen=True)
class Message:
    id: str
    address: str
    sender: str
    subject: str
    snippet: str
    unread: bool
    labels: tuple[str, ...] = ()


@dataclass
class Services:
    call_tool: Callable[[str, dict], str]
    complete: Callable[[str], str]
    send: Callable[..., str]
    record_chat: Callable[[str, str], None]
    settings: dict
    gate: GpuGate
    mailbox: Mailbox | None = None
    place: dict | None = None
    read_price: Callable[[str], object] | None = None


@dataclass
class Run:
    schedule_id: int
    title: str
    spec: dict
    state: dict
    services: Services
    now: datetime
    call: Callable[[str, dict], str]

    def tell(self, title: str, message: str, urgent: bool = False, category: str = notify.ARTY) -> None:
        self.services.send(title, message[:KEEP_MESSAGE_CHARS], urgent=urgent, category=category)

