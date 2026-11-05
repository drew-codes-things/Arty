import logging
import re
from datetime import datetime
from urllib.parse import quote

import aiohttp

DICTIONARY_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/"
DICTIONARY_TIMEOUT_SECONDS = 6
MEANINGS_SPOKEN = 2
MOST_WORDS_TO_DEFINE = 3

FILLER = r"(?:hey |ok |okay |so |um |please |can you |could you |would you |will you |just |arty,? )*"
END = r"[\s.!?,]*(?:please)?[\s.!?]*$"
TERM = r"[\"'“‘]?(?:the (?:word|phrase) )?[\"'“‘]?(?P<term>[\w' -]+?)[\"'”’]?"

TIME_QUESTIONS = re.compile(
    rf"^{FILLER}(?:what(?:'s| is) the time|what time is it|tell me the time|(?:the )?time(?: now)?|"
    rf"what(?:'s| is) the time (?:now|right now)|what time is it (?:now|right now))(?: please)?{END}",
    re.IGNORECASE)
DATE_QUESTIONS = re.compile(
    rf"^{FILLER}(?:what(?:'s| is) (?:the date|today's date|the date today)|what date is it(?: today)?|"
    rf"tell me the date|(?:the )?date(?: today)?){END}", re.IGNORECASE)
DAY_QUESTIONS = re.compile(rf"^{FILLER}what day is it(?: today)?{END}", re.IGNORECASE)
DEFINE_QUESTIONS = [
    re.compile(rf"^{FILLER}what does {TERM} mean{END}", re.IGNORECASE),
    re.compile(rf"^{FILLER}what(?:'s| is) the (?:meaning|definition) of {TERM}{END}", re.IGNORECASE),
    re.compile(rf"^{FILLER}(?:the )?(?:meaning|definition) of {TERM}{END}", re.IGNORECASE),
    re.compile(rf"^{FILLER}(?:define|look up the meaning of|look up the word) {TERM}{END}", re.IGNORECASE),
]
SEARCH_REQUESTS = re.compile(
    rf"^{FILLER}(?:search(?: up| for| the web for| online for| the internet for)?|look up|google|"
    rf"find out(?: about)?)\s+(?P<query>.+?){END}", re.IGNORECASE)

log = logging.getLogger("arty-voice.quick")


def clock_time(now: datetime) -> str:
    hour = now.hour % 12 or 12
    return f"{hour}:{now.minute:02d} {'am' if now.hour < 12 else 'pm'}"


def time_answer(command: str, now: datetime) -> str | None:
    text = " ".join(command.split())
    if TIME_QUESTIONS.match(text):
        return f"It's {clock_time(now)}."
    if DATE_QUESTIONS.match(text):
        return f"It's {now.strftime('%A')} {now.day} {now.strftime('%B %Y')}."
    if DAY_QUESTIONS.match(text):
        return f"It's {now.strftime('%A')}."
    return None


def word_to_define(command: str) -> str | None:
    text = " ".join(command.split())
    for pattern in DEFINE_QUESTIONS:
        if match := pattern.match(text):
            term = match.group("term").strip(" '-")
            if term and len(term.split()) <= MOST_WORDS_TO_DEFINE:
                return term.lower()
    return None


def search_query(command: str) -> str | None:
    match = SEARCH_REQUESTS.match(" ".join(command.split()))
    if not match:
        return None
    query = match.group("query").strip(" ,.")
    return query or None


def search_question(query: str) -> str:
    return f"Search the web for {query} and tell me what you find."


def article_for(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def spoken_definition(term: str, entries: object) -> str | None:
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    sentences = []
    for meaning in entries[0].get("meanings") or []:
        if not isinstance(meaning, dict):
            continue
        definitions = [d for d in meaning.get("definitions") or [] if isinstance(d, dict) and d.get("definition")]
        if not definitions:
            continue
        part = meaning.get("partOfSpeech") or ""
        label = f"As {article_for(part)} {part}" if part else "It means"
        sentences.append(f"{label}: {definitions[0]['definition'].strip().rstrip('.')}.")
        if len(sentences) == MEANINGS_SPOKEN:
            break
    if not sentences:
        return None
    return f"{term.capitalize()}. " + " ".join(sentences)


async def look_up_word(http: aiohttp.ClientSession, term: str) -> str | None:
    timeout = aiohttp.ClientTimeout(total=DICTIONARY_TIMEOUT_SECONDS)
    try:
        async with http.get(DICTIONARY_URL + quote(term), timeout=timeout) as response:
            if response.status != 200:
                return None
            return spoken_definition(term, await response.json(content_type=None))
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        log.warning("dictionary lookup for %r failed: %s", term, error)
        return None
