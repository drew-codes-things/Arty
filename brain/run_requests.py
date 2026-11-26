import logging
import re
import time

import engine_guard
import honesty
from steering import RUN_WORD, with_plain_quotes

log = logging.getLogger("brain")

RUN_REQUEST_PROMPT = """Arty is an assistant with its own computer, where it can run code it writes. %sA user sent Arty this message:
<<<
%s
>>>

Does the user want Arty to run the code on Arty's own computer in this reply, and show what really happened?
Say "yes" for any wording that asks for a run, a test, a check that it works, or the real output or result, also when it is phrased as a wish, a question about whether Arty can, or permission.
Say "no" only when they want just the code, will run it themselves or elsewhere, ask how or where it could run, want to approve it first, forbid running it, or when running is only something the code itself does.
Answer only with JSON: {"run": "yes" or "no"}"""
AFTER_CODE_CONTEXT = "Arty's previous reply to this user contained code. "
RUN_REQUEST_TOKENS = 20
RUN_REQUEST_SECONDS = 90
RUN_REQUEST_HEAD_CHARS = 1500
RUN_REQUEST_TAIL_CHARS = 1500
RUN_REQUEST_MIDDLE_CHARS = 1500
CUT_MARK = "\n[...]\n"
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
FENCED_CODE = re.compile(r"```.*?(?:```|$)", re.S)
CODE_FENCE = "```"
ASSISTANT = "assistant"
USER = "user"
APPROVAL_WORDS = r"(?:ok|okay|approval|go-ahead|say-so|permission|review|sign-off|word|green light|signal)"
RUN_VERB = r"(?:run|execute|test|try)"
RUN_OBJECT = r"(?:it|this|that|them|the (?:code|script|program|file))"
APPROVING = (r"(?:\s+(?:say|tell you|confirm|approve|ok|okay|review|look|check|agree|decide|sign off|read (?:it|through it)|"
             r"give (?:you )?(?:the|my|our) (?:go-ahead|ok|okay|word|green light|nod))|"
             r"(?:'ve|\s+have)\s+(?:had a look|seen|read|reviewed|checked|approved|confirmed|looked|signed off|given)|"
             r"(?:'m|'re|\s+am|\s+are)\s+ready)\b")
WAITS_FOR_THE_USER = re.compile(
    rf"\b{RUN_VERB}\w*\s+{RUN_OBJECT}\s+(?:\w+\s+){{0,2}}?(?:once|after|until|till|when|whenever|as soon as|only if)\s+"
    rf"(?:i|we){APPROVING}|"
    rf"\b(?:after|pending|until|awaiting) (?:my|our) {APPROVAL_WORDS}\b|"
    r"\bcheck with (?:me|us)\b|\bask (?:me|us) (?:first|before)\b|"
    rf"\bhold off(?: on)? {RUN_VERB}\w*|"
    rf"\bwait (?:for (?:me|us|(?:my|our|the) {APPROVAL_WORDS})|until (?:i|we)\b)|"
    r"\bonly (?:after|once|when|if) (?:i|we|my|our)\b|"
    rf"\b(?:i|we)(?:'ll|\s+will)?\s+decide\s+(?:whether|if|when)\s+(?:you\s+|to\s+)?{RUN_VERB}|"
    r"\b(?:i|we)(?:'ll|\s+will)\s+(?:tell|let)\s+you\s+(?:know\s+)?when\b|"
    rf"\bnot yet\b[\s,.;:!-]*(?:please\s+)?(?:don't|do not)\s+{RUN_VERB}\b",
    re.I)
WAITS_THEN_RUN = re.compile(
    rf"^\s*(?:once|after|when|whenever|as soon as)\s+(?:i|we){APPROVING}(?:\s+[\w']+){{0,3}}\s*,\s*(?:then\s+)?(?:please\s+)?"
    rf"{RUN_VERB}\b", re.I)
REFUSES_RUNNING = re.compile(
    rf"\b(?:don't|do not|never|no need to|(?:you )?(?:don't|do not) (?:have|need) to)\s+(?:actually\s+|even\s+)?{RUN_VERB}\w*\s+"
    rf"(?:{RUN_OBJECT}|anything)\b|"
    rf"\b(?:let me|i'll|i will|i'm going to|i am going to)\s+(?:[\w']+\s+)?{RUN_VERB}\s+(?:it|this|that|them)\b|"
    r"\brun nothing\b|\bleave the running to me\b|\brunning it is my job\b|\bjust the code\b|"
    r"\bnot (?:on|in) your (?:computer|machine|workspace|desktop)\b",
    re.I)
ASKS_ARTY_TO_RUN = re.compile(
    rf"(?:^|[.!?;,:]\s*|\b(?:and|so|then|please|just|now|first|but|also)\s+)(?:please\s+|just\s+|now\s+|go ahead and\s+)*"
    rf"(?P<verb>{RUN_VERB})\s+{RUN_OBJECT}\b|"
    rf"\byou\s+(?:can\s+|could\s+|should\s+|may\s+)?(?P<asked>{RUN_VERB})\s+(?:it|this|that)\b|"
    rf"\b(?P<here>{RUN_VERB})\s+(?:it|this|that)\s+here\b",
    re.I | re.M)
REVIEWS_IT_FIRST = re.compile(r"\blet (?:me|us) (?:review|check|look at|read|see) (?:it|this|that|the code|the script)\s+first\b", re.I)
RUNS_ELSEWHERE = re.compile(
    r"\b(?:run|runs|running|execute|executing|test|testing|try|trying)\s+(?:it|this|that|them|the (?:code|script|program))\s+"
    r"(?:\w+\s+){0,2}?(?:on|in|with|under|using)\s+(?:"
    r"(?:my|another|a different|the other|our|his|her|their)\s+(?:own\s+)?(?:\w+\s+)?"
    r"(?:device|machine|computer|laptop|server|pc|mac|phone|pi|box)|"
    r"an?\s+(?:raspberry pi|pi|mac|phone|arduino|windows (?:pc|machine|laptop))|python ?2(?:\.\d+)?)\b", re.I)


def outside_code(text: str) -> str:
    return FENCED_CODE.sub(" ", with_plain_quotes(text))


def waits_for_the_user(prose: str) -> bool:
    if REVIEWS_IT_FIRST.search(prose):
        return True
    return any(WAITS_FOR_THE_USER.search(sentence) or WAITS_THEN_RUN.search(sentence) for sentence in SENTENCE.findall(prose))


def run_verbs_asked_of_arty(prose: str) -> list[int]:
    return [match.start(match.lastgroup) for match in ASKS_ARTY_TO_RUN.finditer(prose)]


def asked_outside(prose: str, pattern: re.Pattern) -> bool:
    spans = [match.span() for match in pattern.finditer(prose)]
    return any(not any(start <= verb < end for start, end in spans) for verb in run_verbs_asked_of_arty(prose))


def runs_only_elsewhere(prose: str) -> bool:
    return bool(RUNS_ELSEWHERE.search(prose)) and not asked_outside(prose, RUNS_ELSEWHERE)


def refuses_running(prose: str) -> bool:
    return bool(REFUSES_RUNNING.search(prose)) and not asked_outside(prose, REFUSES_RUNNING)


def defers_running(text: str) -> bool:
    prose = outside_code(text)
    return waits_for_the_user(prose) or runs_only_elsewhere(prose) or refuses_running(prose)


def mentions_running(text: str) -> bool:
    return bool(RUN_WORD.search(outside_code(text)))


def middle_run_sentences(middle: str) -> str:
    found = [sentence.strip() for sentence in SENTENCE.findall(middle) if RUN_WORD.search(sentence)]
    return " ".join(found)[:RUN_REQUEST_MIDDLE_CHARS]


def judged_part(text: str) -> str:
    if len(text) <= RUN_REQUEST_HEAD_CHARS + RUN_REQUEST_TAIL_CHARS:
        return text
    middle = middle_run_sentences(text[RUN_REQUEST_HEAD_CHARS:-RUN_REQUEST_TAIL_CHARS])
    kept = CUT_MARK + middle if middle else ""
    return text[:RUN_REQUEST_HEAD_CHARS] + kept + CUT_MARK + text[-RUN_REQUEST_TAIL_CHARS:]


def judge_says_run(text: str, after_code: bool = False, model: str = "", keep_alive=None) -> bool | None:
    started = time.monotonic()
    context = AFTER_CODE_CONTEXT if after_code else ""
    try:
        verdict = honesty.ask_judge(RUN_REQUEST_PROMPT % (context, judged_part(text)), RUN_REQUEST_TOKENS, model=model,
                                    keep_alive=keep_alive, seconds=RUN_REQUEST_SECONDS).get("run")
    except engine_guard.TurnStopped:
        raise
    except Exception as error:
        log.warning("run request check failed after %.1f s, answering with code only: %r", time.monotonic() - started, error)
        return None
    if not isinstance(verdict, str) or verdict.strip().lower() not in ("yes", "no"):
        log.warning("run request check gave %r, answering with code only", verdict)
        return None
    log.info("run request check said %s in %.1f s", verdict.strip().lower(), time.monotonic() - started)
    return verdict.strip().lower() == "yes"


def last_reply_before_the_request(messages: list) -> str:
    roles = [message.get("role") for message in messages]
    if USER not in roles:
        return ""
    newest_request = len(roles) - 1 - roles[::-1].index(USER)
    for message in reversed(messages[:newest_request]):
        content = message.get("content")
        if message.get("role") == USER:
            return ""
        if message.get("role") == ASSISTANT and isinstance(content, str) and content.strip():
            return content
    return ""


def follows_code(messages: list) -> bool:
    return CODE_FENCE in last_reply_before_the_request(messages)
