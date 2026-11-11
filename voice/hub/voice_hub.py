from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path

from aiohttp import WSMsgType, web

from accounts import MAX_FIELD_CHARS, AccountError, AccountStore
from devices import DeviceStore, write_private_json
from installers import build_installer

HERE = Path(__file__).resolve().parent
DATA_DIR = os.environ.get("HUB_DATA_DIR", "/var/lib/my-voice")
PORT = int(os.environ.get("HUB_PORT", "8780"))
HOST = os.environ.get("HUB_HOST", "0.0.0.0")
SETUP_CODE_FILE = "setup-code"
SETUP_CODE_BYTES = 9
PUBLIC_URL = os.environ.get("HUB_PUBLIC_URL", f"http://127.0.0.1:{PORT}")
MIC_DEVICE = os.environ.get("HUB_MIC_DEVICE", "plughw:CARD=Mic,DEV=0")
SPEAKER_DEVICE = os.environ.get("HUB_SPEAKER_DEVICE", "plughw:CARD=PCH,DEV=0")
MIXER_CARD = os.environ.get("HUB_MIXER_CARD", "PCH")
MIXER_CONTROL = os.environ.get("HUB_MIXER_CONTROL", "Master")
VOLUME_STEP = 10
VOLUME_CHANGES = ("up", "down", "mute", "unmute")
MIXER_LEVEL = re.compile(r"\[(\d+)%\](?:.*?\[(on|off)\])?")
MIC_RATE = 48000
CHUNK_BYTES = MIC_RATE * 2 // 25
SUBSCRIBER_QUEUE = 50
MAX_SPEECH_BYTES = 20 * 1024 * 1024
SESSION_COOKIE = "my_voice_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILED_LOGINS = 5
MAX_FAILED_LOGINS_PER_ADDRESS = 20
MAX_TRACKED_LOGIN_KEYS = 1000
LOCKOUT_SECONDS = 15 * 60
MIC_RETRY_SECONDS = 3

log = logging.getLogger("my-voice")


class Sessions:

    def __init__(self):
        self.path = os.path.join(DATA_DIR, "sessions.json")

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            now = time.time()
            return {k: v for k, v in json.load(f).items() if v["expires"] > now}

    def create(self, email: str) -> str:
        token = secrets.token_urlsafe(32)
        sessions = self._load()
        sessions[hashlib.sha256(token.encode()).hexdigest()] = {"email": email,
                                                                "expires": time.time() + SESSION_SECONDS}
        write_private_json(self.path, sessions)
        return token

    def email_for(self, token: str) -> str | None:
        if not token:
            return None
        session = self._load().get(hashlib.sha256(token.encode()).hexdigest())
        return session["email"] if session else None

    def revoke(self, token: str) -> None:
        sessions = self._load()
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        write_private_json(self.path, sessions)


class WakeSwitch:

    def __init__(self):
        self.path = os.path.join(DATA_DIR, "wake.json")
        self.sockets: set[web.WebSocketResponse] = set()

    @property
    def enabled(self) -> bool:
        if not os.path.exists(self.path):
            return True
        with open(self.path, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("enabled", True))

    async def set(self, enabled: bool, by: str) -> None:
        write_private_json(self.path, {"enabled": enabled})
        log.info("wake words turned %s by %s", "on" if enabled else "off", by)
        for ws in list(self.sockets):
            try:
                await ws.send_json({"wake_enabled": enabled})
            except ConnectionError:
                self.sockets.discard(ws)


class FailedLogins:

    def __init__(self):
        self.times: dict[str, list[float]] = {}

    def keys(self, address: str, email: str) -> tuple[str, str]:
        return f"{address}|{email}", f"{address}|*"

    def recent(self, key: str, now: float) -> list[float]:
        return [when for when in self.times.get(key, []) if now - when < LOCKOUT_SECONDS]

    def locked(self, address: str, email: str) -> bool:
        now = time.time()
        account_key, address_key = self.keys(address, email)
        return (len(self.recent(account_key, now)) >= MAX_FAILED_LOGINS
                or len(self.recent(address_key, now)) >= MAX_FAILED_LOGINS_PER_ADDRESS)

    def record(self, address: str, email: str) -> None:
        now = time.time()
        for key in self.keys(address, email):
            recent = self.recent(key, now)
            self.times.pop(key, None)
            self.times[key] = recent + [now]
        self.forget_old(now)

    def clear(self, address: str, email: str) -> None:
        self.times.pop(self.keys(address, email)[0], None)

    def forget_old(self, now: float) -> None:
        self.times = {key: times for key, times in self.times.items() if now - times[-1] < LOCKOUT_SECONDS}
        while len(self.times) > MAX_TRACKED_LOGIN_KEYS:
            del self.times[next(iter(self.times))]


class Microphone:

    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.error = ""
        self.closed_on_purpose = False

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        self.subscribers.add(queue)
        if not self.reader or self.reader.done():
            self.reader = asyncio.create_task(self._capture())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)
        if not self.subscribers and self.process and self.process.returncode is None:
            self.closed_on_purpose = True
            self.process.terminate()

    async def _capture(self) -> None:
        while self.subscribers:
            self.closed_on_purpose = False
            self.process = await asyncio.create_subprocess_exec(
                "arecord", "-q", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(MIC_RATE), "-c", "1", "-t", "raw",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            log.info("mic opened for %d listener(s)", len(self.subscribers))
            while True:
                try:
                    chunk = await self.process.stdout.readexactly(CHUNK_BYTES)
                except asyncio.IncompleteReadError:
                    break
                self.error = ""
                for queue in list(self.subscribers):
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(chunk)
            stderr = (await self.process.stderr.read()).decode(errors="replace").strip()
            await self.process.wait()
            log.info("mic closed")
            if self.subscribers and not self.closed_on_purpose:
                self.error = stderr or f"arecord exited with {self.process.returncode}"
                log.error("mic capture stopped: %s - retrying in %d s", self.error, MIC_RETRY_SECONDS)
                await asyncio.sleep(MIC_RETRY_SECONDS)


class Speakers:

    def __init__(self):
        self.listeners: set[web.WebSocketResponse] = set()
        self.lock = asyncio.Lock()

    async def play(self, wav: bytes) -> str:
        for ws in list(self.listeners):
            try:
                await ws.send_bytes(wav)
            except ConnectionError:
                self.listeners.discard(ws)
        async with self.lock:
            process = await asyncio.create_subprocess_exec(
                "aplay", "-q", "-D", SPEAKER_DEVICE, "-", stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            _, stderr = await process.communicate(wav)
        if process.returncode:
            message = stderr.decode(errors="replace").strip()
            log.error("server speakers failed: %s", message)
            return message
        return ""


def mixer_state(amixer_output: str) -> tuple[int, bool]:
    match = MIXER_LEVEL.search(amixer_output)
    if not match:
        raise ValueError(f"no volume level in amixer's output: {amixer_output.strip()[:200]}")
    return int(match.group(1)), match.group(2) == "off"


def volume_target(level: int, muted: bool, change: str | None, wanted: int | None) -> tuple[int, bool]:
    if wanted is not None:
        return max(0, min(100, wanted)), wanted <= 0
    if change == "mute":
        return level, True
    if change == "unmute":
        return level or VOLUME_STEP, False
    if change == "up":
        return min(100, (0 if muted else level) + VOLUME_STEP), False
    return max(0, level - VOLUME_STEP), muted or level - VOLUME_STEP <= 0


class Volume:

    def __init__(self):
        self.lock = asyncio.Lock()

    async def amixer(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "amixer", "-c", MIXER_CARD, "-M", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await process.communicate()
        text = output.decode(errors="replace")
        if process.returncode:
            raise RuntimeError(f"amixer failed: {text.strip()[:200]}")
        return text

    async def get(self) -> tuple[int, bool]:
        return mixer_state(await self.amixer("sget", MIXER_CONTROL))

    async def change(self, change: str | None, wanted: int | None) -> tuple[int, bool]:
        async with self.lock:
            level, muted = volume_target(*await self.get(), change, wanted)
            return mixer_state(await self.amixer("sset", MIXER_CONTROL, f"{level}%", "mute" if muted else "unmute"))


def bearer_token(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def client_address(request: web.Request) -> str:
    return request.remote or "unknown"


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-cache")
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'")
    return response


def logged_in_email(request: web.Request) -> str | None:
    return request.app["sessions"].email_for(request.cookies.get(SESSION_COOKIE, ""))


def require_login(handler):
    async def wrapper(request: web.Request):
        if not logged_in_email(request):
            if request.method == "GET":
                raise web.HTTPSeeOther("/login")
            raise web.HTTPUnauthorized(text="log in first")
        return await handler(request)
    return wrapper


def require_same_origin(request: web.Request) -> None:
    origin = request.headers.get("Origin")
    expected = f"{request.scheme}://{request.host}"
    if origin and origin.rstrip("/") != expected:
        log.warning("refused %s %s: origin %s, expected %s", request.method, request.path, origin, expected)
        raise web.HTTPForbidden(text="cross-site request refused")


def served_over_https(request: web.Request) -> bool:
    return request.secure or PUBLIC_URL.startswith("https://")


def setup_code_path() -> Path:
    return Path(DATA_DIR) / SETUP_CODE_FILE


def ensure_setup_code() -> None:
    path = setup_code_path()
    if path.is_file():
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as code_file:
        code_file.write(secrets.token_urlsafe(SETUP_CODE_BYTES) + "\n")


def setup_code_matches(typed: str) -> bool:
    path = setup_code_path()
    if not path.is_file():
        return False
    expected = path.read_text().strip()
    return bool(expected) and hmac.compare_digest(typed.strip().encode(), expected.encode())


def logged_in_response(request: web.Request, email: str, location: str = "/") -> web.HTTPSeeOther:
    response = web.HTTPSeeOther(location)
    response.set_cookie(SESSION_COOKIE, request.app["sessions"].create(email), max_age=SESSION_SECONDS,
                        httponly=True, samesite="Strict", path="/", secure=served_over_https(request))
    return response


def form_text(form, name: str) -> str:
    return str(form.get(name, ""))[:MAX_FIELD_CHARS]


async def login_page(request: web.Request):
    if not request.app["accounts"].has_accounts():
        raise web.HTTPSeeOther("/setup")
    return web.FileResponse(HERE / "static" / "login.html")


async def login(request: web.Request):
    require_same_origin(request)
    form = await request.post()
    email = form_text(form, "email").strip().lower()
    failures: FailedLogins = request.app["failures"]
    address = client_address(request)
    if failures.locked(address, email):
        log.warning("login locked for %s from %s", email, address)
        raise web.HTTPSeeOther("/login?error=locked")
    if not request.app["accounts"].verify(email, form_text(form, "password")):
        failures.record(address, email)
        log.warning("failed login for %s from %s", email, address)
        raise web.HTTPSeeOther("/login?error=wrong")
    failures.clear(address, email)
    log.info("%s logged in from %s", email, client_address(request))
    raise logged_in_response(request, email)


async def setup_page(request: web.Request):
    if request.app["accounts"].has_accounts():
        raise web.HTTPSeeOther("/login")
    return web.FileResponse(HERE / "static" / "setup.html")


async def setup(request: web.Request):
    require_same_origin(request)
    form = await request.post()
    if not setup_code_matches(form_text(form, "setup_code")):
        log.warning("first-account setup with a wrong setup code from %s", client_address(request))
        message = f"The setup code isn't right. It's in {setup_code_path()} on the server."
        raise web.HTTPSeeOther("/setup?" + urllib.parse.urlencode({"error": message}))
    try:
        email = request.app["accounts"].create_first(form_text(form, "email"), form_text(form, "password"),
                                                     form_text(form, "confirm"))
    except AccountError as error:
        raise web.HTTPSeeOther("/setup?" + urllib.parse.urlencode({"error": str(error)}))
    setup_code_path().unlink(missing_ok=True)
    log.info("first account created for %s from %s", email, client_address(request))
    raise logged_in_response(request, email)


async def logout(request: web.Request):
    require_same_origin(request)
    request.app["sessions"].revoke(request.cookies.get(SESSION_COOKIE, ""))
    response = web.HTTPSeeOther("/login")
    response.del_cookie(SESSION_COOKIE, path="/")
    raise response


@require_login
async def home(request: web.Request):
    return web.FileResponse(HERE / "static" / "index.html")


@require_login
async def status(request: web.Request):
    devices = request.app["devices"]
    microphone: Microphone = request.app["microphone"]
    return web.json_response({
        "mic_listeners": len(microphone.subscribers),
        "mic_error": microphone.error,
        "speaker_listeners": len(request.app["speakers"].listeners),
        "wake_enabled": request.app["wake"].enabled,
        "devices": [d.public() for d in devices.list() if not d.revoked],
        "user": logged_in_email(request),
    })


@require_login
async def download(request: web.Request):
    require_same_origin(request)
    form = await request.post()
    system = str(form.get("system", ""))
    if system not in ("debian", "windows"):
        raise web.HTTPBadRequest(text="pick Debian or Windows")
    device, token = request.app["devices"].add(str(form.get("name", "")), system, "pc")
    filename, body = build_installer(system, PUBLIC_URL, token, device.name)
    log.info("new %s device %s (%s)", system, device.id, device.name)
    return web.Response(body=body, content_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@require_login
async def revoke(request: web.Request):
    require_same_origin(request)
    if not request.app["devices"].revoke(request.match_info["device_id"]):
        raise web.HTTPNotFound(text="no such device")
    for ws in list(request.app["connections"].get(request.match_info["device_id"], ())):
        await ws.close(message=b"revoked")
    return web.json_response({"ok": True})


async def wanted_state(request: web.Request) -> bool:
    try:
        enabled = (await request.json()).get("enabled")
    except (ValueError, AttributeError):
        enabled = None
    if not isinstance(enabled, bool):
        raise web.HTTPBadRequest(text='send {"enabled": true} or {"enabled": false}')
    return enabled


@require_login
async def set_wake(request: web.Request):
    require_same_origin(request)
    enabled = await wanted_state(request)
    await request.app["wake"].set(enabled, logged_in_email(request) or "the website")
    return web.json_response({"wake_enabled": enabled})


async def assistant_set_wake(request: web.Request):
    device = authenticated_device(request)
    if device.role != "assistant":
        raise web.HTTPForbidden(text="only the assistant can switch the wake words by voice")
    enabled = await wanted_state(request)
    await request.app["wake"].set(enabled, "voice")
    return web.json_response({"wake_enabled": enabled})


def authenticated_device(request: web.Request):
    device = request.app["devices"].find(bearer_token(request))
    if not device:
        raise web.HTTPUnauthorized(text="unknown or revoked device key")
    request.app["devices"].touch(device.id)
    return device


async def open_socket(request: web.Request, device) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    request.app["connections"].setdefault(device.id, set()).add(ws)
    return ws


def close_socket(request: web.Request, device, ws) -> None:
    request.app["connections"].get(device.id, set()).discard(ws)


async def forward_mic(ws: web.WebSocketResponse, queue: asyncio.Queue, wake_enabled: bool) -> None:
    try:
        await ws.send_json({"format": "s16le", "rate": MIC_RATE, "channels": 1, "wake_enabled": wake_enabled})
        while not ws.closed:
            await ws.send_bytes(await queue.get())
    except ConnectionError:
        await ws.close()


async def mic_socket(request: web.Request):
    device = authenticated_device(request)
    ws = await open_socket(request, device)
    microphone: Microphone = request.app["microphone"]
    queue = await microphone.subscribe()
    wake: WakeSwitch = request.app["wake"]
    wake.sockets.add(ws)
    log.info("%s is listening to the mic", device.name)
    sender = asyncio.create_task(forward_mic(ws, queue, wake.enabled))
    try:
        async for message in ws:
            if message.type == WSMsgType.ERROR:
                break
    finally:
        sender.cancel()
        wake.sockets.discard(ws)
        microphone.unsubscribe(queue)
        close_socket(request, device, ws)
        log.info("%s stopped listening", device.name)
    return ws


async def speaker_socket(request: web.Request):
    device = authenticated_device(request)
    ws = await open_socket(request, device)
    speakers: Speakers = request.app["speakers"]
    speakers.listeners.add(ws)
    try:
        async for message in ws:
            if message.type == WSMsgType.ERROR:
                break
    finally:
        speakers.listeners.discard(ws)
        close_socket(request, device, ws)
    return ws


async def speak(request: web.Request):
    device = authenticated_device(request)
    if device.role != "assistant":
        raise web.HTTPForbidden(text="only the assistant can speak through the hub")
    wav = await request.read()
    if not wav.startswith(b"RIFF") or len(wav) > MAX_SPEECH_BYTES:
        raise web.HTTPBadRequest(text="send a WAV file under 20 MB")
    problem = await request.app["speakers"].play(wav)
    return web.json_response({"ok": not problem, "server_speakers": problem or "played",
                              "devices": len(request.app["speakers"].listeners)})


async def assistant_volume(request: web.Request):
    device = authenticated_device(request)
    if device.role != "assistant":
        raise web.HTTPForbidden(text="only the assistant can change the speaker volume")
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="send JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="send a JSON object")
    change, wanted = body.get("change"), body.get("level")
    if change is None and wanted is None:
        level, muted = await request.app["volume"].get()
        return web.json_response({"level": level, "muted": muted})
    if change is not None and change not in VOLUME_CHANGES:
        raise web.HTTPBadRequest(text=f"change must be one of {', '.join(VOLUME_CHANGES)}")
    if wanted is not None and (isinstance(wanted, bool) or not isinstance(wanted, int)):
        raise web.HTTPBadRequest(text="level must be a whole number from 0 to 100")
    try:
        level, muted = await request.app["volume"].change(change, wanted)
    except (RuntimeError, ValueError) as error:
        log.error("volume change failed: %s", error)
        raise web.HTTPBadGateway(text=str(error)) from None
    log.info("speaker volume now %d%%%s", level, " (muted)" if muted else "")
    return web.json_response({"level": level, "muted": muted})


def build_app() -> web.Application:
    os.makedirs(DATA_DIR, exist_ok=True)
    app = web.Application(middlewares=[security_headers], client_max_size=MAX_SPEECH_BYTES + 1024)
    app["devices"] = DeviceStore(DATA_DIR)
    app["accounts"] = AccountStore(DATA_DIR)
    if not app["accounts"].has_accounts():
        ensure_setup_code()
    app["sessions"] = Sessions()
    app["microphone"] = Microphone()
    app["speakers"] = Speakers()
    app["volume"] = Volume()
    app["wake"] = WakeSwitch()
    app["failures"] = FailedLogins()
    app["connections"] = {}
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/setup", setup)
    app.router.add_get("/", home)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/wake", set_wake)
    app.router.add_post("/download", download)
    app.router.add_post("/devices/{device_id}/revoke", revoke)
    app.router.add_get("/ws/mic", mic_socket)
    app.router.add_get("/ws/speaker", speaker_socket)
    app.router.add_post("/speak", speak)
    app.router.add_post("/assistant/wake", assistant_set_wake)
    app.router.add_post("/assistant/volume", assistant_volume)
    app.router.add_static("/static", HERE / "static")
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not AccountStore(DATA_DIR).has_accounts():
        log.warning("no account yet - open %s/setup with the code in %s", PUBLIC_URL, setup_code_path())
    web.run_app(build_app(), host=HOST, port=PORT, access_log=None)
