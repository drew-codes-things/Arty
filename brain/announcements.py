import threading
import time

import notify
import store


announced = threading.Condition()
ANNOUNCEMENT_WAIT_SECONDS = 55  # under Cloudflare's 100 s request limit


def record_announcement(title, message, actions, urgent, category=notify.OTHER):
    """notify.send listener: keeps a copy of each notification for the desktop and phone apps."""
    task_ids = {int(a["callback"].split(":")[1]) for a in actions if str(a.get("callback", "")).startswith("t:")}
    kind = "approval" if task_ids else "reminder" if title.startswith(("⏰", "📅")) else "alert" if urgent else "message"
    store.add_announcement(kind, title, message, next(iter(task_ids), None),
                           [a["callback"].split(":")[2] for a in actions if str(a.get("callback", "")).startswith("t:")],
                           category=notify.known_category(category))
    with announced:
        announced.notify_all()


def wait_for_announcements(after: int, wait: float) -> list[dict]:
    deadline = time.time() + min(max(wait, 0), ANNOUNCEMENT_WAIT_SECONDS)
    with announced:
        while True:
            found = store.list_announcements(after)
            remaining = deadline - time.time()
            if found or remaining <= 0:
                return found
            announced.wait(remaining)
