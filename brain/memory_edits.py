import logging
import threading
import unicodedata

from api_support import ApiError
from memory_facts import SECRET_PATTERN

log = logging.getLogger("brain")

MAX_NAME_CHARS = 60
MAX_FACT_CHARS = 300
MAX_TYPE_CHARS = 40
DEFAULT_TYPE = "topic"
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
NO_SECRETS = "Arty doesn't keep passwords, keys, codes or card details in its memory."
edits_lock = threading.Lock()


def folded(text: str) -> str:
    return " ".join(text.split())


def has_hidden_characters(text: str) -> bool:
    return any(unicodedata.category(character) in HIDDEN_CATEGORIES for character in text)


def looked_up(value, field: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, f"{field} must be text")
    text = folded(value)
    if required and not text:
        raise ApiError(400, f"{field} is required")
    return text


def written(value, field: str, limit: int, required: bool = True) -> str:
    text = looked_up(value, field, required)
    if len(text) > limit:
        raise ApiError(400, f"{field} can be at most {limit} characters")
    if has_hidden_characters(text):
        raise ApiError(400, f"{field} has characters that can't be saved")
    if SECRET_PATTERN.search(text):
        raise ApiError(400, NO_SECRETS)
    return text


def entities_of(call) -> list[dict]:
    return list((call("read_graph", {}) or {}).get("entities") or [])


def entity_named(call, name: str) -> dict | None:
    wanted = name.casefold()
    return next((entity for entity in entities_of(call) if folded(str(entity.get("name", ""))).casefold() == wanted), None)


def existing_entity(call, name: str) -> dict:
    found = entity_named(call, name)
    if found is None:
        raise ApiError(404, f"nothing is saved about {name[:MAX_NAME_CHARS]}")
    return found


def existing_fact(entity: dict, fact: str) -> str:
    for saved in entity.get("observations") or []:
        if isinstance(saved, str) and folded(saved) == fact:
            return saved
    raise ApiError(404, f"that fact isn't saved under {entity['name']}")


def add_fact(call, name: str, fact: str) -> None:
    call("add_observations", {"observations": [{"entityName": name, "contents": [fact]}]})


def add(call, request: dict) -> dict:
    name = written(request.get("entity"), "entity", MAX_NAME_CHARS)
    fact = written(request.get("fact"), "fact", MAX_FACT_CHARS)
    kind = written(request.get("type"), "type", MAX_TYPE_CHARS, required=False) or DEFAULT_TYPE
    with edits_lock:
        entity = entity_named(call, name)
        if entity is None:
            call("create_entities", {"entities": [{"name": name, "entityType": kind, "observations": []}]})
            add_fact(call, name, fact)
            log.info("memory: added %s with one fact from an app", name)
            return {"entity": name, "fact": fact}
        if any(isinstance(saved, str) and folded(saved).casefold() == fact.casefold() for saved in entity.get("observations") or []):
            raise ApiError(409, f"that fact is already saved under {entity['name']}")
        add_fact(call, entity["name"], fact)
    log.info("memory: added a fact to %s from an app", entity["name"])
    return {"entity": entity["name"], "fact": fact}


def edit(call, request: dict) -> dict:
    name = looked_up(request.get("entity"), "entity")
    old = looked_up(request.get("old"), "old")
    new = written(request.get("new"), "new", MAX_FACT_CHARS)
    with edits_lock:
        entity = existing_entity(call, name)
        saved = existing_fact(entity, old)
        if new == saved:
            return {"entity": entity["name"], "fact": new}
        add_fact(call, entity["name"], new)
        call("delete_observations", {"deletions": [{"entityName": entity["name"], "observations": [saved]}]})
    log.info("memory: changed a fact on %s from an app", entity["name"])
    return {"entity": entity["name"], "fact": new}


def delete(call, request: dict) -> dict:
    name = looked_up(request.get("entity"), "entity")
    fact = looked_up(request.get("fact"), "fact", required=False)
    with edits_lock:
        entity = existing_entity(call, name)
        if not fact:
            call("delete_entities", {"entityNames": [entity["name"]]})
            log.info("memory: removed %s and its facts from an app", entity["name"])
            return {"entity": entity["name"], "removed": "entity"}
        saved = existing_fact(entity, fact)
        call("delete_observations", {"deletions": [{"entityName": entity["name"], "observations": [saved]}]})
    log.info("memory: removed a fact from %s from an app", entity["name"])
    return {"entity": entity["name"], "removed": "fact"}


EDITS = {"add": add, "edit": edit, "delete": delete}
