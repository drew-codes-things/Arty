"""The assistant's brain: an OpenAI-compatible endpoint that adds memory, instructions and tools.

Clients (Open WebUI, or your own apps with the Arty API key) send ordinary chat
completions. Each request gets the system prompt and the user's saved memories, and the
model's tool calls are executed through the MCP hub until it produces a final answer.

It also runs background tasks (one at a time, pausing for approval or help when
needed), a scheduler that turns schedules into tasks, and a small REST API over all of it.
See docs/API.md.
"""
import base64
import importlib
import json
import logging
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import abilities
import calendar_feed
import coding
import drafts
import email_watch
import expiry
import keepalive
import lectures
import research
import style
import media
import memory_recall
import memory_tidy
import models
import personal
import phone
import quick
import reckon
import sharing
import safety
import desktop_look
import devices
import briefing
import notify
import owner
import policy
import remote_sessions
import store
import timing
import tool_guide
import written_calls
import undo
import weather
import web_app
import window_fit
from agent_sessions import AgentSessions
from announcements import announced, record_announcement, wait_for_announcements
from api_support import (
    IDENTITY_NONCE, optional_int, ApiError, same_secret, identity_proofs, text_field, list_limit, valid_messages,
    client_name)
from archive import is_overheard, message_text, archive_records, recent_exchanges, recall_records, recall
from environment import BRAIN_KEY, APP_KEY, HUB_KEY
from settings import LISTEN_HOST, LISTEN_PORT, HUB_URL, OLLAMA_URL, CONFIG_FILE, TRUSTED_PROXIES, STARFIRE_URL, HOMELAB_NAMES
from native_tools import NATIVE_TOOLS, BACKGROUND_ONLY_EXCLUDED, API_CALLABLE_NATIVE
from reply_rules import (
    OFFER_SENTENCE, trim_offers, URL_TAGS, PROMPT_LEAK, FUMBLED_CALL, ACTION_REQUEST, CLAIMED, CLARIFYING,
    SEARCH_REQUEST, REMIND_REQUEST, CHAT_APP, reminder_from, PROMISE, TOOL_TALK, DO_REQUEST, PRETEND_TOOLS,
    TOOL_META_SENTENCE, TOOL_FAILED, FAILED_NOTE, NOT_ACTIONS, NOT_FOR_ME, VOICE_MODE, ADDRESSED_PROMPT, JSON_BLOCK,
    ANSWERED_ALREADY_CHARS, TOOLLESS_NOTE, APP_NOTES, TOOLS_NOTE_ECHO)
from schedules import (
    create_schedule, ensure_reminder, set_reminder, send_reminder, guarded, due_schedules_now,
    send_calendar_and_deadline_reminders)
from steering import (
    LOOKUP_TOOLS, MESSAGE_PLACEHOLDER, QUERY_PADDING, MAX_LOOKUPS, LOOKUP_TEMPERATURE, WRITE_ONLY_REQUEST,
    RUN_REQUEST, RUN_TOOLS, WRITE_ONLY_NOTE, WRITE_AND_RUN_NOTE, thinking_for, steer_last_request,
    steer_away_from_repeats, with_thinking, LOOKUP_MAX_MESSAGE_CHARS, LOOKUP_LABEL, LOOKUP_CHARS, LOOKUP_FAILED,
    on_my_desktop, sign_in_help, matches_lookup, with_context, parse_arguments, task_links, DESKTOP_TOOLS,
    NOTHING_TO_SIGN_IN, question_for_user, with_homelab_names)

ALERT_TOKEN = os.environ.get("ALERT_TOKEN", "")  # Uptime Kuma's webhook puts this in its URL
SERVERS = ["memory", "time", "fetch", "sequential-thinking", "workspace", "research", "homelab"]
MODEL_ID = "Arty"
MAX_TOOL_ROUNDS = 12
MEMORY_CHARS = 4000
TOOL_RESULT_CHARS = 8000
SPEC_REFRESH_SECONDS = 300
USER_NAME = owner.configured_name(CONFIG_FILE)
MAX_BODY_BYTES = 64 * 1024 * 1024  # chat requests can carry full-size photos as base64
UNKEYED_BODY_BYTES = 4096
WORKER_IDLE_SECONDS = 3
SCHEDULER_TICK_SECONDS = 30
DEFAULT_TIME_ZONE = "Europe/London"
DEFAULT_TIME_ZONE_LABEL = "UK time"
TASK_ACTIONS = ("done", "approve", "deny", "cancel")
SHORT_REPORT_CHARS = 60
REQUEST_IDLE_SECONDS = 60
STREAM_WORKING_EVERY_SECONDS = 15
MAX_RUNNING_CHAT_JOBS = 4
PRUNED_JOBS_KEPT = 1000
SECRET_QUERY_VALUE = re.compile(r"((?:token|key)=)[^&\s\"]*", re.IGNORECASE)
DOWNLOAD_TOKEN = re.compile(r"(/arty-files/[^/\s\"?]+/)[^/\s\"?]+(?=/)")
MAX_DIRECT_CHATS = 6
TASK_LINK_TROUBLE = "Arty couldn't open that task just now: try again in a minute."
GENERIC_ERROR = "Arty's brain hit an error; the details are in its log."
CLIENT_DROPPED_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
NATIVE_TOOL_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error, urllib.error.URLError)
ASKED_TO_DO = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please|can you|could you|would you|will you|i want you to|i need you to)\b",
                         re.IGNORECASE)
PAGE_READER = "workspace__browser_read"
SECRET_PATTERN = re.compile(r"password|passcode|passphrase|passkey|\bpin\b|api[ _-]?key|token|card number|cvv|sort code", re.IGNORECASE)


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
- If you need a human (a website login, a CAPTCHA or bot check), ask the user with your tools: the task pauses until the user has done it, then you continue.
- Outward-facing actions (posting, sending, connecting, buying) are held for the user's approval automatically; just carry on calling the tool.
- When finished, reply with a short report of what you did and found, written to the user as "you". It is sent to his phone."""


NAME_GREETING, NAME_TAIL = owner.vocative_patterns(USER_NAME)


def tidy_answer(answer: str) -> str:
    if leak := PROMPT_LEAK.search(answer):
        log.warning("cut a reply that ran on into its instructions")
        answer = answer[:leak.start()].rstrip()
    if answer.count("```") % 2 == 1 and (fumbled := FUMBLED_CALL.search(answer)):
        answer = answer[:fumbled.start()]  # an unclosed block would turn the rest of the chat into code
    answer = TOOLS_NOTE_ECHO.sub("", LOOKUP_LABEL.sub("", answer))
    answer = TOOL_META_SENTENCE.sub("", JSON_BLOCK.sub("\n", answer))
    answer = re.sub(r"\n{3,}", "\n\n", answer)  # no strip: this also cleans early sentences, whose spaces matter
    answer = NAME_GREETING.sub(lambda m: m.group(1), answer)
    answer = NAME_TAIL.sub("", answer)
    return URL_TAGS.sub("", answer)


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


def stream_chat(body, on_text, timeout=600, on_thinking=None):
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
            if on_thinking and piece.get("thinking"):
                on_thinking(piece["thinking"])
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
    config["lookups"] = with_homelab_names(config.get("lookups", []), HOMELAB_NAMES)
    return config


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

def memory_hub(route: str, body: dict):
    return http_json(f"{HUB_URL}/memory/{route}", body=body, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)


def restore_memory_reply(said: str) -> str | None:
    if not memory_tidy.is_restore_request(said):
        return None
    return memory_tidy.restore_last_run(memory_hub)


def addressed_to_arty(config, heard: str, conversation: list[dict]) -> bool:
    previous = next((message_text(m) for m in reversed(conversation) if m.get("role") == "assistant"), "") or "(nothing yet)"
    try:
        reply = http_json(f"{OLLAMA_URL}/api/chat", timeout=60, body=models.for_runner(config, {
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 20},
            "messages": [{"role": "user", "content": ADDRESSED_PROMPT % (previous[-400:], heard[:400])}]}))
        return json.loads(reply["message"]["content"]).get("for_arty") is not False
    except Exception as error:
        log.warning("couldn't tell whether a voice message was for Arty: %s", error)
        return True


def extract_memories(config, user_text, answer, save=None):
    with memory_lock:
        try:
            reply = http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
                "model": config["model"], "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_predict": 512},
                "messages": [{"role": "user", "content": EXTRACT_PROMPT % user_text[:2000]}],
            }))
            facts = json.loads(reply["message"]["content"]).get("facts") or []
            if isinstance(facts, list) and facts:
                (save or save_facts)([f for f in facts if isinstance(f, dict)])
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


def recalled_memories(request: str = ""):
    try:
        graph = http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
    except (urllib.error.URLError, TimeoutError):
        return ""
    return memory_recall.memory_lines(graph, request, MEMORY_CHARS)


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


def with_tool_guide(instructions: str, tools: list) -> str:
    guide = tool_guide.guide(tool_guide.offered_names(tools))
    return f"{instructions}\n\n{guide}" if guide else instructions


def current_context(config, request: str = ""):
    """What changes every message: memories, recent conversations and the time. It rides at the start of
    the latest user message (see with_context), after everything that can be cached."""
    memories = config["own_memories"] if "own_memories" in config else recalled_memories(request)
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
    return known + "\n\n" + time_line(config)


def local_zone(config) -> ZoneInfo:
    try:
        return ZoneInfo(str(config.get("time_zone") or DEFAULT_TIME_ZONE))
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time_zone %r in brain.json; using %s", config.get("time_zone"), DEFAULT_TIME_ZONE)
        return ZoneInfo(DEFAULT_TIME_ZONE)


def time_line(config, moment: datetime | None = None) -> str:
    now = (moment or datetime.now().astimezone()).astimezone(local_zone(config))
    offset = now.strftime("%z")
    label = str(config.get("time_zone_label") or DEFAULT_TIME_ZONE_LABEL)
    return f"It is now {now:%A %d %B %Y, %H:%M} ({label}, {now:%Z}, UTC{offset[:3]}:{offset[3:]})."


def with_home_zone(name: str, arguments: dict, config) -> dict:
    if name != "place_time" or arguments.get("home"):
        return arguments
    return {**arguments, "home": local_zone(config).key}


def found_no_place(name: str, result) -> bool:
    return name == "place_time" and reckon.NO_PLACE_FOUND in str(result)


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
            blocked = sign_in_help(config) if name.startswith("workspace__gmail") else str(result).split(". Do not")[0]
            continue
        if found_no_place(name, result):
            log.info("lookup %s found no place in the message, so it is left to the model", name)
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


def declined_reply(config: dict, *texts: str) -> str | None:
    declined = safety.check_each(texts, config.get("crisis_help"))
    if declined is None:
        return None
    log.info("declined a %s request in a task or handoff", declined[0])
    return declined[1]


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
        title = str(arguments.get("title") or instructions[:60])
        if declined := declined_reply(config, title, instructions):
            return declined, None
        created = store.create_task(title, instructions, "chat")
        return f"Started background task #{created['id']}. You'll get a notification when it's done.", None
    if name == "schedule_task":
        return create_schedule(arguments, config.get("crisis_help"))[1], None
    if name == "list_scheduled":
        rows = store.list_schedules()
        if not rows:
            return "Nothing is scheduled.", None
        return "\n".join(f"#{s['id']} {s['title']}: {s['description']}, next {datetime.fromtimestamp(s['next_run']):%a %d %b %H:%M}" for s in rows if s["next_run"]), None
    if name == "cancel_scheduled":
        found = store.update_schedule(optional_int(arguments.get("schedule_id")) or 0, enabled=0, next_run=None)
        return ("Cancelled." if found else "No such scheduled job."), None
    if name == "list_tasks":
        rows = store.list_tasks(limit=10)
        if not rows:
            return "No background tasks yet.", None
        return "\n".join(f"#{t['id']} [{t['status']}] {t['title']}" + (f": {t['result'][:200]}" if t["result"] else "") for t in rows), None
    if name == "list_models":
        return models.list_text(config["model"]), None
    if name == "ask_other_model":
        prompt = str(arguments.get("prompt", ""))
        if declined := declined_reply(config, prompt):
            return declined, None
        wanted = str(arguments.get("model", ""))
        return models.ask(wanted, prompt, config, session_keep_alive(wanted, 0)), None
    if name in personal.TOOLS:
        return personal.TOOLS[name][0](arguments), None
    if name in reckon.TOOLS:
        return reckon.TOOLS[name][0](with_home_zone(name, arguments, config)), None
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


def capped_tool_result(result):
    if not isinstance(result, str) or len(result) <= TOOL_RESULT_CHARS:
        return result
    return result[:TOOL_RESULT_CHARS] + f"\n(cut: the result was {len(result)} characters; ask for less to see the rest)"


def run_native_safely(name, arguments, config, background, task):
    try:
        result, waiting = run_native(name, arguments, config, background, task)
        return capped_tool_result(result), waiting
    except NATIVE_TOOL_ERRORS as error:
        log.warning("native tool %s failed: %r", name, error)
        return f"tool error: {name} didn't work ({type(error).__name__}: {error}). Check what you gave it and try again.", None


def week_recap(config) -> str:
    """The last 7 days: what the user asked (one line each) and the background jobs that finished."""
    cutoff = datetime.now().timestamp() - 7 * 86400
    asked = []
    for record in archive_records(config):
        try:
            if datetime.fromisoformat(record["time"]).timestamp() < cutoff:
                break
        except ValueError:
            continue
        if not record["user"].startswith("[background task"):
            asked.append(f"- {record['time'][:10]}: {APP_NOTES.sub('', record['user'])[:150]}")
    jobs = [f"- {t['title']}" for t in store.list_tasks("done", 30) if t["updated"] >= cutoff]
    return ("What the user asked this week (newest first):\n" + "\n".join(asked[:60] or ["- nothing"])
            + "\n\nBackground jobs finished:\n" + "\n".join(jobs or ["- none"])
            + "\n\nSum this up talking to the user as \"you\" (\"You asked me about...\", \"We sorted out...\"). "
              "Don't write it as their own post or in their voice unless they ask for a post.")


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
    for name in (wanted, load_config().get("vision_model", "")):
        model = models.find(name) if name else None
        if model and model["vision"]:
            return model
    model = next((m for m in models.installed() if m["vision"] and models.in_arty(m) and not models.is_separate(m)
                  and not models.kept_solo(m)), None)
    if model is None:
        raise ApiError(400, "No vision model is installed. Run: ai-pull qwen2.5vl:7b")
    return model


def look(images: list[str], prompt: str) -> str:
    """Asks the vision model about base64 images."""
    config = load_config()
    options = {k: v for k, v in config.get("options", {}).items() if k == "num_predict"}
    looker = vision_model()["name"]
    models.make_room(looker, models.vram_budget(config))
    raw = http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
        "model": looker, "stream": False, "think": False, "options": options, "keep_alive": session_keep_alive(looker, 0),
        "messages": [{"role": "user", "content": prompt, "images": images}]}))
    return ((raw or {}).get("message") or {}).get("content", "").strip() or "(the vision model gave no description)"


def reload_main_model(config: dict):
    def load():
        models.make_room(config["model"], models.vram_budget(config))
        try:
            http_json(f"{OLLAMA_URL}/api/generate", body=models.for_runner(config, {"model": config["model"], "keep_alive": config.get("keep_alive", "10m")}),
                      timeout=300)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log.warning("could not bring %s back after another model ran: %s", config["model"], error)
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


NOT_RUN_YET = "Not run: waiting for the user's OK."


def know_the_page(name: str, context: dict):
    if not policy.page_unknown(name, context) or PAGE_READER not in hub.routes:
        return
    policy.remember_tool_result(context, PAGE_READER, hub.call(PAGE_READER, {}))


def private_upload(name: str, arguments: dict) -> str | None:
    path = str(arguments.get("path") or "")
    if name != "workspace__browser_upload" or not sharing.is_private(path):
        return None
    return f"Refused: {path} is private (keys, passwords or a browser profile), so it is never uploaded anywhere."


def hold_for_approval(reason, call, calls, background, state, requested, add, tool_log, raw):
    function = call.get("function", {})
    name = function.get("name") or ""
    arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
    state["groups"] = sorted(requested)
    if background:
        return {"status": "waiting", "waiting": {"kind": "approval", "message": reason, "call": {"name": name, "arguments": arguments}},
                "tools_used": tool_log, "raw": raw}
    for unanswered in calls[calls.index(call):]:
        add({"role": "tool", "content": NOT_RUN_YET, "tool_name": unanswered.get("function", {}).get("name") or ""})
    return {"status": "done", "answer": policy.approval_question(reason), "tools_used": tool_log, "raw": raw}


def save_progress(task, state, requested):
    state["groups"] = sorted(requested)
    store.update_task(task["id"], history=state)


FRESH_LOAD_NANOSECONDS = 1_000_000_000
ROUND_TIMING_FIELDS = ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")
CACHED_PROMPT_FIELD = "prompt_eval_cached_count"
ROUNDS_KEY = "arty_rounds"


def round_timings(model: str, raw: dict) -> dict:
    timings = {"model": model, **{field: int(raw.get(field) or 0) for field in ROUND_TIMING_FIELDS}}
    if CACHED_PROMPT_FIELD in raw:
        timings[CACHED_PROMPT_FIELD] = int(raw[CACHED_PROMPT_FIELD] or 0)
    timings["loaded_fresh"] = timings["load_duration"] >= FRESH_LOAD_NANOSECONDS
    return timings


def turn_rounds(raw: dict | None) -> list[dict]:
    return list((raw or {}).get(ROUNDS_KEY) or [])


WINDOW_OVERFLOW = re.compile(r"context (size|length|window)|exceeds the available context|longer than the context", re.IGNORECASE)
OVERFLOW_STATUS = 413
OVERFLOW_TYPE = "context_length_exceeded"


REPLY_CAP_NOTE = "\n\n(I hit my reply length limit, so this was cut short. Ask me to continue.)"
WINDOW_END_NOTE = ("\n\n(This conversation filled my context window, so this reply was cut short. Send a shorter message or "
                   "start a new conversation so I have room to finish.)")
WINDOW_END_MARGIN_TOKENS = 16


def overflow_message(model: str, window: int, reply_cut: bool) -> str:
    if reply_cut:
        return (f"{model}'s context window of {window} tokens filled up before the reply finished, so it was cut short. "
                "Compact the conversation and send it again.")
    return (f"This conversation is longer than {model}'s context window of {window} tokens, so the model did not read it. "
            "Shorten or compact the conversation and send it again.")


class WindowOverflow(ApiError):
    def __init__(self, model: str, window: int, reply_cut: bool = False):
        super().__init__(OVERFLOW_STATUS, overflow_message(model, window, reply_cut))
        self.model = model
        self.window = window

    def details(self) -> dict:
        return {"message": str(self), "type": OVERFLOW_TYPE, "code": OVERFLOW_STATUS, "model": self.model,
                "context_tokens": self.window}


def ollama_error_text(error: Exception) -> str:
    if not isinstance(error, urllib.error.HTTPError):
        return str(error)
    try:
        found = json.loads(error.read() or b"{}").get("error")
    except (ValueError, AttributeError, OSError):
        return str(error)
    return str(found or error)


def ran_into_window_end(config: dict, raw: dict) -> bool:
    prompt, written = raw.get("prompt_eval_count"), raw.get("eval_count")
    if not isinstance(prompt, int) or not isinstance(written, int):
        return False
    return prompt + written >= models.context_tokens(config, config["model"]) - WINDOW_END_MARGIN_TOKENS


def cut_short_note(config: dict, raw: dict, agent: bool) -> str:
    if not ran_into_window_end(config, raw):
        log.warning("reply hit the num_predict cap and was cut off")
        return REPLY_CAP_NOTE
    window = models.context_tokens(config, config["model"])
    if agent:
        log.warning("an agent reply ran into the end of %s's %d-token window", config["model"], window)
        raise WindowOverflow(config["model"], window, reply_cut=True)
    log.warning("a reply ran into the end of %s's %d-token window and was cut off", config["model"], window)
    return WINDOW_END_NOTE


def model_reply(body: dict, config: dict, draft, risky, on_thinking, agent: bool) -> dict:
    try:
        if draft:
            return stream_chat(body, lambda piece: draft.feed(piece, risky), on_thinking=on_thinking)
        return http_json(f"{OLLAMA_URL}/api/chat", body=body)
    except (urllib.error.HTTPError, RuntimeError) as error:
        reason = ollama_error_text(error)
        if agent and WINDOW_OVERFLOW.search(reason):
            log.warning("an agent conversation did not fit %s's window: %s", config["model"], reason)
            raise WindowOverflow(config["model"], models.context_tokens(config, config["model"])) from None
        raise


def written_reply(body: dict) -> str:
    try:
        shown = http_json(f"{OLLAMA_URL}/api/chat", body={**body, "stream": False, "_debug_render_only": True})
        replay = {"model": body["model"], "prompt": shown["_debug_info"]["rendered_template"], "raw": True, "stream": False,
                  "options": body.get("options") or {}}
        replay.update({key: body[key] for key in ("keep_alive", "shift") if key in body})
        return http_json(f"{OLLAMA_URL}/api/generate", body=replay).get("response") or ""
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError) as error:
        log.warning("couldn't replay an empty model reply to see what it wrote: %s", error)
        return ""


def fitted_to_window(config: dict, body: dict) -> dict:
    fitted, left_out = window_fit.fit_to_window(config, body)
    if left_out:
        log.info("left out %d earlier messages so the prompt and a %d-token reply fit %s's window", left_out,
                 window_fit.reply_room(body), config["model"])
    return fitted


def run_agent(config, state, background=False, chat_approved=None, task=None, client_system="", client_tools=None, skip_groups=(),
              draft=None, looked_up=None, agent=False, on_thinking=None):
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
    instructions = client_system if agent and client_system else system_prompt(config, client_system, background, toolless)
    history = [{"role": "system", "content": instructions}] + messages
    looked_up = looked_up or ([], "", None)
    newest_request = APP_NOTES.sub("", message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {})))
    # Asked for something written (a post, an email): in the user's own voice (style.py).
    voice = style.voice_note() if style.is_writing_request(newest_request) else ""
    if not agent:
        with_context(history, current_context(config, newest_request) + looked_up[1] + voice)
        steer_away_from_repeats(history, messages, newest_request, background)
    native = [models.handoff_tool(config, definition) if name == "ask_other_model" else definition
              for name, definition in NATIVE_TOOLS.items() if not (background and name in BACKGROUND_ONLY_EXCLUDED)]
    all_tools = [] if toolless or agent else hub.tools + native
    hidden = config.get("hidden_tools") or []
    all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], hidden)]
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
    steps_this_turn = client_tool_runs(messages)[0]
    context["failed"] = []  # tools that didn't work this turn (see be_honest)
    nudged = False
    replays = 0
    searched_for_it = False
    client_names = {tool["function"]["name"] for tool in client_tools or []}
    last_request = APP_NOTES.sub("", message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {}))).strip()
    user_texts = [APP_NOTES.sub("", message_text(m)).strip() for m in messages if m.get("role") == "user"]
    think = thinking_for(config, agent_sessions.deciding_request(user_texts) if agent else last_request)
    shown_thinking = on_thinking if agent and client_system else None
    if not background and not agent and WRITE_ONLY_REQUEST.search(last_request):
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
            tools = tools + client_tools
        if answer_from_lookups and round_number == 0:
            tools = []
        if not (agent and client_system):
            history[0] = {"role": "system", "content": with_tool_guide(instructions, tools)}
        options = models.agent_options(config, config["model"], bool(think)) if agent else config.get("options", {})
        if answer_from_lookups:
            options = {**options, "temperature": LOOKUP_TEMPERATURE}  # stick to the looked-up facts
        body = models.for_runner(config, with_thinking({"model": config["model"], "messages": history, "stream": False,
                                                        "options": options, "keep_alive": config.get("keep_alive", "10m")}, think,
                                                       raises_reply_size=not agent))
        if tools:  # Ollama rejects a tools list for models without tool support
            body["tools"] = tools
        if agent:
            body["truncate"] = False
        else:
            body = fitted_to_window(config, body)
        risky = None
        if draft:
            # Hold back what the checks below might still throw away: a reply that will be sent back to use
            # its tools, or replaced by a web search, mustn't have been shown (or said out loud) already.
            may_nudge = not agent and not nudged and bool(tools) and not (tool_log or steps_this_turn)
            may_search = not agent and nudged and not (tool_log or steps_this_turn) and not searched_for_it and "research__web_search" in hub.routes
            draft.start_round(allowed=not (may_nudge and ACTION_REQUEST.search(last_request))
                              and not (may_search and SEARCH_REQUEST.search(last_request)))

            def risky(text, may_nudge=may_nudge, may_search=may_search):
                if agent:
                    return bool(PROMPT_LEAK.search(text))
                if PROMPT_LEAK.search(text) or TOOL_TALK.search(text):
                    return True
                if may_nudge and (PROMISE.search(text) or CLAIMED.search(text)):
                    return True
                return (may_nudge or may_search) and bool(CLARIFYING.search(text) or "web_search" in text)
        raw = model_reply(body, config, draft, risky, shown_thinking, agent)
        log.info("model: loaded in %.1fs, read the %s-token prompt in %.1fs (cached parts are free), wrote %s in %.1fs",
                 raw.get("load_duration", 0) / 1e9,
                 raw.get("prompt_eval_count", "?"), raw.get("prompt_eval_duration", 0) / 1e9,
                 raw.get("eval_count", "?"), raw.get("eval_duration", 0) / 1e9)
        if agent:
            state.setdefault("rounds", []).append(round_timings(config["model"], raw))
        message = raw["message"]
        calls = message.get("tool_calls") or []
        if written_calls.looks_swallowed(raw, tools) and replays < written_calls.REPLAY_LIMIT:
            replays += 1
            replayed_text = written_reply(body)
            offered = [tool["function"]["name"] for tool in tools]
            found = written_calls.calls_written_in(replayed_text)
            renamed = [written_calls.offered_name(call["function"]["name"], offered) for call in found]
            if found and all(renamed):
                calls = [{"function": {**call["function"], "name": name}} for call, name in zip(found, renamed)]
                message = {**message, "tool_calls": calls}
                log.info("Ollama dropped the model's call to %s; using the call it wrote", ", ".join(renamed))
            elif found:
                missing = [call["function"]["name"] for call, name in zip(found, renamed) if not name]
                log.warning("the model called %s, which it wasn't offered; told it which tools it has", ", ".join(missing))
                history.append({"role": "assistant", "content": replayed_text.strip()})
                history.append({"role": "user", "content": written_calls.missing_tool_note(missing, offered)})
                continue
            else:
                message = {**message, "content": written_calls.text_outside_calls(replayed_text)}
        if not calls:
            answer = message.get("content") or ""
            if background and context.get("blocked"):
                # Small models tend to report "please sign in" and stop; pause and ask properly instead.
                host = context.pop("blocked")
                state["groups"] = sorted(requested)
                return {"status": "waiting", "waiting": {"kind": "user", "message": f"Please sign in (or pass the bot check) on {host} in the browser on my desktop.",
                                                         "call": {"name": "ask_user", "arguments": {}}}, "tools_used": tool_log, "raw": raw}
            if not background and not agent and nudged and not (tool_log or steps_this_turn) and not searched_for_it and "research__web_search" in hub.routes and (
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
            if not background and not agent and not nudged and tools and not (tool_log or steps_this_turn) and (
                    PROMISE.search(answer) or CLAIMED.search(answer) or CLARIFYING.search(answer) or ACTION_REQUEST.search(last_request)):
                # Small models describe doing things ("I've set a reminder", "here are my findings") without
                # calling a tool; send them back once to actually do it.
                nudged = True
                history.append({"role": "assistant", "content": answer})
                history.append({"role": "user", "content": "(You haven't used any tools, so none of that has actually happened. "
                                f"{tool_guide.nudge_tools(tool_guide.offered_names(tools))} Only then answer, and only with what "
                                "the tools gave you. If none of your tools can do it, say so plainly in a sentence instead: never "
                                "pretend it's done.)"})
                continue
            if not background and not agent and not nudged and PRETEND_TOOLS.search(answer):
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
                answer += cut_short_note(config, raw, agent)
            state["groups"] = sorted(requested)
            return {"status": "done", "answer": answer, "tools_used": tool_log, "raw": raw}
        written = (message.get("content") or "").strip()
        if not background and not agent and all(call.get("function", {}).get("name") == "use_tools" for call in calls) \
                and len(written) >= ANSWERED_ALREADY_CHARS and not ACTION_REQUEST.search(last_request) \
                and not (PROMISE.search(written) or CLAIMED.search(written)):
            # It answered a plain question, then asked for tools it doesn't need (the next round would only
            # chat about the unlocked tools): keep the answer. Not when it's about to actually do something.
            state["groups"] = sorted(requested)
            return {"status": "done", "answer": message["content"], "tools_used": tool_log, "raw": raw}
        if draft:
            draft.before_tools()
        client_calls = calls if agent else [call for call in calls if call.get("function", {}).get("name") in client_names]
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
            elif policy.matches(name, hidden):
                result = HIDDEN_TOOL_RESULT
            elif name in NATIVE_TOOLS and (held := policy.untrusted_reason(name, arguments, context, rules)) \
                    and not policy.covers(chat_approved, held):
                return hold_for_approval(held, call, calls, background, state, requested, add, tool_log, raw)
            elif name in NATIVE_TOOLS:
                result, waiting = run_native_safely(name, arguments, config, background, task)
                requested.update(TOOL_UNLOCKS.get(name, ()))
                if waiting and waiting["kind"] == "chat_question" and not any(used.startswith(DESKTOP_TOOLS) for used in tool_log):
                    # Nothing on the desktop was tried, so no website can have blocked it: the model reached for this
                    # when unsure (it once answered "how do I kill someone" with the tool's sign-in example).
                    log.info("ignored ask_user with no desktop tool used: %s", waiting["message"][:120])
                    result, waiting = NOTHING_TO_SIGN_IN, None
                if waiting and waiting["kind"] == "chat_question":
                    state["groups"] = sorted(requested)
                    return {"status": "done", "answer": question_for_user(waiting["message"], config), "tools_used": tool_log, "raw": raw}
                if waiting:
                    waiting["call"] = {"name": name, "arguments": arguments}
                    state["groups"] = sorted(requested)
                    return {"status": "waiting", "waiting": waiting, "tools_used": tool_log, "raw": raw}
            elif refused := private_upload(name, arguments):
                result = refused
            else:
                know_the_page(name, context)
                reason = policy.approval_reason(name, arguments, context, rules)
                if reason and (background or not policy.covers(chat_approved, reason)):
                    # Ask in Arty's own words rather than handing the model an instruction it might echo.
                    return hold_for_approval(reason, call, calls, background, state, requested, add, tool_log, raw)
                else:
                    if background and task and name.startswith("workspace__") and not task.get("snapshot_taken") \
                            and config.get("background", {}).get("snapshot_before_desktop_work") and "homelab__snapshot_desktop" in hub.routes:
                        log.info("task %s: snapshot before desktop work: %s", task["id"], hub.call("homelab__snapshot_desktop", {"note": f"before task #{task['id']}: {task['title']}"}))
                        task["snapshot_taken"] = 1
                        store.update_task(task["id"], snapshot_taken=1)
                    result = hub.call(name, arguments)
                    policy.remember_tool_result(context, name, result, rules)
                    if reason:
                        chat_approved = None
            log.info("tool %s(%s) -> %d chars", name, json.dumps(arguments)[:200], len(result))
            if name != "use_tools" and TOOL_FAILED.search(result):
                context["failed"].append((name, result.strip().splitlines()[0][:200] if result.strip() else "no result"))
                result += FAILED_NOTE
            add({"role": "tool", "content": result, "tool_name": name})
            if background and task:
                save_progress(task, state, requested)
    state["groups"] = sorted(requested)
    return {"status": "done", "answer": "I stopped after too many tool steps. Tell me how you'd like me to continue.", "tools_used": tool_log, "raw": raw}


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


def client_tool_runs(conversation: list[dict], by_last_result: bool = False,
                     whole_conversation: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    last_request = max((index for index, message in enumerate(conversation) if message.get("role") == "user"), default=-1)
    counted_from = 0 if whole_conversation else last_request + 1
    results = [message for message in conversation[counted_from:] if message.get("role") == "tool" and message.get("tool_name")]
    used = [message["tool_name"] for message in results]
    judged = {message["tool_name"]: message for message in results}.values() if by_last_result else results
    failed = [(message["tool_name"], message_text(message)[:300]) for message in judged if TOOL_FAILED.search(message_text(message))]
    return used, failed


LOST_TRAIN_OF_THOUGHT = "Sorry, I lost my train of thought there. Could you ask that again?"
LOST_TRACK_START = "Sorry, I lost track before I could answer"
LOST_TRACK_AFTER_STEPS = (LOST_TRACK_START + ", after {steps} on your computer. Have a look at where it got to, and tell "
                          "me what you'd like next.")


def empty_reply(conversation: list[dict]) -> str:
    steps, _failed = client_tool_runs(conversation)
    if not steps:
        return LOST_TRAIN_OF_THOUGHT
    return LOST_TRACK_AFTER_STEPS.format(steps="1 step" if len(steps) == 1 else f"{len(steps)} steps")


def is_own_fallback(answer: str) -> bool:
    return answer == LOST_TRAIN_OF_THOUGHT or answer.startswith(LOST_TRACK_START)


def some_tool_worked(tools_used: list[str], failed: list[tuple[str, str]]) -> bool:
    failed_names = {name for name, _ in failed}
    return any(tool not in failed_names for tool in tools_used)


def ask_judge(prompt: str, max_tokens: int, model: str = "", keep_alive=None) -> dict:
    config = load_config()
    judge_model = model or config["model"]
    judge_keep_alive = keep_alive if model else config.get("keep_alive", "10m")
    body = models.for_runner(config, {"model": judge_model, "stream": False, "format": "json", "think": False, "keep_alive": judge_keep_alive,
                                      "options": {"temperature": 0, "num_predict": max_tokens},
                                      "messages": [{"role": "user", "content": prompt}]})
    return json.loads(http_json(f"{OLLAMA_URL}/api/chat", body=body)["message"]["content"])


def false_claims(answer: str, tools_used: list[str], failed: list[tuple[str, str]], request: str = "", model: str = "",
                 keep_alive=None) -> list[dict]:
    prompt = CLAIM_CHECK_PROMPT % (tools_record(tools_used, failed), request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)",
                                   answer[:CLAIM_CHECK_CHARS])
    try:
        found = ask_judge(prompt, CLAIM_CHECK_TOKENS, model, keep_alive).get("false_claims") or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim check failed, reply left as written: %s", error)
        return []
    candidates = [claim for claim in found if isinstance(claim, dict) and isinstance(claim.get("text"), str)
                  and claim["text"].strip() and claim["text"].strip() in answer]
    return [claim for claim in candidates if reports_an_action(request, claim["text"].strip(), model, keep_alive)]


def reports_an_action(request: str, sentence: str, model: str = "", keep_alive=None) -> bool:
    prompt = CLAIM_CONFIRM_PROMPT % (request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)", sentence)
    try:
        kind = ask_judge(prompt, CLAIM_CONFIRM_TOKENS, model, keep_alive).get("kind")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim confirmation failed, the sentence is treated as a claim: %s", error)
        return True
    return kind != "B"


def be_honest(answer: str, tools_used: list[str], failed: list[tuple[str, str]], already_sent: str = "",
              request: str = "", model: str = "", keep_alive=None) -> str:
    if is_own_fallback(answer):
        return answer
    failed_names = {name for name, _ in failed}
    acted = [tool for tool in tools_used if tool not in NOT_ACTIONS and tool not in LOOKUP_TOOLS]
    if acted and not failed_names & set(acted):
        return answer
    claims = false_claims(answer, tools_used, failed, request, model, keep_alive)
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


MAKING_TOOLS = {"schedule_task", "calendar_add", "list_add", "note_save", "deadline_add", "card_add"}
TURN_SECONDS = 15 * 60
TURN_SLACK_SECONDS = 5
NOTHING_TO_UNDO = ("The last thing I did wasn't setting or adding something (a reminder, event, list item, note, deadline or "
                   "card), so there's nothing for me to undo.")


def undo_last_turn(config) -> str:
    try:
        last = next(archive_records(config), None)
        finished = datetime.fromisoformat(last["time"]).timestamp() if last else None
    except (KeyError, ValueError, OSError):
        last, finished = None, None
    if last is None or finished is None or not MAKING_TOOLS & set(last.get("tools") or []):
        return NOTHING_TO_UNDO
    return undo.undo_last(made_between=(finished - TURN_SECONDS, finished + TURN_SLACK_SECONDS))


def phone_action(said: str) -> dict | None:
    return None if quick.refers_back(said) else phone.from_request(said)


def asks_for_something_else(said: str) -> bool:
    return bool(ASKED_TO_DO.search(said) or REMIND_REQUEST.search(said) or phone.from_request(said) or undo.is_request(said)
                or quick.calendar_add(said) or quick.list_add(said) or TALKED_ABOUT.search(said))


def quick_action(said: str) -> tuple[str, str] | None:
    """(reply, what was done) for an everyday request done directly by code (reminders, calendar, lists), or None.
    A small model mangles these tool calls and then says they worked; code does them reliably."""
    if REMIND_REQUEST.search(said) and (parts := reminder_from(said)):
        return set_reminder(parts, bool(CHAT_APP.search(said))), "schedule_task"
    if undo.is_request(said):
        return undo_last_turn(load_config()), "undo"
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
        reply = http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 120},
            "messages": [{"role": "user", "content": QUIZ_JUDGE_PROMPT % (question, expected, given[:500])}]}))
        marked = json.loads(reply["message"]["content"])
        return bool(marked.get("correct")), str(marked.get("feedback") or "")[:200]
    except (OSError, ValueError, KeyError) as error:
        log.warning("quiz marking failed: %s", error)
        return False, "(I couldn't mark that one properly, so it'll come back soon.)"


PLAIN_COMPLETION_TOKENS = 1500


def complete_text(prompt: str, json_mode: bool = False, think: bool | None = None) -> str:
    """One plain model call (no persona, memory or tools), for writing jobs like lecture notes and research."""
    config = load_config()
    options = {"temperature": 0.2, "num_predict": PLAIN_COMPLETION_TOKENS,
               "repeat_penalty": config.get("options", {}).get("repeat_penalty", 1.1)}  # small models can loop on one line
    body = models.for_runner(config, {"model": config["model"], "stream": False, "options": options,
                                      "keep_alive": config.get("keep_alive", "10m"), "messages": [{"role": "user", "content": prompt}],
                                      **({"format": "json"} if json_mode else {})})
    wanted = (not json_mode and thinking_for(config, writing_job=True)) if think is None else think
    body = with_thinking(body, wanted)
    return (http_json(f"{OLLAMA_URL}/api/chat", body=body).get("message") or {}).get("content") or ""


def classify_text(prompt: str) -> str:
    return complete_text(prompt, think=False)


def researched_answer(said: str, quick: bool) -> tuple[str, list[str]]:
    if quick:
        return research.run(said, hub.call, complete_text, pages=research.QUICK_PAGES, browser=False, classify=classify_text)
    return research.run(said, hub.call, complete_text, classify=classify_text)


def solo_conversation(messages):
    conversation = []
    for message in messages:
        if message.get("role") in ("user", "assistant"):
            conversation.append({"role": message["role"], "content": APP_NOTES.sub("", message_text(message)).strip()})
    return conversation


def solo_turn(model_name, messages):
    config = load_config()
    said = message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {}))
    if declined := safety.check(APP_NOTES.sub("", said), config.get("crisis_help")):
        kind, reply = declined
        return reply, [f"safety_{kind}"], {}, None
    body = models.for_runner(config, {"model": model_name, "messages": solo_conversation(messages), "stream": False,
                                      "keep_alive": config.get("keep_alive", "10m")})
    reply = http_json(f"{OLLAMA_URL}/api/chat", body=body)
    return ((reply.get("message") or {}).get("content") or "").strip(), [], reply, None


HIDDEN_TOOL_RESULT = "That tool isn't available here."


OLLAMA_DOWN = "the AI server's model runner isn't answering; try again in a minute"
OTHER_MODEL_KEEP_ALIVE = "3m"
MAIN_RELOAD_DELAY_SECONDS = 200
AGENT_KEEP_ALIVE = "15m"
AGENT_QUIET_SECONDS = 600
VISION_KEEP_ALIVE = "1m"
VISION_MAIN_RETURN_SECONDS = 30


def found_model(name: str) -> dict | None:
    try:
        return models.find(name)
    except (urllib.error.URLError, TimeoutError) as error:
        log.warning("could not list Ollama models: %s", error)
        raise ApiError(503, OLLAMA_DOWN) from error


def chosen_model(main: str, requested: str, any_installed: bool) -> str:
    if not requested.startswith(models.DROPDOWN_PREFIX):
        return main
    name = requested[len(models.DROPDOWN_PREFIX):]
    chosen = found_model(name)
    if models.usable(chosen):
        return chosen["name"]
    if not any_installed:
        return main
    if chosen is None:
        raise ApiError(400, f"{name} isn't downloaded on the AI server; pick one from /v1/models?all=1")
    if chosen["embedding"]:
        raise ApiError(400, f"{name} is an embedding model and can't chat")
    if models.kept_solo(chosen):
        raise ApiError(400, f"{name} is kept for plain chats (solo_models in brain.json), so it can't use Arty's tools")
    return chosen["name"]


class MainModelReturn:
    def __init__(self):
        self.lock = threading.Lock()
        self.timer = None
        self.running = 0
        self.due = 0.0
        self.kept_model = ""

    def hold(self, model: str):
        with self.lock:
            self.running += 1
            self.kept_model = model
            self._stop_timer()

    def release(self, main_config: dict, quiet_seconds: float):
        with self.lock:
            self.running = max(0, self.running - 1)
            self._return_after(main_config, quiet_seconds)

    def schedule(self, main_config: dict, delay: float = MAIN_RELOAD_DELAY_SECONDS):
        with self.lock:
            self._return_after(main_config, delay)

    def keeps(self, model: str) -> bool:
        with self.lock:
            has_session = self.running > 0 or self.timer is not None
            return has_session and bool(model) and model == self.kept_model

    def _stop_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _return_after(self, main_config: dict, delay: float):
        self.due = max(self.due, time.monotonic() + delay)
        if self.running:
            return
        self._stop_timer()
        timer = threading.Timer(max(0.0, self.due - time.monotonic()), self._bring_main_back, args=(main_config,))
        timer.daemon = True
        self.timer = timer
        timer.start()

    def _bring_main_back(self, main_config: dict):
        with self.lock:
            if self.running or self.timer is not threading.current_thread():
                return
            self.timer, self.due, self.kept_model = None, 0.0, ""
        reload_main_model(main_config)


main_model_return = MainModelReturn()
agent_sessions = AgentSessions()


def schedule_main_reload(main_config: dict):
    main_model_return.schedule(main_config)


def session_keep_alive(model: str, otherwise):
    return AGENT_KEEP_ALIVE if main_model_return.keeps(model) else otherwise


def use_requested_model(config: dict, requested: str, any_installed: bool, session: bool = False) -> bool:
    main_config = dict(config)
    config["model"] = chosen_model(config["model"], requested, any_installed)
    if config["model"] == main_config["model"]:
        return False
    config["keep_alive"] = AGENT_KEEP_ALIVE if session else session_keep_alive(config["model"], OTHER_MODEL_KEEP_ALIVE)
    models.make_room(config["model"], models.vram_budget(config))
    if session:
        main_model_return.hold(config["model"])
    else:
        schedule_main_reload(main_config)
    return True


def as_written(text: str) -> str:
    return text


def never_an_offer(_sentence: str) -> None:
    return None


def is_downloaded(name: str) -> bool | None:
    try:
        return models.find(name) is not None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log.warning("couldn't check whether %s is downloaded: %s", name, error)
        return None


def coding_model_or(config: dict, fallback: str) -> str:
    coding_name = config.get("coding_model", "")
    if not coding_name:
        return fallback
    if not is_downloaded(coding_name):
        log.warning("the coding model %s isn't available, using the main model", coding_name)
        return fallback
    return f"{models.DROPDOWN_PREFIX}{coding_name}"


def agent_model(config: dict, requested: str) -> str:
    if requested.startswith(models.DROPDOWN_PREFIX):
        named = requested[len(models.DROPDOWN_PREFIX):]
        if is_downloaded(named) is not False:
            return requested
        log.warning("an agent asked for %s, which isn't downloaded; choosing the model for it instead", named)
        requested = MODEL_ID
    return coding_model_or(config, requested)


RETIRED_MODEL_ID = "assistant-core"
MAIN_ENTRY_IDS = {MODEL_ID, RETIRED_MODEL_ID, ""}


def arty_description(config: dict, main: dict | None) -> str:
    if main is None:
        return ""
    coding_name, vision_name = config.get("coding_model", ""), config.get("vision_model", "")
    if not coding_name and not vision_name:
        return models.summary(main)
    jobs = [f"{main['name']} for chat"]
    if coding_name and coding_name == vision_name:
        jobs.append(f"{coding_name} for code and pictures")
    else:
        jobs += [f"{name} for {job}" for name, job in ((coding_name, "code"), (vision_name, "pictures")) if name]
    return "Picks the model for each request: " + ", ".join(jobs)


def chat_model(config: dict, requested: str, said: str) -> str:
    routes_coding_chats = bool(config.get("coding_model")) and bool(config.get("route_coding_chats", True))
    if requested not in MAIN_ENTRY_IDS or not routes_coding_chats or not coding.is_request(said):
        return requested
    return coding_model_or(config, requested)


def agent_turn(messages, requested_model, client_tools, on_text, any_installed, on_thinking=None):
    config = load_config()
    main_config = dict(config)
    holds_another_model = use_requested_model(config, agent_model(config, requested_model), any_installed, session=True)
    try:
        return agent_reply(config, messages, client_tools, on_text, on_thinking)
    finally:
        if holds_another_model:
            main_model_return.release(main_config, AGENT_QUIET_SECONDS)


def agent_reply(config, messages, client_tools, on_text, on_thinking):
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    said = APP_NOTES.sub("", message_text(last_user)).strip() if last_user is not None else ""
    if last_user is not None:
        desktop_look.user_active()
        if declined := safety.check(said, config.get("crisis_help")):
            kind, reply = declined
            log.info("declined a %s request from an agent", kind)
            return reply, [f"safety_{kind}"], {}, None
    messages, _ = expand_attachments(messages)
    conversation = to_ollama_messages([m for m in messages if m.get("role") != "system"])
    client_system = "\n".join(message_text(m) for m in messages if m.get("role") == "system").strip()
    state = {"messages": conversation, "context": {}, "groups": []}
    draft = drafts.Draft(on_text, as_written, never_an_offer) if on_text else None
    hub.refresh()
    outcome = run_agent(config, state, client_system=client_system, client_tools=client_tools, draft=draft, agent=True,
                        on_thinking=on_thinking)
    outcome["raw"] = {**(outcome.get("raw") or {}), ROUNDS_KEY: state.get("rounds", [])}
    if outcome["status"] == "client_tools":
        content = draft.finish(outcome["content"]) if draft else outcome["content"]
        return content, outcome["tools_used"], outcome["raw"], outcome["calls"]
    if outcome["status"] == "waiting":
        outcome = {"answer": outcome["waiting"]["message"], "tools_used": outcome["tools_used"], "raw": outcome["raw"]}
    answer = outcome.get("answer") or ""
    client_used, client_failed = client_tool_runs(conversation, by_last_result=True, whole_conversation=True)
    if not some_tool_worked(client_used, client_failed):
        answer = be_honest(answer, client_used + outcome["tools_used"], client_failed, draft.everything() if draft else "",
                           said, config["model"], config.get("keep_alive"))
    if draft:
        answer = draft.finish(answer)
    return answer, outcome["tools_used"], outcome["raw"], None


def run_turn(messages, requested_model="", client_tools=None, skip_groups=(), on_text=None, client="", any_installed=False,
             agent=False, on_thinking=None):
    """One chat turn. Returns (answer, tools_used, raw, client_calls); client_calls is set when the
    model wants the calling app to run one of its own tools, and answer is then whatever text came with it.
    on_text gets the answer's first sentences early (see drafts.py); the answer always starts with them.
    client names the calling app when it says ("phone": the Arty phone app, which can open calls and texts)."""
    if solo := models.solo_choice(requested_model):
        return solo_turn(solo["name"], messages)
    if local_addon and (handled := local_addon.turn(messages, requested_model, client_tools, skip_groups, on_text)) is not None:
        return handled
    if agent:
        return agent_turn(messages, requested_model, client_tools, on_text, any_installed, on_thinking)
    config = load_config()
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    said = APP_NOTES.sub("", message_text(last_user)).strip() if last_user is not None else ""
    turn_model = chat_model(config, requested_model, said)
    use_requested_model(config, turn_model, any_installed)
    on_coding_model = turn_model != requested_model
    if last_user is not None:
        desktop_look.user_active()
        if declined := safety.check(said, config.get("crisis_help")):
            kind, reply = declined
            log.info("declined a %s request", kind)
            archive_turn(config, said, reply, [f"safety_{kind}"])
            return reply, [f"safety_{kind}"], {}, None
        if quizzing := personal.quiz_turn(said, lambda q, e, g: judge_answer(config, q, e, g), client,
                                          asks_for_something_else(said)):
            archive_turn(config, said, quizzing, ["quiz"])
            return quizzing, ["quiz"], {}, None
        if TALKED_ABOUT.search(said):
            reply = tidy_answer(recap_conversations(config, said))
            archive_turn(config, said, reply, ["recall_conversations"])
            return reply, ["recall_conversations"], {}, None
        if action := phone_action(said):
            reply = phone.reply(action, client)
            archive_turn(config, said, phone.without_action(reply), [f"phone_{action['kind']}"])
            return reply, [f"phone_{action['kind']}"], {}, None
        if not quick.refers_back(said) and (done := quick_action(said)):
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
        if (reply := restore_memory_reply(said)) is not None:
            archive_turn(config, said, reply, ["memory__add_observations"])
            return reply, ["memory__add_observations"], {}, None
        if (reply := desktop_look.permission_reply(said, config.get("desktop_look"))) is not None:
            archive_turn(config, said, reply, ["self_start"])
            return reply, ["self_start"], {}, None
        if desktop_look.is_request(said):
            # The model wrote how-to guides for this instead of doing it: code does it, the model only chooses.
            hub.refresh()
            reply = desktop_look.customise(hub.call, complete_text, desktop_look.persona_of(config.get("desktop_look")))
            archive_turn(config, said, reply, ["workspace__run_command"])
            return reply, ["workspace__run_command"], {}, None
        if style.is_learn_request(said):
            reply = style.refresh(gather_writing, complete_text)
            archive_turn(config, said, reply, ["learn_style"])
            return reply, ["learn_style"], {}, None
        if not on_coding_model and style.is_new_draft(said):
            # A new post or email in the user's voice, from their request alone (style.py): with memories and recent
            # chats in view, a small model stitches unrelated true facts into made-up events.
            reply = style.draft(said, complete_text, config.get("user_name", ""))
            archive_turn(config, said, reply, ["draft"])
            return reply, ["draft"], {}, None
        if (not on_coding_model and research.is_quick_question(said) and not research.is_request(said)
                and not matches_lookup(config, said)):
            # Recent facts, "best ...", "how do I make ...": read the top pages (no browser) and answer with sources.
            hub.refresh()
            answer, used = researched_answer(said, quick=True)
            answer = tidy_answer(answer)
            archive_turn(config, said, answer, used)
            return answer, used, {}, None
        if research.is_request(said):
            # "Use your desktop to find out ...": fixed steps (search, read pages, note, write), see research.py.
            hub.refresh()
            answer, used = researched_answer(said, quick=False)
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
    outcome = run_agent(config, state, chat_approved=policy.approved_reason(conversation, message_text),
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
        answer = empty_reply(conversation)
    reminded = ensure_reminder(conversation, outcome["tools_used"], answer)
    if reminded != answer and reminded.startswith("Reminder set"):
        outcome["tools_used"].append("schedule_task")  # set here from the user's words
    answer = reminded
    answer = HASHTAG_LINE.sub("", answer)  # hashtags belong in drafted posts, not in answers
    client_used, client_failed = client_tool_runs(conversation)
    answer = be_honest(answer, client_used + outcome["tools_used"], client_failed + state.get("context", {}).get("failed", []),
                       draft.everything() if draft else "", said if last_user is not None else "", config["model"],
                       config.get("keep_alive"))
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

def fix_still_needed(waiting: dict) -> tuple[bool, str]:
    recheck = waiting.get("recheck")
    if not recheck:
        return True, ""
    try:
        diagnosis = json.loads(hub.call("homelab__diagnose", {"address": recheck["address"], "port": recheck["port"],
                                                              "guest_name": ""}))
    except (ValueError, TypeError, KeyError) as error:
        return True, f"I couldn't check it again first ({error}), so I went ahead as approved. "
    summary = str(diagnosis.get("summary") or "")
    fix = diagnosis.get("fix")
    approved = waiting.get("call") or {}
    if not fix and TOOL_FAILED.search(summary):
        return True, f"I couldn't check it again first ({summary}), so I went ahead as approved. "
    if not fix:
        return False, (f"Not done: by the time the user approved, it no longer needed fixing ({summary}). "
                       "Tell them nothing was restarted.")
    wanted = "homelab__start_guest" if fix.get("action") == "start" else "homelab__restart_guest"
    if wanted != approved.get("name") or fix.get("vmid") != (approved.get("arguments") or {}).get("vmid"):
        return False, (f"Not done: things changed since the user approved ({summary}). Tell them, and ask again "
                       "before doing anything.")
    return True, ""


def run_approved_call(call: dict) -> str:
    name, arguments = call["name"], call.get("arguments") or {}
    if name in NATIVE_TOOLS:
        return run_native_safely(name, arguments, load_config(), True, None)[0]
    return hub.call(name, arguments)


def apply_decision(state: dict, waiting: dict, decision: str, note: str):
    call = waiting.get("call") or {}
    if waiting.get("kind") == "approval":
        if decision != "approve":
            result = "The user said no to this. Don't do it; carry on without it, or finish and report."
        else:
            still_needed, recheck_note = fix_still_needed(waiting)
            if still_needed:
                outcome = run_approved_call(call)
                policy.remember_tool_result(state.setdefault("context", {}), call["name"], outcome)
                result = f"The user approved. {recheck_note}Result:\n{outcome}"
            else:
                result = recheck_note
    else:
        result = "The user says he has done it" + (f": {note}" if note else ".") + " Carry on."
    state["messages"].append({"role": "tool", "content": result, "tool_name": call.get("name", "ask_user")})


INTERRUPTED_DECISION = ("Arty restarted while carrying out what the user approved, so it may or may not have happened. "
                        "Check whether it took effect before doing anything else, and never do it a second time unasked.")


def carries_out_an_approval(waiting: dict) -> bool:
    return waiting.get("kind") == "approval" and waiting.get("decision") == "approve"


def process_task(task: dict):
    config = load_config()
    state = task["history"] or {"messages": [{"role": "user", "content": f"Background task #{task['id']}: {task['title']}\n\n{task['instructions']}"}]}
    waiting = task["waiting"] or {}
    if waiting.get("decision") and waiting.get("applying"):
        state["messages"].append({"role": "tool", "content": INTERRUPTED_DECISION,
                                  "tool_name": (waiting.get("call") or {}).get("name", "ask_user")})
    elif waiting.get("decision"):
        if carries_out_an_approval(waiting):
            store.update_task(task["id"], waiting={**waiting, "applying": True})
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
            notify.send(f"Arty needs you (task #{task['id']})", f"{task['title']}\n\n{pending['message']}\n\n{on_my_desktop(config)}, "
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


def fail_task(task: dict, error: Exception):
    try:
        store.update_task(task["id"], status="failed", result=f"failed: {error}")
        notify.send(f"Arty: task #{task['id']} failed", f"{task['title']}\n\n{error}")
    except Exception:
        log.exception("couldn't record that task %s failed", task["id"])


def worker_step() -> bool:
    try:
        task = store.claim_next_task()
    except Exception:
        log.exception("couldn't take the next task")
        return False
    if task is None:
        return False
    log.info("task %s started: %s", task["id"], task["title"])
    try:
        process_task(task)
    except Exception as error:
        log.exception("task %s failed", task["id"])
        fail_task(task, error)
    return True


def worker_loop():
    while True:
        if not worker_step():
            time.sleep(WORKER_IDLE_SECONDS)


def run_schedule(schedule: dict, now: datetime):
    # Move it on before acting, so a failure below can't make it fire again every tick.
    if schedule["cron"]:
        due = datetime.fromtimestamp(schedule["next_run"]) if schedule.get("next_run") else now
        try:
            following = timing.following_run(schedule["cron"], due, now).timestamp()
        except ValueError as error:
            log.warning("schedule %s can't run again (%s); switched off", schedule["id"], error)
            store.update_schedule(schedule["id"], enabled=0, next_run=None, last_run=now.timestamp())
            following = None
        if following is not None:
            store.update_schedule(schedule["id"], next_run=following, last_run=now.timestamp())
    else:
        store.update_schedule(schedule["id"], enabled=0, next_run=None, last_run=now.timestamp())
    if schedule.get("message"):
        # A plain reminder: send the exact text, no model needed (and nothing to get wrong).
        send_reminder(schedule, now)
        return
    if kind := briefing.kind_of(schedule["instructions"]):
        # Built by code from the real data (the model made up placeholder events); in the background.
        threading.Thread(target=send_briefing, args=(schedule["title"], kind), name="briefing", daemon=True).start()
        return
    created = store.create_task(schedule["title"], schedule["instructions"], "schedule", schedule["id"])
    store.update_schedule(schedule["id"], last_task_id=created["id"])
    log.info("schedule %s queued task %s", schedule["id"], created["id"])


def start_style_refresh():
    if style.due() and time.time() - _style_attempt["time"] > STYLE_RETRY_SECONDS:
        # Weekly: relearn how the user writes, from what they've written since (in the background).
        _style_attempt["time"] = time.time()
        threading.Thread(target=style.refresh, args=(gather_writing, complete_text), name="style", daemon=True).start()


def start_email_watch():
    if email_watch.due(load_config().get("email_watch", {})):
        # In the background: reading Gmail and sorting it can take a minute, and reminders mustn't wait.
        threading.Thread(target=email_watch.check_new, name="email-watch", daemon=True,
                         args=(hub.call, complete_text, lambda title, message: notify.send(title, message))).start()


def start_desktop_look():
    settings = load_config().get("desktop_look", {})
    if desktop_look.due(settings):
        threading.Thread(target=desktop_look.tinker, args=(hub.call, complete_text, desktop_look.persona_of(settings)),
                         name="desktop-look", daemon=True).start()


_expiry_running = threading.Lock()


def check_expiry():
    if not _expiry_running.acquire(blocking=False):
        return
    try:
        for message in expiry.due_warnings(load_config().get("expiry_watch", {})):
            notify.send("⌛ Expiring soon", message)
    except Exception:
        log.exception("expiry watch failed")
    finally:
        _expiry_running.release()


def start_expiry_check():
    threading.Thread(target=check_expiry, name="expiry", daemon=True).start()


def tidy_completion(prompt: str) -> str:
    return complete_text(prompt, json_mode=True, think=False)


def start_memory_tidy():
    if memory_tidy.due(load_config().get("memory_tidy", {})):
        threading.Thread(target=memory_tidy.run_in_background, args=(memory_hub, tidy_completion), name="memory-tidy",
                         daemon=True).start()


def scheduler_tick(now: datetime):
    for schedule in due_schedules_now(now):
        guarded(f"schedule {schedule['id']}", lambda schedule=schedule: run_schedule(schedule, now))
    guarded("calendar or deadline reminders", send_calendar_and_deadline_reminders)
    guarded("style refresh", start_style_refresh)
    guarded("email watch", start_email_watch)
    guarded("desktop look", start_desktop_look)
    guarded("expiry watch", start_expiry_check)
    guarded("memory tidy", start_memory_tidy)


def scheduler_loop():
    while True:
        guarded("scheduler tick", lambda: scheduler_tick(datetime.now()))
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


# ---------- announcements: everything Arty tells the user, as a feed for its own apps ----------


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
_healing_lock = threading.Lock()


def claim_healing(name: str) -> bool:
    with _healing_lock:
        if time.time() - _healing.get(name, 0) < HEAL_COOLDOWN_SECONDS:
            return False
        _healing[name] = time.time()
        return True


def monitor_port(monitor: dict, url) -> int:
    try:
        return int(monitor.get("port") or url.port or (443 if url.scheme == "https" else 80 if url.scheme == "http" else 0))
    except (TypeError, ValueError):
        return 0


def usable_fix(diagnosis: dict) -> dict | None:
    fix = diagnosis.get("fix")
    if not isinstance(fix, dict) or fix.get("action") not in ("start", "restart") or fix.get("vmid") in (None, ""):
        return None
    return {**fix, "name": str(fix.get("name") or f"guest {fix['vmid']}")}


def self_heal(monitor: dict):
    """A service went down: diagnose it, and if starting or restarting its guest would fix it, offer that
    with Fix / Ignore buttons (a background task waiting for approval, like any other)."""
    name = str(monitor.get("name") or "A service")
    if not claim_healing(name):
        return
    try:
        url = urllib.parse.urlparse(str(monitor.get("url") or ""))
        address = str(monitor.get("hostname") or url.hostname or "")
    except ValueError:
        url, address = urllib.parse.urlparse(""), str(monitor.get("hostname") or "")
    port = monitor_port(monitor, url)
    if not address:
        return
    hub.refresh()
    try:
        diagnosis = json.loads(hub.call("homelab__diagnose", {"address": address, "port": port, "guest_name": ""}))
    except (ValueError, TypeError):
        log.warning("self-heal: diagnosis of %s failed", name)
        return
    if not isinstance(diagnosis, dict):
        log.warning("self-heal: diagnosis of %s wasn't an object", name)
        return
    diagnosis["summary"] = str(diagnosis.get("summary") or f"{name} isn't answering.")
    fix = usable_fix(diagnosis)
    if not fix:
        notify.send(f"🔎 {name} is down", diagnosis["summary"])
        return
    tool = "homelab__start_guest" if fix["action"] == "start" else "homelab__restart_guest"
    task = store.create_task(f"Fix: {name} is down", f"{name} went down. {diagnosis['summary']}", "self-heal")
    state = {"messages": [{"role": "user", "content": (
        f"Background task #{task['id']}: {name} ({address}{':' + str(port) if port else ''}) went down. Diagnosis: {diagnosis['summary']} "
        f"You asked the user whether to {fix['action']} {fix['name']}. When you get their answer, if it was done, wait a moment, then "
        f"call homelab__diagnose with address {address} and port {port} to check it's answering again, and report in one or two sentences.")}],
        "context": {}, "groups": ["homelab"]}
    waiting = {"kind": "approval", "message": f"{fix['action']} {fix['name']}", "call": {"name": tool, "arguments": {"vmid": fix["vmid"]}},
               "recheck": {"address": address, "port": port}}
    store.update_task(task["id"], status="waiting", history=state, waiting=waiting)
    buttons = task_links(store.get_task(task["id"]), [(f"Fix ({fix['action']} {fix['name']})", "approve"), ("Ignore", "deny")])
    notify.send(f"🛠 {name} is down", f"{diagnosis['summary']}\n\nShall I {fix['action']} {fix['name']}?", buttons, urgent=True)


def record_alert(body: dict) -> str:
    """Uptime Kuma webhook: a service went down or came back. It only goes into the feed (the desktop
    app says it out loud, the phone shows it), like every notification."""
    monitor = (body.get("monitor") if isinstance(body.get("monitor"), dict) else {}).get("name") or "A service"
    heartbeat = body.get("heartbeat") if isinstance(body.get("heartbeat"), dict) else {}
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
        details = body.get("monitor") if isinstance(body.get("monitor"), dict) else {}
        threading.Thread(target=self_heal, args=(details,), name="self-heal", daemon=True).start()
    return title


class ChatJobs:
    """Chat turns run in the background for clients behind Cloudflare, which cuts off any request
    that waits over 100 seconds: POST starts a turn and returns an id, GET polls it.
    Kept in memory only (a brain restart forgets unfinished jobs) and pruned after an hour.
    While it runs, "partial" holds the answer's first sentences (the final answer starts with them)."""
    KEEP_SECONDS = 3600

    class Busy(Exception):
        pass

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.pruned: dict[str, None] = {}
        self.lock = threading.Lock()

    def prune(self, now: float):
        for old in [k for k, j in self.jobs.items() if now - j["created"] > self.KEEP_SECONDS]:
            del self.jobs[old]
            self.pruned[old] = None
        while len(self.pruned) > PRUNED_JOBS_KEPT:
            del self.pruned[next(iter(self.pruned))]

    def running(self) -> int:
        return sum(1 for job in self.jobs.values() if job["status"] == "running")

    def start(self, messages, model, client="") -> str:
        job_id = uuid.uuid4().hex
        with self.lock:
            now = time.time()
            self.prune(now)
            if self.running() >= MAX_RUNNING_CHAT_JOBS:
                raise ChatJobs.Busy()
            self.jobs[job_id] = {"status": "running", "created": now}
        threading.Thread(target=self._run, args=(job_id, messages, model, client), daemon=True).start()
        return job_id

    def _run(self, job_id, messages, model, client):
        try:
            answer, tools_used, _, _ = run_turn(messages, model, on_text=lambda text: self._add_partial(job_id, text),
                                                client=client)
            outcome = {"status": "done", "answer": answer, "tools_used": tools_used}
        except Exception:
            log.exception("chat job %s failed", job_id)
            outcome = {"status": "failed", "error": GENERIC_ERROR}
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

    def expired(self, job_id) -> bool:
        with self.lock:
            self.prune(time.time())
            return job_id in self.pruned


chat_jobs = ChatJobs()
device_requests = devices.Requests()
remote = remote_sessions.Sessions()
direct_chat_slots = threading.BoundedSemaphore(MAX_DIRECT_CHATS)


def logged_request_line(line: str) -> str:
    return DOWNLOAD_TOKEN.sub(r"\1...", SECRET_QUERY_VALUE.sub(r"\1...", line))


def issue_device_key(request: devices.Request) -> str:
    key = devices.new_device_key()
    store.add_device(request.name, request.address, devices.key_hash(key))
    return key


def make_device_key(name: str, address: str) -> dict:
    if len(store.list_devices()) >= devices.MAX_ACTIVE_DEVICES:
        raise ApiError(429, "too many device keys are live; turn some off in Approved devices first")
    named = devices.cleaned_name(name)
    key = devices.new_device_key()
    made = store.add_device(named, address, devices.key_hash(key))
    return {"id": made, "name": named, "api_key": key}


def is_main_key(supplied: str) -> bool:
    return any(same_secret(supplied, f"Bearer {key}") for key in (BRAIN_KEY, APP_KEY) if key)


def is_device_key(supplied: str) -> bool:
    token = supplied.removeprefix("Bearer ")
    if not token.startswith(devices.DEVICE_KEY_PREFIX):
        return False
    return store.active_device(devices.key_hash(token)) is not None


def remote_caller(supplied: str) -> remote_sessions.Caller:
    if is_main_key(supplied):
        return remote_sessions.Caller(remote_sessions.MAIN_KEY_ID, "Arty's own key", True)
    token = supplied.removeprefix("Bearer ")
    device = store.active_device(devices.key_hash(token)) if token.startswith(devices.DEVICE_KEY_PREFIX) else None
    if device is None:
        raise ApiError(401, "invalid API key")
    return remote_sessions.Caller(f"device:{device['id']}", device["name"], bool(device["remote_control"]))


def device_started(request: devices.Request) -> dict:
    return {"device_code": request.device_code, "user_code": request.user_code,
            "verify_path": f"/{web_app.PREFIX}/?authorise={request.user_code}",
            "interval": devices.POLL_SECONDS, "expires_in": device_requests.seconds_left(request)}


class Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_IDLE_SECONDS

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), logged_request_line(fmt % args))

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
        if is_main_key(supplied) or is_device_key(supplied):
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

    def body(self, limit: int = MAX_BODY_BYTES) -> dict:
        length = self.body_length()
        if length > limit:
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
        try:
            return self.task_link(method)
        except sqlite3.Error:
            log.exception("task link failed")
            return self.send_html(503, "Please try again", f"<p>{escape(TASK_LINK_TROUBLE)}</p>")

    def task_link(self, method):
        segments, query = self.route()
        if len(segments) != 4 or segments[1] != "tasks" or segments[3] not in TASK_ACTIONS or not segments[2].isdigit():
            return self.send_html(404, "Not found", "")
        task = store.get_task(int(segments[2]))
        token = (query.get("token") or [""])[0]
        if task is None or not same_secret(token, task["token"]):
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
        if segments == ["v1", "device", "approver"]:
            return self.send_json(200, {"approve_page": devices.approve_page(STARFIRE_URL)})
        if segments == ["v1", "identity"]:
            nonce = (query.get("nonce") or [""])[0]
            if not IDENTITY_NONCE.fullmatch(nonce):
                return self.send_json(400, {"error": {"message": "nonce must be 16 to 128 hex characters"}})
            try:
                keys = [BRAIN_KEY, APP_KEY, *store.active_device_hashes()]
            except sqlite3.Error:
                log.exception("could not read the approved devices")
                return self.send_json(503, {"error": {"message": "try again in a minute"}})
            return self.send_json(200, {"proofs": identity_proofs(nonce, keys)})
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
        except CLIENT_DROPPED_ERRORS:
            log.info("%s hung up before the answer (%s)", self.address_string(), self.path.split("?")[0])
        except Exception:
            log.exception("request failed")
            self.send_json(500, {"error": {"message": GENERIC_ERROR}})

    def relay(self, method, segments, query, body):
        try:
            caller = remote_caller(self.headers.get("Authorization", ""))
            status, answer = remote_sessions.handle(remote, caller, method, segments[2:], query, body)
        except remote_sessions.RemoteError as error:
            status, answer = error.status, {"error": {"message": str(error)}}
        return self.send_json(status, answer)

    def dispatch_get(self, segments, query):
        if segments[:2] == ["v1", "remote"]:
            return self.relay("GET", segments, query, {})
        if segments == ["v1", "devices"]:
            return self.send_json(200, {"devices": store.list_devices()})
        if segments == ["v1", "device", "pending"]:
            waiting = device_requests.pending((query.get("code") or [""])[0])
            if waiting is None:
                raise ApiError(404, "that code has run out or was never made")
            return self.send_json(200, {"name": waiting.name, "address": waiting.address, "user_code": waiting.user_code,
                                        "expires_in": device_requests.seconds_left(waiting)})
        if segments in (["v1", "models"], ["models"]) and (query.get("all") or [""])[0] == "1":
            try:
                downloaded = models.downloaded_chat_models()
            except (urllib.error.URLError, TimeoutError) as error:
                log.warning("could not list Ollama models: %s", error)
                raise ApiError(503, OLLAMA_DOWN) from error
            config = load_config()
            coding_name = config.get("coding_model", "")
            reply_size = models.reply_tokens(config)
            return self.send_json(200, {"object": "list", "data": [
                {"id": f"{models.DROPDOWN_PREFIX}{m['name']}", "object": "model", "owned_by": "home", "model": m["name"],
                 "description": models.summary(m), "coding": models.is_for_coding(m["name"], coding_name),
                 "context_tokens": models.context_tokens(config, m["name"]), "reply_tokens": reply_size, "agent_sampling": models.agent_sampling(config, m["name"])}
                for m in downloaded]})
        if segments in (["v1", "models"], ["models"]):
            listed = [{"id": MODEL_ID, "object": "model", "owned_by": "home"}]
            try:
                config = load_config()
                main = models.find(config.get("model", ""))
                listed[0].update(model=main["name"] if main else "", description=arty_description(config, main))
                listed += [{"id": f"{models.DROPDOWN_PREFIX}{m['name']}", "name": f"Arty ({m['name']}{'' if m['tools'] else ', no tools'})", "object": "model", "owned_by": "home",
                            "model": m["name"], "description": models.summary(m)}
                           for m in models.chat_models()]
                listed += models.solo_entries()
                listed += local_addon.listed_models() if local_addon else []
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
            if job is None and chat_jobs.expired(segments[3]):
                return self.send_json(410, {"error": "expired"})
            if job is None:
                raise ApiError(404, "no such chat job (the brain restarted)")
            return self.send_json(200, {"id": segments[3], **job})
        if segments == ["v1", "tasks"]:
            status = (query.get("status") or [""])[0]
            limit = list_limit(query, 30)
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
            limit = min(list_limit(query, 10), 100)
            config = load_config()
            records = recall_records(config, q, limit) if q else [r for _, r in zip(range(limit), archive_records(config))]
            return self.send_json(200, {"conversations": records})
        raise ApiError(404, "not found")

    def do_POST(self):
        segments, query = self.route()
        if segments == ["hooks", "alert"]:
            token = (query.get("token") or [""])[0]
            if not ALERT_TOKEN or not same_secret(token, ALERT_TOKEN):
                return self.send_json(403, {"error": {"message": "bad alert token"}})
            try:
                return self.send_json(200, {"recorded": record_alert(self.body())})
            except ApiError as error:
                return self.send_json(error.status, {"error": {"message": str(error)}})
        if segments == ["hooks", "notify"]:
            # The homelab's own watchers (e.g. homelab-security on the host) alerting the user through Arty.
            token = (query.get("token") or [""])[0]
            if not ALERT_TOKEN or not same_secret(token, ALERT_TOKEN):
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
        if segments[:2] == ["v1", "device"] and segments[2:] in (["start"], ["token"]):
            return self.unkeyed_device(segments[2])
        if not self.authorised():
            return
        try:
            if segments == ["v1", "files"]:
                return self.upload()
            self.dispatch_post(segments, self.body())
        except ApiError as error:
            self.send_json(error.status, {"error": {"message": str(error)}})
        except CLIENT_DROPPED_ERRORS:
            # The answer was still archived; the app just wasn't there to receive it.
            log.warning("%s hung up before the answer (%s)", self.address_string(), self.path.split("?")[0])
        except Exception:
            log.exception("request failed")
            self.send_json(500, {"error": {"message": GENERIC_ERROR}})

    def do_DELETE(self):
        segments, _ = self.route()
        if not self.authorised():
            return
        try:
            self.dispatch_delete(segments)
        except CLIENT_DROPPED_ERRORS:
            log.info("%s hung up before the answer (%s)", self.address_string(), self.path.split("?")[0])
        except Exception:
            log.exception("request failed")
            self.send_json(500, {"error": {"message": GENERIC_ERROR}})

    def dispatch_delete(self, segments):
        if segments[:2] == ["v1", "remote"]:
            return self.relay("DELETE", segments, {}, {})
        if len(segments) == 3 and segments[:2] == ["v1", "devices"] and segments[2].isdigit():
            if not is_main_key(self.headers.get("Authorization", "")):
                return self.send_json(403, {"error": {"message": "only Arty's own key can revoke devices"}})
            found = store.revoke_device(int(segments[2]))
            return self.send_json(200 if found else 404, {"ok": found})
        if len(segments) == 3 and segments[:2] == ["v1", "schedules"] and segments[2].isdigit():
            found = store.update_schedule(int(segments[2]), enabled=0, next_run=None)
            return self.send_json(200 if found else 404, {"ok": found})
        if len(segments) == 3 and segments[:2] == ["v1", "calendar"] and segments[2].isdigit():
            found = store.delete_event(int(segments[2]))
            return self.send_json(200 if found else 404, {"ok": found})
        self.send_json(404, {"error": {"message": "not found"}})

    def switch_remote_control(self, device_id, request):
        if not is_main_key(self.headers.get("Authorization", "")):
            raise ApiError(403, "only Arty's own key can change which devices control Arty CLI")
        if not device_id.isdigit():
            raise ApiError(404, "no such device")
        if not isinstance(request.get("allow"), bool):
            raise ApiError(400, "allow must be true or false")
        if not store.set_device_remote_control(int(device_id), request["allow"]):
            raise ApiError(404, "no such device")
        return self.send_json(200, {"ok": True})

    def caller_address(self) -> str:
        peer = self.client_address[0]
        forwarded = (self.headers.get("Cf-Connecting-Ip") or "").strip()
        return forwarded if forwarded and peer in TRUSTED_PROXIES else peer

    def unkeyed_device(self, step: str):
        try:
            body = self.body(UNKEYED_BODY_BYTES)
            if step == "start":
                started = device_requests.start(text_field(body, "name"), self.caller_address())
                return self.send_json(200, device_started(started))
            status, answer = device_requests.collect(text_field(body, "device_code"), issue_device_key)
            return self.send_json(status, answer)
        except ApiError as error:
            return self.send_json(error.status, {"error": {"message": str(error)}})
        except devices.DeviceError as error:
            return self.send_json(error.status, {"error": {"message": str(error)}})

    def dispatch_post(self, segments, request):
        if segments[:2] == ["v1", "remote"]:
            return self.relay("POST", segments, {}, request)
        if len(segments) == 4 and segments[:2] == ["v1", "devices"] and segments[3] == "remote-control":
            return self.switch_remote_control(segments[2], request)
        if segments == ["v1", "devices"]:
            if not is_main_key(self.headers.get("Authorization", "")):
                raise ApiError(403, "only Arty's own key can make device keys")
            return self.send_json(201, make_device_key(text_field(request, "name"), self.caller_address()))
        if segments == ["v1", "device", "approve"]:
            if not is_main_key(self.headers.get("Authorization", "")):
                raise ApiError(403, "only Arty's own key can approve devices")
            if not isinstance(request.get("approve"), bool):
                raise ApiError(400, "approve must be true or false")
            if not device_requests.answer(text_field(request, "user_code"), request["approve"]):
                raise ApiError(404, "that code has run out or was already answered")
            return self.send_json(200, {"ok": True})
        if segments in (["v1", "chat", "completions"], ["chat", "completions"]):
            return self.limited_chat(request)
        if segments == ["v1", "warm"]:
            warming = bool(local_addon) and local_addon.warm(text_field(request, "model"))
            return self.send_json(202 if warming else 200, {"warming": warming})
        if segments == ["v1", "tasks"]:
            instructions = str(request.get("instructions", "")).strip()
            if not instructions:
                raise ApiError(400, "instructions are required")
            title = str(request.get("title") or instructions[:60])
            if declined := declined_reply(load_config(), title, instructions):
                raise ApiError(400, declined)
            created = store.create_task(title, instructions, "api")
            return self.send_json(201, public_task(created))
        if len(segments) == 4 and segments[:2] == ["v1", "tasks"] and segments[2].isdigit() and segments[3] in TASK_ACTIONS:
            status, message = decide_task(int(segments[2]), segments[3], str(request.get("message", "")))
            return self.send_json(status, {"message": message})
        if segments == ["v1", "schedules"]:
            created, message = create_schedule(request, load_config().get("crisis_help"))
            if created is None:
                raise ApiError(400, message)
            return self.send_json(201, {**created, "message": message})
        if segments == ["v1", "chat", "jobs"]:
            messages = valid_messages(request.get("messages"))
            try:
                # "client": "phone" from the Arty phone app, which can open calls and texts (phone.py).
                job_id = chat_jobs.start(messages, str(request.get("model") or ""), client_name(request))
            except ChatJobs.Busy:
                raise ApiError(503, "Arty is busy with other answers; try again in a moment") from None
            return self.send_json(202, {"id": job_id, "status": "running"})
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
            try:
                result, _ = run_native(name, arguments, load_config(), False, None)
            except NATIVE_TOOL_ERRORS as error:
                raise ApiError(400, f"{name} couldn't use those arguments ({type(error).__name__})") from None
            return self.send_json(200, {"result": result})
        hub.refresh()
        if name not in hub.routes:
            raise ApiError(404, f"unknown tool {name}")
        if refused := private_upload(name, arguments):
            raise ApiError(403, refused)
        context: dict = {}
        know_the_page(name, context)
        reason = policy.approval_reason(name, arguments, context, load_config().get("approval", {}))
        if reason and not request.get("approved"):
            return self.send_json(409, {"error": {"message": f"needs approval: {reason}", "type": "needs_approval", "reason": reason}})
        return self.send_json(200, {"result": hub.call(name, arguments)})

    def download(self, segments):
        """Serves a stored file to anyone with its link (the token in the path), like the task hooks."""
        meta = media.for_download(segments[1], segments[2]) if len(segments) >= 3 else None
        try:
            handle = open(meta["path"], "rb") if meta else None
        except OSError:
            handle = None
        if handle is None:
            return self.send_html(404, "Link expired", "<p>Files are kept for 24 hours. Ask Arty to make it again.</p>")
        with handle:
            self.send_file(handle, meta)

    def send_file(self, handle, meta):
        size = os.fstat(handle.fileno()).st_size
        self.send_response(200)
        self.send_header("Content-Type", media.safe_mime(meta["mime"]))
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(meta['name'])}")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
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
        except OSError:
            log.exception("upload could not be stored")
            raise ApiError(503, "the AI server couldn't store that file just now; try again in a minute") from None
        self.send_json(201, media.public(meta))

    def vision(self, request, messages):
        """Messages carrying images (e.g. a desktop screenshot) go straight to a vision model: no persona,
        tools or archive, since the caller asks a precise question about a picture."""
        max_tokens = optional_int(request.get("max_tokens")) if request.get("max_tokens") else None
        if request.get("max_tokens") and max_tokens is None:
            raise ApiError(400, "max_tokens must be a positive whole number")
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
        options = {k: v for k, v in config.get("options", {}).items() if k == "num_predict"}
        if max_tokens:
            options["num_predict"] = max_tokens
        models.make_room(model["name"], models.vram_budget(config))
        try:
            raw = http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
                "model": model["name"], "messages": converted, "stream": False, "options": options,
                "keep_alive": session_keep_alive(model["name"], VISION_KEEP_ALIVE)}))
        finally:
            main_model_return.schedule(config, VISION_MAIN_RETURN_SECONDS)
        answer = (raw.get("message") or {}).get("content", "")
        self.send_json(200, {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()), "model": model["name"],
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]})

    def limited_chat(self, request):
        if not direct_chat_slots.acquire(blocking=False):
            raise ApiError(503, "Arty is busy with other chats; try again in a moment")
        try:
            return self.chat(request)
        finally:
            direct_chat_slots.release()

    def chat(self, request):
        messages = valid_messages(request.get("messages"))
        # Apps asking a precise question about a picture (e.g. a desktop screenshot) can skip Arty and ask the
        # vision model directly. Everything else, attachments included, goes through Arty with its tools.
        if self.headers.get("X-Arty-Direct-Vision", "") == "1" or request.get("arty_direct_vision") is True:
            return self.vision(request, messages)
        # Only apps that opt in get their tools run by the model (the desktop app does). Open WebUI sends its
        # own built-in tools with every chat; those must not reach Arty's model.
        opted_in = self.headers.get("X-Arty-Client-Tools", "") == "1" or request.get("arty_client_tools") is True
        client_tools = [t for t in request.get("tools") or [] if isinstance(t, dict) and t.get("type") == "function"] if opted_in else []
        skip_groups = [g.strip() for g in self.headers.get("X-Arty-Skip-Groups", "").split(",") if g.strip()] if opted_in else []
        any_installed = opted_in and self.headers.get("X-Arty-Any-Model", "") == "1"
        agent = opted_in and self.headers.get("X-Arty-Agent", "") == "1"
        asked_for_reasoning = self.headers.get("X-Arty-Reasoning", "") == "1" or request.get("arty_reasoning") is True
        requested_model = str(request.get("model") or "")
        if any_installed:
            chosen_model("", requested_model, any_installed)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        turn = (messages, requested_model, client_tools, skip_groups)
        client = client_name(request)
        if not request.get("stream"):
            try:
                answer, tools_used, raw, client_calls = run_turn(*turn, client=client, any_installed=any_installed, agent=agent)
            except WindowOverflow as overflow:
                return self.send_json(overflow.status, {"error": overflow.details()})
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sent, stream = [], keepalive.Stream(self.wfile, STREAM_WORKING_EVERY_SECONDS)

            def send_chunk(delta, finish=None, **extra):
                chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": MODEL_ID,
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
                return stream.write(f"data: {json.dumps(chunk)}\n\n".encode())

            def send_early(text):
                # The answer's first sentences, while the model writes the rest (drafts.py). An app that hangs
                # up mustn't stop the turn: the answer is still archived.
                sent.append(text)
                if stream.open and not send_chunk({"role": "assistant", "content": text} if len(sent) == 1 else {"content": text}):
                    log.warning("%s hung up during a streamed answer", self.address_string())

            def send_reasoning(text):
                send_chunk({"reasoning_content": text})
            try:
                with stream:
                    answer, tools_used, raw, client_calls = run_turn(
                        *turn, on_text=send_early, client=client, any_installed=any_installed, agent=agent,
                        on_thinking=send_reasoning if agent and asked_for_reasoning else None)
            except WindowOverflow as overflow:
                if stream.open:
                    after_early_text = "\n\n" if sent else ""
                    send_chunk({"role": "assistant", "content": f"{after_early_text}{overflow}"})
                    send_chunk({}, finish="stop", error=overflow.details())
                    stream.write(b"data: [DONE]\n\n")
                return
            except Exception:
                log.exception("streamed chat failed")
                if stream.open:
                    after_early_text = "\n\n" if sent else ""
                    send_chunk({"role": "assistant", "content": f"{after_early_text}({GENERIC_ERROR})"}, finish="stop")
                    stream.write(b"data: [DONE]\n\n")
                return
            if not stream.open:
                return
        usage = {"prompt_tokens": raw.get("prompt_eval_count", 0), "completion_tokens": raw.get("eval_count", 0)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        if agent:
            usage["rounds"] = turn_rounds(raw)
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
            stream.write(b"data: [DONE]\n\n")
            return
        self.send_json(200, {
            "id": completion_id, "object": "chat.completion", "created": created, "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer, **({"tool_calls": openai_calls} if openai_calls else {})},
                         "finish_reason": finish}],
            "usage": usage, "tools_used": tools_used,
        })


def init_storage(config):
    store.init()
    personal.init(config.get("starter_deck"))
    lectures.init()


LOCAL_ADDON = "local_addon"


def load_local_addon():
    try:
        addon = importlib.import_module(LOCAL_ADDON)
    except ModuleNotFoundError as error:
        if error.name != LOCAL_ADDON:
            raise
        return None
    if not hasattr(addon, "attach"):
        log.warning("%s has no add-on code in it, so the brain runs without it", LOCAL_ADDON)
        return None
    addon.attach(sys.modules[__name__])
    log.info("loaded the local add-on")
    return addon


local_addon = load_local_addon()


if __name__ == "__main__":
    init_storage(load_config())
    notify.listeners.append(record_announcement)
    threading.Thread(target=lectures.worker, args=(complete_text, notify.send), name="lectures", daemon=True).start()
    threading.Thread(target=worker_loop, name="worker", daemon=True).start()
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    log.info("brain listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
