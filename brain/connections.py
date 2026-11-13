import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

import models
import owner

from environment import HUB_KEY
from settings import HUB_URL, OLLAMA_URL, CONFIG_FILE, HOMELAB_NAMES
from steering import thinking_for, with_thinking, with_homelab_names

log = logging.getLogger("brain")


SERVERS = ["memory", "time", "fetch", "sequential-thinking", "workspace", "research", "homelab"]
TOOL_RESULT_CHARS = 8000
SPEC_REFRESH_SECONDS = 300
USER_NAME = owner.configured_name(CONFIG_FILE)


def http_json(url, body=None, headers=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def stream_chat(body, on_text, timeout=600, on_thinking=None):
    """A model call like http_json(f"{OLLAMA_URL}/api/chat", body), but streamed: on_text gets each piece of
    the reply as the model writes it. Returns the same shape as an unstreamed reply."""
    request = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps({**body, "stream": True}).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    content, calls, last = [], [], {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            if not line.strip():
                continue
            last = json.loads(line)
            if last.get("error"):
                raise RuntimeError(f"model error: {last['error']}")
            piece = last.get("message") or {}
            calls += piece.get("tool_calls") or []
            if on_thinking and piece.get("thinking"):
                on_thinking(piece["thinking"])
            if piece.get("content"):
                content.append(piece["content"])
                on_text(piece["content"])
    return {**last, "message": {"role": "assistant", "content": "".join(content), "tool_calls": calls}}


def load_config():
    with open(CONFIG_FILE) as handle:
        config = json.load(handle)
    with open(config["system_prompt_file"]) as handle:
        config["system_prompt"] = handle.read().strip()
    persona_file = config.get("persona_file")
    config["persona"] = Path(persona_file).read_text().strip() if persona_file and Path(persona_file).exists() else ""
    config["lookups"] = with_homelab_names(config.get("lookups", []), HOMELAB_NAMES)
    return config


MAX_SCHEMA_DEPTH = 8


def inline_refs(node, schemas, depth=0):
    """Replace $ref pointers with the schemas they name, so the model sees nested shapes."""
    if depth > MAX_SCHEMA_DEPTH:
        return {}
    if isinstance(node, list):
        return [inline_refs(item, schemas, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if ref:
        return inline_refs(schemas.get(ref.split("/")[-1], {}), schemas, depth + 1)
    return {key: inline_refs(value, schemas, depth + 1) for key, value in node.items() if key != "title"}


class ToolHub:
    def __init__(self):
        self.tools = []
        self.routes = {}
        self.loaded_at = 0.0

    def refresh(self):
        if self.tools and time.time() - self.loaded_at < SPEC_REFRESH_SECONDS:
            return
        tools, routes = [], {}
        for server in SERVERS:
            try:
                spec = http_json(f"{HUB_URL}/{server}/openapi.json", headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
            except (urllib.error.URLError, TimeoutError) as error:
                log.warning("tool server %s unavailable: %s", server, error)
                continue
            schemas = spec.get("components", {}).get("schemas", {})
            for path, methods in spec.get("paths", {}).items():
                operation = methods.get("post")
                if not operation:
                    continue
                name = f"{server.replace('-', '_')}__{path.strip('/')}"[:64]
                parameters = {"type": "object", "properties": {}}
                ref = (((operation.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema", {}).get("$ref")
                if ref:
                    schema = inline_refs(schemas.get(ref.split("/")[-1], {}), schemas)
                    parameters = {"type": "object", "properties": schema.get("properties", {}), "required": schema.get("required", [])}
                tools.append({"type": "function", "function": {"name": name, "description": (operation.get("description") or operation.get("summary") or "")[:1024], "parameters": parameters}})
                routes[name] = f"{HUB_URL}/{server}{path}"
        if tools:
            self.tools, self.routes, self.loaded_at = tools, routes, time.time()
            log.info("loaded %d tools", len(tools))

    def call(self, name, arguments, limit=TOOL_RESULT_CHARS):
        route = self.routes.get(name)
        if not route:
            return f"unknown tool {name}"
        try:
            result = http_json(route, body=arguments or {}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=900)
        except urllib.error.HTTPError as error:
            return f"tool error {error.code}: {error.read().decode(errors='replace')[:500]}"
        except (urllib.error.URLError, TimeoutError) as error:
            return f"tool unavailable: {error}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return text[:limit]


hub = ToolHub()


PLAIN_COMPLETION_TOKENS = 1500


def complete_text(prompt: str, json_mode: bool = False, think: bool | None = None) -> str:
    """One plain model call (no persona, memory or tools), for writing jobs like lecture notes and research."""
    config = load_config()
    options = {"temperature": 0.2, "num_predict": PLAIN_COMPLETION_TOKENS,
               "repeat_penalty": config.get("options", {}).get("repeat_penalty", 1.1)}  # small models can loop on one line
    body = models.for_runner(config, {"model": config["model"], "stream": False, "options": options,
                                      "keep_alive": config.get("keep_alive", "10m"), "messages": [{"role": "user", "content": prompt}],
                                      **({"format": "json"} if json_mode else {})})
    wanted = (not json_mode and thinking_for(config, writing_job=True)) if think is None else think
    body = with_thinking(body, wanted)
    return (http_json(f"{OLLAMA_URL}/api/chat", body=body).get("message") or {}).get("content") or ""


def classify_text(prompt: str) -> str:
    return complete_text(prompt, think=False)
