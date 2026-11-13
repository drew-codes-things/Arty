"""The assistant's brain: an OpenAI-compatible endpoint that adds memory, instructions and tools.

Clients (Open WebUI, or your own apps with the Arty API key) send ordinary chat
completions. Each request gets the system prompt and the user's saved memories, and the
model's tool calls are executed through the MCP hub until it produces a final answer.

It also runs background tasks (one at a time, pausing for approval or help when
needed), a scheduler that turns schedules into tasks, and a small REST API over all of it.
See docs/API.md.
"""
import importlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import calendar_feed
import connections
import desktop_look
import devices
import drafts
import email_watch
import greetings
import keepalive
import lectures
import media
import model_choice
import models
import notify
import personal
import phone
import policy
import quick
import remote_sessions
import research
import safety
import sharing
import store
import style
import web_app

from announcements import record_announcement, wait_for_announcements
from api_support import (IDENTITY_NONCE, optional_int, ApiError, same_secret, identity_proofs, text_field, list_limit,
    valid_messages, client_name)
from archive import is_overheard, message_text, archive_records, recall_records, archive_turn
from environment import BRAIN_KEY, APP_KEY, HUB_KEY
from settings import LISTEN_HOST, LISTEN_PORT, HUB_URL, OLLAMA_URL, TRUSTED_PROXIES, STARFIRE_URL
from native_tools import NATIVE_TOOLS, API_CALLABLE_NATIVE
from reply_rules import (OFFER_SENTENCE, trim_offers, ACTION_REQUEST, REMIND_REQUEST, DO_REQUEST, NOT_FOR_ME,
    VOICE_MODE, user_words, APP_NOTES)
from schedules import create_schedule, ensure_reminder
from steering import matches_lookup, parse_arguments
from connections import complete_text, http_json, load_config
from replies import tidy_answer, as_written, never_an_offer
from memory_facts import (remember_reply, forget_reply, restore_memory_reply, extract_memories, SECRET_PATTERN,
    NOT_A_FACT, REMEMBER_REQUEST, as_fact, draft_facts)
from model_choice import (MODEL_ID, OLLAMA_DOWN, VISION_KEEP_ALIVE, VISION_MAIN_RETURN_SECONDS, chosen_model,
    session_keep_alive, use_requested_model, arty_description, chat_model, main_model_return)
from native_calls import NATIVE_TOOL_ERRORS, look_up_first, declined_reply, run_native, convert_attachment
from attachments import vision_model, expand_attachments, has_new_picture, to_ollama_messages
from agent_loop import know_the_page, private_upload, turn_rounds, WindowOverflow, AgentRun, run_agent, ROUNDS_KEY
from honesty import client_tool_runs, empty_reply, be_honest
from shortcuts import (addressed_to_arty, TALKED_ABOUT, recap_conversations, phone_action, asks_for_something_else,
    quick_action, judge_answer, researched_answer, solo_turn)
from agent_turns import agent_turn
from scheduler import gather_writing, scheduler_loop
from background_tasks import worker_loop, decide_task, public_task
from alerts import record_alert
from api_keys import issue_device_key, make_device_key, is_main_key, is_device_key, remote_caller


__all__ = ["APP_NOTES", "AgentRun", "NOT_A_FACT", "OFFER_SENTENCE", "OLLAMA_URL", "REMEMBER_REQUEST", "ROUNDS_KEY",
           "SECRET_PATTERN", "archive_turn", "as_fact", "as_written", "expand_attachments", "extract_memories",
           "http_json", "load_config", "log", "main_model_return", "message_text", "models", "never_an_offer",
           "personal", "policy", "run_agent", "tidy_answer", "to_ollama_messages"]

ALERT_TOKEN = os.environ.get("ALERT_TOKEN", "")  # Uptime Kuma's webhook puts this in its URL
MAX_BODY_BYTES = 64 * 1024 * 1024  # chat requests can carry full-size photos as base64
UNKEYED_BODY_BYTES = 4096
TASK_ACTIONS = ("done", "approve", "deny", "cancel")
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


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain")


SENDS_FILES = {"convert_file", *sharing.TOOLS}
DOWNLOAD_LINK = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+/" + media.DOWNLOAD_PREFIX + r"/[^)\s]+)\)")


# A lookup that found nothing ("Nothing in the calendar for the next 7 days.") is the whole answer.
EMPTY_RESULT = re.compile(r"^(nothing|no (deadlines|events|items|unread|cards)|the \w+ list is empty|you don't have any)", re.I)
HASHTAG_LINE = re.compile(r"\n\s*(#\w+\s*){2,}\s*$")


def look_keep_alive(config, on_other_model):
    turn_runs_on_the_looker = on_other_model and config["model"] == config.get("vision_model")
    return config.get("keep_alive", 0) if turn_runs_on_the_looker else 0


def facts_for_draft(config, said):
    return "" if "own_memories" in config else draft_facts(said)


def run_turn(messages, requested_model="", client_tools=None, skip_groups=(), on_text=None, client="", any_installed=False,
             agent=False, on_thinking=None):
    """One chat turn. Returns (answer, tools_used, raw, client_calls); client_calls is set when the
    model wants the calling app to run one of its own tools, and answer is then whatever text came with it.
    on_text gets the answer's first sentences early (see drafts.py); the answer always starts with them.
    client names the calling app when it says ("phone": the Arty phone app, which can open calls and texts)."""
    if solo := models.solo_choice(requested_model):
        return solo_turn(solo["name"], messages, client)
    if local_addon and (handled := local_addon.turn(messages, requested_model, client_tools, skip_groups, on_text)) is not None:
        return handled
    if agent:
        return agent_turn(messages, requested_model, client_tools, on_text, any_installed, on_thinking, client)
    config = connections.load_config()
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    said = user_words(message_text(last_user), client) if last_user is not None else ""
    turn_model = chat_model(config, requested_model, said, has_new_picture(messages))
    use_requested_model(config, turn_model, any_installed)
    on_routed_model = turn_model != requested_model
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
        if not quick.refers_back(said) and (done := quick_action(said, after_a_question=greetings.answers_a_question(messages))):
            reply, tool = done
            archive_turn(config, said, reply, [tool])
            return reply, [tool], {}, None
        if email_watch.is_request(said):
            # Code reads the list and the model only sorts and sums up: a failed check is reported, never "nothing".
            connections.hub.refresh()
            reply = email_watch.answer(connections.hub.call, complete_text)
            archive_turn(config, said, reply, ["workspace__gmail_search"])
            return reply, ["workspace__gmail_search"], {}, None
        if email_watch.is_unread_request(said):
            connections.hub.refresh()
            reply = email_watch.unread(connections.hub.call)
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
            connections.hub.refresh()
            reply = desktop_look.customise(connections.hub.call, complete_text, desktop_look.persona_of(config.get("desktop_look")))
            archive_turn(config, said, reply, ["workspace__run_command"])
            return reply, ["workspace__run_command"], {}, None
        if style.is_learn_request(said):
            reply = style.refresh(gather_writing, complete_text)
            archive_turn(config, said, reply, ["learn_style"])
            return reply, ["learn_style"], {}, None
        if not on_routed_model and style.is_new_draft(said) and \
                (reply := style.draft(said, complete_text, config.get("user_name", ""), known=facts_for_draft(config, said))) is not None:
            archive_turn(config, said, reply, ["draft"])
            return reply, ["draft"], {}, None
        if (not on_routed_model and research.is_quick_question(said) and not research.is_request(said)
                and not matches_lookup(config, said)):
            # Recent facts, "best ...", "how do I make ...": read the top pages (no browser) and answer with sources.
            connections.hub.refresh()
            answer, used = researched_answer(said, quick=True)
            answer = tidy_answer(answer)
            archive_turn(config, said, answer, used)
            return answer, used, {}, None
        if research.is_request(said):
            # "Use your desktop to find out ...": fixed steps (search, read pages, note, write), see research.py.
            connections.hub.refresh()
            answer, used = researched_answer(said, quick=False)
            answer = tidy_answer(answer)
            archive_turn(config, said, answer, used)
            return answer, used, {}, None
    messages, attached = expand_attachments(messages, look_keep_alive(config, on_routed_model))
    conversation = to_ollama_messages([m for m in messages if m.get("role") != "system"])
    client_system = "\n".join(message_text(m) for m in messages if m.get("role") == "system").strip()
    # With an attachment in the chat, the conversion tool is always on offer, however few tools the model gets.
    state = {"messages": conversation, "context": {}, "groups": ["files"] if attached else []}
    # A "remind me" answer may be replaced whole by ensure_reminder, so it isn't sent early.
    users = [m for m in conversation if m.get("role") == "user"]
    draft = drafts.Draft(on_text, tidy_answer, OFFER_SENTENCE.search) \
        if on_text and not (users and REMIND_REQUEST.search(message_text(users[-1]))) else None
    connections.hub.refresh()
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
    outcome = run_agent(config, state, run=AgentRun(chat_approved=policy.approved_reason(conversation, message_text),
                                                    client_system=client_system, client_tools=client_tools,
                                                    skip_groups=skip_groups, draft=draft, looked_up=looked_up))
    if outcome["status"] == "client_tools":
        content = draft.finish(outcome["content"]) if draft else outcome["content"]
        return content, outcome["tools_used"], outcome["raw"], outcome["calls"]
    if outcome["status"] == "waiting":  # can't happen in chat mode, but never lose the turn
        outcome = {"answer": outcome["waiting"]["message"], "tools_used": outcome["tools_used"], "raw": outcome["raw"]}
    answer = tidy_answer(trim_offers(outcome["answer"]))
    fallback = ""
    if not answer:
        log.warning("reply was empty after tidying; the model wrote: %r", (outcome["answer"] or "")[:300])
        answer = fallback = empty_reply(conversation)
    reminded = ensure_reminder(conversation, outcome["tools_used"], answer)
    if reminded != answer and reminded.startswith("Reminder set"):
        outcome["tools_used"].append("schedule_task")  # set here from the user's words
    answer = reminded
    answer = HASHTAG_LINE.sub("", answer)  # hashtags belong in drafted posts, not in answers
    client_used, client_failed = client_tool_runs(conversation)
    if answer != fallback:
        answer = be_honest(answer, client_used + outcome["tools_used"], client_failed + state.get("context", {}).get("failed", []),
                           draft.everything() if draft else "", said if last_user is not None else "", config["model"],
                           config.get("keep_alive"), state.get("prefix"))
    if draft:
        answer = draft.finish(answer)
    # Small models sometimes describe the conversion but drop the link; never lose a file the user asked for.
    conversions = [message_text(m) for m in conversation if m.get("role") == "tool" and m.get("tool_name") in SENDS_FILES]
    for result in conversions + ([converted] if converted else []):
        for link in DOWNLOAD_LINK.finditer(result):
            if link.group(1) not in answer:
                answer += f"\n\n{link.group(0)}"
    user_messages = [m for m in conversation if m.get("role") == "user"]
    user_text = user_words(message_text(user_messages[-1]), client) if user_messages else ""
    if is_overheard(answer):
        log.info("voice heard something not meant for Arty; not saving it")
        return NOT_FOR_ME, outcome["tools_used"], outcome["raw"], None
    if user_text:
        archive_turn(config, user_text, answer, outcome["tools_used"])
        if config.get("auto_memory", True):
            threading.Thread(target=extract_memories, args=(config, user_text, answer), kwargs={"prefix": state.get("prefix")},
                             daemon=True).start()
    return answer, outcome["tools_used"], outcome["raw"], None


# ---------- HTTP API ----------


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
            config = connections.load_config()
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
                config = connections.load_config()
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
            connections.hub.refresh()
            config = connections.load_config()
            groups = config.get("tool_groups", {})
            listed = []
            for tool in connections.hub.tools + [NATIVE_TOOLS[name] for name in sorted(API_CALLABLE_NATIVE)]:
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
            return self.send_json(200, connections.http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20) or {})
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
            config = connections.load_config()
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
                                     urgent=bool(body.get("urgent")), category=notify.SERVER)
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
            if declined := declined_reply(connections.load_config(), title, instructions):
                raise ApiError(400, declined)
            created = store.create_task(title, instructions, "api")
            return self.send_json(201, public_task(created))
        if len(segments) == 4 and segments[:2] == ["v1", "tasks"] and segments[2].isdigit() and segments[3] in TASK_ACTIONS:
            status, message = decide_task(int(segments[2]), segments[3], str(request.get("message", "")))
            return self.send_json(status, {"message": message})
        if segments == ["v1", "schedules"]:
            created, message = create_schedule(request, connections.load_config().get("crisis_help"))
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
            return self.send_json(200, {"result": notify.send(str(request.get("title") or "Arty"), str(request.get("message", "")),
                                                              category=notify.known_category(request.get("category")))})
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
                result, _ = run_native(name, arguments, connections.load_config(), False, None)
            except NATIVE_TOOL_ERRORS as error:
                raise ApiError(400, f"{name} couldn't use those arguments ({type(error).__name__})") from None
            return self.send_json(200, {"result": result})
        connections.hub.refresh()
        if name not in connections.hub.routes:
            raise ApiError(404, f"unknown tool {name}")
        if refused := private_upload(name, arguments):
            raise ApiError(403, refused)
        context: dict = {}
        know_the_page(name, context)
        reason = policy.approval_reason(name, arguments, context, connections.load_config().get("approval", {}))
        if reason and not request.get("approved"):
            return self.send_json(409, {"error": {"message": f"needs approval: {reason}", "type": "needs_approval", "reason": reason}})
        return self.send_json(200, {"result": connections.hub.call(name, arguments)})

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
        if meta.get("kind") == "image":
            model_choice.warm_picture_model(connections.load_config())
        self.send_json(201, media.public(meta))

    def vision(self, request, messages):
        """Messages carrying images (e.g. a desktop screenshot) go straight to a vision model: no persona,
        tools or archive, since the caller asks a precise question about a picture."""
        max_tokens = optional_int(request.get("max_tokens")) if request.get("max_tokens") else None
        if request.get("max_tokens") and max_tokens is None:
            raise ApiError(400, "max_tokens must be a positive whole number")
        config = connections.load_config()
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
            raw = connections.http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
                "model": model["name"], "messages": converted, "stream": False, "options": options,
                "keep_alive": session_keep_alive(model["name"], VISION_KEEP_ALIVE)}))
        finally:
            model_choice.main_model_return.schedule(config, VISION_MAIN_RETURN_SECONDS)
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
    init_storage(connections.load_config())
    notify.listeners.append(record_announcement)
    threading.Thread(target=lectures.worker, args=(complete_text, notify.send), name="lectures", daemon=True).start()
    threading.Thread(target=worker_loop, name="worker", daemon=True).start()
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    log.info("brain listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
