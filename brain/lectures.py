"""Lecture capture: a recorded lesson becomes notes and revision cards.

The phone app records a lesson (or takes an audio/video file) and posts it to /v1/lectures. One worker
thread then takes the lectures in turn: it transcribes the recording (faster-whisper on the CPU, roughly a
quarter to half of the recording's length), writes notes a section at a time (an hour's transcript is longer
than the model's context), combines them, turns the key points into revision cards for the quiz, and saves the
notes where note_search finds them. Transcript and notes live in the lectures table; uploads expire after a day.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime

import media
import personal
import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, file_id TEXT, duration REAL,
    transcript TEXT, notes TEXT, cards INTEGER DEFAULT 0, error TEXT, created REAL, updated REAL
);
"""
FINISHED = ("done", "failed")
CHUNK_WORDS = 1800        # about 2,500 tokens: room for the instructions and the notes in an 8k context
MAX_CARDS = 15
TITLE_CHARS = 80
TRANSCRIBE_TIMEOUT_SECONDS = 5 * 3600
LECTURE_NOTE_CHARS = 200_000
IDLE_WAIT_SECONDS = 60

SECTION_PROMPT = """You are writing revision notes from part {part} of {parts} of a recorded lesson called "{title}".
The transcript comes from speech recognition, so fix obvious mis-heard words from context.
Write clear notes in Markdown: short headings and bullet points with the key ideas, definitions, examples,
numbers and anything the teacher stressed or said would be in an exam. Skip chit-chat and admin.
Write only the notes.

Transcript:
{text}"""

COMBINE_PROMPT = """Below are notes on each part of a recorded lesson called "{title}", in order. Combine them into one
set of revision notes in Markdown: start with a two-sentence summary, then headings by topic with bullet points,
then a "Key terms" list (term: meaning). Merge repeats, keep every fact, number and example. Write only the notes.

{notes}"""

CARDS_PROMPT = """Make revision flashcards from these notes on "{title}". Each card tests one fact, definition or idea
from the notes, with a short question and a short, exact answer. At most {limit} cards. Reply with JSON only:
{{"cards": [{{"question": "...", "answer": "..."}}]}}

Notes:
{notes}"""

log = logging.getLogger("brain.lectures")
_wake = threading.Event()


class LectureError(Exception):
    pass


def init():
    """Creates the table and puts lectures a brain restart interrupted back in the queue."""
    with store.write_lock, store.connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute("UPDATE lectures SET status = 'queued' WHERE status NOT IN ('done', 'failed')")


def _update(lecture_id: int, **fields):
    fields["updated"] = time.time()
    columns = ", ".join(f"{name} = ?" for name in fields)
    with store.write_lock, store.connect() as connection:
        connection.execute(f"UPDATE lectures SET {columns} WHERE id = ?", (*fields.values(), lecture_id))


def _row(row) -> dict:
    return dict(row) if row else None


def add(file_id: str, title: str) -> dict:
    """Queues a recording. Raises LectureError for a file that isn't there or has no sound."""
    meta = media.get(str(file_id or ""))
    if meta is None:
        raise LectureError("That recording isn't on the server (uploads are kept for a day). Send it again.")
    if meta["kind"] not in ("audio", "video"):
        raise LectureError("A lecture needs an audio or video recording.")
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:TITLE_CHARS] or f"Lecture {datetime.now():%d %b %Y}"
    now = time.time()
    with store.write_lock, store.connect() as connection:
        lecture_id = connection.execute(
            "INSERT INTO lectures (title, status, file_id, created, updated) VALUES (?, 'queued', ?, ?, ?)",
            (title, meta["id"], now, now)).lastrowid
    _wake.set()
    return get(lecture_id, with_text=False)


def get(lecture_id: int, with_text: bool = True) -> dict | None:
    columns = "*" if with_text else "id, title, status, duration, cards, error, created, updated"
    with store.connect() as connection:
        return _row(connection.execute(f"SELECT {columns} FROM lectures WHERE id = ?", (int(lecture_id),)).fetchone())


def recent(limit: int = 30) -> list[dict]:
    with store.connect() as connection:
        rows = connection.execute("SELECT id, title, status, duration, cards, error, created, updated FROM lectures "
                                  "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(row) for row in rows]


def chunks(transcript: str, words: int | None = None) -> list[str]:
    """The transcript in pieces of about `words` (default CHUNK_WORDS) words, split between lines."""
    words = words or CHUNK_WORDS
    pieces, current, count = [], [], 0
    for line in transcript.splitlines():
        size = len(line.split())
        if current and count + size > words:
            pieces.append("\n".join(current))
            current, count = [], 0
        current.append(line)
        count += size
    if current:
        pieces.append("\n".join(current))
    return pieces


def parse_cards(reply: str) -> list[tuple[str, str]]:
    """(question, answer) pairs from the model's JSON, skipping anything malformed."""
    try:
        cards = json.loads(reply).get("cards") or []
    except (ValueError, AttributeError):
        return []
    pairs = []
    for card in cards[:MAX_CARDS]:
        if isinstance(card, dict) and str(card.get("question", "")).strip() and str(card.get("answer", "")).strip():
            pairs.append((str(card["question"]).strip(), str(card["answer"]).strip()))
    return pairs


def process(lecture: dict, complete) -> dict:
    """Transcript -> notes -> cards for one lecture. complete(prompt, json_mode) asks the model.
    Returns the fields to store; raises LectureError when it can't be done."""
    transcript = lecture.get("transcript") or _transcribe(lecture)
    notes = lecture.get("notes") or _write_notes(lecture, transcript, complete)
    topic = lecture["title"].lower()
    cards = parse_cards(complete(CARDS_PROMPT.format(title=lecture["title"], limit=MAX_CARDS, notes=notes), True))
    added = [personal.card_add(topic, question, answer) for question, answer in cards if not _has_card(topic, question)]
    note = f"Lecture notes: {lecture['title']} ({datetime.fromtimestamp(lecture['created']):%d %b %Y})\n\n{notes}"
    if not _has_note(note):
        personal.note_save(note, LECTURE_NOTE_CHARS)
    return {"status": "done", "notes": notes, "cards": len(added), "error": None}


def _transcribe(lecture: dict) -> str:
    meta = media.get(lecture["file_id"] or "")
    if meta is None:
        raise LectureError("The recording had expired before Arty got to it (uploads are kept for a day). Send it again.")
    duration = media.probe(meta["path"])["duration"]
    _update(lecture["id"], status="transcribing", duration=duration)
    transcript = media.transcribe(meta["path"], timeout=TRANSCRIBE_TIMEOUT_SECONDS, background=True)
    if not transcript:
        raise LectureError("No speech was found in the recording.")
    _update(lecture["id"], status="writing notes", transcript=transcript)
    return transcript


def _write_notes(lecture: dict, transcript: str, complete) -> str:
    parts = chunks(transcript)
    section_notes = [complete(SECTION_PROMPT.format(part=index, parts=len(parts), title=lecture["title"], text=part)).strip()
                     for index, part in enumerate(parts, 1)]
    notes = section_notes[0] if len(section_notes) == 1 else \
        complete(COMBINE_PROMPT.format(title=lecture["title"], notes="\n\n".join(section_notes))).strip()
    if not notes:
        raise LectureError("Arty couldn't write notes from the transcript.")
    _update(lecture["id"], status="making cards", notes=notes)
    return notes


def _has_card(topic: str, question: str) -> bool:
    with store.connect() as connection:
        return connection.execute("SELECT 1 FROM cards WHERE topic = ? AND question = ?", (topic[:80], question[:500])).fetchone() is not None


def _has_note(text: str) -> bool:
    with store.connect() as connection:
        return connection.execute("SELECT 1 FROM notes WHERE text = ?", (text[:LECTURE_NOTE_CHARS],)).fetchone() is not None


def keep_queued_recordings():
    with store.connect() as connection:
        waiting = [row["file_id"] for row in connection.execute("SELECT file_id FROM lectures WHERE status NOT IN ('done', 'failed')")]
    for file_id in waiting:
        if file_id:
            media.touch(file_id)


def _queued() -> dict | None:
    keep_queued_recordings()
    with store.connect() as connection:
        return _row(connection.execute("SELECT * FROM lectures WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone())


def next_queued(find=_queued) -> dict | None:
    try:
        return find()
    except Exception:
        log.exception("couldn't read the lecture queue")
        return None


def worker(complete, announce):
    """Runs forever: one queued lecture at a time. announce(title, message) tells the user."""
    while True:
        lecture = next_queued()
        if lecture is None:
            _wake.wait(timeout=IDLE_WAIT_SECONDS)
            _wake.clear()
            continue
        log.info("lecture %s: %s", lecture["id"], lecture["title"])
        try:
            outcome = process(lecture, complete)
        except (LectureError, media.MediaError) as error:
            outcome = {"status": "failed", "error": str(error)}
        except Exception as error:  # one bad lecture mustn't stop the worker
            log.exception("lecture %s failed", lecture["id"])
            outcome = {"status": "failed", "error": f"something went wrong ({error})"}
        try:
            _update(lecture["id"], **outcome)
        except Exception:
            log.exception("couldn't save lecture %s", lecture["id"])
        if outcome["status"] == "done":
            announce(f"📝 Lecture notes ready: {lecture['title']}",
                     f"Notes saved, and {outcome['cards']} revision cards added. Open Lectures in the app to read them, "
                     f"or say \"quiz me on {lecture['title'].lower()}\".")
        else:
            announce(f"📝 Couldn't do the lecture notes: {lecture['title']}", outcome["error"])
