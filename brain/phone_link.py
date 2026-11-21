import collections
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass

import devices
import spake2

OFFER_SECONDS = 120
OFFER_ID_BYTES = 12
CODE_BYTES = 16
SESSION_BYTES = 16
MAX_OFFERS = 5
MAX_BEGINS_PER_OFFER = 5
MAX_BEGINS_PER_MINUTE = 30
MAX_UNKNOWN_BEGINS_PER_MINUTE = 10
MAX_TRACKED_CALLERS = 1000
BEGIN_WINDOW_SECONDS = 60
OUTCOME_KEPT_SECONDS = 300
PHONE_KIND = "phone"
MAX_ACCESS_CHARS = 200
SEALED_ACCESS_VERSION = 1
ACCESS_FORMAT = re.compile(r"[\x21-\x7e]+")
WAITING, LINKED, GONE = "waiting", "linked", "gone"
USED_UP = "This code has run out or was already used. Show a new one in Arty Desktop: tray menu > Link a phone."
TOO_MANY_BEGINS = "Too many phones tried to link just now. Wait a minute, then scan again."


class LinkRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Offer:
    id: str
    code: str
    access: dict
    created: float
    begins: int = 0
    session: dict | None = None
    state: str = WAITING
    name: str = ""
    device_id: int | None = None
    ended: float | None = None


def cleaned_access(access) -> dict:
    if access in (None, {}):
        return {}
    if not isinstance(access, dict) or set(access) != {"id", "secret"}:
        raise LinkRefused(400, "access must be {\"id\": ..., \"secret\": ...} or left out")
    for value in access.values():
        if not isinstance(value, str) or len(value) > MAX_ACCESS_CHARS or not ACCESS_FORMAT.fullmatch(value):
            raise LinkRefused(400, "the access id and secret are each one word of printable characters")
    return {"id": access["id"], "secret": access["secret"]}


def phone_key(key: bytes) -> str:
    return devices.DEVICE_KEY_PREFIX + spake2.link_token(key)


def handed_access(access: dict, key: bytes, seal) -> dict:
    if not access:
        return {}
    if seal != SEALED_ACCESS_VERSION:
        return {"access": access}
    return {"sealed_access": spake2.seal(key, json.dumps(access, separators=(",", ":")).encode())}


def is_same(supplied, expected: str) -> bool:
    return isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), expected.encode())


class Offers:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.by_id = collections.OrderedDict()
        self.recent_begins = collections.deque()
        self.unknown_begins = collections.OrderedDict()

    def is_out_of_time(self, offer: Offer) -> bool:
        return offer.state == WAITING and self.clock() - offer.created > OFFER_SECONDS

    def end(self, offer: Offer, state: str) -> None:
        offer.state, offer.code, offer.access, offer.session, offer.ended = state, "", {}, None, self.clock()

    def prune(self) -> None:
        for offer in list(self.by_id.values()):
            if self.is_out_of_time(offer):
                self.end(offer, GONE)
            if offer.ended is not None and self.clock() - offer.ended > OUTCOME_KEPT_SECONDS:
                del self.by_id[offer.id]

    def waiting(self) -> list[Offer]:
        return [offer for offer in self.by_id.values() if offer.state == WAITING]

    def create(self, access=None) -> dict:
        kept_access = cleaned_access(access)
        with self.lock:
            self.prune()
            for oldest in self.waiting()[:max(0, len(self.waiting()) - MAX_OFFERS + 1)]:
                self.end(oldest, GONE)
            offer = Offer(secrets.token_urlsafe(OFFER_ID_BYTES), secrets.token_hex(CODE_BYTES), kept_access, self.clock())
            self.by_id[offer.id] = offer
            return {"offer": offer.id, "code": offer.code, "expires_in": OFFER_SECONDS}

    def status(self, offer_id: str) -> dict:
        with self.lock:
            self.prune()
            offer = self.by_id.get(offer_id) if isinstance(offer_id, str) else None
            if offer is None or offer.state == GONE:
                return {"state": GONE}
            if offer.state == LINKED:
                return {"state": LINKED, "name": offer.name, "device_id": offer.device_id}
            return {"state": WAITING, "expires_in": max(0, int(OFFER_SECONDS - (self.clock() - offer.created)))}

    def too_many_recent_begins(self) -> bool:
        while self.recent_begins and self.clock() - self.recent_begins[0] > BEGIN_WINDOW_SECONDS:
            self.recent_begins.popleft()
        return len(self.recent_begins) >= MAX_BEGINS_PER_MINUTE

    def has_guessed_too_often(self, caller: str) -> bool:
        times = self.unknown_begins.get(caller)
        while times and self.clock() - times[0] > BEGIN_WINDOW_SECONDS:
            times.popleft()
        if times is not None and not times:
            del self.unknown_begins[caller]
        return bool(times) and len(times) >= MAX_UNKNOWN_BEGINS_PER_MINUTE

    def note_unknown_begin(self, caller: str) -> None:
        times = self.unknown_begins.pop(caller, None) or collections.deque()
        times.append(self.clock())
        self.unknown_begins[caller] = times
        while len(self.unknown_begins) > MAX_TRACKED_CALLERS:
            self.unknown_begins.popitem(last=False)

    def usable(self, offer_id) -> Offer:
        offer = self.by_id.get(offer_id) if isinstance(offer_id, str) else None
        if offer is None or offer.state != WAITING:
            raise LinkRefused(410, USED_UP)
        return offer

    def begin(self, offer_id, name, phone_share, caller: str = "") -> dict:
        if not spake2.has_share_format(phone_share):
            raise LinkRefused(400, spake2.WRONG_SHAPE)
        with self.lock:
            self.prune()
            if self.has_guessed_too_often(caller):
                raise LinkRefused(429, TOO_MANY_BEGINS)
            try:
                offer = self.usable(offer_id)
            except LinkRefused:
                self.note_unknown_begin(caller)
                raise
            if self.too_many_recent_begins():
                raise LinkRefused(429, TOO_MANY_BEGINS)
            try:
                spake2.element_from_hex(phone_share)
            except spake2.BadShare as problem:
                raise LinkRefused(400, str(problem)) from None
            self.recent_begins.append(self.clock())
            if offer.begins >= MAX_BEGINS_PER_OFFER:
                self.end(offer, GONE)
                raise LinkRefused(410, USED_UP)
            offer.begins += 1
            secret = spake2.new_secret()
            arty_share = spake2.share_hex(secret, offer.code, spake2.ARTY)
            key = spake2.shared_key(secret, offer.code, spake2.ARTY, phone_share, arty_share)
            session = secrets.token_hex(SESSION_BYTES)
            offer.session = {"id": session, "key": key, "name": devices.cleaned_name(name)}
            return {"session": session, "share": arty_share, "proof": spake2.proof(key, spake2.ARTY)}

    def finish(self, offer_id, session_id, phone_proof, issue, seal=None) -> dict:
        with self.lock:
            self.prune()
            offer = self.usable(offer_id)
            session = offer.session
            asks_sealing = session is not None and is_same(phone_proof, spake2.proof(session["key"], spake2.SEALING_APP))
            proven = (session is not None and is_same(session_id, session["id"])
                      and (asks_sealing or is_same(phone_proof, spake2.proof(session["key"], spake2.APP))))
            if not proven:
                self.end(offer, GONE)
                raise LinkRefused(403, "That phone didn't prove it scanned this code, so the code is now used up. "
                                       "Show a new one in Arty Desktop.")
            access = handed_access(dict(offer.access), session["key"], SEALED_ACCESS_VERSION if asks_sealing else seal)
            device_id = issue(session["name"], phone_key(session["key"]))
            offer.name, offer.device_id = session["name"], device_id
            self.end(offer, LINKED)
            return {"linked": True, "name": offer.name, **access}
