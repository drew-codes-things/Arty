import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import headset_commands
import headset_devices
import pairing
import music
import quick_answers
from arty_voice import BRAIN_KEY, BRAIN_URL, Assistant, Senses
from headset_panels import REFRESH_SECONDS, Dashboard

HEADSET_TOKEN = os.environ.get("HEADSET_TOKEN", "")
HEADSET_PORT = int(os.environ.get("HEADSET_PORT", "8790"))
HEADSET_HOST = os.environ.get("HEADSET_HOST", "0.0.0.0")
DEVICES_FILE = os.environ.get("HEADSET_DEVICES_FILE", "/var/lib/arty-voice/headset-devices.json")
LEGACY_PAIRING = os.environ.get("HEADSET_LEGACY_PAIRING", "1") != "0"
MIN_TOKEN_CHARS = 32
OWNER = "owner"
IDENTITY_LIMIT = 30
IDENTITY_WINDOW_SECONDS = 60
MAX_IDENTITY_ADDRESSES = 256
ACTION_TIMEOUT_SECONDS = 10
PANEL_RETRY_SECONDS = 15
SAMPLE_BYTES = 2
IDENTITY_NONCE = re.compile(r"[0-9a-fA-F]{16,128}")
IDENTITY_LABEL = "arty-identity:"
MAX_MESSAGE_BYTES = 1024 * 1024
NO_REPLY = "The headset didn't answer, so I'm not sure that worked."

log = logging.getLogger("arty-voice.headset")


class HeadsetOutput:

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws

    async def send(self, message: dict) -> None:
        if not self.ws.closed:
            await self.ws.send_json(message)

    async def play(self, wav: bytes) -> None:
        if not self.ws.closed:
            await self.ws.send_bytes(wav)

    async def state(self, name: str, text: str = "", seconds: float = 0) -> None:
        await self.send({"type": "state", "state": name, "text": text, "seconds": round(seconds, 1)})

    async def wake_changed(self, enabled: bool) -> None:
        await self.send({"type": "wake", "enabled": enabled})


class HeadsetSession:

    def __init__(self, senses: Senses, http: aiohttp.ClientSession, ws: web.WebSocketResponse):
        self.output = HeadsetOutput(ws)
        self.http = http
        self.assistant = Assistant(senses, self.output, http, bare_wake=True, act=self.act)
        self.collector = senses.new_collector()
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.clock = datetime.now

    async def act(self, command: str) -> bool:
        if reply := quick_answers.time_answer(command, self.clock()):
            await self.assistant.say_all([reply])
            return True
        if meaning := await self.definition(command):
            await self.assistant.say_all([meaning])
            return True
        if (action := headset_commands.parse(command)) is not None:
            return await self.run_on_headset(await self.resolved(action))
        if query := quick_answers.search_query(command):
            await self.assistant.answer(quick_answers.search_question(query))
            return True
        return False

    async def definition(self, command: str) -> str | None:
        term = quick_answers.word_to_define(command)
        if term is None or self.http is None:
            return None
        return await quick_answers.look_up_word(self.http, term)

    async def resolved(self, action: dict) -> dict:
        if action["action"] != "play_on":
            return action
        if music.is_youtube(action["app"]):
            url = await music.youtube_watch_url(self.http, action["query"])
            return {"action": "youtube_play", "query": action["query"], "url": url or ""}
        return {"action": "app_play", "app": action["app"], "query": action["query"]}

    async def run_on_headset(self, action: dict) -> bool:
        log.info("headset command: %s", action["action"])
        action_id = next(self.ids)
        reply = asyncio.get_running_loop().create_future()
        self.pending[action_id] = reply
        await self.output.send({"type": "action", "id": action_id, **action})
        try:
            say = await asyncio.wait_for(reply, ACTION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            say = NO_REPLY
        finally:
            self.pending.pop(action_id, None)
        if say:
            await self.assistant.say_all([say])
        else:
            await self.assistant.show_resting_state()
        return True

    def hear(self, pcm: bytes) -> None:
        pcm = pcm[:len(pcm) - len(pcm) % SAMPLE_BYTES]
        if self.assistant.muted():
            self.collector.reset()
            return
        for utterance in self.collector.feed(pcm):
            self.assistant.hear(utterance)

    async def handle_text(self, data: str) -> None:
        try:
            message = json.loads(data)
        except ValueError:
            log.warning("ignored a headset message that wasn't JSON")
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        action_id = message.get("id")
        if kind == "action_result" and isinstance(action_id, int) and not isinstance(action_id, bool):
            reply = self.pending.get(action_id)
            if reply and not reply.done():
                reply.set_result(str(message.get("say") or ""))
        elif kind == "wake" and isinstance(message.get("enabled"), bool):
            await self.assistant.follow_wake_setting(message["enabled"])


async def keep_panels_fresh(dashboard: Dashboard, output: HeadsetOutput) -> None:
    while not output.ws.closed:
        try:
            await output.send(await dashboard.current())
        except Exception:
            log.exception("couldn't refresh the headset panels; trying again shortly")
            await asyncio.sleep(PANEL_RETRY_SECONDS)
            continue
        await asyncio.sleep(REFRESH_SECONDS)


def close_if_it_fails(task: asyncio.Task, ws: web.WebSocketResponse, job: str) -> None:
    def check(finished: asyncio.Task) -> None:
        if finished.cancelled() or finished.exception() is None:
            return
        log.error("the headset %s stopped (%r), so the headset is disconnected to start afresh", job,
                  finished.exception())
        asyncio.ensure_future(ws.close(message=b"arty restarted this connection"))
    task.add_done_callback(check)


class IdentityLimiter:

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.asked: dict[str, list[float]] = {}

    def allow(self, address: str) -> bool:
        now = self.clock()
        self.asked = {source: [when for when in times if now - when < IDENTITY_WINDOW_SECONDS]
                      for source, times in self.asked.items()}
        self.asked = {source: times for source, times in self.asked.items() if times}
        recent = self.asked.setdefault(address, [])
        if len(recent) >= IDENTITY_LIMIT:
            return False
        if len(self.asked) > MAX_IDENTITY_ADDRESSES:
            return False
        recent.append(now)
        return True


def identity_proof(nonce: str, key: bytes | None = None) -> str:
    secret = key if key is not None else HEADSET_TOKEN.encode()
    return hmac.new(secret, (IDENTITY_LABEL + nonce).encode(), hashlib.sha256).hexdigest()


async def identity(request: web.Request) -> web.Response:
    if not request.app["identity_limiter"].allow(request.remote or ""):
        return web.json_response({"error": "too many questions, try again in a minute"}, status=429)
    nonce = request.query.get("nonce", "")
    if not IDENTITY_NONCE.fullmatch(nonce):
        return web.json_response({"error": "send a nonce of 16 to 128 hex characters"}, status=400)
    device_id = request.query.get("device", "")
    if device_id:
        key = request.app["devices"].key_of(device_id)
        if key is None:
            return web.json_response({"error": "no headset is paired with that id"}, status=404)
        return web.json_response({"proof": identity_proof(nonce, key.encode())})
    if not HEADSET_TOKEN:
        return web.json_response({"error": "this server has no headset key"}, status=503)
    return web.json_response({"proof": identity_proof(nonce)})


def caller(request: web.Request) -> str | None:
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not supplied:
        return None
    if HEADSET_TOKEN and hmac.compare_digest(supplied.encode(), HEADSET_TOKEN.encode()):
        return OWNER
    return request.app["devices"].device_for(supplied)


def require_owner(request: web.Request) -> None:
    if caller(request) != OWNER:
        raise web.HTTPUnauthorized(text="only the server's own headset key can do that")


async def pair_new(request: web.Request) -> web.Response:
    require_owner(request)
    code = request.app["pairing"].issue()
    log.info("a headset setup code was made")
    if request.query.get("format") == "text":
        return web.Response(text=code + "\n")
    return web.json_response({"code": code, "expires_in": pairing.LIFETIME_SECONDS})


async def pair_redeem(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "send the code as JSON"}, status=400)
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str):
        return web.json_response({"error": "send the code as JSON"}, status=400)
    outcome = request.app["pairing"].redeem(code, request.remote or "")
    if outcome == pairing.LOCKED:
        log.warning("setup code tries from %s were refused: too many wrong codes", request.remote)
        return web.json_response({"error": "too many wrong codes, try again in a few minutes"}, status=429)
    if outcome != pairing.OK:
        return web.json_response({"error": "that code isn't right or has run out"}, status=400)
    name = body.get("device_name")
    if isinstance(name, str) and name.strip():
        return pair_device(request, name)
    if not LEGACY_PAIRING:
        return web.json_response({"error": "this server only pairs named headsets; update the Arty Quest app"}, status=400)
    log.warning("a headset setup code from %s was used without a device name, so it got the shared key", request.remote)
    return web.json_response({"token": HEADSET_TOKEN})


def pair_device(request: web.Request, name: str) -> web.Response:
    try:
        device_id, token = request.app["devices"].add(name)
    except headset_devices.DeviceLimitReached as error:
        return web.json_response({"error": f"{error}, then make a new code"}, status=409)
    except (OSError, ValueError) as error:
        log.error("could not save a paired headset in %s: %s", DEVICES_FILE, error)
        return web.json_response({"error": "the server couldn't save this headset; make a new code and try again"}, status=500)
    log.info("headset %s was paired from %s", device_id, request.remote)
    return web.json_response({"token": token, "device": device_id})


async def list_devices(request: web.Request) -> web.Response:
    require_owner(request)
    return web.json_response({"devices": request.app["devices"].listed()})


async def forget_device(request: web.Request) -> web.Response:
    require_owner(request)
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "send the device id as JSON"}, status=400)
    device_id = body.get("device") if isinstance(body, dict) else None
    if not isinstance(device_id, str):
        return web.json_response({"error": "send the device id as JSON"}, status=400)
    if not request.app["devices"].forget(device_id):
        return web.json_response({"error": "no headset is paired with that id"}, status=404)
    log.info("headset %s was forgotten", device_id)
    return web.json_response({"forgotten": device_id})


async def headset_socket(request: web.Request) -> web.WebSocketResponse:
    if caller(request) is None:
        raise web.HTTPUnauthorized(text="unknown headset key")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)
    session = HeadsetSession(request.app["senses"], request.app["http"], ws)
    worker = asyncio.create_task(session.assistant.handle_utterances())
    panels = asyncio.create_task(keep_panels_fresh(request.app["dashboard"], session.output))
    close_if_it_fails(worker, ws, "listener")
    close_if_it_fails(panels, ws, "panels")
    log.info("headset connected from %s", request.remote)
    await session.output.send({"type": "hello", "wake_enabled": session.assistant.wake_enabled})
    await session.assistant.show_resting_state()
    try:
        async for message in ws:
            if message.type == aiohttp.WSMsgType.BINARY:
                session.hear(message.data)
            elif message.type == aiohttp.WSMsgType.TEXT:
                await session.handle_text(message.data)
            elif message.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        worker.cancel()
        panels.cancel()
        log.info("headset disconnected")
    return ws


def build_app(senses: Senses, http: aiohttp.ClientSession) -> web.Application:
    app = web.Application()
    app["senses"] = senses
    app["http"] = http
    app["dashboard"] = Dashboard(http, BRAIN_URL, BRAIN_KEY)
    app["pairing"] = pairing.PairingCodes()
    app["devices"] = headset_devices.HeadsetDevices(Path(DEVICES_FILE))
    app["identity_limiter"] = IdentityLimiter()
    app.router.add_get("/ws/headset", headset_socket)
    app.router.add_get("/pair/new", pair_new)
    app.router.add_post("/pair/redeem", pair_redeem)
    app.router.add_get("/identity", identity)
    app.router.add_get("/pair/devices", list_devices)
    app.router.add_post("/pair/forget", forget_device)
    return app


async def serve(senses: Senses, http: aiohttp.ClientSession) -> None:
    if not HEADSET_TOKEN:
        log.warning("HEADSET_TOKEN isn't set, so the Quest headset can't connect")
        return
    if len(HEADSET_TOKEN) < MIN_TOKEN_CHARS:
        log.error("HEADSET_TOKEN must be at least %d random characters, so the headset server isn't started", MIN_TOKEN_CHARS)
        return
    runner = web.AppRunner(build_app(senses, http), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, HEADSET_HOST, HEADSET_PORT).start()
    log.info("headset server on port %d", HEADSET_PORT)
    await asyncio.Event().wait()
