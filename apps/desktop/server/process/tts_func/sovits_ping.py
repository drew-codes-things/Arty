"""GPT-SoVITS HTTP client."""
import time

import requests

from process.config import load_config

char_config = load_config()
_cfg = char_config["sovits_ping_config"]

API_URL = _cfg.get("api_url", "http://127.0.0.1:9880/tts")


class SovitsError(RuntimeError):
    pass


def build_payload(in_text):
    payload = {
        "text": in_text,
        "text_lang": _cfg["text_lang"],
        "ref_audio_path": _cfg["ref_audio_path"],
        "prompt_text": _cfg["prompt_text"],
        "prompt_lang": _cfg["prompt_lang"],
        "media_type": "wav",
        "streaming_mode": False,
    }
    for key in (
        "text_split_method",
        "speed_factor",
        "temperature",
        "top_k",
        "top_p",
        "batch_size",
        "seed",
    ):
        if key in _cfg and _cfg[key] is not None:
            payload[key] = _cfg[key]
    return payload


def sovits_gen_bytes(in_text, timeout=120):
    if not in_text or not in_text.strip():
        raise SovitsError("Refusing to synthesize empty text.")

    try:
        response = requests.post(API_URL, json=build_payload(in_text), timeout=timeout)
    except requests.RequestException as e:
        raise SovitsError(f"Could not reach GPT-SoVITS at {API_URL}: {e}") from e

    ctype = response.headers.get("content-type", "")
    if response.status_code != 200 or "application/json" in ctype:
        detail = response.text[:500]
        raise SovitsError(f"GPT-SoVITS returned {response.status_code}: {detail}")

    if not response.content:
        raise SovitsError("GPT-SoVITS returned an empty body.")

    return response.content


def sovits_gen(in_text, output_wav_pth="output.wav"):
    try:
        audio = sovits_gen_bytes(in_text)
    except SovitsError as e:
        print("Error in sovits_gen:", e)
        return None

    with open(output_wav_pth, "wb") as f:
        f.write(audio)
    return output_wav_pth


def play_audio(path):
    import soundfile as sf
    import sounddevice as sd

    data, samplerate = sf.read(path)
    sd.play(data, samplerate)
    sd.wait()


if __name__ == "__main__":
    start = time.time()
    print(f"Pinging {API_URL} ...")
    path = sovits_gen("If you hear this, that means it is set up correctly.", "output.wav")
    print(f"Elapsed: {time.time() - start:.2f}s -> {path}")
