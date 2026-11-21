import asyncio
import io
import os
import sys
import time
import types
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import numpy
import pytest

import arty_voice
from barge_in import BargeIn
from listening import FRAME_BYTES, WHISPER_RATE, UtteranceCollector
from speaking import SentenceBuffer, SpokenTurn, wav_seconds, words_heard

FIRST_SENTENCE = "This is the first thing I wanted to tell you about today."
SECOND_SENTENCE = "And this is the second thing, which comes right after it."
THIRD_SENTENCE = "Finally here is the third thing, which you will not hear."
SENTENCE_SECONDS = 1.0


class FakeSenses:

    def __init__(self, synthesis_log=None):
        self.synthesis_log = synthesis_log if synthesis_log is not None else []

    def wav(self, text):
        self.synthesis_log.append((time.monotonic(), text))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(WHISPER_RATE)
            writer.writeframes(bytes(int(SENTENCE_SECONDS * WHISPER_RATE) * 2))
        return buffer.getvalue()

    def new_collector(self):
        return UtteranceCollector(0)


class SlowOutput:

    def __init__(self):
        self.played = []
        self.states = []
        self.stops = 0
        self.release_play = asyncio.Event()
        self.hold_plays = True
        self.play_started = asyncio.Event()

    async def play(self, wav):
        self.played.append(wav)
        self.play_started.set()
        if self.hold_plays:
            await self.release_play.wait()

    async def stop(self):
        self.stops += 1

    async def state(self, name, text="", seconds=0):
        self.states.append(name)

    async def wake_changed(self, enabled):
        return None


class FakeBarge:

    def __init__(self, found=None):
        self.found = found
        self.fed = []
        self.references = []
        self.turns_reset = 0
        self.rooms_forgotten = 0
        self.pause_frames = 25
        self.echo_delay_ms = 200.0

    def feed(self, audio):
        self.fed.append(audio)
        return self.found

    def play(self, wav):
        self.references.append(wav)

    def reset_turn(self):
        self.turns_reset += 1

    def forget_room(self):
        self.rooms_forgotten += 1


def streamed(*pieces, then_wait=None):
    async def brain(_question):
        for piece in pieces:
            yield piece
        if then_wait is not None:
            await then_wait.wait()
    return brain


def test_the_first_sentence_is_spoken_before_the_reply_has_finished_streaming(monkeypatch):
    async def run():
        output = SlowOutput()
        output.hold_plays = False
        assistant = arty_voice.Assistant(FakeSenses(), output, None)
        reply_may_finish = asyncio.Event()
        monkeypatch.setattr(assistant, "ask_brain", streamed(FIRST_SENTENCE + " ", then_wait=reply_may_finish))
        answering = asyncio.create_task(assistant.answer("hello"))
        await asyncio.wait_for(output.play_started.wait(), 2)
        spoken_while_streaming = len(output.played)
        reply_may_finish.set()
        await asyncio.wait_for(answering, 2)
        return spoken_while_streaming

    assert asyncio.run(run()) == 1


def test_a_short_first_sentence_is_not_held_back_for_a_longer_chunk():
    buffer = SentenceBuffer()
    assert buffer.add("Sure thing, I can do that. ") == ["Sure thing, I can do that."]
    assert buffer.add("Here is another one. ") == []


def test_a_long_sentence_without_a_full_stop_is_cut_at_a_comma():
    buffer = SentenceBuffer()
    long_run = "Well, " + "this keeps going and going, " * 6 + "and then it stops"
    chunks = buffer.add(long_run)
    assert len(chunks) == 1 and chunks[0].endswith(",")
    assert buffer.flush() == ["and then it stops"]


def test_the_next_sentence_is_prepared_while_the_current_one_plays(monkeypatch):
    async def run():
        log = []
        output = SlowOutput()
        assistant = arty_voice.Assistant(FakeSenses(log), output, None)
        monkeypatch.setattr(assistant, "ask_brain", streamed(f"{FIRST_SENTENCE} {SECOND_SENTENCE} {THIRD_SENTENCE}"))
        answering = asyncio.create_task(assistant.answer("hello"))
        await asyncio.wait_for(output.play_started.wait(), 2)
        await asyncio.sleep(0.2)
        prepared_during_first = len(log)
        output.hold_plays = False
        output.release_play.set()
        await asyncio.wait_for(answering, 2)
        return prepared_during_first, len(output.played)

    prepared_during_first, played = asyncio.run(run())
    assert prepared_during_first >= 2
    assert played == 2


def run_interrupted_turn(monkeypatch, barge=None):
    monkeypatch.setattr(arty_voice, "PLAYBACK_START_SECONDS", 0.0)

    async def run():
        output = SlowOutput()
        assistant = arty_voice.Assistant(FakeSenses(), output, None, barge=barge)
        monkeypatch.setattr(assistant, "ask_brain", streamed(f"{FIRST_SENTENCE} {SECOND_SENTENCE} {THIRD_SENTENCE}"))

        async def turn(_utterance):
            await assistant.answer("tell me things")

        monkeypatch.setattr(assistant, "handle_utterance", turn)
        worker = asyncio.create_task(assistant.handle_utterances())
        assistant.hear(b"asked")
        await asyncio.wait_for(output.play_started.wait(), 2)
        await asyncio.sleep(0.4)
        began = time.monotonic()
        stopped = await assistant.interrupt()
        await asyncio.sleep(0.05)
        seconds = time.monotonic() - began
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        return assistant, output, stopped, seconds
    return asyncio.run(run())


def test_talking_over_her_stops_the_speakers_and_keeps_what_she_had_said(monkeypatch):
    assistant, output, stopped, seconds = run_interrupted_turn(monkeypatch)
    assert stopped is True
    assert output.stops == 1
    assert len(output.played) == 1
    assert seconds < 0.5
    remembered = assistant.conversation.history()
    assert remembered[0] == {"role": "user", "content": "tell me things"}
    assert remembered[1]["role"] == "assistant"
    assert remembered[1]["content"].endswith("...")
    assert remembered[1]["content"].startswith("This is the")
    assert FIRST_SENTENCE not in remembered[1]["content"]
    assert THIRD_SENTENCE not in remembered[1]["content"]
    assert SECOND_SENTENCE not in remembered[1]["content"]


def test_after_being_talked_over_she_listens_without_the_wake_word_and_is_not_muted(monkeypatch):
    barge = FakeBarge()
    assistant, output, _, _ = run_interrupted_turn(monkeypatch, barge)
    assert assistant.speaking is False
    assert assistant.muted() is False
    assert assistant.interrupted is False
    assert assistant.listening_until > time.time() + arty_voice.LISTEN_AFTER_BARGE_SECONDS - 2
    assert output.states[-1] == "listening"
    assert barge.turns_reset == 1


def test_the_spoken_sentence_is_given_to_the_echo_detector_before_it_plays(monkeypatch):
    barge = FakeBarge()
    run_interrupted_turn(monkeypatch, barge)
    assert barge.references and barge.references[0].startswith(b"RIFF")
    assert len(barge.references) == 1


def test_nothing_is_interrupted_when_she_is_not_speaking():
    async def run():
        assistant = arty_voice.Assistant(FakeSenses(), SlowOutput(), None)
        return await assistant.interrupt()

    assert asyncio.run(run()) is False


def test_a_second_interruption_while_stopping_is_ignored(monkeypatch):
    async def run():
        output = SlowOutput()
        assistant = arty_voice.Assistant(FakeSenses(), output, None)
        monkeypatch.setattr(assistant, "ask_brain", streamed(f"{FIRST_SENTENCE} {SECOND_SENTENCE}"))

        async def turn(_utterance):
            await assistant.answer("go on")

        monkeypatch.setattr(assistant, "handle_utterance", turn)
        worker = asyncio.create_task(assistant.handle_utterances())
        assistant.hear(b"asked")
        await asyncio.wait_for(output.play_started.wait(), 2)
        results = await asyncio.gather(assistant.interrupt(), assistant.interrupt())
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        return results, output.stops

    results, stops = asyncio.run(run())
    assert sorted(results) == [False, True]
    assert stops == 1


def test_a_failing_stop_request_still_ends_the_turn(monkeypatch):
    async def run():
        output = SlowOutput()

        async def broken_stop():
            raise ConnectionError("hub unreachable")

        output.stop = broken_stop
        assistant = arty_voice.Assistant(FakeSenses(), output, None)
        monkeypatch.setattr(assistant, "ask_brain", streamed(f"{FIRST_SENTENCE} {SECOND_SENTENCE}"))

        async def turn(_utterance):
            await assistant.answer("go on")

        monkeypatch.setattr(assistant, "handle_utterance", turn)
        worker = asyncio.create_task(assistant.handle_utterances())
        assistant.hear(b"asked")
        await asyncio.wait_for(output.play_started.wait(), 2)
        stopped = await assistant.interrupt()
        await asyncio.sleep(0.05)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        return stopped, assistant.speaking

    assert asyncio.run(run()) == (True, False)


def tone(seconds):
    samples = numpy.arange(int(seconds * WHISPER_RATE))
    wave = 8000 * numpy.sin(2 * numpy.pi * 220 * samples / WHISPER_RATE) * (1 + 0.5 * numpy.sin(2 * numpy.pi * 3 * samples / WHISPER_RATE))
    return wave.astype(numpy.int16).tobytes()


def silence(seconds):
    return bytes(int(seconds * WHISPER_RATE) * 2)


class RecordingAssistant:

    def __init__(self, speaking=True, interrupts=True):
        self.speaking = speaking
        self.barge = None
        self.heard = []
        self.interrupts = interrupts
        self.interrupted = 0

    async def interrupt(self):
        self.interrupted += 1
        return self.interrupts

    def hear(self, utterance):
        self.heard.append(utterance)


def link_with(assistant, barge):
    assistant.barge = barge
    return arty_voice.HubLink(assistant)


def test_what_you_said_while_she_was_talking_reaches_the_collector_as_your_request():
    async def run():
        barge = FakeBarge(BargeIn(tone(1.0) + silence(1.5), 20))
        assistant = RecordingAssistant()
        link = link_with(assistant, barge)
        await link.listen_for_barge_in(silence(0.1), UtteranceCollector(0))
        return assistant

    assistant = asyncio.run(run())
    assert assistant.interrupted == 1
    assert len(assistant.heard) == 1
    assert len(assistant.heard[0]) >= len(tone(1.0))


def test_a_pause_of_half_a_second_in_the_middle_of_what_you_say_does_not_split_it():
    collector = UtteranceCollector(0)
    found = collector.feed(silence(0.3) + tone(1.0) + silence(0.5) + tone(1.0) + silence(1.5))
    assert len(found) == 1
    assert len(found[0]) >= len(tone(1.0)) * 2


class ScriptedJudge:

    def __init__(self):
        self.asked_after_frames = []
        self.collector = None

    def finished(self, audio):
        self.asked_after_frames.append(self.collector.silence_run)
        return True


def judged_collector():
    judge = ScriptedJudge()
    collector = UtteranceCollector(0, turn=judge)
    judge.collector = collector
    return collector, judge


def test_after_a_barge_in_the_end_of_request_judge_waits_for_a_longer_pause_before_its_first_check():
    collector, judge = judged_collector()
    collector.wait_longer_before_judging(25)
    found = collector.feed(tone(1.0) + silence(1.5))
    assert len(found) == 1
    assert judge.asked_after_frames[0] == 25


def test_the_longer_wait_applies_to_one_request_only():
    collector, judge = judged_collector()
    collector.wait_longer_before_judging(25)
    collector.feed(tone(1.0) + silence(1.5))
    judge.asked_after_frames.clear()
    collector.feed(tone(1.0) + silence(1.5))
    assert judge.asked_after_frames[0] == 10


def test_a_longer_wait_never_makes_the_normal_wait_shorter():
    collector = UtteranceCollector(0, pause_frames=15)
    collector.wait_longer_before_judging(5)
    assert collector.pause_frames == 15


def test_the_barge_in_pause_is_handed_to_the_collector_when_you_cut_in():
    async def run():
        barge = FakeBarge(BargeIn(tone(0.5), 15))
        assistant = RecordingAssistant()
        collector, _ = judged_collector()
        await link_with(assistant, barge).listen_for_barge_in(silence(0.1), collector)
        return collector

    assert asyncio.run(run()).pause_frames == 25


def test_the_microphone_is_not_checked_for_barge_in_while_she_is_quiet():
    async def run():
        barge = FakeBarge(BargeIn(b"", 0))
        assistant = RecordingAssistant(speaking=False)
        link = link_with(assistant, barge)
        await link.listen_for_barge_in(silence(0.1), UtteranceCollector(0))
        return barge, assistant

    barge, assistant = asyncio.run(run())
    assert barge.fed == []
    assert assistant.interrupted == 0


def test_a_refused_interruption_does_not_hand_audio_to_the_collector():
    async def run():
        barge = FakeBarge(BargeIn(tone(1.0) + silence(1.5), 20))
        assistant = RecordingAssistant(interrupts=False)
        await link_with(assistant, barge).listen_for_barge_in(silence(0.1), UtteranceCollector(0))
        return assistant

    assert asyncio.run(run()).heard == []


def test_no_detector_means_the_old_behaviour_of_ignoring_the_mic_while_speaking():
    async def run():
        assistant = RecordingAssistant()
        await link_with(assistant, None).listen_for_barge_in(silence(0.1), UtteranceCollector(0))
        return assistant

    assert asyncio.run(run()).interrupted == 0


def test_the_words_she_had_got_through_are_counted_by_how_long_the_sentence_has_been_playing():
    turn = SpokenTurn()
    turn.begin("one two three four", 2.0, 10.0)
    turn.end()
    turn.begin("five six seven eight nine ten", 3.0, 12.0)
    assert turn.heard(13.0) == "one two three four five six"
    assert turn.heard(11.0) == "one two three four"
    assert SpokenTurn().heard(5.0) == ""


def test_a_sentence_that_has_not_started_playing_is_not_counted_as_heard():
    turn = SpokenTurn()
    turn.begin("alpha beta gamma delta", 4.0, 100.0)
    assert turn.heard(100.0, latency=0.5) == ""
    assert words_heard("alpha beta gamma delta", 2.0) == "alpha beta gamma delta"
    assert words_heard("alpha beta", -1.0) == ""


def test_the_length_of_a_spoken_sentence_comes_from_the_wav_or_is_guessed_from_the_words():
    import io
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(bytes(16000 * 2 * 2))
    assert wav_seconds(buffer.getvalue(), "ignored") == pytest.approx(2.0)
    assert wav_seconds(b"not a wav", "one two three four five") == pytest.approx(2.0)
