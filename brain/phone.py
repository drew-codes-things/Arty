"""Calls and texts: Arty can't place them itself, but the Arty phone app can open them, ready for the user to press
call or send. The reply to the phone app ends with a <phone-action> line the app acts on and hides: it finds the
contact on the phone, then opens the dialler, or the messaging app with the text filled in. Nothing is ever placed
or sent without the user's own tap. Other apps can't, so they get an honest pointer to the phone app instead.
"""
import json
import re

from quick import ASKING

PHONE_CLIENT = "phone"
ANDROID_CLIENT = "android"
IPHONE_CLIENT = "ios"
PHONE_CLIENTS = {PHONE_CLIENT, ANDROID_CLIENT, IPHONE_CLIENT}
DEVICE_ACTION_CLIENTS = {ANDROID_CLIENT, IPHONE_CLIENT}
CAMERA_CLIENTS = {ANDROID_CLIENT, IPHONE_CLIENT}
ANDROID_FEATURES = frozenset({"device_actions", "camera"})
WHO = r"(?P<who>[\w'+() -]{1,40}?)"
CALL = re.compile(ASKING + r"(?:call|ring|phone|facetime)\s+(?!me\b|it\b|that\b|this\b|them\b|those\b|him\b|her\b)" + WHO +
                  r"(?:\s+(?:now|please|for me|back))*\W*$", re.IGNORECASE)
# "text Sam saying I'll be late", "text my mum that I'm on my way", "message Jake: running late", "text Sam"
TEXT = re.compile(ASKING + r"(?:text|sms|message|send\s+(?:a\s+|an\s+)?(?:text|sms|message)\s+to)\s+(?!me\b|them\b|those\b|him\b|her\b)"
                  + WHO +
                  r"(?:(?:\s+(?:saying|to say|and say|and tell (?:him|her|them)|that)\s*:?\s*|\s*[:,]\s*)(?P<body>.+?))?[\s.!]*$",
                  re.IGNORECASE | re.DOTALL)
SEND_TO = re.compile(ASKING + r"send\s+(?!me\b|them\b|those\b|him\b|her\b|it\b|a\b|an\b|the\b)" + WHO +
                     r"\s+(?:a|an)\s+(?:text|sms|message|whatsapp)"
                     r"(?:(?:\s+(?:saying|to say|that)\s*:?\s*|\s*[:,]\s*)(?P<body>.+?))?[\s.!]*$", re.IGNORECASE | re.DOTALL)
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
ONE_TEXT_AT_A_TIME = "I can open one text at a time, so this one is for {who}. Ask me again to text {others}."
CAMERA_KIND = "camera"
CAMERA = re.compile(
    ASKING + r"(?:(?:use|open|start)\s+(?:the\s+|my\s+)?(?:phone\s+)?camera|take\s+(?:a|another|me a)\s+(?:photo|picture|pic|photograph|snap)"
    r"|(?:look|see)\s+(?:at\s+\w+(?:\s+\w+)?\s+)?(?:through|with|using)\s+(?:the\s+|my\s+)?(?:phone\s+)?camera"
    r"|scan\s+(?:this|that|it)\s+with\s+(?:the\s+|my\s+)?camera)\b(?P<rest>[^\n]*)$", re.IGNORECASE)
CAMERA_FOLLOW_UP = re.compile(r"^\W*(?:of\s+\S+(?:\s+\S+)?\s*)?(?:and\s+(?:then\s+)?|then\s+|so\s+(?:you\s+can\s+)?)(?P<question>.{3,})$", re.IGNORECASE)
CAMERA_ELSEWHERE = ("I can only open the camera from the Arty phone app, and only when you ask. On this computer, ask me to use the "
                    "webcam in Arty Desktop, which asks you first every time.")
CAMERA_NOT_YET = ("This version of the phone app can't open the camera for me. Tap the paperclip, attach a photo, and ask me about "
                  "it: I'll look at it as soon as it's attached.")
ACTION_LINE = "<phone-action>{}</phone-action>"
ACTION = re.compile(r"\s*<phone-action>.*?</phone-action>\s*", re.DOTALL)
ACTION_TOOL_PREFIX = "phone_"
CLOCK_TIME = r"(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?:(?P<half>[ap])\.?\s*m\b\.?)?"
LABEL = r"(?:\s+(?:called|named|labelled|labeled|saying)\s+(?P<label>[^\n]{1,40}?))?"
ALARM = re.compile(ASKING + r"(?:set\s+(?:an?\s+|my\s+|the\s+)?alarm\s+(?:for\s+|at\s+)?|wake\s+me(?:\s+up)?\s+at\s+|alarm\s+(?:for|at)\s+)"
                   + CLOCK_TIME + r"(?:\s+tomorrow(?:\s+morning)?)?" + LABEL + r"[\s.!]*$", re.IGNORECASE)
DURATION = r"(?P<duration>[\w\s,-]+?)"
TIMERS = [re.compile(ASKING + start + LABEL + r"[\s.!]*$", re.IGNORECASE) for start in (
    r"(?:set|start)\s+(?:a\s+|the\s+)?timer\s+(?:for\s+)?" + DURATION,
    r"(?:set|start)\s+an?\s+" + DURATION + r"\s*timer",
    r"timer\s+(?:for\s+)?" + DURATION)]
DURATION_PIECE = re.compile(r"(\d+|an?|one|half an)\s*(?:-\s*)?(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b", re.IGNORECASE)
DURATION_GAP = re.compile(r"\s*(?:and|,)\s*|\s+", re.IGNORECASE)
LONGEST_DEVICE_REQUEST = 200
UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}
SPOKEN_AMOUNTS = {"a": 1, "an": 1, "one": 1, "half an": 0.5}
MAX_TIMER_SECONDS = 24 * 3600
HOURS_IN_HALF_DAY = 12
APP = re.compile(ASKING + r"(?P<verb>open|launch)\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<app>[\w&+'][\w&+' -]{0,39}?)(?P<suffix>\s+app)?"
                 r"(?P<where>\s+on\s+(?:my|the)\s+phone)?[\s.!]*$", re.IGNORECASE)
NOT_AN_APP = re.compile(r"\b(?:file|folder|link|page|site|website|tab|door|window|document|doc|email|emails|garage|gate|chat|"
                        r"conversation|it|that|this|them|up)\b", re.IGNORECASE)
COMMON_APPS = {"camera", "calculator", "clock", "calendar", "maps", "settings", "gallery", "photos", "contacts", "messages",
               "phone", "notes", "spotify", "youtube", "whatsapp", "chrome", "gmail", "files", "weather", "music"}


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
    found, kind = TEXT.match(text) or SEND_TO.match(text), "text"
    if not found:
        found, kind = CALL.match(text), "call"
    if not found or NOT_A_PERSON.search(found.group("who")) or not sounds_like_a_contact(found.group("who")):
        return None
    who = re.sub(r"^(my|the)\s+", "", found.group("who").strip(), flags=re.IGNORECASE)
    who = who[:1].upper() + who[1:]  # "mum" reads as a name: "Mum" (contacts are matched ignoring case)
    body = (found.groupdict().get("body") or "").strip()
    return {"kind": kind, "who": who, "body": body[:1].upper() + body[1:]}


def clock_time(found) -> tuple[int, int] | None:
    hour, minute, half = int(found.group("hour")), int(found.group("minute") or 0), (found.group("half") or "").lower()
    if half and not 1 <= hour <= HOURS_IN_HALF_DAY:
        return None
    if half:
        hour = hour % HOURS_IN_HALF_DAY + (HOURS_IN_HALF_DAY if half == "p" else 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def is_duration(text: str) -> bool:
    position = 0
    while position < len(text):
        piece = DURATION_PIECE.match(text, position)
        if not piece:
            return False
        gap = DURATION_GAP.match(text, piece.end())
        if not gap and piece.end() < len(text):
            return False
        position = gap.end() if gap else piece.end()
    return position > 0


def duration_seconds(text: str) -> int:
    total = 0.0
    for amount, unit in DURATION_PIECE.findall(text):
        number = SPOKEN_AMOUNTS.get(amount.lower()) or float(amount)
        total += number * UNIT_SECONDS[unit[0].lower()]
    return int(total)


def label_of(found) -> str:
    return (found.group("label") or "").strip()


def alarm_from(text: str) -> dict | None:
    found = ALARM.match(text)
    when = clock_time(found) if found else None
    if when is None:
        return None
    return {"kind": "alarm", "hour": when[0], "minute": when[1], "label": label_of(found)}


def timer_from(text: str) -> dict | None:
    found = next((match for pattern in TIMERS if (match := pattern.match(text)) and is_duration(match.group("duration"))), None)
    seconds = duration_seconds(found.group("duration")) if found else 0
    if not 0 < seconds <= MAX_TIMER_SECONDS:
        return None
    return {"kind": "timer", "seconds": seconds, "label": label_of(found)}


def sounds_like_an_app(found) -> bool:
    name = found.group("app").strip()
    said_it_is_an_app = bool(found.group("suffix") or found.group("where")) or found.group("verb").lower() == "launch"
    looks_like_a_name = name[:1].isupper() or name.lower() in COMMON_APPS
    return not NOT_AN_APP.search(name) and not name.isdigit() and (said_it_is_an_app or looks_like_a_name)


def app_from(text: str) -> dict | None:
    found = APP.match(text)
    if not found or not sounds_like_an_app(found):
        return None
    name = found.group("app").strip()
    return {"kind": "app", "app": name[:1].upper() + name[1:]}


def device_request(text: str, client: str) -> dict | None:
    if client not in DEVICE_ACTION_CLIENTS:
        return None
    text = (text or "").strip()
    if len(text) > LONGEST_DEVICE_REQUEST:
        return None
    found = alarm_from(text) or timer_from(text)
    if found or client == IPHONE_CLIENT:
        return found
    return app_from(text)


def timer_words(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [(hours, "hour"), (minutes, "minute"), (seconds, "second")]
    return " ".join(f"{amount} {unit}{'' if amount == 1 else 's'}" for amount, unit in parts if amount)


def device_reply(action: dict, client: str) -> str:
    if action["kind"] == "alarm":
        what = f"an alarm for {action['hour']:02d}:{action['minute']:02d}"
    elif action["kind"] == "timer":
        what = f"a {timer_words(action['seconds'])} timer"
    else:
        what = f"{action['app']}"
    if client == IPHONE_CLIENT:
        return (f"I can't set {what} from the iPhone app: Apple doesn't let a Home Screen app set alarms or timers. "
                "Ask Siri instead, or ask me to remind you.")
    line = ACTION_LINE.format(json.dumps(action, ensure_ascii=False))
    if action["kind"] == "app":
        return f"Opening {what} once you press Allow on the card.\n\n{line}"
    return f"Setting {what}. Check it on the card, then press Allow.\n\n{line}"


def reply(action: dict, client: str) -> str:
    """What Arty says: on the phone app, with the action for it to open; elsewhere, where to ask instead."""
    if action["kind"] in ("alarm", "timer", "app"):
        return device_reply(action, client)
    who, body = action["who"], action["body"]
    one_at_a_time = f"{ONE_TEXT_AT_A_TIME.format(who=who, others=' and '.join(action['others']))}\n\n" if action.get("others") else ""
    if client not in PHONE_CLIENTS:
        what = f"call {who}" if action["kind"] == "call" else f"text {who}"
        return (f"I can't make calls or send texts from here, but ask me to {what} in the Arty phone app and I'll open "
                "it there, ready for you to press.")
    line = ACTION_LINE.format(json.dumps({key: action[key] for key in ("kind", "who", "body")}, ensure_ascii=False))
    if action["kind"] == "call":
        return f"Opening a call to {who}. Check it's the right number, then press call.\n\n{line}"
    if body and client == ANDROID_CLIENT:
        return (f"Here's a text for {who}. Pick them from your contacts on the card, check it, then press Send:\n\n> {body}\n\n"
                f"{one_at_a_time}{line}")
    if body:
        return f"Here's a text to {who}. Check it, then press send:\n\n> {body}\n\n{one_at_a_time}{line}"
    return f"Opening a text to {who} for you to write.\n\n{one_at_a_time}{line}"


def without_action(text: str) -> str:
    """The reply as people see it: for the conversation archive, notifications and other apps."""
    return ACTION.sub("\n", text or "").strip()


def camera_request(text: str) -> dict | None:
    found = CAMERA.match((text or "").strip())
    if not found:
        return None
    follow_up = CAMERA_FOLLOW_UP.match(found.group("rest").strip())
    question = follow_up.group("question").strip().rstrip(".") if follow_up else ""
    return {"kind": CAMERA_KIND, "question": question[:1].upper() + question[1:]}


def camera_reply(action: dict, client: str) -> str:
    if client not in CAMERA_CLIENTS:
        return CAMERA_NOT_YET if client == PHONE_CLIENT else CAMERA_ELSEWHERE
    line = ACTION_LINE.format(json.dumps(action, ensure_ascii=False))
    return f"Tap the button to open the camera and take the photo. It comes back here for you to send, and I only look at it once you do.\n\n{line}"


def client_for(name: str, features) -> str:
    declared = {feature for feature in features if isinstance(feature, str)} if isinstance(features, list) else set()
    return ANDROID_CLIENT if name == PHONE_CLIENT and ANDROID_FEATURES <= declared else name


def only_from_code(answer, tools_used) -> str:
    made_by_code = any(str(tool).startswith(ACTION_TOOL_PREFIX) for tool in tools_used or [])
    if made_by_code or not isinstance(answer, str) or not ACTION.search(answer):
        return answer
    return without_action(answer)
