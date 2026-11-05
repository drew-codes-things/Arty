"""Prints the speech in an audio or video file as timestamped lines.

Run by media.py with the faster-whisper venv's Python (provision.sh creates /srv/ai/media/venv),
so the brain itself keeps to the standard library. Runs on the CPU, because Ollama has the GPU.
"""
import os
import sys

from faster_whisper import WhisperModel

MODEL = os.environ.get("WHISPER_MODEL", "small")
MODELS_DIR = os.environ.get("WHISPER_MODELS_DIR", "/srv/ai/media/models")


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: transcribe.py <audio-or-video-file>", file=sys.stderr)
        return 2
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", download_root=MODELS_DIR, cpu_threads=os.cpu_count() or 4)
    segments, info = model.transcribe(sys.argv[1], vad_filter=True, beam_size=1)
    print(f"(language: {info.language})")
    for segment in segments:
        text = segment.text.strip()
        if text:
            print(f"[{stamp(segment.start)}] {text}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
