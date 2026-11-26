import bisect
import re

import safety

QUOTE_CLOSERS = {"\"": "\"", "\u201c": "\u201d", "`": "`"}
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
ADVERBS = r"(?:(?:really|honestly|just|so|truly|actually|literally|badly|desperately|genuinely|seriously|fucking)\s+)*"
EXTRA_STATEMENTS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?<!\bhill )(?<!\bhill that )\bI(?:'m| am)?\s+" + ADVERBS + r"(?:wanna|want to) die\b(?!\s+(?:laughing|of\s+(?:embarrassment|shame|"
    r"laughter|boredom)|on\s+(?:this|that|the|my)\s+hill))",
    r"\bI feel (?:like|as if)\s+" + ADVERBS + r"(?:dying|killing myself|ending it all)\b(?!\s+(?:of\s+(?:embarrassment|shame|"
    r"laughter|boredom)|from\s+(?:embarrassment|shame|laughing|the heat)|laughing))",
    r"\bmy life (?:isn't|is not) worth living\b",
    r"\bI mean (?:actually|literally) die\b",
    r"\bI wish I (?:was|were) dead\b(?!(?:\s+|-)(?:wrong|serious)\b)",
))


def quoted_spans(sentence: str) -> list[tuple[int, int]]:
    last_closer = {closer: sentence.rfind(closer) for closer in set(QUOTE_CLOSERS.values())}
    spans, position = [], 0
    while position < len(sentence):
        closer = QUOTE_CLOSERS.get(sentence[position])
        end = sentence.find(closer, position + 1) if closer and position < last_closer[closer] else -1
        if end == -1:
            position += 1
            continue
        spans.append((position, end + 1))
        position = end + 1
    return spans


def normalised(text: str) -> str:
    return text.replace("’", "'").replace(" ", " ")


def is_within(position: int, spans: list[tuple[int, int]], starts: list[int]) -> bool:
    index = bisect.bisect_right(starts, position) - 1
    return index >= 0 and position < spans[index][1]


def has_extra_statement_in(sentence: str) -> bool:
    hits = [match.start() for pattern in EXTRA_STATEMENTS for match in pattern.finditer(sentence)]
    if not hits:
        return False
    spans = quoted_spans(sentence)
    starts = [start for start, _ in spans]
    return any(not is_within(position, spans, starts) for position in hits)


def has_extra_statement(text: str) -> bool:
    plain = normalised(text or "")
    if not any(pattern.search(plain) for pattern in EXTRA_STATEMENTS):
        return False
    for line in plain.splitlines():
        if not any(pattern.search(line) for pattern in EXTRA_STATEMENTS):
            continue
        if any(has_extra_statement_in(sentence) for sentence in SENTENCE_END.split(line)):
            return True
    return False


def check(text: str, crisis_help: str | None = None, kinds: tuple[str, ...] = safety.ALL_CHECKS) -> tuple[str, str] | None:
    declined = safety.check(text or "", crisis_help, kinds)
    if declined and declined[0] == "self_harm":
        return declined
    if "self_harm" in kinds and has_extra_statement(text):
        return "self_harm", safety.self_harm_reply(crisis_help)
    return declined
