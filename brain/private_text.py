import base64
import binascii
import bisect
import logging
import re
import unicodedata
from collections import Counter
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
ENCODED_GROUP_MIN_CHARS = 12
ENCODED_GROUP = rf"[A-Za-z0-9+/_-]{{{ENCODED_GROUP_MIN_CHARS},}}+={{0,2}}"
ENCODED_LINE_PREFIX = r"(?:>[ \t]?|\d{1,6}(?:[.:|)\t][ \t]?|[ \t]+))"
ENCODED_GAP = r"(?=[ \t\n])(?>[ \t]*(?:\n[ \t]*)*)(?:" + ENCODED_LINE_PREFIX + r"[ \t]*)?"
JOINED_RUN = re.compile(r"(?<![A-Za-z0-9+/_=-])" + ENCODED_GROUP + r"(?:" + ENCODED_GAP + ENCODED_GROUP + r")++")
STILL_JOINING = re.compile(r"(?>[ \t]*(?:\n[ \t]*)*)(?:" + ENCODED_LINE_PREFIX + r"?[ \t]*)?[A-Za-z0-9+/_=-]{0,%d}"
                           % (ENCODED_GROUP_MIN_CHARS - 1))
PREFIXED_LINE = re.compile(r"\n[ \t]*" + ENCODED_LINE_PREFIX)
TOKEN_CHARACTERS = r"A-Za-z0-9+/_\-"
FILLER_TOKEN = rf"[{TOKEN_CHARACTERS}]++={{0,2}}"
ENCODED_TOKEN = (rf"(?<![{TOKEN_CHARACTERS}=])(?=[{TOKEN_CHARACTERS}]*(?:[0-9+/]|[a-z][A-Z]))"
                 rf"[{TOKEN_CHARACTERS}]{{4,}}+={{0,2}}(?![{TOKEN_CHARACTERS}=])")
TOKEN_SEPARATOR = rf"(?:\s++|[^\s{TOKEN_CHARACTERS}=]{{1,4}}+)"
SCATTER_GAP = rf"(?:{TOKEN_SEPARATOR}|{FILLER_TOKEN}){{1,8}}?"
SCATTERED_RUN = re.compile(ENCODED_TOKEN + rf"(?:{SCATTER_GAP}{ENCODED_TOKEN}){{2,}}")
LONE_GROUP = re.compile(rf"(?<![{TOKEN_CHARACTERS}=])(?=[{TOKEN_CHARACTERS}]*(?:[0-9+/]|[a-z][A-Z]))"
                        rf"[{TOKEN_CHARACTERS}]{{{ENCODED_GROUP_MIN_CHARS},}}+={{0,2}}(?![{TOKEN_CHARACTERS}=])")
LONE_GROUP_TAIL_CHARS = 2048
STILL_SCATTERING = re.compile(rf"(?:{TOKEN_SEPARATOR}|{FILLER_TOKEN}){{0,8}}")
TRAILING_GROUPS = re.compile(rf"(?:{TOKEN_SEPARATOR}{FILLER_TOKEN}){{1,2}}")
SENTENCE_ENDED = re.compile(r"(?<!\d)[.!?](?:\s|$)")
TOKEN = re.compile(FILLER_TOKEN)
LOOKS_ENCODED = re.compile(rf"(?=[{TOKEN_CHARACTERS}]*(?:[0-9+/]|[a-z][A-Z]))[{TOKEN_CHARACTERS}]{{4,}}={{0,2}}")
BASE64_OFFSETS = range(4)
READABLE_SAMPLE_CHARS = 4096
READABLE_RUN_LETTER_SHARE = 0.7
READABLE_RUN = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f-\x9f\ufffd]{%d,}" % ENCODED_MIN_CHARS)
OPEN_RUN_RELEASE_CHARS = 1024
CLEAN_CHECK_OVERLAP_CHARS = 1024
CLEAN_CHECK_OVERLAP_TOKENS = 48
CLEAN_CHECK_OVERLAP_MIN_TOKENS = 24
QUIET_RELEASE_CHARS = 256
QUIET_RELEASE_POINT = re.compile(r"[,|\]};\u3002\u3001\uff0c\uff01\uff1f\uff1b\uff1a]|(?<=\S)\r?[\n\t]|(?<![^\s(\[])[+-]?\d[\d.,:/-]* ")
RELEASE_POINT_CONTEXT_CHARS = 32
WHITESPACE_RUN = re.compile(r"\s+")
RELEASE_SHARE = 4
BLOB_TAIL_CHARS = 16000
BLOB_WINDOW_CHARS = 128
ENCODED_RUN_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/_=-")
SPACED_HEX_CHARACTERS = frozenset("0123456789ABCDEFabcdef \t:,")
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
ACROSTIC_LETTERS = 20
DERIVED_RUN_WORDS = 8
MOST_INITIALS_READ = 2000
LEET_WORDS_TO_READ = 3
SHIFT_STRIDE = 20
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
SHIFTS = tuple(shift for shift in range(1, len(ALPHABET)) if shift != len(ALPHABET) // 2)
MORSE = dict(zip(
    ".- -... -.-. -.. . ..-. --. .... .. .--- -.- .-.. -- -. --- .--. --.- .-. ... - ..- ...- .-- -..- -.-- --.. "
    "----- .---- ..--- ...-- ....- ..... -.... --... ---.. ----.".split(), ALPHABET + "0123456789"))
MORSE_RUN = re.compile(r"(?:[.\-·•−–]{1,6}(?:[ \t]++(?:/[ \t]*+)?|/[ \t]*+|\n)){8,}[.\-·•−–]{1,6}")
MORSE_DOTS = str.maketrans("·•−–", "..--")
NATO = {word: word[0] for word in (
    "alfa alpha bravo charlie delta echo foxtrot golf hotel india juliet juliett kilo lima mike november oscar papa "
    "quebec romeo sierra tango uniform victor whiskey whisky xray yankee zulu").split()}
LEET = str.maketrans("4@3105$7", "aaeiosst")
LEET_WORD = re.compile(r"\b(?=\w*[a-zA-Z])(?=\w*[4@3105$7])[\w@$]+")
LINE = re.compile(r"[^\n]+")


@dataclass(frozen=True)
class RunTable:
    word_runs: dict
    letter_runs: dict
    shingle_blocks: dict
    block_sizes: tuple
    block_labels: tuple
    words_text: str = ""
    letters_text: str = ""
    word_ends: tuple = ()
    letter_ends: tuple = ()
    labels: tuple = ()

    def label_at(self, ends: tuple, position: int) -> str:
        return self.labels[min(bisect.bisect_right(ends, position), len(self.labels) - 1)]


def joined_with_ends(lines: list[str], lead: str = "") -> tuple[str, tuple]:
    ends, length = [], len(lead)
    for line in lines:
        length += len(line)
        ends.append(length)
        length += len(TEXT_SEPARATOR)
    return lead + TEXT_SEPARATOR.join(lines) + lead, tuple(ends)


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
    word_lines, letter_lines, text_labels = [], [], []
    for label, text in labelled_texts:
        words = words_in(text)
        word_lines.append(" ".join(words))
        letter_lines.append("".join(words))
        text_labels.append(label)
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
    words_text, word_ends = joined_with_ends(word_lines, " ")
    letters_text, letter_ends = joined_with_ends(letter_lines)
    return RunTable(word_runs, letter_runs, shingle_blocks, tuple(sizes), tuple(labels), words_text, letters_text,
                    word_ends, letter_ends, tuple(text_labels))


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
    finished_plain: dict = field(default_factory=dict)

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
        self.finished_plain = {}

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
        for blob in all_encoded_blobs(text):
            if is_still_being_written(blob, text):
                continue
            key = (blob.start(), blob.end())
            if key not in self.blob_verdicts:
                self.blob_verdicts[key] = self.blob_repeats(blob)
            if self.blob_verdicts[key]:
                return True
        return False

    def blob_repeats(self, blob: re.Match) -> bool:
        for number, plain in enumerate(decoded_readings(blob.group(), blob.re is SCATTERED_RUN)):
            if plain in self.finished_plain:
                if self.finished_plain[plain]:
                    return True
                continue
            watch = self.blob_watches.setdefault((blob.re.pattern, blob.start(), number), Watch(self.hidden))
            self.finished_plain[plain] = watch.read(plain).finds_when_finished()
            if self.finished_plain[plain]:
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


def blob_readings(blob: str, scattered: bool = False) -> list[str]:
    if scattered:
        return scattered_readings(blob)
    lines = [line for line in blob.splitlines() if line.strip()]
    readings = [blob, *(["\n".join(lines[1:]), "\n".join(lines[:-1])] if len(lines) > 1 else [])]
    unprefixed = PREFIXED_LINE.sub("\n", blob)
    return readings + [unprefixed] if unprefixed != blob else readings


def scattered_readings(blob: str) -> list[str]:
    tokens = TOKEN.findall(blob)
    encoded = [token for token in tokens if LOOKS_ENCODED.fullmatch(token)]
    lengths = [len(token) for token in encoded]
    usual = max(set(lengths), key=lambda length: lengths.count(length) * length) if lengths else 0
    grouped = [token for number, token in enumerate(encoded) if len(token) == usual or number == len(encoded) - 1]
    unprefixed = [token[len(HEX_PREFIX):] if token[:len(HEX_PREFIX)].lower() == HEX_PREFIX else token for token in tokens]
    hexed = [token for token in unprefixed if HEX_ONLY.fullmatch(token)]
    return list(dict.fromkeys("".join(reading) for reading in (tokens, encoded, grouped, hexed) if reading))


def decoded_readings(blob: str, scattered: bool = False) -> list[str]:
    return [text for reading in blob_readings(blob, scattered) for text in decodings(reading)]


def decoded(blob: str) -> str:
    return next(iter(decodings(blob)), "")


def is_readable(text: str) -> bool:
    sample = text[:READABLE_SAMPLE_CHARS]
    readable = sum(count for char, count in Counter(sample).items() if char.isprintable() or char.isspace())
    return bool(sample) and readable >= DECODED_PRINTABLE_SHARE * len(sample)


def decodings(blob: str) -> list[str]:
    compact = re.sub(r"[\s:,]", "", blob)
    hex_start = len(HEX_PREFIX) if compact[:len(HEX_PREFIX)].lower() == HEX_PREFIX else 0
    attempts = []
    if (hex_part := HEX_ONLY.match(compact, hex_start)) and hex_part.end() - hex_start >= ENCODED_MIN_CHARS:
        attempts.append(lambda: binascii.unhexlify(hex_part.group()))
    if len(pieces := [piece for piece in compact.split("=") if piece]) > 1:
        attempts.append(lambda: b"".join(base64_bytes(piece) for piece in pieces))
    unpadded = compact.split("=", 1)[0]
    found = [text for attempt in attempts if (text := readable_bytes(attempt))]
    every_offset = [decoded_bytes(lambda offset=offset: base64_bytes(unpadded[offset:])) for offset in BASE64_OFFSETS
                    if len(unpadded) - offset >= ENCODED_MIN_CHARS]
    readable = [is_readable(text) for text in every_offset]
    shares = {index: letter_share(text) for index, text in enumerate(every_offset) if readable[index]}
    best = every_offset[max(shares, key=shares.get)] if shares else ""
    lettered = [every_offset[index] for index, share in shares.items() if share >= READABLE_RUN_LETTER_SHARE]
    runs = [run for text in every_offset for run in readable_runs(text)]
    return list(dict.fromkeys(text for text in (*found, best, *lettered, *runs) if text))


def readable_runs(text: str) -> list[str]:
    return [run.group() for run in READABLE_RUN.finditer(text) if letter_share(run.group()) >= READABLE_RUN_LETTER_SHARE]


def letter_share(text: str) -> float:
    sample = text[:READABLE_SAMPLE_CHARS]
    return sum(count for char, count in Counter(sample).items() if char.isalpha() or char == " ") / max(len(sample), 1)


def decoded_bytes(attempt) -> str:
    try:
        return attempt().decode("utf-8", "ignore")
    except (binascii.Error, ValueError):
        return ""


def readable_bytes(attempt) -> str:
    text = decoded_bytes(attempt)
    return text if is_readable(text) else ""


def base64_bytes(encoded: str) -> bytes:
    usable = encoded[:-1] if len(encoded) % 4 == 1 else encoded
    url_safe = "-" in usable or "_" in usable
    return base64.b64decode(usable + "=" * (-len(usable) % 4), altchars=b"-_" if url_safe else None)


def encoded_blobs(text: str) -> list[re.Match]:
    return [*ENCODED_RUN.finditer(text), *SPACED_HEX.finditer(text)]


def all_encoded_blobs(text: str) -> list[re.Match]:
    return [*encoded_blobs(text), *JOINED_RUN.finditer(text), *SCATTERED_RUN.finditer(text)]


def open_blobs(text: str) -> list[re.Match]:
    still = [blob for blob in all_encoded_blobs(text) if is_still_being_written(blob, text)]
    tail_start = max(0, len(text.rstrip()) - LONE_GROUP_TAIL_CHARS)
    last_group = None
    for last_group in LONE_GROUP.finditer(text, tail_start):
        pass
    if last_group is not None and is_still_being_written(last_group, text):
        still.append(last_group)
    return still


def is_still_being_written(blob: re.Match, text: str) -> bool:
    if blob.re is JOINED_RUN:
        return STILL_JOINING.fullmatch(text, blob.end()) is not None
    if blob.re in (SCATTERED_RUN, LONE_GROUP):
        return STILL_SCATTERING.fullmatch(text, blob.end()) is not None and not SENTENCE_ENDED.search(text, blob.end())
    return not text[blob.end():].strip(STILL_BEING_WRITTEN)


def ends_in_an_encoded_blob(text: str) -> bool:
    return bool(open_blobs(text))


def plain_leak(plain: str, hidden: HiddenText) -> str | None:
    return next(iter(Watch(hidden).read(plain).finish().found), (0, 0, None))[2]


def blob_leak(blob: str, hidden: HiddenText, scattered: bool = False) -> str | None:
    return next((label for plain in decoded_readings(blob, scattered) if (label := plain_leak(plain, hidden))), None)


def decoded_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    if not hidden:
        return []
    leaks, labels = [], {}
    for blob in all_encoded_blobs(text):
        for plain in readings_with_trailing_groups(blob, text):
            if plain not in labels:
                labels[plain] = plain_leak(plain, hidden)
            if labels[plain]:
                leaks.append((blob.start(), blob.end(), labels[plain]))
                break
    return leaks


def readings_with_trailing_groups(blob: re.Match, text: str):
    scattered = blob.re is SCATTERED_RUN
    yield from decoded_readings(blob.group(), scattered)
    if scattered and (trailing := TRAILING_GROUPS.match(text, blob.end())):
        yield from decoded_readings(blob.group() + trailing.group(), scattered)


def shared_pieces(text: str, hidden: HiddenText) -> int:
    words = words_in(text)
    pieces = {tuple(words[start:start + SHINGLE_WORDS]) for start in range(len(words) - SHINGLE_WORDS + 1)}
    return sum(1 for piece in pieces if any(piece in table.shingle_blocks for table in hidden.tables)
               and not hidden.known.has_words(piece))


def letters_found(letters: str, hidden: HiddenText) -> str | None:
    if hidden.known.has_letters(letters):
        return None
    for table in hidden.tables:
        if (position := table.letters_text.find(letters)) >= 0:
            return table.label_at(table.letter_ends, position)
    return None


def words_found(words: tuple, hidden: HiddenText) -> str | None:
    if hidden.known.has_words(words):
        return None
    wanted = f" {' '.join(words)} "
    for table in hidden.tables:
        if (position := table.words_text.find(wanted)) >= 0:
            return table.label_at(table.word_ends, position)
    return None


def first_letter(text: str) -> str:
    return next(iter(words_in(text)), "")[:1]


def letters_of_parts(letters: tuple, hidden: HiddenText) -> str | None:
    return letters_found("".join(letters), hidden)


def initials_leak(parts: list[tuple[int, int, str]], hidden: HiddenText, size: int, find) -> tuple[int, int, str] | None:
    for start in range(len(parts) - size + 1):
        window = parts[start:start + size]
        if label := find(tuple(part for _, _, part in window), hidden):
            return window[0][0], window[-1][1], label
    return None


def acrostic_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    lines = [(found.start(), found.end(), found.group()) for found in LINE.finditer(text) if words_in(found.group())]
    words = [(found.start(), found.end(), found.group()) for found in WORD.finditer(text)][:MOST_INITIALS_READ]
    readings = (
        ([(start, end, first_letter(line)) for start, end, line in lines], ACROSTIC_LETTERS, letters_of_parts),
        ([(start, end, first_letter(word)) for start, end, word in words], ACROSTIC_LETTERS, letters_of_parts),
        ([(start, end, words_in(line)[0]) for start, end, line in lines], DERIVED_RUN_WORDS, words_found),
    )
    return [found for parts, size, find in readings if (found := initials_leak(parts, hidden, size, find))]


def shifted_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    reading = Normalised()
    reading.read(text)
    letters = "".join(reading.letters)
    if len(letters) < RUN_LETTERS + SHIFT_STRIDE:
        return []
    for shift in SHIFTS:
        shifted = letters.translate(str.maketrans(ALPHABET, ALPHABET[shift:] + ALPHABET[:shift]))
        for start in range(0, len(shifted) - RUN_LETTERS + 1, SHIFT_STRIDE):
            window = shifted[start:start + RUN_LETTERS]
            if (label := next((table.letter_runs[window] for table in hidden.tables if window in table.letter_runs), None)) \
                    and not hidden.known.has_letters(window):
                return [(*reading.span_of_letters(0, len(letters) - 1), label)]
    return []


def morse_decoded(code: str) -> str:
    words = re.split(r"[ \t]*/[ \t]*|[ \t]{3,}|\n", code.translate(MORSE_DOTS).strip())
    return " ".join("".join(MORSE.get(sign, "") for sign in word.split()) for word in words)


def spelled_alphabet(text: str) -> list[tuple[int, int, str]]:
    return [(found.start(), found.end(), NATO[found.group().casefold()]) for found in WORD.finditer(text)
            if found.group().casefold() in NATO]


def read_back_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    found = []
    for code in MORSE_RUN.finditer(text):
        if inside := Watch(hidden).read(morse_decoded(code.group())).finish().found:
            found.append((code.start(), code.end(), inside[0][2]))
    letters = spelled_alphabet(text)
    if spelled := initials_leak(letters, hidden, ACROSTIC_LETTERS, letters_of_parts):
        found.append(spelled)
    if len(LEET_WORD.findall(text)) >= LEET_WORDS_TO_READ and (inside := Watch(hidden).read(text.translate(LEET)).finish().found):
        found.append((min(start for start, _, _ in inside), max(end for _, end, _ in inside), inside[0][2]))
    return found


def derived_leaks(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    if not text or not hidden:
        return []
    return acrostic_leaks(text, hidden) + shifted_leaks(text, hidden) + read_back_leaks(text, hidden)


def leaked_spans(text: str, hidden: HiddenText) -> list[tuple[int, int, str]]:
    return Watch(hidden).read(text).finish().found + decoded_leaks(text, hidden) + derived_leaks(text, hidden)


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
    blobs = [(start, end, label) for start, end, label in decoded_leaks(text, hidden) + derived_leaks(text, hidden)
             if end > keep]
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


def last_gap(text: str, start: int) -> int:
    end = max(text.rfind(space, start) for space in " \t\n\r")
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def has_release_point(text: str, start: int) -> bool:
    return any(point.end() > start for point in QUIET_RELEASE_POINT.finditer(text, max(0, start - RELEASE_POINT_CONTEXT_CHARS)))


def last_mark(text: str, start: int) -> int:
    last = 0
    for point in QUIET_RELEASE_POINT.finditer(text, start):
        last = point.end()
    return last


def overlap_tokens_kept(starts: list[int], length: int) -> int:
    kept = min(len(starts), CLEAN_CHECK_OVERLAP_TOKENS)
    while kept > CLEAN_CHECK_OVERLAP_MIN_TOKENS and length - starts[-kept] > CLEAN_CHECK_OVERLAP_CHARS:
        kept -= 1
    return kept


def collapsed(text: str) -> str:
    return WHITESPACE_RUN.sub(lambda run: "\n" if "\n" in run.group() else " ", text)


@dataclass
class OpenRun:
    hidden: HiddenText
    start: int
    checked_to: int = 0
    leaked: bool = False

    def unchecked_start(self) -> int:
        return max(self.start, self.checked_to - CLEAN_CHECK_OVERLAP_CHARS)

    def release_point(self, text: str, checked: int) -> int:
        if len(text) - self.start >= OPEN_RUN_RELEASE_CHARS and (gap := last_gap(text, checked)) > checked:
            return gap
        if len(text) - checked >= QUIET_RELEASE_CHARS:
            return last_mark(text, checked)
        return 0

    def clean_end(self, text: str, sent_length: int, sent_tail: str) -> int:
        checked = max(self.start, self.checked_to)
        if (gap := self.release_point(text, checked)) <= max(checked, sent_length):
            return 0
        if decoded_leaks(sent_tail + collapsed(text[sent_length:gap]), self.hidden):
            self.leaked = True
            return 0
        self.checked_to = gap
        return gap


class StreamGuard:
    def __init__(self, hidden: HiddenText):
        self.hidden = hidden
        self.watch = Watch(hidden)
        self.sent = ""
        self.sent_tail = ""
        self.sent_tail_tokens = []
        self.marks_held = False
        self.recent = ""
        self.held = []
        self.held_length = 0
        self.checked_length = 0
        self.cut = False
        self.open_run = None
        self.blob_verdicts = {}

    def read(self, piece: str) -> str:
        if self.cut:
            return ""
        self.held.append(piece)
        self.held_length += len(piece)
        self.marks_held = self.marks_held or has_release_point(self.recent + piece, len(self.recent))
        self.recent = (self.recent + piece)[-RELEASE_POINT_CONTEXT_CHARS:]
        if self.waits_for_more() and not SENTENCE_ENDED.search(piece):
            return ""
        return self.release()

    def waits_for_more(self) -> bool:
        if self.open_run is None:
            return False
        run_length = len(self.sent) + self.held_length - self.open_run.start
        batch = max(min(run_length // RELEASE_SHARE, QUIET_RELEASE_CHARS if self.marks_held else OPEN_RUN_RELEASE_CHARS),
                    self.held_length // RELEASE_SHARE)
        return run_length >= OPEN_RUN_RELEASE_CHARS and self.held_length - self.checked_length < batch

    def release(self, finished: bool = False) -> str:
        if self.cut or not self.held:
            return ""
        text = self.sent + "".join(self.held)
        tail_start = self.open_run.unchecked_start() if self.open_run is not None else self.scan_start(text)
        tail = text[tail_start:]
        if self.watch.read(text).found or self.finished_blob_repeats(tail, tail_start, finished) \
                or (finished and self.watch.finish().found):
            return self.cut_off()
        still_written = min(open_blobs(tail), key=re.Match.start, default=None)
        self.follow_open_run(tail_start + still_written.start() if still_written and not finished else None)
        if self.open_run is not None and (clean_end := self.open_run.clean_end(text, len(self.sent), self.sent_tail)):
            return self.release_up_to(text, clean_end)
        if self.open_run is not None and self.open_run.leaked:
            return self.cut_off()
        if not finished and (still_written or self.watch.finds_when_finished()):
            self.checked_length = self.held_length
            return ""
        ready = "".join(self.held)
        self.remember_sent(ready)
        self.sent, self.held, self.held_length, self.checked_length = text, [], 0, 0
        self.marks_held = False
        return ready

    def remember_sent(self, ready: str):
        added = collapsed(ready)
        if added[:1].isspace() and self.sent_tail[-1:].isspace():
            joined = "\n" if "\n" in self.sent_tail[-1] + added[0] else " "
            self.sent_tail, added = self.sent_tail[:-1] + joined, added[1:]
        continues_a_token = bool(self.sent_tail) and TOKEN.fullmatch(self.sent_tail[-1:] + added[:1]) is not None
        offset = len(self.sent_tail)
        self.sent_tail_tokens.extend(offset + token.start() for token in TOKEN.finditer(added)
                                     if not (continues_a_token and token.start() == 0))
        self.sent_tail += added
        kept = overlap_tokens_kept(self.sent_tail_tokens, len(self.sent_tail))
        if kept < len(self.sent_tail_tokens):
            cut = self.sent_tail_tokens[-kept]
            self.sent_tail = self.sent_tail[cut:]
            self.sent_tail_tokens = [start - cut for start in self.sent_tail_tokens[-kept:]]

    def follow_open_run(self, start: int | None):
        if start is None:
            self.open_run = None
        elif self.open_run is None or start < self.open_run.start:
            self.open_run = OpenRun(self.hidden, start)

    def release_up_to(self, text: str, end: int) -> str:
        ready = text[len(self.sent):end]
        self.remember_sent(ready)
        self.sent, self.held = text[:end], [text[end:]]
        self.held_length = self.checked_length = len(text) - end
        self.marks_held = has_release_point(text, end)
        return ready

    def scan_start(self, text: str) -> int:
        lowest = max(0, len(text) - BLOB_TAIL_CHARS)
        start = max(lowest, len(self.sent) - BLOB_WINDOW_CHARS)
        for characters in (ENCODED_RUN_CHARACTERS, SPACED_HEX_CHARACTERS):
            while start > lowest and text[start - 1] in characters:
                start -= 1
        return start

    def finished_blob_repeats(self, tail: str, tail_start: int, finished: bool = False) -> bool:
        for blob in all_encoded_blobs(tail):
            if not finished and is_still_being_written(blob, tail):
                continue
            key = (tail_start + blob.start(), tail_start + blob.end())
            if key not in self.blob_verdicts:
                self.blob_verdicts[key] = self.watch.blob_repeats(blob)
            if self.blob_verdicts[key]:
                return True
        return False

    def cut_off(self) -> str:
        self.cut, self.held, self.held_length = True, [], 0
        return ""


class GuardedStream:
    def __init__(self, on_text, hidden: HiddenText):
        self.on_text = on_text
        self.guard = StreamGuard(hidden)

    @property
    def held(self) -> bool:
        return self.guard.cut

    def __call__(self, piece: str):
        if self.guard.cut:
            return
        if ready := self.guard.read(piece):
            self.on_text(ready)
        if self.guard.cut:
            log.info("held back the rest of a streamed reply that repeated private text")

    def kept(self, answer: str) -> int:
        sent = self.guard.sent
        return len(sent) if answer.startswith(sent) else 0
