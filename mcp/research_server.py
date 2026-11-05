"""Finding things out: web search, reading documents, read-only git, and a searchable document library.

Web search goes to the self-hosted SearXNG on this container. Documents are read on
the desktop VM by `doc2text` (pdftotext, pandoc, xlsx2csv, Tesseract OCR). The
library is ~/library on the desktop, indexed into a local SQLite file with
embeddings from Ollama, so it can be searched by meaning, not just file name.
"""
import array
import json
import math
import os
import shlex
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

from remote import clip, quoted
from remote import text as remote_text

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
LIBRARY_DIR = "~/library"
LIBRARY_DB = os.environ.get("LIBRARY_DB", "/srv/ai/brain/state/library.db")
LIBRARY_RESCAN_SECONDS = 600
LIBRARY_MAX_FILES = 3000
LIBRARY_MAX_BYTES = 30 * 1024 * 1024
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
EMBED_BATCH = 16
DOCUMENT_PAGE_CHARS = 6000
SEARCH_RESULTS_MAX = 10
GIT_ACTIONS = ("status", "log", "diff", "show", "branch", "blame", "grep", "ls-files", "clone", "pull")
GIT_BLOCKED_ARGS = ("--output", "--upload-pack", "--receive-pack", "--exec", "--config", "-c", "-u", "--ext-diff", "--open-files-in-pager")
REPOS_DIR = "~/repos"

mcp = FastMCP("research")
index_lock = threading.Lock()


@mcp.tool()
def web_search(query: str, results: int = 6, recent: str = "") -> str:
    """Search the web (private SearXNG). Returns titles, links and snippets; read a result with fetch or browser_open.
    recent: optionally 'day', 'week', 'month' or 'year' to only get recent pages (good for news)."""
    params = {"q": query, "format": "json", "safesearch": "0"}
    if recent in ("day", "week", "month", "year"):
        params["time_range"] = recent
    try:
        with urllib.request.urlopen(f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}", timeout=30) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return f"search is unavailable: {error}"
    found = data.get("results") or []
    if not found:
        suggestions = ", ".join(data.get("suggestions") or [])
        return f"No results for: {query}" + (f". Try: {suggestions}" if suggestions else "")
    lines = []
    for number, item in enumerate(found[: max(1, min(int(results), SEARCH_RESULTS_MAX))], start=1):
        snippet = " ".join((item.get("content") or "").split())[:300]
        date = f" ({item['publishedDate'][:10]})" if item.get("publishedDate") else ""
        lines.append(f"{number}. {item.get('title', '').strip()}{date}\n   {item.get('url')}\n   {snippet}")
    answers = [a if isinstance(a, str) else a.get("answer", "") for a in data.get("answers") or []]
    header = f"Quick answer: {answers[0]}\n\n" if answers and answers[0] else ""
    return header + "\n".join(lines)


def _extract(path_expr: str) -> tuple[int, str]:
    return remote_text(f"doc2text {path_expr}", timeout=600)


@mcp.tool()
def read_document(path_or_url: str, start: int = 0) -> str:
    """Read a document as text: PDF (scanned ones too), Word, OpenDocument, EPUB, RTF, HTML, Markdown, Excel/CSV,
    images with text (OCR), or plain text. Give a path on your computer (relative paths are in ~/projects) or a URL.
    Long documents come back in parts: call again with the `start` value it gives you to continue."""
    target = path_or_url.strip()
    if target.startswith(("http://", "https://")):
        name = os.path.basename(urllib.parse.urlparse(target).path) or "document"
        code, output = remote_text(
            f"mkdir -p ~/Downloads && cd ~/Downloads && curl -sSLf -m 120 -o {shlex.quote(name)} {shlex.quote(target)} && echo ok",
            timeout=150,
        )
        if code != 0:
            return f"could not download {target}: {output.strip()[:300]}"
        path_expr = f'"$HOME"/Downloads/{shlex.quote(name)}'
    else:
        path_expr = quoted(target)
    code, output = _extract(path_expr)
    if code != 0:
        return output.strip()[:500] or f"could not read {target}"
    start = max(0, int(start))
    part = output[start:start + DOCUMENT_PAGE_CHARS]
    if not part.strip():
        return "(no text found)" if start == 0 else "(end of document)"
    end = start + len(part)
    more = f"\n\n[characters {start}-{end} of {len(output)}; call read_document again with start={end} for more]" if end < len(output) else ""
    return part + more


@mcp.tool()
def git(repo: str, action: str = "status", args: str = "") -> str:
    """Read-only git on your computer. repo: a folder (relative paths are in ~/projects, e.g. 'myapp' or '~/repos/x').
    action: status, log, diff, show, branch, blame, grep, ls-files, clone (repo = URL, cloned into ~/repos) or pull.
    args: extra arguments, e.g. '--oneline -20' for log, or 'HEAD~1' for show. Nothing is ever pushed."""
    if action not in GIT_ACTIONS:
        return f"action must be one of: {', '.join(GIT_ACTIONS)}"
    try:
        extra = shlex.split(args)
    except ValueError as error:
        return f"could not parse args: {error}"
    if any(arg.split("=")[0] in GIT_BLOCKED_ARGS for arg in extra):
        return "Those git options aren't allowed here."
    quoted_args = " ".join(shlex.quote(arg) for arg in extra)
    if action == "clone":
        if not repo.startswith(("https://", "http://", "git@")):
            return "To clone, repo must be a URL."
        command = f"mkdir -p {REPOS_DIR} && cd {REPOS_DIR} && git clone --quiet {quoted_args} -- {shlex.quote(repo)} && ls -1t | head -1"
    else:
        pager = "--no-pager" if action in ("log", "diff", "show", "blame", "grep") else ""
        command = f"cd {quoted(repo)} && git {pager} {action} {quoted_args}"
    code, output = remote_text(f"GIT_TERMINAL_PROMPT=0 bash -c {shlex.quote(command)} 2>&1", timeout=300)
    return f"exit code {code}\n{clip(output) or '(no output)'}"


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(LIBRARY_DB), exist_ok=True)
    connection = sqlite3.connect(LIBRARY_DB, timeout=30)
    connection.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, error TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, path TEXT, position INTEGER, content TEXT, vector BLOB)")
    connection.execute("CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path)")
    connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return connection


def _embed(texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": EMBED_MODEL, "input": texts, "keep_alive": "5m", "options": {"num_gpu": 0}}).encode()
    request = urllib.request.Request(f"{OLLAMA_URL}/api/embed", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        vectors = json.loads(response.read())["embeddings"]
    normalised = []
    for vector in vectors:
        length = math.sqrt(sum(value * value for value in vector)) or 1.0
        normalised.append([value / length for value in vector])
    return normalised


def _chunks(content: str) -> list[str]:
    content = "\n".join(line.rstrip() for line in content.splitlines() if line.strip())
    pieces, position = [], 0
    while position < len(content):
        pieces.append(content[position:position + CHUNK_CHARS])
        position += CHUNK_CHARS - CHUNK_OVERLAP
    return pieces


def _index_library(force: bool = False) -> str:
    with index_lock:
        connection = _db()
        try:
            last = connection.execute("SELECT value FROM meta WHERE key = 'scanned'").fetchone()
            if not force and last and time.time() - float(last[0]) < LIBRARY_RESCAN_SECONDS:
                return "up to date"
            code, listing = remote_text(
                f"mkdir -p {LIBRARY_DIR} && find {LIBRARY_DIR} -type f -not -name '.*' -size -{LIBRARY_MAX_BYTES // 1024}k -printf '%T@\\t%s\\t%p\\n' | head -{LIBRARY_MAX_FILES}",
                timeout=60,
            )
            if code != 0:
                return f"could not list the library: {listing.strip()[:300]}"
            current = {}
            for line in listing.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    current[parts[2]] = (float(parts[0]), int(parts[1]))
            known = {row[0]: row[1] for row in connection.execute("SELECT path, mtime FROM files")}
            removed = [path for path in known if path not in current]
            for path in removed:
                connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
                connection.execute("DELETE FROM files WHERE path = ?", (path,))
            changed = [path for path, (mtime, _) in current.items() if known.get(path) != mtime]
            failures = 0
            for path in changed:
                code, content = _extract(shlex.quote(path))
                connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
                error = None if code == 0 else content.strip()[:200]
                if error is None:
                    pieces = _chunks(content)
                    for start in range(0, len(pieces), EMBED_BATCH):
                        batch = pieces[start:start + EMBED_BATCH]
                        vectors = _embed([f"search_document: {piece}" for piece in batch])
                        connection.executemany(
                            "INSERT INTO chunks (path, position, content, vector) VALUES (?, ?, ?, ?)",
                            [(path, start + offset, piece, array.array("f", vector).tobytes()) for offset, (piece, vector) in enumerate(zip(batch, vectors))],
                        )
                else:
                    failures += 1
                connection.execute("INSERT OR REPLACE INTO files (path, mtime, size, error) VALUES (?, ?, ?, ?)", (path, current[path][0], current[path][1], error))
                connection.commit()
            connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('scanned', ?)", (str(time.time()),))
            connection.commit()
            return f"{len(current)} files, {len(changed)} (re)indexed, {len(removed)} removed, {failures} unreadable"
        finally:
            connection.close()


@mcp.tool()
def search_library(query: str, results: int = 5) -> str:
    """Search the documents in your library (~/library on your computer) by meaning, e.g. 'notes about the server move'.
    Returns the most relevant passages with their files. Save documents into ~/library to make them searchable."""
    try:
        status = _index_library()
        query_vector = _embed([f"search_query: {query}"])[0]
    except urllib.error.HTTPError as error:
        if "not found" in error.read().decode(errors="replace"):
            return f"The embedding model isn't installed. Ask the user to run: ai-pull {EMBED_MODEL}"
        raise
    except (urllib.error.URLError, TimeoutError) as error:
        return f"could not reach Ollama: {error}"
    connection = _db()
    try:
        scored = []
        for path, content, blob in connection.execute("SELECT path, content, vector FROM chunks"):
            vector = array.array("f")
            vector.frombytes(blob)
            scored.append((sum(a * b for a, b in zip(query_vector, vector)), path, content))
    finally:
        connection.close()
    if not scored:
        return f"The library is empty ({status}). Save documents into ~/library on your computer first."
    scored.sort(reverse=True)
    lines = [f"[{score:.2f}] {path}\n{clip(content, 700)}" for score, path, content in scored[: max(1, min(int(results), 10))]]
    return "\n\n".join(lines)


@mcp.tool()
def reindex_library() -> str:
    """Re-scan ~/library now (normally done automatically every 10 minutes when you search)."""
    return _index_library(force=True)


if __name__ == "__main__":
    mcp.run()
