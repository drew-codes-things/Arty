import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import connection, phone_setup

FULL = connection.Connection(
    base_url="http://arty-server:8081/v1", api_key="k-123", remote_url="https://arty.example.com/v1",
    access_client_id="abc.access", access_client_secret="s-456",
)
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


def test_the_code_carries_the_address_from_anywhere_and_every_key():
    assert json.loads(phone_setup.setup_text(FULL)) == {
        "arty": 1, "url": "https://arty.example.com", "key": "k-123", "id": "abc.access", "secret": "s-456",
    }


def test_without_an_address_from_anywhere_the_home_address_is_used_and_no_token_is_sent():
    home_only = connection.Connection(base_url="http://arty-server:8081/v1", api_key="k")
    assert json.loads(phone_setup.setup_text(home_only)) == {"arty": 1, "url": "http://arty-server:8081", "key": "k"}


def test_the_code_is_compact_so_the_qr_stays_easy_to_scan():
    assert " " not in phone_setup.setup_text(FULL)


def test_the_picture_is_an_svg_the_window_can_show_as_an_image():
    assert phone_setup.setup_image("hello").startswith("data:image/svg+xml")


def test_nothing_is_shown_before_this_computer_is_connected(monkeypatch):
    monkeypatch.setattr(phone_setup, "saved_connection", lambda: connection.Connection(base_url="", api_key=""))
    with pytest.raises(phone_setup.NothingToShare):
        phone_setup.phone_code()


def test_only_the_arty_window_can_ask_for_the_code(monkeypatch):
    monkeypatch.setattr(phone_setup, "saved_connection", lambda: FULL)
    assert client.get("/phone-setup-code", headers={"Sec-Fetch-Site": "none"}).status_code == 403
    answer = client.get("/phone-setup-code", headers={"X-Arty-Token": arty_server.TOKEN})
    assert answer.status_code == 200
    assert answer.json()["image"].startswith("data:image/svg+xml")
    assert answer.json()["seconds"] == phone_setup.SHOWN_FOR_SECONDS
    assert "k-123" not in json.dumps({key: value for key, value in answer.json().items() if key != "image"})
