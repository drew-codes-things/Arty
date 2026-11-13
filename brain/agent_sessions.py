import hashlib
import threading
import time

IDLE_SECONDS = 2 * 60 * 60
MAX_CONVERSATIONS = 64
MAX_FINGERPRINTS = 64
FINGERPRINT_CHARS = 200
MIN_FINGERPRINT_CHARS = 12


def flattened(text: str) -> str:
    return " ".join(str(text or "").split())


def fingerprint(text: str) -> str:
    first_paragraph = str(text or "").strip().split("\n\n", 1)[0]
    return flattened(first_paragraph)[:FINGERPRINT_CHARS]


def conversation_key(first_text: str) -> str:
    return hashlib.sha256(flattened(first_text).encode()).hexdigest()


class Session:
    def __init__(self, request: str):
        self.request = request
        self.fingerprints: dict[str, None] = {}
        self.last_seen = 0.0

    def remember(self, user_texts: list[str], now: float):
        for text in user_texts:
            mark = fingerprint(text)
            if len(mark) >= MIN_FINGERPRINT_CHARS:
                self.fingerprints.pop(mark, None)
                self.fingerprints[mark] = None
        for stale in list(self.fingerprints)[:-MAX_FINGERPRINTS]:
            del self.fingerprints[stale]
        self.last_seen = now

    def matches(self, first_text: str) -> int:
        carried = flattened(first_text)
        return sum(1 for mark in self.fingerprints if mark in carried)


class Conversation:
    def __init__(self, session: Session, opens_with_carried_text: bool):
        self.session = session
        self.opens_with_carried_text = opens_with_carried_text

    def own_texts(self, user_texts: list[str]) -> list[str]:
        return user_texts[1:] if self.opens_with_carried_text else user_texts


class AgentSessions:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.by_conversation: dict[str, Conversation] = {}

    def deciding_request(self, user_texts: list[str]) -> str:
        if not user_texts:
            return ""
        with self.lock:
            now = self.clock()
            self.forget_idle(now)
            key = conversation_key(user_texts[0])
            conversation = self.by_conversation.get(key) or self.opened(user_texts[0])
            self.by_conversation[key] = conversation
            conversation.session.remember(conversation.own_texts(user_texts), now)
            self.keep_newest()
            return conversation.session.request

    def opened(self, first_text: str) -> Conversation:
        earlier = self.carried_on_from(first_text)
        if earlier:
            return Conversation(earlier, opens_with_carried_text=True)
        return Conversation(Session(first_text), opens_with_carried_text=False)

    def sessions(self) -> set[Session]:
        return {conversation.session for conversation in self.by_conversation.values()}

    def carried_on_from(self, first_text: str) -> Session | None:
        scored = [(session.matches(first_text), session.last_seen, session) for session in self.sessions()]
        best = max(scored, key=lambda entry: entry[:2], default=None)
        return best[2] if best and best[0] else None

    def forget_idle(self, now: float):
        for key, conversation in list(self.by_conversation.items()):
            if now - conversation.session.last_seen > IDLE_SECONDS:
                del self.by_conversation[key]

    def keep_newest(self):
        oldest_first = sorted(self.by_conversation, key=lambda key: self.by_conversation[key].session.last_seen)
        for key in oldest_first[:-MAX_CONVERSATIONS]:
            del self.by_conversation[key]
