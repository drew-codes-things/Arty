import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

import call_defaults
import engine_guard
import main_calls
import models
import owner
import web_host

from environment import HUB_KEY
from settings import HUB_URL, ARTY_ENGINE_URL, CONFIG_FILE, HOMELAB_NAMES, OWN_APPS_FILE, STARFIRE_URL
from steering import thinking_for, with_thinking, with_homelab_names

log = logging.getLogger("brain")


SERVERS = ["memory", "time", "fetch", "sequential-thinking", "workspace", "research", "homelab"]
TOOL_RESULT_CHARS = 8000
SPEC_REFRESH_SECONDS = 300
USER_NAME = owner.configured_name(CONFIG_FILE)


def http_json(url, body=None, headers=None, timeout=600):
    def send(chosen):
        return call_defaults.written_again_if_cut_off(chosen, lambda asked: engine_or_plain_json(url, asked, headers, timeout))
    return main_calls.sent(url, call_defaults.with_call_defaults(url, body), send)


def engine_or_plain_json(url, body=None, headers=None, timeout=600):
    guard = engine_guard.current()
    if guard is not None and body is not None and engine_guard.is_generation(url):
        return engine_guard.guarded_call(url, body, guard, timeout)
    with engine_guard.outside_use(url):
        return plain_json(url, body, headers, timeout)


def plain_json(url, body=None, headers=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def stream_chat(body, on_text, timeout=600, on_thinking=None):
    url = f"{ARTY_ENGINE_URL}/api/chat"

    def send(chosen, hearing):
        return call_defaults.written_again_if_cut_off(chosen, lambda asked: engine_stream(asked, hearing, timeout, on_thinking))
    return main_calls.streamed(url, call_defaults.with_call_defaults(url, body), on_text, send)


def engine_stream(body, on_text, timeout=600, on_thinking=None):
    url = f"{ARTY_ENGINE_URL}/api/chat"
    if (guard := engine_guard.current()) is not None:
        return engine_guard.guarded_call(url, body, guard, timeout, on_text, on_thinking)
    with engine_guard.outside_use(url):
        return plain_stream_chat(body, on_text, timeout, on_thinking)


def plain_stream_chat(body, on_text, timeout=600, on_thinking=None):
    request = urllib.request.Request(f"{ARTY_ENGINE_URL}/api/chat", data=json.dumps({**body, "stream": True}).encode(), method="POST")
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


SAFETY_PROMPT_NAME = "safety-prompt.md"


def safety_prompt_file(config: dict) -> str:
    return config.get("safety_prompt_file") or str(Path(config["system_prompt_file"]).with_name(SAFETY_PROMPT_NAME))


def load_config():
    with open(CONFIG_FILE) as handle:
        config = json.load(handle)
    with open(config["system_prompt_file"]) as handle:
        config["system_prompt"] = handle.read().strip()
    config["safety_prompt"] = Path(safety_prompt_file(config)).read_text().strip()
    persona_file = config.get("persona_file")
    config["persona"] = Path(persona_file).read_text().strip() if persona_file and Path(persona_file).exists() else ""
    config["lookups"] = with_homelab_names(config.get("lookups", []), HOMELAB_NAMES)
    config["approval"] = with_own_app_hosts(config.get("approval", {}), own_app_hosts())
    return models.with_one_model(config)


def host_of(address) -> str:
    try:
        return web_host.host_of(address)
    except web_host.UnclearAddress:
        return ""


class UnreadableOwnApps(ValueError):
    pass


def read_own_apps(path: str) -> list:
    try:
        listed = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise UnreadableOwnApps(f"couldn't read the own app addresses in {path}: {error}") from None
    return list(listed.values()) if isinstance(listed, dict) else []


def listed_own_apps(path: str) -> list:
    try:
        return read_own_apps(path)
    except UnreadableOwnApps as error:
        log.warning("%s", error)
        return []


def distinct_hosts(addresses: list) -> list[str]:
    hosts = []
    for address in addresses:
        try:
            host = web_host.host_of(address)
        except web_host.UnclearAddress as error:
            raise UnreadableOwnApps(f"an own app address can't be used: {error}") from None
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def own_app_hosts(path: str | None = None, starfire_url: str | None = None) -> list[str]:
    starfire = STARFIRE_URL if starfire_url is None else starfire_url
    try:
        return distinct_hosts(listed_own_apps(path or OWN_APPS_FILE) + [starfire])
    except UnreadableOwnApps as error:
        log.warning("%s", error)
        return [host for host in (host_of(starfire),) if host]


def own_app_hosts_or_refuse() -> list[str]:
    return distinct_hosts(read_own_apps(OWN_APPS_FILE) + [STARFIRE_URL])


def is_own_app_address(url, hosts: list[str]) -> bool:
    return web_host.is_own_app_address(url, hosts)


def with_own_app_hosts(approval: dict, hosts: list[str]) -> dict:
    domains = list(approval.get("domains", []))
    return {**approval, "domains": domains + [host for host in hosts if host not in domains]}


PAGE_FETCH_TOOL = "fetch__fetch"
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
        if name == PAGE_FETCH_TOOL:
            import shopping

            return shopping.fetch_tool_text(arguments or {})[:limit]
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
    with main_calls.writing_the_reply():
        return (http_json(f"{ARTY_ENGINE_URL}/api/chat", body=body).get("message") or {}).get("content") or ""


def classify_text(prompt: str) -> str:
    return complete_text(prompt, think=False)
