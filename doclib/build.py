import gzip
import json
import logging
import os
import re
import sqlite3
import subprocess
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from doclib.convert import Section, html_sections, rst_document
from doclib.fetch import read_meta
from doclib.sources import (DEBIAN_REFERENCE_PAGE, DEVDOCS_MISSING_LICENCES, DEVDOCS_PAGE, PEP_PAGE, PEPS_LICENCE,
                            PROXMOX_LICENCE, PSF_LICENCE, PYTHON_PAGE, Source)

log = logging.getLogger("doclib.build")

SCHEMA = """
CREATE TABLE sources(key TEXT PRIMARY KEY, family TEXT NOT NULL, version TEXT NOT NULL, name TEXT NOT NULL,
                     licence TEXT NOT NULL, release TEXT NOT NULL, fetched TEXT NOT NULL, sections INTEGER NOT NULL);
CREATE TABLE sections(id INTEGER PRIMARY KEY, source TEXT NOT NULL, page TEXT NOT NULL, heading TEXT NOT NULL,
                      url TEXT NOT NULL, body TEXT NOT NULL, has_code INTEGER NOT NULL, about TEXT NOT NULL);
CREATE VIRTUAL TABLE sections_fts USING fts5(page, heading, body, content='', tokenize='porter unicode61');
"""
PYTHON_SKIPPED = re.compile(r"(^|/)(_static|_sources|_images|_downloads)/|(^|/)(genindex[^/]*|search|py-modindex|"
                            r"changelog|contents|copyright|license|bugs|about|download)\.html$")
DEVDOCS_SKIPPED_PAGES = {"index", "_index"}
KEPT_PEP_STATUSES = {"Final", "Active", "Accepted", "Provisional"}
WHATS_NEW_PAGE = re.compile(r"(?:^|/)whatsnew/(\d+\.\d+)\.html$")
PEP_FILE = re.compile(r"(?:^|/)peps/pep-(\d{4})\.rst$")
DEBIAN_PAGE = re.compile(r"/debian-reference-common/docs/([\w.-]+\.en\.html)$")
HTML_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAGS = re.compile(r"<[^>]+>")
ANCHOR_WORDS = re.compile(r"[^A-Za-z0-9]+")
INSERT_BATCH = 2000
TITLE_SEPARATOR = " \u2014 "


class Page(NamedTuple):
    title: str
    url: str
    sections: list[Section]
    about: str = ""


def url_with_anchor(url: str, anchor: str) -> str:
    return f"{url}#{anchor}" if anchor else url


def plain_text(html: str) -> str:
    return " ".join(TAGS.sub(" ", html).split())


def devdocs_titles(index: dict) -> dict[str, str]:
    titles = {}
    for entry in index.get("entries", []):
        path = entry.get("path", "")
        if "#" not in path:
            titles.setdefault(path, entry.get("name", ""))
    return titles


def devdocs_pages(source: Source, folder: Path) -> Iterator[Page]:
    with gzip.open(folder / "db.json.gz", "rt", encoding="utf-8") as opened:
        pages = json.load(opened)
    with gzip.open(folder / "index.json.gz", "rt", encoding="utf-8") as opened:
        titles = devdocs_titles(json.load(opened))
    for path, html in pages.items():
        if path in DEVDOCS_SKIPPED_PAGES:
            continue
        url = DEVDOCS_PAGE.format(slug=source.location, path=path)
        yield Page(titles.get(path, ""), url, html_sections(html, titles.get(path, "")))


def tar_texts(archive: Path, wanted) -> Iterator[tuple[str, str]]:
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if member.isfile() and wanted(member.name):
                extracted = tar.extractfile(member)
                if extracted is not None:
                    yield member.name, extracted.read().decode("utf-8", errors="replace")


def page_title(html: str) -> str:
    found = HTML_TITLE.search(html)
    return plain_text(found.group(1)).split(TITLE_SEPARATOR)[0].strip() if found else ""


def python_pages(source: Source, folder: Path) -> Iterator[Page]:
    def wanted(name: str) -> bool:
        return name.endswith(".html") and not PYTHON_SKIPPED.search(name)

    for name, html in tar_texts(folder / "docs-html.tar.bz2", wanted):
        path = name.split("/", 1)[1] if "/" in name else name
        whats_new = WHATS_NEW_PAGE.search(path)
        yield Page(page_title(html), PYTHON_PAGE.format(version=source.version, path=path), html_sections(html),
                   whats_new.group(1) if whats_new else "")


def pep_pages(folder: Path) -> Iterator[Page]:
    for name, text in tar_texts(folder / "peps.tar.gz", lambda name: PEP_FILE.search(name) is not None):
        number = int(PEP_FILE.search(name).group(1))
        fields, sections = rst_document(text)
        if fields.get("Status", "").strip() not in KEPT_PEP_STATUSES:
            continue
        title = f"PEP {number}: {fields.get('Title', '')}"
        renamed = [Section(title, section.heading.replace(section.page_title, title, 1), section.anchor, section.body)
                   for section in sections]
        yield Page(title, PEP_PAGE.format(number=number), renamed, fields.get("Python-Version", "").strip())


def single_page(folder: Path, url: str) -> Iterator[Page]:
    html = (folder / "page.html").read_text(encoding="utf-8", errors="replace")
    yield Page(page_title(html), url, html_sections(html))


def deb_texts(deb: Path, wanted) -> Iterator[tuple[str, str]]:
    unpack = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(deb)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=unpack.stdout, mode="r|") as tar:
            for member in tar:
                if member.isfile() and wanted(member.name):
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        yield member.name, extracted.read().decode("utf-8", errors="replace")
    finally:
        unpack.stdout.close()
        if unpack.wait() != 0:
            raise RuntimeError(f"dpkg-deb couldn't unpack {deb}")


def debian_pages(folder: Path) -> Iterator[Page]:
    debs = sorted(folder.glob("*.deb"))
    if not debs:
        return
    for name, html in deb_texts(debs[-1], lambda name: DEBIAN_PAGE.search(name) is not None):
        page = DEBIAN_PAGE.search(name).group(1)
        yield Page(page_title(html), DEBIAN_REFERENCE_PAGE.format(name=page), html_sections(html))


def pages_of(source: Source, folder: Path) -> Iterator[Page]:
    if source.kind == "devdocs":
        return devdocs_pages(source, folder)
    if source.kind == "python":
        return python_pages(source, folder)
    if source.kind == "peps":
        return pep_pages(folder)
    if source.kind == "page":
        return single_page(folder, source.location)
    if source.kind == "deb":
        return debian_pages(folder)
    raise ValueError(f"unknown source kind {source.kind}")


def licence_of(source: Source, meta: dict) -> str:
    if source.kind == "devdocs":
        stated = plain_text(meta.get("attribution", "")).replace("&copy;", "(c)").replace("&ndash;", "-")
        return stated if stated.strip(". ") else DEVDOCS_MISSING_LICENCES.get(source.location, "not stated by DevDocs")
    if source.kind == "python":
        return PSF_LICENCE
    if source.kind == "peps":
        return PEPS_LICENCE
    if source.kind == "page":
        return PROXMOX_LICENCE
    return "GPL-2+ (Debian Reference, Osamu Aoki)"


def anchor_words(anchor: str) -> str:
    return ANCHOR_WORDS.sub(" ", anchor).strip()


def section_rows(source: Source, page: Page) -> Iterator[tuple]:
    for section in page.sections:
        title = section.page_title or page.title
        yield (source.key, title, section.heading, url_with_anchor(page.url, section.anchor), section.body,
               int("```" in section.body), page.about, anchor_words(section.anchor))


def insert_rows(connection: sqlite3.Connection, rows: list[tuple]):
    for row in rows:
        cursor = connection.execute(
            "INSERT INTO sections(source, page, heading, url, body, has_code, about) VALUES (?, ?, ?, ?, ?, ?, ?)", row[:7])
        connection.execute("INSERT INTO sections_fts(rowid, page, heading, body) VALUES (?, ?, ?, ?)",
                           (cursor.lastrowid, row[1], f"{row[2]} {row[7]}", row[4]))


def index_source(connection: sqlite3.Connection, source: Source, folder: Path) -> int:
    count, batch = 0, []
    for page in pages_of(source, folder):
        batch.extend(section_rows(source, page))
        if len(batch) >= INSERT_BATCH:
            insert_rows(connection, batch)
            count += len(batch)
            batch.clear()
    insert_rows(connection, batch)
    count += len(batch)
    meta = read_meta(folder)
    connection.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (source.key, source.family, source.version, source.name, licence_of(source, meta),
                        str(meta.get("release") or meta.get("file") or ""), str(meta.get("fetched", "")), count))
    connection.commit()
    return count


def build(sources: list[Source], raw_dir: Path, database: Path) -> dict[str, int]:
    building = database.with_name(database.name + ".new")
    building.unlink(missing_ok=True)
    connection = sqlite3.connect(building)
    counts = {}
    try:
        connection.executescript(SCHEMA)
        for source in sources:
            folder = raw_dir / source.key
            if not folder.exists():
                log.warning("%s has not been downloaded; left out", source.key)
                continue
            started = time.monotonic()
            try:
                counts[source.key] = index_source(connection, source, folder)
            except (OSError, ValueError, RuntimeError, tarfile.TarError, json.JSONDecodeError) as error:
                connection.rollback()
                log.error("%s couldn't be indexed and is left out: %s", source.key, error)
                continue
            log.info("%s: %d sections in %.0f s", source.key, counts[source.key], time.monotonic() - started)
        connection.execute("INSERT INTO sections_fts(sections_fts) VALUES ('optimize')")
        connection.commit()
    finally:
        connection.close()
    os.chmod(building, 0o644)
    building.replace(database)
    return counts
