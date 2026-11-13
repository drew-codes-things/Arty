"""Which Ollama models Arty can run on, and switching between them.

Only models with tool support qualify: without tools Arty loses its memory saving,
its computer and the browser. The tool budget (how many tools the model is shown
at once) follows model size when brain.json says "auto": small models pick tools
badly from a long list, so they get a focused subset.
"""
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request

import notify
from settings import CONFIG_FILE, OLLAMA_URL

CACHE_SECONDS = 60
PULL_TIMEOUT_SECONDS = 6 * 3600
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[a-z0-9._-]+)?$")
DROPDOWN_PREFIX = "arty/"
SOLO_PREFIX = "solo/"
CODER_MARK = "coder"
VRAM_BUDGET_MB = 11500
LOAD_OVERHEAD = 1.2
UNLOAD_WAIT_SECONDS = 30
UNLOAD_POLL_SECONDS = 0.5
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
            "bytes": entry.get("size", 0),
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


def separate_models() -> set[str]:
    return set()


def is_separate(model: dict) -> bool:
    return _named_in(model, list(separate_models())) and not _named_in(model, arty_models())


def chat_models() -> list[dict]:
    allowed = toolless_allowed()
    return [model for model in installed() if not model["embedding"] and (model["tools"] or allowed) and in_arty(model)
            and not is_separate(model)]


def downloaded_chat_models() -> list[dict]:
    return [model for model in installed() if not model["embedding"] and not kept_solo(model) and not is_separate(model)]


def usable(model: dict | None) -> bool:
    return (bool(model) and not model["embedding"] and (model["tools"] or toolless_allowed()) and in_arty(model)
            and not is_separate(model))


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


DEFAULT_CONTEXT_TOKENS = 12288


def is_token_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def own_window(config: dict, model: str) -> int | None:
    table = config.get("model_windows") or {}
    if not model or table.get("enabled") is not True:
        return None
    sizes = table.get("num_ctx") or {}
    size = sizes.get(model, sizes.get(model.removesuffix(":latest")))
    return size if is_token_count(size) else None


def context_tokens(config: dict, model: str = "") -> int:
    if own := own_window(config, model):
        return own
    size = config.get("options", {}).get("num_ctx")
    return size if is_token_count(size) else DEFAULT_CONTEXT_TOKENS


DEFAULT_REPLY_TOKENS = 2048


def reply_tokens(config: dict) -> int:
    size = {**config.get("options", {}), **(config.get("agent_options") or {})}.get("num_predict")
    return size if isinstance(size, int) and size > 0 else DEFAULT_REPLY_TOKENS


SHIFTS_CONTEXT = False


def for_runner(config: dict, body: dict) -> dict:
    return {**body, "options": {**(body.get("options") or {}), "num_ctx": context_tokens(config, body.get("model", ""))},
            "shift": SHIFTS_CONTEXT}


CARD_ARM = "card"
STEADY_ARM = "steady"
SAMPLING_ARMS = (CARD_ARM, STEADY_ARM)
DEFAULT_SAMPLING = "default"
PLAIN_REPLY_SAMPLING = "without_thinking"
NOT_SENT_AS_OPTIONS = ("sampling", "models", STEADY_ARM, "num_ctx")
EXACT_COPY_OPTIONS = ("repeat_penalty",)


def sampling_arm(config: dict) -> str:
    arm = (config.get("agent_options") or {}).get("sampling", CARD_ARM)
    if arm in SAMPLING_ARMS:
        return arm
    log.warning("brain.json agent_options.sampling must be one of %s, not %r; using %s", ", ".join(SAMPLING_ARMS), arm, CARD_ARM)
    return CARD_ARM


def sampling_for(config: dict, model: str, thinks: bool) -> dict:
    agent = config.get("agent_options") or {}
    if sampling_arm(config) == STEADY_ARM:
        return dict(agent.get(STEADY_ARM) or {})
    entries = agent.get("models") or {}
    entry = entries.get(model) or entries.get(model.removesuffix(":latest")) or entries.get(DEFAULT_SAMPLING) or {}
    chosen = {key: value for key, value in entry.items() if key != PLAIN_REPLY_SAMPLING}
    if not thinks:
        chosen.update(entry.get(PLAIN_REPLY_SAMPLING) or {})
    return chosen


def agent_options(config: dict, model: str = "", thinks: bool = True) -> dict:
    flat = {key: value for key, value in (config.get("agent_options") or {}).items() if key not in NOT_SENT_AS_OPTIONS}
    exact_copy = {key: flat[key] for key in EXACT_COPY_OPTIONS if key in flat}
    return {**config.get("options", {}), **flat, **sampling_for(config, model, thinks), **exact_copy,
            "num_predict": reply_tokens(config)}


def agent_sampling(config: dict, model: str) -> dict:
    return {"arm": sampling_arm(config), "thinking": sampling_for(config, model, True),
            PLAIN_REPLY_SAMPLING: sampling_for(config, model, False)}


def is_for_coding(name: str, coding_name: str) -> bool:
    is_coding_model = bool(coding_name) and name in (coding_name, f"{coding_name}:latest")
    return is_coding_model or CODER_MARK in name


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
            notify_send("Arty: model download failed", f"{name} didn't install.", category=notify.ARTY)
        elif not usable(model):
            notify_send("Arty: model not usable", f"{name} downloaded, but I'm staying on {previous}: " + TOOLLESS_REFUSAL.format(name=name), category=notify.ARTY)
        else:
            _write_main_model(model["name"])
            notify_send("Arty: model switched", f"Now running on {describe(model)} (was {previous}).", category=notify.ARTY)
    except (urllib.error.URLError, TimeoutError) as error:
        log.error("pull of %s failed: %s", name, error)
        notify_send("Arty: model download failed", f"{name}: {error}", category=notify.ARTY)
    finally:
        _pulls.discard(name)


def is_positive_megabytes(value) -> bool:
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return is_number and math.isfinite(value) and value > 0


def vram_budget(config: dict) -> int:
    budget_mb = config.get("vram_budget_mb", VRAM_BUDGET_MB)
    if not is_positive_megabytes(budget_mb):
        log.warning("brain.json vram_budget_mb must be a positive number of MB, not %r; using %s", budget_mb,
                    VRAM_BUDGET_MB)
        budget_mb = VRAM_BUDGET_MB
    return int(budget_mb * 1024 * 1024)


def _size_on_card(name: str, budget_bytes: int) -> int:
    found = next((m for m in installed() if m["name"] == name), None)
    return int(found["bytes"] * LOAD_OVERHEAD) if found and found.get("bytes") else budget_bytes


def is_loaded(name: str) -> bool:
    try:
        running = _ollama("/api/ps").get("models", [])
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        log.warning("couldn't ask Ollama which models are loaded: %s", error)
        return False
    wanted = {name, f"{name}:latest"}
    return any(model.get("name") in wanted or model.get("model") in wanted for model in running)


def _wait_until_gone(names: list[str]) -> None:
    deadline = time.monotonic() + UNLOAD_WAIT_SECONDS
    while time.monotonic() < deadline:
        still = {m["name"] for m in _ollama("/api/ps").get("models", [])} & set(names)
        if not still:
            return
        time.sleep(UNLOAD_POLL_SECONDS)
    log.warning("Ollama still holds %s %s s after being asked to unload it", ", ".join(names), UNLOAD_WAIT_SECONDS)


def make_room(name: str, budget_bytes: int) -> list[str]:
    try:
        running = _ollama("/api/ps").get("models", [])
        if any(m["name"] == name for m in running):
            return []
        in_use = sum(int(m.get("size_vram", 0)) for m in running)
        if in_use + _size_on_card(name, budget_bytes) <= budget_bytes:
            return []
        others = [m["name"] for m in running]
        for other in others:
            _ollama("/api/generate", {"model": other, "keep_alive": 0}, timeout=60)
        _wait_until_gone(others)
        return others
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        log.warning("couldn't make room on the GPU for %s, leaving it to Ollama: %s", name, error)
        return []


def handoff_tool(config: dict, definition: dict) -> dict:
    strengths = config.get("model_strengths", {})
    if not strengths:
        return definition
    try:
        others = [name for name in arty_models() if name != config["model"] and find(name) is not None]
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log.warning("couldn't list models for the handoff tool: %s", error)
        return definition
    lines = [f"{name}: {strengths[name]}" for name in others if name in strengths]
    if not lines:
        return definition
    function = definition["function"]
    description = f"{function['description']} Models to hand jobs to, and what each is best at: " + "; ".join(lines) + "."
    return {**definition, "function": {**function, "description": description}}


def ask(name: str, prompt: str, config: dict, keep_alive=0) -> str:
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
    options = {key: value for key, value in config.get("options", {}).items() if key in ("num_predict", "repeat_penalty")}
    make_room(model["name"], vram_budget(config))
    try:
        reply = _ollama("/api/chat", for_runner(config, {"model": model["name"], "messages": [{"role": "user", "content": prompt}],
                                                         "stream": False, "options": options, "keep_alive": keep_alive}),
                        timeout=900)
    except (urllib.error.URLError, TimeoutError) as error:
        return f"{name} didn't answer: {error}"
    return f"{model['name']} says:\n{(reply.get('message') or {}).get('content', '').strip()}"
