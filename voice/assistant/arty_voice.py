import asyncio
import json
import logging
import os
import threading
import time
from typing import Awaitable, Callable, Protocol

import aiohttp
from faster_whisper import WhisperModel

from headset_commands import parse_volume
from listening import WHISPER_RATE, Downsampler, Utterance, UtteranceCollector, to_float
from live_text import LiveTextFailed
from speaking import NOT_FOR_ME, SentenceBuffer, Voice, might_be_ignored, plain_speech
from turn_end import load_listening
from wake_spotter import WakeSpotter, wake_spotter_from_settings
from wake import find_switch, find_wake, has_words, is_filler

HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8780").rstrip("/")
BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:8081/v1").rstrip("/")
HUB_TOKEN = os.environ["HUB_TOKEN"]
BRAIN_KEY = os.environ["ARTY_API_KEY"]
VOICE_MODEL = os.environ.get("VOICE_MODEL", "/opt/ai-arty/models/en_GB-alan-medium.onnx")
WAKE_MODEL = os.environ.get("WAKE_MODEL", "tiny.en")
COMMAND_MODEL = os.environ.get("COMMAND_MODEL", "small.en")
WAKE_CHECK_SECONDS = 3
WAKE_CHECK_BYTES = WAKE_CHECK_SECONDS * WHISPER_RATE * 2
WAKE_PRIMER = "Hey Arty. Arty on. Arty off."
LISTEN_AFTER_WAKE_SECONDS = 8
FOLLOW_UP_SECONDS = 6
ECHO_GUARD_SECONDS = 0.6
HISTORY_SECONDS = 5 * 60
HISTORY_MESSAGES = 8
SLOW_ANSWER_SECONDS = 6
BRAIN_TIMEOUT_SECONDS = 300
CLIENT_NAME = "voice"
NO_SPEECH_LIMIT = 0.6
RETRY_SECONDS = (1, 2, 5, 10, 30)
HEALTHY_RUN_SECONDS = 300
SPOKEN_STYLE = ("You are speaking out loud through speakers at home (voice mode). Answer in at most two or "
                "three short spoken sentences of plain words: no markdown, lists, tables, links, code or emoji. If the "
                "full answer is long, give the gist and offer to put the details in a chat. The microphone also "
                "overhears people talking to each other: if this message isn't meant for you (a fragment, an "
                f"exclamation, or part of someone else's conversation), reply with exactly {NOT_FOR_ME} and nothing else.")
TURNED_ON = "I'm on. Say Hey Arty when you need me."
ALREADY_ON = "I'm here."
TURNED_OFF = "Okay, going quiet. Say Arty on when you need me."

log = logging.getLogger("arty-voice")


class Output(Protocol):

    async def play(self, wav: bytes) -> None: ...

    async def state(self, name: str, text: str = "", seconds: float = 0) -> None: ...

    async def wake_changed(self, enabled: bool) -> None: ...


class Senses:

    def __init__(self):
        self.wake_model = WhisperModel(WAKE_MODEL, device="cpu", compute_type="int8")
        self.command_model = WhisperModel(COMMAND_MODEL, device="cpu", compute_type="int8")
        self.voice = Voice(VOICE_MODEL)
        self.model_locks = {id(self.wake_model): threading.Lock(), id(self.command_model): threading.Lock()}
        self.voice_lock = threading.Lock()
        self.listening = load_listening(os.environ)
        self.wake_spotter = wake_spotter_from_settings(os.environ)

    def new_collector(self) -> UtteranceCollector:
        return self.listening.new_collector()

    def transcribe(self, model: WhisperModel, audio: bytes, beam_size: int, primer: str | None = None) -> str:
        with self.model_locks[id(model)]:
            segments, _ = model.transcribe(to_float(audio), language="en", beam_size=beam_size,
                                           condition_on_previous_text=False, without_timestamps=True,
                                           initial_prompt=primer)
            return " ".join(s.text.strip() for s in segments if s.no_speech_prob < NO_SPEECH_LIMIT).strip()

    def wav(self, text: str) -> bytes:
        with self.voice_lock:
            return self.voice.wav(text)


class Conversation:

    def __init__(self):
        self.messages: list[dict] = []
        self.last_turn = 0.0

    def history(self) -> list[dict]:
        if time.time() - self.last_turn > HISTORY_SECONDS:
            self.messages = []
        return list(self.messages)

    def remember(self, question: str, answer: str) -> None:
        self.messages = (self.history() + [{"role": "user", "content": question},
                                           {"role": "assistant", "content": answer}])[-HISTORY_MESSAGES:]
        self.last_turn = time.time()


def is_live(utterance: bytes) -> bool:
    return isinstance(utterance, Utterance) and utterance.live is not None


def release(utterance: bytes) -> None:
    if is_live(utterance):
        utterance.live.cancel()


class Assistant:

    def __init__(self, senses: Senses, output: Output, http: aiohttp.ClientSession, bare_wake: bool = False,
                 act: Callable[[str], Awaitable[bool]] | None = None, wake_spotter: WakeSpotter | None = None):
        self.senses = senses
        self.wake_spotter = wake_spotter
        self.output = output
        self.http = http
        self.bare_wake = bare_wake
        self.act = act
        self.conversation = Conversation()
        self.utterances: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
        self.muted_until = 0.0
        self.speaking = False
        self.listening_until = 0.0
        self.wake_enabled = True

    def muted(self) -> bool:
        return self.speaking or time.time() < self.muted_until

    def hear(self, utterance: bytes) -> None:
        if self.utterances.full():
            release(self.utterances.get_nowait())
        self.utterances.put_nowait(utterance)

    async def follow_wake_setting(self, enabled: bool) -> None:
        if enabled == self.wake_enabled:
            return
        self.wake_enabled = enabled
        self.listening_until = 0.0
        log.info("wake words %s", "on" if enabled else "off")
        await self.show_resting_state()

    async def handle_utterances(self) -> None:
        while True:
            utterance = await self.utterances.get()
            if self.muted():
                release(utterance)
                continue
            try:
                await self.handle_utterance(utterance)
            except Exception:
                log.exception("that turn failed; still listening")
                await self.recover_after_failed_turn()

    async def recover_after_failed_turn(self) -> None:
        self.speaking = False
        try:
            await self.show_resting_state()
        except Exception as error:
            log.warning("couldn't show the resting state after a failed turn: %r", error)

    async def handle_utterance(self, utterance: bytes) -> None:
        try:
            await self.handle_turn(utterance)
        finally:
            release(utterance)

    async def handle_turn(self, utterance: bytes) -> None:
        if await self.handled_by_spotter(utterance):
            return
        opening = await asyncio.to_thread(self.senses.transcribe, self.senses.wake_model,
                                          utterance[:WAKE_CHECK_BYTES], 1, WAKE_PRIMER)
        switch_to = find_switch(opening)
        if switch_to is not None:
            await self.switch(switch_to)
            return
        if not self.wake_enabled:
            return
        command = await self.command_from(utterance, opening)
        if command is None:
            return
        await self.carry_out(command)

    async def handled_by_spotter(self, utterance: bytes) -> bool:
        if self.wake_spotter is None or not is_live(utterance):
            return False
        if not await asyncio.to_thread(self.wake_spotter.heard, utterance[:WAKE_CHECK_BYTES]):
            return False
        text = await self.command_text(utterance)
        switch_to = find_switch(text)
        if switch_to is not None:
            await self.switch(switch_to)
            return True
        woke, rest = find_wake(text, self.bare_wake)
        if not woke or not self.wake_enabled:
            return False
        log.info("heard the wake word (spotter)")
        await self.carry_out(rest)
        return True

    async def carry_out(self, command: str) -> None:
        if not has_words(command):
            self.listening_until = time.time() + LISTEN_AFTER_WAKE_SECONDS
            await self.say_all(["Yes?"])
            return
        await self.output.state("thinking", command)
        if self.act and await self.act(command):
            return
        await self.answer(command)

    async def command_text(self, utterance: bytes) -> str:
        if is_live(utterance):
            try:
                text = await asyncio.to_thread(utterance.live.finish)
            except LiveTextFailed as error:
                log.warning("the live transcript failed, using whisper: %s", error)
            else:
                if text:
                    return text
        return await self.whisper_text(utterance)

    async def whisper_text(self, utterance: bytes) -> str:
        return await asyncio.to_thread(self.senses.transcribe, self.senses.command_model, utterance, 2)

    async def switch(self, enabled: bool) -> None:
        if not enabled and not self.wake_enabled:
            return
        reply = ALREADY_ON if enabled and self.wake_enabled else TURNED_ON if enabled else TURNED_OFF
        self.wake_enabled = enabled
        self.listening_until = 0.0
        await self.output.wake_changed(enabled)
        await self.say_all([reply])

    async def command_from(self, utterance: bytes, opening: str) -> str | None:
        if time.time() < self.listening_until:
            text = await self.command_text(utterance)
            woke, rest = find_wake(text, self.bare_wake)
            command = rest if woke else text
            if not woke and is_filler(command):
                log.info("ignored overheard filler")
                return None
            return command if has_words(text) else None
        if not find_wake(opening, self.bare_wake)[0]:
            return None
        log.info("heard the wake word")
        text = await self.command_text(utterance)
        woke, rest = find_wake(text, self.bare_wake)
        if not woke and is_live(utterance):
            woke, rest = find_wake(await self.whisper_text(utterance), self.bare_wake)
        if not woke:
            log.info("wake word not confirmed; ignoring")
            return None
        return rest

    async def answer(self, question: str) -> None:
        log.info("asked: %s", question)
        self.listening_until = 0.0
        sentences = SentenceBuffer()
        spoken: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self.speak_queue(spoken))
        answer = ""
        fed = 0
        ignored = False
        self.speaking = True
        try:
            reminder = asyncio.create_task(self.slow_answer_reminder(spoken, lambda: answer))
            try:
                async for delta in self.ask_brain(question):
                    answer += delta
                    if might_be_ignored(answer):
                        continue
                    for chunk in sentences.add(answer[fed:]):
                        spoken.put_nowait(chunk)
                    fed = len(answer)
            finally:
                reminder.cancel()
            if answer.strip() == NOT_FOR_ME:
                ignored = True
                log.info("not meant for me; staying quiet")
                return
            for chunk in sentences.add(answer[fed:]) + sentences.flush():
                spoken.put_nowait(chunk)
            self.conversation.remember(question, answer)
            log.info("answered (%d characters)", len(answer))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            log.error("brain request failed: %s", error)
            spoken.put_nowait("Sorry, I can't reach my brain right now.")
        finally:
            spoken.put_nowait(None)
            try:
                await speaker
            finally:
                await self.finish_speaking(follow_up=not ignored)

    async def finish_speaking(self, follow_up: bool) -> None:
        self.speaking = False
        self.muted_until = time.time() + ECHO_GUARD_SECONDS
        if follow_up:
            self.listening_until = time.time() + ECHO_GUARD_SECONDS + FOLLOW_UP_SECONDS
        await self.show_resting_state()

    async def show_resting_state(self) -> None:
        window = self.listening_until - time.time()
        if self.wake_enabled and window > 0:
            await self.output.state("listening", seconds=window)
        else:
            await self.output.state("idle" if self.wake_enabled else "off")

    async def slow_answer_reminder(self, spoken: asyncio.Queue, answer_so_far) -> None:
        await asyncio.sleep(SLOW_ANSWER_SECONDS)
        if not answer_so_far():
            spoken.put_nowait("One moment.")

    async def ask_brain(self, question: str):
        body = {"model": "Arty", "stream": True, "client": CLIENT_NAME,
                "messages": [{"role": "system", "content": SPOKEN_STYLE}, *self.conversation.history(),
                             {"role": "user", "content": question}]}
        headers = {"Authorization": f"Bearer {BRAIN_KEY}"}
        timeout = aiohttp.ClientTimeout(total=BRAIN_TIMEOUT_SECONDS)
        async with self.http.post(f"{BRAIN_URL}/chat/completions", json=body, headers=headers,
                                  timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"brain said {response.status}: {(await response.text())[:200]}")
            async for raw_line in response.content:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                choices = json.loads(line[5:]).get("choices") or [{}]
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta

    async def speak_queue(self, spoken: asyncio.Queue) -> None:
        while (text := await spoken.get()) is not None:
            try:
                await self.say(text)
            except Exception:
                log.exception("couldn't speak a sentence; carrying on with the rest")

    async def say_all(self, texts: list[str], follow_up: bool = False) -> None:
        self.speaking = True
        try:
            for text in texts:
                await self.say(text)
        finally:
            await self.finish_speaking(follow_up)

    async def say(self, text: str) -> None:
        words = plain_speech(text)
        if not words:
            return
        wav = await asyncio.to_thread(self.senses.wav, words)
        await self.output.state("speaking", words)
        await self.output.play(wav)


class HubOutput:

    def __init__(self, http: aiohttp.ClientSession):
        self.http = http
        self.headers = {"Authorization": f"Bearer {HUB_TOKEN}"}

    async def play(self, wav: bytes) -> None:
        try:
            async with self.http.post(f"{HUB_URL}/speak", data=wav,
                                      headers={**self.headers, "Content-Type": "audio/wav"}) as response:
                result = await response.json(content_type=None)
                if response.status != 200 or not result.get("ok"):
                    log.warning("hub couldn't play everywhere: %s", result)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            log.error("couldn't send speech to the hub: %r", error)

    async def state(self, name: str, text: str = "", seconds: float = 0) -> None:
        return None

    async def volume(self, request: dict) -> dict:
        async with self.http.post(f"{HUB_URL}/assistant/volume", json=request, headers=self.headers) as response:
            if response.status != 200:
                raise ValueError(f"hub said {response.status}: {(await response.text())[:200]}")
            return await response.json(content_type=None)

    async def wake_changed(self, enabled: bool) -> None:
        try:
            async with self.http.post(f"{HUB_URL}/assistant/wake", json={"enabled": enabled},
                                      headers=self.headers) as response:
                if response.status != 200:
                    log.warning("hub didn't save the wake switch: %s %s", response.status, (await response.text())[:200])
        except aiohttp.ClientError as error:
            log.error("couldn't tell the hub about the wake switch: %s", error)


class HubLink:

    def __init__(self, assistant: Assistant):
        self.assistant = assistant

    async def listen(self) -> None:
        downsampler, collector = Downsampler(), self.assistant.senses.new_collector()
        headers = {"Authorization": f"Bearer {HUB_TOKEN}"}
        async with self.assistant.http.ws_connect(f"{HUB_URL}/ws/mic", headers=headers, heartbeat=20) as ws:
            log.info("listening to the server mic")
            async for message in ws:
                if message.type == aiohttp.WSMsgType.ERROR:
                    raise ConnectionError(f"mic stream failed: {ws.exception()}")
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self.follow_hub_settings(message.data)
                    continue
                if message.type != aiohttp.WSMsgType.BINARY:
                    continue
                if self.assistant.muted():
                    collector.reset()
                    continue
                for utterance in collector.feed(downsampler.feed(message.data)):
                    self.assistant.hear(utterance)
        log.warning("the hub closed the mic stream")

    async def follow_hub_settings(self, data: str) -> None:
        try:
            settings = json.loads(data)
        except ValueError:
            log.warning("ignored a message from the hub that wasn't JSON")
            return
        enabled = settings.get("wake_enabled") if isinstance(settings, dict) else None
        if isinstance(enabled, bool):
            await self.assistant.follow_wake_setting(enabled)

    async def keep_listening(self) -> None:
        attempt = 0
        while True:
            try:
                await self.listen()
            except aiohttp.WSServerHandshakeError as error:
                if error.status == 401:
                    raise SystemExit("the hub refused Arty's device key - mint a new assistant key on my-voice")
                log.warning("mic: hub said %s", error.status)
            except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError) as error:
                log.warning("mic: %s", error)
            else:
                attempt = 0
            delay = RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)]
            attempt += 1
            await asyncio.sleep(delay)


def volume_reply(state: dict) -> str:
    return "Muted." if state.get("muted") else f"Volume {state.get('level', 0)} percent."


async def room_command(assistant: Assistant, output: HubOutput, command: str) -> bool:
    request = parse_volume(command)
    if request is None:
        return False
    if request.get("change") == "mute":
        await assistant.say_all(["Muting."])
    try:
        state = await output.volume(request)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        log.error("volume change failed: %s", error)
        await assistant.say_all(["Sorry, I couldn't change the volume."])
        return True
    log.info("speaker volume now %s", state)
    if request.get("change") != "mute":
        await assistant.say_all([volume_reply(state)])
    return True


async def keep_running(name: str, run: Callable[[], Awaitable[None]]) -> None:
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            await run()
            return
        except Exception:
            log.exception("%s stopped unexpectedly; restarting it", name)
        if time.monotonic() - started >= HEALTHY_RUN_SECONDS:
            attempt = 0
        delay = RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)]
        attempt += 1
        await asyncio.sleep(delay)


async def main() -> None:
    import headset
    import satellite
    senses = Senses()
    async with aiohttp.ClientSession() as http:
        output = HubOutput(http)
        server_room = Assistant(senses, output, http, wake_spotter=senses.wake_spotter)
        server_room.act = lambda command: room_command(server_room, output, command)
        await asyncio.gather(keep_running("the server mic", HubLink(server_room).keep_listening),
                             keep_running("the room assistant", server_room.handle_utterances),
                             keep_running("the headset server", lambda: headset.serve(senses, http)),
                             keep_running("the speech server", lambda: satellite.serve(senses)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    import arty_voice
    asyncio.run(arty_voice.main())
