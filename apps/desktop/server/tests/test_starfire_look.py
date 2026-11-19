import base64
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from conftest import pair_like_starfire
from process import link, notifications
from process.tools import computer, session

WINDOW = {"X-Arty-Token": arty_server.TOKEN}
APP_NAME = "starfire Desktop"
PICTURE = base64.b64encode(b"\xff\xd8\xff\xe0not really a picture").decode()
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


@pytest.fixture(autouse=True)
def default_computer_settings(monkeypatch):
    monkeypatch.setattr(computer, "setting", lambda section, key, default=None: default)


@pytest.fixture
def served(tmp_path, monkeypatch):
    store = link.Link(tmp_path / ".arty_link")
    monkeypatch.setattr(arty_server, "starfire_link", store)
    monkeypatch.setattr(arty_server, "link_refusals", link.RefusalPause())
    return store


@pytest.fixture
def screen(monkeypatch):
    taken = []
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(computer, "can_see", lambda: True)
    monkeypatch.setattr(computer, "can_picture_one_window", lambda: False)
    monkeypatch.setattr(computer, "screenshot", lambda: taken.append(1) or (PICTURE, 1920, 1080))
    monkeypatch.setattr(session, "window_listing", lambda: ({"discord": ["#general - Discord"], "kate": ["notes.txt - Kate"]}, True))
    monkeypatch.setattr(session, "front_window", lambda: ("discord", "#general - Discord"))
    return taken


def paired_token(store):
    return pair_like_starfire(store, name=APP_NAME)


def linked(token):
    return {"X-Arty-Link": token}


def ask_in_thread(token, body, results):
    def post():
        results.append(client.post("/link/screenshot", json=body, headers=linked(token)))
    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    return thread


def waiting_question(after):
    fresh, _last = notifications.since(after, 5)
    asked = [note for note in fresh if note["kind"] == "link_ask"]
    assert asked, "Arty Desktop never showed the question"
    return asked[-1]


def allow(token, body, ok=True):
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, body, results)
    note = waiting_question(before)
    client.post("/confirm", json={"id": note["ask"], "ok": ok}, headers=WINDOW)
    thread.join(10)
    return results[0], note


def test_the_whole_screen_when_no_app_is_named(screen):
    picture = computer.window_picture("")
    assert picture["image"] == PICTURE and picture["mime"] == "image/jpeg"
    assert picture["target"] == "your whole screen" and picture["window"] == ""
    assert screen == [1]


def test_a_named_app_that_is_in_front(screen):
    picture = computer.window_picture("Discord")
    assert picture["window"] == "#general - Discord"
    assert "Discord is in front" in picture["note"]


def test_a_named_app_that_is_open_but_not_in_front(screen, monkeypatch):
    monkeypatch.setattr(session, "front_window", lambda: ("kate", "notes.txt - Kate"))
    note = computer.window_picture("Discord")["note"]
    assert "not in front" in note and "cover" in note


def test_a_desktop_that_will_not_say_what_is_in_front_is_admitted(screen, monkeypatch):
    monkeypatch.setattr(session, "front_window", lambda: None)
    note = computer.window_picture("discord")["note"]
    assert "does not say" in note


def test_an_app_with_no_window_is_not_found_and_nothing_is_captured(screen):
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Slack")
    assert refused.value.reason == "not_found"
    assert "no window" in str(refused.value).lower() and "Slack" in str(refused.value)
    assert screen == []


def test_a_desktop_that_cannot_list_every_window_says_not_found_in_what_it_can_list(screen, monkeypatch):
    monkeypatch.setattr(session, "window_listing", lambda: ({"kate": ["notes.txt - Kate"]}, False))
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Discord")
    assert refused.value.reason == "not_found"
    assert "cannot list every window" in str(refused.value)
    assert screen == []


def test_a_password_manager_is_never_looked_at(screen, monkeypatch):
    with pytest.raises(computer.CannotLook) as named:
        computer.window_picture("KeePassXC")
    assert named.value.reason == "off_limits"
    monkeypatch.setattr(session, "front_window", lambda: ("keepassxc", "Passwords"))
    with pytest.raises(computer.CannotLook) as in_front:
        computer.window_picture("")
    assert in_front.value.reason == "off_limits"
    assert screen == []


def test_no_screenshot_tool_is_reported(screen, monkeypatch):
    monkeypatch.setattr(computer, "can_see", lambda: False)
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("")
    assert refused.value.reason == "cannot_see"
    assert screen == []


def test_a_failing_capture_is_reported_not_raised_bare(screen, monkeypatch):
    def broken():
        raise RuntimeError("spectacle did not answer")
    monkeypatch.setattr(computer, "screenshot", broken)
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("")
    assert refused.value.reason == "failed" and "spectacle did not answer" in str(refused.value)


def test_a_very_long_app_name_is_refused(screen):
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("x" * (computer.MAX_LOOK_APP_CHARS + 1))
    assert refused.value.reason == "bad_request"


def test_the_link_token_is_needed_for_a_picture(served, screen):
    assert client.post("/link/screenshot", json={"app": ""}).status_code == 403
    assert client.post("/link/screenshot", json={"app": ""}, headers=WINDOW).status_code == 403
    token = paired_token(served)
    assert client.post("/link/screenshot", json={"app": ""}, headers=linked("guess")).status_code == 403
    assert screen == []


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Host": "evil.example:8765"},
])
def test_a_web_page_cannot_ask_for_a_picture_even_with_the_token(served, screen, headers):
    token = paired_token(served)
    answer = client.post("/link/screenshot", json={"app": ""}, headers={**linked(token), **headers})
    assert answer.status_code == 403
    assert screen == []


def test_computer_control_off_refuses_before_asking(served, screen, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(computer, "available", lambda: False)
    answer = client.post("/link/screenshot", json={"app": ""}, headers=linked(token))
    assert answer.status_code == 403 and "turned off" in answer.json()["error"]
    assert screen == []


def test_the_card_shows_the_whole_step_exactly(served, screen):
    token = paired_token(served)
    answer, note = allow(token, {"app": "Discord"})
    assert note["summary"] == (f'{APP_NAME} wants a screenshot of your whole screen, to look for the window '
                               '"Discord". This desktop cannot show Arty where that window is, so the whole screen '
                               'would be sent. Allow it?')
    assert answer.status_code == 200
    body = answer.json()
    assert body["ok"] is True and body["image"] == PICTURE and body["window"] == "#general - Discord"


def test_no_app_named_asks_about_the_screen(served, screen):
    token = paired_token(served)
    answer, note = allow(token, {})
    assert note["summary"] == f"{APP_NAME} wants a screenshot of your whole screen. Allow it?"
    assert answer.json()["target"] == "your whole screen"


@pytest.mark.parametrize("app", ["Dis\u202ecord", "Dis\ncord", "Dis\x00cord", "Dis\u200bcord", "Dis\u2028cord", "Dis\u2066cord"])
def test_control_and_direction_characters_in_the_app_are_refused_before_asking(served, screen, app):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    answer = client.post("/link/screenshot", json={"app": app}, headers=linked(token))
    assert answer.status_code == 400 and answer.json()["reason"] == "bad_request"
    assert [n for n in notifications.since(before, 0)[0] if n["kind"] == "link_ask"] == []
    assert screen == []


def test_the_linked_apps_name_cannot_hide_what_the_card_says(served, screen):
    token = pair_like_starfire(served, name="Evil\u202e name")
    answer, note = allow(token, {"app": "Discord"})
    assert "\u202e" not in note["summary"] and note["summary"].startswith("Evil name wants a screenshot")


@pytest.mark.parametrize("extra", [{"tool": "computer_run_command"}, {"command": "id"}, {"arguments": {}}, {"path": "/etc/passwd"}])
def test_a_picture_request_cannot_carry_anything_but_the_app(served, screen, extra):
    token = paired_token(served)
    answer = client.post("/link/screenshot", json={"app": "", **extra}, headers=linked(token))
    assert answer.status_code == 422
    assert screen == []


def test_a_no_takes_no_picture(served, screen):
    token = paired_token(served)
    answer, _note = allow(token, {"app": "Discord"}, ok=False)
    assert answer.status_code == 403 and answer.json()["declined"] is True and "image" not in answer.json()
    assert screen == []


def test_no_answer_in_time_counts_as_no(served, screen, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(arty_server, "LINK_ASK_SECONDS", 0.2)
    answer = client.post("/link/screenshot", json={"app": "Discord"}, headers=linked(token))
    assert answer.status_code == 403 and answer.json()["declined"] is False
    assert screen == []


def test_an_app_with_no_window_is_a_404_after_the_user_allowed_it(served, screen):
    token = paired_token(served)
    answer, _note = allow(token, {"app": "Slack"})
    assert answer.status_code == 404 and answer.json()["reason"] == "not_found"
    assert screen == []


def test_a_desktop_that_cannot_capture_says_so(served, screen, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(computer, "can_see", lambda: False)
    answer, _note = allow(token, {"app": ""})
    assert answer.status_code == 503 and answer.json()["reason"] == "cannot_see"


def test_a_second_picture_while_one_waits_is_turned_away(served, screen):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"app": ""}, results)
    note = waiting_question(before)
    second = client.post("/link/screenshot", json={"app": ""}, headers=linked(token))
    assert second.status_code == 429
    client.post("/confirm", json={"id": note["ask"], "ok": False}, headers=WINDOW)
    thread.join(10)


def test_the_app_must_be_text_and_not_huge(served, screen):
    token = paired_token(served)
    assert client.post("/link/screenshot", json={"app": 5}, headers=linked(token)).status_code == 422
    huge = {"app": "x" * (computer.MAX_LOOK_APP_CHARS + 1)}
    assert client.post("/link/screenshot", json=huge, headers=linked(token)).status_code == 400
    assert screen == []


def test_the_window_listing_shares_one_reader_with_the_front_window_logic(monkeypatch):
    monkeypatch.setattr(session, "kind", lambda: "wayland")
    monkeypatch.setenv("DISPLAY", ":1")
    seen = []
    monkeypatch.setattr(session, "_x11_client_titles", lambda: seen.append(1) or {"discord": ["Discord"]})
    assert session.window_listing() == ({"discord": ["Discord"]}, False)
    monkeypatch.setattr(session, "kind", lambda: "x11")
    assert session.window_listing() == ({"discord": ["Discord"]}, True)
    monkeypatch.setattr(session, "kind", lambda: "wayland")
    monkeypatch.delenv("DISPLAY")
    assert session.window_listing() == ({}, False)
