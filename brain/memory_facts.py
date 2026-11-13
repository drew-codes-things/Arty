import json
import logging
import re
import threading
import urllib.error

import connections
import memory_recall
import memory_tidy
import models
import owner

from environment import HUB_KEY
from settings import HUB_URL, OLLAMA_URL
from connections import USER_NAME

log = logging.getLogger("brain")


MEMORY_CHARS = 4000
DRAFT_FACTS = 6
SECRET_PATTERN = re.compile(r"password|passcode|passphrase|passkey|\bpin\b|api[ _-]?key|token|card number|cvv|sort code", re.IGNORECASE)


EXTRACT_PROMPT = owner.fill("""You extract lasting facts that OWNER states about their own life, so their assistant can remember them.

Example
OWNER said: "My sister Ella starts uni in Cardiff next month. Anyway, what's 2+2?"
Answer: {"facts": [{"about": "Ella", "fact": "OWNER's sister, starting university in Cardiff"}]}

Example
OWNER said: "Can you check the weather?"
Answer: {"facts": []}

Example
OWNER said: "Remind me to check the oven in 5 minutes, and say hi in three words"
Answer: {"facts": []}

Example
OWNER said: "I've switched to Linux on my laptop for good, I'm done with Windows"
Answer: {"facts": [{"about": "OWNER", "fact": "Uses Linux on their laptop and has stopped using Windows"}]}

Only include facts that will still be true and useful in a month: who OWNER is, their people, belongings, lasting
projects and preferences. Never include: requests or tasks for the assistant, reminders, what they're doing right now or
in the next few hours, how they are feeling today, test messages, files or pictures they sent, or passwords and secrets.
Most messages contain no such fact: then answer {"facts": []}. Answer with JSON only.

OWNER said: "%s"
Answer:""", USER_NAME)


memory_lock = threading.Lock()


# Things the small model still tries to save that aren't lasting facts about the user.
NOT_A_FACT = re.compile(r"\b(in \d+ (seconds?|minutes?|hours?)|remind|reminder|to-?do|today|tonight|tomorrow|right now|"
                        r"at the moment|says? (hello|hi|bye|goodbye)|can say|in (three|five|\d+) words|attached|sent an?|sending|"
                        r"\.(png|jpe?g|wav|mp3|mp4)\b|test|is asking|asked (arty|the assistant|you)|ai assistant|talking about)\b", re.IGNORECASE)


def save_facts(facts):
    graph = connections.http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20) or {}
    entities = {e.get("name", "").lower(): e for e in graph.get("entities") or []}
    for item in facts:
        about = str(item.get("about") or USER_NAME).strip()[:60] or USER_NAME
        fact = str(item.get("fact") or "").strip()[:300]
        if not fact or SECRET_PATTERN.search(fact) or NOT_A_FACT.search(fact):
            continue
        existing = entities.get(about.lower())
        if existing and any(fact.lower() == o.lower() for o in existing.get("observations") or []):
            continue
        if existing:
            body, route = {"observations": [{"entityName": existing["name"], "contents": [fact]}]}, "add_observations"
        else:
            kind = "person" if about.lower() == USER_NAME.lower() else "topic"
            body, route = {"entities": [{"name": about, "entityType": kind, "observations": [fact]}]}, "create_entities"
            entities[about.lower()] = {"name": about, "observations": [fact]}
        connections.http_json(f"{HUB_URL}/memory/{route}", body=body, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
        log.info("remembered: %s: %s", about, fact)


REMEMBER_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you )*remember (?:that |this:?\s*)(?P<fact>.+?)[.!]?\s*$",
                              re.IGNORECASE | re.DOTALL)
FORGET_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you )*forget (?!it\b|i said)(?:about |that )?(?P<what>.+?)[.!?]?\s*$",
                            re.IGNORECASE | re.DOTALL)
FORGET_FILLER = {"the", "that", "fact", "about", "what", "said", "told", "you", "your", "know", "thing", "facts", "and", "for",
                 "this", "with", "from", "have", "saved", "remembered", "anything", "everything", "please"}
MAX_FORGET_MATCHES = 3


def as_fact(text: str) -> str:
    fact = re.sub(r"\b(I am|I'm)\b", f"{USER_NAME} is", text.strip(), flags=re.I)
    fact = re.sub(r"\bmy\b", f"{USER_NAME}'s", fact, flags=re.I)
    fact = re.sub(r"\bI\b", USER_NAME, fact)
    return fact[0].upper() + fact[1:] if fact else fact


def remember_reply(said: str) -> str | None:
    match = REMEMBER_REQUEST.match(said)
    if not match or SECRET_PATTERN.search(said) or NOT_A_FACT.search(match.group("fact")):
        return None
    fact = as_fact(match.group("fact"))
    save_facts([{"about": USER_NAME, "fact": fact}])
    return f"Got it, I'll remember that: {match.group('fact').strip()}."


def forget_reply(said: str) -> str | None:
    match = FORGET_REQUEST.match(said)
    if not match:
        return None
    words = {word for word in re.findall(r"[a-z0-9']{3,}", match.group("what").lower()) if word not in FORGET_FILLER}
    if not words:
        return None
    headers = {"Authorization": f"Bearer {HUB_KEY}"}
    graph = connections.http_json(f"{HUB_URL}/memory/read_graph", body={}, headers=headers, timeout=20) or {}
    matches = [(entity["name"], observation) for entity in graph.get("entities") or []
               for observation in entity.get("observations") or [] if all(word in observation.lower() for word in words)]
    if not matches:
        return "I don't have anything saved about that, so there's nothing to forget."
    if len(matches) > MAX_FORGET_MATCHES:
        return f"That matches {len(matches)} things I've saved. Tell me a bit more about which one to forget."
    deletions = {}
    for name, observation in matches:
        deletions.setdefault(name, []).append(observation)
    connections.http_json(f"{HUB_URL}/memory/delete_observations", headers=headers, timeout=20,
              body={"deletions": [{"entityName": name, "observations": items} for name, items in deletions.items()]})
    log.info("forgot %d fact(s) on request", len(matches))
    forgotten = "\n".join(f"- {observation}" for _, observation in matches)
    return f"Done, I've forgotten:\n{forgotten}"

def memory_hub(route: str, body: dict):
    return connections.http_json(f"{HUB_URL}/memory/{route}", body=body, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)


def restore_memory_reply(said: str) -> str | None:
    if not memory_tidy.is_restore_request(said):
        return None
    return memory_tidy.restore_last_run(memory_hub)


def prefix_for(model: str, prefix) -> dict:
    if not prefix or prefix.get("model", model) != model:
        return {}
    return prefix


def extract_memories(config, user_text, answer, save=None, prefix=None):
    shared = prefix_for(config["model"], prefix)
    body = {"model": config["model"], "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_predict": 512},
            "messages": [*(shared.get("messages") or []), {"role": "user", "content": EXTRACT_PROMPT % user_text[:2000]}]}
    if shared.get("tools"):
        body["tools"] = shared["tools"]
    with memory_lock:
        try:
            reply = connections.http_json(f"{OLLAMA_URL}/api/chat", body=models.for_runner(config, body))
            facts = json.loads(reply["message"]["content"]).get("facts") or []
            if isinstance(facts, list) and facts:
                (save or save_facts)([f for f in facts if isinstance(f, dict)])
        except Exception as error:
            log.warning("memory extraction failed: %s", error)


def memory_graph():
    try:
        return connections.http_json(f"{HUB_URL}/memory/read_graph", body={}, headers={"Authorization": f"Bearer {HUB_KEY}"}, timeout=20)
    except (urllib.error.URLError, TimeoutError):
        return None


def recalled_memories(request: str = ""):
    graph = memory_graph()
    return "" if graph is None else memory_recall.memory_lines(graph, request, MEMORY_CHARS)


def draft_facts(request: str):
    graph = memory_graph()
    return "" if graph is None else memory_recall.matching_lines(graph, request, DRAFT_FACTS)
