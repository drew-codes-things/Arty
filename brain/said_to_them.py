import re

from phone import COMMON_APPS, RELATIONS
from quick import refers_back

ASKED = r"(?:^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please\s+)?|\b(?:can|could|would|will) you\s+(?:please\s+)?|\bplease\s+)"
TELL = re.compile(ASKED + r"(?P<verb>tell|say to|let|text|message|sms)\s+(?P<rest>\S.*)$", re.IGNORECASE | re.DOTALL)
TEXT_VERBS = {"text", "message", "sms"}
SAY_TO = re.compile(ASKED + r"say\s+(?P<body>[\w'!-][\w' !-]{0,59}?)\s+to\s+(?P<rest>\S.*)$", re.IGNORECASE | re.DOTALL)
SAY_TO_END = " \t\r\n.!"
MOST_TOLD_CHARS = 300
TOLD_PERSON = re.compile(r"\s*(?P<my>(?:my|our)\s+)?(?P<name>[^\W\d_][\w'’-]*)", re.IGNORECASE)
PEOPLE_JOINER = re.compile(r"\s*(?:,|&|\band\b)", re.IGNORECASE)
KNOW = re.compile(r"\s+know\b", re.IGNORECASE)
BY_TEXT_WORDS = r"\b(?:by|via|over|in|as|on|with)\s+(?:a\s+)?(?:text|sms|message|whatsapp)\b"
BY_TEXT = re.compile(BY_TEXT_WORDS, re.IGNORECASE)
BY_TEXT_AT_END = re.compile(BY_TEXT_WORDS + r"$", re.IGNORECASE)
LEADING_COPULA = re.compile(r"^\s*(?:are|is|and)\s+(?!(?:you|I|we|they|he|she|say|tell|that|saying|let)\b)", re.IGNORECASE)
BODY_START = re.compile(r"^\s*(?:[:,-]\s*|(?:and\s+)?(?:that|saying|to say|say|tell (?:him|her|them)(?: that)?|I said)\b\s*)?",
                        re.IGNORECASE)
NEVER_TOLD = {"i", "i'm", "i'll", "i've", "i'd", "me", "us", "you", "them", "him", "her", "it", "the", "a", "an", "this", "that",
              "what", "when", "where", "who", "why", "how", "if", "whether", "everyone", "everybody", "anyone", "people",
              "arty", "siri", "alexa", "google", "cortana", "bixby", "gemini", "chatgpt", "copilot", "python", "java",
              "javascript", "excel", "word", "windows", "linux", "chrome", "firefox", "monday", "tuesday", "wednesday",
              "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december", "again"}
THIRD_PERSON = [
    (r"\b(?:he|she|they)(?:'s| is| are|'re)\b", "you're"),
    (r"\b(?:he|she) was\b|\bthey were\b", "you were"),
    (r"\b(?:he|she)(?:'s| has) got\b|\bthey(?:'ve| have) got\b", "you've got"),
    (r"\b(?:he|she) has\b|\bthey have\b", "you have"),
    (r"\b(?:he|she) doesn't\b|\bthey don't\b", "you don't"),
    (r"\b(?:he|she|they)'ll\b", "you'll"),
    (r"\b(?:he|she|they)'d\b", "you'd"),
    (r"\bthey've\b", "you've"),
    (r"\b(?:himself|herself)\b", "yourself"),
    (r"\bthemselves\b", "yourselves"),
    (r"\b(?:his|their)\b", "your"),
    (r"\bher\b(?=\s+(?!and\b|to\b|for\b|so\b|too\b|a\b|very\b|the\b|that\b|now\b|today\b|tonight\b)[a-z])", "your"),
    (r"\b(?:him|her|them)\b", "you"),
]
SUBJECT_AND_VERB = re.compile(r"\b(?:he|she|they)\s+(?P<verb>[a-z']+)", re.IGNORECASE)
IRREGULAR_VERBS = {"does": "do", "goes": "go", "has": "have", "is": "are", "was": "were"}
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I'll|I've|I'd|me|my|mine|myself)\b")
STAYS_LOWER = {"you", "you're", "your", "you've", "you'll", "you'd", "happy", "good", "well", "thanks", "thank",
               "congrats", "congratulations", "hi", "hello", "hey", "goodnight", "goodbye", "bye", "dinner", "lunch",
               "breakfast", "tea", "sorry", "welcome", "great", "nice", "the", "it's", "it", "we", "we're", "come", "don't",
               "please", "love", "see", "have", "merry", "cheers"}
ENDS_A_SENTENCE = (".", "!", "?")
FROM_THE_USER = "{names}, {user} says: \"{words}\""
FROM_SOMEONE = "{names}, here's a message for you: \"{words}\""
TEXT_INSTEAD_ONE = "If {first} isn't with you, say \"text {first}\" and I'll get a text ready."
TEXT_INSTEAD_SEVERAL = "If they aren't with you, say \"text {first}\" and I'll get a text ready."


def is_told_person(word: str, after_my: bool) -> bool:
    lowered = word.lower().replace("’", "'")
    if lowered in RELATIONS:
        return True
    is_possessive = lowered.endswith("'s")
    return not after_my and word[:1].isupper() and lowered not in NEVER_TOLD and lowered not in COMMON_APPS and not is_possessive


def told_people(rest: str) -> tuple[list[str], int]:
    people, position = [], 0
    while True:
        start = position
        if people:
            joiner = PEOPLE_JOINER.match(rest, position)
            if not joiner:
                break
            start = joiner.end()
        found = TOLD_PERSON.match(rest, start)
        if not found or not is_told_person(found.group("name"), bool(found.group("my"))):
            break
        people.append(found.group("name"))
        position = found.end()
    return people, position


def as_names(people: list[str]) -> list[str]:
    return [person[:1].upper() + person[1:] for person in people]


def tidy_body(raw: str) -> tuple[str, bool]:
    by_text = bool(BY_TEXT.match(raw.lstrip())) or bool(BY_TEXT_AT_END.search(raw.rstrip(SAY_TO_END)))
    raw = BY_TEXT.sub("", raw, count=1) if by_text else raw
    copula = "you're " if LEADING_COPULA.match(raw) else ""
    body = BODY_START.sub("", LEADING_COPULA.sub("", raw, count=1), count=1).strip()
    return (copula + body).strip(), by_text


def say_to_request(text: str) -> dict | None:
    found = SAY_TO.search(text)
    if not found:
        return None
    rest = found.group("rest").rstrip(SAY_TO_END)
    people, told_end = told_people(rest)
    if not people or rest[told_end:].strip():
        return None
    return {"people": as_names(people), "body": found.group("body").strip(), "by_text": False}


def tell_request(text: str) -> dict | None:
    text = (text or "").strip()
    if len(text) > MOST_TOLD_CHARS:
        return None
    found = TELL.search(text)
    if not found:
        return say_to_request(text)
    rest = found.group("rest")
    people, told_end = told_people(rest)
    if not people:
        return None
    if found.group("verb").lower() == "let":
        knows = KNOW.match(rest, told_end)
        if not knows:
            return None
        told_end = knows.end()
    body_start = found.start("rest") + told_end
    if refers_back(text[:body_start]):
        return None
    body, by_text = tidy_body(text[body_start:])
    by_text = by_text or found.group("verb").lower() in TEXT_VERBS
    return {"people": as_names(people), "body": body.rstrip(" "), "by_text": by_text}


def with_verb_agreeing(found) -> str:
    verb = found.group("verb").lower()
    if verb in IRREGULAR_VERBS:
        return "you " + IRREGULAR_VERBS[verb]
    if re.search(r"(?:sh|ch|x|ss|o)es$", verb):
        return "you " + verb[:-2]
    if verb.endswith("ies"):
        return "you " + verb[:-3] + "y"
    if verb.endswith("s") and not verb.endswith("ss"):
        return "you " + verb[:-1]
    return "you " + verb


def in_second_person(words: str, several: bool) -> str:
    for pattern, replacement in THIRD_PERSON:
        if replacement == "yourself" and several:
            replacement = "yourselves"
        words = re.sub(pattern, replacement, words, flags=re.IGNORECASE)
    return SUBJECT_AND_VERB.sub(with_verb_agreeing, words)


def joined_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def as_said(words: str) -> str:
    first_word = words.split(" ", 1)[0].lower()
    starts_plainly = first_word in STAYS_LOWER or words.lower().startswith("well done")
    words = words[:1].lower() + words[1:] if starts_plainly else words
    return words if words.endswith(ENDS_A_SENTENCE) else f"{words}!"


def addressed(told: dict, user_name: str = "") -> str:
    names = joined_names(told["people"])
    words = in_second_person(told["body"], len(told["people"]) > 1)
    if not words:
        return f"{names}, hello!"
    if FIRST_PERSON.search(words):
        quoted = words[:1].upper() + words[1:]
        quoted = quoted if quoted.endswith(ENDS_A_SENTENCE) else f"{quoted}."
        template = FROM_THE_USER if user_name else FROM_SOMEONE
        return template.format(names=names, user=user_name, words=quoted)
    return f"{names}, {as_said(words)}"


def text_body(told: dict) -> str:
    words = in_second_person(told["body"], len(told["people"]) > 1)
    return words[:1].upper() + words[1:]


def text_offer(told: dict) -> str:
    template = TEXT_INSTEAD_SEVERAL if len(told["people"]) > 1 else TEXT_INSTEAD_ONE
    return template.format(first=told["people"][0])
