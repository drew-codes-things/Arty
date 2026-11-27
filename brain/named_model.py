import re
from dataclasses import dataclass

SCREEN_LOOK = re.compile(r"<screen>", re.IGNORECASE)
INSERTED_TEXT = (
    re.compile(r"<(context|screen)>.*?(?:</\1>|$)", re.DOTALL | re.IGNORECASE),
    re.compile(r"<file\b[^>]*>.*?(?:</file>|$)", re.DOTALL | re.IGNORECASE),
    re.compile(r"```.*?(?:```|$)", re.DOTALL),
    re.compile(r"`[^`\n]*`"),
    re.compile("\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’"),
    re.compile(r"^[ \t]*>.*$", re.MULTILINE),
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\s*\n\s*")
LEAD_IN = r"(?:(?:please|ok|okay|now|so|right|and|then|hey|arty|can you|could you|would you)[,\s]+)*"
ANY_ENDING = re.compile(r"\s*$|\s*[,;:.!?](?:\s|$)|\s+(?:to|for|from now on|instead|and|please)\b", re.IGNORECASE)
TO_ENDING = re.compile(r"\s+to\b", re.IGNORECASE)
COMMA_ENDING = re.compile(r"\s*[,:](?:\s|$)")
FORMS = (
    (re.compile(rf"{LEAD_IN}from now on,?\s+(?:use|go with|stick with)\s+", re.IGNORECASE), True, ANY_ENDING),
    (re.compile(rf"{LEAD_IN}(?:switch|change|swap)(?:\s+back|\s+over)?\s+to\s+", re.IGNORECASE), True, ANY_ENDING),
    (re.compile(rf"{LEAD_IN}go back to\s+", re.IGNORECASE), True, ANY_ENDING),
    (re.compile(rf"{LEAD_IN}(?:use|stick with)\s+", re.IGNORECASE), False, ANY_ENDING),
    (re.compile(rf"{LEAD_IN}ask\s+", re.IGNORECASE), False, TO_ENDING),
    (re.compile(rf"{LEAD_IN}with\s+", re.IGNORECASE), False, COMMA_ENDING),
)
STAYS = re.compile(r"\bfrom now on\b|\bfor the rest of (?:this|the) (?:chat|conversation)\b", re.IGNORECASE)
WORD = re.compile(r"\S+")
TRAILING_MARKS = re.compile(r"[,;:.!?]+$")
MOST_NAME_WORDS = 8
ARTICLES = {"the", "a", "an", "my", "your", "our", "that", "this"}
MODEL_WORDS = {"model", "one", "llm"}
SIZE = re.compile(r"^\d+(?:\.\d+)?b$", re.IGNORECASE)
TAGGED_SIZE = re.compile(r"^[\w.-]+:(\d+(?:\.\d+)?b)$", re.IGNORECASE)
WHOLE_ID, LABEL, ALIAS = 3, 2, 1
WHICH_ONE = "Which one did you mean: {options}?"
NO_ACCESS = "That model isn't available to you."


@dataclass(frozen=True)
class Candidate:
    id: str
    model: str
    label: str
    also_called: tuple = ()
    where: str = ""


@dataclass(frozen=True)
class Asked:
    candidate: Candidate | None = None
    reply: str = ""
    persist: bool = False


@dataclass(frozen=True)
class Instruction:
    phrases: tuple
    persist: bool


@dataclass(frozen=True)
class Name:
    key: str
    rank: int
    needs_model_word: bool


def typed_part(text: str) -> str:
    for inserted in INSERTED_TEXT:
        text = inserted.sub(" ", text)
    return text


def sentences_of(text: str) -> list[str]:
    said = [sentence.strip() for sentence in SENTENCE_BREAK.split(typed_part(text or "")) if sentence.strip()]
    return list(dict.fromkeys(said[:1] + said[-1:]))


def phrase_ends(rest: str) -> list[int]:
    ends = []
    for word in list(WORD.finditer(rest))[:MOST_NAME_WORDS]:
        marks = TRAILING_MARKS.search(word.group())
        ends += [word.end()] + ([word.end() - len(marks.group())] if marks else [])
    return sorted({end for end in ends if end > 0}, reverse=True)


def instruction_in(sentence: str) -> Instruction | None:
    for form, persist, ending in FORMS:
        if not (opened := form.match(sentence)):
            continue
        rest = sentence[opened.end():]
        phrases = tuple(rest[:end] for end in phrase_ends(rest) if ending.match(rest, end))
        if phrases:
            return Instruction(phrases, persist or bool(STAYS.search(sentence)))
    return None


def instructions_in(text: str) -> list[Instruction]:
    if SCREEN_LOOK.search(text or ""):
        return []
    return [found for sentence in sentences_of(text) if (found := instruction_in(sentence))]


def key_of(phrase: str) -> tuple[str, bool]:
    words = TRAILING_MARKS.sub("", phrase.strip()).lower().split()
    while words and words[0] in ARTICLES:
        words.pop(0)
    says_model = bool(words) and words[-1] in MODEL_WORDS
    if says_model:
        words.pop()
    return " ".join(words), says_model


def size_aliases(label: str) -> list[Name]:
    if tagged := TAGGED_SIZE.match(label):
        return [Name(tagged[1], ALIAS, False)]
    words = label.split()
    at = next((index for index, word in enumerate(words) if SIZE.match(word)), None)
    if at is None:
        return []
    aliases = [Name(" ".join(words[at:]), ALIAS, False)]
    return aliases + ([Name(" ".join(words[at + 1:]), ALIAS, True)] if words[at + 1:] else [])


def names_of(candidate: Candidate) -> list[Name]:
    model = candidate.model.lower()
    label = " ".join(candidate.label.lower().split())
    names = [Name(model, WHOLE_ID, False)] + ([Name(label, LABEL, False)] if label != model else [])
    return names + size_aliases(label) + [Name(word, ALIAS, True) for word in candidate.also_called]


def rank_for(key: str, says_model: bool, candidate: Candidate) -> int:
    ranks = [name.rank for name in names_of(candidate) if name.key == key and (says_model or not name.needs_model_word)]
    return max(ranks, default=0)


def best_of(key: str, says_model: bool, candidates: list[Candidate]) -> list[Candidate]:
    ranked = [(rank_for(key, says_model, candidate), candidate) for candidate in candidates]
    top = max((rank for rank, _ in ranked), default=0)
    return [candidate for rank, candidate in ranked if top and rank == top]


def listed(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def shown_names(candidates: list[Candidate]) -> list[str]:
    labels = [candidate.label for candidate in candidates]
    return [f"{candidate.label} on {candidate.where}" if candidate.where and labels.count(candidate.label) > 1
            else candidate.label for candidate in candidates]


def only_preferred(found: list[Candidate], prefer) -> list[Candidate]:
    preferred = [candidate for candidate in found if prefer(candidate)] if prefer else []
    return preferred if len(preferred) == 1 else found


def matches_for(instruction: Instruction, visible: list[Candidate], locked: list[Candidate],
                prefer=None) -> tuple[list[Candidate], bool]:
    for phrase in instruction.phrases:
        key, says_model = key_of(phrase)
        if not key:
            continue
        if found := only_preferred(best_of(key, says_model, visible), prefer):
            return found, False
        if best_of(key, says_model, locked):
            return [], True
    return [], False


def answer_for(instruction: Instruction, visible: list[Candidate], locked: list[Candidate], prefer=None) -> Asked | None:
    found, out_of_reach = matches_for(instruction, visible, locked, prefer)
    if len(found) == 1:
        return Asked(found[0], persist=instruction.persist)
    if found:
        return Asked(reply=WHICH_ONE.format(options=listed(shown_names(found))))
    return Asked(reply=NO_ACCESS) if out_of_reach else None


def picked_from(reply: str, offered: list[Candidate]) -> list[Candidate]:
    said = " ".join(TRAILING_MARKS.sub("", typed_part(reply).strip()).lower().split())
    by_place = [candidate for candidate in offered if said == f"{candidate.label} on {candidate.where}".lower()]
    return by_place or best_of(*key_of(said), offered)


def which_one_question(messages: list[dict], text_of) -> str:
    said = [message for message in messages if message.get("role") in ("user", "assistant")]
    if len(said) < 3 or [message.get("role") for message in said[-3:]] != ["user", "assistant", "user"]:
        return ""
    question = text_of(said[-2])
    return question if question.startswith(WHICH_ONE.split("{", 1)[0]) else ""


def is_a_tool_step(message: dict) -> bool:
    return message.get("role") == "tool" or (message.get("role") == "assistant" and bool(message.get("tool_calls")))


def latest_request(messages: list[dict]) -> dict | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
        if not is_a_tool_step(message):
            return None
    return None


def may_name_a_model(messages: list[dict], text_of) -> bool:
    if not (request := latest_request(messages)):
        return False
    return bool(instructions_in(text_of(request)) or which_one_question(messages, text_of))


def answered_choice(messages: list[dict], visible: list[Candidate], text_of, prefer=None) -> Asked | None:
    if not which_one_question(messages, text_of):
        return None
    said = [message for message in messages if message.get("role") in ("user", "assistant")]
    for instruction in instructions_in(text_of(said[-3])):
        tied, _ = matches_for(instruction, visible, [], prefer)
        if len(tied) > 1 and len(found := picked_from(text_of(said[-1]), tied)) == 1:
            return Asked(found[0], persist=instruction.persist)
    return None


def asked_model(messages: list[dict], visible: list[Candidate], locked: list[Candidate], text_of,
                prefer=None) -> Asked | None:
    if not (request := latest_request(messages)):
        return None
    if chosen := answered_choice(messages, visible, text_of, prefer):
        return chosen
    for instruction in instructions_in(text_of(request)):
        if asked := answer_for(instruction, visible, locked, prefer):
            return asked
    return None
