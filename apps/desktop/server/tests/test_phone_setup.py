import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import connection, phone_setup, route

FULL = connection.Connection(
    base_url="http://arty-server:8081/v1", api_key="k-123", remote_url="https://arty.example.com/v1",
    access_client_id="abc.access", access_client_secret="s-456",
)
OFFER = {"offer": "offer-id-1", "code": "00112233445566778899aabbccddeeff", "expires_in": 120}
DEVICES = [
    {"id": 3, "name": "Arty CLI on laptop", "address": "192.0.2.4", "created": 1.0, "last_used": None, "remote_control": True, "kind": ""},
    {"id": 7, "name": "Pixel 8", "address": "192.0.2.9", "created": 2.0, "last_used": 3.0, "remote_control": False, "kind": "phone"},
]
WINDOW = {"X-Arty-Token": arty_server.TOKEN}
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeBrain:
    def __init__(self):
        self.calls = []
        self.devices = list(DEVICES)

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url, json, headers))
        path = url.removeprefix("http://arty-server:8081/v1")
        if (method, path) == ("POST", phone_setup.OFFERS_PATH):
            return Response(201, OFFER)
        if method == "GET" and path.startswith(phone_setup.OFFERS_PATH + "/"):
            return Response(200, {"state": "linked", "name": "Pixel 8", "device_id": 7})
        if (method, path) == ("GET", phone_setup.DEVICES_PATH):
            return Response(200, {"devices": self.devices})
        if method == "DELETE":
            removed = int(path.rsplit("/", 1)[1])
            self.devices = [device for device in self.devices if device["id"] != removed]
            return Response(200, {"ok": True})
        return Response(404, {"error": {"message": "not found"}})


@pytest.fixture
def brain(monkeypatch):
    fake = FakeBrain()
    monkeypatch.setattr(phone_setup.requests, "request", fake)
    monkeypatch.setattr(route, "base", lambda: "http://arty-server:8081/v1")
    monkeypatch.setattr(route, "access_headers", lambda: {})
    monkeypatch.setattr(route, "API_KEY", "k-123")
    monkeypatch.setattr(phone_setup, "saved_connection", lambda: FULL)
    return fake


def test_the_code_holds_only_the_addresses_and_a_one_use_offer():
    assert json.loads(phone_setup.setup_text(FULL, OFFER)) == {
        "arty": 2, "url": "https://arty.example.com", "offer": "offer-id-1", "code": "00112233445566778899aabbccddeeff",
        "home": "http://arty-server:8081",
    }


def test_no_long_term_secret_is_in_the_code():
    text = phone_setup.setup_text(FULL, OFFER)
    for secret in (FULL.api_key, FULL.access_client_id, FULL.access_client_secret):
        assert secret not in text


def test_without_an_address_from_anywhere_the_home_address_is_used_and_no_access_pair_goes_to_the_brain():
    home_only = connection.Connection(base_url="http://arty-server:8081/v1", api_key="k", access_client_id="a", access_client_secret="b")
    linked_at_home = json.loads(phone_setup.setup_text(home_only, OFFER))
    assert linked_at_home["url"] == "http://arty-server:8081" and "home" not in linked_at_home
    assert phone_setup.access_pair(home_only) == {}


def test_the_code_is_compact_so_the_qr_stays_easy_to_scan():
    assert " " not in phone_setup.setup_text(FULL, OFFER)


def test_the_picture_is_an_svg_the_window_can_show_as_an_image():
    assert phone_setup.setup_image("hello").startswith("data:image/svg+xml")


def test_nothing_is_shown_before_this_computer_is_connected(monkeypatch):
    monkeypatch.setattr(phone_setup, "saved_connection", lambda: connection.Connection(base_url="", api_key=""))
    with pytest.raises(phone_setup.NothingToShare):
        phone_setup.phone_code()


def test_the_brain_makes_the_offer_and_keeps_the_access_pair_for_the_phone(brain):
    made = phone_setup.phone_code()
    method, url, body, headers = brain.calls[0]
    assert (method, url) == ("POST", "http://arty-server:8081/v1/phone-link/offers")
    assert body == {"access": {"id": "abc.access", "secret": "s-456"}}
    assert headers["Authorization"] == "Bearer k-123"
    assert made["offer"] == "offer-id-1" and made["seconds"] == 120


def test_only_the_arty_window_can_ask_for_the_code(brain):
    assert client.get("/phone-setup-code", headers={"Sec-Fetch-Site": "none"}).status_code == 403
    answer = client.get("/phone-setup-code", headers=WINDOW)
    assert answer.status_code == 200
    assert answer.json()["image"].startswith("data:image/svg+xml")
    assert "k-123" not in json.dumps(answer.json())
    assert "s-456" not in json.dumps({key: value for key, value in answer.json().items() if key != "image"})


def test_the_window_follows_the_offer_and_sees_the_phone_that_linked(brain):
    assert client.get("/phone-link/offer-id-1").status_code == 403
    assert client.get("/phone-link/offer-id-1", headers=WINDOW).json() == {"state": "linked", "name": "Pixel 8", "device_id": 7}


def test_linked_phones_lists_only_phones(brain):
    assert client.get("/linked-phones").status_code == 403
    assert client.get("/linked-phones", headers=WINDOW).json() == {
        "phones": [{"id": 7, "name": "Pixel 8", "created": 2.0, "last_used": 3.0}]}


def test_removing_a_phone_cuts_it_off_and_cannot_remove_other_devices(brain):
    assert client.post("/linked-phones/remove", json={"id": 7}).status_code == 403
    refused = client.post("/linked-phones/remove", json={"id": 3}, headers=WINDOW)
    assert refused.status_code == 502
    assert all(call[0] != "DELETE" for call in brain.calls)
    removed = client.post("/linked-phones/remove", json={"id": 7}, headers=WINDOW)
    assert removed.json() == {"phones": []}
    assert ("DELETE", "http://arty-server:8081/v1/devices/7") in [(call[0], call[1]) for call in brain.calls]


def test_a_brain_that_cannot_be_reached_is_a_plain_message(brain, monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(phone_setup.requests, "request", unreachable)
    answer = client.get("/phone-setup-code", headers=WINDOW)
    assert answer.status_code == 502
    assert "Couldn't reach Arty's brain" in answer.json()["error"]


def test_a_brain_refusal_is_passed_on(monkeypatch, brain):
    monkeypatch.setattr(phone_setup.requests, "request",
                        lambda *a, **k: Response(403, {"error": {"message": "only Arty's own key can make a phone link code"}}))
    assert client.get("/phone-setup-code", headers=WINDOW).json()["error"] == "only Arty's own key can make a phone link code"
    monkeypatch.setattr(phone_setup.requests, "request", lambda *a, **k: Response(500, ValueError("not json")))
    assert client.get("/linked-phones", headers=WINDOW).json()["error"] == "Arty's brain answered 500."
