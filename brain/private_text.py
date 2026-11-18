import base64
import binascii
import bisect
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property, lru_cache

log = logging.getLogger("brain")

RUN_WORDS = 12
RUN_LETTERS = 60
SHINGLE_WORDS = 4
SHARED_BLOCK_WORDS = 20
SHARED_PART_OF_A_BLOCK = 0.5
MERGE_GAP_WORDS = 6
DECODED_PRINTABLE_SHARE = 0.85
CACHED_TABLES = 12
WORDS_LEFT_FOR_A_QUIET_CUT = 5
CHECKS_AFTER_A_QUIET_CUT = 2
EDGE_WORDS_LEFT_ALONE = SHINGLE_WORDS - 1
DECLINE = "(I keep my own instructions private, so I've left that out.)"
UNSEEN_CATEGORIES = frozenset({"Mn", "Me", "Cf"})
JOINING_MARKS = frozenset("'’ʼ")
ENCODED_MIN_CHARS = 24
ROT13 = str.maketrans("abcdefghijklmnopqrstuvwxyz", "nopqrstuvwxyzabcdefghijklm")
ENCODED_RUN = re.compile(rf"[A-Za-z0-9+/_-]{{{ENCODED_MIN_CHARS},}}={{0,2}}(?:[ \t]*\n[ \t]*[A-Za-z0-9+/_-]{{4,}}={{0,2}})*")
SPACED_HEX = re.compile(r"\b(?:[0-9A-Fa-f]{2}[ \t:,]+){15,}[0-9A-Fa-f]{2}\b")
HEX_ONLY = re.compile(r"(?:[0-9A-Fa-f]{2})+")
HEX_PREFIX = "0x"
SENTENCE_CLOSERS = "*_\"')\\]”’"
SENTENCE_BREAK = re.compile(rf"(?:(?<=[.!?])|(?<=[.!?][{SENTENCE_CLOSERS}])|(?<=[.!?][{SENTENCE_CLOSERS}]{{2}}))\s+|\n")
FENCED_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
WORD = re.compile(r"[^\W_]+")
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
STILL_BEING_WRITTEN = " \t:,"
TEXT_SEPARATOR = " | "
LINE_NUMBER = re.compile(r"(?m)^[ \t]*\d+[\t|:] ?")
EMPTY_LIST_ITEM = re.compile(r"(?<![^\n])[ \t]*(?:[-*+]|\d+[.)])[ \t]*\Z")
TABLE_ROW = re.compile(r"[ \t]*\|")
NUMBERED_ITEM = re.compile(r"[ \t]*\d+[.)][ \t]+")
CELL_PADDING = " \t"
QUOTE_OPENERS = (":", "\"", "“")


@dataclass(frozen=True)
class RunTable:
    word_runs: dict
    letter_runs: dict
    shingle_blocks: dict
    block_sizes: tuple
    block_labels: tuple


@dataclass(frozen=True)
class KnownText:
    texts: tuple = ()

    @cached_property
    def readings(self) -> tuple[str, str]:
        word_lines = [" ".join(words_in(text)) for text in (*self.texts, *without_line_numbers(self.texts))]
        return f" {TEXT_SEPARATOR.join(word_lines)} ", TEXT_SEPARATOR.join(line.replace(" ", "") for line in word_lines)

    def has_words(self, words: tuple) -> bool:
        return bool(self.texts) and f" {' '.join(words)} " in self.readings[0]

    def has_letters(self, letters: str) -> bool:
        return bool(self.texts) and letters in self.readings[1]


def without_line_numbers(texts: tuple) -> list[str]:
    stripped = [LINE_NUMBER.sub("", text) for text in texts]
    return [bare for bare, text in zip(stripped, texts) if bare != text]


NOTHING_KNOWN = KnownText()


@dataclass(frozen=True)
class HiddenText:
    tables: tuple = ()
    known: KnownText = NOTHING_KNOWN

    def __bool__(self) -> bool:
        return any(table.word_runs or table.letter_runs for table in self.tables)


NOTHING_HIDDEN = HiddenText()


@dataclass
class Normalised:
    letters: list = field(default_factory=list)
    offsets: list = field(default_factory=list)
    word_starts: list = field(default_factory=list)
    in_word: bool = False

    def read(self, text: str, start: int = 0):
        for index in range(start, len(text)):
            for part in unicodedata.normalize("NFKD", text[index]).casefold():
                self.take(part, index)

    def take(self, part: str, index: int):
        if unicodedata.category(part) in UNSEEN_CATEGORIES or part in JOINING_MARKS:
            return
        if not part.isalnum():
            self.in_word = False
            return
        if not self.in_word:
            self.word_starts.append(len(self.letters))
        self.letters.append(part)
        self.offsets.append(index)
        self.in_word = True

    def word(self, number: int) -> str:
        end = self.word_starts[number + 1] if number + 1 < len(self.word_starts) else len(self.letters)
        return "".join(self.letters[self.word_starts[number]:end])

    def span_of_words(self, first: int, last: int) -> tuple[int, int]:
        end = self.word_starts[last + 1] if last + 1 < len(self.word_starts) else len(self.letters)
        return self.offsets[self.word_starts[first]], self.offsets[end - 1] + 1

    def span_of_letters(self, first: int, last: int) -> tuple[int, int]:
        return self.offsets[first], self.offsets[last] + 1


def words_in(text: str) -> list[str]:
    reading = Normalised()
    reading.read(text)
    return [reading.word(number) for number in range(len(reading.word_starts))]


def blocks_of(text: str) -> list[str]:
    paragraphs = [part for part in PARAGRAPH_BREAK.split(text) if part.strip()]
    lines = [line for paragraph in paragraphs for line in paragraph.splitlines()
             if line.strip() != paragraph.strip()]
    return [block for block in paragraphs + lines if len(WORD.findall(block)) >= SHARED_BLOCK_WORDS]


@lru_cache(maxsize=CACHED_TABLES)
def run_table(labelled_texts: tuple[tuple[str, str], ...]) -> RunTable:
    word_runs, letter_runs, shingle_blocks, sizes, labels = {}, {}, {}, [], []
    for label, text in labelled_texts:
        words = words_in(text)
        for start in range(len(words) - RUN_WORDS + 1):
            word_runs.setdefault(tuple(words[start:start + RUN_WORDS]), label)
        letters = "".join(words)
        for start in range(len(letters) - RUN_LETTERS + 1):
            letter_runs.setdefault(letters[start:start + RUN_LETTERS], label)
        for block in blocks_of(text):
            block_words = words_in(block)
            shingles = {tuple(block_words[start:start + SHINGLE_WORDS])
                        for start in range(len(block_words) - SHINGLE_WORDS + 1)}
            for shingle in shingles:
                shingle_blocks.setdefault(shingle, []).append(len(sizes))
            sizes.append(len(shingles))
            labels.append(label)
    return RunTable(word_runs, letter_runs, shingle_blocks, tuple(sizes), tuple(labels))


def hidden_text(*groups: tuple[tuple[str, str], ...], known: KnownText = NOTHING_KNOWN) -> HiddenText:
    return HiddenText(tuple(run_table(group) for group in groups if group), known)


def run_label(run: tuple, hidden: HiddenText) -> str | None:
    for variant in (run, tuple(word.translate(ROT13) for word in run), tuple(word[::-1] for word in run)):
        label = next((table.word_runs[variant] for table in hidden.tables if variant in table.word_runs), None)
        if label and not hidden.known.has_words(variant):
            return label
    return None


def letters_label(letters: str, hidden: HiddenText) -> str | None:
    for variant in (letters, letters.translate(ROT13), letters[::-1]):
        label = next((table.letter_runs[variant] for table in hidden.tables if variant in table.letter_runs), None)
        if label and not hidden.known.has_letters(variant):
            return label
    return None


@dataclass
class Watch:
    hidden: HiddenText
    text: str = ""
    reading: Normalised = field(default_factory=Normalised)
    words: list = field(default_factory=list)
    words_checked: int = 0
    letters_checked: int = 0
    block_hits: dict = field(default_factory=dict)
    flagged_blocks: set = field(default_factory=set)
    found: list = field(default_factory=list)
    blob_verdicts: dict = field(default_factory=dict)
    blob_watches: dict = field(default_factory=dict)

    def read(self, text: str) -> "Watch":
        if not text.startswith(self.text):
            self.restart()
        start, self.text = len(self.text), text
        if self.hidden:
            self.reading.read(text, start)
            self.check_letters()
            self.check_words(complete=len(self.reading.word_starts) - (1 if self.reading.in_word else 0))
        return self

    def restart(self):
        self.text, self.reading, self.words, self.words_checked, self.letters_checked = "", Normalised(), [], 0, 0
        self.block_hits, self.flagged_blocks, self.found, self.blob_verdicts, self.blob_watches = {}, set(), [], {}, {}

    def finish(self) -> "Watch":
        if self.hidden:
            self.check_words(complete=len(self.reading.word_starts))
        return self

    def check_letters(self):
        letters = self.reading.letters
        last_start = len(letters) - RUN_LETTERS
        for start in range(self.letters_checked, last_start + 1):
            if label := letters_label("".join(letters[start:start + RUN_LETTERS]), self.hidden):
                self.found.append((*self.reading.span_of_letters(start, start + RUN_LETTERS - 1), label))
        self.letters_checked = max(self.letters_checked, last_start + 1)

    def check_words(self, complete: int):
        self.words.extend(self.reading.word(number) for number in range(len(self.words), complete))
        for end in range(max(self.words_checked, 1), complete + 1):
            self.check_run_ending(end)
            self.check_shingle_ending(end)
        self.words_checked = max(self.words_checked, complete + 1)

    def check_run_ending(self, end: int):
        if end < RUN_WORDS:
            return
        if label := run_label(tuple(self.words[end - RUN_WORDS:end]), self.hidden):
            self.found.append((*self.reading.span_of_words(end - RUN_WORDS, end - 1), label))

    def check_shingle_ending(self, end: int):
        if end < SHINGLE_WORDS:
            return
        shingle = tuple(self.words[end - SHINGLE_WORDS:end])
        span = self.reading.span_of_words(end - SHINGLE_WORDS, end - 1)
        for number, table in enumerate(self.hidden.tables):
            for block in table.shingle_blocks.get(shingle, ()):
                self.count_shared_shingle((number, block), table, shingle, span)

    def count_shared_shingle(self, key: tuple[int, int], table: RunTable, shingle: tuple, span: tuple[int, int]):
        label = table.block_labels[key[1]]
        hits = self.block_hits.setdefault(key, {})
        hits.setdefault(shingle, span)
        if key in self.flagged_blocks:
            if not self.hidden.known.has_words(shingle):
                self.found.append((*span, label))
        elif len(hits) >= SHARED_PART_OF_A_BLOCK * table.block_sizes[key[1]]:
            self.flagged_blocks.add(key)
            self.found.extend((*hit, label) for seen, hit in hits.items() if not self.hidden.known.has_words(seen))

    def repeats(self, text: str) -> bool:
        return bool(self.read(text).found) or self.finished_blob_repeats(text)

    def finished_blob_repeats(self, text: str) -> bool:
        for blob in encoded_blobs(text):
            if is_still_being_written(blob, text):
                continue
            key = (blob.start(), blob.end())
            if key not in self.blob_verdicts:
                self.blob_verdicts[key] = self.blob_repeats(blob)
            if self.blob_verdicts[key]:
                return True
        return False

    def blob_repeats(self, blob: re.Match) -> bool:
        for number, reading in enumerate(blob_readings(blob.group())):
            if not (plain := decoded(reading)):
                continue
            watch = self.blob_watches.setdefault((blob.re.pattern, blob.start(), number), Watch(self.hidden))
            if watch.read(plain).finds_when_finished():
                return True
        return False

    def finds_when_finished(self) -> bool:
        if self.found:
            return True
        if not (self.hidden and self.reading.in_word):
            return False
        last = len(self.reading.word_starts) - 1
        words = (*self.words[max(0, last + 1 - RUN_WORDS):last], self.reading.word(last))
        if len(words) == RUN_WORDS and run_label(words, self.hidden):
            return True
        return len(words) >= SHINGLE_WORDS and self.shingle_would_flag(words[-SHINGLE_WORDS:])

    def shingle_would_flag(self, shingle: tuple) -> bool:
        for number, table in enumerate(self.hidden.tables):
            for block in table.shingle_blocks.get(shingle, ()):
                key, hits = (number, block), self.block_hits.get((number, block), {})
                if key in self.flagged_blocks:
                    if not self.hidden.known.has_words(shingle):
                        return True
                elif len(hits) + (shingle not in hits) >= SHARED_PART_OF_A_BLOCK * table.block_sizes[block]:
                    if any(not self.hidden.known.has_words(seen) for seen in (*hits, shingle)):
                        return True
        return False


def blob_readings(blob: str) -> list[str]:
    lines = [line for line in blob.splitlines() if line.strip()]
    return [blob, *(["\n".join(lines[1:]), "\n".join(lines[:-1])] if len(lines) > 1 else [])]


def decoded_readings(blob: str) -> list[str]:
    return [text for reading in blob_readings(blob) if (text := decoded(reading))]


def decoded(blob: str) -> str:
    compact = re.sub(r"[\s:,]", "", blob)
    hex_start = len(HEX_PREFIX) if compact[:len(HEX_PREFIX)].lower() == HEX_PREFIX else 0
    attempts = []
    if (hex_part := HEX_ONLY.match(compact, hex_start)) and hex_part.end() - hex_start >= ENCODED_MIN_CHARS:
        attempts.append(lambda: binascii.unhexlify(hex_part.group()))
    if len(pieces := [piece for piece in compact.split("=") if piece]) > 1:
        attempts.append(lambda: b"".join(base64_bytes(piece) for piece in pieces))
    attempts.append(lambda: base64_bytes(compact.split("=", 1)[0]))
    for attempt in attempts:
        try:
            text = attempt().decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
        if text and sum(char.isprintable() or char.isspace() for char in text) >= DECODED_PRINTABLE_SHARE * len(text):
            return text
    return ""


def base64_bytes(encoded: str) -> bytes:
    usable = encoded[:-1] if len(encoded) % 4 == 1 else encoded
    url_safe = "-" in usable or "_" in usable
    return base64.b64decode(usable + "=" * (-len(usable) % 4), altchars=b"-_" if url_safe else None)


def encoded_blobs(text: str) -> list[re.Match]:
    return [*ENCODED_RUN.finditer(text), *SPACED_HEX.finditer(text)]


def is_still_being_written(blob: re.Match, text: str) -> bool:
    return not text[blob.end():].strip(STILL_BEING_WRITTEN)


def ends_in_an_encoded_blob(text: str) -> bool:
    return any(is_still_being_written(blob, text) for blob in encoded_blobs(text))


def blob_leak(blob: str, hidden: HiddenText) -> str | None:
    for plain in decoded_readings(blob):
        if inside := Watch(hidden).read(plain).finish().found:
            return inside[0][2]
    return None


def decoded_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    if not hidden:
        return []
    return [(blob.start(), blob.end(), label) for blob in encoded_blobs(text) if (label := blob_leak(blob.group(), hidden))]


def leaked_spans(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    return Watch(hidden).read(text).finish().found + decoded_leaks(text, hidden)


def repeats_hidden_text(text: str, hidden: HiddenText) -> bool:
    return bool(text) and bool(hidden) and bool(leaked_spans(text, hidden))


@dataclass(frozen=True)
class Layout:
    text: str
    fences: tuple
    break_starts: tuple
    break_ends: tuple

    @classmethod
    def of(cls, text: str) -> "Layout":
        breaks = [(found.start(), found.end()) for found in SENTENCE_BREAK.finditer(text)]
        return cls(text, tuple((fence.start(), fence.end()) for fence in FENCED_BLOCK.finditer(text)),
                   tuple(start for start, _ in breaks), tuple(end for _, end in breaks))

    def without_short_edges(self, start: int, end: int) -> tuple[int, int]:
        first = bisect.bisect_right(self.break_starts, start)
        last = bisect.bisect_right(self.break_ends, end) - 1
        if first > last:
            return start, end
        trimmed_start, trimmed_end = start, end
        if len(WORD.findall(self.text[start:self.break_starts[first]])) <= EDGE_WORDS_LEFT_ALONE:
            trimmed_start = self.break_ends[first]
        if len(WORD.findall(self.text[self.break_ends[last]:end])) <= EDGE_WORDS_LEFT_ALONE:
            trimmed_end = self.break_starts[last]
        return (trimmed_start, trimmed_end) if trimmed_start < trimmed_end else (start, end)

    def widened(self, start: int, end: int) -> tuple[int, int]:
        for fence_start, fence_end in self.fences:
            if fence_start < end and start < fence_end:
                start, end = min(start, fence_start), max(end, fence_end)
        before = bisect.bisect_right(self.break_ends, start)
        after = bisect.bisect_left(self.break_starts, end)
        return (self.break_ends[before - 1] if before else 0,
                self.break_starts[after] if after < len(self.break_starts) else len(self.text))

    def starts_a_sentence(self, start: int) -> bool:
        index = bisect.bisect_left(self.break_ends, start)
        return start == 0 or (index < len(self.break_ends) and self.break_ends[index] == start)

    def line_start(self, position: int) -> int:
        return self.text.rfind("\n", 0, position) + 1

    def is_in_a_table_row(self, start: int) -> bool:
        return bool(TABLE_ROW.match(self.text, self.line_start(start)))

    def is_a_numbered_item(self, start: int) -> bool:
        return bool(NUMBERED_ITEM.match(self.text, self.line_start(start)))

    def line_end(self, position: int) -> int:
        end = self.text.find("\n", position)
        return len(self.text) if end < 0 else end

    def within_its_line_frame(self, found: tuple[int, int], wide: tuple[int, int]) -> tuple[int, int]:
        start, end = wide
        line_start, line_end = self.line_start(found[0]), self.line_end(found[1])
        if start < line_start or end > line_end:
            return wide
        if self.is_in_a_table_row(found[0]):
            return self.within_cells(found, start, end, line_start, line_end)
        if (marker := NUMBERED_ITEM.match(self.text, line_start)) and marker.end() <= found[0]:
            start = max(start, marker.end())
        return start, end

    def within_cells(self, found: tuple[int, int], start: int, end: int, line_start: int, line_end: int) -> tuple[int, int]:
        bar_before = self.text.rfind("|", line_start, found[0])
        bar_after = self.text.find("|", found[1], line_end)
        if bar_before >= 0:
            start = max(start, bar_before + 1)
            while start < found[0] and self.text[start] in CELL_PADDING:
                start += 1
        if bar_after >= 0:
            end = min(end, bar_after)
            while end > found[1] and self.text[end - 1] in CELL_PADDING:
                end -= 1
        return start, end

    def is_one_sentence(self, start: int, end: int) -> bool:
        first_break = bisect.bisect_left(self.break_starts, start)
        return not any(start < fence_end and fence_start < end for fence_start, fence_end in self.fences) \
            and (first_break == len(self.break_starts) or self.break_starts[first_break] >= end)


def overlapping_joined(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    joined = []
    for start, end in sorted(spans):
        if joined and start <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((start, end))
    return joined


def merged(spans: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    joined = []
    for start, end in sorted(spans):
        if joined and len(WORD.findall(text[joined[-1][1]:start])) <= MERGE_GAP_WORDS:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((start, end))
    return joined


def words_outside(text: str, parts: list[tuple[int, int]]) -> int:
    edges = [0, *(edge for part in parts for edge in part), len(text)]
    return sum(len(WORD.findall(text[edges[index]:edges[index + 1]])) for index in range(0, len(edges), 2))


def introduces_a_quote(text: str, start: int, first_found: int) -> bool:
    lead_in = text[start:first_found]
    return text[:start].rstrip().endswith(":") or any(mark in lead_in for mark in QUOTE_OPENERS)


def quietly_cut(text: str, start: int, end: int, keep: int = 0) -> str:
    before, after = text[:start], text[end:].lstrip(" \t")
    if not after or after.startswith("\n"):
        before = EMPTY_LIST_ITEM.sub("", before).rstrip(" \t")
    if before.endswith("\n") and after.startswith("\n"):
        newlines = max(len(before) - len(before.rstrip("\n")), len(after) - len(after.lstrip("\n")))
        before, after = before.rstrip("\n") + "\n" * newlines, after.lstrip("\n")
    tidied = before + after
    return tidied if tidied.startswith(text[:keep]) else text[:start] + text[end:]


def without_hidden_text(text: str, hidden: HiddenText, keep: int = 0) -> str:
    if not text or not hidden:
        return text
    for checks_left in range(CHECKS_AFTER_A_QUIET_CUT, -1, -1):
        text, cut_quietly = with_hidden_parts_left_out(text, hidden, keep, may_cut_quietly=checks_left > 0)
        if not cut_quietly:
            break
    return text


def with_hidden_parts_left_out(text: str, hidden: HiddenText, keep: int, may_cut_quietly: bool) -> tuple[str, bool]:
    plain = [(start, end, label) for start, end, label in Watch(hidden).read(text).finish().found if end > keep]
    blobs = [(start, end, label) for start, end, label in decoded_leaks(text, hidden) if end > keep]
    if not plain and not blobs:
        return text, False
    layout = Layout.of(text)
    spans = [layout.without_short_edges(start, end) for start, end, _ in plain] + [(start, end) for start, end, _ in blobs]
    widened_spans = [layout.within_its_line_frame(span, layout.widened(*span)) for span in overlapping_joined(spans)]
    parts = merged([(max(start, keep), max(end, keep)) for start, end in widened_spans], text)
    found_starts = sorted(max(start, keep) for start, _ in spans)
    quiet = may_cut_quietly and not blobs and words_outside(text, parts) >= WORDS_LEFT_FOR_A_QUIET_CUT
    log.info("held back %d part(s) of a reply that repeated private text (%s)", len(parts),
             ", ".join(sorted({label for _, _, label in plain + blobs})))
    cut_quietly = False
    for start, end in reversed(parts):
        if quiet and can_cut_quietly(layout, start, end, found_starts):
            text, cut_quietly = quietly_cut(text, start, end, keep), True
        else:
            text = text[:start] + DECLINE + text[end:]
    return text, cut_quietly


def can_cut_quietly(layout: Layout, start: int, end: int, found_starts: list[int]) -> bool:
    first_found = next((found for found in found_starts if start <= found < end), start)
    return layout.starts_a_sentence(start) and layout.is_one_sentence(start, end) and not layout.is_in_a_table_row(start) \
        and not layout.is_a_numbered_item(start) and not introduces_a_quote(layout.text, start, first_found)


class GuardedStream:
    def __init__(self, on_text, hidden: HiddenText):
        self.on_text = on_text
        self.watch = Watch(hidden)
        self.sent = ""
        self.waiting = ""
        self.held = False

    def __call__(self, piece: str):
        if self.held:
            return
        text = self.sent + self.waiting + piece
        if self.watch.repeats(text):
            self.held = True
            log.info("held back the rest of a streamed reply that repeated private text")
            return
        if ends_in_an_encoded_blob(text):
            self.waiting += piece
            return
        ready, self.waiting = self.waiting + piece, ""
        self.sent += ready
        self.on_text(ready)

    def kept(self, answer: str) -> int:
        return len(self.sent) if answer.startswith(self.sent) else 0
