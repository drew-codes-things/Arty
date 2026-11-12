"""Calls and texts: Arty can't place them itself, but the Arty phone app can open them, ready for the user to press
call or send. The reply to the phone app ends with a <phone-action> line the app acts on and hides: it finds the
contact on the phone, then opens the dialler, or the messaging app with the text filled in. Nothing is ever placed
or sent without the user's own tap. Other apps can't, so they get an honest pointer to the phone app instead.
"""
import json
import re

from quick import ASKING

PHONE_CLIENT = "phone"
PHONE_CLIENTS = {PHONE_CLIENT, "ios"}
WHO = r"(?P<who>[\w'+() -]{1,40}?)"
CALL = re.compile(ASKING + r"(?:call|ring|phone|facetime)\s+(?!me\b|it\b|that\b|this\b|them\b|those\b|him\b|her\b)" + WHO +
                  r"(?:\s+(?:now|please|for me|back))*\W*$", re.IGNORECASE)
# "text Sam saying I'll be late", "text my mum that I'm on my way", "message Jake: running late", "text Sam"
TEXT = re.compile(ASKING + r"(?:text|sms|message)\s+(?!me\b|them\b|those\b|him\b|her\b)" + WHO +
                  r"(?:(?:\s+(?:saying|to say|and say|and tell (?:him|her|them)|that)\s*:?\s*|\s*[:,]\s*)(?P<body>.+?))?[\s.!]*$",
                  re.IGNORECASE | re.DOTALL)
NOT_A_PERSON = re.compile(
    r"\b(?:of|too|battery|percentage|level|size|settings?|case|charger|screen|storage|wallpaper|signal|camera|app|api|"
    r"function|method|endpoint|script|result|button|doorbell|bell|tone|log|history|day)\b|\band (?:show|tell|give|then|see|check|let|print)\b",
    re.IGNORECASE)
RELATIONS = {"mum", "mam", "mom", "mummy", "dad", "daddy", "nan", "nana", "nanna", "gran", "granny", "grandma", "grandad",
             "grandpa", "brother", "sister", "bro", "sis", "wife", "husband", "partner", "boyfriend", "girlfriend", "son",
             "daughter", "auntie", "aunty", "aunt", "uncle", "cousin", "boss", "doctor", "doctors", "dentist", "gp", "vet",
             "mate", "bestie", "babe", "fiance", "fiancee", "nephew", "niece", "stepmum", "stepdad", "work", "office",
             "school", "college", "landlord", "plumber", "garage", "surgery", "teacher", "tutor", "manager", "parents", "family"}
PHONE_NUMBER = re.compile(r"\+?[\d ()-]{5,}")
ACTION_LINE = "<phone-action>{}</phone-action>"
ACTION = re.compile(r"\s*<phone-action>.*?</phone-action>\s*", re.DOTALL)


def is_name(word: str) -> bool:
    return word[:1].isupper() and word.lower() not in RELATIONS


def sounds_like_a_contact(who: str) -> bool:
    who = who.strip()
    if PHONE_NUMBER.fullmatch(who):
        return True
    if re.match(r"(?:my|the)\s", who, re.IGNORECASE):
        return True
    words = [word for word in re.split(r"\s+(?:and|&)\s+|\s+", who) if word]
    return bool(words) and (all(word.lower() in RELATIONS for word in words) or is_name(words[0]))


def from_request(text: str) -> dict | None:
    """{"kind": "call" or "text", "who": ..., "body": ...} for a request to call or text someone, or None."""
    text = (text or "").strip()
    found, kind = TEXT.match(text), "text"
    if not found:
        found, kind = CALL.match(text), "call"
    if not found or NOT_A_PERSON.search(found.group("who")) or not sounds_like_a_contact(found.group("who")):
        return None
    who = re.sub(r"^(my|the)\s+", "", found.group("who").strip(), flags=re.IGNORECASE)
    who = who[:1].upper() + who[1:]  # "mum" reads as a name: "Mum" (contacts are matched ignoring case)
    body = (found.groupdict().get("body") or "").strip()
    return {"kind": kind, "who": who, "body": body[:1].upper() + body[1:]}


def reply(action: dict, client: str) -> str:
    """What Arty says: on the phone app, with the action for it to open; elsewhere, where to ask instead."""
    who, body = action["who"], action["body"]
    if client not in PHONE_CLIENTS:
        what = f"call {who}" if action["kind"] == "call" else f"text {who}"
        return (f"I can't make calls or send texts from here, but ask me to {what} in the Arty phone app and I'll open "
                "it there, ready for you to press.")
    line = ACTION_LINE.format(json.dumps(action, ensure_ascii=False))
    if action["kind"] == "call":
        return f"Opening a call to {who}. Check it's the right number, then press call.\n\n{line}"
    if body:
        return f"Here's a text to {who}. Check it, then press send:\n\n> {body}\n\n{line}"
    return f"Opening a text to {who} for you to write.\n\n{line}"


def without_action(text: str) -> str:
    """The reply as people see it: for the conversation archive, notifications and other apps."""
    return ACTION.sub("\n", text or "").strip()
