"""Notifications other apps show on this desktop, so Arty can tell the user about them.

A background thread watches the session bus (D-Bus BecomeMonitor, which any process may do
for its own user's session) for org.freedesktop.Notifications.Notify calls and keeps the
most recent few in memory only. Nothing is stored on disk or sent anywhere unless the user
asks Arty about their notifications (desktop_notifications) or turns on reading them aloud.
"""
import collections
import sys
import threading
import time

KEEP = 50
IGNORED_APPS = {"arty", "arty desktop", "notify-send", ""}

_recent = collections.deque(maxlen=KEEP)
_changed = threading.Condition()
_counter = 0
_started = False


def _record(app_name, summary, body, kind="app", **extra):
    """kind is "app" (another app's notification) or "arty" (something Arty's brain announced)."""
    global _counter
    with _changed:
        _counter += 1
        _recent.append({"id": _counter, "time": time.time(), "app": app_name, "summary": summary, "body": body, "kind": kind, **extra})
        _changed.notify_all()


def _can_watch():
    return sys.platform.startswith("linux")


def _watch():
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.bus_messages import MatchRule
    from jeepney.io.blocking import open_dbus_connection

    monitoring = DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus", interface="org.freedesktop.DBus.Monitoring")
    rule = MatchRule(type="method_call", interface="org.freedesktop.Notifications", member="Notify").serialise()
    while True:
        try:
            connection = open_dbus_connection(bus="SESSION")
            connection.send_and_get_reply(new_method_call(monitoring, "BecomeMonitor", "asu", ([rule], 0)), timeout=5)
            print("[notifications] watching the desktop's notifications", flush=True)
            while True:
                message = connection.receive()
                if message.header.message_type != MessageType.method_call or len(message.body) < 5:
                    continue
                app_name, _replaces, _icon, summary, body = message.body[:5]
                if str(app_name).strip().lower() in IGNORED_APPS:
                    continue
                _record(str(app_name), str(summary), str(body))
        except Exception as error:  # the bus restarting, or monitoring refused: try again later
            print(f"[notifications] stopped watching ({error}); retrying in 30s", flush=True)
            time.sleep(30)


def start():
    global _started
    if _started:
        return
    _started = True
    if not _can_watch():
        print("[notifications] reading other apps' notifications isn't available on this system", flush=True)
        return
    threading.Thread(target=_watch, name="notifications", daemon=True).start()


def since(last_id, timeout):
    """Notifications after `last_id`, waiting up to `timeout` seconds for one to arrive."""
    deadline = time.time() + timeout
    with _changed:
        while True:
            fresh = [n for n in _recent if n["id"] > last_id]
            remaining = deadline - time.time()
            if fresh or remaining <= 0:
                return fresh, _counter
            _changed.wait(remaining)


def record_announcement(item):
    """Something Arty's brain announced (reminder, briefing, alert, approval request); the window adds it to
    Updates and says it out loud, with Approve / Deny (or Done) for a paused task."""
    _record("Arty", item.get("title", ""), item.get("message", ""), kind="arty", update_kind=item.get("kind", "message"),
            update_id=item.get("id"), created=item.get("created"), task_id=item.get("task_id"), actions=item.get("actions") or [])


def record_notice(summary, notice_id):
    _record("Arty", summary, "", kind="notice", notice=notice_id)


def desktop_notifications(limit=10):
    """The tool: the latest notifications from other apps, newest last."""
    items = [n for n in _recent if n["kind"] == "app"][-max(1, min(int(limit or 10), KEEP)):]
    if not items and not _can_watch():
        system = {"win32": "Windows"}.get(sys.platform, "this system")
        return f"Reading other apps' notifications isn't available on {system} yet, so I can't tell you what you missed."
    if not items:
        return "No notifications since Arty started."
    lines = []
    for item in items:
        minutes = round((time.time() - item["time"]) / 60)
        when = "just now" if minutes < 1 else f"{minutes} min ago"
        text = f"{item['summary']}: {item['body']}" if item["body"] else item["summary"]
        lines.append(f"- {when}, {item['app']}: {text[:300]}")
    return "Recent notifications on this computer:\n" + "\n".join(lines)


SPECS = [
    ("desktop_notifications", desktop_notifications, {
        "description": "Read the notifications that have popped up on their computer recently (messages, emails, alerts), "
                       "newest last. Use when they ask what they missed or whether anyone messaged.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "description": "how many (default 10)"}}}}),
]
