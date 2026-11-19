import hashlib
import hmac
import io
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from conftest import STARFIRE_NAME, pair_like_starfire
from process import link, notifications, spake2
from process.tools import computer, registry, session

WINDOW = {"X-Arty-Token": arty_server.TOKEN}
client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
NONCE = "0123456789abcdef0123456789abcdef"


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
def served(store, monkeypatch, clock):
    monkeypatch.setattr(arty_server, "starfire_link", store)
    monkeypatch.setattr(arty_server, "link_refusals", link.RefusalPause(clock=clock))
    return store


@pytest.fixture
def computer_on(monkeypatch):
    ran = []
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(registry, "run", lambda name, arguments: ran.append((name, json.loads(arguments))) or f"{name} done")
    return ran


def linked(token):
    return {"X-Arty-Link": token}


def asked_since(after):
    return [note for note in notifications.since(after, 0)[0] if note["kind"] == "link_ask"]


def waiting_question(after):
    fresh, _last = notifications.since(after, 5)
    asked = [note for note in fresh if note["kind"] == "link_ask"]
    assert asked, "Arty Desktop never showed the question"
    return asked[-1]


def answer(ask_id, ok):
    return client.post("/confirm", json={"id": ask_id, "ok": ok}, headers=WINDOW)


def ask_in_thread(path, token, body, results):
    def post():
        results.append(client.post(path, json=body, headers=linked(token)))
    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    return thread


def card_for(token, tool, arguments, ok=False):
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread("/link/computer", token, {"tool": tool, "arguments": arguments}, results)
    note = waiting_question(before)
    answer(note["ask"], ok)
    thread.join(10)
    return note["summary"], results[0]


def app_side_start(code, name=STARFIRE_NAME):
    secret = spake2.new_secret()
    share = spake2.share_hex(secret, code, spake2.APP)
    return secret, share, {"name": name, "share": share}


def test_pairing_never_sends_the_code_and_both_sides_end_with_the_same_token(store):
    code = store.start_pairing()["code"]
    secret, share, _body = app_side_start(code)
    answer_ = store.begin_pair(STARFIRE_NAME, share)
    assert code not in json.dumps(answer_)
    key = spake2.shared_key(secret, code, spake2.APP, share, answer_["share"])
    assert answer_["proof"] == spake2.proof(key, spake2.ARTY)
    store.finish_pair(answer_["session"], spake2.proof(key, spake2.APP))
    assert store.verify(spake2.link_token(key))


def test_with_the_wrong_code_arty_cannot_prove_itself_and_nothing_pairs(store):
    code = store.start_pairing()["code"]
    wrong = "000000" if code != "000000" else "111111"
    secret, share, _body = app_side_start(wrong)
    answer_ = store.begin_pair(STARFIRE_NAME, share)
    key = spake2.shared_key(secret, wrong, spake2.APP, share, answer_["share"])
    assert answer_["proof"] != spake2.proof(key, spake2.ARTY)
    with pytest.raises(link.PairingRefused):
        store.finish_pair(answer_["session"], spake2.proof(key, spake2.APP))
    assert store.status() == {"linked": False}


def test_every_pairing_attempt_counts_toward_the_five_tries(store):
    code = store.start_pairing()["code"]
    for _ in range(link.MAX_WRONG_CODES):
        store.begin_pair(STARFIRE_NAME, app_side_start("999999")[1])
    with pytest.raises(link.PairingRefused, match="new code"):
        store.begin_pair(STARFIRE_NAME, app_side_start(code)[1])


def test_a_finish_needs_the_session_from_the_latest_start(store):
    code = store.start_pairing()["code"]
    secret, share, _body = app_side_start(code)
    first = store.begin_pair(STARFIRE_NAME, share)
    store.begin_pair(STARFIRE_NAME, app_side_start(code)[1])
    key = spake2.shared_key(secret, code, spake2.APP, share, first["share"])
    with pytest.raises(link.PairingRefused):
        store.finish_pair(first["session"], spake2.proof(key, spake2.APP))
    with pytest.raises(link.PairingRefused):
        store.finish_pair("never-started", "0" * 64)


@pytest.mark.parametrize("bad", ["", "zz" * 256, "00" * 256, "00" * 255 + "01", format(spake2.PRIME - 1, "0512x"),
                                 format(spake2.PRIME, "0512x"), "ab" * 255, None, 7])
def test_a_share_that_is_not_in_the_group_is_refused(store, bad):
    store.start_pairing()
    with pytest.raises(link.PairingRefused):
        store.begin_pair(STARFIRE_NAME, bad)


def test_a_share_outside_the_prime_order_subgroup_is_refused(store):
    non_residue = spake2.PRIME - 2 if pow(spake2.PRIME - 2, spake2.ORDER, spake2.PRIME) != 1 else 3
    store.start_pairing()
    with pytest.raises(link.PairingRefused):
        store.begin_pair(STARFIRE_NAME, format(non_residue, "0512x"))


def test_both_halves_match_the_fixed_vector_starfire_also_checks():
    app_secret, arty_secret, code = 0x1234567, 0x7654321, "123456"
    app_share = spake2.share_hex(app_secret, code, spake2.APP)
    arty_share = spake2.share_hex(arty_secret, code, spake2.ARTY)
    app_key = spake2.shared_key(app_secret, code, spake2.APP, app_share, arty_share)
    arty_key = spake2.shared_key(arty_secret, code, spake2.ARTY, app_share, arty_share)
    assert app_key == arty_key
    assert hashlib.sha256(app_share.encode()).hexdigest()[:16] == VECTOR["app_share_sha256_16"]
    assert hashlib.sha256(arty_share.encode()).hexdigest()[:16] == VECTOR["arty_share_sha256_16"]
    assert app_key.hex() == VECTOR["key"]
    assert spake2.proof(app_key, spake2.ARTY) == VECTOR["arty_proof"]
    assert spake2.proof(app_key, spake2.APP) == VECTOR["app_proof"]
    assert spake2.link_token(app_key) == VECTOR["token"]


VECTOR = {
    "app_share_sha256_16": "4c78a39903b4aaba",
    "arty_share_sha256_16": "e73bdcc2d4aa02fc",
    "key": "c3b78bd58e58e5d74a6888bd8aa84055e700ab0c3b271e59b77c7655d006c3ea",
    "arty_proof": "705b01fec2ca56f73afe7064e6d6150ac7bed859aa1859482fb14b8af6728422",
    "app_proof": "6f250c3523f9f2a38be275862b7c5671dd6287023f8d66018ba5b84526bdb8db",
    "token": "Df6qd57CBd3S165TVvBJBQQF5kTcttjGFLxBCK4zzr4",
}


def test_the_pairing_routes_trade_shares_and_never_the_code(served):
    code = client.post("/link/pairing/start", headers=WINDOW).json()["code"]
    secret, share, body = app_side_start(code)
    started = client.post("/link/pair/start", json=body)
    assert started.status_code == 200 and code not in started.text
    answer_ = started.json()
    key = spake2.shared_key(secret, code, spake2.APP, share, answer_["share"])
    assert answer_["proof"] == spake2.proof(key, spake2.ARTY)
    finished = client.post("/link/pair/finish", json={"session": answer_["session"], "proof": spake2.proof(key, spake2.APP)})
    assert finished.status_code == 200 and finished.json() == {"ok": True}
    assert "token" not in finished.text
    assert served.verify(spake2.link_token(key))


def test_the_old_plain_code_route_is_gone(served):
    code = served.start_pairing()["code"]
    assert client.post("/link/pair", json={"code": code, "name": STARFIRE_NAME}).status_code in (403, 404, 405)
    assert served.status() == {"linked": False}


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "none"}, {"Host": "evil.example:8765"}])
def test_a_web_page_cannot_start_a_pairing(served, headers):
    code = served.start_pairing()["code"]
    assert client.post("/link/pair/start", json=app_side_start(code)[2], headers=headers).status_code == 403
    assert client.get(f"/link/proof?nonce={NONCE}", headers=headers).status_code == 403


def expected_proof(token, port, nonce):
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    return hmac.new(fingerprint.encode(), f"arty-link-proof:{port}:{nonce}".encode(), hashlib.sha256).hexdigest()


def test_arty_proves_it_holds_the_pairing_without_being_sent_the_token(served):
    token = pair_like_starfire(served)
    answer_ = client.get(f"/link/proof?nonce={NONCE}")
    assert answer_.status_code == 200
    assert answer_.json() == {"proof": expected_proof(token, 8765, NONCE)}
    assert answer_.json()["proof"] != expected_proof(token, 8766, NONCE)


def test_without_a_pairing_there_is_nothing_to_prove(served):
    answer_ = client.get(f"/link/proof?nonce={NONCE}")
    assert answer_.status_code == 404 and answer_.json()["linked"] is False


@pytest.mark.parametrize("nonce", ["", "abc", NONCE.upper(), NONCE + "0", "g" * 32])
def test_the_proof_needs_a_fresh_32_hex_nonce(served, nonce):
    pair_like_starfire(served)
    assert client.get(f"/link/proof?nonce={nonce}").status_code == 400


def test_the_card_shows_every_line_of_a_command_or_refuses_it(served, computer_on):
    token = pair_like_starfire(served)
    _fresh, before = notifications.since(0, 0)
    two_lines = client.post("/link/computer", json={"tool": "computer_run_command", "arguments": {"command": "echo hello\npython3 ~/notes/x.py"}},
                            headers=linked(token))
    assert two_lines.status_code == 400 and "line" in two_lines.json()["error"]
    assert asked_since(before) == [] and computer_on == []


def test_the_card_shows_the_whole_step_with_every_argument(served, computer_on):
    token = pair_like_starfire(served)
    arguments = {"command": "ls -la ; id | base64 -d", "cwd": "/etc", "timeout_seconds": 600}
    summary, result = card_for(token, "computer_run_command", arguments, ok=True)
    assert summary == (f"{STARFIRE_NAME} asks to do this on your computer: run a command (computer_run_command).\n"
                       "command: ls -la ; id | base64 -d\ncwd: /etc\ntimeout_seconds: 600\nAllow it?")
    assert result.status_code == 200
    assert computer_on == [("computer_run_command", arguments)]


@pytest.mark.parametrize("tool, arguments, shown", [
    ("computer_write_file", {"path": "~/notes/x.py", "content": "import os; os.system('id')"}, "content: import os; os.system('id')"),
    ("computer_open", {"app": "firefox", "url": "https://evil.example/login"}, "url: https://evil.example/login"),
    ("computer_type", {"text": "t" * 70}, "text: " + "t" * 70),
    ("computer_run_command", {"command": "ls" + " " * 80 + "; id"}, "command: ls" + " " * 80 + "; id"),
    ("computer_open", {"app": "kate", "new_document": True}, "new_document: true"),
])
def test_nothing_in_a_step_is_left_off_the_card(served, computer_on, tool, arguments, shown):
    token = pair_like_starfire(served)
    summary, _result = card_for(token, tool, arguments)
    assert shown in summary.split("\n")


@pytest.mark.parametrize("value", ["ls‮", "ls⁦x", "ls​", "ls\x00", "ls\x1b[2K", "ls\tx", "ls x", "ls x",
                                   "ls\rx", " ls", "ls ", ""])
def test_a_value_that_cannot_be_shown_exactly_is_refused_before_asking(served, computer_on, value):
    token = pair_like_starfire(served)
    _fresh, before = notifications.since(0, 0)
    answer_ = client.post("/link/computer", json={"tool": "computer_run_command", "arguments": {"command": value}}, headers=linked(token))
    assert answer_.status_code == 400
    assert asked_since(before) == [] and computer_on == []


@pytest.mark.parametrize("arguments", [
    {"command": "x" * (link.MAX_SHOWN_STEP_CHARS + 1)},
    {"command": "ls", "extra": "rm -rf ~"},
    {"command": ["ls"]},
    {"command": {"inner": "ls"}},
    {"command": None},
    {"command": "ls", "timeout_seconds": float("inf")},
])
def test_a_step_too_long_or_with_parts_the_card_cannot_show_is_refused(served, computer_on, arguments):
    token = pair_like_starfire(served)
    _fresh, before = notifications.since(0, 0)
    body = json.dumps({"tool": "computer_run_command", "arguments": arguments}, allow_nan=True)
    answer_ = client.post("/link/computer", content=body, headers={**linked(token), "Content-Type": "application/json"})
    assert answer_.status_code == 400
    assert asked_since(before) == [] and computer_on == []


def test_refusals_in_a_row_pause_new_steps(served, computer_on, clock):
    token = pair_like_starfire(served)
    for _ in range(link.REFUSALS_BEFORE_PAUSE):
        card_for(token, "computer_apps", {}, ok=False)
    _fresh, before = notifications.since(0, 0)
    paused = client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
    assert paused.status_code == 429 and paused.json()["reason"] == "busy"
    picture = client.post("/link/screenshot", json={"app": ""}, headers=linked(token))
    assert picture.status_code == 429
    assert asked_since(before) == []
    clock.now += link.REFUSAL_PAUSE_SECONDS + 1
    _summary, allowed = card_for(token, "computer_apps", {}, ok=True)
    assert allowed.status_code == 200


def test_an_allow_resets_the_count_of_refusals(served, computer_on):
    token = pair_like_starfire(served)
    for _ in range(link.REFUSALS_BEFORE_PAUSE - 1):
        card_for(token, "computer_apps", {}, ok=False)
    card_for(token, "computer_apps", {}, ok=True)
    for _ in range(link.REFUSALS_BEFORE_PAUSE - 1):
        card_for(token, "computer_apps", {}, ok=False)
    _summary, last = card_for(token, "computer_apps", {}, ok=True)
    assert last.status_code == 200


def test_unanswered_cards_count_as_refusals_too(served, computer_on, monkeypatch):
    token = pair_like_starfire(served)
    monkeypatch.setattr(arty_server, "LINK_ASK_SECONDS", 0.1)
    for _ in range(link.REFUSALS_BEFORE_PAUSE):
        timed_out = client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
        assert timed_out.status_code == 403
    assert client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token)).status_code == 429


def test_a_step_whose_caller_hung_up_never_runs_on_a_late_allow(served, computer_on, monkeypatch):
    token = pair_like_starfire(served)
    gone = threading.Event()
    monkeypatch.setattr(arty_server, "CALLER_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(arty_server, "_is_caller_gone", lambda request: gone.is_set())
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread("/link/computer", token, {"tool": "computer_apps", "arguments": {}}, results)
    note = waiting_question(before)
    gone.set()
    thread.join(10)
    assert answer(note["ask"], True).json() == {"ok": False}
    assert computer_on == []


def test_an_allow_that_races_the_hang_up_still_runs_nothing(served, computer_on, monkeypatch):
    token = pair_like_starfire(served)
    gone = threading.Event()
    monkeypatch.setattr(arty_server, "CALLER_CHECK_SECONDS", 5)
    monkeypatch.setattr(arty_server, "_is_caller_gone", lambda request: gone.is_set())
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread("/link/screenshot", token, {"app": ""}, results)
    note = waiting_question(before)
    gone.set()
    answer(note["ask"], True)
    thread.join(10)
    assert results[0].status_code == 403
    assert computer_on == []


def test_a_timed_out_card_cannot_be_allowed_later(served, computer_on, monkeypatch):
    token = pair_like_starfire(served)
    monkeypatch.setattr(arty_server, "LINK_ASK_SECONDS", 0.1)
    _fresh, before = notifications.since(0, 0)
    client.post("/link/computer", json={"tool": "computer_apps", "arguments": {}}, headers=linked(token))
    note = waiting_question(before)
    assert answer(note["ask"], True).json() == {"ok": False}
    assert computer_on == []


def test_a_large_body_on_an_open_route_is_refused_before_it_is_read(served):
    served.start_pairing()
    huge = json.dumps({"name": "x" * (arty_server.MAX_OPEN_BODY_BYTES + 1), "share": "00"})
    answer_ = client.post("/link/pair/start", content=huge, headers={"Content-Type": "application/json"})
    assert answer_.status_code == 413


def test_an_open_route_body_without_a_length_is_refused(served):
    served.start_pairing()

    def chunks():
        yield b'{"name": "x", '
        yield b'"share": "00"}'
    answer_ = client.post("/link/pair/start", content=chunks(), headers={"Content-Type": "application/json"})
    assert answer_.status_code == 411


def test_the_name_and_share_have_length_limits(served):
    code = served.start_pairing()["code"]
    body = app_side_start(code)[2]
    assert client.post("/link/pair/start", json={**body, "name": "n" * (link.MAX_SENT_NAME_CHARS + 1)}).status_code == 422
    assert client.post("/link/pair/finish", json={"session": "s" * 200, "proof": "0" * 64}).status_code == 422


@pytest.mark.parametrize("name, shown", [
    ("Evil‮ name", "Evil name"),
    ("Ev​il", "Ev il"),
    ("ok⁦⁩ app", "ok app"),
    ("á́́b", "a b"),
    ("starfire Desktop (work)", "starfire Desktop (work)"),
    ("‮​", link.DEFAULT_NAME),
])
def test_the_linked_apps_name_keeps_only_plain_letters_digits_and_a_few_marks(store, name, shown):
    pair_like_starfire(store, name=name)
    assert store.status()["name"] == shown


def test_a_name_saved_by_an_older_version_is_cleaned_when_read(tmp_path):
    path = tmp_path / ".arty_link"
    path.write_text(json.dumps({"name": "Evil‮ name", "fingerprint": "f" * 64, "linked": 1}))
    assert link.Link(path).status()["name"] == "Evil name"


def test_the_guard_uses_the_routed_path_not_one_built_from_the_host(served, monkeypatch):
    from starlette.datastructures import URL
    from starlette.requests import Request

    def url_from_host(request):
        host = dict(request.scope["headers"]).get(b"host", b"").decode()
        return URL(f"http://{host}")
    monkeypatch.setattr(Request, "url", property(url_from_host))
    answer_ = client.post("/confirm", json={"id": "x", "ok": True}, headers={"Host": "127.0.0.1:8765/link/pair/start?"})
    assert answer_.status_code == 403


def picture_card(token, app, ok=False):
    _fresh, before = notifications.since(0, 0)
    results = []
    thread = ask_in_thread("/link/screenshot", token, {"app": app}, results)
    note = waiting_question(before)
    answer(note["ask"], ok)
    thread.join(10)
    return note["summary"], results[0]


XWD_LSB_FIRST = 0
XWD_MSB_FIRST = 1
XWD_TRUE_COLOR = 4
DISCORD_WINDOW = session.LocatedWindow("Xmessage", "Discord", 10, 20, 50, 30, "0x400020")
COVER_WINDOW = session.LocatedWindow("Xmessage", "Cover", 0, 0, 900, 500, "0x600020")
PLACES = {"0x400020": (10, 20, 50, 30), "0x600020": (0, 0, 900, 500), "0x700001": (0, 0, 5, 5)}


@pytest.fixture
def screen(monkeypatch):
    from PIL import Image

    image = Image.new("RGB", (200, 100), (0, 0, 255))
    image.paste((255, 0, 0), (20, 40, 120, 100))
    taken = []
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(computer, "can_see", lambda: True)
    window = Image.new("RGB", (100, 60), (255, 0, 0))
    monkeypatch.setattr(computer, "_screen_image", lambda: taken.append("screen") or image.copy())
    monkeypatch.setattr(computer, "_window_image", lambda window_id: taken.append(window_id) or window.copy())
    monkeypatch.setattr(session, "kind", lambda: "wayland")
    monkeypatch.setattr(session, "window_listing", lambda: ({"Xmessage": ["Discord"]}, True))
    monkeypatch.setattr(session, "front_window", lambda: ("Xmessage", "Discord"))
    monkeypatch.setattr(session, "located_windows", lambda: [DISCORD_WINDOW])
    monkeypatch.setattr(session, "stacked_window_ids", lambda: [DISCORD_WINDOW.window_id])
    monkeypatch.setattr(session, "window_place", lambda window_id: PLACES.get(window_id))
    monkeypatch.setattr(computer, "can_picture_one_window", lambda: True)
    return taken


def decoded(picture):
    import base64
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(picture["image"]))).convert("RGB")


def test_a_named_window_is_captured_by_itself_and_the_screen_is_never_taken(screen):
    picture = computer.window_picture("Discord", only_window=True)
    shown = decoded(picture)
    assert shown.size == (100, 60)
    red, green, blue = shown.getpixel((50, 30))
    assert red > 200 and blue < 60
    assert picture["target"] == 'the window "Discord"'
    assert screen == ["0x400020"]


def test_a_window_under_another_one_is_still_captured_alone_under_xwayland(screen, monkeypatch):
    monkeypatch.setattr(session, "located_windows", lambda: [DISCORD_WINDOW, COVER_WINDOW])
    monkeypatch.setattr(session, "stacked_window_ids", lambda: ["0x400020", "0x600020"])
    assert decoded(computer.window_picture("Discord", only_window=True)).size == (100, 60)
    assert screen == ["0x400020"]


def test_on_plain_x11_a_covered_window_is_not_captured_because_the_cover_could_show_through(screen, monkeypatch):
    monkeypatch.setattr(session, "kind", lambda: "x11")
    monkeypatch.setattr(session, "located_windows", lambda: [DISCORD_WINDOW, COVER_WINDOW])
    monkeypatch.setattr(session, "stacked_window_ids", lambda: ["0x400020", "0x600020"])
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Discord", only_window=True)
    assert refused.value.reason == "cannot_see"
    assert "covered" in str(refused.value)
    assert screen == []
    monkeypatch.setattr(session, "stacked_window_ids", lambda: ["0x600020", "0x400020", "0x700001"])
    assert decoded(computer.window_picture("Discord", only_window=True)).size == (100, 60)


def test_on_plain_x11_an_untitled_or_unplaceable_window_on_top_still_counts_as_a_cover(screen, monkeypatch):
    monkeypatch.setattr(session, "kind", lambda: "x11")
    for above in ["0x600020", "0x7fffff"]:
        monkeypatch.setattr(session, "stacked_window_ids", lambda: ["0x400020", above])
        with pytest.raises(computer.CannotLook):
            computer.window_picture("Discord", only_window=True)
    assert screen == []


def test_on_plain_x11_a_window_whose_place_in_the_stack_is_unknown_is_not_captured(screen, monkeypatch):
    monkeypatch.setattr(session, "kind", lambda: "x11")
    monkeypatch.setattr(session, "stacked_window_ids", lambda: [])
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Discord", only_window=True)
    assert refused.value.reason == "cannot_see"
    assert screen == []


def test_a_window_that_is_gone_after_the_allow_is_not_found_and_nothing_is_captured(screen, monkeypatch):
    monkeypatch.setattr(session, "located_windows", lambda: [])
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Discord", only_window=True)
    assert refused.value.reason == "not_found"
    assert screen == []


def test_a_window_that_cannot_be_captured_sends_nothing(screen, monkeypatch):
    def fails(window_id):
        raise RuntimeError("xwd: BadMatch")

    monkeypatch.setattr(computer, "_window_image", fails)
    with pytest.raises(computer.CannotLook) as refused:
        computer.window_picture("Discord", only_window=True)
    assert refused.value.reason == "failed"
    assert screen == []


def test_the_card_promises_only_the_window_when_arty_can_capture_it_alone(served, screen):
    token = pair_like_starfire(served)
    summary, result = picture_card(token, "Discord", ok=True)
    assert summary == (f'{STARFIRE_NAME} wants a picture of the window "Discord", and only that window, '
                       "captured by itself so nothing on top of it is sent. Allow it?")
    assert result.status_code == 200 and decoded(result.json()).size == (100, 60)


def test_the_card_says_plainly_when_the_whole_screen_would_be_sent(served, screen, monkeypatch):
    monkeypatch.setattr(computer, "can_picture_one_window", lambda: False)
    token = pair_like_starfire(served)
    summary, result = picture_card(token, "Discord", ok=True)
    assert summary == (f'{STARFIRE_NAME} wants a screenshot of your whole screen, to look for the window "Discord". '
                       "This desktop cannot show Arty where that window is, so the whole screen would be sent. Allow it?")
    assert decoded(result.json()).size == (200, 100)


def test_x11_window_places_and_stacking_are_read_from_xprop_and_xwininfo(monkeypatch):
    outputs = {
        ("xprop", "-root", "_NET_CLIENT_LIST"): "_NET_CLIENT_LIST(WINDOW): window id # 0x400001",
        ("xprop", "-root", "_NET_CLIENT_LIST_STACKING"): "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x400001, 0x600020",
        ("xprop", "-id", "0x400001", "WM_CLASS", "_NET_WM_NAME", "WM_NAME"): 'WM_CLASS(STRING) = "xmessage", "Xmessage"\nWM_NAME(STRING) = "Discord"',
        ("xwininfo", "-id", "0x400001"): ("  Absolute upper-left X:  60\n  Absolute upper-left Y:  84\n"
                                          "  Relative upper-left X:  0\n  Width: 560\n  Height: 200\n"),
    }
    monkeypatch.setattr(session, "run", lambda args, timeout=2.0: outputs.get(tuple(args)))
    assert session.located_windows() == [session.LocatedWindow("Xmessage", "Discord", 60, 84, 560, 200, "0x400001")]
    assert session.stacked_window_ids() == ["0x400001", "0x600020"]
    assert session.window_place("0x400001") == (60, 84, 560, 200)
    assert session.window_place("0x999999") is None


def test_one_window_pictures_need_a_desktop_that_can_place_windows(monkeypatch):
    monkeypatch.setattr(session, "have", lambda tool: tool != "xwd")
    monkeypatch.setattr(session, "kind", lambda: "x11")
    assert not computer.can_picture_one_window()
    monkeypatch.setattr(session, "have", lambda tool: True)
    assert computer.can_picture_one_window()
    monkeypatch.setattr(session, "kind", lambda: "wayland")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert not computer.can_picture_one_window()
    monkeypatch.setattr(session, "kind", lambda: "windows")
    assert not computer.can_picture_one_window()


def xwd_file(width, height, pixels, byte_order=XWD_LSB_FIRST, bits_per_pixel=32, visual_class=XWD_TRUE_COLOR, name=b"Discord\0"):
    import struct

    bytes_per_line = width * bits_per_pixel // 8
    fields = [100 + len(name), 7, 2, 24, width, height, 0, byte_order, 32, byte_order, 32, bits_per_pixel,
              bytes_per_line, visual_class, 0xFF0000, 0xFF00, 0xFF, 8, 256, 0, width, height, 0, 0, 0]
    return struct.pack(">25I", *fields) + name + pixels


def pixels_of(image):
    return [image.getpixel((x, 0)) for x in range(image.width)]


def test_an_xwd_window_dump_becomes_the_same_pixels():
    from process.tools import xwd

    lsb = xwd_file(2, 1, bytes([0, 0, 255, 0, 255, 0, 0, 0]))
    assert pixels_of(xwd.image_from_xwd(lsb)) == [(255, 0, 0), (0, 0, 255)]
    msb = xwd_file(2, 1, bytes([0, 255, 0, 0, 0, 0, 0, 255]), byte_order=XWD_MSB_FIRST)
    assert pixels_of(xwd.image_from_xwd(msb)) == [(255, 0, 0), (0, 0, 255)]


def test_an_xwd_dump_that_is_not_plain_true_colour_or_is_cut_short_is_refused():
    from process.tools import xwd

    for broken in [b"", b"x" * 99, xwd_file(2, 1, bytes(4)), xwd_file(2, 1, bytes(4), bits_per_pixel=16),
                   xwd_file(2, 1, bytes(8), visual_class=3), xwd_file(0, 1, b""), xwd_file(70000, 1, bytes(8))]:
        with pytest.raises(xwd.NotUsable):
            xwd.image_from_xwd(broken)
