"""Arty Desktop's local service: the voice pipeline and hands on this computer.

    python server/arty_server.py

The desktop app talks to this on 127.0.0.1. Thinking, persona and memory are Arty's
brain on the homelab (process.brain); this service turns speech into text (Whisper),
replies into speech (Kokoro), and runs the brain's requests on this computer.
Replies stream as NDJSON, one spoken sentence per event, so speech starts early.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import queue
import signal
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from process import brain
from process import browser
from process import call as phone_call
from process import config as cfg
from process import confirm
from process import connection
from process.notice import Noticer
from process import notifications
from process import phone_setup
from process.asr_func.asr_push_to_talk import build_model, transcribe_audio, transcribe_file
from process.asr_func.listen import Ear, wake_match
from process.asr_func.recorder import Recorder, SAMPLERATE
from process.asr_func.vad import VAD
from process.config import load_config
from process.text_func.speech import SentenceSplitter, split_reply
from process.tools import computer
from process.tools import context
from process.tools import idle
from process.tts_func.engine import TTSError, describe as describe_tts, synthesize
from process.tts_func.engine import warmup as warmup_tts

config = load_config()
app = FastAPI(title="Arty Desktop")

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
TOKEN = secrets.token_urlsafe(32)
TOKEN_FILE = cfg.DATA_ROOT / ".arty_token"
EXTENSION_ID = "lepggpdmafbgbgfgacpglpjelmiofkli"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
FIREFOX_EXTENSION_ORIGIN = r"moz-extension://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
EXTENSION_ORIGINS = re.compile(rf"{re.escape(EXTENSION_ORIGIN)}|{FIREFOX_EXTENSION_ORIGIN}")
EXTENSION_ROUTES = {("POST", "/browser/next"), ("POST", "/browser/result")}
EXTENSION_HELLO_ROUTE = ("POST", "/browser/hello")
OPEN_ROUTES = {("GET", "/whoami")}
PAIRING_FILE = cfg.DATA_ROOT / ".arty_browser_pairing"
PAIRING_CODE = re.compile(r"[0-9a-f]{32}")
PAIRING_BYTES = 16
_pairing = {}
_pairing_lock = threading.Lock()
SERVICE_PROOF_CONTEXT = "arty-service:"
BROWSER_PROOF_CONTEXT = "arty-browser-service:"
NONCE = re.compile(r"[0-9a-f]{32}")


def _write_token():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_private(TOKEN_FILE, TOKEN)


def _has_token(request):
    return hmac.compare_digest(request.headers.get("x-arty-token", "").encode(), TOKEN.encode())


def pairing_code():
    with _pairing_lock:
        if "code" not in _pairing:
            _pairing["code"] = _stored_pairing_code() or _new_pairing_code()
        return _pairing["code"]


def _stored_pairing_code():
    try:
        stored = PAIRING_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    return stored if PAIRING_CODE.fullmatch(stored) else ""


def _new_pairing_code():
    code = secrets.token_hex(PAIRING_BYTES)
    PAIRING_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_private(PAIRING_FILE, code)
    return code


def _is_arty_extension(request):
    is_extension_route = (request.method, request.url.path) in EXTENSION_ROUTES
    if not is_extension_route or not EXTENSION_ORIGINS.fullmatch(request.headers.get("origin") or ""):
        return False
    return hmac.compare_digest(request.headers.get("x-arty-pairing", "").encode(), pairing_code().encode())


def _is_extension_saying_hello(request):
    return (request.method, request.url.path) == EXTENSION_HELLO_ROUTE and bool(EXTENSION_ORIGINS.fullmatch(request.headers.get("origin") or ""))


def _is_from_a_web_page(request):
    return "origin" in request.headers or "sec-fetch-site" in request.headers


@app.middleware("http")
async def only_local_callers(request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin") or ""
    is_open_route = (request.method, request.url.path) in OPEN_ROUTES and not _is_from_a_web_page(request)
    known_caller = is_open_route or _has_token(request) or _is_arty_extension(request) or _is_extension_saying_hello(request)
    if request.method == "OPTIONS" or (host in ALLOWED_HOSTS and known_caller):
        return await call_next(request)
    fetch_from = "/".join(request.headers.get(h, "-") for h in ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest"))
    print(f"[security] refused {request.method} {request.url.path} (origin {origin or 'none'}, host {host or 'none'}, fetch {fetch_from})", flush=True)
    return JSONResponse({"error": "not allowed"}, status_code=403)


app.add_middleware(CORSMiddleware, allow_origin_regex=rf"^(null|{EXTENSION_ORIGINS.pattern})$", allow_methods=["*"],
                   allow_headers=["Content-Type", "X-Arty-Token", "X-Arty-Pairing"])

_listen_cfg = config.get("listen") or {}
LISTEN_SECONDS = float(_listen_cfg.get("max_seconds", 15))
PAUSE_MS = int(_listen_cfg.get("pause_ms", 900))
_wake_cfg = config.get("wake") or {}
WAKE_WORDS = [str(w) for w in (_wake_cfg.get("words") or ["arty"])]
WAKE_WAIT = float(_wake_cfg.get("listen_seconds", LISTEN_SECONDS))
# Whisper spells names it hasn't been told about however it likes.
WAKE_PROMPT = ", ".join(w.capitalize() for w in WAKE_WORDS[:1]) + "."
_barge_cfg = config.get("barge_in") or {}

# Screen awareness: the hotkey asks about whatever is in front of the user.
# In <screen> tags so the brain can keep the description out of its long-term history.
LOOK_NOTE = ("<screen>This is about what's on my screen right now. Here is what it shows: {seen} "
             "Answer from that. If there's an error, say what it means and how to fix it.</screen>")
LOOK_DEFAULT = "What's on my screen? If there's an error or a problem, explain it and how to fix it."
MEDIA_CHUNK_BYTES = 64 * 1024
REPLY_FAILED = "Something went wrong on this computer partway through that reply. Try again, and check service.log if it keeps happening."

_whisper = None
_whisper_lock = threading.Lock()
recorder = Recorder()
call = phone_call.Call()
_ears = {"button": None}
_stream = {"id": 0, "stop_at": None}
_stream_lock = threading.Lock()


def whisper():
    """Loads Whisper on first use (a few seconds)."""
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            print("Loading Faster-Whisper...", flush=True)
            _whisper = build_model()
            print("Faster-Whisper ready.", flush=True)
    return _whisper


def _ndjson(event):
    return json.dumps(event, ensure_ascii=False) + "\n"


# ---------- replies ----------

class ChatIn(BaseModel):
    text: str
    speak: bool = True
    look: bool = False  # answer about what's on the screen


def _speak_segment(segment, index, speak):
    parts = split_reply(segment)
    event = {"type": "chunk", "index": index, "reply": parts["display"], "speech": parts["speech"], "audio": None,
             "quoted": parts["quoted"]}
    if speak and parts["speech"]:
        try:
            wav, _visemes = synthesize(parts["speech"])
            event["audio"] = base64.b64encode(wav).decode("ascii")
        except TTSError as error:
            event["error"] = str(error)
            print(f"[tts] {error}", flush=True)
    return event


def _stream_reply(user_text, speak=True, look=False, note=""):
    """Streams the reply a sentence at a time, synthesising as it goes. An /interrupt
    stops it, and only what was actually heard is kept in the history."""
    with _stream_lock:
        _stream["id"] += 1
        _stream["stop_at"] = None
        stream_id = _stream["id"]

    def stop_at():
        with _stream_lock:
            return index if _stream["id"] != stream_id else _stream["stop_at"]

    yield _ndjson({"type": "start", "id": stream_id, "transcript": user_text})
    splitter = SentenceSplitter()
    spoken = []
    shown = []  # everything shown, spoken or not (a draft to copy is only shown)
    actions = []
    index = 0
    interrupted = False
    if look:
        # Look first, every time: small models often answer from the window title instead.
        yield _ndjson({"type": "activity", "id": None, "where": "computer", "text": "looking at your screen"})
        seen = computer.computer_look(user_text)
        print(f"[look] {len(seen)} chars" if not seen.startswith("Looking failed") else f"[look] {seen[:200]}", flush=True)
        note = LOOK_NOTE.format(seen=seen) + note
    generator = brain.llm_stream(user_text, note=call.note_for(note))

    def emit(segment):
        nonlocal index
        event = _speak_segment(segment, index, speak)
        if not event["reply"]:
            return None
        shown.append(segment)
        if not event["speech"]:
            return _ndjson(event)  # text only: not a spoken chunk, so not counted for interruptions
        spoken.append(segment)
        index += 1
        return _ndjson(event)

    try:
        try:
            for delta in generator:
                if stop_at() is not None:
                    interrupted = True
                    break
                if isinstance(delta, brain.Activity):
                    if not delta.confirm and delta.label:
                        actions.append(delta.label)
                    yield _ndjson({"type": "confirm" if delta.confirm else "activity", "id": delta.confirm,
                                   "where": delta.where, "text": delta.label})
                    continue
                for segment in splitter.feed(delta):
                    event = emit(segment)
                    if event:
                        yield event
            if not interrupted:
                tail = splitter.flush()
                if tail and (event := emit(tail)):
                    yield event
        finally:
            generator.close()
    except brain.BrainError as error:
        print(f"[brain] {error}", flush=True)
        brain.save_turn(user_text, " ".join(spoken), actions=actions)
        yield _ndjson({"type": "error", "message": str(error)})
        return
    except Exception as error:
        print(f"[reply] {type(error).__name__}: {error}", flush=True)
        brain.save_turn(user_text, " ".join(spoken), actions=actions)
        yield _ndjson({"type": "error", "message": REPLY_FAILED})
        return

    limit = stop_at()
    if limit is not None:
        interrupted = True
        heard = " ".join(spoken[:max(0, limit)]).rstrip()
        heard = f"{heard}..." if heard else ""
    else:
        heard = " ".join(shown)
    brain.save_turn(user_text, heard, actions=actions)
    yield _ndjson({"type": "interrupted" if interrupted else "done", "chunks": index, "reply": heard})


@app.post("/chat/stream")
def chat_stream(body: ChatIn):
    text = body.text.strip()
    if not text:
        return StreamingResponse(iter([_ndjson({"type": "error", "message": "Empty message."})]), media_type="application/x-ndjson")
    return StreamingResponse(_stream_reply(text, body.speak, body.look), media_type="application/x-ndjson")


class CallIn(BaseModel):
    speak: bool = True


@app.post("/call/start")
def call_start(body: CallIn):
    call.start()
    print("[call] picked up", flush=True)
    greeting = _stream_reply(phone_call.PICK_UP_SAID, body.speak, note=phone_call.PICK_UP)
    return StreamingResponse(greeting, media_type="application/x-ndjson")


@app.post("/call/end")
def call_end():
    ended = call.end()
    print(f"[call] hung up after {ended['seconds']}s", flush=True)
    return ended


@app.get("/call")
def call_status():
    return call.status()


NOTICE_CHECK_SECONDS = 30
MAX_FAILED_READINGS_IN_A_ROW = 10
NOTICE_SLOW_CHECK_SECONDS = 300
_notices = {"id": 0, "pending": None}
_notices_lock = threading.Lock()


def offer_notice(notice):
    with _notices_lock:
        _notices["id"] += 1
        _notices["pending"] = (_notices["id"], notice)
        notice_id = _notices["id"]
    try:
        notifications.record_notice(notice.said(), notice_id)
    except Exception:
        with _notices_lock:
            if _notices["pending"] and _notices["pending"][0] == notice_id:
                _notices["pending"] = None
        raise
    return notice_id


def take_notice(notice_id):
    with _notices_lock:
        pending = _notices["pending"]
        if not pending or pending[0] != notice_id:
            return None
        _notices["pending"] = None
        return pending[1]


def watch_for_notices(watcher, read_idle=idle.seconds_idle, read_app=context.frontmost_app, sleep=time.sleep):
    failed_in_a_row = 0
    while True:
        try:
            seconds = read_idle()
            if seconds is None:
                print("[notice] this desktop doesn't say how long it has been idle, so Arty won't notice comings and goings", flush=True)
                return
            notice = watcher.observe(seconds, read_app(), holding_back=call.is_on())
            if notice:
                try:
                    offer_notice(notice)
                except Exception:
                    watcher.retract(notice)
                    raise
            failed_in_a_row = 0
        except Exception as error:
            failed_in_a_row += 1
            if failed_in_a_row == MAX_FAILED_READINGS_IN_A_ROW:
                print(f"[notice] the notice check failed {failed_in_a_row} times in a row "
                      f"({type(error).__name__}: {error}); slowing down to one check every "
                      f"{NOTICE_SLOW_CHECK_SECONDS // 60} minutes", flush=True)
            elif failed_in_a_row < MAX_FAILED_READINGS_IN_A_ROW:
                print(f"[notice] the notice check failed ({type(error).__name__}: {error}); trying again", flush=True)
        is_struggling = failed_in_a_row >= MAX_FAILED_READINGS_IN_A_ROW
        sleep(NOTICE_SLOW_CHECK_SECONDS if is_struggling else NOTICE_CHECK_SECONDS)


def start_noticing():
    if not bool(cfg.setting("notice", "enabled", False)):
        return
    watcher = Noticer(away_minutes=float(cfg.setting("notice", "away_minutes", 20)),
                      focus_minutes=float(cfg.setting("notice", "focus_minutes", 120)))
    threading.Thread(target=watch_for_notices, args=(watcher,), name="notice", daemon=True).start()
    print("[notice] noticing when you come back or spend hours in one app", flush=True)


class NoticeReplyIn(BaseModel):
    id: int
    speak: bool = True


@app.post("/notice/reply")
def notice_reply(body: NoticeReplyIn):
    notice = take_notice(body.id)
    if notice is None:
        return StreamingResponse(iter([_ndjson({"type": "cancelled"})]), media_type="application/x-ndjson")
    return StreamingResponse(_stream_reply(notice.said(), body.speak, note=notice.prompt()), media_type="application/x-ndjson")


class InterruptIn(BaseModel):
    chunks: int = 0


@app.post("/interrupt")
def interrupt(body: InterruptIn):
    """Stops the current reply; only the first `chunks` sentences count as heard."""
    with _stream_lock:
        _stream["stop_at"] = max(0, body.chunks)
    confirm.cancel_all()  # stopping Arty is a no to anything it was asking
    return {"ok": True}


class ConfirmIn(BaseModel):
    id: str
    ok: bool


@app.post("/confirm")
def confirm_answer(body: ConfirmIn):
    """The user's answer to something Arty asked before doing it on this computer."""
    print(f"[computer] they {'allowed' if body.ok else 'refused'} it", flush=True)
    return {"ok": confirm.answer(body.id, body.ok)}


# ---------- listening ----------

def _reply_to(transcript, look=False):
    yield _ndjson({"type": "transcript", "text": transcript})
    yield from _stream_reply(transcript, True, look)


def _auto_stream(max_seconds, look=False):
    try:
        ear = Ear(max_seconds=max_seconds, pause_ms=PAUSE_MS).open()
    except Exception as error:
        yield _ndjson({"type": "error", "message": f"Can't open the microphone: {error}"})
        return
    _ears["button"] = ear
    audio = None
    try:
        yield _ndjson({"type": "armed", "max_seconds": max_seconds})
        while audio is None:
            try:
                event = ear.events.get(timeout=0.25)
            except queue.Empty:
                yield _ndjson({"type": "waiting"})
                continue
            if event[0] == "start":
                yield _ndjson({"type": "speech"})
            elif event[0] == "silence":
                if look:  # nothing said: just explain what's on the screen
                    yield from _reply_to(LOOK_DEFAULT, look=True)
                else:
                    yield _ndjson({"type": "cancelled", "message": "Didn't hear anything."})
                return
            elif event[0] == "phrase":
                audio = event[1]
    finally:
        ear.close()
        if _ears["button"] is ear:
            _ears["button"] = None
    yield _ndjson({"type": "transcribing"})
    transcript = transcribe_audio(whisper(), audio)
    if not transcript and look:
        transcript = LOOK_DEFAULT
    if not transcript:
        yield _ndjson({"type": "cancelled", "message": "Didn't catch that."})
        return
    yield from _reply_to(transcript, look)


@app.get("/listen/auto")
def listen_auto(max_seconds: float = 0, look: bool = False):
    """Listens until you pause (or /listen/finish), then replies. For the mic button; with look,
    the answer is about what's on the screen (and silence asks what's there)."""
    threading.Thread(target=whisper, daemon=True).start()
    return StreamingResponse(_auto_stream(min(max_seconds or LISTEN_SECONDS, 60.0), look), media_type="application/x-ndjson")


@app.post("/listen/finish")
def listen_finish():
    ear = _ears["button"]
    if ear is not None:
        ear.finish()
    return {"ok": True, "listening": ear is not None}


def _wake_stream(timeout):
    vad = VAD(SAMPLERATE, margin_db=float(_wake_cfg.get("margin_db", 10.0)),
              speech_ms=int(_wake_cfg.get("speech_ms", 200)), silence_ms=int(_wake_cfg.get("silence_ms", 600)))
    try:
        ear = Ear(vad, max_seconds=max(WAKE_WAIT, 6.0)).open()
    except Exception as error:
        yield _ndjson({"type": "error", "message": f"Can't open the microphone: {error}"})
        return
    request = None
    woke_at = None
    deadline = time.time() + timeout
    try:
        yield _ndjson({"type": "armed"})
        while request is None:
            now = time.time()
            if woke_at is None and now > deadline:
                yield _ndjson({"type": "idle"})
                return
            if woke_at is not None and now > woke_at + WAKE_WAIT:
                yield _ndjson({"type": "cancelled"})
                return
            try:
                event = ear.events.get(timeout=0.25)
            except queue.Empty:
                yield _ndjson({"type": "waiting"})
                continue
            if event[0] == "start":
                if woke_at is not None:
                    yield _ndjson({"type": "speech"})
                continue
            if event[0] != "phrase":
                continue
            # Only the wake word and what follows it is kept; everything else the mic
            # hears is transcribed and thrown away.
            text = transcribe_audio(whisper(), event[1], prompt=WAKE_PROMPT)
            if woke_at is not None:
                if text:
                    request = text
                continue
            rest = wake_match(text, WAKE_WORDS)
            if rest is None:
                continue
            if rest:
                request = rest
            else:
                woke_at = time.time()
                yield _ndjson({"type": "wake", "listen_seconds": WAKE_WAIT})
    finally:
        ear.close()
    yield from _reply_to(request)


@app.get("/wake/listen")
def wake_listen(timeout: float = 120.0):
    """Long poll for "Arty, ...". Said alone, it waits `wake.listen_seconds` for the rest."""
    threading.Thread(target=whisper, daemon=True).start()
    return StreamingResponse(_wake_stream(min(timeout, 600.0)), media_type="application/x-ndjson")


def _barge_stream(timeout):
    """Listens while Arty talks; if the user cuts in, transcribes it and replies on the same stream."""
    if recorder.is_monitoring:
        recorder.cancel()
    vad = VAD(SAMPLERATE, margin_db=float(_barge_cfg.get("margin_db", 12.0)),
              speech_ms=int(_barge_cfg.get("speech_ms", 220)), silence_ms=int(_barge_cfg.get("silence_ms", 800)))
    if not recorder.start(vad):
        yield _ndjson({"type": "error", "message": "Microphone already in use."})
        return
    yield _ndjson({"type": "armed"})
    deadline = time.time() + timeout
    heard = False
    try:
        while True:
            if time.time() > deadline or not recorder.is_monitoring:
                return
            try:
                event = recorder.events.get(timeout=0.25)
            except queue.Empty:
                yield _ndjson({"type": "waiting"})
                continue
            if event == "start" and not heard:
                heard = True
                yield _ndjson({"type": "speech"})
                deadline = time.time() + 60
            elif event == "end" and heard:
                break
        path = recorder.stop(from_onset=True)
        if path is None:
            yield _ndjson({"type": "cancelled"})
            return
        try:
            transcript = transcribe_file(whisper(), path)
        finally:
            path.unlink(missing_ok=True)
        if not transcript:
            yield _ndjson({"type": "cancelled"})
            return
        yield from _reply_to(transcript)
    finally:
        if recorder.is_monitoring:
            recorder.cancel()


@app.get("/barge/listen")
def barge_listen(timeout: float = 45.0):
    if not bool(cfg.setting("barge_in", "enabled", True)):
        return StreamingResponse(iter([_ndjson({"type": "disabled"})]), media_type="application/x-ndjson")
    threading.Thread(target=whisper, daemon=True).start()
    return StreamingResponse(_barge_stream(timeout), media_type="application/x-ndjson")


@app.post("/barge/stop")
def barge_stop():
    stopped = recorder.is_monitoring
    if stopped:
        recorder.cancel()
    return {"ok": True, "stopped": stopped}


# ---------- status and settings ----------

class ComputerIn(BaseModel):
    enabled: bool


@app.get("/computer")
def computer_get():
    return computer.status()


@app.post("/computer")
def computer_set(body: ComputerIn):
    """Lets Arty use this computer, or stops it. Saved to the config."""
    cfg.set_value("computer", "enabled", body.enabled)
    return computer.status()


@app.get("/events")
def events(after: int = 0, timeout: float = 55.0):
    """Long poll for notifications from other apps since `after` (the app can read them aloud)."""
    fresh, last = notifications.since(after, min(timeout, 120.0))
    return {"notifications": fresh, "last": last}


@app.get("/updates")
def updates():
    """Everything Arty has sent (reminders, briefings, results, approvals, alerts) for the Updates view."""
    try:
        items, waiting = brain.updates()
    except brain.BrainError as error:
        return JSONResponse({"updates": [], "waiting": [], "error": str(error)}, status_code=502)
    return {"updates": items, "waiting": waiting}


TASK_ACTIONS = ("approve", "deny", "done", "cancel")


@app.post("/task/{task_id}/{action}")
def task_decide(task_id: int, action: str):
    """Approve / Deny / Done / Cancel on one of Arty's paused background tasks."""
    if action not in TASK_ACTIONS:
        return JSONResponse({"error": f"action must be one of {', '.join(TASK_ACTIONS)}"}, status_code=400)
    try:
        return {"message": brain.decide_task(task_id, action)}
    except brain.BrainError as error:
        return JSONResponse({"error": str(error)}, status_code=502)


class SayIn(BaseModel):
    text: str


@app.post("/say")
def say(body: SayIn):
    """Speech for a line the app wants to say itself, e.g. a notification."""
    text = body.text.strip()[:300]
    if not text:
        return {"audio": None}
    try:
        wav, _visemes = synthesize(text)
    except TTSError as error:
        return {"audio": None, "error": str(error)}
    return {"audio": base64.b64encode(wav).decode("ascii")}


@app.post("/browser/next")
def browser_next(timeout: float = 25.0, browser_id: str = browser.DEFAULT_BROWSER):
    """For the Chrome extension: the next thing to do in the browser, or {} after `timeout`."""
    return browser.next_command(min(timeout, 55.0), browser_id) or {}


@app.post("/browser/hello")
def browser_hello(nonce: str = ""):
    if not NONCE.fullmatch(nonce):
        return JSONResponse({"error": "nonce must be 32 lowercase hex characters"}, status_code=400)
    return {"proof": hmac.new(pairing_code().encode(), f"{BROWSER_PROOF_CONTEXT}{nonce}".encode(), hashlib.sha256).hexdigest()}


class SiteIn(BaseModel):
    site: str


@app.get("/browser/pairing")
def browser_pairing():
    return {"code": pairing_code()}


@app.get("/browser/sites")
def browser_sites():
    return {"sites": browser.allowed_sites()}


@app.post("/browser/sites/forget")
def browser_forget_site(body: SiteIn):
    browser.forget_site(body.site)
    return {"sites": browser.allowed_sites()}


class BrowserResultIn(BaseModel):
    id: int
    result: dict


@app.post("/browser/result")
def browser_result(body: BrowserResultIn):
    browser.put_result(body.id, body.result)
    return {"ok": True}


class EventIn(BaseModel):
    title: str
    when: str


@app.get("/calendar")
def calendar(days: int = 30):
    try:
        status, body = brain.calendar_call("GET", f"?days={max(1, min(days, 366))}")
    except brain.BrainError as error:
        return JSONResponse({"events": [], "error": str(error)}, status_code=502)
    return JSONResponse(body, status_code=status)


@app.post("/calendar")
def calendar_add(event: EventIn):
    try:
        status, body = brain.calendar_call("POST", body={"title": event.title, "when": event.when})
    except brain.BrainError as error:
        return JSONResponse({"message": str(error)}, status_code=502)
    return JSONResponse(body, status_code=status)


@app.delete("/calendar/{event_id}")
def calendar_remove(event_id: int):
    try:
        status, body = brain.calendar_call("DELETE", f"/{event_id}")
    except brain.BrainError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
    return JSONResponse(body, status_code=status)


@app.get("/models")
def models():
    try:
        return {"models": brain.list_models(), "current": brain.current_model()}
    except brain.BrainError as error:
        return {"models": [], "current": brain.current_model(), "error": str(error)}


class ModelIn(BaseModel):
    id: str


@app.post("/model")
def model_set(body: ModelIn):
    """Switches the brain model this app uses, and remembers it in the config."""
    brain.set_model(body.id)
    cfg.set_value("brain", "model", body.id)
    return {"current": body.id}


@app.get("/media")
def media(link: str):
    try:
        response = brain.media(link)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except brain.BrainError as error:
        return JSONResponse({"error": str(error)}, status_code=502)
    return StreamingResponse(response.iter_content(MEDIA_CHUNK_BYTES), background=BackgroundTask(response.close),
                             media_type=response.headers.get("content-type", "application/octet-stream"))


class ConnectionIn(BaseModel):
    base_url: str
    api_key: str
    remote_url: str = ""
    access_client_id: str = ""
    access_client_secret: str = ""


def restart_service():
    os.kill(os.getpid(), signal.SIGTERM)


def _from_arty_window(request):
    return _has_token(request)


@app.get("/connection")
def connection_get():
    return {"configured": connection.is_configured(), **connection.addresses()}


@app.post("/connection")
def connection_set(body: ConnectionIn, request: Request):
    if not _from_arty_window(request):
        return JSONResponse({"error": "Only the Arty window can change which Arty this app talks to."}, status_code=403)
    try:
        entered = connection.cleaned(connection.Connection(**body.model_dump()))
        reached = connection.check(entered)
    except connection.ConnectionProblem as problem:
        return JSONResponse({"error": str(problem)}, status_code=400)
    connection.save(entered)
    return JSONResponse({"ok": True, "reached": reached}, background=BackgroundTask(restart_service))


@app.get("/phone-setup-code")
def phone_setup_code(request: Request):
    if not _from_arty_window(request):
        return JSONResponse({"error": "Only the Arty window can show the phone setup code."}, status_code=403)
    try:
        return phone_setup.phone_code()
    except phone_setup.NothingToShare as problem:
        return JSONResponse({"error": str(problem)}, status_code=400)


@app.get("/whoami")
def whoami(nonce: str = ""):
    if not NONCE.fullmatch(nonce):
        return JSONResponse({"error": "nonce must be 32 lowercase hex characters"}, status_code=400)
    return {"proof": hmac.new(TOKEN.encode(), f"{SERVICE_PROOF_CONTEXT}{nonce}".encode(), hashlib.sha256).hexdigest()}


@app.get("/health")
def health():
    return {"ok": True, "brain": brain.describe_endpoint(), "tts": describe_tts(),
            "whisper_loaded": _whisper is not None, "computer": computer.status(), "chrome": browser.connected()}


@app.get("/history")
def history():
    return {"messages": [m for m in brain.load_history() if m.get("role") in ("user", "assistant")]}


@app.post("/reset")
def reset():
    brain.reset_history()
    return {"ok": True}


@app.post("/warmup")
def warmup():
    whisper()
    warmup_tts()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    _write_token()
    notifications.start()
    brain.watch_announcements(notifications.record_announcement)
    start_noticing()
    bridge = config.get("bridge") or {}
    uvicorn.run(app, host=bridge.get("host", "127.0.0.1"), port=int(bridge.get("port", 8765)), log_level="warning")
