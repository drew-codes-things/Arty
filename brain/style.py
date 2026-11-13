"""Writing in the user's voice: posts, emails and messages Arty drafts should sound like them.

Arty learns the voice from what the user writes: their messages to it (from every app, via the conversation
archive) and their own LinkedIn posts (read in the browser on Arty's desktop, when it's signed in). The model
turns those into a short description of the voice, kept with a few real examples in STYLE_FILE, and it's
refreshed weekly. When the user asks for something written (a post, an email, a reply), the description goes
into Arty's notes for that message. Drafts copy the voice but not the typos: they're for other people.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

STYLE_FILE = Path(os.environ.get("STYLE_FILE", "/srv/ai/brain/state/style.json"))
REFRESH_SECONDS = 7 * 24 * 3600
MIN_MESSAGE_CHARS = 40
MAX_MESSAGES = 150
MIN_MESSAGES = 8
MAX_POSTS = 10
SOURCE_CHARS = 9000       # what the model reads to describe the voice: fits an 8k context with the instructions
EXAMPLE_POST_CHARS = 700
EXAMPLE_MESSAGES = 3

# Asking for something written for (or as) the user.
WRITING_REQUEST = re.compile(
    r"\b(write|draft|compose|reword|rewrite|polish|tidy up|improve|help me (?:write|with)|come up with|make)\b[^.?!]{0,60}?"
    r"\b(post|e-?mail|message|reply|caption|bio|cover letter|letter|text|tweet|comment|announcement|summary of my)s?\b"
    r"|\blinkedin\b|\bpost ideas?\b", re.IGNORECASE)
# Writing about something that already exists ("reply to this email", "improve this:"): needs reading or
# pasted text, so it goes through the normal route with tools rather than straight to drafting.
REFERS_TO_EXISTING = re.compile(r"\b(reply|respond|answer)\b|\b(this|that|the) (e-?mail|message|post|comment|text)\b|:\s*\S", re.IGNORECASE)
# "Learn how I write" / "update my style": refresh now.
LEARN_REQUEST = re.compile(r"\b(learn|study|update|refresh)\b[^.?!]{0,30}\b(how i (write|talk|type)|my (writing )?style|my voice)\b",
                           re.IGNORECASE)

PROFILE_PROMPT = """Below are things one person wrote: {what}.
Describe how they write, so a ghost-writer could write posts, emails and messages that sound like them. Cover:
tone and formality{separately}, sentence length and structure, words and phrases they use, how they open and sign
off, emoji, exclamation marks and hashtags, how their posts are laid out (paragraphs, where the thanks and hashtags
go), and what they like to talk about. Describe the style only, not what happened. Only describe what you can see in
the writing below; don't guess about anything else. Ignore spelling mistakes and typos (drafts will be spelled
correctly) and leave spelling out. Write 8 to 12 short bullet points and nothing else.

{sources}"""

log = logging.getLogger("brain.style")
_building = threading.Lock()


def is_writing_request(text: str) -> bool:
    return bool(WRITING_REQUEST.search(text or ""))


def is_new_draft(text: str) -> bool:
    """Asked to write something new from what's in the request (a post, an email, a caption)."""
    return is_writing_request(text) and not REFERS_TO_EXISTING.search(text or "") and not re.search(r"\bideas?\b", text or "", re.I)


DRAFT_PROMPT = """You write for the person below, in their own voice. Their request:
"{request}"

What you know about them (true; use a fact only where it fits the request):
{known}

How they write:
{profile}

Write exactly what they asked for, ready to post or send, with correct {spelling} and grammar. {layout}
Use only the facts in their request and the facts above. Never add anything else that would have to be true: no events,
steps, problems, equipment, numbers, dates, times, places, people, companies, results, achievements or feelings about
things that happened. Where a good {kind} really needs a detail they didn't give, put a short placeholder in square
brackets naming that detail, like [your role] or [the date], and keep the rest general. A short draft with placeholders
is better than a longer one with made-up details. Never use a placeholder as a greeting or name.
{sign_off}If the request is for something meant to deceive, threaten, harass or defraud someone (a scam or phishing message,
pretending to be a real company or person, a threat), or anything else plainly illegal, reply only with the word {declined}.
Otherwise reply with the {kind} itself only: no title, no introduction, no notes."""
NOTHING_KNOWN = "(nothing beyond the request)"
PROFILE_EXAMPLE = re.compile(r"\s*\([^()]*\)")
QUOTED = re.compile(r"[\"\u201c\u201d]")
SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:])")
CHECK_PROMPT = """Below are the FACTS a person gave and a DRAFT written for them. List every detail in the DRAFT that the FACTS
don't state or clearly imply: names, numbers, dates, times, places, companies, products or equipment, events, steps
taken, problems, results, achievements, and feelings about specific things that happened. General wording, opinions
about the topic, why they want it or what they hope to learn, greetings, sign-offs, their name, thanks, hashtags and
[placeholders in square brackets] are fine. For each one, copy
the made-up words exactly as they appear in the DRAFT (only the made-up part, as short as possible) and give a short
placeholder in square brackets naming what the person should fill in instead.

FACTS:
Their request: "{request}"
Their name: {name}
{known}

DRAFT:
{draft}

Reply with JSON only: {{"invented": [{{"words": "exact words from the draft", "placeholder": "[what to fill in]"}}]}}
Reply {{"invented": []}} if everything in the draft is supported by the facts."""
DECLINED = "DECLINE"
SPELLING = "British English spelling"
SPELLING_CLAIM = re.compile(r"\bspell(?:ing|ings|ed|t)?\b|\b(?:american|us|british|uk) (?:english|variants?)\b", re.IGNORECASE)

LAYOUTS = {
    "post": "A social post: no greeting and no sign-off; short paragraphs; finish with their usual thanks if it fits, then "
            "3 to 5 relevant hashtags on the last line.",
    "email": "An email: a subject line first (\"Subject: ...\"), then a greeting, the message and a sign-off.",
}
# Hashtags at the end of a post's text, which belong on a line of their own.
TRAILING_HASHTAGS = re.compile(r"(?:\s+#\w+)+\s*$")
# A model's own lead-in or rule lines around the draft ("Here's a LinkedIn post based on your request:", "---").
LEAD_IN = re.compile(r"^\s*((here'?s|here is|sure|below is|certainly)\b[^\n]{0,90}:?|-{3,}|\*{3,})\s*$", re.IGNORECASE)

DRAFT_INTRO = "Here's a draft {kind} in your style. It's here in the chat to copy and change:"
FILL_IN = "Fill in {placeholders} before you {action} it."
POSTED_KINDS = ("post", "tweet", "caption", "comment", "bio", "announcement")
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,60}\]")
NUMBER_PLACEHOLDER = "[number]"
FACT_PLACEHOLDERS = {"amount": "[the amount]", "time": "[the time]", "date": "[the date]", "year": "[the year]"}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
INVENTED_FACT = re.compile(
    r"(?P<amount>[\u00a3$\u20ac]\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn)\b)?)"
    r"|(?P<time>(?<![\w:/-])\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|(?<![\w:/-])\d{1,2}:\d{2}(?![\w:/]))"
    rf"|(?P<date>(?<![\w/-])\d{{1,2}}(?:st|nd|rd|th)?\s(?:of\s)?(?-i:{MONTHS})\b|(?-i:{MONTHS})\s\d{{1,2}}(?:st|nd|rd|th)?\b"
    r"|(?<![\w/-])\d{1,2}/\d{1,2}/\d{2,4}(?![\w/])|(?<![\w/-])\d{1,2}(?:st|nd|rd|th)\b)"
    r"|(?P<year>(?<![\w/-])(?:19|20)\d{2}(?![\w/%-]))"
    r"|(?P<size>(?<![\w/.-])\d+(?:\.\d+)?(?=\s?(?:KB|MB|GB|TB|GHz|MHz|Mbps|Gbps)\b))"
    r"|(?P<duration>(?<![\w/.-])\d+(?=\+?\s(?:years?|months?)\b))",
    re.IGNORECASE)
NUMBER_WORDS = {word: value for value, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen".split())} | {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                                     "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
TENS_AND_UNITS = re.compile(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[- ](one|two|three|four|five|six|seven|eight|nine)\b",
                            re.IGNORECASE)
WORD = re.compile(r"[a-z]+", re.IGNORECASE)
OWN_DETAIL_PLACEHOLDER = "[your own detail]"
MAX_PLACEHOLDER_CHARS = 60
MIN_INVENTED_CHARS = 3
DIGITS = re.compile(r"\d+")
JSON_OBJECT = re.compile(r"\{[\s\S]*\}")
APOSTROPHES = "'\u2018\u2019`"
DOUBLE_QUOTES = '"\u201c\u201d'
DASHES = "-\u2013\u2014"
DRAFT_KINDS = ("linkedin post", "post", "email", "e-mail", "caption", "bio", "cover letter", "letter", "message", "tweet",
               "comment", "announcement", "text")


def invented_details(request: str, known: str, written: str, complete, name: str = "") -> list[dict]:
    reply = complete(CHECK_PROMPT.format(request=request, name=name or "not given", known=known, draft=written), json_mode=True)
    found = JSON_OBJECT.search(reply or "")
    try:
        items = json.loads(found.group()).get("invented") if found else []
    except (ValueError, AttributeError):
        log.warning("the draft check gave no usable answer: %s", (reply or "")[:200])
        return []
    return [item for item in items or [] if isinstance(item, dict) and isinstance(item.get("words"), str)]


def as_placeholder(text) -> str:
    name = str(text or "").strip().strip("[]").strip()
    if not name or len(name) > MAX_PLACEHOLDER_CHARS or "[" in name or "]" in name:
        return OWN_DETAIL_PLACEHOLDER
    return f"[{name}]"


def loose_pattern(words: str) -> re.Pattern:
    parts = []
    for char in words.strip():
        if char in APOSTROPHES:
            parts.append(f"[{APOSTROPHES}]")
        elif char in DOUBLE_QUOTES:
            parts.append(f"[{DOUBLE_QUOTES}]")
        elif char in DASHES:
            parts.append(f"[{DASHES}]")
        elif char.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(char))
    return re.compile("".join(parts).replace(r"\s+\s+", r"\s+"), re.IGNORECASE)


def is_only_hashtags(words: str) -> bool:
    return all(word.startswith("#") for word in words.split())


def is_worth_replacing(words: str, written: str, given: str) -> bool:
    if len(words) < MIN_INVENTED_CHARS or len(words) > len(written) / 2:
        return False
    if is_only_hashtags(words) or "[" in words or "]" in words:
        return False
    return not loose_pattern(words).search(given)


def first_match_outside_placeholders(pattern: re.Pattern, written: str):
    placeholders = [match.span() for match in PLACEHOLDER.finditer(written)]
    return next((match for match in pattern.finditer(written)
                 if not any(start <= match.start() < end for start, end in placeholders)), None)


def without_invented(written: str, invented: list[dict], given: str = "") -> str:
    for item in invented:
        words = item["words"].strip()
        if not is_worth_replacing(words, written, given):
            continue
        found = first_match_outside_placeholders(loose_pattern(words), written)
        if found is None:
            log.info("the draft check named words that aren't in the draft: %s", words[:80])
            continue
        written = written[:found.start()] + as_placeholder(item.get("placeholder")) + written[found.end():]
    return written


def numbers_given(facts: str) -> set[str]:
    spelled = {str(NUMBER_WORDS[tens.lower()] + NUMBER_WORDS[unit.lower()]) for tens, unit in TENS_AND_UNITS.findall(facts)}
    words = {str(NUMBER_WORDS[word.lower()]) for word in WORD.findall(facts) if word.lower() in NUMBER_WORDS}
    return set(DIGITS.findall(facts)) | spelled | words


def without_unknown_numbers(written: str, facts: str) -> str:
    known = numbers_given(facts)

    def checked(match):
        token = match.group()
        if set(DIGITS.findall(token)) <= known:
            return token
        return FACT_PLACEHOLDERS.get(match.lastgroup, NUMBER_PLACEHOLDER)
    return INVENTED_FACT.sub(checked, written)


def fill_in_line(written: str, kind: str) -> str:
    placeholders = list(dict.fromkeys(PLACEHOLDER.findall(written)))
    if not placeholders:
        return ""
    listed = placeholders[0] if len(placeholders) == 1 else ", ".join(placeholders[:-1]) + " and " + placeholders[-1]
    action = "post" if any(word in kind.lower() for word in POSTED_KINDS) else "send"
    return FILL_IN.format(placeholders=listed, action=action)


def without_spelling_claims(profile: str) -> str:
    return "\n".join(line for line in profile.splitlines() if not SPELLING_CLAIM.search(line))


def voice_only(profile: str) -> str:
    without_examples = PROFILE_EXAMPLE.sub("", without_spelling_claims(profile))
    lines = [line for line in without_examples.splitlines() if not QUOTED.search(line)]
    return SPACE_BEFORE_PUNCTUATION.sub(r"\1", "\n".join(lines))


def draft(request: str, complete, name: str = "", known: str = "") -> str | None:
    lowered = request.lower()
    kind = next((k for k in DRAFT_KINDS if k in lowered), "draft").replace("linkedin", "LinkedIn").replace("e-mail", "email")
    profile = voice_only(load().get("profile") or "") or "Not learned yet: write in a natural, friendly, clear voice."
    layout = LAYOUTS.get("post" if "post" in kind else kind, "")
    known = known.strip() or NOTHING_KNOWN
    sign_off = ""
    if kind in ("email", "letter", "cover letter", "message"):
        # Small models mix up who's writing and who's reading; spell it out.
        sign_off = (f"It is from {name or 'them'} to someone else. Open with \"Hi\" and the recipient's name from the request, "
                    f"or a placeholder for it like [Tutor's name]; never greet {name or 'the writer'}. End with a sign-off "
                    f"such as \"Thanks,\" and then {name or '[Your name]'}. ")
    written = complete(DRAFT_PROMPT.format(request=request.strip(), known=known, profile=profile, kind=kind, layout=layout,
                                           sign_off=sign_off, declined=DECLINED, spelling=SPELLING)).strip().strip('"')
    if written.strip(" .") == DECLINED:
        return None
    lines = written.splitlines()
    while lines and (not lines[0].strip() or LEAD_IN.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or LEAD_IN.match(lines[-1])):
        lines.pop()
    written = "\n".join(lines)
    if not written:
        return "I couldn't write that just now. Try asking again."
    written = without_invented(written, invented_details(request.strip(), known, written, complete, name),
                               f"{request}\n{known}\n{name}")
    written = without_unknown_numbers(written, f"{request}\n{known}\n{name}")
    if "post" in kind and (tags := TRAILING_HASHTAGS.search(written)) and "\n" not in tags.group().strip():
        written = written[:tags.start()].rstrip() + "\n\n" + " ".join(tags.group().split())
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in written.splitlines())
    reply = DRAFT_INTRO.format(kind=kind) + "\n\n" + quoted
    return reply + (f"\n\n{fill}" if (fill := fill_in_line(written, kind)) else "")


def is_learn_request(text: str) -> bool:
    return bool(LEARN_REQUEST.search(text or ""))


def load() -> dict:
    try:
        return json.loads(STYLE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def due() -> bool:
    return time.time() - load().get("updated", 0) > REFRESH_SECONDS


def pick_messages(messages: list[str]) -> list[str]:
    """The user's own longer messages, newest last; test messages and repeats left out."""
    seen, picked = set(), []
    for message in messages:
        text = " ".join(message.split())
        if len(text) < MIN_MESSAGE_CHARS or text.upper().startswith("TEST") or text.lower() in seen:
            continue
        seen.add(text.lower())
        picked.append(text)
    return picked[-MAX_MESSAGES:]


def split_posts(linkedin_output: str) -> list[str]:
    """The post texts from linkedin_posts' output ("### Name (when)" then the post), reposts left out."""
    posts = []
    for block in re.split(r"^### .*$", linkedin_output or "", flags=re.MULTILINE)[1:]:
        text = block.strip()
        if text and not text.lower().startswith("(repost"):
            posts.append(text)
    return posts[:MAX_POSTS]


def sources_text(messages: list[str], posts: list[str]) -> str:
    """What the model reads: posts first (the best guide for public writing), then messages, within SOURCE_CHARS."""
    parts = [f"LinkedIn post:\n{post}" for post in posts] + [f"Chat message: {message}" for message in reversed(messages)]
    text, used = [], 0
    for part in parts:
        if used + len(part) > SOURCE_CHARS:
            break
        text.append(part)
        used += len(part)
    return "\n\n".join(text)


def build(messages: list[str], posts: list[str], complete) -> dict:
    """Describes the voice from the user's writing and saves it. Raises ValueError with too little to go on."""
    messages = pick_messages(messages)
    if len(messages) + 3 * len(posts) < MIN_MESSAGES:
        raise ValueError("I haven't seen enough of your writing yet. Chat with me a bit more, then ask again.")
    what = "chat messages to their assistant, and their public LinkedIn posts" if posts else "chat messages to their assistant"
    separately = " (casual messages and public posts separately)" if posts else ""
    profile = without_spelling_claims(
        complete(PROFILE_PROMPT.format(what=what, separately=separately, sources=sources_text(messages, posts)))).strip()
    if not profile:
        raise ValueError("I couldn't describe your writing just now. Try again in a bit.")
    style = {
        "profile": profile,
        "post_examples": [post[:EXAMPLE_POST_CHARS] for post in posts[:2]],
        "message_examples": sorted(messages, key=len, reverse=True)[:EXAMPLE_MESSAGES],
        "updated": time.time(),
        "sources": {"messages": len(messages), "posts": len(posts)},
    }
    STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STYLE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(style, ensure_ascii=False))
    temporary.replace(STYLE_FILE)
    return style


def refresh(gather, complete) -> str:
    """Rebuilds the profile now (one at a time). gather() returns (messages, posts). Returns a reply for the user."""
    if not _building.acquire(blocking=False):
        return "I'm already learning how you write; it'll be ready in a minute or two."
    try:
        messages, posts = gather()
        style = build(messages, posts, complete)
    except ValueError as error:
        return str(error)
    finally:
        _building.release()
    counts = style["sources"]
    return (f"Done: I've learned how you write from {counts['messages']} of your messages"
            + (f" and {counts['posts']} of your LinkedIn posts" if counts["posts"] else "")
            + ". Posts, emails and messages I draft for you will sound like you now. Here's what I noticed:\n\n" + style["profile"])


def voice_note() -> str:
    """For Arty's notes when the user asks for something written; empty until a profile exists."""
    style = load()
    if not style.get("profile"):
        return ""
    # Only the description: small models copy the facts from an example post into the new one.
    return ("\n\nWRITING AS THE USER: when you draft posts, emails or messages for them, write in their voice (as described "
            f"below), always with correct {SPELLING} and grammar. Use only facts from their request and what you truly know "
            "about them; never invent events, places, people or achievements.\n" + without_spelling_claims(style["profile"]))
