import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import route


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(route, "LOCAL_URL", "http://192.168.1.13:8081/v1")
    monkeypatch.setattr(route, "REMOTE_URL", "https://arty.example.com/v1")
    monkeypatch.setattr(route, "ACCESS_CLIENT_ID", "id")
    monkeypatch.setattr(route, "ACCESS_CLIENT_SECRET", "secret")
    monkeypatch.setattr(route, "_route", {"checked": float("-inf"), "base": route.LOCAL_URL})


def test_home_is_used_when_reachable_with_no_access_headers(remote, monkeypatch):
    monkeypatch.setattr(route, "_local_reachable", lambda: True)
    assert route.base() == "http://192.168.1.13:8081/v1"
    assert route.access_headers() == {}


def test_away_from_home_uses_the_remote_with_access_headers(remote, monkeypatch):
    monkeypatch.setattr(route, "_local_reachable", lambda: False)
    assert route.base() == "https://arty.example.com/v1"
    assert route.access_headers() == {"CF-Access-Client-Id": "id", "CF-Access-Client-Secret": "secret"}


def test_the_choice_is_rechecked_after_a_while(remote, monkeypatch):
    reachable = iter([False, True])
    monkeypatch.setattr(route, "_local_reachable", lambda: next(reachable))
    assert route.base() == route.REMOTE_URL
    assert route.base() == route.REMOTE_URL
    monkeypatch.setitem(route._route, "checked", float("-inf"))
    assert route.base() == route.LOCAL_URL


def test_without_a_remote_it_stays_at_home(monkeypatch):
    monkeypatch.setattr(route, "REMOTE_URL", "")
    monkeypatch.setattr(route, "_local_reachable", lambda: pytest.fail("no need to check"))
    assert route.base() == route.LOCAL_URL
    assert route.access_headers() == {}
