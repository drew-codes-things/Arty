import logging

log = logging.getLogger("brain.notify")

# Called with every notification (title, message, actions, urgent). See brain.record_announcement.
listeners = []


def send(title: str, message: str, actions: list[dict] | None = None, urgent: bool = False) -> str:
    """Send to Updates in the phone and desktop apps. actions: [{"label", "url", "callback"}]. Failures are logged,
    never raised, so a notification problem can't break the task that triggered it. `urgent` shows it as an alert."""
    actions = actions or []
    delivered = False
    for listener in listeners:
        try:
            listener(title, message, actions, urgent)
            delivered = True
        except Exception:  # a feed problem must never stop the task that sent it
            log.exception("notification listener failed")
    return "sent to Updates in the phone and desktop apps" if delivered else "notification failed: the apps' feed isn't set up"
