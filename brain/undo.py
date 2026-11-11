""""Undo that": removes the last thing Arty made (a reminder, calendar event, list items, a note, a deadline or a
revision card). Every table records when each row was created, so the newest row across them is what was made
last, however it was made (a quick action, the model's tools or an app). Only recent things are undone, so an
"undo" long after never removes something old.
"""
import re
import time

import store

UNDO_WINDOW_SECONDS = 2 * 3600
# List items added in one request are written one after another: they go together.
SAME_REQUEST_SECONDS = 2

# "undo that", "undo the last thing", "take that back", "scrap that": the whole message, nothing else.
REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you)\s+)*(?:undo(?: that| it| the last(?: thing| one)?)?"
                     r"|take that back|scrap that|remove what you just (?:added|made|set))(?: please)?\W*$", re.IGNORECASE)

# (table, the column that names a row, what to call it in the reply); list items are worded separately.
KINDS = [
    ("schedules", "title", "the reminder"),
    ("events", "title", "the calendar event"),
    ("list_items", "item", ""),
    ("notes", "text", "the note"),
    ("deadlines", "title", "the deadline"),
    ("cards", "question", "the revision card"),
]
LABEL_CHARS = 80


def is_request(text: str) -> bool:
    return bool(REQUEST.search(text or ""))


def newest(connection, since: float, until: float = float("inf")):
    """(table, row id, created, label column value, list name or None) of the newest recent row, or None."""
    found = None
    for table, label, _ in KINDS:
        columns = f"id, created, {label}" + (", list" if table == "list_items" else "")
        row = connection.execute(f"SELECT {columns} FROM {table} WHERE created >= ? AND created <= ? ORDER BY created DESC LIMIT 1",
                                 (since, until)).fetchone()
        if row and (found is None or row[1] > found[2]):
            found = (table, row[0], row[1], row[2], row[3] if table == "list_items" else None)
    return found


def undo_last(now: float | None = None, made_between: tuple[float, float] | None = None) -> str:
    """Removes the newest thing made in the last UNDO_WINDOW_SECONDS and says what it was."""
    now = time.time() if now is None else now
    since, until = now - UNDO_WINDOW_SECONDS, now
    if made_between is not None:
        since, until = max(since, made_between[0]), min(until, made_between[1])
    with store.write_lock, store.connect() as connection:
        found = newest(connection, since, until)
        if found is None:
            return "There's nothing recent for me to undo: I haven't set or added anything in the last couple of hours."
        table, row_id, created, label, list_name = found
        if table == "list_items":
            items = connection.execute("SELECT id, item FROM list_items WHERE list = ? AND created >= ? ORDER BY id",
                                       (list_name, created - SAME_REQUEST_SECONDS)).fetchall()
            connection.executemany("DELETE FROM list_items WHERE id = ?", [(item_id,) for item_id, _ in items])
            names = ", ".join(f'"{item}"' for _, item in items)
            return f"Undone: I removed {names} from the {list_name} list."
        connection.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    noun = next(noun for name, _, noun in KINDS if name == table)
    text = " ".join(str(label).split())
    shown = text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS].rstrip() + "..."
    return f'Undone: I removed {noun} "{shown}".'
