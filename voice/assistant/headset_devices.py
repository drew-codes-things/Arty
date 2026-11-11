import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

TOKEN_BYTES = 32
DEVICE_ID_BYTES = 6
MAX_DEVICES = 20
MAX_NAME_CHARS = 40
FILE_MODE = 0o600


class DeviceLimitReached(Exception):
    pass


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def cleaned_name(name: str) -> str:
    printable = "".join(character for character in name if character.isprintable())
    return printable.strip()[:MAX_NAME_CHARS] or "headset"


class HeadsetDevices:

    def __init__(self, path: Path, clock=time.time):
        self.path = path
        self.clock = clock

    def load(self) -> dict[str, dict]:
        if not self.path.is_file():
            return {}
        devices = json.loads(self.path.read_text())
        if not isinstance(devices, dict):
            raise ValueError(f"{self.path} should hold a JSON object of paired headsets")
        return devices

    def save(self, devices: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, staged = tempfile.mkstemp(dir=self.path.parent, prefix=".headset-devices.")
        try:
            with os.fdopen(handle, "w") as staged_file:
                json.dump(devices, staged_file, indent=1, sort_keys=True)
            os.chmod(staged, FILE_MODE)
            os.replace(staged, self.path)
        except BaseException:
            Path(staged).unlink(missing_ok=True)
            raise

    def add(self, name: str) -> tuple[str, str]:
        devices = self.load()
        if len(devices) >= MAX_DEVICES:
            raise DeviceLimitReached(f"{MAX_DEVICES} headsets are paired already; forget one first")
        device_id = secrets.token_hex(DEVICE_ID_BYTES)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        devices[device_id] = {"name": cleaned_name(name), "key": token_key(token), "paired": int(self.clock())}
        self.save(devices)
        return device_id, token

    def device_for(self, token: str) -> str | None:
        if not token:
            return None
        key = token_key(token)
        for device_id, device in self.load().items():
            if hmac.compare_digest(str(device.get("key", "")).encode(), key.encode()):
                return device_id
        return None

    def key_of(self, device_id: str) -> str | None:
        device = self.load().get(device_id)
        return device.get("key") if isinstance(device, dict) else None

    def forget(self, device_id: str) -> bool:
        devices = self.load()
        if devices.pop(device_id, None) is None:
            return False
        self.save(devices)
        return True

    def listed(self) -> list[dict]:
        return [{"device": device_id, "name": device.get("name", ""), "paired": device.get("paired", 0)}
                for device_id, device in sorted(self.load().items())]
