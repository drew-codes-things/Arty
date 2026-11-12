import hashlib
import hmac
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import route


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(route, "LOCAL_URL", "http://192.0.2.13:8081/v1")
    monkeypatch.setattr(route, "REMOTE_URL", "https://arty.example.com/v1")
    monkeypatch.setattr(route, "ACCESS_CLIENT_ID", "id")
    monkeypatch.setattr(route, "ACCESS_CLIENT_SECRET", "secret")
    monkeypatch.setattr(route, "_route", {"checked": float("-inf"), "base": route.LOCAL_URL})


def test_home_is_used_when_reachable_with_no_access_headers(remote, monkeypatch):
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: True)
    assert route.base() == "http://192.0.2.13:8081/v1"
    assert route.access_headers() == {}


def test_away_from_home_uses_the_remote_with_access_headers(remote, monkeypatch):
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: False)
    assert route.base() == "https://arty.example.com/v1"
    assert route.access_headers() == {"CF-Access-Client-Id": "id", "CF-Access-Client-Secret": "secret"}


def test_the_choice_is_rechecked_after_a_while(remote, monkeypatch):
    reachable = iter([False, True])
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: next(reachable))
    assert route.base() == route.REMOTE_URL
    assert route.base() == route.REMOTE_URL
    monkeypatch.setitem(route._route, "checked", float("-inf"))
    assert route.base() == route.LOCAL_URL


def test_without_a_remote_it_stays_at_home(monkeypatch):
    monkeypatch.setattr(route, "LOCAL_URL", "http://127.0.0.1:8081/v1")
    monkeypatch.setattr(route, "REMOTE_URL", "")
    monkeypatch.setattr(route, "_home_brain_proves_itself", lambda: pytest.fail("no need to check"))
    assert route.base() == route.LOCAL_URL
    assert route.access_headers() == {}


class Answer:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def proof(key, nonce):
    return hmac.new(key.encode(), f"arty-identity:{nonce}".encode(), hashlib.sha256).hexdigest()


def answering(monkeypatch, make_answer):
    asked = []

    def get(url, params=None, timeout=None, headers=None, **kwargs):
        asked.append({"url": url, "params": params, "headers": headers or {}})
        return make_answer(params["nonce"])
    monkeypatch.setattr(route.requests, "get", get)
    monkeypatch.setattr(route, "API_KEY", "the-key")
    return asked


def test_home_is_used_only_when_the_brain_there_proves_it_holds_our_key(remote, monkeypatch):
    asked = answering(monkeypatch, lambda nonce: Answer(200, {"proofs": ["other", proof("the-key", nonce)]}))
    assert route.base() == route.LOCAL_URL
    assert asked[0]["url"] == "http://192.0.2.13:8081/v1/identity"
    assert len(asked[0]["params"]["nonce"]) == 32
    assert "Authorization" not in asked[0]["headers"]


def test_a_device_key_is_proven_by_the_brain_keying_its_proof_with_the_keys_sha256(remote, monkeypatch):
    hashed = hashlib.sha256(b"the-key").hexdigest()
    answering(monkeypatch, lambda nonce: Answer(200, {"proofs": ["other", proof(hashed, nonce)]}))
    assert route.base() == route.LOCAL_URL


def test_with_no_key_set_no_proof_is_ever_accepted(remote, monkeypatch):
    answering(monkeypatch, lambda nonce: Answer(200, {"proofs": [proof("", nonce), proof(hashlib.sha256(b"").hexdigest(), nonce)]}))
    monkeypatch.setattr(route, "API_KEY", "")
    assert route.base() == route.REMOTE_URL


@pytest.mark.parametrize("make_answer", [
    lambda nonce: Answer(200, {"proofs": [proof("someone-elses-key", nonce)]}),
    lambda nonce: Answer(200, {"proofs": [proof("the-key", "an-old-nonce")]}),
    lambda nonce: Answer(404, {"error": "not found"}),
    lambda nonce: Answer(200, ValueError("not json")),
    lambda nonce: Answer(200, {"proofs": "nope"}),
    lambda nonce: (_ for _ in ()).throw(requests.ConnectionError("no route")),
])
def test_anything_else_answering_at_the_home_address_gets_the_remote_route(remote, monkeypatch, make_answer):
    answering(monkeypatch, make_answer)
    assert route.base() == route.REMOTE_URL
