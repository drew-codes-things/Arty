import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

log = logging.getLogger("brain.local_docs")

LIBRARY_URL = os.environ.get("DOCS_LIBRARY_URL", "")
ASK_SECONDS = 2.0
SECTIONS = 3
MIN_SCORE = 2.0
READ_CHARS = 8000


class Found(NamedTuple):
    url: str
    heading: str
    text: str


def ask(path: str, parameters: dict) -> dict:
    address = f"{LIBRARY_URL.rstrip('/')}{path}?{urllib.parse.urlencode(parameters)}"
    with urllib.request.urlopen(address, timeout=ASK_SECONDS) as response:
        return json.loads(response.read())


def best_sections(question: str) -> list[Found]:
    if not LIBRARY_URL:
        return []
    try:
        hits = ask("/search", {"q": question, "limit": SECTIONS}).get("results", [])
        good = [hit for hit in hits if hit.get("score", 0) >= MIN_SCORE]
        return [Found(hit["url"], hit["heading"], ask("/read", {"id": hit["id"], "chars": READ_CHARS})["text"])
                for hit in good]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        log.warning("the local docs library didn't answer; using the web instead: %r", error)
        return []
