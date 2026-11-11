import hashlib
import hmac
import re
import secrets


MAX_LIST_LIMIT = 200
IDENTITY_NONCE = re.compile(r"[0-9a-fA-F]{16,128}")
IDENTITY_LABEL = "arty-identity:"
MAX_CLIENT_NAME_CHARS = 32
CLIENT_NAME_UNWANTED = re.compile(r"[^a-z0-9_-]")


def optional_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def same_secret(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode("utf-8", "surrogateescape"), expected.encode("utf-8", "surrogateescape"))


def identity_proofs(nonce: str, keys) -> list[str]:
    message = (IDENTITY_LABEL + nonce).encode()
    return [hmac.new(key.encode(), message, hashlib.sha256).hexdigest() for key in keys if key]


def text_field(body: dict, name: str) -> str:
    value = body.get(name, "")
    if not isinstance(value, str):
        raise ApiError(400, f"{name} must be text")
    return value


def list_limit(query: dict, default: int) -> int:
    limit = int((query.get("limit") or [str(default)])[0])
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ApiError(400, f"limit must be 1 to {MAX_LIST_LIMIT}")
    return limit


def valid_messages(messages) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise ApiError(400, "messages are required")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ApiError(400, "each message must be an object with a role")
        if not isinstance(message.get("content", ""), (str, list, type(None))):
            raise ApiError(400, "message content must be text or a list of parts")
    return messages


def client_name(body: dict) -> str:
    value = body.get("client")
    if not isinstance(value, str):
        return ""
    return CLIENT_NAME_UNWANTED.sub("", value.strip().lower())[:MAX_CLIENT_NAME_CHARS]
