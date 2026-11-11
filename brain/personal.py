"""The user's lists, notes, deadlines and revision cards, kept in Arty's database.

Each is a few small tools for the model (see TOOLS at the bottom) plus, for deadlines, reminders the
scheduler sends as a deadline approaches. Revision cards use a simple Leitner scheme: a card answered
right moves up a box and comes back later; one answered wrong goes back to box 1 and comes back soon.
"""
import re
import threading
import time
from datetime import datetime

import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS list_items (
  id INTEGER PRIMARY KEY, list TEXT NOT NULL, item TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deadlines (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, due REAL NOT NULL, notes TEXT NOT NULL DEFAULT '',
  done INTEGER NOT NULL DEFAULT 0, reminded TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY, topic TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
  box INTEGER NOT NULL DEFAULT 1, due REAL NOT NULL, right INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
"""

DAY = 86400
CARD_PARTS = 3
BOX_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 21}  # when a card in each box comes back
# Reminders before a deadline: (seconds before, how to say it).
DEADLINE_WARNINGS = [(7 * DAY, "within a week"), (2 * DAY, "within 2 days"), (DAY, "within a day"), (3 * 3600, "within 3 hours")]
NOTE_RESULTS = 8
NOTE_SEARCH_CHARS = 1500


def deck_cards(deck) -> list[tuple[str, str, str]]:
    return [tuple(str(part) for part in card) for card in deck or []
            if isinstance(card, (list, tuple)) and len(card) == CARD_PARTS and all(card)]


def init(starter_deck=None):
    with store.write_lock, store.connect() as connection:
        connection.executescript(SCHEMA)
        cards = deck_cards(starter_deck)
        if cards and connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0:
            now = time.time()
            connection.executemany("INSERT INTO cards (topic, question, answer, due, created) VALUES (?, ?, ?, ?, ?)",
                                   [(topic, question, answer, now, now) for topic, question, answer in cards])


def _list_name(name) -> str:
    name = re.sub(r"\s+list$", "", str(name or "").strip().lower()) or "to-do"
    return {"todo": "to-do", "to do": "to-do", "shop": "shopping", "groceries": "shopping"}.get(name, name)


# ---------- lists ----------

PAIRED_NAMES = ["salt and vinegar", "salt and pepper", "mac and cheese", "macaroni and cheese", "fish and chips",
                "bread and butter", "cheese and onion", "ham and cheese", "cheese and tomato", "sweet and sour", "gin and tonic",
                "strawberries and cream", "bangers and mash", "rhubarb and custard", "pen and paper", "nuts and bolts",
                "black and white", "spaghetti and meatballs", "peanut butter and jelly", "rock and roll"]
PAIRED_JOIN = "\x00"


def _split_items(text: str) -> list[str]:
    for paired in sorted(PAIRED_NAMES, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(paired)}\b", lambda found: found.group(0).replace(" and ", f" {PAIRED_JOIN} "), text,
                      flags=re.IGNORECASE)
    parts = re.split(r",|\band\b|\n", text)
    return [part.replace(f" {PAIRED_JOIN} ", " and ").strip() for part in parts]


def _number(value) -> int | None:
    try:
        return int(str(value).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def list_add(list_name, items) -> str:
    name = _list_name(list_name)
    if isinstance(items, str):
        items = _split_items(items)
    items = [str(item).strip() for item in items or [] if str(item).strip()]
    if not items:
        return "Say what to add."
    with store.write_lock, store.connect() as connection:
        connection.executemany("INSERT INTO list_items (list, item, created) VALUES (?, ?, ?)",
                               [(name, item[:200], time.time()) for item in items])
    return f"Added to the {name} list: {', '.join(items)}.\n" + list_show(name)


def list_show(list_name=None) -> str:
    with store.connect() as connection:
        if not list_name:
            rows = connection.execute("SELECT list, COUNT(*) AS open FROM list_items WHERE done = 0 GROUP BY list ORDER BY list").fetchall()
            return ("Lists: " + ", ".join(f"{r['list']} ({r['open']})" for r in rows) + ".") if rows else "There are no lists yet."
        name = _list_name(list_name)
        rows = connection.execute("SELECT id, item FROM list_items WHERE list = ? AND done = 0 ORDER BY id", (name,)).fetchall()
    if not rows:
        return f"The {name} list is empty."
    return f"{name.capitalize()} list:\n" + "\n".join(f"{index}. {row['item']}" for index, row in enumerate(rows, 1))


def list_done(list_name, items) -> str:
    """Ticks items off by their number in list_show, or by (part of) their text."""
    name = _list_name(list_name)
    with store.connect() as connection:
        rows = connection.execute("SELECT id, item FROM list_items WHERE list = ? AND done = 0 ORDER BY id", (name,)).fetchall()
    wanted = items if isinstance(items, list) else re.split(r",|\band\b", str(items or ""))
    ticked = []
    for want in (str(w).strip() for w in wanted if str(w).strip()):
        match = rows[int(want) - 1] if want.isdigit() and 0 < int(want) <= len(rows) else \
            next((r for r in rows if want.lower() in r["item"].lower() and r not in ticked), None)
        if match:
            ticked.append(match)
    if not ticked:
        return f"Nothing on the {name} list matches that.\n" + list_show(name)
    with store.write_lock, store.connect() as connection:
        connection.executemany("UPDATE list_items SET done = 1 WHERE id = ?", [(r["id"],) for r in ticked])
    return f"Ticked off: {', '.join(r['item'] for r in ticked)}.\n" + list_show(name)


def list_clear(list_name) -> str:
    name = _list_name(list_name)
    with store.write_lock, store.connect() as connection:
        removed = connection.execute("DELETE FROM list_items WHERE list = ?", (name,)).rowcount
    return f"Cleared the {name} list ({removed} items)."


# ---------- notes ----------

NOTE_CHARS = 4000


def note_save(text, limit: int = NOTE_CHARS) -> str:
    text = str(text or "").strip()
    if not text:
        return "Say what to note."
    with store.write_lock, store.connect() as connection:
        note_id = connection.execute("INSERT INTO notes (text, created) VALUES (?, ?)", (text[:limit], time.time())).lastrowid
    return f"Noted (#{note_id})."


def _shown_note(text: str) -> str:
    if len(text) <= NOTE_SEARCH_CHARS:
        return text
    return text[:NOTE_SEARCH_CHARS] + f"... (cut: the full note is {len(text)} characters)"


def note_search(query="") -> str:
    words = [w for w in re.findall(r"\w+", str(query or "").lower()) if len(w) > 2]
    with store.connect() as connection:
        rows = connection.execute("SELECT id, text, created FROM notes ORDER BY id DESC").fetchall()
    found = [r for r in rows if all(w in r["text"].lower() for w in words)] if words else rows
    if not found:
        return "No notes match that." if words else "There are no notes yet."
    return "Notes:\n" + "\n".join(f"#{r['id']} ({datetime.fromtimestamp(r['created']):%d %b}): {_shown_note(r['text'])}" for r in found[:NOTE_RESULTS])


def note_delete(note_id) -> str:
    with store.write_lock, store.connect() as connection:
        removed = connection.execute("DELETE FROM notes WHERE id = ?", (_number(note_id) or 0,)).rowcount
    return "Deleted that note." if removed else f"There's no note #{note_id}."


# ---------- deadlines ----------

def deadline_add(title, when, notes="") -> str:
    import calendar_feed  # it imports store too; imported here to keep this module light
    title = str(title or "").strip()
    if not title:
        return "Say what the deadline is for."
    try:
        due, all_day, _ = calendar_feed.parse_when(str(when or ""))
    except ValueError as error:
        return f"Couldn't add it: {error}"
    if all_day:
        due = due.replace(hour=23, minute=59)  # "due Friday" means by the end of Friday
    with store.write_lock, store.connect() as connection:
        deadline_id = connection.execute("INSERT INTO deadlines (title, due, notes, created) VALUES (?, ?, ?, ?)",
                                         (title[:200], due.timestamp(), str(notes or "")[:1000], time.time())).lastrowid
    return f"Deadline #{deadline_id} added: {title}, due {due:%a %d %b %H:%M}. I'll remind you a week, 2 days, 1 day and 3 hours before."


def deadlines_show() -> str:
    with store.connect() as connection:
        rows = connection.execute("SELECT * FROM deadlines WHERE done = 0 ORDER BY due").fetchall()
    if not rows:
        return "You don't have any deadlines coming up."
    now = time.time()
    lines = []
    for r in rows:
        days = (r["due"] - now) / DAY
        left = "OVERDUE" if days < 0 else "today" if days < 1 else f"{int(days)} days left"
        lines.append(f"#{r['id']} {r['title']}: due {datetime.fromtimestamp(r['due']):%a %d %b %H:%M} ({left})")
    return "Deadlines:\n" + "\n".join(lines)


def deadline_done(deadline_id) -> str:
    with store.write_lock, store.connect() as connection:
        row = connection.execute("SELECT title FROM deadlines WHERE id = ?", (_number(deadline_id) or 0,)).fetchone()
        if row is None:
            return f"There's no deadline #{deadline_id}."
        connection.execute("UPDATE deadlines SET done = 1 WHERE id = ?", (_number(deadline_id),))
    return f"Marked done: {row['title']}. Nice one."


def due_deadline_reminders() -> list[str]:
    """Reminder texts for deadlines crossing a warning point, each sent once. Called by the scheduler."""
    now = time.time()
    messages = []
    with store.write_lock, store.connect() as connection:
        for r in connection.execute("SELECT * FROM deadlines WHERE done = 0").fetchall():
            sent = set(filter(None, r["reminded"].split(",")))
            passed = [(seconds, words) for seconds, words in DEADLINE_WARNINGS if 0 < r["due"] - now <= seconds]
            if passed and str(passed[-1][0]) not in sent:
                # The closest warning point that applies ("in 3 hours"), with the earlier ones counted as done.
                messages.append(f"{r['title']} is due {passed[-1][1]} ({datetime.fromtimestamp(r['due']):%a %d %b %H:%M}).")
            sent.update(str(seconds) for seconds, _ in passed)
            reminded = ",".join(sorted(sent))
            if reminded != r["reminded"]:
                connection.execute("UPDATE deadlines SET reminded = ? WHERE id = ?", (reminded, r["id"]))
    return messages


# ---------- revision ----------

def card_add(topic, question, answer) -> str:
    topic, question, answer = (str(x or "").strip() for x in (topic, question, answer))
    if not (topic and question and answer):
        return "A card needs a topic, a question and an answer."
    with store.write_lock, store.connect() as connection:
        card_id = connection.execute("INSERT INTO cards (topic, question, answer, due, created) VALUES (?, ?, ?, ?, ?)",
                                     (topic.lower()[:80], question[:500], answer[:1000], time.time(), time.time())).lastrowid
    return f"Card #{card_id} added to {topic}."


def quiz_next(topic="") -> str:
    """The next card due for revision (in a topic, or any)."""
    topic = str(topic or "").strip().lower()
    with store.connect() as connection:
        query = "SELECT * FROM cards WHERE due <= ?" + (" AND topic LIKE ?" if topic else "") + " ORDER BY box, due LIMIT 1"
        row = connection.execute(query, (time.time(), f"%{topic}%") if topic else (time.time(),)).fetchone()
        waiting = connection.execute("SELECT COUNT(*) FROM cards" + (" WHERE topic LIKE ?" if topic else ""),
                                     (f"%{topic}%",) if topic else ()).fetchone()[0]
    if row is None:
        if waiting:
            return f"Nothing is due{' in ' + topic if topic else ''} right now ({waiting} cards, all coming back later). You could add new cards."
        return (f"There are no cards about {topic} yet. Call card_add five times now with good exam-style questions (don't show "
                "them to the user), then call quiz_next again.") if topic else "There are no revision cards yet."
    return (f"Card #{row['id']} ({row['topic']}, box {row['box']}). Ask the user this, and wait for their answer before "
            f"revealing anything:\nQ: {row['question']}\n(Answer, for you to mark against: {row['answer']})")


def quiz_mark(card_id, correct) -> str:
    """Records whether the user got a card right; it comes back later if right, soon if wrong."""
    correct = str(correct).lower() in ("true", "1", "yes", "right", "correct")
    with store.write_lock, store.connect() as connection:
        row = connection.execute("SELECT * FROM cards WHERE id = ?", (_number(card_id) or 0,)).fetchone()
        if row is None:
            return f"There's no card #{card_id}."
        box = min(row["box"] + 1, max(BOX_DAYS)) if correct else 1
        connection.execute("UPDATE cards SET box = ?, due = ?, right = right + ?, wrong = wrong + ? WHERE id = ?",
                           (box, time.time() + BOX_DAYS[box] * DAY + (0 if correct else 600), int(correct), int(not correct), row["id"]))
    days = BOX_DAYS[box]
    later = f"in {days} day{'s' if days != 1 else ''}" if correct and days else "soon"
    return f"Marked {'right' if correct else 'wrong'}; it comes back {later}."


def quiz_progress() -> str:
    with store.connect() as connection:
        rows = connection.execute("SELECT topic, COUNT(*) AS cards, SUM(right) AS right, SUM(wrong) AS wrong, "
                                  "SUM(due <= ?) AS due FROM cards GROUP BY topic ORDER BY topic", (time.time(),)).fetchall()
    if not rows:
        return "No revision cards yet."
    return "Revision:\n" + "\n".join(
        f"- {r['topic']}: {r['cards']} cards, {r['due']} due now, {r['right']} right / {r['wrong']} wrong so far" for r in rows)


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = {
    "list_add": (lambda a: list_add(a.get("list"), a.get("items")), _tool(
        "list_add", "Add things to one of the user's lists (shopping, to-do, or any list they name).",
        {"list": {"type": "string", "description": "e.g. shopping, to-do, packing"},
         "items": {"type": "array", "items": {"type": "string"}}}, ["list", "items"])),
    "list_show": (lambda a: list_show(a.get("list")), _tool(
        "list_show", "Show a list (numbered), or with no list given, which lists exist.",
        {"list": {"type": "string"}}, [])),
    "list_done": (lambda a: list_done(a.get("list"), a.get("items")), _tool(
        "list_done", "Tick things off a list, by their numbers from list_show or their names.",
        {"list": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}, ["list", "items"])),
    "list_clear": (lambda a: list_clear(a.get("list")), _tool(
        "list_clear", "Empty a list completely (only when the user asks).", {"list": {"type": "string"}}, ["list"])),
    "note_save": (lambda a: note_save(a.get("text")), _tool(
        "note_save", "Save a note for the user (something to look up later, e.g. 'the router password is on the back').",
        {"text": {"type": "string"}}, ["text"])),
    "note_search": (lambda a: note_search(a.get("query", "")), _tool(
        "note_search", "Find the user's notes by a few words (or list the latest with no words).", {"query": {"type": "string"}}, [])),
    "note_delete": (lambda a: note_delete(a.get("note_id")), _tool(
        "note_delete", "Delete a note by its #number.", {"note_id": {"type": "integer"}}, ["note_id"])),
    "deadline_add": (lambda a: deadline_add(a.get("title"), a.get("when"), a.get("notes", "")), _tool(
        "deadline_add", "Track a deadline (assignment, form, payment). The user is reminded a week, 2 days, 1 day and 3 hours before.",
        {"title": {"type": "string"}, "when": {"type": "string", "description": "copy their words: 'Friday', '14 Nov 17:00', 'next Monday'"},
         "notes": {"type": "string"}}, ["title", "when"])),
    "deadlines_show": (lambda a: deadlines_show(), _tool("deadlines_show", "The user's upcoming deadlines.", {}, [])),
    "deadline_done": (lambda a: deadline_done(a.get("deadline_id")), _tool(
        "deadline_done", "Mark a deadline as done by its #number.", {"deadline_id": {"type": "integer"}}, ["deadline_id"])),
    "card_add": (lambda a: card_add(a.get("topic"), a.get("question"), a.get("answer")), _tool(
        "card_add", "Add a revision flashcard for the user's studies (anything they're revising). "
                    "Write clear exam-style questions with short, correct answers.",
        {"topic": {"type": "string", "description": "e.g. networking, cryptography, linux, law and ethics"},
         "question": {"type": "string"}, "answer": {"type": "string"}}, ["topic", "question", "answer"])),
    "quiz_next": (lambda a: quiz_next(a.get("topic", "")), _tool(
        "quiz_next", "Get the next revision card that's due, to ask the user. Ask the question, wait for their answer, then mark it with quiz_mark.",
        {"topic": {"type": "string"}}, [])),
    "quiz_mark": (lambda a: quiz_mark(a.get("card_id"), a.get("correct")), _tool(
        "quiz_mark", "Record whether the user answered a revision card right (it comes back later) or wrong (it comes back soon).",
        {"card_id": {"type": "integer"}, "correct": {"type": "boolean"}}, ["card_id", "correct"])),
    "quiz_progress": (lambda a: quiz_progress(), _tool("quiz_progress", "How the user's revision is going, per topic.", {}, [])),
}


# ---------- a quiz, run by the brain itself ----------
# Small models can't keep track of a quiz (they invent questions and ignore answers), so "quiz me" is handled
# here: ask the next due card, mark the user's reply (the model only judges right/wrong), ask the next.

QUIZ_START = re.compile(r"\b(quiz|test) me\b(?:\s+(?:on|about)\s+(?P<topic>[\w\s&-]+?))?\s*[.!?]*$|\brevision (quiz|session)\b", re.I)
QUIZ_STOP = re.compile(r"^\W*(stop|end|quit|enough|that's enough|no more|i'?m done|done)\b", re.I)
QUIZ_IDLE_SECONDS = 30 * 60
MAX_QUIZ_CLIENTS = 16
_quizzes: dict[str, dict] = {}
_quizzes_lock = threading.Lock()


def _quiz_for(client: str) -> dict:
    with _quizzes_lock:
        if (client or "") not in _quizzes and len(_quizzes) >= MAX_QUIZ_CLIENTS:
            del _quizzes[min(_quizzes, key=lambda name: _quizzes[name]["asked"])]
        return _quizzes.setdefault(client or "", {"card": None, "topic": "", "asked": 0.0})


def _card_due(topic: str):
    with store.connect() as connection:
        query = "SELECT * FROM cards WHERE due <= ?" + (" AND topic LIKE ?" if topic else "") + " ORDER BY box, RANDOM() LIMIT 1"
        return connection.execute(query, (time.time(), f"%{topic}%") if topic else (time.time(),)).fetchone()


def _ask(quiz: dict, topic: str, opening: str = "") -> str:
    card = _card_due(topic)
    if card is None:
        quiz.update(card=None)
        nothing = f"Nothing left to revise{' in ' + topic if topic else ''} right now: cards come back as they fall due."
        return (opening + "\n\n" + nothing).strip() + "\n\n" + quiz_progress()
    quiz.update(card=dict(card), topic=topic, asked=time.time())
    return (opening + "\n\n" if opening else "") + f"{card['topic'].capitalize()}: {card['question']}"


def quiz_turn(text: str, judge, client: str = "", something_else: bool = False) -> str | None:
    """The reply if this message starts, answers or stops a quiz; None if it's nothing to do with one.
    judge(question, expected, given) -> (correct, feedback) asks the model to mark an answer."""
    text = text.strip()
    quiz = _quiz_for(client)
    if match := QUIZ_START.search(text):
        topic = (match.group("topic") or "").strip().lower()
        return _ask(quiz, topic, f"Quiz{' on ' + topic if topic else ''}: answer each one, or say stop.")
    card = quiz["card"]
    if card is None or time.time() - quiz["asked"] > QUIZ_IDLE_SECONDS:
        return None
    if QUIZ_STOP.search(text):
        quiz.update(card=None)
        return "Quiz over.\n\n" + quiz_progress()
    if "?" in text:  # a question of their own, not an answer: the quiz is over, Arty answers it normally
        quiz.update(card=None)
        return None
    if something_else:
        quiz.update(card=None)
        return None
    correct, feedback = judge(card["question"], card["answer"], text)
    quiz_mark(card["id"], correct)
    # The model's feedback adds something when they're right; when wrong, the answer itself says it all.
    verdict = f"Right! {feedback}".strip() if correct else f"Not quite: the answer is {card['answer']}."
    return _ask(quiz, quiz["topic"], verdict)
