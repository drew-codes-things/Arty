import json
import logging
import threading
import time
import urllib.parse

import connections
import notify
import store

from announcements import announced
from steering import task_links

log = logging.getLogger("brain")


# ---------- announcements: everything Arty tells the user, as a feed for its own apps ----------


HEAL_COOLDOWN_SECONDS = 30 * 60
_healing: dict[str, float] = {}
_healing_lock = threading.Lock()


def claim_healing(name: str) -> bool:
    with _healing_lock:
        if time.time() - _healing.get(name, 0) < HEAL_COOLDOWN_SECONDS:
            return False
        _healing[name] = time.time()
        return True


def monitor_port(monitor: dict, url) -> int:
    try:
        return int(monitor.get("port") or url.port or (443 if url.scheme == "https" else 80 if url.scheme == "http" else 0))
    except (TypeError, ValueError):
        return 0


def usable_fix(diagnosis: dict) -> dict | None:
    fix = diagnosis.get("fix")
    if not isinstance(fix, dict) or fix.get("action") not in ("start", "restart") or fix.get("vmid") in (None, ""):
        return None
    return {**fix, "name": str(fix.get("name") or f"guest {fix['vmid']}")}


def self_heal(monitor: dict):
    """A service went down: diagnose it, and if starting or restarting its guest would fix it, offer that
    with Fix / Ignore buttons (a background task waiting for approval, like any other)."""
    name = str(monitor.get("name") or "A service")
    if not claim_healing(name):
        return
    try:
        url = urllib.parse.urlparse(str(monitor.get("url") or ""))
        address = str(monitor.get("hostname") or url.hostname or "")
    except ValueError:
        url, address = urllib.parse.urlparse(""), str(monitor.get("hostname") or "")
    port = monitor_port(monitor, url)
    if not address:
        return
    connections.hub.refresh()
    try:
        diagnosis = json.loads(connections.hub.call("homelab__diagnose", {"address": address, "port": port, "guest_name": ""}))
    except (ValueError, TypeError):
        log.warning("self-heal: diagnosis of %s failed", name)
        return
    if not isinstance(diagnosis, dict):
        log.warning("self-heal: diagnosis of %s wasn't an object", name)
        return
    diagnosis["summary"] = str(diagnosis.get("summary") or f"{name} isn't answering.")
    fix = usable_fix(diagnosis)
    if not fix:
        notify.send(f"🔎 {name} is down", diagnosis["summary"], category=notify.SERVER)
        return
    tool = "homelab__start_guest" if fix["action"] == "start" else "homelab__restart_guest"
    task = store.create_task(f"Fix: {name} is down", f"{name} went down. {diagnosis['summary']}", "self-heal")
    state = {"messages": [{"role": "user", "content": (
        f"Background task #{task['id']}: {name} ({address}{':' + str(port) if port else ''}) went down. Diagnosis: {diagnosis['summary']} "
        f"You asked the user whether to {fix['action']} {fix['name']}. When you get their answer, if it was done, wait a moment, then "
        f"call homelab__diagnose with address {address} and port {port} to check it's answering again, and report in one or two sentences.")}],
        "context": {}, "groups": ["homelab"]}
    waiting = {"kind": "approval", "message": f"{fix['action']} {fix['name']}", "call": {"name": tool, "arguments": {"vmid": fix["vmid"]}},
               "recheck": {"address": address, "port": port}}
    store.update_task(task["id"], status="waiting", history=state, waiting=waiting)
    buttons = task_links(store.get_task(task["id"]), [(f"Fix ({fix['action']} {fix['name']})", "approve"), ("Ignore", "deny")])
    notify.send(f"🛠 {name} is down", f"{diagnosis['summary']}\n\nShall I {fix['action']} {fix['name']}?", buttons, urgent=True,
                category=notify.APPROVALS)


def record_alert(body: dict) -> str:
    """Uptime Kuma webhook: a service went down or came back. It only goes into the feed (the desktop
    app says it out loud, the phone shows it), like every notification."""
    monitor = (body.get("monitor") if isinstance(body.get("monitor"), dict) else {}).get("name") or "A service"
    heartbeat = body.get("heartbeat") if isinstance(body.get("heartbeat"), dict) else {}
    if heartbeat.get("status") == 0:
        title, kind = f"{monitor} is down", "alert"
    elif heartbeat.get("status") == 1:
        title, kind = f"{monitor} is back up", "message"
    else:
        title, kind = f"Uptime Kuma: {monitor}", "message"
    store.add_announcement(kind, title, str(heartbeat.get("msg") or body.get("msg") or "")[:500], category=notify.SERVER)
    with announced:
        announced.notify_all()
    if heartbeat.get("status") == 0:
        details = body.get("monitor") if isinstance(body.get("monitor"), dict) else {}
        threading.Thread(target=self_heal, args=(details,), name="self-heal", daemon=True).start()
    return title
