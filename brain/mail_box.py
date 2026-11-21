import email
import email.policy
import imaplib
import logging
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

import app_settings
from job_types import MailboxError, Message

SETTINGS_FILE = app_settings.settings_file("MAIL_ENV", "/srv/ai/secrets/mail.env")
SECURITY_CHOICES = ("ssl", "starttls", "none")
DEFAULT_IMAP_PORTS = {"ssl": 993, "starttls": 143, "none": 143}
DEFAULT_SMTP_PORTS = {"ssl": 465, "starttls": 587, "none": 25}
TIMEOUT_SECONDS = 30
INBOX = "INBOX"
DEFAULT_DRAFTS_FOLDER = "Drafts"
DRAFT_FLAG = r"(\Draft)"
SEEN_FLAG = b"\\Seen"
MOST_LISTED = 25
REPLY_PREFIX = re.compile(r"^\s*re\s*:", re.IGNORECASE)
FETCH_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
HTML_TAG = re.compile(r"<[^<>]*>")
BLANK_LINES = re.compile(r"\n{3,}")
GMAIL_EXTENSION = "X-GM-EXT-1"
MOVE_EXTENSION = "MOVE"
DEFAULT_ARCHIVE_FOLDER = "Archive"
TIDY_LABEL = re.compile(r"^[A-Za-z0-9_ /-]{1,40}$")
ARCHIVE_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_ ./\[\]-]{1,100}$")
TIDY_MESSAGE_ID = re.compile(r"^(\d{1,20})\.(\d{1,20})$")
LARGEST_SCANNED_BYTES = 1_000_000
LARGEST_BODY_CHARS = 200_000
SNIPPET_CHARS = 300

log = logging.getLogger("brain.mail")


class MailError(RuntimeError):
    pass


@dataclass(frozen=True)
class MailSettings:
    address: str
    user: str
    password: str
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    drafts_folder: str
    name: str
    archive_folder: str = ""


def _security(value: str, label: str) -> str:
    security = (value or "ssl").lower()
    if security not in SECURITY_CHOICES:
        raise MailError(f"{label} must be one of {', '.join(SECURITY_CHOICES)}")
    return security


def _port(value: str, default: int, label: str) -> int:
    if not value:
        return default
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise MailError(f"{label} must be a port number")
    return int(value)


def load_settings(values: dict[str, str] | None = None) -> MailSettings | None:
    values = app_settings.read_settings(SETTINGS_FILE) if values is None else values
    address = values.get("MAIL_ADDRESS", "")
    if not (address and values.get("MAIL_IMAP_HOST") and values.get("MAIL_SMTP_HOST")):
        return None
    imap_security = _security(values.get("MAIL_IMAP_SECURITY", ""), "MAIL_IMAP_SECURITY")
    smtp_security = _security(values.get("MAIL_SMTP_SECURITY", ""), "MAIL_SMTP_SECURITY")
    return MailSettings(
        address=address, user=values.get("MAIL_USER") or address, password=values.get("MAIL_PASSWORD", ""),
        imap_host=values["MAIL_IMAP_HOST"],
        imap_port=_port(values.get("MAIL_IMAP_PORT", ""), DEFAULT_IMAP_PORTS[imap_security], "MAIL_IMAP_PORT"),
        imap_security=imap_security, smtp_host=values["MAIL_SMTP_HOST"],
        smtp_port=_port(values.get("MAIL_SMTP_PORT", ""), DEFAULT_SMTP_PORTS[smtp_security], "MAIL_SMTP_PORT"),
        smtp_security=smtp_security, drafts_folder=values.get("MAIL_DRAFTS_FOLDER") or DEFAULT_DRAFTS_FOLDER,
        name=values.get("MAIL_NAME", ""), archive_folder=_archive_folder(values.get("MAIL_ARCHIVE_FOLDER", "")))


def _archive_folder(value: str) -> str:
    if value and not ARCHIVE_FOLDER_NAME.fullmatch(value):
        raise MailError("MAIL_ARCHIVE_FOLDER may only use letters, numbers, spaces and . _ / [ ] -")
    return value


def _refuse_plain_text_off_the_lan(host: str, security: str):
    if security == "none" and not app_settings.is_local_host(host):
        raise MailError(f"{host} is not on this machine or the home network, so the password would go unencrypted: "
                        "use ssl or starttls")


def _open_imap(settings: MailSettings) -> imaplib.IMAP4:
    _refuse_plain_text_off_the_lan(settings.imap_host, settings.imap_security)
    try:
        if settings.imap_security == "ssl":
            connection = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, ssl_context=ssl.create_default_context(),
                                           timeout=TIMEOUT_SECONDS)
        else:
            connection = imaplib.IMAP4(settings.imap_host, settings.imap_port, timeout=TIMEOUT_SECONDS)
            if settings.imap_security == "starttls":
                connection.starttls(ssl_context=ssl.create_default_context())
        connection.login(settings.user, settings.password)
    except (OSError, imaplib.IMAP4.error) as error:
        raise MailError(f"couldn't sign in to the mail server {settings.imap_host} ({type(error).__name__})") from None
    return connection


def _close_imap(connection: imaplib.IMAP4):
    try:
        connection.logout()
    except (OSError, imaplib.IMAP4.error) as error:
        log.warning("the mail server didn't close the connection cleanly: %s", type(error).__name__)


def _checked(answer: tuple, what: str):
    status, data = answer
    if status != "OK":
        raise MailError(f"the mail server refused to {what}")
    return data


def _fetched_parts(data: list) -> list[tuple[bytes, bytes]]:
    return [(item[0], item[1]) for item in data if isinstance(item, tuple) and len(item) == 2]


def _plain_text(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()[:LARGEST_BODY_CHARS]
    if part.get_content_subtype() == "html":
        text = HTML_TAG.sub("", text)
    return BLANK_LINES.sub("\n\n", text.replace("\r\n", "\n")).strip()


def _summary(uid: str, raw: bytes, flags: bytes) -> dict:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    name, address = parseaddr(str(message.get("From", "")))
    return {"uid": uid, "name": name, "address": address, "subject": str(message.get("Subject", "")),
            "date": str(message.get("Date", "")), "unread": SEEN_FLAG not in flags.split()}


def recent(settings: MailSettings, count: int) -> list[dict]:
    count = max(1, min(int(count), MOST_LISTED))
    connection = _open_imap(settings)
    try:
        _checked(connection.select(INBOX, readonly=True), "open the inbox")
        uids = _checked(connection.uid("SEARCH", None, "ALL"), "list the inbox")[0].split()[-count:]
        found = []
        for uid in reversed(uids):
            for head, raw in _fetched_parts(_checked(connection.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER])"), "read an email")):
                flags = FETCH_FLAGS.search(head)
                found.append(_summary(uid.decode(), raw, flags.group(1) if flags else b""))
        return found
    finally:
        _close_imap(connection)


def message(settings: MailSettings, uid: str) -> dict:
    if not str(uid).isdigit():
        raise MailError("an email's number is a whole number from email_inbox")
    connection = _open_imap(settings)
    try:
        _checked(connection.select(INBOX, readonly=True), "open the inbox")
        parts = _fetched_parts(_checked(connection.uid("FETCH", str(uid), "(BODY.PEEK[])"), "read that email"))
    finally:
        _close_imap(connection)
    if not parts:
        raise MailError(f"there's no email number {uid} in the inbox")
    parsed = email.message_from_bytes(parts[0][1][:LARGEST_SCANNED_BYTES], policy=email.policy.default)
    name, address = parseaddr(str(parsed.get("From", "")))
    return {"uid": str(uid), "name": name, "address": address, "subject": str(parsed.get("Subject", "")),
            "date": str(parsed.get("Date", "")), "message_id": str(parsed.get("Message-ID", "")).strip(),
            "references": " ".join(str(parsed.get("References", "")).split()), "body": _plain_text(parsed)}


def reply_subject(subject: str) -> str:
    return subject if REPLY_PREFIX.match(subject or "") else f"Re: {subject}".strip()


def compose(settings: MailSettings, to: list[str], subject: str, body: str, cc: list[str] = (),
            replying_to: dict | None = None) -> EmailMessage:
    composed = EmailMessage()
    composed["From"] = formataddr((settings.name, settings.address)) if settings.name else settings.address
    composed["To"] = ", ".join(to)
    if cc:
        composed["Cc"] = ", ".join(cc)
    composed["Subject"] = subject
    composed["Date"] = formatdate(localtime=True)
    composed["Message-ID"] = make_msgid(domain=settings.address.rpartition("@")[2] or None)
    if replying_to and replying_to.get("message_id"):
        composed["In-Reply-To"] = replying_to["message_id"]
        composed["References"] = " ".join(filter(None, [replying_to.get("references", ""), replying_to["message_id"]]))
    composed.set_content(body)
    return composed


def send(settings: MailSettings, composed: EmailMessage):
    _refuse_plain_text_off_the_lan(settings.smtp_host, settings.smtp_security)
    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(),
                                      timeout=TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_SECONDS)
        with server:
            server.ehlo()
            if settings.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings.password:
                server.login(settings.user, settings.password)
            refused = server.send_message(composed)
    except (OSError, smtplib.SMTPException) as error:
        raise MailError(f"the mail server {settings.smtp_host} didn't take the email ({type(error).__name__})") from None
    if refused:
        raise MailError(f"the mail server refused {', '.join(sorted(refused))}")


def save_draft(settings: MailSettings, composed: EmailMessage):
    connection = _open_imap(settings)
    try:
        _checked(connection.append(settings.drafts_folder, DRAFT_FLAG, imaplib.Time2Internaldate(time.time()),
                                   composed.as_bytes()), f"save the draft in {settings.drafts_folder}")
    finally:
        _close_imap(connection)


class TidyMailbox:
    def __init__(self, load=load_settings):
        self.load = load

    def _settings(self) -> MailSettings:
        try:
            settings = self.load()
        except MailError as error:
            raise MailboxError(str(error)) from None
        if settings is None:
            raise MailboxError("email isn't set up yet (bin/set-mail)")
        return settings

    def inbox(self, limit: int) -> list[Message]:
        settings = self._settings()
        try:
            connection = _open_imap(settings)
            try:
                _checked(connection.select(INBOX, readonly=True), "open the inbox")
                validity = _uid_validity(connection)
                found = _checked(connection.uid("SEARCH", None, "SMALLER", str(LARGEST_SCANNED_BYTES)), "list the inbox")
                uids = found[0].split()[-max(1, int(limit)):] if found and found[0] else []
                return [message for uid in reversed(uids) for message in _tidy_messages(connection, validity, uid)]
            finally:
                _close_imap(connection)
        except (MailError, OSError, imaplib.IMAP4.error) as error:
            raise MailboxError(_plain_reason(error)) from None

    def add_label(self, message_id: str, label: str) -> None:
        if not TIDY_LABEL.fullmatch(label or ""):
            raise MailboxError("a label may only use letters, numbers, spaces and _ / -")
        self._change(message_id, lambda connection, uid, gmail: _label(connection, uid, label, gmail))

    def archive(self, message_id: str) -> None:
        settings = self._settings()
        self._change(message_id, lambda connection, uid, gmail: _archive(connection, uid, settings, gmail))

    def _change(self, message_id: str, change) -> None:
        validity, uid = _tidy_id_parts(message_id)
        settings = self._settings()
        try:
            connection = _open_imap(settings)
            try:
                _checked(connection.select(INBOX), "open the inbox")
                if _uid_validity(connection) != validity:
                    raise MailboxError("the inbox was renumbered since it was read, so nothing was changed")
                change(connection, uid, GMAIL_EXTENSION in _capabilities(connection))
            finally:
                _close_imap(connection)
        except (MailError, OSError, imaplib.IMAP4.error) as error:
            raise MailboxError(_plain_reason(error)) from None


def _plain_reason(error: Exception) -> str:
    return str(error) if isinstance(error, MailError) else f"the mail server failed ({type(error).__name__})"


def _uid_validity(connection: imaplib.IMAP4) -> str:
    _, values = connection.response("UIDVALIDITY")
    value = values[0] if values and values[0] else b""
    value = value.decode() if isinstance(value, bytes) else str(value)
    if not value.isdigit():
        raise MailError("the mail server didn't say how it numbers the inbox")
    return value


def _capabilities(connection: imaplib.IMAP4) -> set[str]:
    listed = _checked(connection.capability(), "list what it supports")
    return {word.decode().upper() for word in (listed[0] or b"").split()}


def _tidy_id_parts(message_id: str) -> tuple[str, str]:
    parts = TIDY_MESSAGE_ID.fullmatch(message_id or "")
    if not parts:
        raise MailboxError("that isn't an email number from the inbox tidy")
    return parts.group(1), parts.group(2)


def _tidy_messages(connection: imaplib.IMAP4, validity: str, uid: bytes) -> list[Message]:
    fetched = _fetched_parts(_checked(connection.uid("FETCH", uid, "(FLAGS BODY.PEEK[])"), "read an email"))
    found = []
    for head, raw in fetched:
        flags_seen = FETCH_FLAGS.search(head)
        flags = (flags_seen.group(1) if flags_seen else b"").split()
        parsed = email.message_from_bytes(raw, policy=email.policy.default)
        name, address = parseaddr(str(parsed.get("From", "")))
        keywords = tuple(flag.decode(errors="replace") for flag in flags if not flag.startswith(b"\\"))
        found.append(Message(id=f"{validity}.{uid.decode()}", address=address, sender=name,
                             subject=str(parsed.get("Subject", "")), snippet=_plain_text(parsed)[:SNIPPET_CHARS],
                             unread=SEEN_FLAG not in flags, labels=keywords))
    return found


def _label(connection: imaplib.IMAP4, uid: str, label: str, gmail: bool):
    if gmail:
        _checked(connection.uid("STORE", uid, "+X-GM-LABELS", f'("{label}")'), f"label an email {label}")
    else:
        _checked(connection.uid("STORE", uid, "+FLAGS.SILENT", f"({label.replace(' ', '_')})"), f"label an email {label}")


def _archive(connection: imaplib.IMAP4, uid: str, settings: MailSettings, gmail: bool):
    if gmail and not settings.archive_folder:
        _checked(connection.uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)"), "archive an email")
        return
    if MOVE_EXTENSION not in _capabilities(connection):
        raise MailError("the mail server can't move mail, so nothing was archived")
    folder = settings.archive_folder or DEFAULT_ARCHIVE_FOLDER
    _checked(connection.uid("MOVE", uid, f'"{folder}"'), f"move an email to {folder}")
