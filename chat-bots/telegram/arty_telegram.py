"""Arty on Telegram (@ArtyAssistor_bot), for its owner only.

Uses only the Python standard library: the Bot API is plain HTTPS, read with long polling.
- Private chat: every message goes to Arty.
- Groups: /arty <text>, an @mention, or a reply to one of Arty's messages.
- /new forgets the current conversation in that chat.
- Photos, voice notes, audio, videos, video notes and files go to Arty as attachments (it sees
  pictures, hears speech, watches videos); stickers arrive as their emoji. Files Arty makes come
  back as Telegram files, since its download links only work on the home network.
- Notification buttons (Approve / Deny / Done / Cancel on paused tasks) arrive as callbacks
  "t:<task id>:<action>" and are passed to the brain's task API.
Pairing: until an owner is set, the bot ignores everyone except a user who sends
/pair <code> with the code from config.env; that user's id is saved to owner.txt.
Anyone else is ignored and their messages never reach Arty.

Runs as a PufferPanel server on the home server; settings come from config.env in its folder.
"""
import http.client
import json
import logging
import os
import queue
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OWNER_FILE = HERE / "owner.txt"
OFFSET_FILE = HERE / ".offset"
HISTORY_MESSAGES = 8  # sent with each message: 8 to 12 (see trimmed)
HISTORY_STEP_MESSAGES = 4
TELEGRAM_LIMIT = 4096
POLL_SECONDS = 25  # home routers drop idle connections; longer waits kept failing with "read timed out"
BRAIN_TIMEOUT_SECONDS = 900
TELEGRAM_NOTE = ("You are replying on Telegram. Keep replies short (under 1500 characters unless asked for more) "
                 "and use plain text: no markdown tables or headings.")


NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError, RuntimeError)
RETRY_SECONDS = 5


def load_config(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_config(HERE / "config.env")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_KEY = os.environ.get("ARTY_API_KEY", "")
PAIR_CODE = os.environ.get("TELEGRAM_PAIR_CODE", "")
PAIR_ATTEMPTS_PER_HOUR = 5
PAIR_WINDOW_SECONDS = 3600
pair_failures: list[float] = []
announced_locks: set[float] = set()
PAIRING_LOCKED_REPLY = "Too many wrong pairing codes. Try again later."
BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:8081/v1/chat/completions")
BRAIN_BASE = BRAIN_URL.split("/v1/", 1)[0]
CLIENT_NAME = "telegram"
TELEGRAM_FILE_LIMIT = 20 * 1024 * 1024  # bots may only download files up to 20 MB
DOWNLOAD_LINK = re.compile(r"!?\[([^\]]*)\]\((https?://[^)\s]+?(/arty-files/[^)\s]+))\)")
UPLOAD_KINDS = {"jpg": ("sendPhoto", "photo"), "jpeg": ("sendPhoto", "photo"), "png": ("sendPhoto", "photo"),
                "webp": ("sendPhoto", "photo"), "gif": ("sendAnimation", "animation"), "mp4": ("sendVideo", "video"),
                "mov": ("sendVideo", "video"), "webm": ("sendVideo", "video")}
DOCUMENT_UPLOAD = ("sendDocument", "document")
# Telegram message field -> default file name and type, for attachments that don't carry their own.
ATTACHMENT_KINDS = [("voice", "voice-note.ogg", "audio/ogg"), ("audio", "audio.mp3", "audio/mpeg"),
                    ("video", "video.mp4", "video/mp4"), ("video_note", "video-note.mp4", "video/mp4"),
                    ("animation", "animation.mp4", "video/mp4"), ("document", "file", "application/octet-stream")]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("arty-telegram")
histories: dict[int, list] = defaultdict(list)


def trimmed(history: list) -> list:
    """Cuts the conversation back to HISTORY_MESSAGES in one go once it's grown by HISTORY_STEP_MESSAGES.
    Its start then stays put for a few messages, so the brain's cached prompt stays valid; dropping the
    oldest message every time would make the model re-read the whole conversation on every message."""
    return history[-HISTORY_MESSAGES:] if len(history) > HISTORY_MESSAGES + HISTORY_STEP_MESSAGES else history


def telegram(method: str, http_timeout: int = 30, **params):
    request = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = json.loads(error.read() or b"{}")
    if not body.get("ok"):
        raise RuntimeError(f"{method}: {body.get('description')}")
    return body["result"]


def ask_brain(messages: list[dict]) -> str:
    body = json.dumps({"model": "Arty", "messages": messages, "client": CLIENT_NAME}).encode()
    request = urllib.request.Request(BRAIN_URL, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=BRAIN_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())["choices"][0]["message"]["content"].strip() or "(no reply)"
    except (OSError, KeyError, ValueError) as error:  # OSError covers URLError, timeouts and dropped connections
        log.error("brain request failed: %s", error)
        return f"I couldn't reach my brain just now ({error}). The AI server may be switched off."


def task_action(task_id: int, action: str) -> str:
    """Answer a paused background task through the brain's API; returns its message."""
    base = BRAIN_URL.split("/v1/", 1)[0]
    request = urllib.request.Request(f"{base}/v1/tasks/{task_id}/{action}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json_message(response.read(), "Done.", "The AI server sent a reply I didn't understand.")
    except urllib.error.HTTPError as error:
        fallback = f"Error {error.code}"
        return json_message(error.read(), fallback, fallback)
    except (urllib.error.URLError, TimeoutError) as error:
        return f"Couldn't reach the AI server ({error})."


def json_message(raw: bytes, missing: str, unreadable: str) -> str:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return unreadable
    if not isinstance(body, dict):
        return unreadable
    return body.get("message", missing)


def pairing_allowed(now: float) -> bool:
    pair_failures[:] = [moment for moment in pair_failures if now - moment < PAIR_WINDOW_SECONDS]
    return len(pair_failures) < PAIR_ATTEMPTS_PER_HOUR


def announce_lock_once(chat_id: int) -> None:
    lock = pair_failures[0]
    if lock in announced_locks:
        return
    announced_locks.intersection_update(pair_failures)
    announced_locks.add(lock)
    log.warning("pairing is locked after %d wrong codes", len(pair_failures))
    reply(chat_id, PAIRING_LOCKED_REPLY)


def code_matches(offered: str) -> bool:
    return bool(PAIR_CODE) and secrets.compare_digest(offered.encode(), PAIR_CODE.encode())


def handle_callback(query: dict):
    data = query.get("data") or ""
    if (query.get("from") or {}).get("id") != owner_id():
        telegram("answerCallbackQuery", callback_query_id=query["id"], text="These buttons are for Arty's owner.")
        return
    match = re.fullmatch(r"t:(\d+):(approve|deny|done|cancel)", data)
    if not match:
        telegram("answerCallbackQuery", callback_query_id=query["id"], text="That button has expired.")
        return
    result = task_action(int(match.group(1)), match.group(2))
    telegram("answerCallbackQuery", callback_query_id=query["id"], text=result[:190])
    message = query.get("message")
    if message:
        telegram("editMessageReplyMarkup", chat_id=message["chat"]["id"], message_id=message["message_id"],
                 reply_markup={"inline_keyboard": []})
        reply(message["chat"]["id"], result, reply_to=message["message_id"])


def chunks(text: str) -> list[str]:
    parts = []
    while len(text) > TELEGRAM_LIMIT:
        cut = text.rfind("\n", 0, TELEGRAM_LIMIT)
        cut = cut if cut > TELEGRAM_LIMIT // 2 else TELEGRAM_LIMIT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text] if text else parts


def owner_id() -> int | None:
    if OWNER_FILE.exists() and OWNER_FILE.read_text().strip().isdigit():
        return int(OWNER_FILE.read_text().strip())
    return None


def reply(chat_id: int, text: str, reply_to: int | None = None):
    for index, part in enumerate(chunks(text)):
        params = {"chat_id": chat_id, "text": part, "link_preview_options": {"is_disabled": True}}
        if reply_to and index == 0:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        telegram("sendMessage", **params)


def attachment_of(message: dict) -> tuple[str, str, str, int] | None:
    """(Telegram file id, name, mime type, size) of a photo, voice note, video or file in the message."""
    if message.get("photo"):
        largest = message["photo"][-1]
        return largest["file_id"], "photo.jpg", "image/jpeg", largest.get("file_size", 0)
    for key, default_name, default_mime in ATTACHMENT_KINDS:
        item = message.get(key)
        if item:
            return item["file_id"], item.get("file_name") or default_name, item.get("mime_type") or default_mime, item.get("file_size", 0)
    return None


def has_media(message: dict) -> bool:
    return attachment_of(message) is not None or "sticker" in message


def download_from_telegram(file_id: str) -> bytes:
    path = telegram("getFile", file_id=file_id)["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=120) as response:
        return response.read()


def upload_to_brain(data: bytes, name: str, mime: str) -> str:
    request = urllib.request.Request(f"{BRAIN_BASE}/v1/files", data=data, method="POST", headers={
        "Authorization": f"Bearer {API_KEY}", "Content-Type": mime, "X-Filename": urllib.parse.quote(name)})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())["id"]


def upload_kind(name: str) -> tuple[str, str]:
    return UPLOAD_KINDS.get(name.rsplit(".", 1)[-1].lower(), DOCUMENT_UPLOAD) if "." in name else DOCUMENT_UPLOAD


def send_upload(chat_id: int, name: str, data: bytes, reply_to: int | None, method: str, field: str):
    boundary = uuid.uuid4().hex
    fields = {"chat_id": str(chat_id)}
    if reply_to:
        fields["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
                    for key, value in fields.items())
    safe_name = name.replace('"', "")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{safe_name}\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=300) as response:
        response.read()


def send_file(chat_id: int, name: str, data: bytes, reply_to: int | None = None):
    method, field = upload_kind(name)
    if (method, field) != DOCUMENT_UPLOAD:
        try:
            return send_upload(chat_id, name, data, reply_to, method, field)
        except urllib.error.HTTPError as error:
            log.warning("%s refused %s (%s), sending it as a file", method, name, error)
    send_upload(chat_id, name, data, reply_to, *DOCUMENT_UPLOAD)


def send_made_files(chat_id: int, answer: str, reply_to: int | None) -> str:
    """Arty's download links only work at home: send those files here instead, and drop the links."""
    for link in DOWNLOAD_LINK.finditer(answer):
        name, path = link.group(1) or "file", link.group(3)
        try:
            with urllib.request.urlopen(f"{BRAIN_BASE}{path}", timeout=120) as response:
                send_file(chat_id, urllib.parse.unquote(name), response.read(), reply_to)
            answer = answer.replace(link.group(0), f"{name} (sent below)")
        except OSError as error:
            log.error("couldn't send %s: %s", name, error)
    return answer


def prompt_for(message: dict, me: dict) -> str | None:
    """The text meant for Arty, or None if this message isn't addressed to it. A photo, voice note
    or sticker with no words is addressed to Arty in a private chat, and gives "" here."""
    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        return "" if message["chat"]["type"] == "private" and has_media(message) else None
    if message["chat"]["type"] == "private":
        return re.sub(r"^/arty(@\w+)?\s*", "", text) or ("" if has_media(message) else None)
    command = re.match(rf"^/arty(@{me['username']})?\b\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if command:
        return command.group(2).strip() or None
    if f"@{me['username']}".lower() in text.lower():
        return re.sub(rf"@{me['username']}", "", text, flags=re.IGNORECASE).strip() or None
    replied = message.get("reply_to_message") or {}
    if (replied.get("from") or {}).get("id") == me["id"]:
        return text
    return None


def handle(message: dict, me: dict):
    sender = (message.get("from") or {}).get("id")
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    owner = owner_id()
    if owner is None:
        pair = re.match(r"^/(pair|start)\s+(\S+)", text)
        if not pair or message["chat"]["type"] != "private":
            return
        if not pairing_allowed(time.time()):
            announce_lock_once(chat_id)
            return
        if not code_matches(pair.group(2)):
            pair_failures.append(time.time())
            log.warning("wrong pairing code from %s (%d in the last hour)", sender, len(pair_failures))
            return
        OWNER_FILE.write_text(str(sender))
        log.info("paired with owner %s", sender)
        reply(chat_id, "Paired. I'm Arty, and from now on I only listen to you.")
        return
    if sender != owner:
        return
    if re.match(rf"^/new(@{me['username']})?$", text):
        histories[chat_id].clear()
        reply(chat_id, "Fresh start: I've forgotten this chat's recent messages (my long-term memory stays).")
        return
    if re.match(r"^/start\b", text):
        reply(chat_id, "Hi, I'm Arty. Just message me here, or use /arty in groups.")
        return
    prompt = prompt_for(message, me)
    if prompt is None:
        return
    reply_to = message["message_id"] if message["chat"]["type"] != "private" else None
    if "sticker" in message:
        prompt = f"{message['sticker'].get('emoji') or '🙂'} (sent as a sticker){' ' + prompt if prompt else ''}"
    content: str | list = prompt
    found = attachment_of(message)
    if found:
        file_id, name, mime, size = found
        if size > TELEGRAM_FILE_LIMIT:
            reply(chat_id, "Telegram only lets me download files up to 20 MB. Send it through the Arty app instead.", reply_to)
            return
        try:
            brain_file = upload_to_brain(download_from_telegram(file_id), name, mime)
        except (OSError, RuntimeError, KeyError, ValueError) as error:
            log.error("couldn't pass %s on to Arty: %s", name, error)
            reply(chat_id, f"I couldn't open that file ({error}).", reply_to)
            return
        content = [{"type": "text", "text": prompt or "(no message, just this)"},
                   {"type": "file", "file": {"file_id": brain_file, "filename": name}}]
    history = histories[chat_id]
    thinking = threading.Event()
    threading.Thread(target=keep_typing, args=(chat_id, thinking), daemon=True).start()
    try:
        answer = ask_brain([{"role": "system", "content": TELEGRAM_NOTE}, *history, {"role": "user", "content": content}])
        answer = send_made_files(chat_id, answer, reply_to)
    finally:
        thinking.set()
    histories[chat_id] = trimmed(history + [{"role": "user", "content": content}, {"role": "assistant", "content": answer}])
    reply(chat_id, answer, reply_to=reply_to)


TYPING_EVERY_SECONDS = 4  # Telegram shows "typing…" for about 5 seconds per request


def keep_typing(chat_id: int, done: threading.Event):
    """Keeps "typing…" showing for as long as Arty takes to answer."""
    while not done.is_set():
        try:
            telegram("sendChatAction", chat_id=chat_id, action="typing")
        except (OSError, RuntimeError):
            pass
        done.wait(TYPING_EVERY_SECONDS)


def message_worker():
    """Answers messages one at a time, so polling (and approval buttons) carry on while Arty thinks."""
    while True:
        message, me = incoming.get()
        try:
            handle(message, me)
        except Exception:  # one bad message must never stop the bot
            log.exception("handling a message failed")


incoming: queue.Queue = queue.Queue()


def until_it_works(step):
    while True:
        try:
            return step()
        except NETWORK_ERRORS as error:
            log.warning("can't reach Telegram yet: %s", error)
            time.sleep(RETRY_SECONDS)


def start_up() -> dict:
    me = telegram("getMe")
    telegram("setMyCommands", commands=[{"command": "arty", "description": "Ask Arty"},
                                        {"command": "new", "description": "Start a fresh conversation"}])
    return me


def main():
    if not TOKEN or not API_KEY:
        # Exit cleanly (code 0) so PufferPanel's crash recovery doesn't restart it in a loop.
        print("Set TELEGRAM_BOT_TOKEN and ARTY_API_KEY in config.env (PufferPanel > Files), then start again.")
        return
    me = until_it_works(start_up)
    log.info("running as @%s; owner %s", me["username"], owner_id() or "not paired yet (send /pair <code> in a private chat)")
    offset = int(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else 0
    threading.Thread(target=message_worker, name="messages", daemon=True).start()
    while True:
        try:
            updates = telegram("getUpdates", http_timeout=POLL_SECONDS + 10, offset=offset,
                               timeout=POLL_SECONDS, allowed_updates=["message", "callback_query"])
        except NETWORK_ERRORS as error:
            log.warning("polling failed: %s", error)
            time.sleep(RETRY_SECONDS)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            OFFSET_FILE.write_text(str(offset))
            try:
                if "message" in update:
                    incoming.put((update["message"], me))
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])  # quick, so straight away
            except Exception:  # one bad update must never stop the bot
                log.exception("handling an update failed")


if __name__ == "__main__":
    main()
