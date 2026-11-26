import difflib
import hashlib
import re
from dataclasses import dataclass

import room_notes
import room_voice

CHAT, COUNCIL, PLAN, WORK, REVIEW = "chat", "council", "plan", "work", "review"
TASK_KINDS = frozenset({PLAN, WORK, REVIEW})
FORMAT_KINDS = frozenset({COUNCIL, PLAN, WORK, REVIEW})
ROOM_SKILLS = frozenset({"planning", "coding", "writing", "research", "review", "design", "chat"})
LONG_DASHES = "".join(map(chr, (0x2012, 0x2013, 0x2014, 0x2015, 0x2E3A, 0x2E3B, 0xFE31, 0xFE58)))
DASH = f"[{LONG_DASHES}]"
DASH_CHARACTER = re.compile(DASH)
DASH_AFTER_ACTION = re.compile(rf"\][ \t]*{DASH}+[ \t]*")
DASH_IN_RANGE = re.compile(rf"(?<=\d)[ \t]*{DASH}[ \t]*(?=\d)")
DASH_AS_MINUS = re.compile(rf"(?<![\w)\]]){DASH}(?=\d)")
DASH_STARTING_A_LINE = re.compile(rf"(?m)^([ \t]*){DASH}+[ \t]*")
DASH_BETWEEN_WORDS = re.compile(rf"[ \t]*{DASH}+[ \t]*")
KEPT_AS_WRITTEN = re.compile(r"```.*?(?:```|\Z)|\[file\b[^\]]*\].*?(?:\[/file\]|\Z)|https?://\S+", re.DOTALL | re.IGNORECASE)
ENDS_A_CLAUSE = ".,!?;:)\n"
COMMA = ", "
NEW_LINE = "\n"
PLAIN_DASH = "-"
ACTION_SEPARATOR = "] :: "

NAME = r"[^\n:()]{1,80}"
NUDGES = (
    (PLAN, re.compile(rf"\(Your job now, (?P<me>{NAME}): write the plan as \[task\] lines\.\)")),
    (WORK, re.compile(rf"\(Your task now, (?P<me>{NAME}): hand in the work as described\.\)")),
    (REVIEW, re.compile(rf"\(Your review now, (?P<me>{NAME}): start with \[approve\] or \[changes\]\.\)")),
    (CHAT, re.compile(rf"\(It's your turn, (?P<me>{NAME})\. Write your next message only, or PASS\.\)")),
)
APP_BLOCK = re.compile(
    r"(?:\A|\n\n)\(room\) (?P<guidelines>(?:(?!\n\n\(room\) ).)*?)\n\nRoom topic: (?P<topic>[^\n]{0,200})\n"
    r"Other AIs here: (?P<others>[^\n]*)\.\nMessages from people \(not AIs\) are marked \[person\]\.\n\n(?P<nudge>\([^\n]{1,200}\))\Z",
    re.DOTALL)
COUNCIL_TOPIC = re.compile(r"City council, day \d{1,6}")
COUNCIL_GUIDE = "You are {name}, one of the councillors who build and run a city together."
TASK_LINE = re.compile(r"^YOUR TASK \(#\d{1,4}, ([a-z ]{1,20})\): ", re.MULTILINE)
PROJECT_MESSAGE = re.compile(r"(?:\A|\n\n)\(room\) Project: ")
TASK_SECTION = re.compile(r"\n\nAll tasks:\n(?:\d{1,4}\. \[[a-z ]{1,20}\] [^\n]*\n)+\n(?P<task>YOUR TASK \(#\d{1,4}, [a-z ]{1,20}\): )")
FILES_HEADING = "\n\nProject files so far:\n"
ABOUT_PART = re.compile(r"\s*\([^()]*\)")
NOBODY = "nobody yet"
HUB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}")
CITY_NAME = re.compile(r"(?:\A|\n\n)\(room\) (?:CITY: |You are [^\n]{1,80}? on the council of )([^,\n]{1,80}), day \d{1,6}\.")
SCOPE_DIGEST_CHARS = room_notes.SCOPE_DIGEST_CHARS
COUNCIL_SCOPE = room_notes.COUNCIL_SCOPE
TRYOUT_SKILL = re.compile(r"This is challenge \d{1,3} of \d{1,3} \(skill: ([a-z]{1,20})\)\.")
APP_MARKERS = re.compile(r"\((?:It's your turn|Your task now|Your job now|Your review now), |Arty's own note|"
                         r"private room scratchpad", re.IGNORECASE)
UNMISTAKABLE_HEADING_CHARS = 40
SCRATCHPAD_HEADINGS = re.compile(
    r"^(?:[^\n:()]{1,60}: )?(?:" + "|".join(map(re.escape, room_notes.HEADINGS)) + ")|"
    + "|".join(re.escape(heading) for heading in room_notes.HEADINGS if len(heading) >= UNMISTAKABLE_HEADING_CHARS),
    re.MULTILINE | re.IGNORECASE)
WORD = re.compile(r"[a-z0-9']+")
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{1,30}\b", re.MULTILINE)
YES_OR_NO = re.compile(r"\b(yes|no|approve|changes|agree|disagree)\b", re.IGNORECASE)
SAME_MESSAGE_RATIO = 0.85
MIN_WORDS_TO_COMPARE = 4
PASS = "PASS"
TIMEOUT_APOLOGY = re.compile(r"(?:(?:sorry|apologies)[,.!]?\s*)?(?:(?:my|the|this|your)\s+)?(?:request|reply|answer|response|"
                             r"call|connection|turn)\s+(?:has\s+|was\s+)?(?:timed out|time ?out)[.!]?"
                             r"(?:\s*,?\s*(?:please\s+)?try again(?:\s+later)?[.!]?)?", re.IGNORECASE)
SHORT_REPLY_CHARS = 240
ASKED_FILE = re.compile(r"\[file path=([\w./-]{1,120})\]")
FENCED_BLOCK = re.compile(r"```[\w+-]*\n(.*?)\n?```", re.DOTALL)
FILE_BLOCK_START = "[file"
OPENED_HAND_IN = {"file": re.compile(r"\[file\b[^\]\n]*\]", re.IGNORECASE), "doc": re.compile(r"\[doc\]", re.IGNORECASE)}
CLOSED_HAND_IN = {"file": re.compile(r"\[/file\]", re.IGNORECASE), "doc": re.compile(r"\[/doc\]", re.IGNORECASE)}
FENCE = "```"
CLOSABLE_TAGS = "answer|doc|file|idea|name|score|search|task"
OPENING_TAG = re.compile(rf"\[({CLOSABLE_TAGS})\b[^\]\n]*\]", re.IGNORECASE)
CLOSING_TAG = re.compile(rf"\[/({CLOSABLE_TAGS})\]", re.IGNORECASE)
BRACKET_LINE = re.compile(r"^[ \t]*\[", re.MULTILINE)
LINE_OR_BLOCK_END = re.compile(r"\[/[a-z]{1,20}\]|\n", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?](?=[\s\"')\]]|\Z)")
WORD_BEFORE = re.compile(r"([A-Za-z.]+)\Z")
ABBREVIATIONS = frozenset({"approx", "etc", "vs", "cf", "ca", "mr", "mrs", "ms", "dr", "prof", "fig", "st", "inc", "ltd", "co",
                           "ph.d"})
NUMBER_SIGN = "no"
NEXT_DIGIT = re.compile(r"\s*\d")
FEWEST_WORDS_KEPT = 3
SHORT_FORM_PART = re.compile(r"[a-z]")
LEADING_TAG = re.compile(r"\A\s*(\[[a-z]{1,20}\])(?=\s|\Z)", re.IGNORECASE)
VERDICT_KEPT_ALONE = "[approve]"
ENDS_A_SENTENCE = re.compile(r"[.!?][\"')\]]*\Z")
NEXT_INITIAL = re.compile(r"\s+[A-Z]\.")
PREVIOUS_INITIAL = re.compile(r"\b[A-Z]\.\s+\Z")
LINE_TAGS = frozenset({"task", "score", "search", "idea"})
FULL_STOP = "."


@dataclass(frozen=True)
class AppTurn:
    kind: str = CHAT
    topic: str = ""
    skill: str = ""
    me: str = ""
    from_app: bool = False
    names: tuple = ()
    city: str = ""
    asked: tuple = ()

    @property
    def legacy_scope(self) -> str:
        if self.kind == COUNCIL:
            return COUNCIL_SCOPE
        topic = self.topic.strip().lower()
        return "" if topic.startswith(COUNCIL_SCOPE) else topic

    @property
    def scope(self) -> str:
        if self.kind == COUNCIL:
            return f"{COUNCIL_SCOPE}: {self.city}" if self.city else COUNCIL_SCOPE
        subject = self.topic.strip().lower()
        if not subject:
            return ""
        where = hashlib.sha256("\n".join(self.names).encode()).hexdigest()[:SCOPE_DIGEST_CHARS]
        return f"{where} {subject}"


def app_turn(said: str) -> AppTurn:
    block = APP_BLOCK.search(said)
    if not block:
        return AppTurn()
    for kind, nudge in NUDGES:
        if found := nudge.fullmatch(block.group("nudge")):
            me, topic = found.group("me").strip(), block.group("topic").strip()
            before = said[:block.start()]
            kind = COUNCIL if kind == CHAT and is_council(me, topic, block.group("guidelines")) else kind
            return AppTurn(kind, topic, skill_of(kind, before, block.group("guidelines")), me, True,
                           names_of(block.group("others")), city_of(kind, before), asked_files(before))
    return AppTurn()


def without_descriptions(others: str) -> str:
    while ABOUT_PART.search(others):
        others = ABOUT_PART.sub("", others)
    return others


def names_of(others: str) -> tuple:
    names = {name.strip() for name in without_descriptions(others).split(",")}
    return tuple(sorted(name for name in names if HUB_NAME.fullmatch(name) and name != NOBODY))


def asked_files(before: str) -> tuple:
    return tuple(sorted(set(ASKED_FILE.findall(own_task(before)))))


def city_of(kind: str, before: str) -> str:
    city = CITY_NAME.search(before) if kind == COUNCIL else None
    return city.group(1).strip().lower() if city else ""


def task_section(text: str) -> tuple[int, int, int] | None:
    project = PROJECT_MESSAGE.search(text)
    sections = list(TASK_SECTION.finditer(text, project.end())) if project else []
    if len(sections) != 1:
        return None
    section_start, task_start = sections[0].start(), sections[0].start("task")
    files = text.find(FILES_HEADING, task_start)
    return section_start, task_start, files if files >= 0 else len(text)


def own_task(before: str) -> str:
    section = task_section(before)
    return before[section[1]:section[2]] if section else ""


def kept_start(text: str, keep: int, cut_note: str) -> str:
    return text if keep >= len(text) else f"{text[:keep]}{cut_note}"


def kept_end(text: str, keep: int, cut_note: str) -> str:
    return text if keep >= len(text) else f"{cut_note}{text[len(text) - keep:]}"


def app_block_without_descriptions(tail: str) -> str:
    block = APP_BLOCK.search(tail)
    if not block:
        return tail
    start, end = block.span("others")
    return f"{tail[:start]}{without_descriptions(tail[start:end])}{tail[end:]}"


def trimmed_keeping_task(text: str, limit: int, cut_note: str) -> str:
    section, block = task_section(text), APP_BLOCK.search(text)
    if not section:
        return ""
    project_start, (section_start, task_start, section_end) = PROJECT_MESSAGE.search(text).start(), section
    tail_start = block.start() if block and block.start() >= section_end else len(text)
    task_line_end = text.find("\n", task_start, section_end) + 1 or section_end
    earlier, brief = text[:project_start], text[project_start:section_start]
    task, detail = text[section_start:task_line_end], text[task_line_end:section_end]
    files, tail = text[section_end:tail_start], text[tail_start:]
    if len(task) + len(detail) + len(tail) + 3 * len(cut_note) > limit:
        tail = app_block_without_descriptions(tail)
    budget = limit - len(task) - len(tail) - 4 * len(cut_note)
    if budget < 0:
        return ""
    detail_keep = min(len(detail), budget)
    budget -= detail_keep
    brief_keep = min(len(brief), budget // 2)
    files_keep = min(len(files), budget - brief_keep)
    brief_keep = min(len(brief), budget - files_keep)
    earlier_keep = budget - brief_keep - files_keep
    return (kept_end(earlier, earlier_keep, cut_note) + kept_start(brief, brief_keep, cut_note) + task
            + kept_start(detail, detail_keep, cut_note) + kept_start(files, files_keep, cut_note) + tail)


def is_council(me: str, topic: str, guidelines: str) -> bool:
    return bool(COUNCIL_TOPIC.fullmatch(topic)) and guidelines.startswith(COUNCIL_GUIDE.format(name=me))


def known_skill(skill: str) -> str:
    return skill if skill in ROOM_SKILLS else ""


def skill_of(kind: str, before: str, guidelines: str) -> str:
    if kind != WORK:
        return ""
    if tryout := TRYOUT_SKILL.search(guidelines):
        return known_skill(tryout.group(1))
    task = TASK_LINE.match(own_task(before))
    return known_skill(task.group(1)) if task else ""


def has_spoofed_markers(said: str) -> bool:
    block = APP_BLOCK.search(said)
    room_part = said[:block.start("nudge")] if block else said
    return bool(APP_MARKERS.search(room_part) or SCRATCHPAD_HEADINGS.search(room_part)
                or room_voice.has_focus_marker(room_part))


def room_messages(said: str) -> list[str]:
    block = APP_BLOCK.search(said)
    room_part = said[:block.start()] if block else said
    return [part.strip() for part in room_part.split("\n\n") if part.strip()]


def newest_room_message(said: str) -> str:
    spoken = [part for part in room_messages(said) if not part.startswith("(room) ")]
    return spoken[-1] if spoken else ""


def kept_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in KEPT_AS_WRITTEN.finditer(text)]


def without_long_dashes(text: str, council: bool = False) -> str:
    if not DASH_CHARACTER.search(text):
        return text
    pieces, position = [], 0
    for start, end in kept_spans(text):
        pieces.append(prose_without_dashes(text[position:start], council))
        pieces.append(DASH_CHARACTER.sub(PLAIN_DASH, text[start:end]))
        position = end
    pieces.append(prose_without_dashes(text[position:], council))
    return "".join(pieces)


def prose_without_dashes(text: str, council: bool) -> str:
    if not DASH_CHARACTER.search(text):
        return text
    if council:
        text = DASH_AFTER_ACTION.sub(ACTION_SEPARATOR, text)
    text = DASH_IN_RANGE.sub(PLAIN_DASH, text)
    text = DASH_AS_MINUS.sub(PLAIN_DASH, text)
    text = DASH_STARTING_A_LINE.sub(lambda match: f"{match.group(1)}{PLAIN_DASH} ", text)
    return DASH_BETWEEN_WORDS.sub(lambda match: dash_replacement(*neighbours(match)), text)


def neighbours(match: re.Match) -> tuple[str, str]:
    text, start, end = match.string, match.start(), match.end()
    return (text[start - 1] if start else NEW_LINE), (text[end] if end < len(text) else NEW_LINE)


def dash_replacement(before: str, after: str) -> str:
    if after in ENDS_A_CLAUSE:
        return ""
    if before == NEW_LINE:
        return ""
    if before in ENDS_A_CLAUSE:
        return " "
    return COMMA


def with_asked_file_block(answer: str, asked: tuple) -> str:
    fenced = FENCED_BLOCK.findall(answer)
    if len(asked) != 1 or len(fenced) != 1 or FILE_BLOCK_START in answer:
        return answer
    return FENCED_BLOCK.sub(lambda match: f"[file path={asked[0]}]\n{fenced[0]}\n[/file]", answer, count=1)


def is_pass(answer: str) -> bool:
    return answer.strip().rstrip(".!").strip().upper() == PASS


def sounds_like_a_failure(answer: str) -> bool:
    text = answer.strip()
    return len(text) <= SHORT_REPLY_CHARS and bool(TIMEOUT_APOLOGY.fullmatch(text))


def has_open_hand_in(answer: str) -> bool:
    unclosed = any(len(OPENED_HAND_IN[tag].findall(answer)) > len(CLOSED_HAND_IN[tag].findall(answer))
                   for tag in OPENED_HAND_IN)
    return unclosed or answer.count(FENCE) % 2 == 1


def open_block_start(answer: str) -> int:
    last_opening = {found.group(1).lower(): found.start() for found in OPENING_TAG.finditer(answer)}
    last_closing = {found.group(1).lower(): found.start() for found in CLOSING_TAG.finditer(answer)}
    unclosed = [start for tag, start in last_opening.items()
                if last_closing.get(tag, -1) < start and not (tag in LINE_TAGS and "\n" in answer[start:])]
    return min(unclosed, default=len(answer))


def is_initial(text: str, stop: int, letter: str) -> bool:
    return letter.isupper() and bool(NEXT_INITIAL.match(text, stop + 1) or PREVIOUS_INITIAL.search(text[:stop - 1]))


def is_dotted_short_form(word: str) -> bool:
    return FULL_STOP in word and all(SHORT_FORM_PART.fullmatch(part) for part in word.split(FULL_STOP))


def is_complete(kept: str) -> bool:
    has_bracket_line = kept.startswith("[") or bool(BRACKET_LINE.search(kept))
    return has_bracket_line or bool(ENDS_A_SENTENCE.search(kept)) or len(kept.split()) >= FEWEST_WORDS_KEPT


def follows_digit(text: str, start: int) -> bool:
    return start > 0 and text[start - 1].isdigit()


def leading_verdict(text: str, kind: str) -> str:
    found = LEADING_TAG.match(text)
    tag = found.group(1) if found else ""
    return tag if kind == REVIEW and tag.lower() == VERDICT_KEPT_ALONE else ""


def is_abbreviation(text: str, stop: int) -> bool:
    word = WORD_BEFORE.search(text[:stop])
    found = word.group(1).lower() if word else ""
    single_letter = word.group(1) if word and len(found) == 1 else ""
    is_number_sign = found == NUMBER_SIGN and bool(NEXT_DIGIT.match(text, stop + 1))
    is_listed = found in ABBREVIATIONS and not follows_digit(text, word.start() if word else stop)
    is_short_form = (is_listed or is_dotted_short_form(found) or is_number_sign or single_letter.islower()
                     or is_initial(text, stop, single_letter))
    return text[stop] == FULL_STOP and is_short_form


def has_bracket_blocks(answer: str) -> bool:
    return len(BRACKET_LINE.findall(answer)) > 1 or bool(OPENING_TAG.search(answer) or CLOSING_TAG.search(answer))


def finished_part(answer: str, kind: str = CHAT) -> str:
    if has_open_hand_in(answer):
        return ""
    text = answer[:open_block_start(answer)]
    ends = [found.end() for found in LINE_OR_BLOCK_END.finditer(text)]
    if not has_bracket_blocks(answer):
        ends += [found.end() for found in SENTENCE_END.finditer(text) if not is_abbreviation(text, found.start())]
    kept = text[:max(ends)].strip() if ends else ""
    return kept if is_complete(kept) else leading_verdict(text, kind)


def words_of(text: str) -> list[str]:
    return WORD.findall(text.lower())


def key_details(text: str) -> frozenset:
    return frozenset([*NUMBER.findall(text), *(word.lower() for word in YES_OR_NO.findall(text)),
                      *CAPITALISED.findall(text)])


def is_same_message(new: str, earlier: str) -> bool:
    new_words, earlier_words = words_of(new), words_of(earlier)
    if min(len(new_words), len(earlier_words)) < MIN_WORDS_TO_COMPARE:
        return False
    if key_details(new) != key_details(earlier):
        return False
    return difflib.SequenceMatcher(None, new_words, earlier_words).ratio() >= SAME_MESSAGE_RATIO


def repeats_itself(answer: str, earlier_answers: list[str]) -> bool:
    return any(is_same_message(answer, earlier) for earlier in earlier_answers)
