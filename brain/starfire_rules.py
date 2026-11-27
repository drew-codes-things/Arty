import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import content_rules
import content_screen
import private_text
import safety
import screen_review

from api_support import ApiError
from prompting import system_prompt

MAX_MODEL_CHARS = 200
MAX_CONTEXT_CHARS = 20000
SAID_PIECE_CHARS = 20000
SAID_PIECE_OVERLAP = 2000
JOIN_CHARS = SAID_PIECE_OVERLAP
MAX_CHECKED_CHARS = 1_000_000
MAX_EXCHANGES = 2000
SAID_TOO_LONG = 413
CHECK_SECONDS = 6.0
CHECK_RAN_OUT = 503
REJUDGED_REQUESTS = 3
VERDICTS_KEPT = 4096
MINORS_KIND = "minors"
EVERY_MODEL = "*"
SELF_HARM_KIND = "self_harm"
CALLER_ONLY_KINDS = frozenset({SELF_HARM_KIND, "bot_check", "scam"})
STANDING_KINDS = frozenset({"bot_check", "scam"})
ANSWER_KINDS = frozenset({"weapons", MINORS_KIND})
HELP_KINDS = frozenset({SELF_HARM_KIND})
SAID_PARTS = ("caller", "standing", "tools", "offered_tools")
STANDING_SENTENCE = re.compile(r"[^\n.!?;]+")
SENTENCE_JOIN = ".\n"
CLAUSE_BREAK = re.compile(r" ?(?:(?P<hard>:|\b(?:so|but|then|because|unless|otherwise|always|just)\b)|(?P<comma>,)|"
                          r"\b(?P<list>and|or|nor)\b) ?", re.IGNORECASE)
LIST_ENDS_IN_A_CHOICE = re.compile(r"\b(?:or|nor)\b", re.IGNORECASE)
CLAUSES_PER_TIME_CHECK = 256
PLAIN_PROHIBITION = re.compile(
    r"\b(?:never|don['\u2019]?t|do not|must not|mustn['\u2019]?t|should not|shouldn['\u2019]?t|won['\u2019]?t|will not|"
    r"can['\u2019]?t|cannot|not allowed to|forbidden to|avoid)\b"
    r"(?!\s+(?:refus|declin|hesitat|mind|question|object|worry|care|stop)\w*)", re.IGNORECASE)
NEGATION_BEFORE = r"(?<!\bnever )(?<!\bnot )(?<!n't )(?<!n’t )"
NOT_A_PROHIBITION = r"(?!\s+(?:refus|declin|hesitat|mind|question|object|worry|care|stop|fail)\w*)"
PROHIBITION = re.compile(
    r"(?:\b(?:never|don['’]?t|do not|does not|doesn['’]?t|must not|mustn['’]?t|should not|shouldn['’]?t|won['’]?t|will not|"
    r"can['’]?t|cannot|may not|shall not|(?:are|is|am) not to|(?:not|never) (?:allowed|permitted|supposed) to|"
    r"(?:forbidden|prohibited|banned) (?:to|from)|avoid(?:ing)?|under no circumstances|in no case|at no (?:point|time)|"
    r"rather than|instead of|against (?:\w+['’]s |our |the |my |your |their |this |company )?(?:\w+ )?"
    r"(?:policy|policies|rules|terms|guidelines) to)\b|\bno (?=\w+ing\b))" + NOT_A_PROHIBITION + "|"
    + NEGATION_BEFORE + r"\b(?:refus(?:e|es|ing)|declin(?:e|es|ing))(?: (?:any|all|every))?(?: (?:requests?|asks?|attempts?))? to\b|"
    r"(?P<defer>\b(?:ask|tell|let|have|get|leave it to|wait for) (?:the )?(?:user|human|person|customer|caller|visitor|people|them)\b)",
    re.IGNORECASE)
ASSISTANT_ACTS = re.compile(r"\b(?:while|when|whenever|each time|every time|before|after|as|once|until|and|then|that) (?=you\b)",
                            re.IGNORECASE)
THIRD_PARTY = re.compile(r"\b(?:criminals|scammers|fraudsters|attackers|hackers|crooks|thieves|bad actors|cyber-?criminals|"
                         r"spammers|phishers)\b", re.IGNORECASE)
THIRD_PARTY_ROLE = (r"\b(?:play(?:s|ing)?|as|act(?:s|ing)? as|work(?:s|ing)? for|on behalf of|join(?:s|ing)?|you are|you['’]re|"
                    r"be(?:come)?|pretend(?:ing)? to be|role-?play(?:ing)?(?: as)?) (?:the |a |an |some |one of the )?")
TAKES_IT_UP = re.compile(r"\b(?:so (?:will|should|must|can|do) you|you (?:will|should|must|can) too|and (?:so )?(?:will|should) you|"
                         r"(?:just )?like (?:them|they do))\b|" + THIRD_PARTY_ROLE + THIRD_PARTY.pattern, re.IGNORECASE)
CONDITION = re.compile(r"^\W*(?:if|when|whenever|should|in case|once|where)\b.*\b(?:asks?|asked|requires?|required|wants?|"
                       r"tells?|told|requests?|requested|needs?|prompts?|says?)\b", re.IGNORECASE)
TURNS_IT_DOWN = re.compile(r"^\W*(?:(?:politely|just|simply|then) )?(?:say no|refuse|decline|reject|ignore|stop|pause|"
                           r"hold off|turn (?:it|them|that) down|hand (?:\w+ )?(?:back|over))\b", re.IGNORECASE)
GOES_ALONG = re.compile(r"^\W*(?:just )?(?:do (?:it|so|that|as (?:it|they|he|she) says?)|comply|go ahead|proceed|carry on|"
                        r"follow (?:it|them|that)|obey|help(?: them| with (?:it|that))?)\b", re.IGNORECASE)
FORBIDDEN_WORDS = r"(?:blocked|banned|forbidden|prohibited|disallowed|off[- ]limits|restricted|not allowed|not permitted)"
LISTED_THINGS = (r"(?:topics?|tasks?|actions?|acts?|requests?|things?|activities|content|uses?|subjects?|areas?|behaviou?rs?|"
                 r"items?|list|jobs?|work)")
LIST_HEADING = re.compile(r"^\W*(?:" + FORBIDDEN_WORDS + r"(?: " + LISTED_THINGS + r")?|" + LISTED_THINGS
                          + r"(?: that are| you are| which are)? " + FORBIDDEN_WORDS + r"|(?:never|do not|don['’]?t)(?: do)?)\W*$",
                          re.IGNORECASE)
FORBIDDEN_AFTER = re.compile(
    r"\b(?:is|are) (?:strictly |absolutely |never |not )?(?:prohibited|forbidden|not allowed|not permitted|banned|"
    r"off[- ]limits)\b|\bmust (?:always )?be (?:turned down|refused|declined|rejected|ignored)\b", re.IGNORECASE)
WORD = re.compile(r"\w")
ASIDE = re.compile(r"\s*(?:under any circumstances|in any case|at any (?:time|point)|ever|even if (?:asked|told)|please|"
                   r"whatever happens|no matter what)\s*", re.IGNORECASE)
JOIN_FRAGMENT_CHARS = 400
GOES_ON_AS_ITS_OWN_CLAUSE = re.compile(
    r"\s*(?:,\s*(?:it|they|this|that|which|she|he)\b|(?:shows?|keeps?|is|are|was|were|appears?|loads?|reloads?|"
    r"pops? up|comes? up|won['\u2019]?t|doesn['\u2019]?t|does|did|has|had|just|mock-?ups?|templates?|wireframes?|"
    r"screenshots?|designs?)\b)", re.IGNORECASE)
HEAD_WINDOW_CHARS = 1000
MAX_JOINED_HEADS = 8
HEAD_START = re.compile(r"\n\s*|[.?!]\s+")
SENTENCE_END = re.compile(r"[.?!]")
NOT_MEANINGFUL = re.compile(r"[\W_]+")
ANSWER_MIN_CHARS = 20
MAX_CONTEXT_LINES_DROPPED = 8
STARFIRE_LIMITS = ("You are answering in the starfire chat app, on a model someone else runs. Here you have no memories of the "
                   "user, no earlier conversations, no access to their computer, server, files, email, calendar or phone, and "
                   "only the tools starfire offers in this chat. If something needs more than that, say so and suggest they ask "
                   "Arty on a local model.")


def optional_text(request: dict, field: str) -> str:
    value = request.get(field, "")
    if not isinstance(value, str):
        raise ApiError(400, f"{field} must be text")
    return value


@dataclass(frozen=True)
class SaidParts:
    caller: str
    standing: str = ""
    tools: str = ""
    offered_tools: str = ""

    def size(self) -> int:
        return len(self.caller) + len(self.standing) + len(self.tools) + len(self.offered_tools)


def checked_said(request: dict) -> str:
    return optional_text(request, "said")


def checked_said_parts(request: dict, said: str) -> SaidParts:
    parts = request.get("said_parts")
    if parts is None:
        return SaidParts(caller=said)
    if not isinstance(parts, dict) or set(parts) - set(SAID_PARTS):
        raise ApiError(400, f"said_parts must be an object with only {', '.join(SAID_PARTS)} text")
    return SaidParts(*(optional_text(parts, part) for part in SAID_PARTS))


def checked_exchange(exchange) -> tuple[str, str]:
    if not isinstance(exchange, dict):
        raise ApiError(400, "each answered exchange must be an object with asked and answer text")
    asked, answer = exchange.get("asked", ""), exchange.get("answer", "")
    if not isinstance(asked, str) or not isinstance(answer, str):
        raise ApiError(400, "asked and answer must be text")
    return asked, answer


def checked_answered(request: dict) -> list[tuple[str, str]]:
    answered = request.get("answered", [])
    if not isinstance(answered, list):
        raise ApiError(400, "answered must be a list")
    if len(answered) > MAX_EXCHANGES:
        raise ApiError(SAID_TOO_LONG, f"answered has over {MAX_EXCHANGES} exchanges, so Arty can't check all of them")
    return [checked_exchange(exchange) for exchange in answered]


def checked_size(said: str, parts: SaidParts, answered: list[tuple[str, str]]):
    size = max(len(said), parts.size()) + sum(len(asked) + len(answer) for asked, answer in answered)
    if size > MAX_CHECKED_CHARS:
        raise ApiError(SAID_TOO_LONG,
                       f"said and answered are over {MAX_CHECKED_CHARS} characters, so Arty can't check all of it")


def checked_request(request: dict) -> tuple[str, str, SaidParts, list[tuple[str, str]], str, bool]:
    model = request.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > MAX_MODEL_CHARS:
        raise ApiError(400, f"model must be a model name of 1 to {MAX_MODEL_CHARS} characters")
    if "unrestricted" in request and not isinstance(request["unrestricted"], bool):
        raise ApiError(400, "unrestricted must be true or false")
    starfire_allows_unrestricted = request.get("unrestricted", True)
    said, answered = checked_said(request), checked_answered(request)
    parts = checked_said_parts(request, said)
    checked_size(said, parts, answered)
    return (model.strip(), said, parts, answered, optional_text(request, "context")[-MAX_CONTEXT_CHARS:],
            starfire_allows_unrestricted)


def refusal_texts(config: dict) -> tuple[str, ...]:
    texts = (safety.WEAPONS_REPLY, safety.BOT_CHECK_REPLY, safety.SCAM_REPLY, safety.self_harm_reply(None),
             safety.self_harm_reply(config.get("crisis_help")), content_screen.MINORS_REPLY, content_screen.CANT_CHECK_REPLY,
             private_text.DECLINE)
    return tuple(sorted({text.strip() for text in texts if text and text.strip()}, key=len, reverse=True))


def without_refusals(text: str, refusals: tuple[str, ...]) -> str:
    for refusal in refusals:
        text = text.replace(refusal, " ")
    return text


def is_refusal(answer: str, refusals: tuple[str, ...]) -> bool:
    return any(answer.strip().endswith(refusal) for refusal in refusals)


def said_pieces(said: str) -> list[str]:
    step = SAID_PIECE_CHARS - SAID_PIECE_OVERLAP
    return [said[start:start + SAID_PIECE_CHARS] for start in range(0, max(len(said) - SAID_PIECE_OVERLAP, 1), step)]


class CheckRanOut(ApiError):
    def __init__(self):
        super().__init__(CHECK_RAN_OUT, f"Arty could not check all of this within {CHECK_SECONDS:g} seconds")


class Checker:
    def __init__(self, crisis_help, kinds: tuple[str, ...], context: str, refusals: tuple[str, ...]):
        self.crisis_help = crisis_help
        self.kinds = kinds
        self.context = context
        self.refusals = refusals
        self.deadline = time.monotonic() + CHECK_SECONDS
        earlier = content_screen.Context(context) if MINORS_KIND in kinds else None
        self.minors_always = bool(earlier and (earlier.recent_sexual or earlier.creative_young or earlier.sexual_young
                                               or earlier.has_young_character))

    def may_involve_minors(self, piece: str) -> bool:
        return self.minors_always or bool(content_screen.ANY_SEXUAL.search(content_screen.readable(piece)))

    def kinds_without(self, left_alone: frozenset[str]) -> tuple[str, ...]:
        return tuple(kind for kind in self.kinds if kind not in left_alone)

    def kinds_only(self, wanted: frozenset[str]) -> tuple[str, ...]:
        return tuple(kind for kind in self.kinds if kind in wanted)

    def piece_kinds(self, piece: str, kinds: tuple[str, ...] | None = None) -> tuple[str, ...]:
        kinds = self.kinds if kinds is None else kinds
        if MINORS_KIND in kinds and not self.may_involve_minors(piece):
            return tuple(kind for kind in kinds if kind != MINORS_KIND)
        return kinds

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise CheckRanOut()

    def declined(self, text: str, kinds: tuple[str, ...] | None = None) -> tuple[str, str] | None:
        kinds = self.kinds if kinds is None else kinds
        if not kinds or not text:
            return None
        for piece in said_pieces(without_refusals(text, self.refusals)):
            self.check_time()
            if declined := safety.check(piece, self.crisis_help, self.piece_kinds(piece, kinds), self.context):
                return declined
        return None


@dataclass(frozen=True)
class Clause:
    text: str
    ends_with: str | None
    choice_follows: bool


def break_kind(found: re.Match) -> str:
    return next(kind for kind in ("hard", "comma", "list") if found.group(kind))


def is_a_choice(found: re.Match) -> bool:
    return bool(found.group("list") and LIST_ENDS_IN_A_CHOICE.fullmatch(found.group("list")))


def clauses(checker: Checker, sentence: str) -> list[Clause]:
    pieces, start = [], 0
    for number, found in enumerate(CLAUSE_BREAK.finditer(sentence)):
        if number % CLAUSES_PER_TIME_CHECK == 0:
            checker.check_time()
        if found.start() > start:
            pieces.append([sentence[start:found.start()], break_kind(found), is_a_choice(found)])
        elif pieces:
            pieces[-1][1] = "hard" if found.group("hard") or pieces[-1][1] == "hard" else break_kind(found)
            pieces[-1][2] = pieces[-1][2] or is_a_choice(found)
        start = found.end()
    if start < len(sentence):
        pieces.append([sentence[start:], None, False])
    found_clauses, choice_later = [], False
    for text, ends_with, choice in reversed(pieces):
        choice_later = choice_later or choice
        found_clauses.append(Clause(text, ends_with, choice_later))
        if ends_with == "hard":
            choice_later = choice
    return found_clauses[::-1]


def lists_more_forbidden_acts(checker: Checker, forbidden: str, clause: Clause, kinds: tuple[str, ...]) -> bool:
    if not WORD.search(forbidden) or ASIDE.fullmatch(forbidden):
        return True
    if clause.ends_with in ("list", "comma") and clause.choice_follows:
        return True
    return clause.ends_with == "list" and bool(checker.declined(forbidden, kinds))


def is_a_deferral(cue: re.Match) -> bool:
    return cue.re is PROHIBITION and bool(cue.group("defer"))


def first_cue(prohibition: re.Match | None, party: re.Match | None) -> re.Match | None:
    if party is not None and (prohibition is None or party.start() < prohibition.start()):
        return party
    return prohibition


def split_at_prohibition(checker: Checker, text: str, third_parties_count: bool) -> tuple[str, str | None]:
    kept, position, turns = [], 0, 0
    party = THIRD_PARTY.search(text) if third_parties_count else None
    while True:
        if turns % CLAUSES_PER_TIME_CHECK == 0:
            checker.check_time()
        turns += 1
        if party is not None and party.start() < position:
            party = THIRD_PARTY.search(text, position)
        cue = first_cue(PROHIBITION.search(text, position), party)
        if cue is None:
            break
        kept.append(text[position:cue.start()])
        acts = ASSISTANT_ACTS.search(text, cue.end()) if is_a_deferral(cue) else None
        if acts is None:
            return " ".join(kept), text[cue.end():]
        position = acts.end()
    kept.append(text[position:])
    return " ".join(kept), None


def turned_down(later: list[Clause]) -> bool:
    goes_along = any(GOES_ALONG.match(clause.text) for clause in later)
    return not goes_along and any(PROHIBITION.search(clause.text) or TURNS_IT_DOWN.match(clause.text) for clause in later)


def is_a_condition_only(parts: list[Clause], index: int) -> bool:
    clause = parts[index]
    ends_before_more = clause.ends_with in ("comma", "hard") and index + 1 < len(parts)
    return index == 0 and ends_before_more and bool(CONDITION.match(clause.text)) and turned_down(parts[index + 1:])


def unforbidden(checker: Checker, sentence: str, kinds: tuple[str, ...]) -> str:
    kept, carried = [], False
    parts = clauses(checker, sentence)
    third_parties_count = not TAKES_IT_UP.search(sentence)
    for index, clause in enumerate(parts):
        text = clause.text
        if not carried and LIST_HEADING.match(text) and clause.ends_with == "hard":
            return " ".join(kept)
        if not carried and is_a_condition_only(parts, index):
            kept.append("")
            continue
        if carried or FORBIDDEN_AFTER.search(text):
            kept.append("")
            carried = carried and lists_more_forbidden_acts(checker, text, clause, kinds)
            continue
        allowed, forbidden = split_at_prohibition(checker, text, third_parties_count)
        kept.append(allowed)
        carried = forbidden is not None and lists_more_forbidden_acts(checker, forbidden, clause, kinds)
    return " ".join(kept)


def orders_in(checker: Checker, text: str, kinds: tuple[str, ...]) -> str:
    if not kinds or not text:
        return ""
    sentences = []
    for sentence in STANDING_SENTENCE.findall(text):
        checker.check_time()
        sentences.append(unforbidden(checker, safety.spaced(sentence), kinds))
    return SENTENCE_JOIN.join(sentences)


def heads_before_a_prohibition(text: str) -> str:
    return SENTENCE_JOIN.join(PLAIN_PROHIBITION.split(sentence, maxsplit=1)[0] for sentence in STANDING_SENTENCE.findall(text))


def declined_orders(checker: Checker, text: str) -> tuple[str, str] | None:
    kinds = checker.kinds_only(STANDING_KINDS)
    return checker.declined(heads_before_a_prohibition(text), kinds) or checker.declined(orders_in(checker, text, kinds), kinds)


def sentence_tail(text: str) -> str:
    tail = text[-JOIN_FRAGMENT_CHARS:]
    ends = [found.end() for found in SENTENCE_END.finditer(tail)]
    return tail[ends[-1]:] if ends else tail


def sentence_heads(text: str) -> list[str]:
    window = text[:HEAD_WINDOW_CHARS]
    starts = [0, *(found.end() for found in HEAD_START.finditer(window))][:MAX_JOINED_HEADS]
    heads = []
    for start in starts:
        head = text[start:start + JOIN_FRAGMENT_CHARS]
        end = SENTENCE_END.search(head)
        heads.append(head[:end.end()] if end else head)
    return list(dict.fromkeys(head for head in heads if head.strip()))


def crosses_into(checker: Checker, before: str, after: str, kinds: tuple[str, ...]) -> tuple[str, str] | None:
    joined = checker.declined(f"{before}\n{after}", kinds)
    return joined if joined and not checker.declined(before, kinds) and not checker.declined(after, kinds) else None


def crossing(checker: Checker, tail: str, head: str, kinds: tuple[str, ...]) -> tuple[str, str] | None:
    checker.check_time()
    before, after = safety.spaced(tail).strip(), safety.spaced(head).strip()
    if not before or not after:
        return None
    joined = f"{before} {after}"
    for kind, found in safety.found_matches(joined, kinds):
        crosses = found.start() < len(before) and found.end() > len(before) + 1
        if crosses and not GOES_ON_AS_ITS_OWN_CLAUSE.match(joined, found.end()):
            return safety.check(joined, checker.crisis_help, (kind,))
    return None


def joined_across(checker: Checker, before: str, after: str, kinds: tuple[str, ...]) -> tuple[str, str] | None:
    tail = sentence_tail(without_refusals(before[-JOIN_CHARS:], checker.refusals))
    if not kinds or not tail.strip() or not after:
        return None
    return next((found for head in sentence_heads(without_refusals(after[:JOIN_CHARS], checker.refusals))
                 if (found := crossing(checker, tail, head, kinds))), None)


def split_across(checker: Checker, befores, afters) -> tuple[str, str] | None:
    minors = checker.kinds_only(frozenset({MINORS_KIND}))
    fragment_kinds = checker.kinds_without(HELP_KINDS | {MINORS_KIND})
    pairs = [(before, after) for before in dict.fromkeys(befores) if before for after in dict.fromkeys(afters) if after]
    return next((found for before, after in pairs
                 if (found := (minors and crosses_into(checker, before[-JOIN_CHARS:], after[:JOIN_CHARS], minors))
                     or joined_across(checker, before, after, fragment_kinds))), None)


def declined_split(checker: Checker, parts: SaidParts) -> tuple[str, str] | None:
    return split_across(checker, [parts.standing], [parts.caller, parts.tools]) or \
        split_across(checker, [parts.caller], [parts.tools])


def declined_turn(checker: Checker, said: str, parts: SaidParts) -> tuple[str, str] | None:
    return (checker.declined(parts.caller, checker.kinds_only(HELP_KINDS))
            or checker.declined(said, checker.kinds_without(CALLER_ONLY_KINDS))
            or checker.declined(parts.caller, checker.kinds_only(CALLER_ONLY_KINDS - HELP_KINDS))
            or declined_orders(checker, parts.standing)
            or declined_orders(checker, parts.offered_tools)
            or declined_split(checker, parts))


def is_a_full_answer(answer: str) -> bool:
    return len(NOT_MEANINGFUL.sub("", answer or "")) >= ANSWER_MIN_CHARS


def is_any_answer(answer: str) -> bool:
    return bool(NOT_MEANINGFUL.sub("", answer or ""))


def exchange_declined(checker: Checker, asked: str, answer: str, newer: str, newest: str, waiting: bool,
                      briefly_answered: bool) -> bool:
    if is_refusal(answer, checker.refusals) or checker.declined(asked, checker.kinds_without(HELP_KINDS)) \
            or declined_orders(checker, asked) or checker.declined(answer, checker.kinds_only(ANSWER_KINDS)):
        return True
    if waiting:
        return bool(split_across(checker, [asked, answer], [newer, newest]))
    fragment_kinds = checker.kinds_without(HELP_KINDS | {MINORS_KIND})
    afters = (newer, newest) if briefly_answered else (newer,)
    return any(joined_across(checker, before, after, fragment_kinds)
               for before in (asked, answer) if before for after in dict.fromkeys(afters))


@dataclass(frozen=True)
class HistoryVerdict:
    left_out: list[int]
    unchecked: list[int]


def history_verdict(checker: Checker, newest: str, answered: list[tuple[str, str]]) -> HistoryVerdict:
    left_out, newer = [], newest
    waiting, briefly_answered = len(waiting_asks(answered, is_any_answer)), len(waiting_asks(answered))
    for index, (asked, answer) in enumerate(answered):
        try:
            declined = exchange_declined(checker, asked, answer, newer, newest, index < waiting, index < briefly_answered)
        except CheckRanOut:
            return HistoryVerdict(left_out, list(range(index, len(answered))))
        if declined:
            left_out.append(index)
        else:
            newer = asked
    return HistoryVerdict(left_out, [])


def waiting_asks(answered: list[tuple[str, str]], answers=is_a_full_answer) -> list[str]:
    waiting = []
    for asked, answer in answered:
        if answers(answer):
            break
        waiting.append(asked)
    return waiting


def context_before(earlier: str, oldest_waiting: str) -> str:
    lines = oldest_waiting.split("\n")
    for dropped in range(min(len(lines), MAX_CONTEXT_LINES_DROPPED)):
        marker = "\n".join(lines[dropped:])
        if marker.strip() and (at := earlier.rfind(marker)) >= 0:
            return earlier[:at].rstrip("\n")
    return earlier


def unanswered_requests(said: str, answered: list[tuple[str, str]], earlier: str) -> list[tuple[str, str]]:
    waiting = waiting_asks(answered)[:REJUDGED_REQUESTS]
    return [(said, earlier), *((asked, context_before(earlier, asked) if asked.strip() else "") for asked in waiting)]


@dataclass(frozen=True)
class KeptVerdict:
    judge: Callable
    verdict: tuple[str, str] | None


def is_a_real_verdict(verdict: tuple[str, str] | None) -> bool:
    return verdict is None or verdict[1] != content_screen.CANT_CHECK_REPLY


class JudgedRequests:
    def __init__(self, kept: int):
        self.kept = kept
        self.verdicts: OrderedDict[bytes, KeptVerdict] = OrderedDict()
        self.lock = threading.Lock()

    def verdict(self, config: dict, model: str, said: str, earlier: str) -> tuple[str, str] | None:
        key = hashlib.sha256(json.dumps([model, content_rules.is_unrestricted(config, model), said, earlier]).encode()).digest()
        judge = screen_review.closer_look
        with self.lock:
            kept = self.verdicts.get(key)
            if kept is not None and kept.judge is judge:
                self.verdicts.move_to_end(key)
                return kept.verdict
        verdict = screen_review.declined(config, model, said, earlier)
        if not is_a_real_verdict(verdict):
            return verdict
        with self.lock:
            self.verdicts[key] = KeptVerdict(judge, verdict)
            self.verdicts.move_to_end(key)
            while len(self.verdicts) > self.kept:
                self.verdicts.popitem(last=False)
        return verdict


judged_requests = JudgedRequests(VERDICTS_KEPT)


def unanswered_verdict(checker: Checker, config: dict, model: str,
                       requests: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, list[int]]:
    for index, (said, earlier) in enumerate(requests):
        if index:
            try:
                checker.check_time()
            except CheckRanOut:
                return None, list(range(index - 1, len(requests) - 1))
        if said.strip() and (found := judged_requests.verdict(config, model, said, earlier)):
            return found, []
    return None, []


def rules_for(config: dict, request: dict) -> dict:
    model, said, parts, answered, context, starfire_allows_unrestricted = checked_request(request)
    unrestricted = content_rules.is_unrestricted(config, model) and starfire_allows_unrestricted
    ruled = {**config, "model": model, content_rules.UNRESTRICTED_MODELS: [EVERY_MODEL] if unrestricted else []}
    kinds = content_rules.safety_checks(ruled, model)
    earlier = content_screen.earlier_text([context])
    closer_look = {**ruled, content_rules.UNRESTRICTED_MODELS: [EVERY_MODEL]} if request.get("unrestricted") is True else ruled
    checker = Checker(config.get("crisis_help"), kinds, earlier, refusal_texts(config))
    declined = declined_turn(checker, said, parts)
    history = history_verdict(checker, parts.caller, answered) if answered and not declined else HistoryVerdict([], [])
    if not declined:
        declined, unjudged = unanswered_verdict(checker, closer_look, model, unanswered_requests(said, answered, earlier))
        history = HistoryVerdict(history.left_out, sorted({*history.unchecked, *unjudged}))
    return {"system": system_prompt(ruled, STARFIRE_LIMITS), "declined": declined[1] if declined else None,
            "declined_kind": declined[0] if declined else None, "safety_rules": not unrestricted,
            "approvals": content_rules.approvals_apply(ruled, model),
            "left_out": sorted({*history.left_out, *history.unchecked}), "unchecked": history.unchecked}
