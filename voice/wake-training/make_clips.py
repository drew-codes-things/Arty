import random
import sys
from pathlib import Path

import numpy
from kokoro_onnx import Kokoro

MODELS = Path.home() / "Projects/arty-14b/apps/desktop/models/kokoro"
OUT = Path(sys.argv[1])
SEED = 7

WAKE = ["Hey Arty", "Hey Arty.", "Hey Arty!", "Hey, Arty", "Hey Arty?", "Hey Artie", "Hey Arty,", "hey arty"]
COMMANDS = ["what's the time", "play the next song", "turn it up", "what's the weather like", "pause",
            "open Beat Saber", "set a timer for ten minutes", "what does ephemeral mean", "skip this song",
            "tell me a joke", "is the server up", "play Timber by Pitbull on YouTube"]
NEAR = ["Hey Marty", "Hey party", "Hey smarty", "Hey Archie", "Hey Artemis", "Hey everybody", "Hey there",
        "Hey Siri", "Hey Barry", "Hey Harvey", "Hey Andy", "Hey Martin", "Arty", "Arty, play the next song",
        "a hearty meal", "the party", "hey hey", "Okay", "Hey Alexa", "Hey Google", "Hey Jarvis", "art gallery",
        "hey, are you there", "Hey Karty", "Hey dirty", "Hey thirty", "forty", "Smarty pants", "Hey Artur",
        "hearty", "Hey Arnie", "Hey Aunty", "Hey Avery"]
OTHER = ["what's the time", "play the next song", "I think we should leave soon", "can you pass the salt",
         "the weather is nice today", "turn the lights off", "where are my keys", "dinner is ready",
         "let's watch a film tonight", "I'm going to the shop", "that was a great game", "call me later",
         "the meeting starts at nine", "have you seen the cat", "it is raining again", "good morning everyone",
         "open the window please", "how was school", "I need a coffee", "see you tomorrow",
         "what are you doing", "this is the best song", "turn it down a bit", "the car needs petrol",
         "happy birthday", "are you hungry", "we won the match", "put the kettle on", "where is the remote",
         "I'll be back in five minutes", "the party was fun", "artificial intelligence", "it's a work of art"]


def voices(kokoro, rng):
    names = kokoro.get_voices()
    english = [v for v in names if v[:2] in ("af", "am", "bf", "bm")]
    picks = [(v, kokoro.get_voice_style(v), "en-gb" if v.startswith("b") else "en-us") for v in names]
    for _ in range(40):
        first, second = rng.sample(english, 2)
        weight = rng.uniform(0.25, 0.75)
        style = kokoro.get_voice_style(first) * weight + kokoro.get_voice_style(second) * (1 - weight)
        picks.append((f"{first}+{second}", style, "en-gb" if first.startswith("b") else "en-us"))
    return picks


def main():
    rng = random.Random(SEED)
    kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    clips, labels, speakers, texts = [], [], [], []
    for name, style, lang in voices(kokoro, rng):
        jobs = []
        for _ in range(4):
            jobs.append((rng.choice(WAKE), 1))
        for _ in range(2):
            jobs.append((f"{rng.choice(WAKE)}, {rng.choice(COMMANDS)}", 2))
        for _ in range(4):
            jobs.append((rng.choice(NEAR), 0))
        for _ in range(3):
            jobs.append((rng.choice(OTHER), 0))
        for text, label in jobs:
            audio, rate = kokoro.create(text, voice=style, speed=rng.uniform(0.8, 1.25), lang=lang)
            assert rate == 24000
            clips.append(numpy.asarray(audio, dtype=numpy.float32))
            labels.append(label)
            speakers.append(name)
            texts.append(text)
        print(name, len(clips), flush=True)
    lengths = numpy.array([len(c) for c in clips])
    numpy.savez_compressed(OUT, audio=numpy.concatenate(clips), lengths=lengths, labels=numpy.array(labels),
                           speakers=numpy.array(speakers), texts=numpy.array(texts))


main()
