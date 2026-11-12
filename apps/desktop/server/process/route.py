import time
from urllib.parse import urlsplit

import requests

from process import identity
from process.config import load_config

_brain = load_config().get("brain") or {}
DEFAULT_LOCAL_URL = "http://127.0.0.1:8081/v1"
LOCAL_URL = str(_brain.get("base_url") or DEFAULT_LOCAL_URL).rstrip("/")
REMOTE_URL = str(_brain.get("remote_url") or "").rstrip("/")
ACCESS_CLIENT_ID = str(_brain.get("access_client_id") or "")
ACCESS_CLIENT_SECRET = str(_brain.get("access_client_secret") or "")
API_KEY = str(_brain.get("api_key") or "")
LOCAL_CHECK_SECONDS = 1.5
ROUTE_CHECK_SECONDS = 30
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_route = {"checked": float("-inf"), "base": LOCAL_URL}


class UnprovenBrain(requests.ConnectionError):
    pass


def _home_brain_proves_itself():
    nonce = identity.new_nonce()
    try:
        response = requests.get(f"{LOCAL_URL.removesuffix('/v1')}/v1/identity", params={"nonce": nonce},
                                timeout=LOCAL_CHECK_SECONDS)
        proofs = response.json().get("proofs") if response.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError):
        return False
    return identity.is_proven(proofs, API_KEY, nonce)


def _home_needs_proof():
    parts = urlsplit(LOCAL_URL)
    return parts.scheme == "http" and (parts.hostname or "") not in LOOPBACK_HOSTS


def _proven_home():
    if not _home_needs_proof():
        return LOCAL_URL
    if time.monotonic() - _route["checked"] > ROUTE_CHECK_SECONDS:
        _route.update(checked=time.monotonic(), base=LOCAL_URL if _home_brain_proves_itself() else None)
    if _route["base"] is None:
        raise UnprovenBrain(f"Arty's brain at {LOCAL_URL} didn't prove it holds this computer's API key, so nothing was sent "
                            "to it. If you're away from home, add the address through Cloudflare under Connect to another "
                            "Arty in the tray menu.")
    return LOCAL_URL


def base():
    if not REMOTE_URL:
        return _proven_home()
    if time.monotonic() - _route["checked"] > ROUTE_CHECK_SECONDS:
        chosen = LOCAL_URL if _home_brain_proves_itself() else REMOTE_URL
        if chosen != _route["base"]:
            print(f"[brain] using {chosen}", flush=True)
        _route.update(checked=time.monotonic(), base=chosen)
    return _route["base"]


def access_headers():
    if not REMOTE_URL or base() != REMOTE_URL or not ACCESS_CLIENT_ID:
        return {}
    return {"CF-Access-Client-Id": ACCESS_CLIENT_ID, "CF-Access-Client-Secret": ACCESS_CLIENT_SECRET}


def describe():
    return f"{LOCAL_URL} or {REMOTE_URL}" if REMOTE_URL else LOCAL_URL
