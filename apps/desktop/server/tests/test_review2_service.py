import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests
from fastapi.testclient import TestClient

import arty_server
from process import brain, route

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
ARTY_EXTENSION = f"chrome-extension://{arty_server.EXTENSION_ID}"
NONCE = {"nonce": "0" * 32}


@pytest.fixture
def pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(arty_server, "PAIRING_FILE", tmp_path / ".arty_browser_pairing")
    monkeypatch.setattr(arty_server, "_pairing", {})
    return arty_server.pairing_code()


def test_the_extension_origin_alone_no_longer_gets_the_browser_queue(pairing):
    assert client.post("/browser/next?timeout=0", headers={"Origin": ARTY_EXTENSION}).status_code == 403
    assert client.post("/browser/result", headers={"Origin": ARTY_EXTENSION}, json={"id": 0, "result": {}}).status_code == 403
    wrong = {"Origin": ARTY_EXTENSION, "X-Arty-Pairing": "f" * 32}
    assert client.post("/browser/next?timeout=0", headers=wrong).status_code == 403


def test_the_paired_extension_gets_the_browser_queue_and_nothing_else(pairing):
    paired = {"Origin": ARTY_EXTENSION, "X-Arty-Pairing": pairing}
    assert client.post("/browser/next?timeout=0", headers=paired).status_code == 200
    assert client.post("/browser/result", headers=paired, json={"id": 0, "result": {}}).status_code == 200
    assert client.get("/computer", headers=paired).status_code == 403
    assert client.post("/browser/next?timeout=0", headers={"X-Arty-Pairing": pairing}).status_code == 403


def test_the_pairing_code_is_kept_privately_and_shown_only_to_the_app(pairing, tmp_path):
    assert len(pairing) == 32 and int(pairing, 16) >= 0
    saved = tmp_path / ".arty_browser_pairing"
    assert saved.read_text() == pairing
    if sys.platform != "win32":
        assert saved.stat().st_mode & 0o777 == 0o600
    shown = client.get("/browser/pairing", headers={"X-Arty-Token": arty_server.TOKEN})
    assert shown.json() == {"code": pairing}
    assert client.get("/browser/pairing").status_code == 403
    arty_server._pairing.clear()
    assert arty_server.pairing_code() == pairing


def test_the_pairing_header_is_allowed_by_cors(pairing):
    preflight = client.options("/browser/next", headers={
        "Origin": ARTY_EXTENSION, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-arty-pairing"})
    assert "x-arty-pairing" in preflight.headers.get("access-control-allow-headers", "").lower()


def test_web_pages_cannot_use_whoami_to_spot_arty():
    assert client.get("/whoami", params=NONCE).status_code == 200
    assert client.get("/whoami", params=NONCE, headers={"Origin": "null"}).status_code == 403
    assert client.get("/whoami", params=NONCE, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


@pytest.fixture
def home_only(monkeypatch):
    monkeypatch.setattr(route, "LOCAL_URL", "http://192.168.1.13:8081/v1")
    monkeypatch.setattr(route, "REMOTE_URL", "")
    monkeypatch.setattr(route, "_route", {"checked": float("-inf"), "base": route.LOCAL_URL})


def test_a_home_address_that_cannot_prove_itself_gets_nothing_without_a_remote(home_only, monkeypatch):
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: False)
    with pytest.raises(route.UnprovenBrain) as raised:
        route.base()
    assert isinstance(raised.value, requests.ConnectionError)
    assert route.access_headers() == {}


def test_a_home_address_that_proves_itself_is_used(home_only, monkeypatch):
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: True)
    assert route.base() == "http://192.168.1.13:8081/v1"


@pytest.mark.parametrize("address", ["http://127.0.0.1:8081/v1", "http://localhost:8081/v1", "https://arty.home.example/v1"])
def test_loopback_and_https_addresses_need_no_proof(monkeypatch, address):
    monkeypatch.setattr(route, "LOCAL_URL", address)
    monkeypatch.setattr(route, "REMOTE_URL", "")
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: pytest.fail("no need to check"))
    assert route.base() == address


def test_the_reply_says_why_nothing_was_sent(home_only, monkeypatch):
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: False)
    monkeypatch.setattr(brain.requests, "post", lambda *a, **k: pytest.fail("nothing may be sent"))
    with pytest.raises(brain.BrainError) as raised:
        list(brain._stream([{"role": "user", "content": "hi"}], None))
    assert "prove" in str(raised.value)
    assert "192.168.1.13" in brain.describe_endpoint()
