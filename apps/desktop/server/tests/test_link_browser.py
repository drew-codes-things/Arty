import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from conftest import pair_like_starfire
from process import browser, link, link_browser, notifications
from process.tools import computer

WINDOW = {"X-Arty-Token": arty_server.TOKEN}
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
PAGE = "https://www.bbc.co.uk/news"


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def token(tmp_path, monkeypatch):
    clock = Clock()
    store = link.Link(tmp_path / ".arty_link", clock=clock)
    monkeypatch.setattr(arty_server, "starfire_link", store)
    monkeypatch.setattr(arty_server, "link_refusals", link.RefusalPause(clock=clock))
    return pair_like_starfire(store)


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(browser, "connected_names", lambda: ["Chrome", "Firefox"])
    monkeypatch.setattr(browser, "open_tab", lambda url, wanted: calls.append((url, wanted)) or 7)
    return calls


def linked(token):
    return {"X-Arty-Link": token}


def open_body(url=PAGE, name="Firefox"):
    return {"action": "open", "url": url, "browser": name}


def card_and_answer(token, body, ok):
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = threading.Thread(target=lambda: results.append(client.post("/link/browser", json=body, headers=linked(token))),
                              daemon=True)
    thread.start()
    fresh, _last = notifications.since(before, 5)
    asked = [note for note in fresh if note["kind"] == "link_ask"]
    assert asked, "no card was shown"
    client.post("/confirm", json={"id": asked[-1]["ask"], "ok": ok}, headers=WINDOW)
    thread.join(10)
    return asked[-1]["summary"], results[0]


def test_an_allowed_open_shows_the_exact_browser_and_address_then_opens_it(token, opened):
    card, response = card_and_answer(token, open_body(), True)
    assert card == (f"starfire Desktop asks to open a web page in your browser.\nbrowser: Firefox\naddress: {PAGE}\n"
                    f"site: www.bbc.co.uk\n{link.ALLOW_LINE}")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "browser": "Firefox", "tab": 7}
    assert opened == [(PAGE, "Firefox")]


def test_a_declined_open_opens_nothing(token, opened):
    _card, response = card_and_answer(token, open_body(), False)
    assert response.status_code == 403
    assert response.json()["reason"] == "declined"
    assert opened == []


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "chrome://settings",
                                 "https://user:pw@evil.example/", "https://evil.example\\@good.example/",
                                 "https://ex​ample.com/", " https://example.com/", "https://example.com/\nx", "https://",
                                 "https://%65vil.example/"])
def test_addresses_that_cannot_be_shown_or_opened_safely_are_refused_before_any_card(token, opened, url):
    _fresh, before = notifications.since(0, 0)
    response = client.post("/link/browser", json=open_body(url), headers=linked(token))
    assert response.status_code == 400
    assert response.json()["reason"] == "bad_request"
    assert not [note for note in notifications.since(before, 0)[0] if note["kind"] == "link_ask"]
    assert opened == []


def test_a_too_long_address_is_refused_not_cut(token, opened):
    response = client.post("/link/browser", json=open_body(PAGE + "?q=" + "a" * 2000), headers=linked(token))
    assert response.status_code == 413
    assert opened == []


@pytest.mark.parametrize("name, reason", [("Opera", "not_connected"), ("Netscape", "not_connected"), ("", "which_browser"),
                                          ("Fire​fox", "bad_request")])
def test_the_browser_must_be_connected_and_named_plainly(token, opened, name, reason):
    response = client.post("/link/browser", json=open_body(name=name), headers=linked(token))
    assert response.status_code in (400, 409)
    assert response.json()["reason"] == reason
    assert opened == []


def test_with_one_browser_connected_no_name_is_needed(token, opened, monkeypatch):
    monkeypatch.setattr(browser, "connected_names", lambda: ["Chrome"])
    card, response = card_and_answer(token, open_body(name=""), True)
    assert "browser: Chrome" in card
    assert response.json()["browser"] == "Chrome"


def test_no_browser_connected_is_refused(token, opened, monkeypatch):
    monkeypatch.setattr(browser, "connected_names", lambda: [])
    response = client.post("/link/browser", json=open_body(), headers=linked(token))
    assert response.json()["reason"] == "no_browser"


@pytest.mark.parametrize("body", [{"action": "open", "url": PAGE}, {**open_body(), "newTab": False}, {**open_body(), "action": "read"},
                                  {**open_body(), "url": 5}])
def test_only_the_exact_request_shape_is_taken(token, opened, body):
    assert client.post("/link/browser", json=body, headers=linked(token)).status_code == 422
    assert opened == []


def test_computer_control_off_refuses(token, opened, monkeypatch):
    monkeypatch.setattr(computer, "available", lambda: False)
    response = client.post("/link/browser", json=open_body(), headers=linked(token))
    assert response.status_code == 403
    assert response.json()["reason"] == "unavailable"


@pytest.mark.parametrize("headers", [{}, {"X-Arty-Link": "x" * 40}, WINDOW])
def test_only_the_linked_app_can_ask(token, opened, headers):
    assert client.post("/link/browser", json=open_body(), headers=headers).status_code == 403


def test_a_web_page_cannot_ask_even_with_the_token(token, opened):
    headers = {**linked(token), "Origin": "https://evil.example"}
    assert client.post("/link/browser", json=open_body(), headers=headers).status_code == 403


def test_a_second_open_while_one_is_waiting_is_busy(token, opened):
    arty_server._link_computer_busy.acquire()
    try:
        response = client.post("/link/browser", json=open_body(), headers=linked(token))
    finally:
        arty_server._link_computer_busy.release()
    assert response.status_code == 429
    assert response.json()["reason"] == "busy"


def test_the_browser_failing_is_reported_without_page_text(token, opened, monkeypatch):
    def fail(url, wanted):
        raise browser.BrowserError("the browser didn't answer in time")
    monkeypatch.setattr(browser, "open_tab", fail)
    _card, response = card_and_answer(token, open_body(), True)
    assert response.status_code == 502
    assert response.json() == {"error": "Couldn't open it: the browser didn't answer in time.", "reason": "failed"}


def test_status_lists_capabilities(token, opened, monkeypatch):
    monkeypatch.setattr(computer, "can_see", lambda: True)
    monkeypatch.setenv("ARTY_VERSION", "1.6.0")
    answer = client.get("/link/status", headers=linked(token)).json()
    assert answer["ok"] is True and answer["computer"] is True and answer["arty_desktop"] == "1.6.0"
    by_id = {entry["id"]: entry for entry in answer["capabilities"]}
    assert by_id["screen.look"] == {"id": "screen.look", "version": 1, "available": True}
    assert by_id["browser.open"] == {"id": "browser.open", "version": 1, "available": True,
                                     "browsers": [{"name": "Chrome", "state": "connected"}, {"name": "Firefox", "state": "connected"}]}


def test_status_says_nothing_is_available_with_computer_control_off(token, opened, monkeypatch):
    monkeypatch.setattr(computer, "available", lambda: False)
    answer = client.get("/link/status", headers=linked(token)).json()
    assert not any(entry["available"] for entry in answer["capabilities"])
    assert json.dumps(answer).count("Firefox") == 0


@pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f.1/", "http://127.1/", "http://017700000001/"])
def test_numbers_written_as_a_site_name_are_refused(url):
    with pytest.raises(link_browser.OpenRefused):
        link_browser.checked_address(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/link", "http://localhost:8765/", "http://[::1]:8765/",
                                 "http://192.168.1.1/apply.cgi?reboot=1"])
def test_the_card_says_when_the_page_is_on_this_computer_or_the_home_network(url):
    assert link_browser.checked_address(url) == url
    assert link_browser.HOME_NETWORK_LINE in link_browser.card("starfire Desktop", "Firefox", url).splitlines()


def test_an_ordinary_site_has_no_home_network_line():
    assert link_browser.HOME_NETWORK_LINE not in link_browser.card("starfire Desktop", "Firefox", PAGE)


@pytest.mark.parametrize("url, site", [("https://\u0430pple.com/login", "xn--pple-43d.com"),
                                       ("https://google.com\u3002evil.example/", "google.com.evil.example"),
                                       ("http://192.168.1.1/a", "http://192.168.1.1")],
                         ids=["cyrillic letter", "ideographic full stop", "home address"])
def test_the_card_shows_the_real_site_as_the_browser_will_read_it(url, site):
    assert f"site: {site}" in link_browser.card("starfire Desktop", "Firefox", link_browser.checked_address(url)).splitlines()


@pytest.mark.parametrize("url", ["https://accounts.google.com." + "a" * 1900 + ".example/",
                                 "https://" + "a" * 64 + ".example/",
                                 "https://" + ".".join(["a" * 60] * 5) + ".example/"],
                         ids=["very long host", "label over 63", "host over 253"])
def test_a_site_too_long_to_show_on_the_card_is_refused(url):
    with pytest.raises(link_browser.OpenRefused):
        link_browser.checked_address(url)
