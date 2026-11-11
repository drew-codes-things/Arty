import base64
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))

from devices import DeviceStore
from installers import build_installer


def test_device_tokens_are_stored_hashed(tmp_path):
    store = DeviceStore(str(tmp_path))
    device, token = store.add("  Desk   PC ", "debian", "pc")
    raw = (tmp_path / "devices.json").read_text()
    assert token not in raw
    assert device.name == "Desk PC"
    assert store.find(token).id == device.id
    assert oct((tmp_path / "devices.json").stat().st_mode & 0o777) == "0o600"


def test_revoked_device_is_refused(tmp_path):
    store = DeviceStore(str(tmp_path))
    device, token = store.add("PC", "windows", "pc")
    assert store.revoke(device.id)
    assert store.find(token) is None
    assert not store.revoke(device.id)


def test_bad_values_are_rejected(tmp_path):
    store = DeviceStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.add("PC", "macos", "pc")
    with pytest.raises(ValueError):
        store.add("PC", "debian", "admin")
    assert store.find("") is None


def embedded_config(script: str) -> dict:
    return json.loads(base64.b64decode(re.findall(r'"([A-Za-z0-9+/=]{40,})"', script)[-1]))


def test_debian_installer_carries_its_config():
    filename, body = build_installer("debian", "http://hub:8780", "secret-token", "Desk")
    script = body.decode()
    assert filename == "my-voice-setup.sh"
    assert "__" + "CONFIG_B64__" not in script
    assert embedded_config(script) == {"hub": "http://hub:8780", "token": "secret-token", "name": "Desk"}


def test_windows_installer_is_crlf_with_bom():
    filename, body = build_installer("windows", "http://hub:8780", "secret-token", "Desk")
    assert filename == "my-voice-setup.ps1"
    assert body.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in body and b"\n" not in body.replace(b"\r\n", b"")


def test_device_name_cannot_break_the_script():
    _, body = build_installer("debian", "http://hub:8780", "t", "x\"; rm -rf ~ #`$(id)`")
    name_line = next(line for line in body.decode().splitlines() if line.startswith("DEVICE_NAME="))
    value = name_line[len('DEVICE_NAME="'):-1]
    assert not set(value) & set('"$`\\')


def test_a_device_seen_again_within_a_minute_does_not_rewrite_the_file(tmp_path, monkeypatch):
    import devices
    store = DeviceStore(str(tmp_path))
    device, _ = store.add("PC", "debian", "pc")
    writes = []
    real_write = devices.write_private_json
    monkeypatch.setattr(devices, "write_private_json", lambda path, data: (writes.append(1), real_write(path, data)))
    store.touch(device.id)
    store.touch(device.id)
    store.touch(device.id)
    assert len(writes) == 1
    assert store.list()[0].last_seen > 0
