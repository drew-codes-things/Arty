import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

ARCHIVE_FILE = Path(os.environ.get("MEMORY_ARCHIVE_FILE", "/srv/ai/memory/archived-facts.jsonl"))
STATE_FILE = Path(os.environ.get("MEMORY_TIDY_STATE", "/srv/ai/brain/state/memory-tidy.json"))
DEFAULT_HOUR = 4
MIN_HOURS_BETWEEN_RUNS = 20
HOUR_SECONDS = 3600
MAX_FACTS_PER_CHECK = 40
RESTORED = "restored"

REPLACED_PROMPT = """These are saved facts about {entity}, numbered, oldest first.

{facts}

Find facts that are no longer true because a LATER fact in the list replaces them: a new address, job, phone, partner,
car, school, or a changed preference or plan. Only a direct replacement counts. A later fact that only adds detail
does not replace an earlier one, and facts about different things never replace each other. When unsure, leave it out.

Reply with JSON only: {{"replaced": [{{"old": <number of the replaced fact>, "new": <number of the later fact>}}]}}
or {{"replaced": []}} when nothing is replaced."""

RESTORE_REQUEST = re.compile(
    r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you )*"
    r"(?:undo (?:the |your |last night's |tonight's )?memory (?:tidy|clean)[- ]?up"
    r"|(?:bring back|restore) (?:the |your )?(?:archived|tidied[- ]up) (?:facts|memories))\W*$",
    re.IGNORECASE)

log = logging.getLogger("brain.memory_tidy")
_running = threading.Lock()


def is_restore_request(said: str) -> bool:
    return bool(RESTORE_REQUEST.match(said or ""))


def normalised(fact: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", fact.lower()).split())


def entities_of(graph) -> list[dict]:
    entities = graph.get("entities") if isinstance(graph, dict) else None
    return [item for item in entities or [] if isinstance(item, dict) and item.get("name")]


def observations_of(entity: dict) -> list[str]:
    return [str(item) for item in entity.get("observations") or []]


def duplicates(facts: list[str]) -> list[tuple[str, str]]:
    newest_wording = {}
    for fact in facts:
        newest_wording[normalised(fact)] = fact
    return [(fact, f"same as: {newest_wording[normalised(fact)]}") for fact in facts
            if fact != newest_wording[normalised(fact)]]


def is_fact_number(value, count: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count


def replaced_pairs(reply: str, count: int) -> list[tuple[int, int]]:
    try:
        parsed = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        log.warning("memory tidy: the model's reply was not JSON")
        return []
    items = parsed.get("replaced") if isinstance(parsed, dict) else None
    pairs, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        old, new = item.get("old"), item.get("new")
        if is_fact_number(old, count) and is_fact_number(new, count) and old < new and old not in seen:
            pairs.append((old, new))
            seen.add(old)
    return pairs


def replaced(entity: str, facts: list[str], complete) -> list[tuple[str, str]]:
    if len(facts) < 2:
        return []
    checked = facts[-MAX_FACTS_PER_CHECK:]
    numbered = "\n".join(f"{index}. {fact}" for index, fact in enumerate(checked))
    reply = complete(REPLACED_PROMPT.format(entity=entity, facts=numbered))
    return [(checked[old], f"replaced by: {checked[new]}") for old, new in replaced_pairs(reply, len(checked))]


def append_records(archive_file: Path, records: list[dict]) -> None:
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    with open(archive_file, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(state_file)


def load_state(state_file: Path) -> dict:
    try:
        state = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def tidy_entity(entity: dict, complete, use_model: bool) -> list[tuple[str, str]]:
    facts = observations_of(entity)
    found = duplicates(facts)
    archived = {fact for fact, _ in found}
    remaining = [fact for fact in facts if fact not in archived]
    if use_model:
        found += replaced(entity["name"], remaining, complete)
    return found


def tidy(call, complete, now: float | None = None, use_model: bool = True,
         archive_file: Path | None = None, state_file: Path | None = None) -> dict:
    now = time.time() if now is None else now
    archive_file, state_file = archive_file or ARCHIVE_FILE, state_file or STATE_FILE
    run = datetime.fromtimestamp(now).isoformat(timespec="seconds")
    save_state(state_file, {**load_state(state_file), "last_run": now})
    records = []
    for entity in entities_of(call("read_graph", {})):
        for fact, reason in tidy_entity(entity, complete, use_model):
            records.append({"run": run, "time": run, "entity": entity["name"], "entityType": entity.get("entityType") or "",
                            "fact": fact, "reason": reason})
    if records:
        append_records(archive_file, records)
        deletions = {}
        for record in records:
            deletions.setdefault(record["entity"], []).append(record["fact"])
        call("delete_observations", {"deletions": [{"entityName": name, "observations": facts} for name, facts in deletions.items()]})
    log.info("memory tidy %s archived %d fact(s)", run, len(records))
    return {"run": run, "archived": len(records)}


def archive_lines(archive_file: Path) -> list[dict]:
    if not archive_file.exists():
        return []
    records = []
    for line in archive_file.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("run"), str):
            records.append(record)
    return records


def last_unrestored_run(records: list[dict]) -> str | None:
    restored = {record["run"] for record in records if record.get("type") == RESTORED}
    runs = [record["run"] for record in records if record.get("type") != RESTORED and record["run"] not in restored]
    return runs[-1] if runs else None


def restore_last_run(call, now: float | None = None, archive_file: Path | None = None, state_file: Path | None = None) -> str:
    now = time.time() if now is None else now
    archive_file = archive_file or ARCHIVE_FILE
    records = archive_lines(archive_file)
    run = last_unrestored_run(records)
    if run is None:
        return "There's nothing archived to bring back: no memory tidy-up has put any facts away."
    facts = [record for record in records if record["run"] == run and record.get("type") != RESTORED]
    existing = {entity["name"] for entity in entities_of(call("read_graph", {}))}
    grouped: dict[str, dict] = {}
    for record in facts:
        grouped.setdefault(record["entity"], {"kind": record.get("entityType") or "topic", "facts": []})["facts"].append(record["fact"])
    additions = [{"entityName": name, "contents": group["facts"]} for name, group in grouped.items() if name in existing]
    creations = [{"name": name, "entityType": group["kind"], "observations": group["facts"]}
                 for name, group in grouped.items() if name not in existing]
    if creations:
        call("create_entities", {"entities": creations})
    if additions:
        call("add_observations", {"observations": additions})
    append_records(archive_file, [{"run": run, "type": RESTORED, "time": datetime.fromtimestamp(now).isoformat(timespec="seconds")}])
    log.info("memory tidy %s restored %d fact(s)", run, len(facts))
    return f"Done, I've put back the {len(facts)} fact(s) the memory tidy-up of {run[:16].replace('T', ' ')} archived."


def tidy_hour(settings: dict) -> int:
    hour = settings.get("hour", DEFAULT_HOUR)
    return hour if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23 else DEFAULT_HOUR


def due(settings: dict, now: float | None = None, state_file: Path | None = None) -> bool:
    now = time.time() if now is None else now
    if settings.get("enabled", True) is False or datetime.fromtimestamp(now).hour != tidy_hour(settings):
        return False
    last_run = load_state(state_file or STATE_FILE).get("last_run", 0)
    return not isinstance(last_run, (int, float)) or now - last_run >= MIN_HOURS_BETWEEN_RUNS * HOUR_SECONDS


def run_in_background(call, complete) -> None:
    if not _running.acquire(blocking=False):
        return
    try:
        tidy(call, complete)
    except Exception:
        log.exception("memory tidy failed")
    finally:
        _running.release()
