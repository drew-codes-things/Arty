import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import link, notifications, spake2

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
WINDOW = {"X-Arty-Token": arty_server.TOKEN}


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path, monkeypatch):
    clock = Clock()
    linked = link.Link(tmp_path / ".arty_link", offer_path=tmp_path / link.LOCAL_OFFER_NAME, clock=clock)
    linked.clock = clock
    monkeypatch.setattr(arty_server, "starfire_link", linked)
    monkeypatch.setattr(arty_server, "link_refusals", link.RefusalPause(clock=clock))
    return linked


def offered_code(store):
    return store.offer_path.read_text(encoding="utf-8")


def pair_locally(code, name="starfire Desktop", http=False):
    secret = spake2.new_secret()
    app_share = spake2.share_hex(secret, code, spake2.APP)
    start = {"name": name, "share": app_share, "local": True}
    answer = client.post("/link/pair/start", json=start).json() if http else arty_server.starfire_link.begin_pair(name, app_share, local=True)
    key = spake2.shared_key(secret, code, spake2.APP, app_share, answer["share"])
    if answer.get("proof") != spake2.proof(key, spake2.ARTY):
        raise link.PairingRefused("Arty Desktop did not prove it knows the same secret")
    finish = {"session": answer["session"], "proof": spake2.proof(key, spake2.APP), "local": True}
    if http:
        assert client.post("/link/pair/finish", json=finish).status_code == 200
    else:
        arty_server.starfire_link.finish_pair(finish["session"], finish["proof"], local=True)
    return spake2.link_token(key)


def test_the_offer_is_a_long_secret_only_this_user_can_read(store):
    assert store.offer_locally() is True
    assert re.fullmatch(r"[0-9a-f]{32}", offered_code(store))
    if sys.platform != "win32":
        assert stat.S_IMODE(store.offer_path.stat().st_mode) == 0o600


def test_an_app_that_can_read_the_offer_links_by_itself_and_the_offer_is_used_up(store):
    store.offer_locally()
    token = pair_locally(offered_code(store), http=True)
    assert store.verify(token)
    assert store.status() == {"linked": True, "name": "starfire Desktop", "since": store.status()["since"], "automatic": True}
    assert not store.offer_path.exists()
    with pytest.raises(link.PairingRefused):
        pair_locally("0" * 32)


def test_linking_by_itself_is_announced_in_the_arty_window(store):
    store.offer_locally()
    _fresh, before = notifications.since(0, 0)
    pair_locally(offered_code(store), http=True)
    fresh, _last = notifications.since(before, 0)
    assert [note["summary"] for note in fresh if note["kind"] == "linked"] == [
        "starfire Desktop linked to Arty Desktop by itself (same user on this computer)."]


def test_wrong_secrets_are_refused_without_changing_the_offer(store):
    store.offer_locally()
    first = offered_code(store)
    for _try in range(link.MAX_WRONG_CODES + 2):
        with pytest.raises(link.PairingRefused):
            pair_locally("f" * 32)
    assert offered_code(store) == first
    assert store.verify(pair_locally(first))


def start_locally(store, code, name="starfire Desktop"):
    secret = spake2.new_secret()
    app_share = spake2.share_hex(secret, code, spake2.APP)
    answer = store.begin_pair(name, app_share, local=True)
    key = spake2.shared_key(secret, code, spake2.APP, app_share, answer["share"])
    return answer["session"], spake2.proof(key, spake2.APP), spake2.link_token(key)


def test_strangers_starting_meanwhile_do_not_break_a_pairing_in_progress(store):
    store.offer_locally()
    session, proof, token = start_locally(store, offered_code(store))
    for _stranger in range(link.MAX_LOCAL_SESSIONS - 1):
        start_locally(store, "0" * 32, name="stranger")
    store.finish_pair(session, proof, local=True)
    assert store.verify(token)


def test_a_local_pairing_left_unfinished_runs_out(store):
    store.offer_locally()
    session, proof, _token = start_locally(store, offered_code(store))
    store.clock.now += link.LOCAL_SESSION_SECONDS + 1
    with pytest.raises(link.PairingRefused):
        store.finish_pair(session, proof, local=True)


def test_the_six_digit_code_and_the_local_offer_are_kept_apart(store):
    store.offer_locally()
    with pytest.raises(link.PairingRefused):
        store.begin_pair("x", spake2.share_hex(spake2.new_secret(), offered_code(store), spake2.APP))
    code = store.start_pairing()["code"]
    with pytest.raises(link.PairingRefused):
        pair_locally(code)


def test_nothing_is_offered_while_linked_and_unlinking_offers_again(store):
    store.offer_locally()
    pair_locally(offered_code(store))
    assert store.offer_locally() is False and not store.offer_path.exists()
    store.unlink()
    assert store.status() == {"linked": False}
    assert store.offer_locally() is True and store.offer_path.exists()


def test_linking_with_the_code_also_withdraws_the_offer(store):
    from conftest import pair_like_starfire

    store.offer_locally()
    pair_like_starfire(store)
    assert not store.offer_path.exists()
    assert store.status()["automatic"] is False


def test_with_the_offer_withdrawn_local_linking_is_refused(store):
    store.offer_locally()
    code = offered_code(store)
    store.withdraw_local_offer()
    assert not store.offer_path.exists()
    with pytest.raises(link.PairingRefused):
        pair_locally(code)


def test_the_window_switch_turns_linking_by_itself_off_and_on(store, monkeypatch):
    saved = {}
    monkeypatch.setattr(arty_server.cfg, "set_value", lambda section, key, value: saved.update({(section, key): value}))
    monkeypatch.setattr(arty_server.cfg, "setting", lambda section, key, default=None: saved.get((section, key), default))
    answer = client.post("/link/automatic", json={"enabled": False}, headers=WINDOW)
    assert answer.json()["automatic"] is False and not store.offer_path.exists()
    answer = client.post("/link/automatic", json={"enabled": True}, headers=WINDOW)
    assert answer.json()["automatic"] is True and store.offer_path.exists()
    assert client.post("/link/automatic", json={"enabled": True}).status_code == 403


def test_the_window_sees_whether_the_link_was_made_by_itself(store):
    store.offer_locally()
    pair_locally(offered_code(store))
    state = client.get("/link", headers=WINDOW).json()
    assert state["linked"] is True and state["automatic"] is True


def test_a_web_page_cannot_start_a_local_pairing(store):
    store.offer_locally()
    secret = spake2.new_secret()
    body = {"name": "x", "share": spake2.share_hex(secret, offered_code(store), spake2.APP), "local": True}
    assert client.post("/link/pair/start", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
