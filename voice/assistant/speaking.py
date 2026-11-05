import io
import re
import wave

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MIN_SPOKEN_CHARS = 60
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

    def add(self, delta: str) -> list[str]:
        self.text += delta
        parts = SENTENCE_END.split(self.text)
        complete, self.text = parts[:-1], parts[-1]
        return self._group(complete)

    def flush(self) -> list[str]:
        remaining, self.text = self.text.strip(), ""
        return [remaining] if remaining else []

    def _group(self, sentences: list[str]) -> list[str]:
        chunks, current = [], ""
        for sentence in sentences:
            current = f"{current} {sentence}".strip()
            if len(current) >= MIN_SPOKEN_CHARS:
                chunks.append(current)
                current = ""
        if current:
            self.text = f"{current} {self.text}"
        return chunks


class Voice:

    def __init__(self, model_path: str):
        from piper import PiperVoice
        self.voice = PiperVoice.load(model_path)

    def wav(self, text: str) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            self.voice.synthesize_wav(text, writer)
        return buffer.getvalue()
