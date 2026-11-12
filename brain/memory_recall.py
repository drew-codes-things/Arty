import re
import sqlite3
from dataclasses import dataclass

MIN_TERM_CHARS = 3
FACT_SEPARATOR = "; "
PLAIN_LINE_START = "- "
STOP_WORDS = frozenset({
    "about", "all", "and", "any", "anything", "are", "arty", "but", "can", "could", "did", "does", "for", "from", "had",
    "has", "have", "how", "into", "just", "know", "like", "need", "not", "please", "should", "some", "tell", "than",
    "that", "the", "their", "them", "then", "there", "they", "this", "want", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
})


@dataclass(frozen=True)
class Fact:
    entity: str
    kind: str
    text: str
    position: int


def graph_facts(graph) -> list[Fact]:
    facts = []
    entities = graph.get("entities") if isinstance(graph, dict) else None
    for item in entities or []:
        if not isinstance(item, dict):
            continue
        name, kind = str(item.get("name") or ""), str(item.get("entityType") or "")
        for observation in item.get("observations") or []:
            text = " ".join(str(observation).split())
            if name and text:
                facts.append(Fact(name, kind, text, len(facts)))
    return facts


def query_terms(request: str) -> list[str]:
    words = re.findall(r"\w+", (request or "").lower())
    return sorted({word for word in words if len(word) >= MIN_TERM_CHARS and word not in STOP_WORDS})


def relevant_positions(facts: list[Fact], request: str) -> list[int]:
    terms = query_terms(request)
    if not facts or not terms:
        return []
    search = " OR ".join(f'"{term}"' for term in terms)
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE VIRTUAL TABLE facts USING fts5(entity, text, tokenize='porter unicode61')")
        connection.executemany("INSERT INTO facts (rowid, entity, text) VALUES (?, ?, ?)",
                               [(fact.position, fact.entity, fact.text) for fact in facts])
        rows = connection.execute("SELECT rowid FROM facts WHERE facts MATCH ? ORDER BY bm25(facts), rowid DESC", (search,)).fetchall()
    return [row[0] for row in rows]


def priority_order(facts: list[Fact], request: str) -> list[Fact]:
    relevant = relevant_positions(facts, request)
    newest_first = [fact.position for fact in reversed(facts)]
    ordered = list(dict.fromkeys(relevant + newest_first))
    return [facts[position] for position in ordered]


def entity_header(fact: Fact) -> str:
    return f"- {fact.entity} ({fact.kind}): "


def chosen_facts(facts: list[Fact], request: str, budget: int) -> list[Fact]:
    chosen, entities_shown, used = [], set(), 0
    for fact in priority_order(facts, request):
        is_first_of_entity = fact.entity not in entities_shown
        line_break = 1 if is_first_of_entity and entities_shown else 0
        cost = len(fact.text) + (line_break + len(entity_header(fact)) if is_first_of_entity else len(FACT_SEPARATOR))
        if used + cost > budget:
            continue
        chosen.append(fact)
        entities_shown.add(fact.entity)
        used += cost
    return chosen


def render(facts: list[Fact]) -> str:
    grouped: dict[str, list[Fact]] = {}
    for fact in sorted(facts, key=lambda fact: fact.position):
        grouped.setdefault(fact.entity, []).append(fact)
    lines = [entity_header(group[0]) + FACT_SEPARATOR.join(fact.text for fact in group) for group in grouped.values()]
    return "\n".join(lines)


def fact_lines(facts: list[str], request: str, budget: int) -> str:
    listed = [Fact("", "", text, position) for position, text in enumerate(str(fact) for fact in facts if str(fact).strip())]
    chosen, used = [], 0
    for fact in priority_order(listed, request):
        cost = len(PLAIN_LINE_START) + len(fact.text) + (1 if chosen else 0)
        if used + cost > budget:
            continue
        chosen.append(fact)
        used += cost
    return "\n".join(PLAIN_LINE_START + fact.text for fact in sorted(chosen, key=lambda fact: fact.position))


def memory_lines(graph, request: str, budget: int) -> str:
    facts = graph_facts(graph)
    return render(chosen_facts(facts, request, budget))
