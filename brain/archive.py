import json
import os
import re
from datetime import datetime
from pathlib import Path

from api_support import optional_int
from reply_rules import NOT_FOR_ME


RECENT_CHARS = 300
RECALL_EXCERPT_CHARS = 400


ARCHIVE_READ_BLOCK = 64 * 1024


def is_overheard(answer: str) -> bool:
    return answer.strip().strip(".").lower() == NOT_FOR_ME


def message_text(message):
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def lines_backwards(path: Path):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position, leftover = handle.tell(), b""
        while position > 0:
            step = min(ARCHIVE_READ_BLOCK, position)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + leftover).split(b"\n")
            leftover = lines.pop(0)
            for line in reversed(lines):
                yield line.decode("utf-8", errors="replace")
        yield leftover.decode("utf-8", errors="replace")


def is_archive_record(record) -> bool:
    return isinstance(record, dict) and all(isinstance(record.get(key), str) for key in ("time", "user", "assistant"))


def archive_records(config):
    folder = Path(config["conversations_dir"])
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.jsonl"), reverse=True):
        for line in lines_backwards(path):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_archive_record(record):
                yield record


def recent_exchanges(config):
    records = []
    for record in archive_records(config):
        records.append(record)
        if len(records) >= int(config.get("recent_exchanges", 6)):
            break
    lines = [f"[{r['time'][:16].replace('T', ' ')}] User: {r['user'][:RECENT_CHARS]}\n  Arty (you): {r['assistant'][:RECENT_CHARS]}" for r in reversed(records)]
    return "\n".join(lines)


def recall_records(config, query, limit=5):
    terms = [term for term in re.findall(r"\w+", (query or "").lower()) if len(term) > 2]
    found = []
    if not terms:
        return found
    for record in archive_records(config):
        text = f"{record.get('user', '')} {record.get('assistant', '')}".lower()
        if all(term in text for term in terms) or (len(terms) > 2 and sum(term in text for term in terms) >= len(terms) - 1):
            found.append(record)
            if len(found) >= max(1, optional_int(limit) or 5):
                break
    return found


def recall(config, query, limit=5):
    if not re.findall(r"\w{3,}", query or ""):
        return "Give me a few key words to search for."
    found = recall_records(config, query, limit)
    excerpts = [f"[{r['time'][:16].replace('T', ' ')}] User: {r['user'][:RECALL_EXCERPT_CHARS]}\n  Arty (you): {r['assistant'][:RECALL_EXCERPT_CHARS]}" for r in found]
    return "\n\n".join(excerpts) if excerpts else f"No past conversations mention: {query}"


def archive_turn(config, user_text, answer, tools_used):
    folder = Path(config["conversations_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {"time": now.isoformat(timespec="seconds"), "user": user_text, "assistant": answer, "tools": tools_used}
    with open(folder / f"{now:%Y-%m}.jsonl", "a") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
