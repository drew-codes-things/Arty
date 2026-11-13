import logging

log = logging.getLogger("brain.notify")

EMAIL = "email"
SERVER = "server"
TASKS = "tasks"
APPROVALS = "approvals"
REMINDERS = "reminders"
CALENDAR = "calendar"
LECTURES = "lectures"
ARTY = "arty"
OTHER = "other"
CATEGORIES = (EMAIL, SERVER, TASKS, APPROVALS, REMINDERS, CALENDAR, LECTURES, ARTY, OTHER)

listeners = []


def known_category(value) -> str:
    named = str(value).strip().lower() if isinstance(value, str) else ""
    return named if named in CATEGORIES else OTHER


def send(title: str, message: str, actions: list[dict] | None = None, urgent: bool = False, category: str = OTHER) -> str:
    """Send to Updates in the phone and desktop apps. actions: [{"label", "url", "callback"}]. Failures are logged,
    never raised, so a notification problem can't break the task that triggered it. `urgent` shows it as an alert."""
    actions = actions or []
    category = known_category(category)
    delivered = False
    for listener in listeners:
        try:
            listener(title, message, actions, urgent, category)
            delivered = True
        except Exception:  # a feed problem must never stop the task that sent it
            log.exception("notification listener failed")
    return "sent to Updates in the phone and desktop apps" if delivered else "notification failed: the apps' feed isn't set up"
