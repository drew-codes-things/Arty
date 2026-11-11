"""Expiry watch: warns before the domain registration or a website's TLS certificate runs out.

Checked once a day from the brain's scheduler; settings are "expiry_watch" in brain.json:
{"domains": ["example.com"], "certificates": ["example.com", ...]}. The domain's expiry comes from
RDAP (free, no account), certificates from connecting to the site. Each thing is warned about once per
warning point (30, 14, 7 and 1 days before), remembered in STATE_FILE.
"""
import json
import logging
import os
import socket
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(os.environ.get("EXPIRY_STATE", "/srv/ai/brain/state/expiry-warned.json"))
RDAP_URL = "https://rdap.org/domain/{}"
WARN_DAYS = (30, 14, 7, 1)
CHECK_EVERY_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 20
DAY_SECONDS = 86400

CERT_HAS_EXPIRED = 10

log = logging.getLogger("brain.expiry")


class CertificateProblem(Exception):
    pass
_last_check = {"time": 0.0}


def domain_expiry(domain: str) -> float:
    """When the domain's registration runs out (epoch seconds), from RDAP."""
    with urllib.request.urlopen(RDAP_URL.format(domain), timeout=TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    for event in data.get("events", []):
        if event.get("eventAction") == "expiration":
            return datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00")).timestamp()
    raise ValueError(f"RDAP gave no expiry date for {domain}")


def certificate_expiry(host: str) -> float:
    """When the TLS certificate the site serves runs out (epoch seconds)."""
    context = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=TIMEOUT_SECONDS) as connection:
        try:
            with context.wrap_socket(connection, server_hostname=host) as secure:
                not_after = secure.getpeercert()["notAfter"]
        except ssl.SSLCertVerificationError as error:
            if error.verify_code == CERT_HAS_EXPIRED:
                raise CertificateProblem("it has expired") from None
            raise CertificateProblem(f"it isn't valid ({error.verify_message})") from None
    return ssl.cert_time_to_seconds(not_after)


def problem_warning(name: str, problem: str, warned: dict) -> str | None:
    key = f"{name}@problem:{problem}"
    if key in warned:
        return None
    warned[key] = [0]
    return f"{name} has a problem: {problem}."


def forget_problems(name: str, warned: dict):
    for key in [key for key in warned if key.startswith(f"{name}@problem:")]:
        del warned[key]


def warning_for(name: str, expires: float, now: float, warned: dict) -> str | None:
    """The message to send if `name` has just reached a warning point it wasn't warned about yet."""
    days_left = (expires - now) / DAY_SECONDS
    due = [days for days in WARN_DAYS if days_left <= days]
    if not due:
        return None
    point = min(due)
    key = f"{name}@{int(expires)}"  # a renewal (new expiry date) starts the warnings afresh
    if point in warned.get(key, []):
        return None
    warned.setdefault(key, []).append(point)
    when = datetime.fromtimestamp(expires, timezone.utc).strftime("%-d %B %Y")
    if days_left <= 0:
        return f"{name} expired on {when}."
    return f"{name} expires on {when} ({max(1, round(days_left))} days left)."


def due_warnings(settings: dict) -> list[str]:
    """Warnings to send now. Runs the checks at most once a day; a failed check is logged and retried tomorrow."""
    now = time.time()
    if now - _last_check["time"] < CHECK_EVERY_SECONDS:
        return []
    _last_check["time"] = now
    checks = [(f"The domain {d}", domain_expiry, d) for d in settings.get("domains", [])]
    checks += [(f"The certificate for {h}", certificate_expiry, h) for h in settings.get("certificates", [])]
    try:
        warned = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except ValueError:
        warned = {}
    messages = []
    for name, check, target in checks:
        try:
            expires = check(target)
            forget_problems(name, warned)
            message = warning_for(name, expires, now, warned)
        except CertificateProblem as problem:
            message = problem_warning(name, str(problem), warned)
        except (OSError, ValueError, KeyError, ssl.SSLError) as error:
            log.warning("expiry check for %s failed: %s", target, error)
            continue
        if message:
            messages.append(message)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(warned))
    return messages
