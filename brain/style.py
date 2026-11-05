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
correctly), but say if they use British spelling. Write 8 to 12 short bullet points and nothing else.

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

How they write:
{profile}

Write exactly what they asked for, ready to post or send, with correct spelling and grammar. {layout}
Use only the facts in their request: never add events, places, people, companies, dates, problems or achievements
that aren't in it. Where a good {kind} really needs a fact they didn't give, put a short placeholder in square
brackets naming that fact, like [the date] or [your tutor's name]; never use a placeholder as a greeting or name.
{sign_off}Reply with the {kind} itself only: no title, no introduction, no notes."""

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
DRAFT_KINDS = ("linkedin post", "post", "email", "e-mail", "caption", "bio", "cover letter", "letter", "message", "tweet",
               "comment", "announcement", "text")


def draft(request: str, complete, name: str = "") -> str:
    """A new piece of writing in the user's voice, from their request alone (see is_new_draft). The draft is a
    Markdown quote under a one-line intro, so the apps show it but only say the intro out loud."""
    lowered = request.lower()
    kind = next((k for k in DRAFT_KINDS if k in lowered), "draft").replace("linkedin", "LinkedIn").replace("e-mail", "email")
    profile = load().get("profile") or "(not learned yet: write in a natural, friendly, clear voice)"
    layout = LAYOUTS.get("post" if "post" in kind else kind, "")
    sign_off = ""
    if kind in ("email", "letter", "cover letter", "message"):
        # Small models mix up who's writing and who's reading; spell it out.
        sign_off = (f"It is from {name or 'them'} to someone else. Open with \"Hi\" and the recipient's name from the request, "
                    f"or a placeholder for it like [Tutor's name]; never greet {name or 'the writer'}. End with a sign-off "
                    f"such as \"Thanks,\" and then {name or '[Your name]'}. ")
    written = complete(DRAFT_PROMPT.format(request=request.strip(), profile=profile, kind=kind, layout=layout,
                                           sign_off=sign_off)).strip().strip('"')
    lines = written.splitlines()
    while lines and (not lines[0].strip() or LEAD_IN.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or LEAD_IN.match(lines[-1])):
        lines.pop()
    written = "\n".join(lines)
    if "post" in kind and (tags := TRAILING_HASHTAGS.search(written)) and "\n" not in tags.group().strip():
        written = written[:tags.start()].rstrip() + "\n\n" + " ".join(tags.group().split())
    if not written:
        return "I couldn't write that just now. Try asking again."
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in written.splitlines())
    return DRAFT_INTRO.format(kind=kind) + "\n\n" + quoted


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
    profile = complete(PROFILE_PROMPT.format(what=what, separately=separately, sources=sources_text(messages, posts))).strip()
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
            "below), always with correct spelling and grammar. Use only facts from their request and what you truly know "
            "about them; never invent events, places, people or achievements.\n" + style["profile"])
