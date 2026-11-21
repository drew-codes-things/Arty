import io
import re
import wave

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MIN_SPOKEN_CHARS = 60
FIRST_SPOKEN_CHARS = 20
LONGEST_UNFINISHED_CHARS = 140
CLAUSE_END = re.compile(r"[,;:](?=\s)")
WORDS_PER_SECOND = 2.5
CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
BARE_URL = re.compile(r"https?://\S+")
LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
MARKUP = re.compile(r"[*_#`>|~]")
EMOJI = re.compile(r"[\U0001F000-\U0001FFFF☀-➿️]")
NOT_FOR_ME = "(not for me)"


def plain_speech(text: str) -> str:
    text = CODE_BLOCK.sub(" I've put the code in a chat instead. ", text)
    text = MARKDOWN_LINK.sub(r"\1", text)
    text = BARE_URL.sub("a link", text)
    text = LIST_MARKER.sub("", text)
    text = MARKUP.sub("", text)
    text = EMOJI.sub("", text)
    return " ".join(text.split())


def might_be_ignored(answer: str) -> bool:
    return NOT_FOR_ME.startswith(answer.strip())


class SentenceBuffer:

    def __init__(self):
        self.text = ""
        self.handed_out = False

    def add(self, delta: str) -> list[str]:
        self.text += delta
        parts = SENTENCE_END.split(self.text)
        complete, self.text = parts[:-1], parts[-1]
        chunks = self._group(complete)
        chunks += self._break_long_unfinished_sentence(chunks)
        self.handed_out = self.handed_out or bool(chunks)
        return chunks

    def flush(self) -> list[str]:
        remaining, self.text = self.text.strip(), ""
        self.handed_out = self.handed_out or bool(remaining)
        return [remaining] if remaining else []

    def _enough_to_speak(self, text: str, already: list[str]) -> bool:
        first = not self.handed_out and not already
        return len(text) >= (FIRST_SPOKEN_CHARS if first else MIN_SPOKEN_CHARS)

    def _group(self, sentences: list[str]) -> list[str]:
        chunks, current = [], ""
        for sentence in sentences:
            current = f"{current} {sentence}".strip()
            if self._enough_to_speak(current, chunks):
                chunks.append(current)
                current = ""
        if current:
            self.text = f"{current} {self.text}"
        return chunks

    def _break_long_unfinished_sentence(self, already: list[str]) -> list[str]:
        if already or len(self.text) < LONGEST_UNFINISHED_CHARS:
            return []
        breaks = [m.end() for m in CLAUSE_END.finditer(self.text) if m.end() >= FIRST_SPOKEN_CHARS]
        if not breaks:
            return []
        spoken, self.text = self.text[:breaks[-1]].strip(), self.text[breaks[-1]:].lstrip()
        return [spoken]


def wav_seconds(wav: bytes, words: str) -> float:
    try:
        with wave.open(io.BytesIO(wav)) as reader:
            return reader.getnframes() / reader.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        return len(words.split()) / WORDS_PER_SECOND


def words_heard(words: str, share: float) -> str:
    spoken = words.split()
    return " ".join(spoken[:int(len(spoken) * min(1.0, max(0.0, share)))])


class SpokenTurn:

    def __init__(self):
        self.said: list[str] = []
        self.current: tuple[str, float, float] | None = None

    def begin(self, words: str, seconds: float, now: float) -> None:
        self.current = (words, now, max(seconds, 0.01))

    def end(self) -> None:
        if self.current:
            self.said.append(self.current[0])
        self.current = None

    def heard(self, now: float, latency: float = 0.0) -> str:
        partial = ""
        if self.current:
            words, began, seconds = self.current
            partial = words_heard(words, (now - began - latency) / seconds)
        return " ".join(part for part in (*self.said, partial) if part)


class Voice:

    def __init__(self, model_path: str):
        from piper import PiperVoice
        self.voice = PiperVoice.load(model_path)

    def wav(self, text: str) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            self.voice.synthesize_wav(text, writer)
        return buffer.getvalue()
