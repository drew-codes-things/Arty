import sys
import wave
from pathlib import Path

import numpy
import onnxruntime
from scipy.signal import fftconvolve, resample_poly
from sklearn.neural_network import MLPClassifier

RATE = 16000
FIRST = 12400
HOP = 1280
FRAMES = 16
WINDOW = FIRST + (FRAMES - 1) * HOP
LEAD = WINDOW - FIRST
rng = numpy.random.default_rng(11)
SOUNDS_LIKE_THE_WAKE_WORD = {"hey rt"}

data = numpy.load(sys.argv[1])
embedding_path = Path(sys.argv[2])
out = Path(sys.argv[3])
samples_folder = Path(sys.argv[4]) if len(sys.argv) > 4 else None
options = onnxruntime.SessionOptions()
options.intra_op_num_threads = 2
session = onnxruntime.InferenceSession(str(embedding_path), sess_options=options)
input_name = session.get_inputs()[0].name


def embed(samples):
    return session.run(None, {input_name: samples[numpy.newaxis].astype(numpy.float32)})[0][0, :, 0, :]


def windows_of(frames):
    return [frames[i:i + FRAMES].reshape(-1) for i in range(0, len(frames) - FRAMES + 1)]


def augment(audio):
    audio = audio * rng.uniform(0.2, 1.0) / max(1e-4, numpy.abs(audio).max())
    if rng.random() < 0.5:
        tail = numpy.exp(-numpy.arange(int(RATE * rng.uniform(0.05, 0.4))) / (RATE * rng.uniform(0.02, 0.12)))
        impulse = rng.standard_normal(len(tail)) * tail
        impulse[0] = 1.0
        wet = fftconvolve(audio, impulse)[:len(audio)]
        audio = wet / max(1e-4, numpy.abs(wet).max()) * numpy.abs(audio).max()
    noise = rng.standard_normal(len(audio))
    if rng.random() < 0.5:
        noise = numpy.cumsum(noise)
        noise -= numpy.convolve(noise, numpy.ones(400) / 400, mode="same")
    snr = rng.uniform(5, 35)
    noise *= numpy.sqrt(numpy.mean(audio ** 2) / (numpy.mean(noise ** 2) + 1e-12) / 10 ** (snr / 10))
    return numpy.clip(audio + noise, -1, 1).astype(numpy.float32)


def positive_window(audio):
    tail = numpy.zeros(int(RATE * rng.uniform(0.0, 0.25)), dtype=numpy.float32)
    clip = numpy.concatenate([audio, tail])[-WINDOW:]
    return numpy.concatenate([numpy.zeros(WINDOW - len(clip), dtype=numpy.float32), clip])


def sliding(audio):
    padded = numpy.concatenate([numpy.zeros(LEAD, dtype=numpy.float32), audio, numpy.zeros(RATE // 3, dtype=numpy.float32)])
    return windows_of(embed(padded))


starts = numpy.concatenate([[0], numpy.cumsum(data["lengths"])[:-1]])
clips = [resample_poly(data["audio"][s:s + n], 2, 3).astype(numpy.float32) for s, n in zip(starts, data["lengths"])]
labels, speakers = data["labels"], data["speakers"]
kept = [i for i, text in enumerate(data["texts"]) if str(text).lower() not in SOUNDS_LIKE_THE_WAKE_WORD]
clips, labels, speakers = [clips[i] for i in kept], labels[kept], speakers[kept]
names = sorted(set(speakers))
held = set(numpy.random.default_rng(3).choice(names, size=len(names) // 5, replace=False))
print(f"{len(clips)} clips, {len(names)} speakers, {len(held)} held out")

train_x, train_y, test = [], [], {"pos": [], "pos_cmd": [], "neg": []}
for audio, label, speaker in zip(clips, labels, speakers):
    is_test = speaker in held
    copies = 1 if is_test else 3
    for copy in range(copies):
        sample = audio if (is_test or copy == 0) else augment(audio)
        if label == 1:
            vector = windows_of(embed(positive_window(sample)))[0]
            (test["pos"].append([vector]) if is_test else (train_x.append(vector), train_y.append(1)))
        elif label == 2:
            if is_test:
                test["pos_cmd"].append(sliding(sample))
        else:
            vectors = sliding(sample)
            if is_test:
                test["neg"].append(vectors)
            else:
                train_x.extend(vectors)
                train_y.extend([0] * len(vectors))
for _ in range(400):
    noise = augment(numpy.zeros(RATE * 2, dtype=numpy.float32) + rng.standard_normal(RATE * 2).astype(numpy.float32) * 0.01)
    vectors = sliding(noise)
    train_x.extend(vectors)
    train_y.extend([0] * len(vectors))

train_x, train_y = numpy.array(train_x), numpy.array(train_y)
mean, scale = train_x.mean(axis=0), train_x.std(axis=0) + 1e-6
print(f"train windows {len(train_y)}, positives {int(train_y.sum())}")
weights = numpy.where(train_y == 1, (train_y == 0).sum() / max(1, train_y.sum()), 1.0)
model = MLPClassifier(hidden_layer_sizes=(64,), alpha=1e-3, max_iter=200, random_state=0, early_stopping=False)
balanced_x = numpy.concatenate([train_x[train_y == 0], numpy.repeat(train_x[train_y == 1], int(weights.max()), axis=0)])
balanced_y = numpy.concatenate([numpy.zeros((train_y == 0).sum()), numpy.ones((train_y == 1).sum() * int(weights.max()))])
model.fit((balanced_x - mean) / scale, balanced_y)


def score(vectors):
    return float(model.predict_proba((numpy.array(vectors) - mean) / scale)[:, 1].max())


for threshold in (0.5, 0.7, 0.9):
    hits = numpy.mean([score(v) >= threshold for v in test["pos"]])
    hits_cmd = numpy.mean([score(v) >= threshold for v in test["pos_cmd"]])
    false = numpy.mean([score(v) >= threshold for v in test["neg"]])
    print(f"threshold {threshold}: held-out wake alone {hits:.1%}, wake + command {hits_cmd:.1%}, "
          f"negative clips firing {false:.1%} of {len(test['neg'])}")
def save_samples(folder):
    folder.mkdir(parents=True, exist_ok=True)
    counts = {"wake": 0, "other": 0}
    for audio, label, speaker in zip(clips, labels, speakers):
        if speaker not in held:
            continue
        kind = "wake" if label else "other"
        counts[kind] += 1
        with wave.open(str(folder / f"{kind}-{counts[kind]:03d}.wav"), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(RATE)
            writer.writeframes((numpy.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"saved {counts['wake']} held-out wake clips and {counts['other']} others to {folder}")


if samples_folder:
    save_samples(samples_folder)
numpy.savez(out, mean=mean.astype(numpy.float32), scale=scale.astype(numpy.float32),
            hidden_weights=model.coefs_[0].astype(numpy.float32), hidden_bias=model.intercepts_[0].astype(numpy.float32),
            output_weights=model.coefs_[1].astype(numpy.float32), output_bias=model.intercepts_[1].astype(numpy.float32))
print("saved", out)
