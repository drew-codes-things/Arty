import math
import os
import sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))

from listening import FRAME_BYTES, HUB_RATE, WHISPER_RATE, Downsampler, UtteranceCollector
from speaking import SentenceBuffer, might_be_ignored, plain_speech
from wake import find_wake, has_words, is_filler


def test_wake_phrase_variants():
    assert find_wake("Hey Arty, what's the weather?") == (True, "what's the weather?")
    assert find_wake("hey artie turn the lights off") == (True, "turn the lights off")
    assert find_wake("Hi, Arty.") == (True, "")
    assert find_wake("OK Arty set a timer") == (True, "set a timer")


def test_ordinary_speech_does_not_wake():
    assert find_wake("the party was great")[0] is False
    assert find_wake("Arthur came round")[0] is False
    assert find_wake("")[0] is False


def test_has_words():
    assert has_words("lights off")
    assert not has_words("?")
    assert not has_words("")


def test_downsampler_keeps_leftover_samples():
    downsampler = Downsampler()
    first = downsampler.feed(numpy.ones(4, dtype=numpy.int16).tobytes())
    second = downsampler.feed(numpy.ones(2, dtype=numpy.int16).tobytes())
    assert len(first) == 2 and len(second) == 2


def tone(seconds: float, rate: int = WHISPER_RATE) -> bytes:
    samples = numpy.arange(int(seconds * rate))
    wave = 8000 * numpy.sin(2 * math.pi * 220 * samples / rate) * (1 + 0.5 * numpy.sin(2 * math.pi * 3 * samples / rate))
    return wave.astype(numpy.int16).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * WHISPER_RATE) * 2)


def test_collector_ignores_silence():
    assert UtteranceCollector(0).feed(silence(3)) == []


def test_collector_returns_one_utterance_after_trailing_silence():
    collector = UtteranceCollector(0)
    found = collector.feed(silence(0.5) + tone(1.5) + silence(1.5))
    assert len(found) == 1
    assert len(found[0]) % FRAME_BYTES == 0
    assert len(found[0]) >= len(tone(1.5))


def test_collector_cuts_very_long_speech():
    collector = UtteranceCollector(0)
    found = collector.feed(tone(20))
    assert len(found) == 1
    assert len(found[0]) <= 15 * WHISPER_RATE * 2


def test_hub_rate_is_a_multiple():
    assert HUB_RATE % WHISPER_RATE == 0


def test_plain_speech_strips_markup():
    text = "**Sure.** Here's [the forecast](https://x.y) 🌧️\n- rain at 3\n- see https://example.com"
    assert plain_speech(text) == "Sure. Here's the forecast rain at 3 see a link"


def test_sentence_buffer_groups_short_sentences():
    buffer = SentenceBuffer()
    assert buffer.add("Yes. It is. ") == []
    chunks = buffer.add("The kettle has been on for a while now, so it should be ready. And then")
    assert chunks == ["Yes. It is. The kettle has been on for a while now, so it should be ready."]
    assert buffer.flush() == ["And then"]


def test_sentence_buffer_flush_empty():
    assert SentenceBuffer().flush() == []


def test_switch_phrases():
    from wake import find_switch
    assert find_switch("Arty on.") is True
    assert find_switch("Hey Arty, on!") is True
    assert find_switch("Arty, off.") is False
    assert find_switch("Artie turn off") is False
    assert find_switch("Arty of.") is False


def test_sentences_with_on_or_off_are_not_switches():
    from wake import find_switch
    for text in ("Hey Arty, turn on the lights.", "Arty, what's on tonight?", "arty online", "Party on!", "Hey Arty"):
        assert find_switch(text) is None


def test_overheard_filler_is_not_a_request():
    for text in ("Oh my God.", "Up.", "Wait.", "hahaha", "Oh.", "Huh? What?"):
        assert is_filler(text), text
    for text in ("That's my point about the weather?", "what time is it", "turn it up", "stop"):
        assert not is_filler(text), text


def test_not_for_me_reply_is_held_back_until_it_differs():
    assert might_be_ignored("")
    assert might_be_ignored("(not f")
    assert might_be_ignored(" (not for me) ")
    assert not might_be_ignored("(not for me) but")
    assert not might_be_ignored("It's 3 o'clock.")
