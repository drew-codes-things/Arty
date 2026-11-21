import stat
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from conftest import pair_like_starfire
from process import confirm, link, notifications, spake2
from process.tools import computer, registry

WINDOW = {"X-Arty-Token": arty_server.TOKEN}
APP_NAME = "starfire Desktop"
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def default_computer_settings(monkeypatch):
    monkeypatch.setattr(computer, "setting", lambda section, key, default=None: default)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return link.Link(tmp_path / ".arty_link", clock=clock)


@pytest.fixture
def served(store, monkeypatch):
    monkeypatch.setattr(arty_server, "starfire_link", store)
    monkeypatch.setattr(arty_server, "link_refusals", link.RefusalPause())
    return store


def paired_token(store):
    return pair_like_starfire(store, name=APP_NAME)


def linked(token):
    return {"X-Arty-Link": token}


def test_a_code_is_six_digits_and_lives_two_minutes(store):
    started = store.start_pairing()
    assert len(started["code"]) == link.CODE_DIGITS and started["code"].isdigit()
    assert started["expires_in"] == link.CODE_LIFETIME_SECONDS == 120


def test_the_right_code_pairs_and_gives_a_token(store):
    token = paired_token(store)
    assert store.verify(token)
    assert store.status()["name"] == APP_NAME


def test_a_code_works_once(store):
    code = store.start_pairing()["code"]
    pair_like_starfire(store, code=code)
    with pytest.raises(link.PairingRefused, match="No code"):
        pair_like_starfire(store, code=code)


def test_a_code_expires(store, clock):
    code = store.start_pairing()["code"]
    clock.now += link.CODE_LIFETIME_SECONDS + 1
    with pytest.raises(link.PairingRefused, match="expired"):
        pair_like_starfire(store, code=code)


def test_a_code_still_works_just_before_it_expires(store, clock):
    code = store.start_pairing()["code"]
    clock.now += link.CODE_LIFETIME_SECONDS - 1
    assert store.verify(pair_like_starfire(store, code=code))


def test_five_wrong_guesses_burn_the_code(store):
    code = store.start_pairing()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(link.MAX_WRONG_CODES):
        with pytest.raises(AssertionError, match="did not prove"):
            pair_like_starfire(store, code=wrong)
    with pytest.raises(link.PairingRefused, match="new code"):
        pair_like_starfire(store, code=code)


def test_without_a_code_started_nothing_pairs(store):
    with pytest.raises(link.PairingRefused, match="No code"):
        pair_like_starfire(store, code="123456")


def test_a_new_pairing_replaces_the_old_link(store):
    first = paired_token(store)
    second = paired_token(store)
    assert not store.verify(first)
    assert store.verify(second)


def test_unlinking_forgets_the_token(store):
    token = paired_token(store)
    store.unlink()
    assert not store.verify(token)
    assert store.status() == {"linked": False}


def test_the_link_survives_a_restart(store, tmp_path):
    token = paired_token(store)
    assert link.Link(tmp_path / ".arty_link").verify(token)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix permission bits")
def test_the_file_is_private_and_holds_no_usable_token(store, tmp_path):
    token = paired_token(store)
    path = tmp_path / ".arty_link"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text()


@pytest.mark.parametrize("bad", ["", "nope", "café", "x" * 5000])
def test_a_wrong_token_does_not_verify(store, bad):
    paired_token(store)
    assert not store.verify(bad)


def test_nothing_verifies_before_anything_is_paired(store):
    assert not store.verify("anything")


def test_a_damaged_link_file_means_not_linked(tmp_path):
    path = tmp_path / ".arty_link"
    path.write_text("{not json")
    assert link.Link(path).status() == {"linked": False}


def test_a_name_is_shortened_and_cleaned(store):
    pair_like_starfire(store, name="  evil\nname " + "x" * 200)
    shown = store.status()["name"]
    assert "\n" not in shown and len(shown) <= link.NAME_CHARS


def test_only_the_arty_window_can_start_a_pairing(served):
    assert client.post("/link/pairing/start").status_code == 403
    assert client.post("/link/pairing/start", headers=WINDOW).status_code == 200
    token = paired_token(served)
    assert client.post("/link/pairing/start", headers=linked(token)).status_code == 403


def test_the_window_gets_a_code_to_show(served):
    answer = client.post("/link/pairing/start", headers=WINDOW).json()
    assert len(answer["code"]) == 6 and answer["expires_in"] == 120


def pairing_start_body(code):
    return {"name": APP_NAME, "share": spake2.share_hex(spake2.new_secret(), code, spake2.APP)}


def test_the_app_and_arty_pair_once_through_the_open_routes(served):
    code = client.post("/link/pairing/start", headers=WINDOW).json()["code"]
    assert client.post("/link/pair/start", json=pairing_start_body(code)).status_code == 200
    paired_token(served)
    assert client.post("/link/pair/start", json=pairing_start_body(code)).status_code == 403


def test_a_pairing_message_that_is_not_a_share_is_refused_with_a_reason(served):
    client.post("/link/pairing/start", headers=WINDOW)
    answer = client.post("/link/pair/start", json={"name": APP_NAME, "share": "abcdef"})
    assert answer.status_code == 400 and answer.json()["error"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "none"},
    {"Host": "evil.example:8765"},
    {"Host": "192.168.77.5:8765"},
])
def test_a_web_page_cannot_pair(served, headers):
    code = served.start_pairing()["code"]
    answer = client.post("/link/pair/start", json=pairing_start_body(code), headers=headers)
    assert answer.status_code == 403
    assert not served.status()["linked"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Host": "evil.example:8765"},
])
def test_a_web_page_cannot_use_the_link_even_with_the_token(served, headers):
    token = paired_token(served)
    assert client.get("/link/status", headers={**linked(token), **headers}).status_code == 403
    body = {"tool": "computer_apps", "arguments": {}}
    assert client.post("/link/computer", json=body, headers={**linked(token), **headers}).status_code == 403


def test_the_link_token_opens_only_the_link_routes(served):
    token = paired_token(served)
    assert client.get("/link/status", headers=linked(token)).status_code == 200
    for path in ("/computer", "/history", "/memory", "/models", "/health", "/connection", "/phone-setup-code", "/browser/pairing"):
        assert client.get(path, headers=linked(token)).status_code == 403, path
    assert client.post("/chat/stream", json={"text": "hi"}, headers=linked(token)).status_code == 403
    assert client.post("/confirm", json={"id": "x", "ok": True}, headers=linked(token)).status_code == 403


def test_without_a_token_the_link_routes_refuse(served):
    paired_token(served)
    assert client.get("/link/status").status_code == 403
    assert client.get("/link/status", headers=linked("guess")).status_code == 403
    assert client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}).status_code == 403


def test_the_window_token_is_not_a_link_token(served):
    paired_token(served)
    assert client.get("/link/status", headers=WINDOW).status_code == 403
    assert client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=WINDOW).status_code == 403


def test_the_window_asks_who_is_linked(served):
    assert client.get("/link", headers=WINDOW).json()["linked"] is False
    paired_token(served)
    assert client.get("/link", headers=WINDOW).json()["name"] == APP_NAME
    assert client.get("/link").status_code == 403


def test_either_side_can_unlink(served):
    token = paired_token(served)
    assert client.post("/link/unlink", headers=linked(token)).status_code == 200
    assert not served.status()["linked"]
    paired_token(served)
    assert client.post("/link/unlink", headers=WINDOW).status_code == 200
    assert not served.status()["linked"]
    assert client.post("/link/unlink").status_code == 403


def test_status_reports_whether_computer_control_is_on(served, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(computer, "available", lambda: False)
    off = client.get("/link/status", headers=linked(token)).json()
    assert (off["ok"], off["computer"]) == (True, False)
    monkeypatch.setattr(computer, "available", lambda: True)
    on = client.get("/link/status", headers=linked(token)).json()
    assert (on["ok"], on["computer"]) == (True, True)
    assert {entry["id"] for entry in on["capabilities"]} >= {"screen.look", "browser.open"}


@pytest.fixture
def computer_on(monkeypatch):
    ran = []
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(registry, "run", lambda name, arguments: ran.append((name, arguments)) or f"{name} done")
    return ran


def ask_in_thread(token, body, results):
    def post():
        results.append(client.post("/link/computer", json=body, headers=linked(token)))
    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    return thread


def waiting_question(after):
    fresh, _last = notifications.since(after, 5)
    asked = [note for note in fresh if note["kind"] == "link_ask"]
    assert asked, "Arty Desktop never showed the question"
    return asked[-1]


def answer(ask_id, ok):
    return client.post("/confirm", json={"id": ask_id, "ok": ok}, headers=WINDOW)


def test_computer_control_off_refuses_before_asking(served, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(computer, "available", lambda: False)
    ran = []
    monkeypatch.setattr(registry, "run", lambda *a: ran.append(a))
    answer_ = client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
    assert answer_.status_code == 403 and "turned off" in answer_.json()["error"]
    assert ran == []


@pytest.mark.parametrize("tool", ["rm", "chrome_click", "read_clipboard", "open_url", "desktop_notifications", ""])
def test_only_the_computer_tools_can_be_asked_for(served, computer_on, tool):
    token = paired_token(served)
    answer_ = client.post("/link/computer", json={"tool": tool, "arguments": {}}, headers=linked(token))
    assert answer_.status_code == 400
    assert computer_on == []


def test_arguments_must_be_an_object_and_not_huge(served, computer_on):
    token = paired_token(served)
    assert client.post("/link/computer", json={"tool": "computer_apps", "arguments": [1]}, headers=linked(token)).status_code == 422
    huge = {"text": "x" * (link.MAX_ARGUMENT_CHARS + 1)}
    assert client.post("/link/computer", json={"tool": "computer_type", "arguments": huge}, headers=linked(token)).status_code == 413
    assert computer_on == []


def test_the_user_is_asked_even_for_a_step_arty_would_do_unasked(served, computer_on):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"tool": "computer_apps", "arguments": {}}, results)
    note = waiting_question(before)
    assert note["summary"].startswith(f"{APP_NAME} asks to do this on your computer: list the open windows (computer_apps).")
    assert computer_on == []
    assert answer(note["ask"], True).json() == {"ok": True}
    thread.join(10)
    assert results[0].json() == {"ok": True, "result": "computer_apps done"}
    assert computer_on == [("computer_apps", "{}")]


def test_a_no_runs_nothing(served, computer_on):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"tool": "computer_apps", "arguments": {}}, results)
    answer(waiting_question(before)["ask"], False)
    thread.join(10)
    assert results[0].status_code == 403
    assert results[0].json()["declined"] is True
    assert computer_on == []


def test_no_answer_in_time_counts_as_no(served, computer_on, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(arty_server, "LINK_ASK_SECONDS", 0.2)
    answer_ = client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
    assert answer_.status_code == 403 and answer_.json()["declined"] is False
    assert computer_on == []


def test_arty_own_question_is_part_of_what_is_shown(served, computer_on):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    body = {"tool": "computer_run_command", "arguments": {"command": "rm -rf /tmp/starfire-link-probe"}}
    thread = ask_in_thread(token, body, results)
    note = waiting_question(before)
    assert "command: rm -rf /tmp/starfire-link-probe" in note["summary"].split("\n")
    assert link.OWN_RULES_LINE in note["summary"]
    answer(note["ask"], False)
    thread.join(10)
    assert computer_on == []


def test_the_mouse_and_keyboard_question_is_asked_every_time(served, computer_on):
    token = paired_token(served)
    for _ in range(2):
        _fresh, before = notifications.since(0, 0)
        results = []
        thread = ask_in_thread(token, {"tool": "computer_key", "arguments": {"keys": "ctrl+s"}}, results)
        note = waiting_question(before)
        assert "keys: ctrl+s" in note["summary"] and link.OWN_RULES_LINE in note["summary"]
        answer(note["ask"], True)
        thread.join(10)
    assert [name for name, _ in computer_on] == ["computer_key", "computer_key"]


def test_a_second_step_while_one_waits_is_turned_away(served, computer_on):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"tool": "computer_apps", "arguments": {}}, results)
    note = waiting_question(before)
    second = client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
    assert second.status_code == 429
    answer(note["ask"], False)
    thread.join(10)


def test_stopping_arty_answers_a_waiting_link_question_with_no(served, computer_on):
    token = paired_token(served)
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"tool": "computer_apps", "arguments": {}}, results)
    waiting_question(before)
    assert client.post("/interrupt", json={"chunks": 0}, headers=WINDOW).status_code == 200
    thread.join(10)
    assert results[0].status_code == 403
    assert computer_on == []


def test_a_failing_tool_is_reported_not_raised(served, monkeypatch):
    token = paired_token(served)
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(registry, "run", lambda name, arguments: "computer_apps failed: OSError: no screen")
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread(token, {"tool": "computer_apps", "arguments": {}}, results)
    answer(waiting_question(before)["ask"], True)
    thread.join(10)
    assert results[0].status_code == 200 and "no screen" in results[0].json()["result"]
