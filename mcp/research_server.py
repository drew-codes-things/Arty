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
import re
import shlex
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

from remote import PRIVATE_EXIT, PRIVATE_REFUSAL, clip, private_path, quoted, resolve_outside_private_folders
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
GIT_ALLOWED_LONG = {
    "status": {"--short", "--branch", "--porcelain", "--long", "--untracked-files", "--ignored", "--ahead-behind", "--no-ahead-behind"},
    "log": {"--oneline", "--graph", "--stat", "--shortstat", "--name-only", "--name-status", "--since", "--until", "--after",
            "--before", "--author", "--committer", "--grep", "--max-count", "--all", "--decorate", "--no-merges", "--merges",
            "--format", "--pretty", "--abbrev-commit", "--follow", "--reverse", "--patch", "--date", "--first-parent",
            "--no-color", "--skip"},
    "diff": {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--cached", "--staged", "--word-diff",
             "--color-words", "--unified", "--ignore-all-space", "--ignore-space-change", "--no-color", "--patience",
             "--histogram", "--minimal", "--diff-filter", "--no-ext-diff"},
    "show": {"--stat", "--shortstat", "--name-only", "--name-status", "--oneline", "--format", "--pretty", "--no-patch",
             "--word-diff", "--abbrev-commit", "--no-color", "--no-ext-diff"},
    "branch": {"--list", "--all", "--remotes", "--verbose", "--contains", "--no-contains", "--merged", "--no-merged", "--sort",
               "--show-current", "--format", "--points-at", "--no-color"},
    "blame": {"--porcelain", "--line-porcelain", "--show-email", "--date", "--root", "--show-name", "--show-number"},
    "grep": {"--ignore-case", "--line-number", "--count", "--files-with-matches", "--files-without-match", "--word-regexp",
             "--extended-regexp", "--fixed-strings", "--perl-regexp", "--basic-regexp", "--max-count", "--untracked",
             "--cached", "--context", "--after-context", "--before-context", "--heading", "--break", "--invert-match",
             "--max-depth", "--no-color", "--all-match"},
    "ls-files": {"--cached", "--deleted", "--modified", "--others", "--exclude-standard", "--stage", "--unmerged", "--killed"},
    "clone": {"--depth", "--branch", "--single-branch", "--no-single-branch", "--no-tags", "--shallow-since", "--sparse",
              "--filter"},
    "pull": {"--ff-only", "--no-rebase", "--depth"},
}
GIT_ALLOWED_SHORT = {
    "status": "sbu",
    "log": "pn",
    "diff": "wbU",
    "show": "sw",
    "branch": "avrl",
    "blame": "wesL",
    "grep": "inlcLwEFPGvhHeABCm",
    "ls-files": "cdmosu",
    "clone": "b",
    "pull": "",
}
GIT_SHORT_TAKING_VALUE = "uUnABCmeLb"
GIT_NO_EXTRA_NAMES = ("pull", "clone")
GIT_BRANCH_LISTING = {"--list", "-l", "--all", "-a", "--remotes", "-r", "--contains", "--no-contains", "--merged",
                      "--no-merged", "--points-at", "--sort", "--format"}
GIT_DEPTH = "--depth"
GIT_SEPARATE_VALUE_OPTIONS = {"--depth", "--branch", "-b", "--shallow-since", "--filter"}
GIT_NUMBER = re.compile(r"^-\d+$")
GIT_END_OF_OPTIONS = "--"
REPOS_DIR = "~/repos"
UNSAFE_NAME_CHARACTERS = re.compile(r"[\x00-\x1f/\\]")
DEFAULT_DOWNLOAD_NAME = "document"

mcp = FastMCP("research")
index_lock = threading.Lock()
index_state = {"thread": None, "status": "not indexed yet"}


class EmbedModelMissing(RuntimeError):
    pass


class EmbedRefused(RuntimeError):
    pass


class OllamaUnreachable(RuntimeError):
    pass


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
    return remote_text(f"{resolve_outside_private_folders(path_expr)} doc2text \"$target\"", timeout=600)


def download_name(url: str) -> str:
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    return UNSAFE_NAME_CHARACTERS.sub("_", name).lstrip("-.").strip() or DEFAULT_DOWNLOAD_NAME


def download_script(url: str) -> str:
    name = download_name(url)
    stem, extension = os.path.splitext(name)
    return (
        f"mkdir -p ~/Downloads && cd ~/Downloads && name={shlex.quote(name)} && n=1 &&"
        f" while [ -e \"$name\" ]; do name={shlex.quote(stem)}\" ($n)\"{shlex.quote(extension)}; n=$((n + 1)); done &&"
        f" {{ curl -sSLf -m 120 -o \"$name\" {shlex.quote(url)} || {{ rm -f \"$name\"; exit 22; }}; }} && printf '%s\\n' \"$name\""
    )


@mcp.tool()
def read_document(path_or_url: str, start: int = 0) -> str:
    """Read a document as text: PDF (scanned ones too), Word, OpenDocument, EPUB, RTF, HTML, Markdown, Excel/CSV,
    images with text (OCR), or plain text. Give a path on your computer (relative paths are in ~/projects) or a URL.
    Long documents come back in parts: call again with the `start` value it gives you to continue."""
    target = path_or_url.strip()
    if target.startswith(("http://", "https://")):
        code, output = remote_text(download_script(target), timeout=150)
        lines = output.strip().splitlines()
        if code != 0 or not lines:
            return f"could not download {target}: {output.strip()[:300]}"
        path_expr = f'"$HOME"/Downloads/{shlex.quote(lines[-1])}'
    elif private_path(target):
        return PRIVATE_REFUSAL
    else:
        path_expr = quoted(target)
    code, output = _extract(path_expr)
    if code == PRIVATE_EXIT:
        return PRIVATE_REFUSAL
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
    args: extra arguments, e.g. '--oneline -20' for log, or 'HEAD~1' for show. Nothing is ever pushed; only clone and pull change files."""
    if action not in GIT_ACTIONS:
        return f"action must be one of: {', '.join(GIT_ACTIONS)}"
    try:
        extra = shlex.split(args)
    except ValueError as error:
        return f"could not parse args: {error}"
    if not git_arguments_allowed(action, extra):
        return "Those git options aren't allowed here."
    quoted_args = " ".join(shlex.quote(arg) for arg in extra)
    if action == "clone":
        if not repo.startswith(("https://", "http://", "git@")):
            return "To clone, repo must be a URL."
        command = f"mkdir -p {REPOS_DIR} && cd {REPOS_DIR} && git clone --quiet {quoted_args} -- {shlex.quote(repo)} && ls -1t | head -1"
    else:
        pager = "--no-pager" if action in ("log", "diff", "show", "blame", "grep") else ""
        if private_path(repo):
            return PRIVATE_REFUSAL
        guard = resolve_outside_private_folders(quoted(repo))
        command = f"{guard} cd -- \"$target\" && git {pager} {action} {quoted_args}"
    code, output = remote_text(f"GIT_TERMINAL_PROMPT=0 bash -c {shlex.quote(command)} 2>&1", timeout=300)
    if code == PRIVATE_EXIT:
        return PRIVATE_REFUSAL
    return f"exit code {code}\n{clip(output) or '(no output)'}"


def short_option_allowed(action: str, option: str) -> bool:
    if action in ("log", "show") and GIT_NUMBER.match(option):
        return True
    allowed = GIT_ALLOWED_SHORT.get(action, "")
    for position, letter in enumerate(option[1:], start=1):
        if letter not in allowed:
            return False
        if letter in GIT_SHORT_TAKING_VALUE:
            return position == len(option) - 1 or option[position + 1:].isalnum()
    return True


def git_option_allowed(action: str, option: str) -> bool:
    if option.startswith("--"):
        return option.split("=", 1)[0] in GIT_ALLOWED_LONG.get(action, set())
    return short_option_allowed(action, option)


def git_arguments_allowed(action: str, arguments: list[str]) -> bool:
    options_ended = False
    names = []
    for argument in arguments:
        if options_ended:
            names.append(argument)
        elif argument == GIT_END_OF_OPTIONS:
            options_ended = True
        elif argument.startswith("-") and len(argument) > 1:
            if not git_option_allowed(action, argument):
                return False
        else:
            names.append(argument)
    if action in GIT_NO_EXTRA_NAMES:
        return set(names) <= option_values(arguments)
    if action == "branch" and names:
        return bool(GIT_BRANCH_LISTING & set(arguments)) and GIT_END_OF_OPTIONS not in arguments
    return True


def option_values(arguments: list[str]) -> set[str]:
    return {following for current, following in zip(arguments, arguments[1:])
            if current in GIT_SEPARATE_VALUE_OPTIONS and (current != GIT_DEPTH or following.isdigit())}


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
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            vectors = json.loads(response.read())["embeddings"]
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        if "not found" in detail:
            raise EmbedModelMissing(f"the embedding model {EMBED_MODEL} isn't installed") from None
        raise EmbedRefused(f"Ollama said {error.code}: {detail[:200]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise OllamaUnreachable(f"could not reach Ollama: {error}") from None
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
                    try:
                        _store_chunks(connection, path, content)
                    except EmbedRefused as refused:
                        connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
                        error = str(refused)[:200]
                if error is not None:
                    failures += 1
                connection.execute("INSERT OR REPLACE INTO files (path, mtime, size, error) VALUES (?, ?, ?, ?)", (path, current[path][0], current[path][1], error))
                connection.commit()
            connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('scanned', ?)", (str(time.time()),))
            connection.commit()
            return f"{len(current)} files, {len(changed)} (re)indexed, {len(removed)} removed, {failures} unreadable"
        finally:
            connection.close()


def _store_chunks(connection: sqlite3.Connection, path: str, content: str):
    pieces = _chunks(content)
    for start in range(0, len(pieces), EMBED_BATCH):
        batch = pieces[start:start + EMBED_BATCH]
        vectors = _embed([f"search_document: {piece}" for piece in batch])
        connection.executemany(
            "INSERT INTO chunks (path, position, content, vector) VALUES (?, ?, ?, ?)",
            [(path, start + offset, piece, array.array("f", vector).tobytes()) for offset, (piece, vector) in enumerate(zip(batch, vectors))],
        )


def _index_and_remember(force: bool = False):
    try:
        status = _index_library(force)
    except (RuntimeError, sqlite3.Error) as error:
        status = f"indexing stopped: {error}"
        print(f"library: {status}", file=sys.stderr, flush=True)
    index_state["status"] = status


def _index_in_background(force: bool = False) -> str:
    running = index_state["thread"]
    if index_lock.locked() or (running is not None and running.is_alive()):
        return "already indexing"
    thread = threading.Thread(target=_index_and_remember, args=(force,), daemon=True)
    index_state["thread"] = thread
    thread.start()
    return "checking the library for new files in the background"


def index_thread_done(timeout: float):
    thread = index_state["thread"]
    if thread is not None:
        thread.join(timeout)


@mcp.tool()
def search_library(query: str, results: int = 5) -> str:
    """Search the documents in your library (~/library on your computer) by meaning, e.g. 'notes about the server move'.
    Returns the most relevant passages with their files. Save documents into ~/library to make them searchable."""
    _index_in_background()
    try:
        query_vector = _embed([f"search_query: {query}"])[0]
    except EmbedModelMissing:
        return f"The embedding model isn't installed. Ask the user to run: ai-pull {EMBED_MODEL}"
    except (EmbedRefused, OllamaUnreachable) as error:
        return str(error)
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
        return (f"The library is empty or still being indexed ({index_state['status']}). Save documents into ~/library "
                "on your computer, or try again in a few minutes.")
    scored.sort(reverse=True)
    lines = [f"[{score:.2f}] {path}\n{clip(content, 700)}" for score, path, content in scored[: max(1, min(int(results), 10))]]
    return "\n\n".join(lines)


@mcp.tool(description="Re-scan ~/library now, in the background (normally done automatically every 10 minutes when you search).\n"
                       "Returns at once with the result of the last scan; search_library works on what is indexed so far.")
def reindex_library() -> str:
    last = index_state["status"]
    started = _index_in_background(force=True)
    if started == "already indexing":
        return f"Already re-scanning the library. Last finished scan: {last}"
    return f"Re-scanning the library in the background. Last finished scan: {last}"


if __name__ == "__main__":
    mcp.run()
