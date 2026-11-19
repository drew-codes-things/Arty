import hashlib
import hmac
import json
import math
import secrets
import threading
import time
import unicodedata

from process import config as cfg
from process import spake2

LINK_FILE = cfg.DATA_ROOT / ".arty_link"
CODE_DIGITS = 6
CODE_LIFETIME_SECONDS = 120
MAX_WRONG_CODES = 5
NAME_CHARS = 40
MAX_SENT_NAME_CHARS = 200
NAME_MARKS = set(" .,-_'()&+")
DEFAULT_NAME = "another app"
MAX_TOKEN_CHARS = 200
MAX_ARGUMENT_CHARS = 200_000
MAX_SHOWN_STEP_CHARS = 1000
SESSION_BYTES = 16
PROOF_CONTEXT = "arty-link-proof"
REFUSALS_BEFORE_PAUSE = 3
REFUSAL_PAUSE_SECONDS = 60
ALLOW_LINE = "Allow it?"
OWN_RULES_LINE = "Arty's own rules would ask about this step too."


class PairingRefused(ValueError):
    pass


class StepNotShown(ValueError):
    pass


def fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_name_character(character):
    return character in NAME_MARKS or unicodedata.category(character)[0] in ("L", "N")


def _clean_name(name):
    if not isinstance(name, str):
        return DEFAULT_NAME
    kept = "".join(character if _is_name_character(character) else " " for character in name)
    return " ".join(kept.split())[:NAME_CHARS].strip() or DEFAULT_NAME


def is_shown_exactly(text):
    has_hidden = any(unicodedata.category(character)[0] in ("C", "Z") and character != " " for character in text)
    return bool(text) and text == text.strip(" ") and not has_hidden


def _shown_value(key, value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return json.dumps(value)
    if not isinstance(value, str):
        raise StepNotShown(f"The {key} is not plain text or a number, so the card could not show it.")
    if not is_shown_exactly(value):
        raise StepNotShown(f"The {key} is empty, has more than one line, spaces at its ends or characters "
                           "that cannot be shown on the card, so it was not asked.")
    return value


def shown_step(arguments, known_keys):
    shown = []
    for key, value in arguments.items():
        if key not in known_keys:
            raise StepNotShown(f"{key} is not something this step takes.")
        shown.append((key, _shown_value(key, value)))
    if sum(len(value) for _key, value in shown) > MAX_SHOWN_STEP_CHARS:
        raise StepNotShown(f"That step is longer than the {MAX_SHOWN_STEP_CHARS} characters the card can show in full.")
    return shown


def step_card(app_name, title, shown, own_rules_ask):
    lines = [f"{app_name} asks to do this on your computer: {title}."]
    lines += [f"{key}: {value}" for key, value in shown]
    if own_rules_ask:
        lines.append(OWN_RULES_LINE)
    lines.append(ALLOW_LINE)
    return "\n".join(lines)


class RefusalPause:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._in_a_row = 0
        self._paused_until = 0.0

    def note(self, allowed):
        with self._lock:
            if allowed:
                self._in_a_row = 0
                return
            self._in_a_row += 1
            if self._in_a_row >= REFUSALS_BEFORE_PAUSE:
                self._in_a_row = 0
                self._paused_until = self._clock() + REFUSAL_PAUSE_SECONDS

    def seconds_left(self):
        with self._lock:
            return max(0.0, self._paused_until - self._clock())


class Link:
    def __init__(self, path, clock=time.monotonic):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._pairing = None

    def start_pairing(self):
        with self._lock:
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
            self._pairing = {"code": code, "expires": self._clock() + CODE_LIFETIME_SECONDS, "wrong": 0, "session": None}
        return {"code": code, "expires_in": CODE_LIFETIME_SECONDS}

    def begin_pair(self, name, app_share):
        with self._lock:
            pairing = self._usable_pairing()
            if pairing["wrong"] >= MAX_WRONG_CODES:
                self._pairing = None
                raise PairingRefused("Too many tries. Make a new code in Arty Desktop.")
            try:
                spake2.element_from_hex(app_share)
            except spake2.BadShare as problem:
                raise PairingRefused(str(problem))
            pairing["wrong"] += 1
            secret = spake2.new_secret()
            arty_share = spake2.share_hex(secret, pairing["code"], spake2.ARTY)
            key = spake2.shared_key(secret, pairing["code"], spake2.ARTY, app_share, arty_share)
            session = secrets.token_hex(SESSION_BYTES)
            pairing["session"] = {"id": session, "key": key, "name": name}
            return {"session": session, "share": arty_share, "proof": spake2.proof(key, spake2.ARTY)}

    def finish_pair(self, session_id, app_proof):
        with self._lock:
            pairing = self._usable_pairing()
            session = pairing["session"]
            pairing["session"] = None
            is_this_session = (session is not None and isinstance(session_id, str)
                               and hmac.compare_digest(session_id.encode(), session["id"].encode()))
            if not is_this_session:
                raise PairingRefused("That pairing was not started here, or a newer one replaced it. Try again.")
            expected = spake2.proof(session["key"], spake2.APP)
            if not isinstance(app_proof, str) or not hmac.compare_digest(app_proof.encode(), expected.encode()):
                raise PairingRefused("That code is not right.")
            self._pairing = None
            token = spake2.link_token(session["key"])
            self._save({"name": _clean_name(session["name"]), "fingerprint": fingerprint(token), "linked": int(time.time())})

    def _usable_pairing(self):
        pairing = self._pairing
        if pairing is None:
            raise PairingRefused("No code is showing. Make a new code in Arty Desktop.")
        if self._clock() > pairing["expires"]:
            self._pairing = None
            raise PairingRefused("That code has expired. Make a new code in Arty Desktop.")
        return pairing

    def verify(self, token):
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            return False
        stored = self._load().get("fingerprint")
        return bool(stored) and hmac.compare_digest(fingerprint(token), stored)

    def proof(self, port, nonce):
        stored = self._load().get("fingerprint")
        if not stored:
            return None
        return hmac.new(stored.encode(), f"{PROOF_CONTEXT}:{port}:{nonce}".encode(), hashlib.sha256).hexdigest()

    def status(self):
        record = self._load()
        if not record.get("fingerprint"):
            return {"linked": False}
        return {"linked": True, "name": _clean_name(record.get("name")), "since": record.get("linked")}

    def unlink(self):
        with self._lock:
            self._path.unlink(missing_ok=True)

    def _load(self):
        try:
            record = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    def _save(self, record):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_private(self._path, json.dumps(record))
