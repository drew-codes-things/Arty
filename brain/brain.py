"""The assistant's brain: an OpenAI-compatible endpoint that adds memory, instructions and tools.

Clients (Open WebUI, or your own apps with the Arty API key) send ordinary chat
completions. Each request gets the system prompt and the user's saved memories, and the
model's tool calls are executed through the MCP hub until it produces a final answer.

It also runs background tasks (one at a time, pausing for Drew's approval or help when
needed), a scheduler that turns schedules into tasks, and a small REST API over all of it.
See docs/API.md.
"""
import base64
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import abilities
import calendar_feed
import drafts
import email_watch
import expiry
import lectures
import research
import style
import media
import models
import personal
import phone
import quick
import reckon
import sharing
import safety
import desktop_look
import briefing
import notify
import owner
import policy
import store
import timing
import undo
import weather
import web_app

LISTEN_HOST = os.environ.get("BRAIN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("BRAIN_PORT", "8081"))
PUBLIC_URL = os.environ.get("BRAIN_PUBLIC_URL", "http://192.168.1.13:8081").rstrip("/")
BRAIN_KEY = os.environ["BRAIN_API_KEY"]
APP_KEY = os.environ.get("ARTY_API_KEY", "")
ALERT_TOKEN = os.environ.get("ALERT_TOKEN", "")  # Uptime Kuma's webhook puts this in its URL
HUB_KEY = os.environ["MCPO_API_KEY"]
HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CONFIG_FILE = os.environ.get("BRAIN_CONFIG", "/srv/ai/brain/brain.json")
SERVERS = ["memory", "time", "fetch", "sequential-thinking", "workspace", "research", "homelab"]
MODEL_ID = "assistant-core"
MAX_TOOL_ROUNDS = 12
MEMORY_CHARS = 4000
TOOL_RESULT_CHARS = 8000
SPEC_REFRESH_SECONDS = 300
USER_NAME = owner.configured_name(CONFIG_FILE)
RECENT_CHARS = 300
RECALL_EXCERPT_CHARS = 400
MAX_BODY_BYTES = 64 * 1024 * 1024  # chat requests can carry full-size photos as base64
WORKER_IDLE_SECONDS = 3
SCHEDULER_TICK_SECONDS = 30
TASK_ACTIONS = ("done", "approve", "deny", "cancel")
SHORT_REPORT_CHARS = 60
DESKTOP_HELP = "on Arty's desktop (Proxmox web UI > VM 105 ai-desktop > Console)"
SECRET_PATTERN = re.compile(r"password|passcode|passphrase|passkey|\bpin\b|api[ _-]?key|token|card number|cvv|sort code", re.IGNORECASE)


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


NATIVE_TOOLS = {
    "recall_conversations": _tool(
        "recall_conversations",
        "Search every past conversation with the user by a few key words. Returns dated excerpts, newest first.",
        {"query": {"type": "string", "description": "a few key words to look for"},
         "limit": {"type": "integer", "description": "how many excerpts to return (default 5)"}},
        ["query"]),
    "ask_user": _tool(
        "ask_user",
        "Only for things a human must do on your desktop: signing in to a website, or passing a CAPTCHA or bot check. "
        "Not for permission or questions: ask those in your normal reply. In a background task this pauses until the user has done it.",
        {"message": {"type": "string", "description": "what the user needs to do, written to him as 'you', e.g. 'Please sign in to LinkedIn in the browser on my desktop.'"}},
        ["message"]),
    "notify_user": _tool(
        "notify_user",
        "Send the user a notification right now (to Updates in their phone and desktop apps). Not for reminders: anything set with "
        "schedule_task already arrives there by itself at the right time.",
        {"message": {"type": "string", "description": "the notification, written to him as 'you'"}, "title": {"type": "string", "description": "short title (optional)"}},
        ["message"]),
    "start_background_task": _tool(
        "start_background_task",
        "Start a job that you work on by yourself in the background, so the user doesn't have to wait in the chat. "
        "He gets a phone notification with the result.",
        {"instructions": {"type": "string", "description": "everything needed to do the job, written as instructions to yourself"},
         "title": {"type": "string", "description": "a short name for the job"}},
        ["instructions"]),
    "schedule_task": _tool(
        "schedule_task",
        "Set a reminder, or schedule a job for later or on repeat. For a reminder (\"remind me ...\"), give reminder_text: "
        "that exact text is sent to the user's phone and desktop apps at that time. For a job (daily check, report), give instructions: "
        "you do it in the background when it runs and the user gets the result. Call this whenever the user asks to be reminded.",
        {"reminder_text": {"type": "string", "description": "for reminders: the exact message to send, written to the user, e.g. 'Time to stretch.'"},
         "instructions": {"type": "string", "description": "for jobs: what to do each time, as instructions to yourself"},
         "when": {"type": "string", "description": "copy the user's own words for the time, e.g. 'in 2 minutes', 'tomorrow 08:00', 'weekdays 07:30'. " + timing.HELP},
         "title": {"type": "string", "description": "a short name"}},
        ["when"]),
    "list_scheduled": _tool("list_scheduled", "List the scheduled jobs and when each runs next.", {}, []),
    "cancel_scheduled": _tool("cancel_scheduled", "Cancel a scheduled job by its number.",
                              {"schedule_id": {"type": "integer"}}, ["schedule_id"]),
    "list_tasks": _tool("list_tasks", "List recent background tasks and their status/results.", {}, []),
    "list_models": _tool("list_models", "List the AI models you can run on, and which is your main one.", {}, []),
    "ask_other_model": _tool(
        "ask_other_model",
        "Hand one self-contained job to another installed AI model and get its answer back. "
        "You stay on your own model. list_models shows what's installed.",
        {"model": {"type": "string", "description": "installed model name, e.g. qwen3:14b"},
         "prompt": {"type": "string", "description": "the full job, with everything the other model needs to know"}},
        ["model", "prompt"]),
    "calendar_events": _tool(
        "calendar_events",
        "The user's calendar: their events for today or the next few days (your own calendar, plus Google Calendar if connected). "
        "Your own events have #numbers for calendar_change and calendar_remove.",
        {"days": {"type": "integer", "description": "how many days ahead, from now (default 1 = today)"}},
        []),
    "calendar_add": _tool(
        "calendar_add",
        "Put an event in the user's calendar (your own calendar, which you remind them about 15 minutes before). "
        "Use for appointments, plans and birthdays; use schedule_task for plain reminders.",
        {"title": {"type": "string", "description": "what it is, e.g. 'Dentist'"},
         "when": {"type": "string", "description": "copy the user's words: 'Tuesday 3pm', '25 Dec 14:00', '24/12' (all day), "
                                                   "'tomorrow 18:00', 'every Monday 6pm' (repeats)"},
         "duration_minutes": {"type": "integer", "description": "how long (default 60, or all day for a date with no time)"},
         "location": {"type": "string"}, "notes": {"type": "string"}},
        ["title", "when"]),
    "calendar_change": _tool(
        "calendar_change",
        "Move, rename or change the length or place of one of your own calendar events, by its #number from calendar_events.",
        {"event_id": {"type": "integer"}, "title": {"type": "string"}, "when": {"type": "string"},
         "duration_minutes": {"type": "integer"}, "location": {"type": "string"}},
        ["event_id"]),
    "calendar_remove": _tool(
        "calendar_remove",
        "Delete one of your own calendar events by its #number from calendar_events (a repeating event stops repeating).",
        {"event_id": {"type": "integer"}},
        ["event_id"]),
    "weather": _tool(
        "weather",
        "The weather for the user's home town (set in brain.json): now, today, or the next few days.",
        {"days": {"type": "integer", "description": "1 = today (default), up to 7"}},
        []),
    "week_recap": _tool(
        "week_recap",
        "Looking back: what the user asked you about and what you did together over the last 7 days, for summaries and "
        "LinkedIn post ideas. Not for what's coming up (that's calendar_events).",
        {}, []),
    "convert_file": _tool(
        "convert_file",
        "Convert a file the user attached (or one you made) to another format, and get a download link for the result. "
        "Images: png, jpg, webp, gif, bmp, tiff, ico, avif, heic (optionally resized). Audio: mp3, wav, ogg, flac, m4a, aac, opus. "
        "Video: mp4, webm, mkv, mov, gif, or any audio format to pull out the sound.",
        {"file_id": {"type": "string", "description": "the file id shown with the attachment"},
         "format": {"type": "string", "description": "the target format, e.g. png, mp3, mp4"},
         "width": {"type": "integer", "description": "optional new width in pixels (images and video)"},
         "height": {"type": "integer", "description": "optional new height in pixels (images and video)"},
         "quality": {"type": "integer", "description": "optional image quality 1-100 (jpg/webp), lower is smaller"}},
        ["file_id", "format"]),
    "switch_model": _tool(
        "switch_model",
        "ONLY when the user explicitly asks you to change your main model: switch to it, e.g. 'qwen3:14b'. Downloads it first if needed.",
        {"model": {"type": "string", "description": "Ollama model name, e.g. qwen3:14b"}},
        ["model"]),
}
NATIVE_TOOLS.update({name: definition for name, (_, definition) in personal.TOOLS.items()})
NATIVE_TOOLS.update({name: definition for name, (_, definition) in reckon.TOOLS.items()})
NATIVE_TOOLS.update({name: definition for name, (_, definition) in sharing.TOOLS.items()})
BACKGROUND_ONLY_EXCLUDED = {"start_background_task", "schedule_task", "cancel_scheduled", "switch_model"}
API_CALLABLE_NATIVE = {*personal.TOOLS, *sharing.TOOLS, "weather", "week_recap", "calendar_events", "calendar_add", "calendar_change", "calendar_remove", "convert_file", "recall_conversations", "notify_user", "start_background_task", "schedule_task", "list_scheduled", "cancel_scheduled", "list_tasks", "list_models", "switch_model", "ask_other_model"}

EXTRACT_PROMPT = owner.fill("""You extract lasting facts that OWNER states about their own life, so their assistant can remember them.

Example
OWNER said: "My sister Ella starts uni in Cardiff next month. Anyway, what's 2+2?"
Answer: {"facts": [{"about": "Ella", "fact": "OWNER's sister, starting university in Cardiff"}]}

Example
OWNER said: "Can you check the weather?"
Answer: {"facts": []}

Example
OWNER said: "Remind me to check the oven in 5 minutes, and say hi in three words"
Answer: {"facts": []}

Example
OWNER said: "I've switched to Linux on my laptop for good, I'm done with Windows"
Answer: {"facts": [{"about": "OWNER", "fact": "Uses Linux on their laptop and has stopped using Windows"}]}

Only include facts that will still be true and useful in a month: who OWNER is, their people, belongings, lasting
projects and preferences. Never include: requests or tasks for the assistant, reminders, what they're doing right now or
in the next few hours, how they are feeling today, test messages, files or pictures they sent, or passwords and secrets.
Most messages contain no such fact: then answer {"facts": []}. Answer with JSON only.

OWNER said: "%s"
Answer:""", USER_NAME)
BACKGROUND_INSTRUCTIONS = """BACKGROUND TASK MODE
You are working on a task by yourself; the user is not watching the chat. Work through it fully with your tools, checking each step.
- If you need a human (a website login, a CAPTCHA or bot check), call ask_user: the task pauses until the user has done it, then you continue.
- Outward-facing actions (posting, sending, connecting, buying) are held for the user's approval automatically; just carry on calling the tool.
- When finished, reply with a short report of what you did and found, written to the user as "you". It is sent to his phone."""
# Trailing offers the user asked not to get ("Would you like...?", "Let me know if..."); small models add them anyway.
OFFER_SENTENCE = re.compile(
    r"(would you like|do you want me to|do you want to|shall i|should i|ready to proceed|is there anything else|anything else i can|"
    r"let me know if|feel free to|if you need anything|if you decide|just let me know|how can i (help|assist)|"
    r"what would you like|anything (specific|else) you need|need any (other|more|further) (help|assistance)|"
    r"want to explore|explore this further|happy to help|here to help|have a (good|great|nice) (day|night|evening|week)|"
    r"what would you prefer|which would you prefer|or do you have (a|any)|if you('d| would)? (want|like)( me)? to|"
    r"i can (also )?(find|fetch|look|search|get|check)( some| more)?|i hope (this|that) helps|hope this helps|"
    r"if you need (more|any more|any further|further|other|any)|we can (look into|explore|check)|let me know how|"
    r"let's move on|if so, (please )?(provide|tell me|give me|let me know)|what would be a good time|i can check again|"
    r"what else can i (help|assist|do)|anything else (for|i can)|would you like to (add|explore|know|learn|see)|"
    r"i can help (you )?(with that|set|schedule|add)|you can check your \w+ directly|otherwise, everything is|"
    r"(questions|tasks) i can help with|can help with today|what do you prefer|if (that|this) would (be useful|help)|"
    r"(an honour|a pleasure) to (assist|help)|happy to assist|i'd be (happy|glad) to)", re.IGNORECASE)


def trim_offers(answer: str) -> str:
    """Drop the sentences at the very end that only offer more help. Only the last paragraph is touched,
    so lists and structure stay as they are, and something of the answer always remains."""
    paragraphs = answer.strip().split("\n\n")
    while paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", paragraphs[-1].strip())
        while sentences and OFFER_SENTENCE.search(sentences[-1]):
            sentences.pop()
        if sentences:
            paragraphs[-1] = " ".join(sentences)
            break
        if len(paragraphs) == 1:
            return answer.strip()  # the whole answer was an offer: leave it
        paragraphs.pop()
    return "\n\n".join(paragraphs)


# The user wants to be called "you", never by name; the persona's facts mention "Drew" a lot, so small
# models still greet him by it. Only the vocative ("Goodnight Drew!", "Sure, Drew.") is removed.
NAME_GREETING, NAME_TAIL = owner.vocative_patterns(USER_NAME)
URL_TAGS = re.compile(r"</?url>", re.IGNORECASE)
# Small models sometimes run on past their answer into their own instructions (the time line, the tool list).
PROMPT_LEAK = re.compile(r"((?:^|\n)\s*It is now \w+day \d+ \w+ \d{4}, \d\d:\d\d \(UK time[^)\n]*\)|\n\s*# Tools\b|<tools>|"
                         r"You may call one or more functions|\n\s*FACTS YOU ALREADY KNOW|\n\s*BACKGROUND ONLY, do not continue|</?notes>|"
                         r"\[\d{4}-\d\d-\d\d \d\d:\d\d\] User:|\n\s*Arty \(you\):)")


# A reply ending by opening a code block with nothing in it: the model began a tool call as text.
FUMBLED_CALL = re.compile(r"\s*```[a-z]*\s*$")


def tidy_answer(answer: str) -> str:
    if leak := PROMPT_LEAK.search(answer):
        log.warning("cut a reply that ran on into its instructions")
        answer = answer[:leak.start()].rstrip()
    if answer.count("```") % 2 == 1 and (fumbled := FUMBLED_CALL.search(answer)):
        answer = answer[:fumbled.start()]  # an unclosed block would turn the rest of the chat into code
    answer = LOOKUP_LABEL.sub("", answer)
    answer = TOOL_META_SENTENCE.sub("", JSON_BLOCK.sub("\n", answer))
    answer = re.sub(r"\n{3,}", "\n\n", answer)  # no strip: this also cleans early sentences, whose spaces matter
    answer = NAME_GREETING.sub(lambda m: m.group(1), answer)
    answer = NAME_TAIL.sub("", answer)
    return URL_TAGS.sub("", answer)


# The user asked for something done, or the reply claims something was done or found: with no tool
# used, the small model is describing results it never got (a reminder that was never set, "findings
# from my web searches" it never ran). It's sent back once to actually do it.
ACTION_REQUEST = re.compile(r"\b(remind me|look (it |that |this )?up|search|research|find out|google|look into|check (on |the |my )?|"
                            r"use your (desktop|computer|pc|browser)|schedule|set (a|an|up)|send (me|it)|what'?s the (weather|news|latest|price))\b", re.I)
CLAIMED = re.compile(r"\b(I('ve| have) (just )?(checked|searched|looked|found|set|scheduled|created|sent|opened|added|researched)|"
                     r"here are (some|the) (findings|results)|from my (recent )?(web )?search(es)?|I found (that|some|the))", re.I)
# Asking the user what they mean, about something it could have looked up.
CLARIFYING = re.compile(r"(could you (please )?(provide|clarify|specify|give)|more (context|details)|what (exactly )?do you mean|"
                        r"not sure what you('re| are) referring|doesn't appear to be|can you (clarify|explain what))", re.I)
SEARCH_REQUEST = re.compile(r"\b(look (it |that |this )?up|search|research|find out|google|look into|what('s| is| does)|"
                            r"who('s| is)|verdict|mean(s|ing)?|reference)\b", re.I)
# "remind me to ...", "remind to ...", "set a reminder to ..." (but not "it reminds me of ...")
REMIND_REQUEST = re.compile(r"\bremind(?:\s+(?:me|us))?(?=\s+(?:to|about|in|at|on|by|tomorrow|tonight|this|next|every|each)\b)|"
                            r"\b(?:set|make|add|create|put in)\s+(?:me\s+)?(?:a|an)\s+reminder\b", re.I)
# "... on Telegram" / "send it to Discord": left out of the reminder's text; reminders only go to Arty's apps.
CHAT_APP = re.compile(r"[\s,]*(?:\b(?:and\s+)?(?:send|post|put)\s+(?:it|that|them)\s+)?\b(?:on|to|via|through|in|over|and)\s+(?:my\s+)?"
                      r"(telegram|discord)\b(?:\s+(?:too|as well))?", re.I)
REMINDER_PARTS = [
    re.compile(r"\bremind(?: (?:me|us))? (?:to |about )(?P<what>.+?) (?P<when>(?:in|at|on|by|tomorrow|tonight|this|next|every|each)\b.+)$", re.I),
    re.compile(r"\bremind(?: (?:me|us))? (?P<when>(?:in|at|on|tomorrow|tonight|this|next|every|each)\b.*?) (?:to|about) (?P<what>.+)$", re.I),
    re.compile(r"\breminder (?:to |for |about )(?P<what>.+?) (?P<when>(?:in|at|on|by|tomorrow|tonight|this|next|every|each)\b.+)$", re.I),
    re.compile(r"\breminder (?P<when>(?:in|at|on|tomorrow|tonight|this|next|every|each)\b.*?) (?:to|for|about) (?P<what>.+)$", re.I),
]


def reminder_from(text: str) -> tuple[str, str] | None:
    """(what, when) from "remind me to X in 5 minutes" / "remind me in 5 minutes to X", or None."""
    text = CHAT_APP.sub("", text)
    text = re.sub(r"[\s,]*\b(please|pls|plz|thanks|thank you|cheers|ta|mate)\W*$", "", text.strip(), flags=re.I).rstrip(".!?")
    for pattern in REMINDER_PARTS:
        if match := pattern.search(text):
            what = match["what"].strip()
            return what[:1].upper() + what[1:] + ".", match["when"].strip()
    return None


PROMISE = re.compile(r"\b(I'll|I will|I've|I have|I'm going to)\s+(remind|set (up )?(a |the |your )?(reminder|alarm|schedule)|schedule|start(ed)? (a |the )?(background )?task)", re.IGNORECASE)
# Talking about its tools ("let's use the `homelab__service_status` function", "I'll run this command"):
# the user never needs to see that, so it isn't sent early (see run_agent).
TOOL_TALK = re.compile(r"\b\w+__\w+|\bfunction\b|\btool\b|I'll run|let me (run|use|call)|let's (use|run|call)", re.I)
# Asking Arty to change something ("put the dentist in my calendar", "add milk to my list"): a read-only lookup
# may run first, but the tools to actually do it must stay on offer.
DO_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you|would you|will you|i want you to|"
                        r"i need you to)\s+)*(add|put|book|schedule|set|create|make|remind|cancel|delete|remove|move|change|update|"
                        r"send|save|note|mark|tick|clear|rename|turn|start|stop|restart|open|close|play|pause)\b", re.I)

# A reply that plays at using tools (made-up JSON results, "using the `homelab__x` function") instead of answering.
PRETEND_TOOLS = re.compile(r"```json|\b[a-z]+__[a-z_]+\b|<tool_call>", re.I)
# Sentences that only talk about Arty's own machinery.
TOOL_META_SENTENCE = re.compile(r"[^.!?\n]*(\b(none of the|the provided|the available|my available)\s+(functions|tools)\b|"
                                r"\b[a-z]+__[a-z_]+\b|answer (your question )?directly instead|(with|using) the (tool|function)\b)"
                                r"[^.!?\n]*[.!?:]?", re.I)
# A tool result that says it didn't work: the start of hub.call's errors, MCP errors and native refusals.
TOOL_FAILED = re.compile(r"^\W*(unknown tool|tool error|tool unavailable|error\b|failed\b|blocked\b|(i )?(couldn't|could not|can't|cannot)\b)", re.I)
FAILED_NOTE = ("\n\n(That didn't work. Tell the user plainly that it failed and why, in a sentence; never say it worked "
               "and never make up a result.)")
# Tools that do things; read-only lookups and unlocking tool groups don't count as having done anything.
NOT_ACTIONS = {"use_tools"}
NOT_FOR_ME = "(not for me)"
VOICE_MODE = "(voice mode)"
ADDRESSED_PROMPT = ("A home voice assistant called Arty heard this through a room microphone, either right after its "
                    "wake word or in the few seconds after it last spoke. Decide whether the person was talking TO Arty "
                    "(a question, request, command, or a reply to what Arty just said) or whether it was overheard: "
                    "people talking to each other, an exclamation, laughter, or a fragment that asks nothing of Arty.\n\n"
                    "What Arty last said: %s\nWhat the microphone heard: %s\n\n"
                    'Reply with JSON only: {"for_arty": true} or {"for_arty": false}')
JSON_BLOCK = re.compile(r"\n?```json[\s\S]*?```\n?", re.I)
# A reply this long that only then asks to unlock tools has already answered (see run_agent).
ANSWERED_ALREADY_CHARS = 200
TOOLLESS_NOTE = ("You are running on a model without tool support: you can't use tools, search, your computer or the browser "
                 "right now. Answer from what you know and from the memories above; if something needs tools, say so briefly.")
memory_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain")


def http_json(url, body=None, headers=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def stream_chat(body, on_text, timeout=600):
    """A model call like http_json(f"{OLLAMA_URL}/api/chat", body), but streamed: on_text gets each piece of
    the reply as the model writes it. Returns the same shape as an unstreamed reply."""
    request = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps({**body, "stream": True}).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    content, calls, last = [], [], {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            if not line.strip():
                continue
            last = json.loads(line)
            if last.get("error"):
                raise RuntimeError(f"model error: {last['error']}")
            piece = last.get("message") or {}
            calls += piece.get("tool_calls") or []
            if piece.get("content"):
                content.append(piece["content"])
                on_text(piece["content"])
    return {**last, "message": {"role": "assistant", "content": "".join(content), "tool_calls": calls}}


def load_config():
    with open(CONFIG_FILE) as handle:
        config = json.load(handle)
    with open(config["system_prompt_file"]) as handle:
        config["system_prompt"] = handle.read().strip()
    persona_file = config.get("persona_file")
    config["persona"] = Path(persona_file).read_text().strip() if persona_file and Path(persona_file).exists() else ""
    return config


def is_overheard(answer: str) -> bool:
    return answer.strip().strip(".").lower() == NOT_FOR_ME

def message_text(message):
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def archive_records(config):
    folder = Path(config["conversations_dir"])
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.jsonl"), reverse=True):
        for line in reversed(path.read_text().splitlines()):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def gather_writing() -> tuple[list[str], list[str]]:
    """The user's own writing, for style.py: their messages from the archive (every app) and their LinkedIn
    posts (read in the desktop browser; skipped if it isn't signed in)."""
    config = load_config()
    messages = []
    for path in sorted(Path(config["conversations_dir"]).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                messages.append(APP_NOTES.sub("", str(json.loads(line).get("user") or "")).strip())
            except ValueError:
                continue
    hub.refresh()
    posts_text = hub.call("workspace__linkedin_posts", {"profile": "me", "max_posts": style.MAX_POSTS})
    posts = [] if posts_text.startswith(("BLOCKED", "browser error", "unknown tool", "tool error")) else style.split_posts(posts_text)
    return messages, posts


STYLE_RETRY_SECONDS = 24 * 3600
STYLE_STARTUP_GRACE_SECONDS = 300  # let the tool servers finish starting (they restart with the brain on a deploy)
_style_attempt = {"time": time.time() - STYLE_RETRY_SECONDS + STYLE_STARTUP_GRACE_SECONDS}


def archive_turn(config, user_text, answer, tools_used):
    folder = Path(config["conversations_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {"time": now.isoformat(timespec="seconds"), "user": user_text, "assistant": answer, "tools": tools_used}
    with open(folder / f"{now:%Y-%m}.jsonl", "a") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def recent_exchanges(config):
    records = []
    for record in archive_records(config):
        records.append(record)
        if len(records) >= int(config.get("recent_exchanges", 6)):
            break
    lines = [f"[{r['time'][:16].replace('T', ' ')}] User: {r['user'][:RECENT_CHARS]}\n  Arty (you): {r['assistant'][:RECENT_CHARS]}" for r in reversed(records)]
    return "\n".join(lines)


def recall_records(config, query, limit=5):
    terms = [term for term in re.findall(r"\w+", (query or "").lower()) if len(term) > 2]
    found = []
    if not terms:
        return found
    for record in archive_records(config):
        text = f"{record.get('user', '')} {record.get('assistant', '')}".lower()
        if all(term in text for term in terms) or (len(terms) > 2 and sum(term in text for term in terms) >= len(terms) - 1):
            found.append(record)
            if len(found) >= max(1, int(limit or 5)):
                break
    return found


def recall(config, query, limit=5):
    if not re.findall(r"\w{3,}", query or ""):
        return "Give me a few key words to search for."
    found = recall_records(config, query, limit)
    excerpts = [f"[{r['time'][:16].replace('T', ' ')}] User: {r['user'][:RECALL_EXCERPT_CHARS]}\n  Arty (you): {r['assistant'][:RECALL_EXCERPT_CHARS]}" for r in found]
    return "\n\n".join(excerpts) if excerpts else f"No past conversations mention: {query}"


# Things the small model still tries to save that aren't lasting facts about the user.
NOT_A_FACT = re.compile(r"\b(in \d+ (seconds?|minutes?|hours?)|remind|reminder|to-?do|today|tonight|tomorrow|right now|"
                        r"at the moment|says? (hello|hi|bye|goodbye)|can say|in (three|five|\d+) words|attached|sent an?|sending|"
                        r"\.(png|jpe?g|wav|mp3|mp4)\b|test|is asking|asked (arty|the assistant|you)|ai assistant|talking about)\b", re.IGNORECASE)


def save_facts(facts):
    graph = http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20) or {}
    entities = {e.get("name", "").lower(): e for e in graph.get("entities") or []}
    for item in facts:
        about = str(item.get("about") or USER_NAME).strip()[:60] or USER_NAME
        fact = str(item.get("fact") or "").strip()[:300]
        if not fact or SECRET_PATTERN.search(fact) or NOT_A_FACT.search(fact):
            continue
        existing = entities.get(about.lower())
        if existing and any(fact.lower() == o.lower() for o in existing.get("observations") or []):
            continue
        if existing:
            body, route = {"observations": [{"entityName": existing["name"], "contents": [fact]}]}, "add_observations"
        else:
            kind = "person" if about.lower() == USER_NAME.lower() else "topic"
            body, route = {"entities": [{"name": about, "entityType": kind, "observations": [fact]}]}, "create_entities"
            entities[about.lower()] = {"name": about, "observations": [fact]}
        http_json(f"{HUB_URL}/memory/{route}", body=body, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
        log.info("remembered: %s: %s", about, fact)


REMEMBER_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you )*remember (?:that |this:?\s*)(?P<fact>.+?)[.!]?\s*$",
                              re.IGNORECASE | re.DOTALL)
FORGET_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you )*forget (?!it\b|i said)(?:about |that )?(?P<what>.+?)[.!?]?\s*$",
                            re.IGNORECASE | re.DOTALL)
FORGET_FILLER = {"the", "that", "fact", "about", "what", "said", "told", "you", "your", "know", "thing", "facts", "and", "for",
                 "this", "with", "from", "have", "saved", "remembered", "anything", "everything", "please"}
MAX_FORGET_MATCHES = 3


def as_fact(text: str) -> str:
    fact = re.sub(r"\b(I am|I'm)\b", f"{USER_NAME} is", text.strip(), flags=re.I)
    fact = re.sub(r"\bmy\b", f"{USER_NAME}'s", fact, flags=re.I)
    fact = re.sub(r"\bI\b", USER_NAME, fact)
    return fact[0].upper() + fact[1:] if fact else fact


def remember_reply(said: str) -> str | None:
    match = REMEMBER_REQUEST.match(said)
    if not match or SECRET_PATTERN.search(said) or NOT_A_FACT.search(match.group("fact")):
        return None
    fact = as_fact(match.group("fact"))
    save_facts([{"about": USER_NAME, "fact": fact}])
    return f"Got it, I'll remember that: {match.group('fact').strip()}."


def forget_reply(said: str) -> str | None:
    match = FORGET_REQUEST.match(said)
    if not match:
        return None
    words = {word for word in re.findall(r"[a-z0-9']{3,}", match.group("what").lower()) if word not in FORGET_FILLER}
    if not words:
        return None
    headers = {"Authorization": f"Bearer {HUB_KEY}"}
    graph = http_json(f"{HUB_URL}/memory/read_graph", body={}, headers=headers, timeout=20) or {}
    matches = [(entity["name"], observation) for entity in graph.get("entities") or []
               for observation in entity.get("observations") or [] if all(word in observation.lower() for word in words)]
    if not matches:
        return "I don't have anything saved about that, so there's nothing to forget."
    if len(matches) > MAX_FORGET_MATCHES:
        return f"That matches {len(matches)} things I've saved. Tell me a bit more about which one to forget."
    deletions = {}
    for name, observation in matches:
        deletions.setdefault(name, []).append(observation)
    http_json(f"{HUB_URL}/memory/delete_observations", headers=headers, timeout=20,
              body={"deletions": [{"entityName": name, "observations": items} for name, items in deletions.items()]})
    log.info("forgot %d fact(s) on request", len(matches))
    forgotten = "\n".join(f"- {observation}" for _, observation in matches)
    return f"Done, I've forgotten:\n{forgotten}"

def addressed_to_arty(config, heard: str, conversation: list[dict]) -> bool:
    previous = next((message_text(m) for m in reversed(conversation) if m.get("role") == "assistant"), "") or "(nothing yet)"
    try:
        reply = http_json(f"{OLLAMA_URL}/api/chat", timeout=60, body={
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 20, "num_ctx": config.get("options", {}).get("num_ctx", 12288)},
            "messages": [{"role": "user", "content": ADDRESSED_PROMPT % (previous[-400:], heard[:400])}]})
        return json.loads(reply["message"]["content"]).get("for_arty") is not False
    except Exception as error:
        log.warning("couldn't tell whether a voice message was for Arty: %s", error)
        return True


def extract_memories(config, user_text, answer):
    with memory_lock:
        try:
            reply = http_json(f"{OLLAMA_URL}/api/chat", body={
                "model": config["model"], "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_predict": 512, "num_ctx": config.get("options", {}).get("num_ctx", 12288)},
                "messages": [{"role": "user", "content": EXTRACT_PROMPT % user_text[:2000]}],
            })
            facts = json.loads(reply["message"]["content"]).get("facts") or []
            if isinstance(facts, list) and facts:
                save_facts([f for f in facts if isinstance(f, dict)])
        except Exception as error:
            log.warning("memory extraction failed: %s", error)


MAX_SCHEMA_DEPTH = 8


def inline_refs(node, schemas, depth=0):
    """Replace $ref pointers with the schemas they name, so the model sees nested shapes."""
    if depth > MAX_SCHEMA_DEPTH:
        return {}
    if isinstance(node, list):
        return [inline_refs(item, schemas, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if ref:
        return inline_refs(schemas.get(ref.split("/")[-1], {}), schemas, depth + 1)
    return {key: inline_refs(value, schemas, depth + 1) for key, value in node.items() if key != "title"}


class ToolHub:
    def __init__(self):
        self.tools = []
        self.routes = {}
        self.loaded_at = 0.0

    def refresh(self):
        if self.tools and time.time() - self.loaded_at < SPEC_REFRESH_SECONDS:
            return
        tools, routes = [], {}
        for server in SERVERS:
            try:
                spec = http_json(f"{HUB_URL}/{server}/openapi.json", headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
            except (urllib.error.URLError, TimeoutError) as error:
                log.warning("tool server %s unavailable: %s", server, error)
                continue
            schemas = spec.get("components", {}).get("schemas", {})
            for path, methods in spec.get("paths", {}).items():
                operation = methods.get("post")
                if not operation:
                    continue
                name = f"{server.replace('-', '_')}__{path.strip('/')}"[:64]
                parameters = {"type": "object", "properties": {}}
                ref = (((operation.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema", {}).get("$ref")
                if ref:
                    schema = inline_refs(schemas.get(ref.split("/")[-1], {}), schemas)
                    parameters = {"type": "object", "properties": schema.get("properties", {}), "required": schema.get("required", [])}
                tools.append({"type": "function", "function": {"name": name, "description": (operation.get("description") or operation.get("summary") or "")[:1024], "parameters": parameters}})
                routes[name] = f"{HUB_URL}/{server}{path}"
        if tools:
            self.tools, self.routes, self.loaded_at = tools, routes, time.time()
            log.info("loaded %d tools", len(tools))

    def call(self, name, arguments):
        route = self.routes.get(name)
        if not route:
            return f"unknown tool {name}"
        try:
            result = http_json(route, body=arguments or {}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=900)
        except urllib.error.HTTPError as error:
            return f"tool error {error.code}: {error.read().decode(errors='replace')[:500]}"
        except (urllib.error.URLError, TimeoutError) as error:
            return f"tool unavailable: {error}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return text[:TOOL_RESULT_CHARS]


hub = ToolHub()


def recalled_memories():
    try:
        graph = http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
    except (urllib.error.URLError, TimeoutError):
        return ""
    entities = (graph or {}).get("entities") or []
    lines = [f"- {e.get('name')} ({e.get('entityType')}): {'; '.join(e.get('observations') or [])}" for e in entities]
    text = "\n".join(lines)
    return text[-MEMORY_CHARS:]


def system_prompt(config, client_system="", background=False, toolless=False):
    """The instructions, which stay the same from one message to the next, so Ollama can reuse
    its cache of them (and of the tool list after them) instead of reading ~4000 tokens again."""
    # Tool-less models get no tool instructions: they can't use them, and small ones have short context windows.
    instructions = TOOLLESS_NOTE if toolless else config["system_prompt"]
    system = (config["persona"] + "\n\n" if config["persona"] else "") + instructions
    if background:
        system += "\n\n" + BACKGROUND_INSTRUCTIONS
    if client_system:
        system += "\n\nInstructions from the app you are talking through:\n" + client_system
    return system


def current_context(config):
    """What changes every message: memories, recent conversations and the time. It rides at the start of
    the latest user message (see with_context), after everything that can be cached."""
    memories = recalled_memories()
    log.info("loaded %d memory lines", memories.count("\n") + 1 if memories else 0)
    if memories:
        known = (
            "FACTS YOU ALREADY KNOW ABOUT THE USER (from your long-term memory). "
            "Answer questions about the user from these facts first; only search memory if the answer is not here.\n"
            + memories
        )
    else:
        known = "You have no saved memories about the user yet."
    recent = recent_exchanges(config)
    if recent:
        known += "\n\nBACKGROUND ONLY, do not continue these: your most recent earlier conversations with the user (any app), oldest first:\n" + recent
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return known + (f"\n\nIt is now {now:%A %d %B %Y, %H:%M} (UK time, {now:%Z}, UTC{offset[:3]}:{offset[3:]}). "
                    "For the time or time difference in another place, use the time tool's convert_time, which states the difference.")


# Tools the brain may run by itself before the model answers (see look_up_first): read-only ones only.
LOOKUP_TOOLS = {"homelab__proxmox_status", "homelab__service_status", "weather", "calendar_events", "deadlines_show",
                "list_show", "quiz_progress", "week_recap", "research__web_search", "workspace__gmail_unread", "date_facts",
                "currency_facts", "workspace__look_at_desktop", "homelab__pufferpanel_servers"}
# In a lookup rule's arguments, this stands for the user's message (e.g. the web search query).
MESSAGE_PLACEHOLDER = "{message}"
# Polite padding that only makes a search query worse.
QUERY_PADDING = re.compile(r"^(hey |hi )?(arty,? )?(can|could|would) you (please )?(tell me|show me|find( me)?|let me know)?\s*|\bplease\b", re.I)
MAX_LOOKUPS = 2
LOOKUP_TEMPERATURE = 0.15
HARD_REQUEST = re.compile(r"\b(think (it |this |that )?(through|hard|carefully)|step[- ]by[- ]step|work out|figure out|calculate|"
                          r"solve|puzzle|riddle|prove|plan (out |my |a |the )?|compare|pros and cons|"
                          r"trade-?offs?|debug|why (is|does|do|did|won'?t|isn'?t|doesn'?t)|optimi[sz]e|design|strategy|"
                          r"best way|what if|estimate)\b", re.I)
MULTI_STEP_REQUEST = re.compile(r"\b(and then|after that|once (that'?s|it'?s) done|first\b.{3,80}\bthen|"
                                r"(build|create|make|write|code) (me )?(an? |the )?(\w+ )?(app|bot|website|site|program|tool|game|"
                                r"script|extension|server|api|dashboard)s?|set up|install|automate|orchestrat\w*|migrate|"
                                r"refactor|research)\b", re.I)
THINKING_MIN_TOKENS = 6144
WRITE_ONLY_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you |would you )?(?:write|give me|show me|"
                                r"what'?s|what is|how do i)\b.*\b(?:one-?liner|command|script|function|code|regex|snippet|query|alias)\b", re.I)
RUN_REQUEST = re.compile(r"\b(run|execute|test|try (it|this|that)|on your (computer|desktop|pc)|check (it|that) works)\b", re.I)
RUN_TOOLS = {"workspace__run_command", "workspace__read_terminal", "workspace__list_files", "workspace__read_file",
             "workspace__write_file", "workspace__edit_file"}
WRITE_ONLY_NOTE = "\n\n(Reply with the code itself. Don't run anything or look around your computer for this.)"
WRITE_AND_RUN_NOTE = "\n\n(Write it, then run it on your computer with run_command and show what it printed.)"


def thinking_for(config, request_text="", writing_job=False) -> bool:
    setting = config.get("think", False)
    if setting != "auto":
        return bool(setting)
    model = models.find(config["model"])
    if not model or not model.get("thinking"):
        return False
    request_text = request_text or ""
    return writing_job or bool(HARD_REQUEST.search(request_text) or MULTI_STEP_REQUEST.search(request_text))


def steer_last_request(history, note: str):
    for index in range(len(history) - 1, 0, -1):
        if history[index].get("role") == "user":
            if isinstance(history[index].get("content"), str):
                history[index] = {**history[index], "content": history[index]["content"] + note}
            return


def with_thinking(body, think: bool):
    options = body.get("options") or {}
    if think:
        options = {**options, "num_predict": max(int(options.get("num_predict", 0)), THINKING_MIN_TOKENS)}
    return {**body, "think": think, "options": options}


# Longer messages are mostly pasted text (an error, a document), where a keyword means nothing.
LOOKUP_MAX_MESSAGE_CHARS = 300
# The labels the looked-up results carry in the notes, which small models sometimes copy into the reply.
LOOKUP_LABEL = re.compile(r"[ \t]*:?[ \t]*\[(?:" + "|".join(sorted(LOOKUP_TOOLS)) + r")\]")
LOOKUP_CHARS = 2500


# A lookup that didn't work: an error, or the desktop's browser not answering.
LOOKUP_FAILED = re.compile(TOOL_FAILED.pattern + r"|isn't running|is paused|not reachable|timed out|isn't set up", re.I)
SIGN_IN_HELP = ("I can't open your Gmail right now: the Gmail window on my desktop isn't signed in to Google. Sign in once "
                "(Proxmox > VM 105 > Console, then the \"Sign in to Google (Gmail)\" icon on its desktop), leave that window "
                "open, and ask me again.")


def matches_lookup(config, said: str) -> bool:
    return len(said) <= LOOKUP_MAX_MESSAGE_CHARS and any(
        rule.get("tool") in LOOKUP_TOOLS and re.search(rule.get("pattern", "(?!)"), said, re.I) for rule in config.get("lookups", []))


def look_up_first(config, said: str) -> tuple[list[str], str, str | None]:
    """Runs the read-only tools a message plainly needs ("what are the IPs", "will it rain") before the
    model answers, so a small model only has to put the result into words instead of picking among dozens
    of tools (which it often gets wrong). Rules: "lookups" in brain.json. Returns (tool names, notes text,
    and a reply to give instead when a lookup hit a sign-in wall)."""
    used, results, blocked, failed = [], [], None, []
    if len(said) > LOOKUP_MAX_MESSAGE_CHARS:
        return [], "", None
    for rule in config.get("lookups", []):
        name = rule.get("tool", "")
        if len(used) >= MAX_LOOKUPS or name in used or name not in LOOKUP_TOOLS:
            continue
        if rule.get("skip_for_changes") and DO_REQUEST.search(said):
            continue

        if not re.search(rule.get("pattern", "(?!)"), said, re.I):
            continue
        query = QUERY_PADDING.sub("", said).strip(" ?.!")[:200]
        arguments = {key: query if value == MESSAGE_PLACEHOLDER else value for key, value in (rule.get("arguments") or {}).items()}
        try:
            result = run_native(name, arguments, config, False, None)[0] if name in NATIVE_TOOLS else hub.call(name, arguments)
        except Exception as error:  # a failed lookup just leaves the model to use its tools
            log.warning("lookup %s failed: %s", name, error)
            continue
        if str(result).startswith("BLOCKED:"):
            # Small models turn this into vague advice; say exactly what's wrong and what to do.
            blocked = SIGN_IN_HELP if name.startswith("workspace__gmail") else str(result).split(". Do not")[0]
            continue
        if LOOKUP_FAILED.search(str(result)):
            # Left to the model, a failed check becomes "nothing new": say it couldn't be checked, and why.
            log.warning("lookup %s didn't work: %s", name, str(result)[:200])
            failed.append(str(result).strip())
            continue
        used.append(name)
        results.append(f"[{name}]\n{str(result)[:LOOKUP_CHARS]}")
        log.info("looked up %s before answering", name)
    if blocked and not results:
        return [], "", blocked
    if not results and not failed:
        return [], "", None
    results += [f"[couldn't be checked; if the user asked about this, tell them it didn't work and why]\n{reason}" for reason in failed]
    return used, ("\n\nAlready looked up for this message. Answer the message from this, and only this, unless it "
                  "asks for something else (don't call these tools again):\n"
                  + "\n\n".join(results)), blocked


def with_context(history: list[dict], context: str):
    """Puts the per-message context at the start of the latest user message. Not as a system message:
    Ollama merges every system message into the one block above the tool list, so a changing one there
    makes the model re-read the whole prompt (some 4,000 tokens, about 9 seconds) on every message.
    The message is replaced, not edited: it's shared with the conversation that gets saved."""
    note = f"<notes>\nYour notes for this message (not written by the user):\n{context}\n</notes>\n\n"
    last_user = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=None)
    if last_user is None:
        history.append({"role": "user", "content": note.strip()})
    else:
        history[last_user] = {**history[last_user], "content": note + str(history[last_user].get("content") or "")}


def tool_parameters(name: str) -> dict:
    """The parameters a tool takes (its JSON schema's properties), or {} when unknown."""
    definition = NATIVE_TOOLS.get(name) or next((t for t in hub.tools if t["function"]["name"] == name), None)
    return ((definition or {}).get("function", {}).get("parameters") or {}).get("properties") or {}


def repair_arguments(name: str, arguments: dict) -> dict:
    """Small models sometimes nest a call's arguments under a made-up key ({"title: when": {"title": ...}}).
    Lifts the tool's real parameters out of any nested object; anything already right is left alone."""
    known = tool_parameters(name)
    if not known or all(key in known for key in arguments):
        return arguments
    # Stray punctuation in a key ({"url: ": ...}) is the same mistake in another form.
    arguments = {(cleaned if (cleaned := str(key).strip(" :\"'")) in known else key): value for key, value in arguments.items()}
    repaired = {key: value for key, value in arguments.items() if key in known}
    for value in arguments.values():
        if isinstance(value, dict):
            repaired.update({key: inner for key, inner in value.items() if key in known and key not in repaired})
    if repaired != arguments:
        log.info("repaired %s arguments: %s -> %s", name, json.dumps(arguments)[:200], json.dumps(repaired)[:200])
    return repaired or arguments


def parse_arguments(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def task_links(task: dict, actions: list[tuple[str, str]]) -> list[dict]:
    """Buttons for a paused task: a callback ("t:<id>:<action>") the phone and desktop apps answer through the task
    API, and the equivalent home-network /hooks link."""
    return [{"label": label, "callback": f"t:{task['id']}:{action}",
             "url": f"{PUBLIC_URL}/hooks/tasks/{task['id']}/{action}?token={task['token']}"} for label, action in actions]


ON_THE_WEB = re.compile(r"\b(site|website|web|browser|desktop|page|account|google|gmail|linkedin|captcha|\w+\.(com|co|org|net|io|uk))\b",
                        re.IGNORECASE)
LOGIN_WORDS = re.compile(r"sign in|log ?in|captcha|bot check|verif|human", re.IGNORECASE)


# Tools that work on the desktop (browser, Gmail window, screen, terminal): only after one of them can a website have
# asked for a sign-in or a CAPTCHA.
DESKTOP_TOOLS = ("workspace__",)
NOTHING_TO_SIGN_IN = ("Nothing on the desktop was used, so there's nothing for the user to sign in to or pass. "
                      "Don't use ask_user; reply to the user directly.")


def question_for_user(message: str) -> str:
    """What Arty says in chat when it needs the user to do something on its desktop."""
    text = message.strip().rstrip(".") + "."
    if LOGIN_WORDS.search(text) and ON_THE_WEB.search(text):  # not "sign in to your phone": that's a confused model
        text += (" On my desktop (Proxmox web UI > VM 105 ai-desktop > Console), double-click \"Sign in to websites\", "
                 "sign in, then close that window. Tell me when it's done and I'll carry on.")
    return text


def run_native(name, arguments, config, background, task):
    """Run one of the brain's own tools. Returns (result text, waiting dict or None)."""
    if name == "recall_conversations":
        return recall(config, arguments.get("query", ""), arguments.get("limit", 5)), None
    if name == "ask_user":
        message = str(arguments.get("message", "")).strip() or "Arty needs your help"
        # Chat ends the turn and shows the question itself; background pauses the task.
        return "", {"kind": "user" if background else "chat_question", "message": message}
    if name == "notify_user":
        return notify.send(str(arguments.get("title") or "Arty"), str(arguments.get("message", ""))), None
    if name == "start_background_task":
        instructions = str(arguments.get("instructions", "")).strip()
        if not instructions:
            return "Give the task's instructions.", None
        created = store.create_task(str(arguments.get("title") or instructions[:60]), instructions, "chat")
        return f"Started background task #{created['id']}. You'll get a notification when it's done.", None
    if name == "schedule_task":
        return create_schedule(arguments)[1], None
    if name == "list_scheduled":
        rows = store.list_schedules()
        if not rows:
            return "Nothing is scheduled.", None
        return "\n".join(f"#{s['id']} {s['title']}: {s['description']}, next {datetime.fromtimestamp(s['next_run']):%a %d %b %H:%M}" for s in rows if s["next_run"]), None
    if name == "cancel_scheduled":
        found = store.update_schedule(int(arguments.get("schedule_id", 0)), enabled=0, next_run=None)
        return ("Cancelled." if found else "No such scheduled job."), None
    if name == "list_tasks":
        rows = store.list_tasks(limit=10)
        if not rows:
            return "No background tasks yet.", None
        return "\n".join(f"#{t['id']} [{t['status']}] {t['title']}" + (f": {t['result'][:200]}" if t["result"] else "") for t in rows), None
    if name == "list_models":
        return models.list_text(config["model"]), None
    if name == "ask_other_model":
        return models.ask(str(arguments.get("model", "")), str(arguments.get("prompt", "")), config), None
    if name in personal.TOOLS:
        return personal.TOOLS[name][0](arguments), None
    if name in reckon.TOOLS:
        return reckon.TOOLS[name][0](arguments), None
    if name in sharing.TOOLS:
        return sharing.TOOLS[name][0](arguments), None
    if name == "weather":
        return weather.forecast(arguments.get("days", 1), config.get("weather")), None
    if name == "week_recap":
        return week_recap(config), None
    if name == "calendar_events":
        return calendar_feed.events(arguments.get("days", 1)), None
    if name == "calendar_add":
        return calendar_feed.add(arguments.get("title"), arguments.get("when"), arguments.get("duration_minutes"),
                                 arguments.get("location", ""), arguments.get("notes", "")), None
    if name == "calendar_change":
        return calendar_feed.change(arguments.get("event_id"), arguments.get("title"), arguments.get("when"),
                                    arguments.get("duration_minutes"), arguments.get("location")), None
    if name == "calendar_remove":
        return calendar_feed.remove(arguments.get("event_id")), None
    if name == "convert_file":
        try:
            made = media.convert(str(arguments.get("file_id", "")), str(arguments.get("format", "")),
                                 optional_int(arguments.get("width")), optional_int(arguments.get("height")), optional_int(arguments.get("quality")))
        except media.MediaError as error:
            return f"Couldn't convert it: {error}.", None
        return (f"Converted. Give the user this download link exactly as written (it works for 24 hours): "
                f"{media.link(made)}\nIf the user's message also asked something else (e.g. what's in the file), answer that too."), None
    if name == "switch_model":
        return models.switch(str(arguments.get("model", "")), config["model"], notify.send), None
    return f"unknown tool {name}", None


def reminder_wording(text: str) -> str:
    """The reminder as the user should read it: "Reminder: drink some water" -> "Drink some water."."""
    text = re.sub(r"^\s*(⏰\s*)?(reminder|remember|remind me|don't forget)\s*(to\b)?\s*[:\-–]?\s*(to\s+)?", "", str(text), flags=re.I).strip()
    return (text[:1].upper() + text[1:]) if text else "Your reminder."


def week_recap(config) -> str:
    """The last 7 days: what the user asked (one line each) and the background jobs that finished."""
    cutoff = datetime.now().timestamp() - 7 * 86400
    asked = []
    for record in archive_records(config):
        if datetime.fromisoformat(record["time"]).timestamp() < cutoff:
            break
        if not record["user"].startswith("[background task"):
            asked.append(f"- {record['time'][:10]}: {APP_NOTES.sub('', record['user'])[:150]}")
    jobs = [f"- {t['title']}" for t in store.list_tasks("done", 30) if t["updated"] >= cutoff]
    return ("What the user asked this week (newest first):\n" + "\n".join(asked[:60] or ["- nothing"])
            + "\n\nBackground jobs finished:\n" + "\n".join(jobs or ["- none"])
            + "\n\nSum this up talking to the user as \"you\" (\"You asked me about...\", \"We sorted out...\"). "
              "Don't write it as their own post or in their voice unless they ask for a post.")


def optional_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


CONVERT_REQUEST = re.compile(r"\bconvert (?:it|this|that|them|the (?:file|image|picture|photo|video|audio|recording) )?\s*(?:in)?to (?:an? )?\.?(?P<format>[a-z0-9]{2,5})\b",
                             re.I)
ATTACHED_FILE_ID = re.compile(r"\bfile id (?P<id>[\w-]+)\.\)")


def convert_attachment(said: str, expanded_text: str) -> str | None:
    request = CONVERT_REQUEST.search(said)
    attached = ATTACHED_FILE_ID.findall(expanded_text)
    if not request or len(attached) != 1:
        return None
    return run_native("convert_file", {"file_id": attached[0], "format": request.group("format")}, load_config(), False, None)[0]


SENDS_FILES = {"convert_file", *sharing.TOOLS}
TOOL_UNLOCKS = {"start_screen_recording": sharing.RECORDING_UNLOCKS}
DOWNLOAD_LINK = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+/" + media.DOWNLOAD_PREFIX + r"/[^)\s]+)\)")


# ---------- attachments ----------

def vision_model(wanted: str = ""):
    model = models.find(wanted) if wanted else None
    if not model or not model["vision"]:
        model = next((m for m in models.installed() if m["vision"]), None)
    if model is None:
        raise ApiError(400, "No vision model is installed. Run: ai-pull qwen2.5vl:7b")
    return model


def look(images: list[str], prompt: str) -> str:
    """Asks the vision model about base64 images."""
    config = load_config()
    options = {k: v for k, v in config.get("options", {}).items() if k in ("num_ctx", "num_predict")}
    raw = http_json(f"{OLLAMA_URL}/api/chat", body={"model": vision_model()["name"], "stream": False, "options": options, "keep_alive": 0,
                                                   "messages": [{"role": "user", "content": prompt, "images": images}]})
    return ((raw or {}).get("message") or {}).get("content", "").strip() or "(the vision model gave no description)"


def reload_main_model(config: dict):
    def load():
        try:
            http_json(f"{OLLAMA_URL}/api/generate", body={"model": config["model"], "keep_alive": config.get("keep_alive", "10m")}, timeout=300)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log.warning("could not reload %s after a vision look: %s", config["model"], error)
    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    return thread


def stored_attachment(part: dict) -> dict | None:
    """Stores (or finds) the file behind one content part: image_url data URIs, input_audio, or an uploaded file id."""
    kind = part.get("type")
    if kind == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        if not url.startswith("data:") or "," not in url:
            return None
        header, data = url.split(",", 1)
        mime = header[5:].split(";", 1)[0] or "image/png"
        return media.save_bytes(base64.b64decode(data), f"image{mimetypes.guess_extension(mime) or '.png'}", mime, "chat")
    if kind == "input_audio":
        audio = part.get("input_audio") or {}
        extension = str(audio.get("format") or "wav")
        return media.save_bytes(base64.b64decode(audio.get("data", "")), f"audio.{extension}", "", "chat")
    if kind == "file":
        return media.get(str((part.get("file") or {}).get("file_id", "")))
    return None


def expand_attachments(messages: list[dict]) -> tuple[list[dict], bool]:
    """Replaces attachments in user messages with text the chat model can use: what's in them, and the
    file id for convert_file. Returns (messages, whether there were any)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    found = False
    expanded = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            expanded.append(message)
            continue
        question = message_text(message)
        notes = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") == "text":
                continue
            found = True
            try:
                meta = stored_attachment(part)
            except (ValueError, OSError) as error:
                notes.append(f"[An attachment couldn't be read: {error}]")
                continue
            if meta is None:
                notes.append("[An attachment has expired or couldn't be read; ask the user to attach it again if you need it]")
                continue
            # Plain sentences: small models copy bracket/link-like notes back into their replies as fake links.
            heading = f'(The user attached a{"n" if meta["kind"][0] in "aeiou" else ""} {meta["kind"]} file called {meta["name"]}, {meta["size"] // 1024} KB, file id {meta["id"]}.)'
            if index != last_user and not meta.get("analysis"):
                notes.append(heading)
                continue
            try:
                notes.append(f"{heading}\nWhat's in it: {media.analyse(meta, question, look)}")
            except (media.MediaError, ApiError, urllib.error.URLError, TimeoutError) as error:
                log.warning("could not analyse %s: %s", meta["id"], error)
                notes.append(f"{heading}\n(Couldn't look inside it: {error})")
        text = "\n\n".join(notes + ([question] if question.strip() else []))
        expanded.append({**message, "content": text})
    return expanded, found


def create_schedule(arguments) -> tuple[dict | None, str]:
    reminder = str(arguments.get("reminder_text", "") or arguments.get("message", "")).strip() or None
    instructions = str(arguments.get("instructions", "")).strip() or (f"Remind the user: {reminder}" if reminder else "")
    if not instructions:
        return None, "Give reminder_text (for a reminder) or instructions (for a job)."
    try:
        cron, first, description = timing.parse_when(str(arguments.get("when", "")))
    except ValueError as error:
        return None, f"Couldn't schedule it: {error}"
    title = str(arguments.get("title") or (reminder or instructions)[:60])
    created = store.create_schedule(title, instructions, cron, description, first.timestamp(), reminder)
    minutes = max(1, round((first - datetime.now()).total_seconds() / 60))
    away = f"in {minutes} minutes" if minutes < 120 else f"in about {round(minutes / 60)} hours"
    return created, f"Scheduled #{created['id']} ({description}). First run {away}, at {first:%H:%M on %a %d %b}."


def to_ollama_messages(messages):
    """Client apps send OpenAI-style history: assistant tool_calls with JSON-string arguments and
    tool results keyed by tool_call_id. Ollama wants argument objects and the tool's name."""
    names, out = {}, []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function", {})
                names[call.get("id", "")] = function.get("name", "")
                calls.append({"function": {"name": function.get("name", ""), "arguments": parse_arguments(function.get("arguments"))}})
            out.append({"role": "assistant", "content": message_text(message), "tool_calls": calls})
        elif role == "tool":
            out.append({"role": "tool", "content": message_text(message),
                        "tool_name": message.get("tool_name") or names.get(message.get("tool_call_id", ""), "")})
        else:
            out.append({**message, "content": message_text(message)})
    return out


def run_agent(config, state, background=False, chat_approved=False, task=None, client_system="", client_tools=None, skip_groups=(),
              draft=None, looked_up=None):
    """The tool loop. state = {"messages": [...], "context": {...}, "groups": [...]} and is updated in place.
    Returns {"status": "done", "answer": ...} or {"status": "waiting", "waiting": {...}}.
    With a draft (drafts.Draft), the answer's safe sentences go out while the model writes."""
    hub.refresh()
    config = {**config, "tool_budget": models.budget_for(config, config["model"])}
    messages = state["messages"]
    context = state.setdefault("context", {})
    requested = set(state.setdefault("groups", []))
    model_info = models.find(config["model"])
    toolless = bool(model_info) and not model_info["tools"]
    history = [{"role": "system", "content": system_prompt(config, client_system, background, toolless)}] + messages
    looked_up = looked_up or ([], "", None)
    newest_request = APP_NOTES.sub("", message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {})))
    # Asked for something written (a post, an email): in the user's own voice (style.py).
    voice = style.voice_note() if style.is_writing_request(newest_request) else ""
    with_context(history, current_context(config) + looked_up[1] + voice)
    native = [definition for name, definition in NATIVE_TOOLS.items() if not (background and name in BACKGROUND_ONLY_EXCLUDED)]
    all_tools = [] if toolless else hub.tools + native
    if skip_groups:
        # The calling app does these itself (e.g. the desktop app drives the user's own browser).
        skipped = [pattern for group in skip_groups for pattern in config.get("tool_groups", {}).get(group, {}).get("tools", [])]
        all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], skipped)]
    if looked_up[0]:
        all_tools = [t for t in all_tools if t["function"]["name"] not in looked_up[0]]  # its result is already in the notes
    request_text = " ".join(message_text(m) for m in messages if m.get("role") == "user")[-2000:]
    rules = config.get("approval", {})
    max_rounds = int(config.get("background", {}).get("max_rounds", 30)) if background else int(config.get("max_tool_rounds", MAX_TOOL_ROUNDS))
    tool_log, raw = list(looked_up[0]), {}
    context["failed"] = []  # tools that didn't work this turn (see be_honest)
    nudged = False
    searched_for_it = False
    client_names = {tool["function"]["name"] for tool in client_tools or []}
    last_request = APP_NOTES.sub("", message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {}))).strip()
    think = thinking_for(config, last_request)
    if not background and WRITE_ONLY_REQUEST.search(last_request):
        if RUN_REQUEST.search(last_request):
            steer_last_request(history, WRITE_AND_RUN_NOTE)
        else:
            all_tools = [tool for tool in all_tools if tool["function"]["name"] not in RUN_TOOLS]
            steer_last_request(history, WRITE_ONLY_NOTE)

    def add(message):
        messages.append(message)
        history.append(message)

    # A plain question whose answer was looked up first: the model answers from that, with no tools to
    # wander off with (small models otherwise pick unrelated ones). Asked to do something, it keeps them.
    answer_from_lookups = bool(looked_up[0]) and not background and not ACTION_REQUEST.search(last_request) \
        and not DO_REQUEST.search(last_request)
    for round_number in range(max_rounds):
        if task and (store.get_task(task["id"]) or {}).get("status") == "cancelled":
            return {"status": "cancelled", "tools_used": tool_log, "raw": raw}
        tools = policy.select_tools(all_tools, config, request_text, requested)
        if len(tools) < len(all_tools):
            tools = [policy.use_tools_definition(config)] + tools  # first: it never changes, so it stays cached
        if client_tools and not toolless:
            # The calling app's own tools (e.g. the desktop app's hands on Drew's PC): always offered.
            tools = tools + client_tools
        if answer_from_lookups and round_number == 0:
            tools = []
        options = config.get("options", {})
        if answer_from_lookups:
            options = {**options, "temperature": LOOKUP_TEMPERATURE}  # stick to the looked-up facts
        body = with_thinking({"model": config["model"], "messages": history, "stream": False,
                              "options": options, "keep_alive": config.get("keep_alive", "10m")}, think)
        if tools:  # Ollama rejects a tools list for models without tool support
            body["tools"] = tools
        if draft:
            # Hold back what the checks below might still throw away: a reply that will be sent back to use
            # its tools, or replaced by a web search, mustn't have been shown (or said out loud) already.
            may_nudge = not nudged and bool(tools) and not tool_log
            may_search = nudged and not tool_log and not searched_for_it and "research__web_search" in hub.routes
            draft.start_round(allowed=not (may_nudge and ACTION_REQUEST.search(last_request))
                              and not (may_search and SEARCH_REQUEST.search(last_request)))

            def risky(text, may_nudge=may_nudge, may_search=may_search):
                if PROMPT_LEAK.search(text) or TOOL_TALK.search(text):
                    return True
                if may_nudge and (PROMISE.search(text) or CLAIMED.search(text)):
                    return True
                return (may_nudge or may_search) and bool(CLARIFYING.search(text) or "web_search" in text)
            raw = stream_chat(body, lambda piece: draft.feed(piece, risky))
        else:
            raw = http_json(f"{OLLAMA_URL}/api/chat", body=body)
        log.info("model: loaded in %.1fs, read the %s-token prompt in %.1fs (cached parts are free), wrote %s in %.1fs",
                 raw.get("load_duration", 0) / 1e9,
                 raw.get("prompt_eval_count", "?"), raw.get("prompt_eval_duration", 0) / 1e9,
                 raw.get("eval_count", "?"), raw.get("eval_duration", 0) / 1e9)
        message = raw["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            answer = message.get("content") or ""
            if background and context.get("blocked"):
                # Small models tend to report "please sign in" and stop; pause and ask properly instead.
                host = context.pop("blocked")
                state["groups"] = sorted(requested)
                return {"status": "waiting", "waiting": {"kind": "user", "message": f"Please sign in (or pass the bot check) on {host} in the browser on my desktop.",
                                                         "call": {"name": "ask_user", "arguments": {}}}, "tools_used": tool_log, "raw": raw}
            if not background and nudged and not tool_log and not searched_for_it and "research__web_search" in hub.routes and (
                    CLARIFYING.search(answer) or "web_search" in answer or SEARCH_REQUEST.search(last_request)):
                # Sent back once and still no search: run it ourselves and have the model answer from the results.
                searched_for_it = True
                results = hub.call("research__web_search", {"query": last_request[:200]})
                tool_log.append("research__web_search")
                log.info("searched for the model: %s", last_request[:80])
                history.append({"role": "assistant", "content": answer})
                history.append({"role": "user", "content": f"(Web search results for my message:\n{results[:TOOL_RESULT_CHARS]}\n\n"
                                "Now answer my message from these results, in your own words, briefly. Say which source if it helps.)"})
                continue
            if not background and not nudged and tools and not tool_log and (
                    PROMISE.search(answer) or CLAIMED.search(answer) or CLARIFYING.search(answer) or ACTION_REQUEST.search(last_request)):
                # Small models describe doing things ("I've set a reminder", "here are my findings") without
                # calling a tool; send them back once to actually do it.
                nudged = True
                history.append({"role": "assistant", "content": answer})
                history.append({"role": "user", "content": "(You haven't used any tools, so none of that has actually happened. Do it now "
                                "with your tools: schedule_task (with reminder_text) for reminders, web_search for anything you need to "
                                "look up (including anything I mentioned that you don't recognise, before asking me what I mean), "
                                "calendar_add for events. Only then answer, and only with what the tools gave you. If none of your tools can "
                                "do it, say so plainly in a sentence instead: never pretend it's done.)"})
                continue
            if not background and not nudged and PRETEND_TOOLS.search(answer):
                nudged = True
                log.info("reply played at using tools; sent back once")
                history.append({"role": "assistant", "content": answer})
                history.append({"role": "user", "content": "(Don't write out tool calls, function names or made-up tool results. "
                                "Answer my message in plain words, from your notes if they cover it.)"})
                continue
            if not nudged and tools and answer.count("```") % 2 == 1 and FUMBLED_CALL.search(answer):
                # It started writing a tool call as text and stopped: have it make the call properly.
                nudged = True
                log.info("reply ended in a half-written tool call; sent back once")
                history.append({"role": "assistant", "content": answer})
                history.append({"role": "user", "content": "(Your reply stopped at the start of a code block instead of using a tool. "
                                "If you need a tool, call it properly now; otherwise answer my message.)"})
                continue
            if not answer.strip() and not nudged:
                # Small models occasionally return nothing at all; one nudge almost always gets a real answer.
                nudged = True
                history.append({"role": "user", "content": "(Your reply was empty. Answer my last message, using your tools if needed.)"})
                continue
            if raw.get("done_reason") == "length":
                log.warning("reply hit the num_predict cap and was cut off")
                answer += "\n\n(I hit my reply length limit, so this was cut short. Ask me to continue.)"
            state["groups"] = sorted(requested)
            return {"status": "done", "answer": answer, "tools_used": tool_log, "raw": raw}
        written = (message.get("content") or "").strip()
        if not background and all(call.get("function", {}).get("name") == "use_tools" for call in calls) \
                and len(written) >= ANSWERED_ALREADY_CHARS and not ACTION_REQUEST.search(last_request) \
                and not (PROMISE.search(written) or CLAIMED.search(written)):
            # It answered a plain question, then asked for tools it doesn't need (the next round would only
            # chat about the unlocked tools): keep the answer. Not when it's about to actually do something.
            state["groups"] = sorted(requested)
            return {"status": "done", "answer": message["content"], "tools_used": tool_log, "raw": raw}
        if draft:
            draft.before_tools()
        client_calls = [call for call in calls if call.get("function", {}).get("name") in client_names]
        if client_calls:
            state["groups"] = sorted(requested)
            return {"status": "client_tools", "calls": client_calls, "content": message.get("content") or "",
                    "tools_used": tool_log + [c["function"]["name"] for c in client_calls], "raw": raw}
        add({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            function = call.get("function", {})
            name = function.get("name") or ""
            arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
            tool_log.append(name)
            if name == "use_tools":
                wanted = [g for g in arguments.get("groups") or [] if g in config.get("tool_groups", {})]
                requested.update(wanted)
                result = f"Unlocked: {', '.join(wanted) or 'nothing (unknown group)'}. Those tools are available from your next step."
            elif name in NATIVE_TOOLS:
                result, waiting = run_native(name, arguments, config, background, task)
                requested.update(TOOL_UNLOCKS.get(name, ()))
                if waiting and waiting["kind"] == "chat_question" and not any(used.startswith(DESKTOP_TOOLS) for used in tool_log):
                    # Nothing on the desktop was tried, so no website can have blocked it: the model reached for this
                    # when unsure (it once answered "how do I kill someone" with the tool's sign-in example).
                    log.info("ignored ask_user with no desktop tool used: %s", waiting["message"][:120])
                    result, waiting = NOTHING_TO_SIGN_IN, None
                if waiting and waiting["kind"] == "chat_question":
                    state["groups"] = sorted(requested)
                    return {"status": "done", "answer": question_for_user(waiting["message"]), "tools_used": tool_log, "raw": raw}
                if waiting:
                    waiting["call"] = {"name": name, "arguments": arguments}
                    state["groups"] = sorted(requested)
                    return {"status": "waiting", "waiting": waiting, "tools_used": tool_log, "raw": raw}
            else:
                reason = policy.approval_reason(name, arguments, context, rules)
                if reason and background:
                    state["groups"] = sorted(requested)
                    return {"status": "waiting", "waiting": {"kind": "approval", "message": reason, "call": {"name": name, "arguments": arguments}},
                            "tools_used": tool_log, "raw": raw}
                if reason and not chat_approved:
                    # Ask in Arty's own words rather than handing the model an instruction it might echo.
                    state["groups"] = sorted(requested)
                    return {"status": "done", "answer": f"Before I do that, I need your OK: {reason}. Shall I go ahead?",
                            "tools_used": tool_log, "raw": raw}
                else:
                    if background and task and name.startswith("workspace__") and not task.get("snapshot_taken") \
                            and config.get("background", {}).get("snapshot_before_desktop_work") and "homelab__snapshot_desktop" in hub.routes:
                        log.info("task %s: snapshot before desktop work: %s", task["id"], hub.call("homelab__snapshot_desktop", {"note": f"before task #{task['id']}: {task['title']}"}))
                        task["snapshot_taken"] = 1
                        store.update_task(task["id"], snapshot_taken=1)
                    result = hub.call(name, arguments)
                    policy.remember_tool_result(context, name, result)
            log.info("tool %s(%s) -> %d chars", name, json.dumps(arguments)[:200], len(result))
            if name != "use_tools" and TOOL_FAILED.search(result):
                context["failed"].append((name, result.strip().splitlines()[0][:200] if result.strip() else "no result"))
                result += FAILED_NOTE
            add({"role": "tool", "content": result, "tool_name": name})
    state["groups"] = sorted(requested)
    return {"status": "done", "answer": "I stopped after too many tool steps. Tell me how you'd like me to continue.", "tools_used": tool_log, "raw": raw}


# Notes an app attaches to a message for this turn only (the desktop app's <context> and <screen>):
# useful to answer it, but not what the user said, so they're kept out of the archive and memory.
APP_NOTES = re.compile(r"\s*<(context|screen)>.*?</\1>", re.DOTALL)


def ensure_reminder(conversation: list[dict], tools_used: list[str], answer: str) -> str:
    """"Remind me ..." must never end with a reminder that was promised but not set: if the model didn't
    call schedule_task, set it here from the user's own words, or say plainly that it wasn't set."""
    users = [m for m in conversation if m.get("role") == "user"]
    request = APP_NOTES.sub("", message_text(users[-1])) if users else ""
    if not REMIND_REQUEST.search(request) or "schedule_task" in tools_used:
        return answer
    parts = reminder_from(request)
    if parts is None:
        if PROMISE.search(answer) or CLAIMED.search(answer):
            return "I didn't manage to set that reminder. Tell me what and when, e.g. 'remind me to drink water in 5 minutes'."
        return answer  # e.g. Arty asked when
    log.info("setting a reminder the model only described")
    return set_reminder(parts, bool(CHAT_APP.search(request)))


def set_reminder(parts: tuple[str, str], asked_for_a_chat_app: bool = False) -> str:
    """Creates a reminder from (what, when) and says what happened. Reminders only go to Updates in Arty's apps,
    so one asked for "on Telegram" says so rather than pretending."""
    created, message = create_schedule({"reminder_text": parts[0], "when": parts[1]})
    if created is None:
        return f"I couldn't set that reminder: {message}"
    elsewhere = " (Not Telegram or Discord: everything I send you goes to Updates in the apps now.)" if asked_for_a_chat_app else ""
    return (f"Reminder set: \"{parts[0]}\" ({message.split('. ', 1)[0].split(' (', 1)[-1].rstrip(')')}). "
            f"It'll come to Updates on your phone and the desktop app.{elsewhere}")


# "What did we talk about earlier / today / yesterday?"
TALKED_ABOUT = re.compile(r"\bwhat (did|have) (we|i) (talk|chat|ask|say)(ed)?( you)?( about)?\b|\bwhat were we (talking|chatting) about\b"
                          r"|\bwhat (did|have) (we|i) (get|got|been) up to\b|\bwhat (did|have) we (do|done|get done)\b", re.IGNORECASE)
THIS_WEEK = re.compile(r"\b(this|the past|the last) week\b", re.IGNORECASE)
RECAP_PROMPT = """You are Arty, an assistant. Below is what the user said to you {when} (USER) and what you replied (ARTY),
oldest first:

{turns}

Write 2 to 5 short bullet points for the user, saying what they asked about and anything you did or set up for
them, like "- You asked about the weather" or "- I set a reminder to stretch at 20:18". Only use what's above.
Reply with the bullet points only."""
RECAP_TURN_CHARS = 200
RECAP_MAX_TURNS = 30
RECAP_MAX_BULLETS = 5


def recap_conversations(config, said: str) -> str:
    """What was talked about today, yesterday or this week (as asked), from the archive, summarised."""
    yesterday = "yesterday" in said.lower()
    week = bool(THIS_WEEK.search(said))
    day = (datetime.now() - timedelta(days=1 if yesterday else 0)).date().isoformat()
    week_start = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    turns = []
    for path in sorted(Path(config["conversations_dir"]).glob("*.jsonl"))[-2:]:
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            in_range = record.get("time", "") >= week_start if week else record.get("time", "").startswith(day)
            if in_range and record.get("user") and not record["user"].startswith("[background task"):
                turns.append(f"USER: {APP_NOTES.sub('', record['user'])[:RECAP_TURN_CHARS]}\nARTY: {str(record.get('assistant'))[:RECAP_TURN_CHARS]}")
    if not turns:
        return "We haven't talked this week." if week else "We didn't talk yesterday." if yesterday else "We haven't talked yet today."
    when = "this week" if week else "yesterday" if yesterday else "today"
    summary = complete_text(RECAP_PROMPT.format(when=when, turns="\n\n".join(turns[-RECAP_MAX_TURNS:]))).strip()
    # Only the bullet points: small models sometimes echo the instructions or add a lead-in first.
    bullets = []
    for line in summary.splitlines():
        if line.lstrip().startswith(("-", "*", "•")) and line.strip().lower() not in {b.strip().lower() for b in bullets}:
            bullets.append(line)
    bullets = [re.sub(r"^(\s*[-*•]\s*)(the )?user\b", r"\1You", line, flags=re.I) for line in bullets]
    return "\n".join(bullets[:RECAP_MAX_BULLETS]) if bullets else summary


WEATHER_QUESTION = re.compile(r"\b(weather|forecast|rain(ing|y)?|snow(ing)?|temperature|umbrella|sunny|windy)\b", re.I)
# A lookup that found nothing ("Nothing in the calendar for the next 7 days.") is the whole answer.
EMPTY_RESULT = re.compile(r"^(nothing|no (deadlines|events|items|unread|cards)|the \w+ list is empty|you don't have any)", re.I)
HASHTAG_LINE = re.compile(r"\n\s*(#\w+\s*){2,}\s*$")


CLAIM_CHECK_PROMPT = """You check an assistant's reply for honesty. The assistant can only do or change things through tools, and these are all the tools that ran while it wrote this reply:
%s

What the user said:
<<<
%s
>>>

The reply:
<<<
%s
>>>

Find every sentence where the reply says or implies the assistant did, changed, sent, set, saved, noted, made, started or booked something that no tool above did (or that a tool above tried but failed). Only actions count. Stating a fact is never a claim, even a fact about the user, their plans, their likes or what they said earlier ("Your favourite colour is blue.", "Your appointment is at 3pm.", "You said 5pm."), and neither is describing what it saw, answering, explaining, suggesting, offering or asking. Wording and language don't matter, only meaning.
Copy each such sentence exactly as it is in the reply. For each one write a short first-person correction in the reply's language that says what is actually true about that action (e.g. that it didn't do it, or that it was already like that), or "" when the sentence should simply go.
Answer only with JSON: {"false_claims": [{"text": "...", "correction": "..."}]}"""
CLAIM_CHECK_TOKENS = 400
CLAIM_CONFIRM_PROMPT = """The person said: "%s"
The assistant replied with this sentence: "%s"

Which is the sentence?
A: the assistant reporting that it carried something out, including saying that what the person asked for is now done
B: the assistant answering a question, giving information, or repeating what the person said

Answer only with JSON: {"kind": "A" or "B"}"""
CLAIM_CONFIRM_TOKENS = 20
CLAIM_CHECK_CHARS = 6000
CLAIM_CHECK_REQUEST_CHARS = 2000
HONEST_NOTHING_DONE = ("I didn't actually do that: I don't have a way to do it from here, so nothing has changed. "
                       "If you tell me how you'd like it done, I'll help where I can.")


def tools_record(tools_used: list[str], failed: list[tuple[str, str]]) -> str:
    lines = []
    for tool in dict.fromkeys(tools_used):
        if tool in NOT_ACTIONS:
            continue
        runs = tools_used.count(tool)
        errors = [error for name, error in failed if name == tool]
        if not errors:
            lines.append(f"- {tool}: worked" + (f" ({runs} times)" if runs > 1 else ""))
        elif len(errors) >= runs:
            lines.append(f"- {tool}: failed ({errors[-1][:200]})")
        else:
            lines.append(f"- {tool}: ran {runs} times, {len(errors)} failed (last error: {errors[-1][:200]})")
    return "\n".join(lines) or "- none"


def client_tool_runs(conversation: list[dict]) -> tuple[list[str], list[tuple[str, str]]]:
    last_request = max((index for index, message in enumerate(conversation) if message.get("role") == "user"), default=-1)
    results = [message for message in conversation[last_request + 1:] if message.get("role") == "tool" and message.get("tool_name")]
    used = [message["tool_name"] for message in results]
    failed = [(message["tool_name"], message_text(message)[:300]) for message in results if TOOL_FAILED.search(message_text(message))]
    return used, failed


def ask_judge(prompt: str, max_tokens: int) -> dict:
    config = load_config()
    body = {"model": config["model"], "stream": False, "format": "json", "think": False, "keep_alive": config.get("keep_alive", "10m"),
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": config.get("options", {}).get("num_ctx", 12288)},
            "messages": [{"role": "user", "content": prompt}]}
    return json.loads(http_json(f"{OLLAMA_URL}/api/chat", body=body)["message"]["content"])


def false_claims(answer: str, tools_used: list[str], failed: list[tuple[str, str]], request: str = "") -> list[dict]:
    prompt = CLAIM_CHECK_PROMPT % (tools_record(tools_used, failed), request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)",
                                   answer[:CLAIM_CHECK_CHARS])
    try:
        found = ask_judge(prompt, CLAIM_CHECK_TOKENS).get("false_claims") or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim check failed, reply left as written: %s", error)
        return []
    candidates = [claim for claim in found if isinstance(claim, dict) and isinstance(claim.get("text"), str)
                  and claim["text"].strip() and claim["text"].strip() in answer]
    return [claim for claim in candidates if reports_an_action(request, claim["text"].strip())]


def reports_an_action(request: str, sentence: str) -> bool:
    prompt = CLAIM_CONFIRM_PROMPT % (request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)", sentence)
    try:
        kind = ask_judge(prompt, CLAIM_CONFIRM_TOKENS).get("kind")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim confirmation failed, the sentence is treated as a claim: %s", error)
        return True
    return kind != "B"


def be_honest(answer: str, tools_used: list[str], failed: list[tuple[str, str]], already_sent: str = "",
              request: str = "") -> str:
    failed_names = {name for name, _ in failed}
    acted = [tool for tool in tools_used if tool not in NOT_ACTIONS and tool not in LOOKUP_TOOLS]
    if acted and not failed_names & set(acted):
        return answer
    claims = false_claims(answer, tools_used, failed, request)
    if not claims:
        return answer
    log.warning("reply claimed %d thing(s) the tools didn't do: corrected %s", len(claims),
                "; ".join(repr(claim["text"].strip()[:120]) for claim in claims))
    afterwards = []
    for claim in claims:
        text, correction = claim["text"].strip(), str(claim.get("correction") or "").strip()
        if text in already_sent:
            afterwards.append(correction or HONEST_NOTHING_DONE)
        else:
            answer = answer.replace(text, correction, 1)
    answer = re.sub(r"[ \t]{2,}", " ", answer).strip()
    if afterwards:
        answer = f"{answer}\n\n{' '.join(afterwards)}".strip()
    return answer or HONEST_NOTHING_DONE


def quick_action(said: str) -> tuple[str, str] | None:
    """(reply, what was done) for an everyday request done directly by code (reminders, calendar, lists), or None.
    A small model mangles these tool calls and then says they worked; code does them reliably."""
    if REMIND_REQUEST.search(said) and (parts := reminder_from(said)):
        return set_reminder(parts, bool(CHAT_APP.search(said))), "schedule_task"
    if undo.is_request(said):
        return undo.undo_last(), "undo"
    if abilities.is_question(said):
        hub.refresh()
        return abilities.describe(load_config().get("tool_groups", {}), set(hub.routes) | set(NATIVE_TOOLS)), "abilities"
    if worked_out := quick.calculate(said):
        return worked_out, "calculator"
    if WEATHER_QUESTION.search(said) and not DO_REQUEST.search(said) and (forecast := weather.answer(said, load_config().get("weather"))):
        return forecast, "weather"
    if quick.asks_for_ips(said):
        hub.refresh()
        if listed := quick.ip_list(hub.call("homelab__proxmox_status", {})):
            return "Your servers' IP addresses:\n\n" + listed, "homelab__proxmox_status"
    if found := quick.calendar_add(said):
        return calendar_feed.add(*found), "calendar_add"
    if found := quick.list_add(said):
        return personal.list_add(found[1], found[0]), "list_add"
    if not REMIND_REQUEST.search(said) and not quick.MORE_THAN_ONE_THING.search(said) and (honest := quick.cant_do(said)):
        return honest, "cant_do"
    return None


QUIZ_JUDGE_PROMPT = """You mark a student's answer to a revision question. Be fair: accept answers that mean the same thing,
even if worded differently or missing minor detail. Reply with JSON only: {"correct": true or false, "feedback": "one short sentence"}.

Question: %s
Correct answer: %s
Student's answer: %s"""


def judge_answer(config, question: str, expected: str, given: str) -> tuple[bool, str]:
    try:
        reply = http_json(f"{OLLAMA_URL}/api/chat", body={
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 120, "num_ctx": config.get("options", {}).get("num_ctx", 12288)},
            "messages": [{"role": "user", "content": QUIZ_JUDGE_PROMPT % (question, expected, given[:500])}]})
        marked = json.loads(reply["message"]["content"])
        return bool(marked.get("correct")), str(marked.get("feedback") or "")[:200]
    except (OSError, ValueError, KeyError) as error:
        log.warning("quiz marking failed: %s", error)
        return False, "(I couldn't mark that one properly, so it'll come back soon.)"


PLAIN_COMPLETION_TOKENS = 1500


def complete_text(prompt: str, json_mode: bool = False) -> str:
    """One plain model call (no persona, memory or tools), for writing jobs like lecture notes and research."""
    config = load_config()
    options = {"temperature": 0.2, "num_predict": PLAIN_COMPLETION_TOKENS, "num_ctx": config.get("options", {}).get("num_ctx", 12288),
               "repeat_penalty": config.get("options", {}).get("repeat_penalty", 1.1)}  # small models can loop on one line
    body = {"model": config["model"], "stream": False, "options": options,
            "keep_alive": config.get("keep_alive", "10m"), "messages": [{"role": "user", "content": prompt}], **({"format": "json"} if json_mode else {})}
    body = with_thinking(body, not json_mode and thinking_for(config, writing_job=True))
    return (http_json(f"{OLLAMA_URL}/api/chat", body=body).get("message") or {}).get("content") or ""


def solo_conversation(messages):
    conversation = []
    for message in messages:
        if message.get("role") in ("user", "assistant"):
            conversation.append({"role": message["role"], "content": APP_NOTES.sub("", message_text(message)).strip()})
    return conversation


def solo_turn(model_name, messages):
    said = message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {}))
    if declined := safety.check(APP_NOTES.sub("", said)):
        kind, reply = declined
        return reply, [f"safety_{kind}"], {}, None
    body = {"model": model_name, "messages": solo_conversation(messages), "stream": False,
            "keep_alive": load_config().get("keep_alive", "10m")}
    reply = http_json(f"{OLLAMA_URL}/api/chat", body=body)
    return ((reply.get("message") or {}).get("content") or "").strip(), [], reply, None


def run_turn(messages, requested_model="", client_tools=None, skip_groups=(), on_text=None, client=""):
    """One chat turn. Returns (answer, tools_used, raw, client_calls); client_calls is set when the
    model wants the calling app to run one of its own tools, and answer is then whatever text came with it.
    on_text gets the answer's first sentences early (see drafts.py); the answer always starts with them.
    client names the calling app when it says ("phone": the Arty phone app, which can open calls and texts)."""
    if solo := models.solo_choice(requested_model):
        return solo_turn(solo["name"], messages)
    config = load_config()
    if requested_model.startswith(models.DROPDOWN_PREFIX):
        chosen = models.find(requested_model[len(models.DROPDOWN_PREFIX):])
        if models.usable(chosen):
            config["model"] = chosen["name"]
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if last_user is not None:
        said = APP_NOTES.sub("", message_text(last_user)).strip()
        desktop_look.user_active()
        if declined := safety.check(said):
            kind, reply = declined
            log.info("declined a %s request", kind)
            archive_turn(config, said, reply, [f"safety_{kind}"])
            return reply, [f"safety_{kind}"], {}, None
        if quizzing := personal.quiz_turn(said, lambda q, e, g: judge_answer(config, q, e, g)):
            archive_turn(config, said, quizzing, ["quiz"])
            return quizzing, ["quiz"], {}, None
        if TALKED_ABOUT.search(said):
            reply = tidy_answer(recap_conversations(config, said))
            archive_turn(config, said, reply, ["recall_conversations"])
            return reply, ["recall_conversations"], {}, None
        if action := phone.from_request(said):
            reply = phone.reply(action, client)
            archive_turn(config, said, phone.without_action(reply), [f"phone_{action['kind']}"])
            return reply, [f"phone_{action['kind']}"], {}, None
        if done := quick_action(said):
            reply, tool = done
            archive_turn(config, said, reply, [tool])
            return reply, [tool], {}, None
        if email_watch.is_request(said):
            # Code reads the list and the model only sorts and sums up: a failed check is reported, never "nothing".
            hub.refresh()
            reply = email_watch.answer(hub.call, complete_text)
            archive_turn(config, said, reply, ["workspace__gmail_search"])
            return reply, ["workspace__gmail_search"], {}, None
        if email_watch.is_unread_request(said):
            hub.refresh()
            reply = email_watch.unread(hub.call)
            archive_turn(config, said, reply, ["workspace__gmail_search"])
            return reply, ["workspace__gmail_search"], {}, None
        if (reply := remember_reply(said)) is not None:
            archive_turn(config, said, reply, ["memory__add_observations"])
            return reply, ["memory__add_observations"], {}, None
        if (reply := forget_reply(said)) is not None:
            archive_turn(config, said, reply, ["memory__delete_observations"])
            return reply, ["memory__delete_observations"], {}, None
        if (reply := desktop_look.permission_reply(said, config.get("desktop_look"))) is not None:
            archive_turn(config, said, reply, ["self_start"])
            return reply, ["self_start"], {}, None
        if desktop_look.is_request(said):
            # The model wrote how-to guides for this instead of doing it: code does it, the model only chooses.
            hub.refresh()
            reply = desktop_look.customise(hub.call, complete_text)
            archive_turn(config, said, reply, ["workspace__run_command"])
            return reply, ["workspace__run_command"], {}, None
        if style.is_learn_request(said):
            reply = style.refresh(gather_writing, complete_text)
            archive_turn(config, said, reply, ["learn_style"])
            return reply, ["learn_style"], {}, None
        if style.is_new_draft(said):
            # A new post or email in the user's voice, from their request alone (style.py): with memories and recent
            # chats in view, a small model stitches unrelated true facts into made-up events.
            reply = style.draft(said, complete_text, config.get("user_name", ""))
            archive_turn(config, said, reply, ["draft"])
            return reply, ["draft"], {}, None
        if research.is_quick_question(said) and not research.is_request(said) and not matches_lookup(config, said):
            # Recent facts, "best ...", "how do I make ...": read the top pages (no browser) and answer with sources.
            hub.refresh()
            answer, used = research.run(said, hub.call, complete_text, pages=research.QUICK_PAGES, browser=False)
            answer = tidy_answer(answer)
            archive_turn(config, said, answer, used)
            return answer, used, {}, None
        if research.is_request(said):
            # "Use your desktop to find out ...": fixed steps (search, read pages, note, write), see research.py.
            hub.refresh()
            answer, used = research.run(said, hub.call, complete_text)
            answer = tidy_answer(answer)
            archive_turn(config, said, answer, used)
            return answer, used, {}, None
    messages, attached = expand_attachments(messages)
    conversation = to_ollama_messages([m for m in messages if m.get("role") != "system"])
    client_system = "\n".join(message_text(m) for m in messages if m.get("role") == "system").strip()
    # With an attachment in the chat, the conversion tool is always on offer, however few tools the model gets.
    state = {"messages": conversation, "context": {}, "groups": ["files"] if attached else []}
    # A "remind me" answer may be replaced whole by ensure_reminder, so it isn't sent early.
    users = [m for m in conversation if m.get("role") == "user"]
    draft = drafts.Draft(on_text, tidy_answer, OFFER_SENTENCE.search) \
        if on_text and not (users and REMIND_REQUEST.search(message_text(users[-1]))) else None
    hub.refresh()
    # Only for a new message: not when an app sends its tools' results back to finish the same turn.
    new_message = bool(conversation) and conversation[-1].get("role") == "user"
    if new_message and last_user is not None and VOICE_MODE in client_system and not addressed_to_arty(config, said, conversation):
        log.info("voice heard something not meant for Arty; not answering or saving it")
        return NOT_FOR_ME, [], {}, None
    looked_up = look_up_first(config, said) if new_message and last_user is not None else ([], "", None)
    converted = convert_attachment(said, message_text(users[-1])) if attached and new_message and users and last_user is not None else None
    if looked_up[2]:
        archive_turn(config, said, looked_up[2], [])
        return looked_up[2], [], {}, None
    if len(looked_up[0]) == 1 and "[couldn't be checked" not in looked_up[1] and not DO_REQUEST.search(said) \
            and not ACTION_REQUEST.search(said):
        result = looked_up[1].rsplit("]\n", 1)[-1].strip()  # the one result, without the notes' heading and "[tool]" label
        if EMPTY_RESULT.search(result) and len(result) < 200:
            archive_turn(config, said, result, looked_up[0])
            return result, looked_up[0], {}, None
    if converted:
        done = ["convert_file"] if converted.startswith("Converted") else []
        looked_up = ([*looked_up[0], *done], (looked_up[1] or "\n\nAlready done for this message:")
                     + f"\n\n[convert_file]\n{converted}", None)
    outcome = run_agent(config, state, chat_approved=policy.chat_says_yes(conversation, message_text),
                        client_system=client_system, client_tools=client_tools, skip_groups=skip_groups, draft=draft,
                        looked_up=looked_up)
    if outcome["status"] == "client_tools":
        content = draft.finish(outcome["content"]) if draft else outcome["content"]
        return content, outcome["tools_used"], outcome["raw"], outcome["calls"]
    if outcome["status"] == "waiting":  # can't happen in chat mode, but never lose the turn
        outcome = {"answer": outcome["waiting"]["message"], "tools_used": outcome["tools_used"], "raw": outcome["raw"]}
    answer = tidy_answer(trim_offers(outcome["answer"]))
    if not answer:
        log.warning("reply was empty after tidying; the model wrote: %r", (outcome["answer"] or "")[:300])
        answer = "Sorry, I lost my train of thought there. Could you ask that again?"
    reminded = ensure_reminder(conversation, outcome["tools_used"], answer)
    if reminded != answer and reminded.startswith("Reminder set"):
        outcome["tools_used"].append("schedule_task")  # set here from the user's words
    answer = reminded
    answer = HASHTAG_LINE.sub("", answer)  # hashtags belong in drafted posts, not in answers
    client_used, client_failed = client_tool_runs(conversation)
    answer = be_honest(answer, client_used + outcome["tools_used"], client_failed + state.get("context", {}).get("failed", []),
                       draft.everything() if draft else "", said if last_user is not None else "")
    if draft:
        answer = draft.finish(answer)
    # Small models sometimes describe the conversion but drop the link; never lose a file the user asked for.
    conversions = [message_text(m) for m in conversation if m.get("role") == "tool" and m.get("tool_name") in SENDS_FILES]
    for result in conversions + ([converted] if converted else []):
        for link in DOWNLOAD_LINK.finditer(result):
            if link.group(1) not in answer:
                answer += f"\n\n{link.group(0)}"
    user_messages = [m for m in conversation if m.get("role") == "user"]
    user_text = APP_NOTES.sub("", message_text(user_messages[-1])).strip() if user_messages else ""
    if is_overheard(answer):
        log.info("voice heard something not meant for Arty; not saving it")
        return NOT_FOR_ME, outcome["tools_used"], outcome["raw"], None
    if user_text:
        archive_turn(config, user_text, answer, outcome["tools_used"])
        if config.get("auto_memory", True):
            threading.Thread(target=extract_memories, args=(config, user_text, answer), daemon=True).start()
    return answer, outcome["tools_used"], outcome["raw"], None


# ---------- background tasks ----------

def apply_decision(state: dict, waiting: dict, decision: str, note: str):
    """Turn Drew's answer to a paused task into the tool result the model sees next."""
    call = waiting.get("call") or {}
    if waiting.get("kind") == "approval":
        if decision == "approve":
            result = hub.call(call["name"], call.get("arguments") or {})
            policy.remember_tool_result(state.setdefault("context", {}), call["name"], result)
            result = f"The user approved. Result:\n{result}"
        else:
            result = "The user said no to this. Don't do it; carry on without it, or finish and report."
    else:
        result = "The user says he has done it" + (f": {note}" if note else ".") + " Carry on."
    state["messages"].append({"role": "tool", "content": result, "tool_name": call.get("name", "ask_user")})


def process_task(task: dict):
    config = load_config()
    state = task["history"] or {"messages": [{"role": "user", "content": f"Background task #{task['id']}: {task['title']}\n\n{task['instructions']}"}]}
    waiting = task["waiting"] or {}
    if waiting.get("decision"):
        hub.refresh()
        apply_decision(state, waiting, waiting["decision"], waiting.get("note", ""))
    store.update_task(task["id"], history=state, waiting=None)
    outcome = run_agent(config, state, background=True, task=task)
    tools_used = (task["tools_used"] or []) + outcome["tools_used"]
    if outcome["status"] == "cancelled" or (store.get_task(task["id"]) or {}).get("status") == "cancelled":
        store.update_task(task["id"], history=state, tools_used=tools_used)
        log.info("task %s cancelled", task["id"])
        return
    if outcome["status"] == "waiting":
        pending = outcome["waiting"]
        store.update_task(task["id"], status="waiting", history=state, waiting=pending, tools_used=tools_used)
        refreshed = store.get_task(task["id"])
        if pending["kind"] == "approval":
            buttons = task_links(refreshed, [("Approve", "approve"), ("Deny", "deny"), ("Cancel task", "cancel")])
            notify.send(f"Arty wants approval (task #{task['id']})", f"{task['title']}\n\nArty wants to: {pending['message']}", buttons, urgent=True)
        else:
            buttons = task_links(refreshed, [("Done", "done"), ("Cancel task", "cancel")])
            notify.send(f"Arty needs you (task #{task['id']})", f"{task['title']}\n\n{pending['message']}\n\nOn my desktop (Proxmox > VM 105 > Console), "
                        "double-click \"Sign in to websites\", sign in, close that window, then tap Done.", buttons, urgent=True)
        log.info("task %s waiting for the user: %s", task["id"], pending["message"])
        return
    answer = tidy_answer(trim_offers(outcome["answer"])) or "(Arty finished without writing a report.)"
    store.update_task(task["id"], status="done", history=state, result=answer, tools_used=tools_used)
    archive_turn(config, f"[background task #{task['id']}] {task['title']}: {task['instructions'][:500]}", answer, tools_used)
    # A short report ("done", "triggered") is meaningless on its own, e.g. for reminders, so include the job itself.
    message = answer if len(answer) >= SHORT_REPORT_CHARS else f"{task['instructions'][:1000]}\n\n{answer}".strip()
    notify.send(f"Arty: {task['title']}", message)
    log.info("task %s done", task["id"])


def worker_loop():
    while True:
        task = store.claim_next_task()
        if task is None:
            time.sleep(WORKER_IDLE_SECONDS)
            continue
        log.info("task %s started: %s", task["id"], task["title"])
        try:
            process_task(task)
        except Exception as error:
            log.exception("task %s failed", task["id"])
            store.update_task(task["id"], status="failed", result=f"failed: {error}")
            notify.send(f"Arty: task #{task['id']} failed", f"{task['title']}\n\n{error}")


def scheduler_loop():
    while True:
        now = datetime.now()
        try:
            for schedule in store.due_schedules(now.timestamp()):
                # Move it on before acting, so a failure below can't make it fire again every tick.
                if schedule["cron"]:
                    store.update_schedule(schedule["id"], next_run=timing.next_run(schedule["cron"], now).timestamp(), last_run=now.timestamp())
                else:
                    store.update_schedule(schedule["id"], enabled=0, next_run=None, last_run=now.timestamp())
                if schedule.get("message"):
                    # A plain reminder: send the exact text, no model needed (and nothing to get wrong).
                    notify.send("⏰ Reminder", reminder_wording(schedule["message"]))
                    continue
                if kind := briefing.kind_of(schedule["instructions"]):
                    # Built by code from the real data (the model made up placeholder events); in the background.
                    threading.Thread(target=send_briefing, args=(schedule["title"], kind), name="briefing", daemon=True).start()
                    continue
                created = store.create_task(schedule["title"], schedule["instructions"], "schedule", schedule["id"])
                store.update_schedule(schedule["id"], last_task_id=created["id"])
                log.info("schedule %s queued task %s", schedule["id"], created["id"])
        except Exception:
            log.exception("scheduler tick failed")
        try:
            for event in calendar_feed.due_reminders():
                notify.send("📅 Coming up", calendar_feed.reminder_text(event))
            for message in personal.due_deadline_reminders():
                notify.send("⏰ Deadline", message)
        except Exception:
            log.exception("calendar or deadline reminders failed")
        if style.due() and time.time() - _style_attempt["time"] > STYLE_RETRY_SECONDS:
            # Weekly: relearn how the user writes, from what they've written since (in the background).
            _style_attempt["time"] = time.time()
            threading.Thread(target=style.refresh, args=(gather_writing, complete_text), name="style", daemon=True).start()
        if email_watch.due(load_config().get("email_watch", {})):
            # In the background: reading Gmail and sorting it can take a minute, and reminders mustn't wait.
            threading.Thread(target=email_watch.check_new, name="email-watch", daemon=True,
                             args=(hub.call, complete_text, lambda title, message: notify.send(title, message))).start()
        if desktop_look.due(load_config().get("desktop_look", {})):
            threading.Thread(target=desktop_look.tinker, args=(hub.call, complete_text), name="desktop-look", daemon=True).start()
        try:
            for message in expiry.due_warnings(load_config().get("expiry_watch", {})):
                notify.send("⌛ Expiring soon", message)
        except Exception:
            log.exception("expiry watch failed")
        time.sleep(SCHEDULER_TICK_SECONDS)


def decide_task(task_id: int, action: str, note: str = "") -> tuple[int, str]:
    task = store.get_task(task_id)
    if task is None:
        return 404, "No such task."
    if action == "cancel":
        if task["status"] in ("done", "failed", "cancelled"):
            return 409, f"Task is already {task['status']}."
        store.update_task(task_id, status="cancelled", waiting=None)
        return 200, "Task cancelled."
    waiting = task["waiting"] or {}
    if task["status"] != "waiting":
        return 409, f"Task isn't waiting (it's {task['status']})."
    expected = ("approve", "deny") if waiting.get("kind") == "approval" else ("done",)
    if action not in expected:
        return 409, f"This task is waiting for: {' or '.join(expected)}."
    waiting.update({"decision": action, "note": note[:500]})
    store.update_task(task_id, status="queued", waiting=waiting)
    return 200, {"approve": "Approved, Arty is carrying on.", "deny": "Denied, Arty will carry on without it.", "done": "Thanks, Arty is carrying on."}[action]


def public_task(task: dict, with_history: bool = False) -> dict:
    hidden = {"token"} | (set() if with_history else {"history"})
    return {key: value for key, value in task.items() if key not in hidden}


# ---------- HTTP API ----------

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


api_context: dict = {}

# ---------- announcements: everything Arty tells the user, as a feed for its own apps ----------

announced = threading.Condition()
ANNOUNCEMENT_WAIT_SECONDS = 55  # under Cloudflare's 100 s request limit


def record_announcement(title, message, actions, urgent):
    """notify.send listener: keeps a copy of each notification for the desktop and phone apps."""
    task_ids = {int(a["callback"].split(":")[1]) for a in actions if str(a.get("callback", "")).startswith("t:")}
    kind = "approval" if task_ids else "reminder" if title.startswith(("⏰", "📅")) else "alert" if urgent else "message"
    store.add_announcement(kind, title, message, next(iter(task_ids), None),
                           [a["callback"].split(":")[2] for a in actions if str(a.get("callback", "")).startswith("t:")])
    with announced:
        announced.notify_all()


def wait_for_announcements(after: int, wait: float) -> list[dict]:
    deadline = time.time() + min(max(wait, 0), ANNOUNCEMENT_WAIT_SECONDS)
    with announced:
        while True:
            found = store.list_announcements(after)
            remaining = deadline - time.time()
            if found or remaining <= 0:
                return found
            announced.wait(remaining)


def send_briefing(title: str, kind: str):
    try:
        if not hub.routes:
            hub.refresh()
        text = briefing.build(kind, hub.call, complete_text, load_config().get("weather"))
    except Exception as error:  # a broken briefing must still say something, never vanish
        log.exception("%s briefing failed", kind)
        text = f"I couldn't put your {kind} briefing together ({error})."
    notify.send(title, text)


HEAL_COOLDOWN_SECONDS = 30 * 60
_healing: dict[str, float] = {}


def self_heal(monitor: dict):
    """A service went down: diagnose it, and if starting or restarting its guest would fix it, offer that
    with Fix / Ignore buttons (a background task waiting for approval, like any other)."""
    name = monitor.get("name") or "A service"
    if time.time() - _healing.get(name, 0) < HEAL_COOLDOWN_SECONDS:
        return
    _healing[name] = time.time()
    url = urllib.parse.urlparse(monitor.get("url") or "")
    address = monitor.get("hostname") or url.hostname or ""
    port = int(monitor.get("port") or url.port or (443 if url.scheme == "https" else 80 if url.scheme == "http" else 0))
    if not address:
        return
    hub.refresh()
    try:
        diagnosis = json.loads(hub.call("homelab__diagnose", {"address": address, "port": port, "guest_name": ""}))
    except (ValueError, TypeError):
        log.warning("self-heal: diagnosis of %s failed", name)
        return
    fix = diagnosis.get("fix")
    if not fix:
        notify.send(f"🔎 {name} is down", diagnosis.get("summary", ""))
        return
    tool = "homelab__start_guest" if fix["action"] == "start" else "homelab__restart_guest"
    task = store.create_task(f"Fix: {name} is down", f"{name} went down. {diagnosis['summary']}", "self-heal")
    state = {"messages": [{"role": "user", "content": (
        f"Background task #{task['id']}: {name} ({address}{':' + str(port) if port else ''}) went down. Diagnosis: {diagnosis['summary']} "
        f"You asked the user whether to {fix['action']} {fix['name']}. When you get their answer, if it was done, wait a moment, then "
        f"call homelab__diagnose with address {address} and port {port} to check it's answering again, and report in one or two sentences.")}],
        "context": {}, "groups": ["homelab"]}
    waiting = {"kind": "approval", "message": f"{fix['action']} {fix['name']}", "call": {"name": tool, "arguments": {"vmid": fix["vmid"]}}}
    store.update_task(task["id"], status="waiting", history=state, waiting=waiting)
    buttons = task_links(store.get_task(task["id"]), [(f"Fix ({fix['action']} {fix['name']})", "approve"), ("Ignore", "deny")])
    notify.send(f"🛠 {name} is down", f"{diagnosis['summary']}\n\nShall I {fix['action']} {fix['name']}?", buttons, urgent=True)


def record_alert(body: dict) -> str:
    """Uptime Kuma webhook: a service went down or came back. It only goes into the feed (the desktop
    app says it out loud, the phone shows it), like every notification."""
    monitor = (body.get("monitor") or {}).get("name") or "A service"
    heartbeat = body.get("heartbeat") or {}
    if heartbeat.get("status") == 0:
        title, kind = f"{monitor} is down", "alert"
    elif heartbeat.get("status") == 1:
        title, kind = f"{monitor} is back up", "message"
    else:
        title, kind = f"Uptime Kuma: {monitor}", "message"
    store.add_announcement(kind, title, str(heartbeat.get("msg") or body.get("msg") or "")[:500])
    with announced:
        announced.notify_all()
    if heartbeat.get("status") == 0:
        threading.Thread(target=self_heal, args=(body.get("monitor") or {},), name="self-heal", daemon=True).start()
    return title


class ChatJobs:
    """Chat turns run in the background for clients behind Cloudflare, which cuts off any request
    that waits over 100 seconds: POST starts a turn and returns an id, GET polls it.
    Kept in memory only (a brain restart forgets unfinished jobs) and pruned after an hour.
    While it runs, "partial" holds the answer's first sentences (the final answer starts with them)."""
    KEEP_SECONDS = 3600

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, messages, model, client="") -> str:
        job_id = uuid.uuid4().hex
        with self.lock:
            now = time.time()
            for old in [k for k, j in self.jobs.items() if now - j["created"] > self.KEEP_SECONDS]:
                del self.jobs[old]
            self.jobs[job_id] = {"status": "running", "created": now}
        threading.Thread(target=self._run, args=(job_id, messages, model, client), daemon=True).start()
        return job_id

    def _run(self, job_id, messages, model, client):
        try:
            answer, tools_used, _, _ = run_turn(messages, model, on_text=lambda text: self._add_partial(job_id, text),
                                                client=client)
            outcome = {"status": "done", "answer": answer, "tools_used": tools_used}
        except Exception as error:
            log.exception("chat job %s failed", job_id)
            outcome = {"status": "failed", "error": str(error)}
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(outcome)

    def _add_partial(self, job_id, text):
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]["partial"] = self.jobs[job_id].get("partial", "") + text

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None


chat_jobs = ChatJobs()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, status, title, body_html):
        page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{escape(title)}</title><body style='font-family:sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem'>"
                f"<h2>{escape(title)}</h2>{body_html}</body>")
        data = page.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorised(self):
        supplied = self.headers.get("Authorization", "")
        keys = [key for key in (BRAIN_KEY, APP_KEY) if key]
        if any(secrets.compare_digest(supplied, f"Bearer {key}") for key in keys):
            return True
        self.send_json(401, {"error": {"message": "invalid API key"}})
        return False

    def body_length(self) -> int:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            raise ApiError(400, "Content-Length must be a number") from None
        if length < 0:
            raise ApiError(400, "Content-Length can't be negative")
        return length

    def body(self) -> dict:
        length = self.body_length()
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "request too large")
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError(400, "body must be JSON") from None
        if not isinstance(parsed, dict):
            raise ApiError(400, "body must be a JSON object")
        return parsed

    def route(self):
        parts = urllib.parse.urlsplit(self.path)
        return [segment for segment in parts.path.split("/") if segment], urllib.parse.parse_qs(parts.query)

    # -- hooks: tapped from phone notifications, authenticated by the task's own token --
    def hook(self, method):
        segments, query = self.route()
        if len(segments) != 4 or segments[1] != "tasks" or segments[3] not in TASK_ACTIONS or not segments[2].isdigit():
            return self.send_html(404, "Not found", "")
        task = store.get_task(int(segments[2]))
        token = (query.get("token") or [""])[0]
        if task is None or not secrets.compare_digest(token, task["token"]):
            return self.send_html(403, "Link not valid", "<p>This link is for a different or deleted task.</p>")
        action = segments[3]
        if method == "GET":
            waiting = task["waiting"] or {}
            form = (f"<p><b>{escape(task['title'])}</b></p><p>{escape(waiting.get('message', ''))}</p>"
                    f"<form method=post><button style='font-size:1.2rem;padding:.6rem 1.4rem'>{escape(action.title())}</button></form>")
            return self.send_html(200, f"Task #{task['id']}: {action}?", form)
        status, message = decide_task(task["id"], action)
        return self.send_html(status, message, "")

    def do_GET(self):
        segments, query = self.route()
        if segments[:1] == ["hooks"]:
            return self.hook("GET")
        if segments == ["health"]:
            return self.send_json(200, {"ok": True})
        if segments[:1] == [media.DOWNLOAD_PREFIX]:
            return self.download(segments)
        if segments[:1] == [web_app.PREFIX]:
            return self.web_app_file(segments)
        if not self.authorised():
            return
        try:
            self.dispatch_get(segments, query)
        except ApiError as error:
            self.send_json(error.status, {"error": {"message": str(error)}})
        except ValueError as error:  # a malformed number in the query string
            self.send_json(400, {"error": {"message": f"bad request: {error}"}})
        except (BrokenPipeError, ConnectionResetError):
            log.info("%s hung up before the answer (%s)", self.address_string(), self.path.split("?")[0])
        except Exception as error:
            log.exception("request failed")
            self.send_json(500, {"error": {"message": f"brain error: {error}"}})

    def dispatch_get(self, segments, query):
        if segments in (["v1", "models"], ["models"]):
            listed = [{"id": MODEL_ID, "object": "model", "owned_by": "home"}]
            try:
                main = models.find(load_config().get("model", ""))
                listed[0].update(model=main["name"] if main else "", description=models.summary(main) if main else "")
                listed += [{"id": f"{models.DROPDOWN_PREFIX}{m['name']}", "name": f"Arty ({m['name']}{'' if m['tools'] else ', no tools'})", "object": "model", "owned_by": "home",
                            "model": m["name"], "description": models.summary(m)}
                           for m in models.chat_models()]
                listed += models.solo_entries()
            except (urllib.error.URLError, TimeoutError) as error:
                log.warning("could not list Ollama models: %s", error)
            return self.send_json(200, {"object": "list", "data": listed})
        if segments == ["v1", "tools"]:
            hub.refresh()
            config = load_config()
            groups = config.get("tool_groups", {})
            listed = []
            for tool in hub.tools + [NATIVE_TOOLS[name] for name in sorted(API_CALLABLE_NATIVE)]:
                name = tool["function"]["name"]
                listed.append({**tool, "groups": [group for group, spec in groups.items() if policy.matches(name, spec.get("tools", []))]})
            return self.send_json(200, {"tools": listed})
        if len(segments) == 4 and segments[:3] == ["v1", "chat", "jobs"]:
            job = chat_jobs.get(segments[3])
            if job is None:
                raise ApiError(404, "no such chat job (it may have expired, or the brain restarted)")
            return self.send_json(200, {"id": segments[3], **job})
        if segments == ["v1", "tasks"]:
            status = (query.get("status") or [""])[0]
            limit = min(int((query.get("limit") or ["30"])[0]), 200)
            return self.send_json(200, {"tasks": [public_task(t) for t in store.list_tasks(status, limit)]})
        if len(segments) == 3 and segments[:2] == ["v1", "tasks"] and segments[2].isdigit():
            task = store.get_task(int(segments[2]))
            if task is None:
                raise ApiError(404, "no such task")
            return self.send_json(200, public_task(task, with_history="history" in query))
        if segments == ["v1", "schedules"]:
            return self.send_json(200, {"schedules": store.list_schedules(include_disabled="all" in query)})
        if segments == ["v1", "memory"]:
            return self.send_json(200, http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20) or {})
        if len(segments) == 3 and segments[:2] == ["v1", "files"]:
            meta = media.get(segments[2])
            if meta is None:
                raise ApiError(404, "no such file (files are kept for 24 hours)")
            return self.send_json(200, media.public(meta))
        if segments == ["v1", "calendar"]:
            return self.send_json(200, calendar_feed.event_list(float((query.get("days") or ["30"])[0])))
        if segments == ["v1", "lectures"]:
            return self.send_json(200, {"lectures": lectures.recent()})
        if len(segments) == 3 and segments[:2] == ["v1", "lectures"] and segments[2].isdigit():
            lecture = lectures.get(int(segments[2]))
            if lecture is None:
                raise ApiError(404, "no such lecture")
            return self.send_json(200, lecture)
        if segments == ["v1", "announcements"]:
            after = int((query.get("after") or ["0"])[0])
            wait = float((query.get("wait") or ["0"])[0])
            found = wait_for_announcements(after, wait)
            return self.send_json(200, {"announcements": found, "last": found[-1]["id"] if found else max(after, store.latest_announcement_id())})
        if segments == ["v1", "conversations"]:
            q = (query.get("q") or [""])[0]
            limit = min(int((query.get("limit") or ["10"])[0]), 100)
            config = load_config()
            records = recall_records(config, q, limit) if q else [r for _, r in zip(range(limit), archive_records(config))]
            return self.send_json(200, {"conversations": records})
        raise ApiError(404, "not found")

    def do_POST(self):
        segments, query = self.route()
        if segments == ["hooks", "alert"]:
            token = (query.get("token") or [""])[0]
            if not ALERT_TOKEN or not secrets.compare_digest(token, ALERT_TOKEN):
                return self.send_json(403, {"error": {"message": "bad alert token"}})
            try:
                return self.send_json(200, {"recorded": record_alert(self.body())})
            except ApiError as error:
                return self.send_json(error.status, {"error": {"message": str(error)}})
        if segments == ["hooks", "notify"]:
            # The homelab's own watchers (e.g. homelab-security on the host) alerting the user through Arty.
            token = (query.get("token") or [""])[0]
            if not ALERT_TOKEN or not secrets.compare_digest(token, ALERT_TOKEN):
                return self.send_json(403, {"error": {"message": "bad alert token"}})
            try:
                body = self.body()
                result = notify.send(str(body.get("title") or "Homelab")[:200], str(body.get("message") or "")[:3000],
                                     urgent=bool(body.get("urgent")))
                return self.send_json(200, {"result": result})
            except ApiError as error:
                return self.send_json(error.status, {"error": {"message": str(error)}})
        if segments[:1] == ["hooks"]:
            return self.hook("POST")
        if not self.authorised():
            return
        try:
            if segments == ["v1", "files"]:
                return self.upload()
            self.dispatch_post(segments, self.body())
        except ApiError as error:
            self.send_json(error.status, {"error": {"message": str(error)}})
        except (BrokenPipeError, ConnectionResetError):
            # The answer was still archived; the app just wasn't there to receive it.
            log.warning("%s hung up before the answer (%s)", self.address_string(), self.path.split("?")[0])
        except Exception as error:
            log.exception("request failed")
            self.send_json(500, {"error": {"message": f"brain error: {error}"}})

    def do_DELETE(self):
        segments, _ = self.route()
        if not self.authorised():
            return
        if len(segments) == 3 and segments[:2] == ["v1", "schedules"] and segments[2].isdigit():
            found = store.update_schedule(int(segments[2]), enabled=0, next_run=None)
            return self.send_json(200 if found else 404, {"ok": found})
        if len(segments) == 3 and segments[:2] == ["v1", "calendar"] and segments[2].isdigit():
            found = store.delete_event(int(segments[2]))
            return self.send_json(200 if found else 404, {"ok": found})
        self.send_json(404, {"error": {"message": "not found"}})

    def dispatch_post(self, segments, request):
        if segments in (["v1", "chat", "completions"], ["chat", "completions"]):
            return self.chat(request)
        if segments == ["v1", "tasks"]:
            instructions = str(request.get("instructions", "")).strip()
            if not instructions:
                raise ApiError(400, "instructions are required")
            created = store.create_task(str(request.get("title") or instructions[:60]), instructions, "api")
            return self.send_json(201, public_task(created))
        if len(segments) == 4 and segments[:2] == ["v1", "tasks"] and segments[2].isdigit() and segments[3] in TASK_ACTIONS:
            status, message = decide_task(int(segments[2]), segments[3], str(request.get("message", "")))
            return self.send_json(status, {"message": message})
        if segments == ["v1", "schedules"]:
            created, message = create_schedule(request)
            if created is None:
                raise ApiError(400, message)
            return self.send_json(201, {**created, "message": message})
        if segments == ["v1", "chat", "jobs"]:
            messages = request.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ApiError(400, "messages are required")
            # "client": "phone" from the Arty phone app, which can open calls and texts (phone.py).
            return self.send_json(202, {"id": chat_jobs.start(messages, str(request.get("model") or ""), str(request.get("client") or "")),
                                        "status": "running"})
        if segments == ["v1", "lectures"]:
            try:
                return self.send_json(202, lectures.add(request.get("file_id"), request.get("title")))
            except lectures.LectureError as error:
                raise ApiError(400, str(error)) from None
        if segments == ["v1", "calendar"]:
            message = calendar_feed.add(request.get("title"), request.get("when"), request.get("duration_minutes"),
                                        str(request.get("location") or ""), str(request.get("notes") or ""))
            return self.send_json(201 if message.startswith("Added") else 400, {"message": message})
        if segments == ["v1", "notify"]:
            return self.send_json(200, {"result": notify.send(str(request.get("title") or "Arty"), str(request.get("message", "")))})
        if len(segments) == 3 and segments[:2] == ["v1", "tools"]:
            return self.call_tool(segments[2], request)
        raise ApiError(404, "not found")

    def call_tool(self, name, request):
        arguments = request.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ApiError(400, "arguments must be an object")
        if name in NATIVE_TOOLS:
            if name not in API_CALLABLE_NATIVE:
                raise ApiError(400, f"{name} only works inside a conversation")
            result, _ = run_native(name, arguments, load_config(), False, None)
            return self.send_json(200, {"result": result})
        hub.refresh()
        if name not in hub.routes:
            raise ApiError(404, f"unknown tool {name}")
        reason = policy.approval_reason(name, arguments, api_context, load_config().get("approval", {}))
        if reason and not request.get("approved"):
            return self.send_json(409, {"error": {"message": f"needs approval: {reason}", "type": "needs_approval", "reason": reason}})
        result = hub.call(name, arguments)
        policy.remember_tool_result(api_context, name, result)
        return self.send_json(200, {"result": result})

    def download(self, segments):
        """Serves a stored file to anyone with its link (the token in the path), like the task hooks."""
        meta = media.for_download(segments[1], segments[2]) if len(segments) >= 3 else None
        if meta is None:
            return self.send_html(404, "Link expired", "<p>Files are kept for 24 hours. Ask Arty to make it again.</p>")
        size = os.path.getsize(meta["path"])
        self.send_response(200)
        self.send_header("Content-Type", meta["mime"])
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(meta['name'])}")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with open(meta["path"], "rb") as handle:
            while chunk := handle.read(1 << 20):
                self.wfile.write(chunk)

    def web_app_file(self, segments):
        if urllib.parse.urlsplit(self.path).path == f"/{web_app.PREFIX}":
            self.send_response(301)
            self.send_header("Location", f"/{web_app.PREFIX}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        found = web_app.find(segments)
        if found is None:
            return self.send_html(404, "Not found", "")
        data = found.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", web_app.content_type(found))
        self.send_header("Content-Length", str(len(data)))
        for name, value in web_app.SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def upload(self):
        """POST /v1/files: the raw file is the body; its name comes from X-Filename (URL-encoded)."""
        name = urllib.parse.unquote_plus(self.headers.get("X-Filename", "")) or "upload"
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            meta = media.save_stream(self.rfile, length, name, self.headers.get("Content-Type", ""), "upload")
        except ValueError:
            raise ApiError(400, "Content-Length is required") from None
        except media.MediaError as error:
            raise ApiError(413 if "at most" in str(error) else 400, str(error)) from None
        self.send_json(201, media.public(meta))

    def vision(self, request, messages):
        """Messages carrying images (e.g. a desktop screenshot) go straight to a vision model: no persona,
        tools or archive, since the caller asks a precise question about a picture."""
        config = load_config()
        model = vision_model(str(request.get("model") or "").removeprefix(models.DROPDOWN_PREFIX))
        converted = []
        for message in messages:
            content = message.get("content")
            images = []
            if isinstance(content, list):
                for part in content:
                    url = (part.get("image_url") or {}).get("url", "") if isinstance(part, dict) else ""
                    if url.startswith("data:") and "," in url:
                        images.append(url.split(",", 1)[1])
            converted.append({"role": message.get("role", "user"), "content": message_text(message), **({"images": images} if images else {})})
        options = {k: v for k, v in config.get("options", {}).items() if k in ("num_ctx", "num_predict")}
        if request.get("max_tokens"):
            options["num_predict"] = int(request["max_tokens"])
        try:
            raw = http_json(f"{OLLAMA_URL}/api/chat", body={"model": model["name"], "messages": converted, "stream": False, "options": options, "keep_alive": 0})
        finally:
            reload_main_model(config)
        answer = (raw.get("message") or {}).get("content", "")
        self.send_json(200, {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()), "model": model["name"],
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]})

    def chat(self, request):
        messages = request.get("messages") or []
        # Apps asking a precise question about a picture (e.g. a desktop screenshot) can skip Arty and ask the
        # vision model directly. Everything else, attachments included, goes through Arty with its tools.
        if self.headers.get("X-Arty-Direct-Vision", "") == "1" or request.get("arty_direct_vision") is True:
            return self.vision(request, messages)
        # Only apps that opt in get their tools run by the model (the desktop app does). Open WebUI sends its
        # own built-in tools with every chat; those must not reach Arty's model.
        opted_in = self.headers.get("X-Arty-Client-Tools", "") == "1" or request.get("arty_client_tools") is True
        client_tools = [t for t in request.get("tools") or [] if isinstance(t, dict) and t.get("type") == "function"] if opted_in else []
        skip_groups = [g.strip() for g in self.headers.get("X-Arty-Skip-Groups", "").split(",") if g.strip()] if opted_in else []
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        turn = (request.get("messages") or [], str(request.get("model") or ""), client_tools, skip_groups)
        if not request.get("stream"):
            answer, tools_used, raw, client_calls = run_turn(*turn)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sent, connection = [], {"open": True}

            def send_chunk(delta, finish=None, **extra):
                chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": MODEL_ID,
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()

            def send_early(text):
                # The answer's first sentences, while the model writes the rest (drafts.py). An app that hangs
                # up mustn't stop the turn: the answer is still archived.
                sent.append(text)
                if not connection["open"]:
                    return
                try:
                    send_chunk({"role": "assistant", "content": text} if len(sent) == 1 else {"content": text})
                except (BrokenPipeError, ConnectionResetError):
                    connection["open"] = False
                    log.warning("%s hung up during a streamed answer", self.address_string())
            try:
                answer, tools_used, raw, client_calls = run_turn(*turn, on_text=send_early)
            except Exception as error:
                log.exception("streamed chat failed")
                if connection["open"]:
                    after_early_text = "\n\n" if sent else ""
                    send_chunk({"role": "assistant", "content": f"{after_early_text}(Arty's brain hit an error: {error})"}, finish="stop")
                    self.wfile.write(b"data: [DONE]\n\n")
                return
            if not connection["open"]:
                return
        usage = {"prompt_tokens": raw.get("prompt_eval_count", 0), "completion_tokens": raw.get("eval_count", 0)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        openai_calls = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                         "function": {"name": c["function"]["name"], "arguments": json.dumps(parse_arguments(c["function"].get("arguments")))}}
                        for c in client_calls or []]
        finish = "tool_calls" if openai_calls else "stop"
        if request.get("stream"):
            already = "".join(sent)
            if answer.startswith(already):  # Draft.finish makes sure of it
                rest = answer[len(already):]
            else:
                log.warning("the answer didn't continue its early sentences; sending it whole after them")
                rest = "\n\n" + answer
            if rest or not sent:
                send_chunk({"content": rest} if sent else {"role": "assistant", "content": rest})
            if openai_calls:
                send_chunk({"tool_calls": [{"index": i, **call} for i, call in enumerate(openai_calls)]})
            send_chunk({}, finish=finish, usage=usage)
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self.send_json(200, {
            "id": completion_id, "object": "chat.completion", "created": created, "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer, **({"tool_calls": openai_calls} if openai_calls else {})},
                         "finish_reason": finish}],
            "usage": usage, "tools_used": tools_used,
        })


if __name__ == "__main__":
    store.init()
    personal.init()
    lectures.init()
    notify.listeners.append(record_announcement)
    threading.Thread(target=lectures.worker, args=(complete_text, notify.send), name="lectures", daemon=True).start()
    threading.Thread(target=worker_loop, name="worker", daemon=True).start()
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    log.info("brain listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
