from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass

ROLES = ("pc", "assistant")
SYSTEMS = ("debian", "windows", "assistant")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def write_private_json(path: str, data) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@dataclass
class Device:
    id: str
    name: str
    system: str
    role: str
    token_digest: str
    created: float
    last_seen: float = 0.0
    revoked: bool = False

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "system": self.system, "role": self.role,
                "created": self.created, "last_seen": self.last_seen, "revoked": self.revoked}


class DeviceStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "devices.json")
        self._lock = threading.Lock()

    def list(self) -> list[Device]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [Device(**item) for item in json.load(f)]

    def _save(self, devices: list[Device]) -> None:
        write_private_json(self.path, [asdict(d) for d in devices])

    def add(self, name: str, system: str, role: str) -> tuple[Device, str]:
        if system not in SYSTEMS:
            raise ValueError(f"system must be one of {', '.join(SYSTEMS)}")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        name = " ".join(name.split())[:60] or f"{system} device"
        token = secrets.token_urlsafe(32)
        device = Device(id=uuid.uuid4().hex[:12], name=name, system=system, role=role,
                        token_digest=digest(token), created=time.time())
        with self._lock:
            self._save(self.list() + [device])
        return device, token

    def find(self, token: str) -> Device | None:
        if not token:
            return None
        wanted = digest(token)
        return next((d for d in self.list() if not d.revoked and secrets.compare_digest(d.token_digest, wanted)),
                    None)

    def touch(self, device_id: str) -> None:
        with self._lock:
            devices = self.list()
            for device in devices:
                if device.id == device_id:
                    device.last_seen = time.time()
            self._save(devices)

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = self.list()
            found = False
            for device in devices:
                if device.id == device_id and not device.revoked:
                    device.revoked = True
                    found = True
            if found:
                self._save(devices)
            return found
