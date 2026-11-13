import argparse
import ipaddress
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doclib.search import is_coding_question, library_summary, read, search  # noqa: E402

log = logging.getLogger("doclib.server")

DEFAULT_PORT = 8889
DEFAULT_DATABASE = Path("/srv/ai/docs-library/docs.db")
LOOPBACK = "127.0.0.1"
MAX_QUERY_CHARS = 500
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
DEFAULT_READ_CHARS = 8000
MAX_READ_CHARS = 30000
GATED_MIN_SCORE = 2.0


class BadRequest(ValueError):
    pass


def is_allowed_client(address: str, networks: tuple = ()) -> bool:
    try:
        client = ipaddress.ip_address(address)
    except ValueError:
        return False
    return client.is_loopback or any(client in network for network in networks)


def bridge_interface(name: str) -> ipaddress.IPv4Interface:
    shown = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", name], capture_output=True, text=True, timeout=10)
    if shown.returncode != 0:
        raise RuntimeError(f"couldn't read the address of {name}: {shown.stderr.strip()}")
    for link in json.loads(shown.stdout or "[]"):
        for address in link.get("addr_info", []):
            return ipaddress.IPv4Interface(f"{address['local']}/{address['prefixlen']}")
    raise RuntimeError(f"{name} has no IPv4 address yet")


def number_from(query: dict, name: str, default: int, highest: int) -> int:
    raw = (query.get(name) or [str(default)])[0]
    try:
        value = int(raw)
    except ValueError as error:
        raise BadRequest(f"{name} must be a whole number") from error
    return max(1, min(value, highest))


def search_answer(database: Path, query: dict) -> dict:
    question = (query.get("q") or [""])[0].strip()
    if not question:
        raise BadRequest("q is required")
    if len(question) > MAX_QUERY_CHARS:
        raise BadRequest(f"q is longer than {MAX_QUERY_CHARS} characters")
    limit = number_from(query, "limit", DEFAULT_LIMIT, MAX_LIMIT)
    gated = (query.get("gate") or [""])[0] == "coding"
    if gated and not is_coding_question(question):
        return {"query": question, "results": []}
    hits = search(database, question, limit)
    if gated:
        hits = [hit for hit in hits if hit.score >= GATED_MIN_SCORE]
    return {"query": question, "results": [{**hit._asdict(), "title": hit.heading} for hit in hits]}


def read_answer(database: Path, query: dict) -> dict:
    section_id = number_from(query, "id", 0, sys.maxsize)
    chars = number_from(query, "chars", DEFAULT_READ_CHARS, MAX_READ_CHARS)
    section = read(database, section_id)
    if section is None:
        raise LookupError(f"no section {section_id}")
    body = section.pop("body")
    return {**section, "text": body[:chars], "truncated": len(body) > chars}


def health_answer(database: Path) -> dict:
    sources = library_summary(database)
    return {"ok": bool(sources), "sources": len(sources), "sections": sum(source["sections"] for source in sources)}


def make_handler(database: Path, networks: tuple = ()):
    routes = {"/search": lambda query: search_answer(database, query),
              "/read": lambda query: read_answer(database, query),
              "/health": lambda query: health_answer(database),
              "/sources": lambda query: {"sources": library_summary(database)}}

    class Handler(BaseHTTPRequestHandler):
        server_version = "docs-library/1.0"

        def send_json(self, status: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not is_allowed_client(self.client_address[0], networks):
                self.send_json(403, {"error": "only this container and its Docker network may ask"})
                return
            parsed = urlparse(self.path)
            route = routes.get(parsed.path)
            if route is None:
                self.send_json(404, {"error": f"unknown path {parsed.path}; use /search, /read, /sources or /health"})
                return
            try:
                self.send_json(200, route(parse_qs(parsed.query)))
            except BadRequest as error:
                self.send_json(400, {"error": str(error)})
            except LookupError as error:
                self.send_json(404, {"error": str(error)})
            except sqlite3.Error as error:
                log.error("library database error on %s: %s", parsed.path, error)
                self.send_json(503, {"error": f"the library database isn't readable: {error}"})

        def log_message(self, format, *args):
            log.debug("%s %s", self.client_address[0], format % args)

    return Handler


def serve(hosts: list[str], port: int, database: Path, networks: tuple = ()):
    handler = make_handler(database, networks)
    servers = [ThreadingHTTPServer((host, port), handler) for host in hosts]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True, name=f"docs-{server.server_address[0]}").start()
    log.info("serving %s on %s port %d", database, ", ".join(hosts), port)
    servers[0].serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search and read the local documentation library over HTTP.")
    parser.add_argument("--bridge", action="append", default=[],
                        help="also listen on this network interface (e.g. docker0) and answer clients on its network")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bridges = [bridge_interface(name) for name in options.bridge]
    hosts = [LOOPBACK] + [str(bridge.ip) for bridge in bridges]
    serve(hosts, options.port, options.db, tuple(bridge.network for bridge in bridges))
    return 0


if __name__ == "__main__":
    sys.exit(main())
