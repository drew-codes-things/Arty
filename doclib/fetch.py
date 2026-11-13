import gzip
import json
import logging
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from doclib.sources import DEVDOCS_CATALOGUE, DEVDOCS_DOCUMENTS, PYTHON_ARCHIVE, Source

log = logging.getLogger("doclib.fetch")

USER_AGENT = "docs-library-refresh/1.0"
TIMEOUT_SECONDS = 120
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
META_FILE = "meta.json"
NOT_MODIFIED = 304


class FetchError(RuntimeError):
    pass


def read_meta(folder: Path) -> dict:
    try:
        return json.loads((folder / META_FILE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_meta(folder: Path, meta: dict):
    (folder / META_FILE).write_text(json.dumps(meta, indent=1, sort_keys=True))


def request_for(url: str, previous: dict) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if previous.get("etag"):
        headers["If-None-Match"] = previous["etag"]
    if previous.get("last_modified"):
        headers["If-Modified-Since"] = previous["last_modified"]
    return urllib.request.Request(url, headers=headers)


def download(url: str, target: Path, previous: dict | None = None) -> dict | None:
    previous = previous or {}
    if not target.exists():
        previous = {}
    partial = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(request_for(url, previous), timeout=TIMEOUT_SECONDS) as response, open(partial, "wb") as out:
            copied = 0
            while chunk := response.read(CHUNK_BYTES):
                copied += len(chunk)
                if copied > MAX_DOWNLOAD_BYTES:
                    raise FetchError(f"{url} is over {MAX_DOWNLOAD_BYTES} bytes; not kept")
                out.write(chunk)
            headers = response.headers
    except urllib.error.HTTPError as error:
        partial.unlink(missing_ok=True)
        if error.code == NOT_MODIFIED:
            return None
        raise FetchError(f"{url} answered HTTP {error.code}") from error
    except FetchError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise FetchError(f"couldn't download {url}: {error}") from error
    partial.replace(target)
    return {"url": url, "etag": headers.get("ETag", ""), "last_modified": headers.get("Last-Modified", ""),
            "bytes": copied, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def gzipped(source: Path, target: Path):
    with open(source, "rb") as plain, gzip.open(target, "wb", compresslevel=6) as packed:
        shutil.copyfileobj(plain, packed, CHUNK_BYTES)
    source.unlink()


def devdocs_catalogue(raw_dir: Path) -> dict[str, dict]:
    target = raw_dir / "devdocs-docs.json"
    download(DEVDOCS_CATALOGUE, target)
    return {entry["slug"]: entry for entry in json.loads(target.read_text())}


def fetch_devdocs(source: Source, folder: Path, catalogue: dict[str, dict]) -> bool:
    entry = catalogue.get(source.location)
    if entry is None:
        raise FetchError(f"DevDocs no longer lists {source.location}")
    meta = read_meta(folder)
    complete = (folder / "db.json.gz").exists() and (folder / "index.json.gz").exists()
    if complete and meta.get("mtime") == entry.get("mtime"):
        return False
    for name in ("db.json", "index.json"):
        plain = folder / name
        download(f"{DEVDOCS_DOCUMENTS.format(slug=source.location, name=name)}?{entry.get('mtime', '')}", plain)
        gzipped(plain, folder / f"{name}.gz")
    write_meta(folder, {"mtime": entry.get("mtime"), "release": entry.get("release") or "",
                        "attribution": entry.get("attribution") or "", "links": entry.get("links") or {},
                        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return True


def fetch_url(url: str, folder: Path, name: str) -> bool:
    meta = read_meta(folder)
    fresh = download(url, folder / name, meta)
    if fresh is None:
        return False
    write_meta(folder, fresh)
    return True


def fetch_deb(package: str, folder: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="doclib-deb-") as scratch:
        done = subprocess.run(["apt-get", "download", package], cwd=scratch, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if done.returncode != 0:
            raise FetchError(f"apt-get download {package} failed: {done.stderr.strip()[:300]}")
        fetched = sorted(Path(scratch).glob(f"{package}_*.deb"))
        if not fetched:
            raise FetchError(f"apt-get download {package} left no .deb file")
        newest = fetched[-1]
        if (folder / newest.name).exists():
            return False
        for old in folder.glob(f"{package}_*.deb"):
            old.unlink()
        shutil.move(newest, folder / newest.name)
    write_meta(folder, {"package": package, "file": newest.name,
                        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return True


def fetch_source(source: Source, raw_dir: Path, catalogue: dict[str, dict] | None) -> bool:
    folder = raw_dir / source.key
    folder.mkdir(parents=True, exist_ok=True)
    if source.kind == "devdocs":
        return fetch_devdocs(source, folder, catalogue or {})
    if source.kind == "python":
        return fetch_url(PYTHON_ARCHIVE.format(version=source.location), folder, "docs-html.tar.bz2")
    if source.kind == "peps":
        return fetch_url(source.location, folder, "peps.tar.gz")
    if source.kind == "page":
        return fetch_url(source.location, folder, "page.html")
    if source.kind == "deb":
        return fetch_deb(source.location, folder)
    raise FetchError(f"unknown source kind {source.kind}")


def fetch_all(sources: list[Source], raw_dir: Path) -> dict[str, str]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    catalogue = devdocs_catalogue(raw_dir) if any(source.kind == "devdocs" for source in sources) else None
    outcome = {}
    for source in sources:
        try:
            outcome[source.key] = "updated" if fetch_source(source, raw_dir, catalogue) else "unchanged"
        except FetchError as error:
            log.error("%s: %s", source.key, error)
            outcome[source.key] = f"failed: {error}"
        log.info("%s: %s", source.key, outcome[source.key])
    return outcome
