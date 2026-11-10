import asyncio
import io
import json
import os
import sys
import types
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import numpy
import pytest

import satellite

VOICE_RATE = 22050


class FakeSenses:
    command_model = "small.en"

    def __init__(self):
        self.heard = []
        self.said = []

    def transcribe(self, model, audio, beam_size, primer=None):
        self.heard.append((model, audio))
        return "what time is it"

    def wav(self, text):
        self.said.append(text)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(VOICE_RATE)
            writer.writeframes(b"\x01\x00" * VOICE_RATE * 2)
        return buffer.getvalue()


def event(kind, data=None, payload=b""):
    return satellite.encode_event(kind, data, payload)


async def talk(senses, allowed, *events, replies=1):
    server = await satellite.start_server(senses, allowed, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for each in events:
            writer.write(each)
        await writer.drain()
        answers = []
        for _ in range(replies):
            answers.append(await asyncio.wait_for(satellite.read_event(reader), 5))
        return answers
    finally:
        writer.close()
        server.close()
        await server.wait_closed()


def run(coroutine):
    return asyncio.run(coroutine)


def test_describe_names_arty_for_speech_to_text_and_text_to_speech():
    [(kind, info, _)] = run(talk(FakeSenses(), {"127.0.0.1"}, event("describe")))
    assert kind == "info"
    assert info["asr"][0]["models"][0]["languages"] == ["en"]
    assert info["tts"][0]["voices"][0]["name"] == "arty"


def test_what_the_speaker_hears_is_transcribed_at_whispers_rate():
    senses = FakeSenses()
    one_second_48k_stereo = numpy.zeros(48000 * 2, dtype="<i2").tobytes()
    [(kind, data, _)] = run(talk(senses, {"127.0.0.1"},
                                 event("transcribe", {"language": "en"}),
                                 event("audio-start", {"rate": 48000, "width": 2, "channels": 2}),
                                 event("audio-chunk", {"rate": 48000, "width": 2, "channels": 2}, one_second_48k_stereo),
                                 event("audio-stop")))
    assert (kind, data["text"]) == ("transcript", "what time is it")
    model, audio = senses.heard[0]
    assert model == "small.en"
    assert len(audio) == satellite.WHISPER_RATE * 2


def test_arty_speaks_back_in_its_own_voice_a_second_at_a_time():
    senses = FakeSenses()
    answers = run(talk(senses, {"127.0.0.1"}, event("synthesize", {"text": "It's **ten** past four."}), replies=4))
    assert [kind for kind, _, _ in answers] == ["audio-start", "audio-chunk", "audio-chunk", "audio-stop"]
    assert answers[0][1] == {"rate": VOICE_RATE, "width": 2, "channels": 1}
    assert sum(len(payload) for _, _, payload in answers) == VOICE_RATE * 2 * 2
    assert senses.said == ["It's ten past four."]


def test_nothing_to_say_is_an_error_not_silence():
    [(kind, data, _)] = run(talk(FakeSenses(), {"127.0.0.1"}, event("synthesize", {"text": "  "})))
    assert kind == "error"


def test_an_address_not_on_the_list_is_hung_up_on():
    with pytest.raises((satellite.HungUp, ConnectionError)):
        run(talk(FakeSenses(), {"192.0.2.50"}, event("describe")))


def test_a_huge_header_is_hung_up_on():
    with pytest.raises((satellite.HungUp, ConnectionError)):
        run(talk(FakeSenses(), {"127.0.0.1"}, b"x" * (satellite.MAX_HEADER_BYTES + 10) + b"\n"))


def test_without_an_allow_list_it_does_not_listen_at_all():
    run(asyncio.wait_for(satellite.serve(FakeSenses(), allowed=set()), 1))


def test_the_allow_list_is_read_from_a_comma_list():
    assert satellite.parse_allow(" 192.0.2.7, 192.0.2.8 ,,") == {"192.0.2.7", "192.0.2.8"}


def test_events_round_trip_with_data_and_payload():
    async def read_back():
        reader = asyncio.StreamReader()
        reader.feed_data(event("audio-chunk", {"rate": 16000}, b"\x00\x01"))
        reader.feed_eof()
        return await satellite.read_event(reader)
    assert run(read_back()) == ("audio-chunk", {"rate": 16000}, b"\x00\x01")
