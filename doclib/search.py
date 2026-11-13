import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from doclib.sources import SOURCES, Source

FAMILY_WORDS = {
    "python": "python", "python3": "python", "cpython": "python", "pep": "python", "peps": "python",
    "javascript": "javascript", "js": "javascript", "ecmascript": "javascript", "mdn": "javascript",
    "html": "html", "css": "css", "http": "http",
    "typescript": "typescript", "ts": "typescript",
    "node": "node", "nodejs": "node", "npm": "node",
    "bash": "bash", "shellscript": "bash",
    "cpp": "cpp", "cplusplus": "cpp",
    "rust": "rust", "rustlang": "rust",
    "golang": "go",
    "java": "java", "jdk": "java", "openjdk": "java",
    "kotlin": "kotlin", "php": "php", "ruby": "ruby",
    "sqlite": "sqlite", "sqlite3": "sqlite",
    "postgres": "postgresql", "postgresql": "postgresql", "psql": "postgresql",
    "docker": "docker", "dockerfile": "docker",
    "git": "git",
    "linux": "linux", "manpage": "linux", "coreutils": "linux",
    "proxmox": "proxmox", "pve": "proxmox",
    "debian": "debian",
}
RELATED = {"node": ("javascript",), "typescript": ("javascript",), "bash": ("linux",), "linux": ("bash",),
           "debian": ("linux",), "html": ("javascript",)}
TOPIC_FAMILIES = {"http", "html", "css"}
NORMALISED = ((re.compile(r"\bc\+\+", re.IGNORECASE), " cpp "), (re.compile(r"\bc#", re.IGNORECASE), " csharp "),
              (re.compile(r"\bnode\.js\b", re.IGNORECASE), " nodejs "),
              (re.compile(r"\bshell script", re.IGNORECASE), " shellscript script"),
              (re.compile(r"\bman pages?\b", re.IGNORECASE), " manpage "))
GO_LANGUAGE = re.compile(r"(?<![.!?]\s)(?<!^)\bGo\b(?!-)|\bgo\s?1\.\d+|\bin go\b|"
                         r"^\s*Go\s+(?!to\b|ahead\b|back\b|on\b|for\b|and\b|with\b|away\b|home\b|out\b|into\b)\w", re.MULTILINE)
C_LANGUAGE = re.compile(r"(?<![\w+#])C(?![\w+#])(?!\s*:)|\bC\d\d\b|\bin c\b")
FAMILY_VERSION = re.compile(r"\b(python|node|nodejs|java|jdk|ruby|postgres(?:ql)?|go)\s?v?(\d+(?:\.\d+)?)\b", re.IGNORECASE)
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*")
STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "from", "as", "is", "are", "was",
    "be", "it", "its", "this", "that", "these", "those", "what", "which", "who", "how", "why", "when", "where", "do",
    "does", "did", "can", "could", "should", "would", "will", "i", "me", "my", "we", "you", "your", "use", "using",
    "used", "show", "give", "get", "make", "write", "want", "need", "example", "examples", "code", "please", "way",
    "best", "some", "any", "into", "about", "there", "their", "then", "than", "but", "not", "if", "so", "up", "out",
    "new", "version", "versions", "tell", "explain", "work", "works", "like", "via", "vs", "versus", "just",
    "doc", "docs", "documentation", "language", "programming",
}
CANDIDATES = 200
PAGE_WEIGHT = 6.0
HEADING_WEIGHT = 4.0
BODY_WEIGHT = 1.0
HEADING_HIT_WEIGHT = 0.6
COVERAGE_WEIGHT = 1.5
CODE_BONUS = 0.15
VERSION_BONUS = 1.0
IDENTIFIER_BONUS = 0.8
PAGE_NAME_BONUS = 0.5
RELATED_PENALTY = 0.6
KEPT_FAMILY_WORDS = {"dockerfile", "pep", "peps", "coreutils"}
IDENTIFIER = re.compile(r"[A-Za-z_][\w]*(?:(?:::|\.|#)[A-Za-z_][\w]*)+|[A-Za-z]\w*_\w+|[a-z]+[A-Z]\w*")
IDENTIFIER_SEPARATORS = re.compile(r"::|#|/|\s>\s")
PAGE_NAME = re.compile(r"[\w.+:-]+")
SNIPPET_CHARS = 300
FENCE_LINE = re.compile(r"^```.*$", re.MULTILINE)
CODE_TOKEN = re.compile(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*|\w\(\)|(?:^|\s)--?[a-z][\w-]*|::|\b[a-z]+_[a-z_]+\b|"
                        r"\b[a-z]+[A-Z]\w*\b|=>|->|\$\{|<<|\b\w+(?:Error|Exception)\b")


class Hit(NamedTuple):
    id: int
    source: str
    family: str
    version: str
    page: str
    heading: str
    url: str
    score: float
    snippet: str


class Plan(NamedTuple):
    terms: list[str]
    families: set[str]
    versions: dict[str, str]
    sources: list[str]
    identifiers: list[str] = []


def normalised(question: str) -> str:
    for pattern, replacement in NORMALISED:
        question = pattern.sub(replacement, question)
    return question


def named_families(question: str) -> set[str]:
    words = {word.lower() for word in WORD.findall(question)}
    families = {FAMILY_WORDS[word] for word in words if word in FAMILY_WORDS}
    if GO_LANGUAGE.search(question):
        families.add("go")
    if C_LANGUAGE.search(question):
        families.add("c")
    if families - TOPIC_FAMILIES:
        families -= {"http"}
    return families


def named_versions(question: str) -> dict[str, str]:
    versions = {}
    for name, number in FAMILY_VERSION.findall(question):
        family = FAMILY_WORDS.get(name.lower(), "go" if name.lower() == "go" else name.lower())
        versions.setdefault(family, number)
    return versions


def version_matches(wanted: str, version: str) -> bool:
    return version == wanted or version.startswith(wanted + ".")


def chosen_sources(families: set[str], versions: dict[str, str], sources: tuple[Source, ...] | None = None) -> list[str]:
    sources = SOURCES if sources is None else sources
    wanted = set(families)
    for family in families:
        wanted.update(RELATED.get(family, ()))
    chosen, defaulted = [], set()
    for source in sources:
        if wanted and source.family not in wanted:
            continue
        if not source.version:
            chosen.append(source.key)
            continue
        asked = versions.get(source.family)
        has_match = asked and any(other.family == source.family and version_matches(asked, other.version)
                                  for other in sources)
        if has_match:
            if version_matches(asked, source.version):
                chosen.append(source.key)
        elif source.family not in defaulted:
            defaulted.add(source.family)
            chosen.append(source.key)
    return chosen


def is_coding_question(question: str) -> bool:
    text = normalised(question)
    return bool(named_families(text)) or CODE_TOKEN.search(text) is not None


def query_terms(question: str) -> list[str]:
    terms = []
    for word in WORD.findall(question):
        lowered = word.lower()
        if lowered in STOP_WORDS or (lowered in FAMILY_WORDS and lowered not in KEPT_FAMILY_WORDS) or lowered.isdigit():
            continue
        if len(lowered) < 2 and "-" not in lowered:
            continue
        if lowered not in terms:
            terms.append(lowered)
    return terms


def plan_for(question: str) -> Plan:
    text = normalised(question)
    families = named_families(text)
    versions = named_versions(text)
    identifiers = list(dict.fromkeys(identifier_text(found) for found in IDENTIFIER.findall(question)))
    return Plan(query_terms(text), families, versions, chosen_sources(families, versions), identifiers)


def identifier_text(text: str) -> str:
    return IDENTIFIER_SEPARATORS.sub(".", text.lower())


def names_identifier(text: str, identifier: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(identifier)}(?![\w])", text) is not None


def identifier_hits(row: sqlite3.Row, identifiers: list[str]) -> int:
    url_tail = row["url"].split("://", 1)[-1].split("/", 1)[-1]
    text = identifier_text(f"{row['heading']} {url_tail}")
    return sum(1 for identifier in identifiers if names_identifier(text, identifier))


def last_segment(name: str) -> str:
    return re.split(r"::|\.", name)[-1]


def page_names(row: sqlite3.Row) -> set[str]:
    parts = row["heading"].lower().split(" > ")
    words = PAGE_NAME.findall(parts[0].split(" (")[0])
    names = {last_segment(words[0])} if words else set()
    if len(words) > 1 and words[0] in FAMILY_WORDS:
        names.add(last_segment(words[1]))
    if len(parts) > 1:
        names.update(last_segment(item.split()[0]) for item in parts[1].split(",") if item.split())
    return names


def names_page(row: sqlite3.Row, terms: list[str]) -> bool:
    return bool(page_names(row) & set(terms))


def fts_term(term: str) -> str:
    return '"' + " ".join(part for part in re.split(r"[-_]", term) if part) + '"' if "-" in term else f'"{term}"'


def fts_query(terms: list[str]) -> str:
    return " OR ".join(fts_term(term) for term in terms)


def term_root(term: str) -> str:
    return term.replace("-", " ")[:max(4, len(term) - 2)] if len(term) > 4 else term.replace("-", " ")


SHORT_TERM = 4


@lru_cache(maxsize=4096)
def term_pattern(term: str) -> re.Pattern:
    root = re.escape(term_root(term).replace("_", " "))
    return re.compile(rf"\b{root}(?:s|es|ed|ing)?\b" if len(term) <= SHORT_TERM else rf"\b{root}")


def flattened(text: str) -> str:
    return text.lower().replace("_", " ").replace("-", " ")


def heading_hits(heading: str, terms: list[str]) -> int:
    lowered = flattened(heading)
    return sum(1 for term in terms if term_pattern(term).search(lowered))


def coverage(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    lowered = flattened(text)
    return sum(1 for term in terms if term_pattern(term).search(lowered)) / len(terms)


def snippet_of(body: str) -> str:
    plain = " ".join(FENCE_LINE.sub(" ", body).split())
    return plain[:SNIPPET_CHARS]


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def ranked_rows(connection: sqlite3.Connection, terms: list[str], sources: list[str], limit: int) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in sources)
    source_filter = f"AND s.source IN ({placeholders})" if sources else ""
    sql = (f"SELECT s.id, s.source, src.family, src.version, s.page, s.heading, s.url, s.body, s.has_code, s.about, "
           f"bm25(sections_fts, {PAGE_WEIGHT}, {HEADING_WEIGHT}, {BODY_WEIGHT}) AS rank "
           f"FROM sections_fts JOIN sections s ON s.id = sections_fts.rowid JOIN sources src ON src.key = s.source "
           f"WHERE sections_fts MATCH ? {source_filter} ORDER BY rank LIMIT ?")
    return connection.execute(sql, [fts_query(terms), *sources, limit]).fetchall()


def source_groups(plan: Plan, sources: tuple[Source, ...] | None = None) -> list[list[str]]:
    sources = SOURCES if sources is None else sources
    if not plan.families:
        return [plan.sources]
    family_of = {source.key: source.family for source in sources}
    named = [key for key in plan.sources if family_of.get(key) in plan.families]
    related = [key for key in plan.sources if key not in named]
    return [group for group in (named, related) if group]


def candidates(connection: sqlite3.Connection, plan: Plan, limit: int) -> list[sqlite3.Row]:
    if not plan.terms:
        return []
    rows = []
    for group in source_groups(plan):
        rows.extend(ranked_rows(connection, plan.terms, group, limit))
    return rows


def is_about_named_version(row: sqlite3.Row, versions: dict[str, str]) -> bool:
    named = versions.get(row["family"])
    return bool(named and row["about"] and version_matches(named, row["about"]))


def is_related_only(row: sqlite3.Row, families: set[str]) -> bool:
    return bool(families) and row["family"] not in families


def scored(row: sqlite3.Row, plan: Plan, best_rank: float) -> float:
    relative = row["rank"] / best_rank if best_rank else 0.0
    headline = f"{row['page']} {row['heading']} {row['url'].rsplit('/', 1)[-1]}"
    return (relative + HEADING_HIT_WEIGHT * heading_hits(headline, plan.terms) / max(len(plan.terms), 1)
            + COVERAGE_WEIGHT * coverage(f"{headline} {row['body']}", plan.terms) + CODE_BONUS * row["has_code"]
            + VERSION_BONUS * is_about_named_version(row, plan.versions)
            + IDENTIFIER_BONUS * min(identifier_hits(row, plan.identifiers), 1)
            + PAGE_NAME_BONUS * names_page(row, plan.terms)
            - RELATED_PENALTY * is_related_only(row, plan.families))


def search(database: Path, question: str, limit: int = 5) -> list[Hit]:
    plan = plan_for(question)
    with closing(connect(database)) as connection:
        rows = candidates(connection, plan, CANDIDATES)
    if not rows:
        return []
    best_rank = min(row["rank"] for row in rows)
    scores = {row["id"]: scored(row, plan, best_rank) for row in rows}
    ranked = sorted(rows, key=lambda row: -scores[row["id"]])
    return [Hit(row["id"], row["source"], row["family"], row["version"], row["page"], row["heading"], row["url"],
                round(scores[row["id"]], 3), snippet_of(row["body"])) for row in ranked[:limit]]


def read(database: Path, section_id: int) -> dict | None:
    with closing(connect(database)) as connection:
        row = connection.execute(
            "SELECT s.id, s.source, src.name, src.family, src.version, src.licence, s.page, s.heading, s.url, s.body "
            "FROM sections s JOIN sources src ON src.key = s.source WHERE s.id = ?", (section_id,)).fetchone()
    return dict(row) if row else None


def library_summary(database: Path) -> list[dict]:
    with closing(connect(database)) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT key, name, family, version, release, licence, fetched, sections FROM sources ORDER BY key")]
