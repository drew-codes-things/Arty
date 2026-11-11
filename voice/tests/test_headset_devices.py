import asyncio
import hashlib
import hmac
import logging
import os
import stat
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import pytest
from aiohttp.test_utils import TestClient, TestServer

import headset
import headset_devices

OWNER_KEY = "o" * 40
OWNER = {"Authorization": f"Bearer {OWNER_KEY}"}
NONCE = "ab" * 16


@pytest.fixture
def devices_file(tmp_path, monkeypatch):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", OWNER_KEY)
    monkeypatch.setattr(headset, "DEVICES_FILE", str(tmp_path / "state" / "headset-devices.json"))
    monkeypatch.setattr(headset, "LEGACY_PAIRING", True)
    return tmp_path / "state" / "headset-devices.json"


def run_with_client(steps):
    async def run():
        client = TestClient(TestServer(headset.build_app(None, None)))
        await client.start_server()
        try:
            return await steps(client)
        finally:
            await client.close()
    return asyncio.run(run())


async def pair(client, name="Quest 3S"):
    code = (await (await client.get("/pair/new", headers=OWNER)).json())["code"]
    return await client.post("/pair/redeem", json={"code": code, "device_name": name})


def caller_for(token: str, devices_path):
    request = types.SimpleNamespace(headers={"Authorization": f"Bearer {token}"},
                                    app={"devices": headset_devices.HeadsetDevices(devices_path)})
    return headset.caller(request)


def test_a_named_headset_gets_its_own_key_and_only_a_hash_is_stored(devices_file):
    async def steps(client):
        reply = await pair(client)
        return reply.status, await reply.json()

    status, body = run_with_client(steps)
    assert status == 200
    assert body["token"] != OWNER_KEY and len(body["token"]) >= 40 and body["device"]
    stored = devices_file.read_text()
    assert body["token"] not in stored and headset_devices.token_key(body["token"]) in stored
    assert stat.S_IMODE(os.stat(devices_file).st_mode) == 0o600
    assert caller_for(body["token"], devices_file) == body["device"]
    assert caller_for(OWNER_KEY, devices_file) == headset.OWNER
    assert caller_for("guess", devices_file) is None


def test_a_headset_key_cannot_make_codes_or_manage_headsets(devices_file):
    async def steps(client):
        token = (await (await pair(client)).json())["token"]
        device = {"Authorization": f"Bearer {token}"}
        return [(await client.get("/pair/new", headers=device)).status,
                (await client.get("/pair/devices", headers=device)).status,
                (await client.post("/pair/forget", json={"device": "x"}, headers=device)).status]

    assert run_with_client(steps) == [401, 401, 401]


def test_the_owner_can_list_and_forget_a_headset(devices_file):
    async def steps(client):
        body = await (await pair(client, "Quest\n3S")).json()
        listed = await (await client.get("/pair/devices", headers=OWNER)).json()
        forgot = await client.post("/pair/forget", json={"device": body["device"]}, headers=OWNER)
        unknown = await client.post("/pair/forget", json={"device": body["device"]}, headers=OWNER)
        return body, listed, forgot.status, unknown.status

    body, listed, forgot, unknown = run_with_client(steps)
    assert [(entry["device"], entry["name"]) for entry in listed["devices"]] == [(body["device"], "Quest3S")]
    assert "key" not in listed["devices"][0]
    assert (forgot, unknown) == (200, 404)
    assert caller_for(body["token"], devices_file) is None


def test_identity_for_a_headset_is_keyed_by_its_own_key(devices_file):
    async def steps(client):
        body = await (await pair(client)).json()
        mine = await client.get("/identity", params={"nonce": NONCE, "device": body["device"]})
        unknown = await client.get("/identity", params={"nonce": NONCE, "device": "000000000000"})
        return body["token"], await mine.json(), unknown.status

    token, proof, unknown = run_with_client(steps)
    key = headset_devices.token_key(token).encode()
    assert proof["proof"] == hmac.new(key, ("arty-identity:" + NONCE).encode(), hashlib.sha256).hexdigest()
    assert unknown == 404


def test_pairing_without_a_name_can_be_switched_off(devices_file, monkeypatch):
    monkeypatch.setattr(headset, "LEGACY_PAIRING", False)

    async def steps(client):
        code = (await (await client.get("/pair/new", headers=OWNER)).json())["code"]
        reply = await client.post("/pair/redeem", json={"code": code})
        return reply.status, await reply.text()

    status, text = run_with_client(steps)
    assert status == 400 and OWNER_KEY not in text


def test_identity_answers_are_limited_per_address(devices_file):
    async def steps(client):
        return [(await client.get("/identity", params={"nonce": NONCE})).status
                for _ in range(headset.IDENTITY_LIMIT + 1)]

    statuses = run_with_client(steps)
    assert statuses[:headset.IDENTITY_LIMIT] == [200] * headset.IDENTITY_LIMIT and statuses[-1] == 429


def test_a_short_headset_key_stops_the_server_from_starting(monkeypatch, caplog):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", "short-key")
    monkeypatch.setattr(headset, "HEADSET_PORT", 0)
    with caplog.at_level(logging.ERROR, logger="arty-voice.headset"):
        asyncio.run(asyncio.wait_for(headset.serve(None, None), timeout=2))
    assert f"at least {headset.MIN_TOKEN_CHARS}" in caplog.text
