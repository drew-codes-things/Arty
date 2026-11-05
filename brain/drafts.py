"""Faster first words: a chat answer's sentences go out while the model is still writing the rest.

Sent text can't be taken back (the desktop app may already have said it out loud), so a sentence goes
out only once it's complete and nothing will rewrite it, and Draft.finish makes the final answer start
with everything already sent. Clients can then treat the sent pieces as an ordinary append-only stream.
"""
import re

# The end of a sentence (or a line, for lists): where a piece of the answer can go out. Not a colon
# mid-line ('The error message "TypeError: ...'), which often isn't the end of anything.
SENTENCE_END = re.compile(r"(?<=[.!?])[ \t]+|\n+")
ROUND_BREAK = "\n\n"
FENCE = "```"


def _in_code_block(text: str) -> bool:
    """Whether text ends inside an unclosed ``` block (sending it would turn the rest into code)."""
    return text.count(FENCE) % 2 == 1


def _join(before: str, after: str) -> str:
    return f"{before}{ROUND_BREAK}{after}" if before and after else before or after


def remainder(sent: str, final: str) -> str | None:
    """What `final` adds after `sent`, if it starts with it (whitespace may differ: trim_offers
    re-joins sentences with single spaces). None if it says something else."""
    if final.startswith(sent):
        return final[len(sent):]
    position = 0
    for char in sent:
        if char.isspace():
            continue
        while position < len(final) and final[position].isspace():
            position += 1
        if position >= len(final) or final[position] != char:
            return None
        position += 1
    return final[position:]


class Draft:
    """One chat turn's early text. The tool loop calls start_round before each model call and feed
    with each piece the model writes; the turn ends with finish(final answer)."""

    def __init__(self, on_text, tidy, is_offer):
        self.on_text = on_text    # gets each new piece of text, in order
        self.tidy = tidy          # the same per-sentence clean-up the final answer gets
        self.is_offer = is_offer  # "Let me know if..." sentences, which trim_offers may drop from the end
        self.earlier = ""         # sent during earlier model rounds of this turn
        self.sent = ""            # sent during this round
        self.raw = ""             # this round's text as the model wrote it
        self.used = 0             # how much of raw has gone out
        self.open = False

    def everything(self) -> str:
        return _join(self.earlier, self.sent)

    def start_round(self, allowed: bool):
        """A new model call. allowed=False holds this round back (its answer may be thrown away)."""
        self.earlier, self.sent, self.raw, self.used, self.open = self.everything(), "", "", 0, allowed

    def feed(self, piece: str, risky):
        """More of the model's text. risky(text) says the round so far might yet be rewritten,
        which stops it going out for the rest of the round."""
        self.raw += piece
        if not self.open:
            return
        if risky(self.raw):
            self.open = False
            return
        start, ready = self.used, self.used
        for boundary in SENTENCE_END.finditer(self.raw, self.used):
            sentence = self.raw[start:boundary.start()].strip()
            if sentence and not self.is_offer(sentence) and not _in_code_block(self.raw[:boundary.start()]):
                ready = boundary.start()  # a held-back offer goes out once something follows it
            start = boundary.end()
        if ready > self.used:
            self._send(self.raw[self.used:ready])
            self.used = ready

    def before_tools(self):
        """The round ended in tool calls, so its unfinished last sentence is complete: send it. Small models
        sometimes start writing the call as a ```json block first; an unclosed block like that is dropped."""
        end = self.raw.rindex(FENCE) if _in_code_block(self.raw) else len(self.raw)
        if self.open and end > self.used and self.raw[self.used:end].strip():
            self._send(self.raw[self.used:end].rstrip())
        self.used = max(self.used, len(self.raw))

    def _send(self, raw_piece: str):
        text = self.tidy(raw_piece)
        if self.sent:
            self.sent += text
            self.on_text(text)
            return
        text = text.lstrip()
        if text:
            self.sent = text
            self.on_text(ROUND_BREAK + text if self.earlier else text)

    def finish(self, final: str) -> str:
        """The answer to give, starting with exactly what was already sent."""
        if self.sent and (rest := remainder(self.sent, final)) is not None:
            return _join(self.earlier, self.sent + rest)
        return _join(self.everything(), final)
