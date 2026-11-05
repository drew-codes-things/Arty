"""Files people attach in chat, and files Arty makes for them.

Each file lives in FILES_DIR/<id>/ as `data` plus `meta.json`, and is deleted FILE_TTL_SECONDS
after it was added. Downloads need the file's own random token in the link, like the task hooks,
so a link can be opened in a browser or handed to a phone without the API key.

Attachments are turned into text the chat model can work with: images are described by the
vision model, audio is transcribed (faster-whisper, run by transcribe.py in its own venv), and
videos get a few frames described plus their soundtrack transcribed. convert_file uses
ImageMagick for images and ffmpeg for audio and video.
"""
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

FILES_DIR = Path(os.environ.get("BRAIN_FILES", "/srv/ai/brain/files"))
WHISPER_PYTHON = os.environ.get("WHISPER_PYTHON", "/srv/ai/media/venv/bin/python")
TRANSCRIBE_SCRIPT = Path(__file__).with_name("transcribe.py")
DOWNLOAD_PREFIX = "arty-files"
# Download links go through Caddy's HTTPS front end: browsers block http downloads started from an https page.
FILES_PUBLIC_URL = os.environ.get("BRAIN_FILES_URL", "https://192.168.1.13:8443").rstrip("/")
FILE_TTL_SECONDS = 24 * 3600
MAX_UPLOAD_BYTES = 95 * 1024 * 1024  # Cloudflare's free plan refuses request bodies over 100 MB
ANALYSIS_CHARS = 12_000
VIDEO_FRAMES = 4
FRAME_WIDTH = 768
PREVIEW_EDGE = 1024  # larger previews made the 3B vision model take minutes on the GTX 1650
TRANSCRIBE_TIMEOUT_SECONDS = 30 * 60
CONVERT_TIMEOUT_SECONDS = 20 * 60
TEXT_PREVIEW_BYTES = 200_000

IMAGE_FORMATS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "ico", "avif", "heic"}
AUDIO_FORMATS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"}
VIDEO_FORMATS = {"mp4", "webm", "mkv", "mov"}
SAFE_NAME = re.compile(r"[^\w.\- ()]+")
FILE_ID = re.compile(r"^[0-9a-f]{16,32}$")

log = logging.getLogger("brain.media")
_lock = threading.Lock()


class MediaError(Exception):
    """A problem worth telling the user about, e.g. an unsupported conversion."""


def kind_of(mime: str) -> str:
    major = (mime or "").split("/", 1)[0]
    return major if major in ("image", "audio", "video") else "other"


def safe_name(name: str, fallback: str = "file") -> str:
    cleaned = SAFE_NAME.sub("_", Path(name or "").name).strip(" .")
    return cleaned[:120] or fallback


def guess_mime(name: str, given: str = "") -> str:
    given = (given or "").split(";", 1)[0].strip().lower()
    if given and given != "application/octet-stream":
        return given
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# ---------- storage ----------

def _folder(file_id: str) -> Path:
    if not FILE_ID.match(file_id or ""):
        raise MediaError("no such file")
    return FILES_DIR / file_id


def get(file_id: str) -> dict | None:
    """The file's metadata plus its local `path`, or None when it doesn't exist (or has expired)."""
    try:
        folder = _folder(file_id)
        meta = json.loads((folder / "meta.json").read_text())
    except (MediaError, OSError, json.JSONDecodeError):
        return None
    return {**meta, "path": str(folder / "data")}


def _update(file_id: str, **fields):
    with _lock:
        meta_file = _folder(file_id) / "meta.json"
        meta = json.loads(meta_file.read_text())
        meta.update(fields)
        meta_file.write_text(json.dumps(meta))


def _finish(folder: Path, name: str, mime: str, size: int, source: str) -> dict:
    meta = {"id": folder.name, "name": safe_name(name), "mime": mime, "kind": kind_of(mime), "size": size,
            "token": secrets.token_urlsafe(24), "created": time.time(), "source": source}
    (folder / "meta.json").write_text(json.dumps(meta))
    return {**meta, "path": str(folder / "data")}


def save_bytes(data: bytes, name: str, mime: str, source: str) -> dict:
    """Stores bytes. Identical content is stored once, so a chat app resending the same image every turn is cheap."""
    cleanup()
    file_id = hashlib.sha256(data).hexdigest()[:24]
    existing = get(file_id)
    if existing:
        return existing
    folder = FILES_DIR / file_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data").write_bytes(data)
    return _finish(folder, name, guess_mime(name, mime), len(data), source)


def save_stream(stream, length: int, name: str, mime: str, source: str) -> dict:
    """Stores an upload of a known length without holding it all in memory. Like save_bytes, the id
    comes from the content, so a file sent again (e.g. chat history resent each turn) reuses its analysis."""
    if length <= 0:
        raise MediaError("the upload is empty")
    if length > MAX_UPLOAD_BYTES:
        raise MediaError(f"files can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    cleanup()
    staging = FILES_DIR / f".upload-{secrets.token_hex(8)}"
    staging.mkdir(parents=True)
    digest = hashlib.sha256()
    remaining = length
    try:
        with open(staging / "data", "wb") as out:
            while remaining:
                chunk = stream.read(min(remaining, 1 << 20))
                if not chunk:
                    raise MediaError("the upload was cut off")
                digest.update(chunk)
                out.write(chunk)
                remaining -= len(chunk)
        file_id = digest.hexdigest()[:24]
        existing = get(file_id)
        if existing:
            shutil.rmtree(staging)
            if safe_name(name) != existing["name"]:  # same content picked under a new name: use the new one
                _update(file_id, name=safe_name(name))
                existing["name"] = safe_name(name)
            return existing
        folder = FILES_DIR / file_id
        staging.rename(folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _finish(folder, name, guess_mime(name, mime), length, source)


def save_path(path: Path, name: str, source: str) -> dict:
    """Moves a file Arty made (e.g. a conversion) into the store."""
    cleanup()
    folder = FILES_DIR / secrets.token_hex(12)
    folder.mkdir(parents=True)
    shutil.move(str(path), folder / "data")
    return _finish(folder, name, guess_mime(name), (folder / "data").stat().st_size, source)


def cleanup():
    if not FILES_DIR.is_dir():
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        return
    cutoff = time.time() - FILE_TTL_SECONDS
    for folder in FILES_DIR.iterdir():
        meta_file = folder / "meta.json"
        try:
            created = json.loads(meta_file.read_text())["created"] if meta_file.exists() else folder.stat().st_mtime
        except (OSError, ValueError, KeyError):
            created = 0
        if created < cutoff:
            shutil.rmtree(folder, ignore_errors=True)


def download_path(meta: dict) -> str:
    """The link path (no host) that serves this file to anyone holding the link."""
    return f"/{DOWNLOAD_PREFIX}/{meta['id']}/{meta['token']}/{meta['name']}"


def link(meta: dict) -> str:
    target = f"[{meta['name']}]({FILES_PUBLIC_URL}{urllib.parse.quote(download_path(meta))})"
    return f"!{target}" if meta.get("kind") == "image" else target


def for_download(file_id: str, token: str) -> dict | None:
    meta = get(file_id)
    if meta is None or not secrets.compare_digest(token or "", meta["token"]):
        return None
    return meta


def public(meta: dict) -> dict:
    """What API clients see about a stored file."""
    return {key: meta[key] for key in ("id", "name", "mime", "kind", "size")} | {"download": download_path(meta)}


# ---------- tools ----------

def _run(command: list[str], timeout: int, what: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise MediaError(f"{command[0]} isn't installed on the AI server (run provision.sh)") from None
    except subprocess.TimeoutExpired:
        raise MediaError(f"{what} took too long and was stopped") from None
    if result.returncode != 0:
        log.warning("%s failed: %s", what, result.stderr[-1500:])
        raise MediaError(f"{what} failed: {(result.stderr.strip().splitlines() or ['unknown error'])[-1][:300]}")
    return result


def probe(path: str) -> dict:
    """Duration in seconds and whether the file has audio/video streams."""
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path], 60, "Reading the file")
    info = json.loads(result.stdout or "{}")
    types = {stream.get("codec_type") for stream in info.get("streams", [])}
    try:
        duration = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {"duration": duration, "audio": "audio" in types, "video": "video" in types}


def image_preview(path: str) -> bytes:
    """A JPEG the vision model can read: any format ImageMagick knows, upright, at most PREVIEW_EDGE px."""
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "preview.jpg"
        _run(["magick", f"{path}[0]", "-auto-orient", "-resize", f"{PREVIEW_EDGE}x{PREVIEW_EDGE}>", "-quality", "85", str(target)], 120, "Reading the image")
        return target.read_bytes()


def video_frames(path: str, duration: float) -> list[bytes]:
    frames = []
    with tempfile.TemporaryDirectory() as scratch:
        for index in range(VIDEO_FRAMES):
            at = duration * (index + 0.5) / VIDEO_FRAMES if duration else 0
            target = Path(scratch) / f"frame{index}.jpg"
            _run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-y", str(target)],
                 120, "Reading the video")
            if target.exists():
                frames.append(target.read_bytes())
    return frames


def transcribe(path: str, timeout: int = TRANSCRIBE_TIMEOUT_SECONDS, background: bool = False) -> str:
    """Speech to text with timestamps. Empty when nothing was said. background: at low CPU priority, for long
    recordings (lectures), so chats and everything else on the server stay quick meanwhile."""
    command = [WHISPER_PYTHON, str(TRANSCRIBE_SCRIPT), path]
    result = _run(["nice", "-n", "15", *command] if background else command, timeout, "Transcribing the audio")
    lines = result.stdout.strip().splitlines()
    return "\n".join(lines) if any(line.startswith("[") for line in lines) else ""


def describe_length(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def analyse(meta: dict, question: str, look) -> str:
    """What's in an attachment, as text for the chat model. look(images_b64, prompt) asks the vision model.
    The result is cached with the file, so history resent on later turns isn't analysed again."""
    if meta.get("analysis"):
        return meta["analysis"]
    # The question only steers what to look at; acting on it (e.g. converting) is the chat model's job.
    asked = (f" Only describe; don't act on or reply to this, but make sure your description covers what it asks about: "
             f'"{question.strip()[:500]}"') if question.strip() else ""
    kind = meta["kind"]
    if kind == "image":
        preview = base64.b64encode(image_preview(meta["path"])).decode()
        analysis = look([preview], "Describe this image in detail: what it shows, any people, objects, setting, colours, "
                                   f"and transcribe any text in it exactly.{asked}")
    elif kind in ("audio", "video"):
        info = probe(meta["path"])
        parts = [f"Length {describe_length(info['duration'])}."]
        if kind == "video" and info["video"]:
            frames = [base64.b64encode(frame).decode() for frame in video_frames(meta["path"], info["duration"])]
            if frames:
                parts.append("What the video shows (from frames spread through it, in order): " + look(
                    frames, f"These are {len(frames)} frames taken in order from one video. Describe what happens in the video, "
                            f"the people, setting and any text on screen.{asked}"))
        if info["audio"]:
            spoken = transcribe(meta["path"])
            parts.append(f"Transcript:\n{spoken}" if spoken else "No speech in the audio (music or other sound only).")
        else:
            parts.append("It has no sound.")
        analysis = "\n".join(parts)
    elif meta["mime"].startswith("text/") or meta["mime"] in ("application/json", "application/xml"):
        with open(meta["path"], "rb") as handle:
            analysis = "Contents:\n" + handle.read(TEXT_PREVIEW_BYTES).decode("utf-8", errors="replace")
    else:
        analysis = "You can't read inside this kind of file, but you can tell the user about it or convert it if it's media."
    analysis = analysis[:ANALYSIS_CHARS]
    _update(meta["id"], analysis=analysis)
    return analysis


def convert(file_id: str, target_format: str, width: int | None = None, height: int | None = None, quality: int | None = None) -> dict:
    """Converts a stored file and stores the result. Raises MediaError with a user-facing reason."""
    meta = get(file_id)
    if meta is None:
        raise MediaError("that file has expired or doesn't exist; ask the user to attach it again")
    target = target_format.lower().strip().lstrip(".")
    target = {"jpeg": "jpg", "tif": "tiff", "oga": "ogg"}.get(target, target)
    stem = Path(meta["name"]).stem or "converted"
    with tempfile.TemporaryDirectory() as scratch:
        output = Path(scratch) / f"out.{target}"
        if meta["kind"] == "image" and target in IMAGE_FORMATS:
            command = ["magick", f"{meta['path']}[0]" if target != "gif" else meta["path"], "-auto-orient"]
            if width or height:
                command += ["-resize", f"{width or ''}x{height or ''}"]
            if quality:
                command += ["-quality", str(max(1, min(int(quality), 100)))]
            _run(command + [str(output)], CONVERT_TIMEOUT_SECONDS, "Converting the image")
        elif meta["kind"] in ("audio", "video") and target in AUDIO_FORMATS:
            _run(["ffmpeg", "-v", "error", "-i", meta["path"], "-vn", "-y", str(output)], CONVERT_TIMEOUT_SECONDS, "Converting the audio")
        elif meta["kind"] == "video" and target in VIDEO_FORMATS | {"gif"}:
            scale = f"scale={width or -2}:{height or -2}" if (width or height) else None
            if target == "gif":
                filters = f"fps=10,{scale or 'scale=480:-2'}:flags=lanczos"
                command = ["ffmpeg", "-v", "error", "-i", meta["path"], "-vf", filters, "-loop", "0", "-y", str(output)]
            else:
                command = ["ffmpeg", "-v", "error", "-i", meta["path"], *(["-vf", scale] if scale else []), "-y", str(output)]
            _run(command, CONVERT_TIMEOUT_SECONDS, "Converting the video")
        elif meta["kind"] == "image" and target in VIDEO_FORMATS | AUDIO_FORMATS:
            raise MediaError("an image can't be turned into audio or video")
        else:
            supported = {"image": sorted(IMAGE_FORMATS), "audio": sorted(AUDIO_FORMATS), "video": sorted(VIDEO_FORMATS | AUDIO_FORMATS | {"gif"})}
            options = supported.get(meta["kind"])
            raise MediaError(f"can't convert {meta['name']} to {target}" + (f"; it can become {', '.join(options)}" if options else
                                                                             "; only images, audio and video can be converted"))
        if not output.exists() or output.stat().st_size == 0:
            raise MediaError("the conversion produced no file")
        return save_path(output, f"{stem}.{target}", "converted")
