import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FetchTimeout
from typing import NamedTuple
from urllib.parse import urlparse

import connections
import research

log = logging.getLogger("brain.docs")

FETCH_TOOL = "fetch__fetch"
PAGE_CHARS = 30000
FETCHED_CHARS = PAGE_CHARS + 500
FETCH_SECONDS = 6.0
EXCERPT_CHARS = 4000
CODE_BONUS = 3
HEADING_BONUS = 4
DISTINCT_WORD_WEIGHT = 2
UNIT_LENGTH_STEP = 400

DOCS_HOSTS = {"pypi.org", "github.com", "peps.python.org", "developer.mozilla.org", "pkg.go.dev", "go.dev", "kotlinlang.org",
              "nodejs.org", "react.dev", "kubernetes.io", "learn.microsoft.com", "typescriptlang.org", "man7.org",
              "fastapi.tiangolo.com", "numpy.org", "pandas.pydata.org", "rust-lang.org", "developer.android.com"}
DOCS_HOST_PREFIXES = ("docs.", "doc.")
DOCS_HOST_SUFFIXES = (".readthedocs.io", ".readthedocs.org", ".palletsprojects.com")

FETCH_HEADER = re.compile(r"^\s*Contents of \S+:\s*\n")
FETCH_NOTE = re.compile(r"<error>[^<]*</error>")
LINK = re.compile(r"!?\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^#{1,6}\s")
NAME_DECORATION = re.compile(r"[*\\`]")

DOTTED_CALL = re.compile(r"\b([a-z_]\w*)\.([a-z_]\w*)\(\)", re.IGNORECASE)
DUNDER = re.compile(r"^__\w+__$")
VERSIONED_NAME = re.compile(r"\b([a-z][a-z+#-]{2,30})\s?v?\d+(?:\.\d+)*\b", re.IGNORECASE)
COMMAND_LINE_TOOL = re.compile(r"\b([\w.-]{3,})\s+(?:cli|command[- ]line(?: tool)?)\b", re.IGNORECASE)
QUOTED_NAME = re.compile(r"['\"`‘’]([a-z_][\w.-]{2,})['\"`‘’]", re.IGNORECASE)
NOT_NAMES = research.STOP_WORDS | research.ASKING_WORDS | {"since", "version", "versions", "release", "python's"}

DOCS_NOTE = ("[read from {url} just now ({kind}). Where it differs from what you remember (names, imports, how to call or "
             "set it up), follow this page, not your memory: use its names and its code, and for the library it covers "
             "use only the classes, methods and functions it shows, never others from memory. If the user named a "
             "function, option or flag that isn't on it, say you couldn't confirm it exists rather than describing "
             "it]\n{excerpt}")
UNCONFIRMED_NOTE = ("[read {url} just now: it never mentions {names}, and neither do the search results, so nothing "
                    "found confirms that {names} exists. Say you couldn't confirm it rather than describing it]")
OFFICIAL = "the official documentation"
NOT_OFFICIAL = ("a page about this, not the official documentation; any setup or versions on it are someone else's, not "
                "the user's")


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_docs_site(url: str) -> bool:
    host = host_of(url)
    return host in DOCS_HOSTS or host.startswith(DOCS_HOST_PREFIXES) or host.endswith(DOCS_HOST_SUFFIXES)


def described_results(search_text: str) -> dict[str, str]:
    starts = [match.start() for match in research.RESULT_START.finditer(search_text)]
    described = {}
    for start, end in zip(starts, starts[1:] + [len(search_text)]):
        entry = search_text[start:end]
        if found := research.RESULT_URL.search(entry):
            described.setdefault(found.group(1), entry.replace(found.group(1), ""))
    return described


def is_skipped_site(url: str) -> bool:
    host = host_of(url)
    return any(host == site or host.endswith("." + site) for site in research.SKIPPED_SITES)


def page_to_read(search_text: str, question: str) -> str:
    described = described_results(search_text)
    urls = [url for url in described if not is_skipped_site(url)]
    official = [url for url in urls if is_docs_site(url)]
    if not official:
        return urls[0] if urls else ""
    words = research.key_words(question)
    return max(official, key=lambda url: word_hits(described[url], words))


def named_things(question: str) -> set[str]:
    names = set()
    for owner, attribute in DOTTED_CALL.findall(question):
        names.add(owner if DUNDER.match(attribute) else attribute)
    names.update(VERSIONED_NAME.findall(question))
    names.update(COMMAND_LINE_TOOL.findall(question))
    names.update(QUOTED_NAME.findall(question))
    return {name.lower() for name in names if name.lower() not in NOT_NAMES}


def names_all(text: str, names: set[str]) -> bool:
    plain = NAME_DECORATION.sub("", text).lower()
    return all(NAME_DECORATION.sub("", name) in plain for name in names)


def readable(page: str) -> str:
    text = FETCH_NOTE.sub("", FETCH_HEADER.sub("", page, count=1))
    return LINK.sub(r"\1", text).replace("¶", "").strip()


class Unit(NamedTuple):
    heading: str
    text: str


def units_of(text: str) -> list[Unit]:
    units, current, in_code, heading = [], [], False, ""

    def close():
        if any(line.strip() for line in current):
            units.append(Unit(heading, "\n".join(current).strip("\n")))
        current.clear()

    for line in text.splitlines():
        if FENCE.match(line):
            if not in_code:
                close()
            current.append(line)
            in_code = not in_code
            if not in_code:
                close()
            continue
        if in_code:
            current.append(line)
            continue
        if HEADING.match(line):
            close()
            heading = line.strip()
            continue
        if not line.strip():
            close()
            continue
        current.append(line)
    if in_code:
        current.append("```")
    close()
    return joined_to_their_code(units)


def joined_to_their_code(units: list[Unit]) -> list[Unit]:
    joined = []
    for unit in units:
        follows_prose = joined and joined[-1].heading == unit.heading and not FENCE.match(joined[-1].text.splitlines()[-1])
        if FENCE.match(unit.text) and follows_prose:
            joined[-1] = Unit(unit.heading, f"{joined[-1].text}\n\n{unit.text}")
        else:
            joined.append(unit)
    return joined


def word_hits(text: str, words: set[str]) -> tuple[int, int]:
    lowered = text.lower()
    counts = [lowered.count(word) for word in words]
    return sum(1 for count in counts if count), sum(counts)


def unit_score(unit: Unit, words: set[str]) -> float:
    distinct, hits = word_hits(unit.text, words)
    if not hits:
        return 0
    bonus = (CODE_BONUS if "```" in unit.text else 0) + (HEADING_BONUS if word_hits(unit.heading, words)[0] else 0)
    return (DISTINCT_WORD_WEIGHT * distinct + hits + bonus) / (1 + len(unit.text) // UNIT_LENGTH_STEP)


def excerpt(text: str, question: str, limit: int = EXCERPT_CHARS) -> str:
    words = research.key_words(question)
    units = units_of(text)
    scores = [unit_score(unit, words) for unit in units]
    ranked = sorted((index for index, score in enumerate(scores) if score > 0), key=lambda index: (-scores[index], index))
    chosen, used = set(), 0
    for index in ranked:
        size = len(units[index].heading) + len(units[index].text) + 4
        if used + size > limit:
            continue
        chosen.add(index)
        used += size
    return "\n\n".join(with_headings([units[index] for index in sorted(chosen)]))


def with_headings(units: list[Unit]) -> list[str]:
    parts, shown = [], ""
    for unit in units:
        if unit.heading and unit.heading != shown:
            parts.append(unit.heading)
            shown = unit.heading
        parts.append(unit.text)
    return parts


def fetch_page(url: str) -> str:
    return connections.hub.call(FETCH_TOOL, {"url": url, "max_length": PAGE_CHARS}, limit=FETCHED_CHARS)


def fetched_in_time(url: str, fetch) -> str:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="docs-page")
    try:
        return str(pool.submit(fetch, url).result(timeout=FETCH_SECONDS))
    except FetchTimeout:
        log.warning("%s took over %s s to fetch; answering from the search results", url, FETCH_SECONDS)
        return ""
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def is_usable(text: str) -> bool:
    return len(text) >= research.MIN_PAGE_CHARS and not research.BOT_CHECK.search(text[:1500])


def read_top_page(search_text: str, question: str, fetch=fetch_page) -> str:
    url = page_to_read(search_text, question)
    if not url:
        return ""
    text = readable(fetched_in_time(url, fetch))
    if not is_usable(text):
        log.info("%s gave no readable page; answering from the search results", url)
        return ""
    missing = sorted(name for name in named_things(question) if not names_all(text, {name}))
    if missing:
        log.info("%s doesn't name %s; leaving it out", url, missing)
        unconfirmed = [name for name in missing if not names_all(search_text, {name})]
        return UNCONFIRMED_NOTE.format(url=url, names=" or ".join(unconfirmed)) if unconfirmed else ""
    found = excerpt(text, question)
    if not found:
        log.info("nothing on %s matches the question; leaving it out", url)
        return ""
    return DOCS_NOTE.format(url=url, kind=OFFICIAL if is_docs_site(url) else NOT_OFFICIAL, excerpt=found)
