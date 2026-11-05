"""Only Arty's own callers may use the local service: it can control the computer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


def status(**headers):
    return client.get("/computer", headers=headers).status_code


def test_the_arty_window_and_local_scripts_get_in_with_the_token():
    assert status(**{"X-Arty-Token": arty_server.TOKEN}) == 200


def test_the_chrome_extension_gets_in_by_how_chrome_marks_it():
    assert status(**{"Sec-Fetch-Site": "none"}) == 200
    assert status(Origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop") == 200


def test_web_pages_are_refused_however_they_try():
    assert status() == 403                                           # no token at all
    assert status(**{"X-Arty-Token": "guess"}) == 403
    assert status(Origin="null", **{"Sec-Fetch-Site": "cross-site"}) == 403   # sandboxed frame
    assert status(**{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}) == 403  # <img> tag
    assert status(Origin="https://evil.example") == 403


def test_dns_rebinding_is_refused_even_with_the_token():
    response = client.get("/computer", headers={"Host": "evil.example:8765", "X-Arty-Token": arty_server.TOKEN})
    assert response.status_code == 403
