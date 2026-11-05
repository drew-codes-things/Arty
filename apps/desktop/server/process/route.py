import socket
import time
from urllib.parse import urlparse

from process.config import load_config

_brain = load_config().get("brain") or {}
LOCAL_URL = str(_brain.get("base_url") or "http://192.168.1.13:8081/v1").rstrip("/")
REMOTE_URL = str(_brain.get("remote_url") or "").rstrip("/")
ACCESS_CLIENT_ID = str(_brain.get("access_client_id") or "")
ACCESS_CLIENT_SECRET = str(_brain.get("access_client_secret") or "")
LOCAL_CHECK_SECONDS = 1.5
ROUTE_CHECK_SECONDS = 30
_route = {"checked": float("-inf"), "base": LOCAL_URL}


def _local_reachable():
    address = urlparse(LOCAL_URL)
    port = address.port or (443 if address.scheme == "https" else 80)
    try:
        with socket.create_connection((address.hostname, port), timeout=LOCAL_CHECK_SECONDS):
            return True
    except OSError:
        return False


def base():
    if not REMOTE_URL:
        return LOCAL_URL
    if time.monotonic() - _route["checked"] > ROUTE_CHECK_SECONDS:
        chosen = LOCAL_URL if _local_reachable() else REMOTE_URL
        if chosen != _route["base"]:
            print(f"[brain] using {chosen}", flush=True)
        _route.update(checked=time.monotonic(), base=chosen)
    return _route["base"]


def access_headers():
    if base() != REMOTE_URL or not ACCESS_CLIENT_ID:
        return {}
    return {"CF-Access-Client-Id": ACCESS_CLIENT_ID, "CF-Access-Client-Secret": ACCESS_CLIENT_SECRET}


def describe():
    return f"{LOCAL_URL} or {REMOTE_URL}" if REMOTE_URL else LOCAL_URL
