import collections
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_HALF = 4
LIFETIME_SECONDS = 600
POLL_SECONDS = 3
MAX_PER_ADDRESS = 3
MAX_PENDING = 50
MAX_STARTS_PER_MINUTE = 20
START_WINDOW_SECONDS = 60
DEVICE_KEY_PREFIX = "arty_dev_"
MAX_NAME_CHARS = 80
UNNAMED = "A device"
PENDING, APPROVED, DENIED = "pending", "approved", "denied"
NOT_PRINTABLE = re.compile(r"[\x00-\x1f\x7f]+")


class DeviceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Request:
    device_code: str
    user_code: str
    name: str
    address: str
    created: float
    state: str = PENDING


def normalised_code(code: str) -> str:
    letters = re.sub(r"[\s-]", "", str(code or "")).upper()
    return f"{letters[:CODE_HALF]}-{letters[CODE_HALF:]}" if len(letters) == 2 * CODE_HALF else ""


def cleaned_name(name: str) -> str:
    return " ".join(NOT_PRINTABLE.sub(" ", str(name or "")).split())[:MAX_NAME_CHARS] or UNNAMED


def new_device_key() -> str:
    return DEVICE_KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()


def new_user_code() -> str:
    letters = "".join(secrets.choice(CODE_ALPHABET) for _ in range(2 * CODE_HALF))
    return f"{letters[:CODE_HALF]}-{letters[CODE_HALF:]}"


class Requests:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.by_device = {}
        self.recent_starts = collections.deque()

    def expired(self, request: Request) -> bool:
        return self.clock() - request.created > LIFETIME_SECONDS

    def prune(self) -> None:
        for device_code in [code for code, request in self.by_device.items() if self.expired(request)]:
            del self.by_device[device_code]

    def by_user_code(self, user_code: str) -> Request | None:
        wanted = normalised_code(user_code)
        return next((request for request in self.by_device.values() if request.user_code == wanted), None)

    def too_many_recent_starts(self) -> bool:
        while self.recent_starts and self.clock() - self.recent_starts[0] > START_WINDOW_SECONDS:
            self.recent_starts.popleft()
        return len(self.recent_starts) >= MAX_STARTS_PER_MINUTE

    def start(self, name: str, address: str) -> Request:
        with self.lock:
            self.prune()
            if self.too_many_recent_starts():
                raise DeviceError(429, "too many devices asked to be approved just now; try again in a minute")
            if len(self.by_device) >= MAX_PENDING:
                raise DeviceError(429, "too many devices are waiting to be approved; try again in a few minutes")
            if sum(request.address == address for request in self.by_device.values()) >= MAX_PER_ADDRESS:
                raise DeviceError(429, "this device already has requests waiting; approve or let them run out first")
            user_code = new_user_code()
            while self.by_user_code(user_code):
                user_code = new_user_code()
            request = Request(secrets.token_urlsafe(32), user_code, cleaned_name(name), address, self.clock())
            self.by_device[request.device_code] = request
            self.recent_starts.append(self.clock())
            return request

    def pending(self, user_code: str) -> Request | None:
        with self.lock:
            self.prune()
            request = self.by_user_code(user_code)
            return request if request and request.state == PENDING else None

    def answer(self, user_code: str, approve: bool) -> bool:
        with self.lock:
            self.prune()
            request = self.by_user_code(user_code)
            if request is None or request.state != PENDING:
                return False
            request.state = APPROVED if approve else DENIED
            return True

    def collect(self, device_code: str, key) -> tuple[int, dict]:
        with self.lock:
            self.prune()
            request = self.by_device.get(device_code)
            if request is None:
                return 410, {"error": "expired"}
            if request.state == PENDING:
                return 202, {"status": "pending"}
            del self.by_device[device_code]
            if request.state == DENIED:
                return 403, {"error": "denied"}
            return 200, {"api_key": key(request) if callable(key) else key}

    def seconds_left(self, request: Request) -> int:
        return max(0, int(LIFETIME_SECONDS - (self.clock() - request.created)))
