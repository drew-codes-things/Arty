"""Calls and texts: Arty can't place them itself, but the Arty phone app can open them, ready for the user to press
call or send. The reply to the phone app ends with a <phone-action> line the app acts on and hides: it finds the
contact on the phone, then opens the dialler, or the messaging app with the text filled in. Nothing is ever placed
or sent without the user's own tap. Other apps can't, so they get an honest pointer to the phone app instead.
"""
import json
import re

from quick import ASKING

PHONE_CLIENT = "phone"
WHO = r"(?P<who>[\w'+() -]{1,40}?)"
# "call my mum", "ring 07700 900123", "phone Sam back" (not "call me Drew": that's naming)
CALL = re.compile(ASKING + r"(?:call|ring|phone|facetime)\s+(?!me\b|it\b|that\b|this\b)" + WHO +
                  r"(?:\s+(?:now|please|for me|back))*\W*$", re.IGNORECASE)
# "text Sam saying I'll be late", "text my mum that I'm on my way", "message Jake: running late", "text Sam"
TEXT = re.compile(ASKING + r"(?:text|sms|message)\s+(?!me\b)" + WHO +
                  r"(?:(?:\s+(?:saying|to say|and say|and tell (?:him|her|them)|that)\s*:?\s*|\s*[:,]\s*)(?P<body>.+?))?[\s.!]*$",
                  re.IGNORECASE | re.DOTALL)
ACTION_LINE = "<phone-action>{}</phone-action>"
ACTION = re.compile(r"\s*<phone-action>.*?</phone-action>\s*", re.DOTALL)


def from_request(text: str) -> dict | None:
    """{"kind": "call" or "text", "who": ..., "body": ...} for a request to call or text someone, or None."""
    text = (text or "").strip()
    found, kind = TEXT.match(text), "text"
    if not found:
        found, kind = CALL.match(text), "call"
    if not found:
        return None
    who = re.sub(r"^(my|the)\s+", "", found.group("who").strip(), flags=re.IGNORECASE)
    who = who[:1].upper() + who[1:]  # "mum" reads as a name: "Mum" (contacts are matched ignoring case)
    body = (found.groupdict().get("body") or "").strip()
    return {"kind": kind, "who": who, "body": body[:1].upper() + body[1:]}


def reply(action: dict, client: str) -> str:
    """What Arty says: on the phone app, with the action for it to open; elsewhere, where to ask instead."""
    who, body = action["who"], action["body"]
    if client != PHONE_CLIENT:
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
