import hmac
import secrets
import time

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
GROUP_LENGTH = 4
LIFETIME_SECONDS = 600
MAX_LIVE_CODES = 5
MAX_WRONG_TRIES = 10
MAX_WRONG_TRIES_EVERYWHERE = 100
MAX_TRACKED_ADDRESSES = 256
WRONG_WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 300
EVERYWHERE = "*"

OK = "ok"
WRONG = "wrong"
LOCKED = "locked"


def normalised(text: str) -> str:
    return "".join(character for character in text.upper() if character.isalnum())


class PairingCodes:

    def __init__(self, clock=time.time):
        self.clock = clock
        self.live: dict[str, float] = {}
        self.wrong_by_address: dict[str, list[float]] = {}
        self.wrong_everywhere: list[float] = []
        self.locked_until: dict[str, float] = {}

    def forget_expired(self) -> None:
        now = self.clock()
        self.live = {code: expires for code, expires in self.live.items() if expires > now}

    def issue(self) -> str:
        self.forget_expired()
        while len(self.live) >= MAX_LIVE_CODES:
            del self.live[min(self.live, key=self.live.get)]
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        self.live[code] = self.clock() + LIFETIME_SECONDS
        return f"{code[:GROUP_LENGTH]}-{code[GROUP_LENGTH:]}"

    def redeem(self, text: str, source: str = "") -> str:
        now = self.clock()
        if self.is_locked(source, now):
            return LOCKED
        self.forget_expired()
        typed = normalised(text)
        match = next((code for code in self.live if hmac.compare_digest(code.encode(), typed.encode())), None)
        if match is not None:
            del self.live[match]
            self.wrong_by_address.pop(source, None)
            return OK
        self.count_wrong(source, now)
        return WRONG

    def is_locked(self, source: str, now: float) -> bool:
        self.locked_until = {key: until for key, until in self.locked_until.items() if until > now}
        return EVERYWHERE in self.locked_until or source in self.locked_until

    def count_wrong(self, source: str, now: float) -> None:
        recent = [when for when in self.wrong_by_address.pop(source, []) if now - when < WRONG_WINDOW_SECONDS] + [now]
        self.wrong_everywhere = [when for when in self.wrong_everywhere if now - when < WRONG_WINDOW_SECONDS] + [now]
        if len(recent) >= MAX_WRONG_TRIES:
            self.locked_until[source] = now + LOCKOUT_SECONDS
        else:
            self.wrong_by_address[source] = recent
        if len(self.wrong_everywhere) >= MAX_WRONG_TRIES_EVERYWHERE:
            self.locked_until[EVERYWHERE] = now + LOCKOUT_SECONDS
            self.wrong_everywhere.clear()
        self.forget_old_addresses(now)

    def forget_old_addresses(self, now: float) -> None:
        self.wrong_by_address = {source: times for source, times in self.wrong_by_address.items()
                                 if now - times[-1] < WRONG_WINDOW_SECONDS}
        while len(self.wrong_by_address) > MAX_TRACKED_ADDRESSES:
            del self.wrong_by_address[next(iter(self.wrong_by_address))]
