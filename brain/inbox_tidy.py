import logging
import re
from dataclasses import dataclass

import notify
import store
from email_watch import BULK_DOMAIN, MATTERS, from_a_person
from job_types import JobFailed, Mailbox, MailboxError, Message, Run
from steering import task_links
from watchers import plain

log = logging.getLogger("brain.inbox_tidy")

APPLY_TOOL = "inbox_apply"
SOURCE = "inbox_tidy"
LABEL_KIND = "label"
ARCHIVE_KIND = "archive"
KINDS = (LABEL_KIND, ARCHIVE_KIND)
SCAN_LIMIT = 50
MAX_CHANGES = 25
MAX_RULES = 20
REMEMBERED_SUGGESTIONS = 500
SHOWN_SUBJECT_CHARS = 60
SHOWN_SENDER_CHARS = 40
MAX_LABEL_CHARS = 40
NEWSLETTERS = "Newsletters"
NOTIFICATIONS = "Notifications"
RECEIPTS = "Receipts"
NEWSLETTER_SENDER = re.compile(r"^(news(letter)?s?|marketing|offers?|deals|digest|promo(tion)?s?|mailer|updates?)\b", re.IGNORECASE)
NOTIFICATION_SENDER = re.compile(r"^(notifications?|notify|alerts?|no-?reply|do-?not-?reply)\b", re.IGNORECASE)
UNSUBSCRIBE = re.compile(r"\bunsubscribe\b", re.IGNORECASE)
RECEIPT_SUBJECT = re.compile(r"\b(receipt|order (confirmation|confirmed)|your order|payment received|thanks for your (order|purchase))\b",
                             re.IGNORECASE)
KEEP_IN_INBOX = re.compile(
    r"\b(payments?|amount|balance|bill|instal+ments?|premium|rent|fee|fine|charge)s?\b[^.!?\n]{0,40}\b(is |are |now |falls? )?(due|owed|outstanding|overdue)\b|"
    r"\bpay (by|before|now|today)\b|\b(due|payable) (today|tomorrow|now|in \d+ days?)\b|\boutstanding balance\b|\bfinal (notice|reminder)\b|"
    r"\b(late|missed) (payment|fee)\b|\bcouncil tax\b|\brenew(s|al|ing)?\b|"
    r"\b(deliver(y|ies|ed|ing)?|parcels?|packages?|couriers?|dispatch(ed)?|shipped|shipping|out for delivery|tracking|collection|"
    r"missed you|tried to deliver)\b|"
    r"\b(appointments?|bookings?|reservations?|booked|reschedul\w*|check-?in|your (visit|consultation|session)|"
    r"dentist|doctor|gp|surgery|clinic|hospital|vet)\b",
    re.IGNORECASE)
ADDRESS = re.compile(r"^[^@\s*]+@[^@\s*]+\.[^@\s*]+$")
DOMAIN_RULE = re.compile(r"^@[^@\s*]+\.[^@\s*]+$")
LABEL_NAME = re.compile(r"^[\w &/-]{1,%d}$" % MAX_LABEL_CHARS)
MESSAGE_ID = re.compile(r"^[\w.=+-]{1,200}$")
DENIED_TEXT = "Not applied: you said no, so nothing in your inbox was changed."

_connected: dict[str, Mailbox | None] = {"mailbox": None}


def connect(mailbox: Mailbox | None) -> None:
    _connected["mailbox"] = mailbox


def connected() -> Mailbox | None:
    return _connected["mailbox"]


@dataclass(frozen=True)
class Change:
    message_id: str
    kind: str
    label: str
    address: str
    sender: str
    subject: str

    @property
    def key(self) -> str:
        return f"{self.message_id}|{self.kind}|{self.label}"

    def line(self) -> str:
        what = f'label "{self.label}"' if self.kind == LABEL_KIND else "archive"
        return f'{what}: "{plain(self.subject, SHOWN_SUBJECT_CHARS)}" from {plain(self.sender or self.address, SHOWN_SENDER_CHARS)}'

    def as_arguments(self) -> dict:
        return {"message_id": self.message_id, "kind": self.kind, "label": self.label}


def label_for(message: Message) -> str | None:
    text = f"{message.subject} {message.snippet}"
    if from_a_person({"email": message.address}) or MATTERS.search(text) or KEEP_IN_INBOX.search(text):
        return None
    if RECEIPT_SUBJECT.search(message.subject):
        return RECEIPTS
    local, _, domain = message.address.partition("@")
    if NEWSLETTER_SENDER.match(local) or BULK_DOMAIN.match(domain) or UNSUBSCRIBE.search(text):
        return NEWSLETTERS
    if NOTIFICATION_SENDER.match(local):
        return NOTIFICATIONS
    return None


def suggest(messages: list[Message]) -> list[Change]:
    changes = []
    for message in messages:
        label = label_for(message)
        if label is None:
            continue
        made = [Change(message.id, LABEL_KIND, label, message.address, message.sender, message.subject)] \
            if label not in message.labels else []
        if label != RECEIPTS:
            made.append(Change(message.id, ARCHIVE_KIND, "", message.address, message.sender, message.subject))
        changes.extend(made)
    return changes


def rule_problem(sender: str, action: str, label: str) -> str | None:
    if not (ADDRESS.fullmatch(sender) or DOMAIN_RULE.fullmatch(sender)):
        return "Give one exact sender address (like news@shop.com) or one exact domain written @shop.com, with no wildcards."
    if action not in KINDS:
        return "The action has to be label or archive."
    if action == LABEL_KIND and not LABEL_NAME.fullmatch(label):
        return "Give the label's name (letters, numbers, spaces, up to 40 characters)."
    if action == ARCHIVE_KIND and label:
        return "An archive rule has no label."
    return None


def covers(rule: dict, change: Change) -> bool:
    if rule.get("action") != change.kind or rule.get("label", "") != change.label:
        return False
    sender = str(rule.get("sender", "")).lower()
    address = change.address.lower()
    return address.rpartition("@")[2] == sender[1:] if sender.startswith("@") else address == sender


def validated(arguments: dict) -> list[dict]:
    changes = arguments.get("changes")
    if not isinstance(changes, list) or not changes or len(changes) > MAX_CHANGES:
        raise ValueError("the change list is missing or too long")
    for change in changes:
        valid = (isinstance(change, dict) and change.get("kind") in KINDS and isinstance(change.get("message_id"), str)
                 and MESSAGE_ID.fullmatch(change["message_id"]) and isinstance(change.get("label", ""), str)
                 and (change["kind"] == ARCHIVE_KIND or LABEL_NAME.fullmatch(change.get("label", ""))))
        if not valid:
            raise ValueError("a change in the list isn't a plain label or archive")
    return changes


def apply_changes(mailbox: Mailbox, changes: list[dict]) -> tuple[int, int]:
    done = failed = 0
    for change in changes:
        try:
            if change["kind"] == LABEL_KIND:
                mailbox.add_label(change["message_id"], change["label"])
            else:
                mailbox.archive(change["message_id"])
            done += 1
        except MailboxError as error:
            failed += 1
            log.warning("inbox change %s failed: %s", change["kind"], plain(error, 100))
    return done, failed


def apply_approved(mailbox: Mailbox | None, arguments: dict) -> str:
    if mailbox is None:
        return "No mailbox is connected, so nothing was changed."
    try:
        changes = validated(arguments)
    except ValueError as error:
        return f"Nothing was changed: {error}."
    done, failed = apply_changes(mailbox, changes)
    left = f" {failed} couldn't be done and were left as they were." if failed else ""
    return f"Applied {done} of {len(changes)} inbox changes.{left}"


def card_is_open(state: dict) -> bool:
    task = store.get_task(state["open_task"]) if state.get("open_task") else None
    return bool(task) and task["status"] == "waiting"


def open_card(run: Run, changes: list[Change]) -> None:
    lines = [f"{number}. {change.line()}" for number, change in enumerate(changes, 1)]
    message = "Arty wants to change your inbox (nothing is deleted or sent):\n" + "\n".join(lines)
    task = store.create_task(f"Inbox tidy: {len(changes)} changes", "Apply the inbox changes you allow.", SOURCE)
    pending = {"kind": "approval", "message": message,
               "call": {"name": APPLY_TOOL, "arguments": {"changes": [change.as_arguments() for change in changes]}}}
    store.update_task(task["id"], status="waiting", waiting=pending)
    buttons = task_links(store.get_task(task["id"]), [("Allow", "approve"), ("Deny", "deny")])
    run.services.send(f"Arty wants to tidy your inbox (task #{task['id']})", message, buttons, urgent=True, category=notify.APPROVALS)
    run.state["open_task"] = task["id"]


def remember(run: Run, changes: list[Change]) -> None:
    kept = run.state.get("suggested", []) + [change.key for change in changes]
    run.state["suggested"] = kept[-REMEMBERED_SUGGESTIONS:]


def summary_of(applied: list[Change], done: int, failed: int) -> str:
    kinds = {}
    for change in applied:
        name = f'labelled {change.label}' if change.kind == LABEL_KIND else "archived"
        kinds[name] = kinds.get(name, 0) + 1
    parts = ", ".join(f"{count} {name}" for name, count in kinds.items())
    left = f" {failed} couldn't be done." if failed else ""
    return f"Applied {done} changes you pre-approved ({parts}).{left}"


def run_tidy(run: Run) -> None:
    mailbox = run.services.mailbox
    if mailbox is None:
        raise JobFailed("no mailbox is connected yet")
    try:
        messages = mailbox.inbox(SCAN_LIMIT)
    except MailboxError as error:
        raise JobFailed(f"couldn't read the inbox ({plain(error, 100)})") from error
    seen = set(run.state.get("suggested", []))
    fresh = [change for change in suggest(messages) if change.key not in seen][:MAX_CHANGES]
    rules = run.spec.get("preapproved", [])
    pre_approved = [change for change in fresh if any(covers(rule, change) for rule in rules)]
    needs_allow = [change for change in fresh if change not in pre_approved]
    if pre_approved:
        done, failed = apply_changes(mailbox, [change.as_arguments() for change in pre_approved])
        remember(run, pre_approved)
        run.tell("Inbox tidied", summary_of(pre_approved, done, failed), category=notify.EMAIL)
    if needs_allow and not card_is_open(run.state):
        open_card(run, needs_allow)
        remember(run, needs_allow)
