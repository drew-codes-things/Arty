import base64
import functools
import logging
import mimetypes
import urllib.error

import connections
import media
import models

from api_support import ApiError
from archive import message_text
from settings import OLLAMA_URL
from steering import parse_arguments
from model_choice import session_keep_alive

log = logging.getLogger("brain")


# ---------- attachments ----------

def vision_model(wanted: str = ""):
    for name in (wanted, connections.load_config().get("vision_model", "")):
        model = models.find(name) if name else None
        if model and model["vision"]:
            return model
    model = next((m for m in models.installed() if m["vision"] and models.in_arty(m) and not models.is_separate(m)
                  and not models.kept_solo(m)), None)
    if model is None:
        raise ApiError(400, "No vision model is installed. Run: ai-pull qwen2.5vl:7b")
    return model


def look(images: list[str], prompt: str, keep_alive=0) -> str:
    """Asks the vision model about base64 images."""
    config = connections.load_config()
    options = {k: v for k, v in config.get("options", {}).items() if k == "num_predict"}
    looker = vision_model()["name"]
    models.make_room(looker, models.vram_budget(config))
    raw = connections.http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, {
        "model": looker, "stream": False, "think": False, "options": options, "keep_alive": session_keep_alive(looker, keep_alive),
        "messages": [{"role": "user", "content": prompt, "images": images}]}))
    return ((raw or {}).get("message") or {}).get("content", "").strip() or "(the vision model gave no description)"


def stored_attachment(part: dict) -> dict | None:
    """Stores (or finds) the file behind one content part: image_url data URIs, input_audio, or an uploaded file id."""
    kind = part.get("type")
    if kind == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        if not url.startswith("data:") or "," not in url:
            return None
        header, data = url.split(",", 1)
        mime = header[5:].split(";", 1)[0] or "image/png"
        return media.save_bytes(base64.b64decode(data), f"image{mimetypes.guess_extension(mime) or '.png'}", mime, "chat")
    if kind == "input_audio":
        audio = part.get("input_audio") or {}
        extension = str(audio.get("format") or "wav")
        return media.save_bytes(base64.b64decode(audio.get("data", "")), f"audio.{extension}", "", "chat")
    if kind == "file":
        return media.get(str((part.get("file") or {}).get("file_id", "")))
    return None


def is_picture_part(part) -> bool:
    if not isinstance(part, dict):
        return False
    if part.get("type") == "image_url":
        return str((part.get("image_url") or {}).get("url", "")).startswith("data:image/")
    if part.get("type") == "file":
        stored = media.get(str((part.get("file") or {}).get("file_id", "")))
        return bool(stored) and stored.get("kind") == "image"
    return False


def has_new_picture(messages: list[dict]) -> bool:
    latest = next((message for message in reversed(messages) if message.get("role") == "user"), None)
    content = latest.get("content") if latest else None
    return isinstance(content, list) and any(is_picture_part(part) for part in content)


def expand_attachments(messages: list[dict], look_keep_alive=0) -> tuple[list[dict], bool]:
    """Replaces attachments in user messages with text the chat model can use: what's in them, and the
    file id for convert_file. Returns (messages, whether there were any)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    found = False
    expanded = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            expanded.append(message)
            continue
        question = message_text(message)
        notes = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") == "text":
                continue
            found = True
            try:
                meta = stored_attachment(part)
            except (ValueError, OSError) as error:
                notes.append(f"[An attachment couldn't be read: {error}]")
                continue
            if meta is None:
                notes.append("[An attachment has expired or couldn't be read; ask the user to attach it again if you need it]")
                continue
            # Plain sentences: small models copy bracket/link-like notes back into their replies as fake links.
            heading = f'(The user attached a{"n" if meta["kind"][0] in "aeiou" else ""} {meta["kind"]} file called {meta["name"]}, {meta["size"] // 1024} KB, file id {meta["id"]}.)'
            if index != last_user and not meta.get("analysis"):
                notes.append(heading)
                continue
            try:
                notes.append(f"{heading}\nWhat's in it: {media.analyse(meta, question, functools.partial(look, keep_alive=look_keep_alive))}")
            except (media.MediaError, ApiError, urllib.error.URLError, TimeoutError) as error:
                log.warning("could not analyse %s: %s", meta["id"], error)
                notes.append(f"{heading}\n(Couldn't look inside it: {error})")
        text = "\n\n".join(notes + ([question] if question.strip() else []))
        expanded.append({**message, "content": text})
    return expanded, found


def to_ollama_messages(messages):
    """Client apps send OpenAI-style history: assistant tool_calls with JSON-string arguments and
    tool results keyed by tool_call_id. Ollama wants argument objects and the tool's name."""
    names, out = {}, []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function", {})
                names[call.get("id", "")] = function.get("name", "")
                calls.append({"function": {"name": function.get("name", ""), "arguments": parse_arguments(function.get("arguments"))}})
            out.append({"role": "assistant", "content": message_text(message), "tool_calls": calls})
        elif role == "tool":
            out.append({"role": "tool", "content": message_text(message),
                        "tool_name": message.get("tool_name") or names.get(message.get("tool_call_id", ""), "")})
        else:
            out.append({**message, "content": message_text(message)})
    return out
