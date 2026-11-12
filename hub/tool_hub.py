import argparse
import asyncio
import hmac
import json
import logging
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import ImageContent, PaginatedRequestParams, TextContent
from mcp.types.jsonrpc import CONNECTION_CLOSED

KEY_VARIABLE = "MCPO_API_KEY"
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
CALL_TIMEOUT_SECONDS = 900
CALL_GRACE_SECONDS = 30
STARTUP_WAIT_SECONDS = 60
PING_EVERY_SECONDS = 30
PING_TIMEOUT_SECONDS = 20
RESTART_DELAYS_SECONDS = (1, 2, 5, 10, 30)
MAX_BODY_BYTES = 16 * 1024 * 1024
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
MCP_ERROR_STATUS = {METHOD_NOT_FOUND: 404, INVALID_PARAMS: 422}
LOCAL_DEFINITIONS = ("$defs", "definitions")

log = logging.getLogger("tool_hub")


class ServerUnavailable(Exception):
    pass


class UnknownTool(Exception):
    pass


def text_items(value, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return value


def text_mapping(value, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{label} must map names to string values")
    return value


def server_parameters(name: str, spec) -> StdioServerParameters:
    if not SERVER_NAME.match(name):
        raise ValueError(f"tool server name {name!r} may only hold letters, digits, - and _")
    if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not spec["command"]:
        raise ValueError(f"tool server {name} needs a command (only servers started over stdio are supported)")
    return StdioServerParameters(command=spec["command"], args=text_items(spec.get("args", []), f"{name} args"),
                                 env=text_mapping(spec.get("env", {}), f"{name} env"))


def load_servers(config_path: Path) -> dict[str, StdioServerParameters]:
    try:
        config = json.loads(Path(config_path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"can't read the tool hub config {config_path}: {error}") from error
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"{config_path} needs an mcpServers object naming at least one server")
    return {name: server_parameters(name, spec) for name, spec in servers.items()}


def form_name(tool_name: str) -> str:
    return f"{tool_name}_form_model"


def with_component_refs(node, renamed: dict[str, str]):
    if isinstance(node, list):
        return [with_component_refs(item, renamed) for item in node]
    if not isinstance(node, dict):
        return node
    moved = {}
    for key, value in node.items():
        if key in LOCAL_DEFINITIONS:
            continue
        if key == "$ref" and isinstance(value, str) and value.split("/")[-1] in renamed and value.startswith("#/"):
            moved[key] = f"#/components/schemas/{renamed[value.split('/')[-1]]}"
        else:
            moved[key] = with_component_refs(value, renamed)
    return moved


def tool_schemas(tool_name: str, input_schema: dict) -> dict[str, dict]:
    definitions = {}
    for key in LOCAL_DEFINITIONS:
        if isinstance(input_schema.get(key), dict):
            definitions.update(input_schema[key])
    renamed = {name: f"{tool_name}__{name}" for name in definitions}
    form = with_component_refs(input_schema, renamed)
    form.setdefault("type", "object")
    form.setdefault("properties", {})
    schemas = {form_name(tool_name): form}
    schemas.update({renamed[name]: with_component_refs(schema, renamed) for name, schema in definitions.items()})
    return schemas


def openapi_document(server: str, tools: list) -> dict:
    paths, schemas = {}, {}
    for tool in tools:
        schemas.update(tool_schemas(tool.name, dict(tool.input_schema or {})))
        paths[f"/{tool.name}"] = {"post": {
            "operationId": f"tool_{tool.name}_post",
            "summary": tool.title or tool.name.replace("_", " ").capitalize(),
            "description": tool.description or "",
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{form_name(tool.name)}"}}}},
            "responses": {"200": {"description": "Successful Response"}},
        }}
    return {"openapi": "3.1.0", "info": {"title": server, "version": "1"}, "paths": paths, "components": {"schemas": schemas}}


def content_value(content):
    if isinstance(content, TextContent):
        try:
            return json.loads(content.text)
        except json.JSONDecodeError:
            return content.text
    if isinstance(content, ImageContent):
        return f"data:{content.mime_type};base64,{content.data}"
    return f"Unsupported tool result content: {getattr(content, 'type', 'unknown')}"


def result_response(result) -> tuple[int, object]:
    if result.is_error:
        first = result.content[0] if result.content else None
        message = first.text if isinstance(first, TextContent) else "Unknown tool execution error"
        return 500, {"detail": {"message": message}}
    values = [content_value(content) for content in result.content]
    return 200, values[0] if len(values) == 1 else values


async def every_tool(session: ClientSession) -> list:
    tools, listing = [], await session.list_tools()
    tools.extend(listing.tools)
    while listing.next_cursor:
        listing = await session.list_tools(params=PaginatedRequestParams(cursor=listing.next_cursor))
        tools.extend(listing.tools)
    return tools


class ToolServer:
    def __init__(self, name: str, parameters: StdioServerParameters):
        self.name, self.parameters = name, parameters
        self.session: ClientSession | None = None
        self.tools: list = []
        self.settled = asyncio.Event()
        self.broken = asyncio.Event()

    async def keep_running(self):
        failures = 0
        while True:
            try:
                await self.run_once()
            except Exception as error:
                log.warning("tool server %s stopped: %s", self.name, error)
            failures = 0 if self.session is not None else failures + 1
            self.session, self.tools = None, []
            self.settled.set()
            await asyncio.sleep(RESTART_DELAYS_SECONDS[min(failures, len(RESTART_DELAYS_SECONDS) - 1)])

    async def run_once(self):
        async with stdio_client(self.parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            self.tools = await every_tool(session)
            self.broken.clear()
            self.session = session
            self.settled.set()
            log.info("tool server %s is up with %d tools", self.name, len(self.tools))
            await self.watch(session)

    async def watch(self, session: ClientSession):
        while True:
            try:
                await asyncio.wait_for(self.broken.wait(), PING_EVERY_SECONDS)
            except TimeoutError:
                await asyncio.wait_for(session.send_ping(), PING_TIMEOUT_SECONDS)
                continue
            raise ConnectionError("a tool call lost the connection")

    def openapi(self) -> dict:
        if self.session is None:
            raise ServerUnavailable(self.name)
        return openapi_document(self.name, self.tools)

    async def call(self, tool: str, arguments: dict):
        session = self.session
        if session is None:
            raise ServerUnavailable(self.name)
        if tool not in {known.name for known in self.tools}:
            raise UnknownTool(tool)
        try:
            return await session.call_tool(tool, arguments, read_timeout_seconds=CALL_TIMEOUT_SECONDS)
        except MCPError as error:
            if error.code == CONNECTION_CLOSED:
                self.broken.set()
            raise
        except Exception:
            self.broken.set()
            raise


class Hub:
    def __init__(self, parameters: dict[str, StdioServerParameters]):
        self.parameters = parameters
        self.servers: dict[str, ToolServer] = {}
        self.loop = asyncio.new_event_loop()
        self.keepers: list[asyncio.Task] = []

    def start(self):
        threading.Thread(target=self.loop.run_forever, name="mcp-clients", daemon=True).start()
        self.run(self.start_servers(), STARTUP_WAIT_SECONDS + CALL_GRACE_SECONDS)

    async def start_servers(self):
        for name, parameters in self.parameters.items():
            self.servers[name] = ToolServer(name, parameters)
            self.keepers.append(asyncio.create_task(self.servers[name].keep_running()))
        waiting = [asyncio.create_task(server.settled.wait()) for server in self.servers.values()]
        await asyncio.wait(waiting, timeout=STARTUP_WAIT_SECONDS)
        for task in waiting:
            task.cancel()

    def run(self, coroutine, timeout: float):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def openapi(self, server: str) -> dict:
        return self.servers[server].openapi()

    def call(self, server: str, tool: str, arguments: dict):
        return self.run(self.servers[server].call(tool, arguments), CALL_TIMEOUT_SECONDS + CALL_GRACE_SECONDS)


def error_body(message: str) -> dict:
    return {"detail": message}


def presented_key(header: str) -> str:
    scheme, _, credentials = (header or "").partition(" ")
    return credentials.strip() if scheme.lower() == "bearer" else ""


def handler_for(hub: Hub, key: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.answer("GET")

        def do_POST(self):
            self.answer("POST")

        def log_message(self, format, *args):
            log.debug("%s %s", self.address_string(), format % args)

        def answer(self, method: str):
            try:
                status, payload = self.route(method)
            except Exception as error:
                log.exception("tool hub request failed")
                status, payload = 502, {"detail": {"message": "Unexpected error", "error": str(error)}}
            self.send(status, payload)

        def route(self, method: str) -> tuple[int, object]:
            given = presented_key(self.headers.get("Authorization", ""))
            if not given:
                return 401, error_body("Missing or invalid Authorization header")
            if not hmac.compare_digest(given.encode(), key.encode()):
                return 403, error_body("Invalid API key")
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            if len(parts) != 2 or parts[0] not in hub.servers:
                return 404, error_body("Not Found")
            server, rest = parts
            if method == "GET" and rest == "openapi.json":
                return self.openapi(server)
            if method == "POST":
                return self.call(server, rest)
            return 405, error_body("Method Not Allowed")

        def openapi(self, server: str) -> tuple[int, object]:
            try:
                return 200, hub.openapi(server)
            except ServerUnavailable:
                return 503, error_body(f"tool server {server} is not running")

        def arguments(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("body too large")
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None

        def call(self, server: str, tool: str) -> tuple[int, object]:
            try:
                arguments = self.arguments()
            except (ValueError, json.JSONDecodeError):
                return 400, error_body("The body must be a JSON object of the tool's arguments")
            if arguments is None:
                return 400, error_body("The body must be a JSON object of the tool's arguments")
            log.info("calling %s/%s", server, tool)
            try:
                return result_response(hub.call(server, tool, arguments))
            except ServerUnavailable:
                return 503, error_body(f"tool server {server} is not running")
            except UnknownTool:
                return 404, error_body("Not Found")
            except MCPError as error:
                return MCP_ERROR_STATUS.get(error.code, 500), {"detail": {"message": error.message}}
            except TimeoutError:
                return 504, error_body(f"{server}/{tool} took longer than {CALL_TIMEOUT_SECONDS} seconds")

        def send(self, status: int, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def arguments_from(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serves MCP tool servers to the brain over HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", required=True, type=Path)
    return parser.parse_args(argv)


def stop_on_terminate(signal_number, frame):
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> int:
    options = arguments_from(argv)
    key = os.environ.get(KEY_VARIABLE, "")
    if not key:
        print(f"tool_hub: {KEY_VARIABLE} is not set, refusing to run the tool hub without a key", file=sys.stderr)
        return 1
    try:
        parameters = load_servers(options.config)
    except ValueError as error:
        print(f"tool_hub: {error}", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    hub = Hub(parameters)
    hub.start()
    server = ThreadingHTTPServer((options.host, options.port), handler_for(hub, key))
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, stop_on_terminate)
    log.info("tool hub listening on %s:%d with %s", options.host, options.port, ", ".join(parameters))
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
