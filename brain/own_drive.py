import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import app_settings

SETTINGS_FILE = app_settings.settings_file("OWN_APPS_ENV", "/srv/ai/secrets/own-apps.env")
TIMEOUT_SECONDS = 60
USER_HEADER = "X-Forwarded-User"
PROXY_SECRET_HEADER = "X-Drive-Proxy-Secret"
CSRF_HEADER = "X-CSRF-Token"
MY_DRIVE = 0
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
MOST_SAVED_BYTES = 50 * 1024 * 1024
DAY_SECONDS = 86400
LONGEST_SHARE_DAYS = 365


class DriveAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveSettings:
    url: str
    user: str
    proxy_secret: str
    public_url: str


def load_settings(values: dict[str, str] | None = None) -> DriveSettings | None:
    values = app_settings.read_settings(SETTINGS_FILE) if values is None else values
    url = values.get("OWN_DRIVE_URL", "").rstrip("/")
    if not url:
        return None
    if not app_settings.is_safe_address(url):
        raise DriveAppError("OWN_DRIVE_URL must be https (or http on this machine or the home network)")
    return DriveSettings(url=url, user=values.get("OWN_DRIVE_USER", ""), proxy_secret=values.get("OWN_DRIVE_PROXY_SECRET", ""),
                         public_url=(values.get("OWN_DRIVE_PUBLIC_URL") or url).rstrip("/"))


class DirectDrive:
    most_bytes = MOST_SAVED_BYTES

    def __init__(self, settings: DriveSettings):
        self.settings = settings
        self._csrf = ""

    def _send(self, method: str, path: str, body: bytes | None = None, content_type: str = "application/json"):
        request = urllib.request.Request(self.settings.url + path, data=body, method=method)
        if self.settings.user:
            request.add_header(USER_HEADER, self.settings.user)
        if self.settings.proxy_secret:
            request.add_header(PROXY_SECRET_HEADER, self.settings.proxy_secret)
        if method not in ("GET", "HEAD"):
            request.add_header(CSRF_HEADER, self._csrf_token())
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise DriveAppError(f"the Starfire Drive app answered {error.code}: {_error_text(error.read())}") from None
        except (urllib.error.URLError, OSError) as error:
            raise DriveAppError(f"couldn't reach the Starfire Drive app ({type(error).__name__})") from None
        return json.loads(raw) if raw else None

    def json(self, method: str, path: str, payload=None):
        return self._send(method, path, json.dumps(payload).encode() if payload is not None else None)

    def _csrf_token(self) -> str:
        if not self._csrf:
            session = self._send("GET", "/api/session") or {}
            self._csrf = str(session.get("csrfToken") or "")
            if not self._csrf:
                raise DriveAppError("the Starfire Drive app didn't hand out a CSRF token")
        return self._csrf

    def upload(self, name: str, data: bytes, folder_id: int) -> dict:
        upload = self.json("POST", "/api/uploads", {"name": name, "size": len(data), "parentId": folder_id})
        chunk = int(upload.get("chunkBytes") or DEFAULT_CHUNK_BYTES)
        for offset in range(0, len(data), chunk):
            self._send("PUT", f"/api/uploads/{urllib.parse.quote(upload['id'])}?offset={offset}", data[offset:offset + chunk],
                       "application/octet-stream")
        return self.json("POST", f"/api/uploads/{urllib.parse.quote(upload['id'])}/finish")

    def share_link(self, token: str) -> str:
        return f"{self.settings.public_url}/s/{token}"


class DriveClient:
    def __init__(self, connection):
        self.connection = connection

    @property
    def most_bytes(self) -> int:
        return self.connection.most_bytes

    def folder_id(self, name: str) -> int:
        if not name:
            return MY_DRIVE
        listing = self.connection.json("GET", f"/api/folders/{MY_DRIVE}") or {}
        wanted = name.strip().strip("/").casefold()
        matching = [item for item in listing.get("items", []) if item.get("kind") == "folder"
                    and str(item.get("name", "")).casefold() == wanted]
        if not matching:
            raise DriveAppError(f"there's no folder called {name!r} at the top of My Drive")
        return int(matching[0]["id"])

    def save(self, name: str, data: bytes, folder_id: int = MY_DRIVE) -> dict:
        if len(data) > self.most_bytes:
            raise DriveAppError(f"that file is over {self.most_bytes // (1024 * 1024)} MB; upload it in the Starfire Drive app")
        return self.connection.upload(name, data, folder_id)

    def find_file(self, name: str) -> dict:
        found = self.connection.json("GET", f"/api/search?{urllib.parse.urlencode({'q': name})}") or {}
        wanted = name.strip().casefold()
        exact = [item for item in found.get("items", []) if str(item.get("name", "")).casefold() == wanted]
        if len(exact) != 1:
            raise DriveAppError(f"{'no' if not exact else len(exact)} files in Drive are called {name!r}; give the exact name")
        return exact[0]

    def share(self, node_id: int, days: int) -> dict:
        days = max(1, min(int(days), LONGEST_SHARE_DAYS))
        share = self.connection.json("POST", "/api/shares", {"nodeId": int(node_id), "expiresAt": time.time() + days * DAY_SECONDS})
        return {**share, "link": self.connection.share_link(share["token"])}

    def trash(self, node_id: int):
        self.connection.json("POST", "/api/nodes/trash", {"ids": [int(node_id)]})

    def revoke(self, share_id: int):
        self.connection.json("DELETE", f"/api/shares/{int(share_id)}")


def _error_text(raw: bytes) -> str:
    try:
        return str(json.loads(raw).get("error", ""))[:200]
    except (ValueError, AttributeError):
        return "no details"
