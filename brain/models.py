"""Which Ollama models Arty can run on, and switching between them.

Only models with tool support qualify: without tools Arty loses its memory saving,
its computer and the browser. The tool budget (how many tools the model is shown
at once) follows model size when brain.json says "auto": small models pick tools
badly from a long list, so they get a focused subset.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

from settings import CONFIG_FILE, OLLAMA_URL

CACHE_SECONDS = 60
PULL_TIMEOUT_SECONDS = 6 * 3600
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[a-z0-9._-]+)?$")
DROPDOWN_PREFIX = "arty/"
SOLO_PREFIX = "solo/"
SOLO_LABEL = "{name} (on its own: no Arty, no memory)"
SOLO_DESCRIPTION = "Talks straight to the model, like starfire. Nothing is saved to Arty's memory."

log = logging.getLogger("brain.models")
_cache = {"at": 0.0, "models": []}
_cache_lock = threading.Lock()
_refresh_lock = threading.Lock()
_pulls: set[str] = set()


def _ollama(path: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{OLLAMA_URL}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def _parameter_billions(details: dict) -> float:
    match = re.match(r"([\d.]+)\s*([BM])", str(details.get("parameter_size", "")).upper())
    if not match:
        return 0.0
    value = float(match.group(1))
    return value / 1000 if match.group(2) == "M" else value


def installed(refresh: bool = False) -> list[dict]:
    """Installed models: name, size in billions of parameters, and whether they support tools/vision."""
    with _cache_lock:
        if not refresh and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["models"]
        stale = _cache["models"] if _cache["at"] and not refresh else None
    if not _refresh_lock.acquire(blocking=stale is None):
        return stale
    try:
        return _refresh_installed()
    finally:
        _refresh_lock.release()


def _refresh_installed() -> list[dict]:
    models = []
    for entry in _ollama("/api/tags").get("models", []):
        try:
            shown = _ollama("/api/show", {"model": entry["name"]})
        except (urllib.error.URLError, TimeoutError):
            continue
        capabilities = shown.get("capabilities") or []
        models.append({
            "name": entry["name"],
            "billions": _parameter_billions(shown.get("details") or entry.get("details") or {}),
            "tools": "tools" in capabilities,
            "vision": "vision" in capabilities,
            "embedding": "embedding" in capabilities,
            "thinking": "thinking" in capabilities,
        })
    with _cache_lock:
        _cache.update(at=time.time(), models=models)
    return models


def toolless_allowed() -> bool:
    """brain.json "allow_toolless_models": let models without tool support run Arty (chat, persona and memory only)."""
    with open(CONFIG_FILE) as handle:
        return bool(json.load(handle).get("allow_toolless_models", False))


def arty_models() -> list[str]:
    with open(CONFIG_FILE) as handle:
        return list(json.load(handle).get("arty_models") or [])


def solo_models() -> list[str]:
    with open(CONFIG_FILE) as handle:
        return list(json.load(handle).get("solo_models") or [])


def solo_choice(requested: str) -> dict | None:
    if not requested.startswith(SOLO_PREFIX):
        return None
    name = requested.removeprefix(SOLO_PREFIX)
    if name not in solo_models():
        return None
    return find(name)


def solo_entries() -> list[dict]:
    entries = []
    for name in solo_models():
        model = find(name)
        if model:
            entries.append({"id": f"{SOLO_PREFIX}{model['name']}", "name": SOLO_LABEL.format(name=model["name"]),
                            "object": "model", "owned_by": "home", "model": model["name"], "description": SOLO_DESCRIPTION})
    return entries


def _named_in(model: dict, names: list[str]) -> bool:
    return model["name"] in names or model["name"].removesuffix(":latest") in names


def in_arty(model: dict) -> bool:
    allowed = arty_models()
    return not allowed or _named_in(model, allowed)


def kept_solo(model: dict) -> bool:
    return _named_in(model, solo_models()) and not _named_in(model, arty_models())


def chat_models() -> list[dict]:
    allowed = toolless_allowed()
    return [model for model in installed() if not model["embedding"] and (model["tools"] or allowed) and in_arty(model)]


def downloaded_chat_models() -> list[dict]:
    return [model for model in installed() if not model["embedding"] and not kept_solo(model)]


def usable(model: dict | None) -> bool:
    return bool(model) and not model["embedding"] and (model["tools"] or toolless_allowed()) and in_arty(model)


NOT_ARTY_REFUSAL = ("{name} isn't one of my models ({allowed}). It's still there as a plain chat in starfire, "
                    "or add it to arty_models in brain.json.")
TOOLLESS_REFUSAL = ("{name} can't use tools, so I'd lose memory saving, my computer and the browser. "
                    "You can allow it anyway with `ai-toolless on` on the AI server.")


def summary(model: dict) -> str:
    size = f"{model['billions']:g}B" if model.get("billions") else ""
    traits = ["thinks when needed" if model.get("thinking") else "",
              "all tools" if model.get("tools") else "chat only, no tools",
              "sees images" if model.get("vision") else ""]
    return " · ".join(part for part in [size, *traits] if part)


def find(name: str) -> dict | None:
    for model in installed():
        if model["name"] == name or model["name"] == f"{name}:latest":
            return model
    return None


def budget_for(config: dict, model_name: str) -> int:
    """How many tools to show this model at once (0 = all)."""
    budget = config.get("tool_budget", "auto")
    if budget != "auto":
        return int(budget)
    model = find(model_name)
    billions = model["billions"] if model else 0.0
    if not billions:
        return 0
    for limit, size_budget in sorted(((float(k), v) for k, v in config.get("tool_budget_by_size", {}).items())):
        if billions <= limit:
            return int(size_budget)
    return 0


def _write_main_model(name: str):
    with open(CONFIG_FILE) as handle:
        raw = json.load(handle)
    raw["model"] = name
    temporary = f"{CONFIG_FILE}.tmp"
    with open(temporary, "w") as handle:
        handle.write(json.dumps(raw, indent=2) + "\n")
    os.replace(temporary, CONFIG_FILE)


def describe(model: dict) -> str:
    size = f"{model['billions']:g}B" if model["billions"] else "size unknown"
    extras = (", can see images" if model["vision"] else "") + ("" if model["tools"] else ", no tools")
    return f"{model['name']} ({size}{extras})"


def list_text(current: str) -> str:
    models = chat_models()
    if not models:
        return "No tool-capable models are installed."
    lines = [f"{'* ' if m['name'] == current else '  '}{describe(m)}" for m in models]
    return "Models I can run on (* = my main model):\n" + "\n".join(lines) + "\nMore at https://ollama.com/search?c=tools"


def switch(name: str, current: str, notify_send) -> str:
    """Make `name` Arty's main model, downloading it first if needed (in the background)."""
    name = name.strip().lower()
    if not MODEL_NAME.match(name):
        return f"'{name}' doesn't look like an Ollama model name (e.g. qwen2.5:7b)."
    if name in (current, f"{current}:latest") or current == f"{name}:latest":
        return f"I'm already running on {current}."
    if not in_arty({"name": name}):
        return f"Not switching: {NOT_ARTY_REFUSAL.format(name=name, allowed=', '.join(arty_models()))}"
    model = find(name)
    if model:
        if not usable(model):
            return "Not switching: " + TOOLLESS_REFUSAL.format(name=name)
        _write_main_model(model["name"])
        return f"Switched my main model to {describe(model)}, from {current}. It applies from the next message."
    if name in _pulls:
        return f"{name} is already downloading; I'll switch when it's done."
    _pulls.add(name)
    threading.Thread(target=_pull_then_switch, args=(name, current, notify_send), daemon=True).start()
    return f"{name} isn't downloaded yet. I've started downloading it and will switch (and notify you) when it's ready."


def _pull_then_switch(name: str, previous: str, notify_send):
    try:
        _ollama("/api/pull", {"model": name, "stream": False}, timeout=PULL_TIMEOUT_SECONDS)
        model = find(name) or (installed(refresh=True) and find(name))
        if not model:
            notify_send("Arty: model download failed", f"{name} didn't install.")
        elif not usable(model):
            notify_send("Arty: model not usable", f"{name} downloaded, but I'm staying on {previous}: " + TOOLLESS_REFUSAL.format(name=name))
        else:
            _write_main_model(model["name"])
            notify_send("Arty: model switched", f"Now running on {describe(model)} (was {previous}).")
    except (urllib.error.URLError, TimeoutError) as error:
        log.error("pull of %s failed: %s", name, error)
        notify_send("Arty: model download failed", f"{name}: {error}")
    finally:
        _pulls.discard(name)


def ask(name: str, prompt: str, config: dict) -> str:
    """One-shot question to another installed model (no tools, no memory), for delegating a job."""
    if not prompt.strip():
        return "Give the other model the full job to do."
    model = find(name.strip())
    if not model:
        return f"{name} isn't installed. {list_text(config['model'])}"
    if not in_arty(model):
        return NOT_ARTY_REFUSAL.format(name=model["name"], allowed=", ".join(arty_models()))
    if model["embedding"]:
        return f"{name} is a search-index model and can't answer questions."
    options = {key: value for key, value in config.get("options", {}).items() if key in ("num_ctx", "num_predict", "repeat_penalty")}
    try:
        reply = _ollama("/api/chat", {"model": model["name"], "messages": [{"role": "user", "content": prompt}], "stream": False,
                                      "options": options, "keep_alive": 0}, timeout=900)
    except (urllib.error.URLError, TimeoutError) as error:
        return f"{name} didn't answer: {error}"
    return f"{model['name']} says:\n{(reply.get('message') or {}).get('content', '').strip()}"
