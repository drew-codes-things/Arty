"""SQLite storage for background tasks and schedules.

One small database on the shared AI disk (/srv/ai), so it is in the nightly
Google Drive sync. A fresh connection per call keeps it safe across the HTTP,
worker and scheduler threads; WAL mode lets them read while one writes.
"""
import json
import os
import secrets
import sqlite3
import threading
import time

DB_FILE = os.environ.get("BRAIN_DB", "/srv/ai/brain/state/arty.db")
TASK_STATES = ("queued", "running", "waiting", "done", "failed", "cancelled")
write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  instructions TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  source TEXT NOT NULL DEFAULT 'api',
  schedule_id INTEGER,
  token TEXT NOT NULL,
  history TEXT,
  waiting TEXT,
  result TEXT,
  tools_used TEXT,
  snapshot_taken INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  task_id INTEGER,
  actions TEXT,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  starts REAL NOT NULL,
  ends REAL NOT NULL,
  all_day INTEGER NOT NULL DEFAULT 0,
  repeat TEXT,
  location TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  instructions TEXT NOT NULL,
  cron TEXT,
  description TEXT NOT NULL,
  next_run REAL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run REAL,
  last_task_id INTEGER,
  created REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    connection = sqlite3.connect(DB_FILE, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init():
    with write_lock, connect() as connection:
        connection.executescript(SCHEMA)
        # Added later: plain reminders store the exact text to send, and need no model when they fire.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(schedules)")}
        if "message" not in columns:
            connection.execute("ALTER TABLE schedules ADD COLUMN message TEXT")
        # Added later: which chat apps (Telegram, Discord) a reminder also goes to; empty means only Arty's apps.
        if "chats" not in [row[1] for row in connection.execute("PRAGMA table_info(schedules)")]:
            connection.execute("ALTER TABLE schedules ADD COLUMN chats TEXT NOT NULL DEFAULT ''")
        # A task interrupted by a restart goes back in the queue rather than hanging as "running".
        connection.execute("UPDATE tasks SET status = 'queued', updated = ? WHERE status = 'running'", (time.time(),))


def _task(row) -> dict | None:
    if row is None:
        return None
    task = dict(row)
    for key in ("history", "waiting", "tools_used"):
        task[key] = json.loads(task[key]) if task[key] else None
    return task


def create_task(title: str, instructions: str, source: str, schedule_id: int | None = None) -> dict:
    now = time.time()
    with write_lock, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO tasks (title, instructions, source, schedule_id, token, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title[:120], instructions, source, schedule_id, secrets.token_urlsafe(24), now, now),
        )
        return get_task(cursor.lastrowid, connection)


def get_task(task_id: int, connection: sqlite3.Connection | None = None) -> dict | None:
    if connection is not None:
        return _task(connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    with connect() as fresh:
        return _task(fresh.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def list_tasks(status: str = "", limit: int = 30) -> list[dict]:
    with connect() as connection:
        if status:
            rows = connection.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = connection.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_task(row) for row in rows]


def update_task(task_id: int, **fields):
    if not fields:
        return
    for key in ("history", "waiting", "tools_used"):
        if key in fields and fields[key] is not None:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated"] = time.time()
    columns = ", ".join(f"{key} = ?" for key in fields)
    with write_lock, connect() as connection:
        connection.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*fields.values(), task_id))


def claim_next_task() -> dict | None:
    """Atomically take the oldest queued task and mark it running."""
    with write_lock, connect() as connection:
        row = connection.execute("SELECT id FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        connection.execute("UPDATE tasks SET status = 'running', updated = ? WHERE id = ?", (time.time(), row["id"]))
        return get_task(row["id"], connection)


ANNOUNCEMENTS_KEPT = 500


def add_announcement(kind: str, title: str, message: str, task_id: int | None = None, actions: list[str] | None = None) -> int:
    """Records something Arty told the user (reminder, task result, alert), for the apps' feeds."""
    with write_lock, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO announcements (kind, title, message, task_id, actions, created) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, title[:200], message[:4000], task_id, json.dumps(actions) if actions else None, time.time()),
        )
        connection.execute("DELETE FROM announcements WHERE id <= ?", (cursor.lastrowid - ANNOUNCEMENTS_KEPT,))
        return cursor.lastrowid


def list_announcements(after: int = 0, limit: int = 50) -> list[dict]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM announcements WHERE id > ? ORDER BY id DESC LIMIT ?", (after, limit)).fetchall()
    announcements = [dict(row) for row in reversed(rows)]
    for item in announcements:
        item["actions"] = json.loads(item["actions"]) if item["actions"] else []
    return announcements


def latest_announcement_id() -> int:
    with connect() as connection:
        return connection.execute("SELECT COALESCE(MAX(id), 0) FROM announcements").fetchone()[0]


def add_event(title: str, starts: float, ends: float, all_day: bool, repeat: str | None, location: str = "", notes: str = "") -> dict:
    """An event in Arty's own calendar. `repeat` is a cron rule for repeating events."""
    with write_lock, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO events (title, starts, ends, all_day, repeat, location, notes, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title[:200], starts, ends, int(all_day), repeat, location[:200], notes[:1000], time.time()),
        )
        return dict(connection.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone())


def list_events() -> list[dict]:
    with connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY starts").fetchall()]


def get_event(event_id: int) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def update_event(event_id: int, **fields) -> bool:
    columns = ", ".join(f"{key} = ?" for key in fields)
    with write_lock, connect() as connection:
        return connection.execute(f"UPDATE events SET {columns} WHERE id = ?", (*fields.values(), event_id)).rowcount > 0


def delete_event(event_id: int) -> bool:
    with write_lock, connect() as connection:
        return connection.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount > 0


def create_schedule(title: str, instructions: str, cron: str | None, description: str, next_run: float, message: str | None = None,
                    chats: str = "") -> dict:
    with write_lock, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO schedules (title, instructions, cron, description, next_run, created, message, chats) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title[:120], instructions, cron, description, next_run, time.time(), message, chats),
        )
        return dict(connection.execute("SELECT * FROM schedules WHERE id = ?", (cursor.lastrowid,)).fetchone())


def list_schedules(include_disabled: bool = False) -> list[dict]:
    query = "SELECT * FROM schedules" + ("" if include_disabled else " WHERE enabled = 1") + " ORDER BY next_run"
    with connect() as connection:
        return [dict(row) for row in connection.execute(query).fetchall()]


def due_schedules(now: float) -> list[dict]:
    with connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ?", (now,)).fetchall()]


def update_schedule(schedule_id: int, **fields) -> bool:
    columns = ", ".join(f"{key} = ?" for key in fields)
    with write_lock, connect() as connection:
        return connection.execute(f"UPDATE schedules SET {columns} WHERE id = ?", (*fields.values(), schedule_id)).rowcount > 0
