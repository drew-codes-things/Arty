import asyncio
import hmac
import itertools
import json
import logging
import os

import aiohttp
from aiohttp import web

import headset_commands
from arty_voice import BRAIN_KEY, BRAIN_URL, Assistant, Senses
from headset_panels import REFRESH_SECONDS, Dashboard
from listening import UtteranceCollector

HEADSET_TOKEN = os.environ.get("HEADSET_TOKEN", "")
HEADSET_PORT = int(os.environ.get("HEADSET_PORT", "8790"))
ACTION_TIMEOUT_SECONDS = 10
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
        self.assistant = Assistant(senses, self.output, http, bare_wake=True, act=self.act)
        self.collector = UtteranceCollector()
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)

    async def act(self, command: str) -> bool:
        action = headset_commands.parse(command)
        if action is None:
            return False
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
        if kind == "action_result":
            reply = self.pending.get(message.get("id"))
            if reply and not reply.done():
                reply.set_result(str(message.get("say") or ""))
        elif kind == "wake" and isinstance(message.get("enabled"), bool):
            await self.assistant.follow_wake_setting(message["enabled"])


async def keep_panels_fresh(dashboard: Dashboard, output: HeadsetOutput) -> None:
    while not output.ws.closed:
        await output.send(await dashboard.current())
        await asyncio.sleep(REFRESH_SECONDS)


def authorised(request: web.Request) -> bool:
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(HEADSET_TOKEN) and hmac.compare_digest(supplied.encode(), HEADSET_TOKEN.encode())


async def headset_socket(request: web.Request) -> web.WebSocketResponse:
    if not authorised(request):
        raise web.HTTPUnauthorized(text="unknown headset key")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)
    session = HeadsetSession(request.app["senses"], request.app["http"], ws)
    worker = asyncio.create_task(session.assistant.handle_utterances())
    panels = asyncio.create_task(keep_panels_fresh(request.app["dashboard"], session.output))
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
    app.router.add_get("/ws/headset", headset_socket)
    return app


async def serve(senses: Senses, http: aiohttp.ClientSession) -> None:
    if not HEADSET_TOKEN:
        log.warning("HEADSET_TOKEN isn't set, so the Quest headset can't connect")
        return
    runner = web.AppRunner(build_app(senses, http), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEADSET_PORT).start()
    log.info("headset server on port %d", HEADSET_PORT)
    await asyncio.Event().wait()
