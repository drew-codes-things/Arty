import re

WAKE_PHRASE = re.compile(
    r"\b(?:hey|hay|hi|hiya|ok|okay)[\s,.!?-]*(?:arty|artie|arti|ardy|hearty|harty|r\.?\s?t)\b[\s,.!?-]*",
    re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]", re.IGNORECASE)
SPOKEN_WORD = re.compile(r"[a-z0-9']+", re.IGNORECASE)
FILLER_WORDS = {"oh", "ah", "aw", "eh", "um", "umm", "uh", "er", "erm", "hmm", "mm", "huh", "wait", "wow", "lol", "ooh",
                "my", "god", "gosh", "up", "what", "ow", "ouch", "yay", "whoa", "oops", "shh", "the", "a", "and"}
LAUGH = re.compile(r"^(?:ha|he|ah)+h?$", re.IGNORECASE)
BARE_NAME = re.compile(r"^\W*(?:arty|artie|arti|ardy|harty|r\.?\s?t)\b[\s,.!?-]*", re.IGNORECASE)
SWITCH_PHRASE = re.compile(
    r"^\W*(?:(?:hey|hay|hi|ok|okay)[\s,.!?-]*)?(?:arty|artie|arti|ardy|hearty|harty|r\.?\s?t)[\s,.!?-]+"
    r"(?:turn[\s-]+)?(?P<state>on|off|of)\W*$",
    re.IGNORECASE)


def find_wake(text: str, bare_name: bool = False) -> tuple[bool, str]:
    match = WAKE_PHRASE.search(text) or (BARE_NAME.match(text) if bare_name else None)
    if not match:
        return False, ""
    return True, text[match.end():].strip()


def has_words(text: str) -> bool:
    return len(WORD.findall(text)) >= 2


def find_switch(text: str) -> bool | None:
    match = SWITCH_PHRASE.match(text)
    if not match:
        return None
    return match.group("state").lower() == "on"


def is_filler(text: str) -> bool:
    words = [word.lower().strip("'") for word in SPOKEN_WORD.findall(text)]
    return all(word in FILLER_WORDS or LAUGH.match(word) for word in words)
