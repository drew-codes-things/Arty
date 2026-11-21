import json
import re
import struct
import sys
from pathlib import Path

EXTENSION_ORIGIN = "chrome-extension://lepggpdmafbgbgfgacpglpjelmiofkli/"
FIREFOX_ADDON_ID = "arty-browser@arty.home"
LENGTH_BYTES = 4
LENGTH_FORMAT = "=I"
MAX_MESSAGE_BYTES = 4096
PAIRING_FILE_NAME = ".arty_browser_pairing"
PAIRING_CODE = re.compile(r"[0-9a-f]{32}")
PAIR_REQUEST = "pair"
REFUSED_CALLER_EXIT = 2
BAD_MESSAGE_EXIT = 3


class BadMessage(ValueError):
    pass


def is_known_caller(argv):
    is_chromium_extension = len(argv) >= 2 and argv[1] == EXTENSION_ORIGIN
    is_firefox_addon = len(argv) >= 3 and argv[2] == FIREFOX_ADDON_ID
    return is_chromium_extension or is_firefox_addon


def _read_exactly(stream, count):
    data = stream.read(count)
    if data is None or len(data) != count:
        raise BadMessage(f"expected {count} bytes, got {0 if data is None else len(data)}")
    return data


def read_message(stream, limit=MAX_MESSAGE_BYTES):
    (length,) = struct.unpack(LENGTH_FORMAT, _read_exactly(stream, LENGTH_BYTES))
    if length > limit:
        raise BadMessage(f"a message of {length} bytes is over the {limit} byte limit")
    try:
        message = json.loads(_read_exactly(stream, length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise BadMessage(f"the message is not JSON: {error}") from None
    if not isinstance(message, dict):
        raise BadMessage("the message is not a JSON object")
    return message


def framed(answer):
    body = json.dumps(answer, separators=(",", ":")).encode("utf-8")
    return struct.pack(LENGTH_FORMAT, len(body)) + body


def stored_pairing_code(data_root):
    try:
        stored = (data_root / PAIRING_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return stored if PAIRING_CODE.fullmatch(stored) else ""


def answer_for(message, data_root):
    if message.get("type") != PAIR_REQUEST:
        return {"type": "unknown"}
    code = stored_pairing_code(data_root)
    return {"type": "pairing", "code": code} if code else {"type": "unavailable"}


def serve(argv, stdin, stdout, data_root):
    if not is_known_caller(argv):
        return REFUSED_CALLER_EXIT
    try:
        message = read_message(stdin)
    except BadMessage:
        return BAD_MESSAGE_EXIT
    stdout.write(framed(answer_for(message, data_root)))
    stdout.flush()
    return 0


def main():
    data_root = Path(__file__).resolve().parents[1]
    return serve(sys.argv, sys.stdin.buffer, sys.stdout.buffer, data_root)


if __name__ == "__main__":
    sys.exit(main())
