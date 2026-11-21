import os

import pytest

STARFIRE_NAME = "starfire Desktop"
REAL_SCREEN_CONTROL = "real_screen_control"
INTERNET_ADDRESS = "93.184.215.14"
HAS_POSIX_MODES = os.name == "posix"


def pytest_configure(config):
    config.addinivalue_line("markers", f"{REAL_SCREEN_CONTROL}: run with the real screen control session and field checks")


class PassThroughControl:
    def run(self, step):
        return step()

    def chunks(self, text):
        return [text]

    def inject(self, action):
        return action()

    def new_reply(self):
        return None

    def stop(self, reason):
        return False


@pytest.fixture(autouse=True)
def screen_control_out_of_the_way(request, monkeypatch):
    if request.node.get_closest_marker(REAL_SCREEN_CONTROL):
        return
    from process import screen_control
    from process.tools import computer

    monkeypatch.setattr(screen_control, "control", PassThroughControl())
    monkeypatch.setattr(computer, "_private_typing_refusal", lambda text: None)


@pytest.fixture(autouse=True)
def site_names_lead_to_the_internet(monkeypatch):
    from process import home_network

    monkeypatch.setattr(home_network, "resolve", lambda host, port: [(2, 1, 6, "", (INTERNET_ADDRESS, 0))])


@pytest.fixture(autouse=True)
def no_tools_used_before_the_test(monkeypatch):
    from process.tools import registry

    monkeypatch.setattr(registry, "_last_used", {})


@pytest.fixture(autouse=True)
def every_test_starts_a_fresh_reply(monkeypatch):
    from process.tools import registry

    for key, value in list(registry._reply.items()):
        monkeypatch.setitem(registry._reply, key, type(value)())


@pytest.fixture
def linux(monkeypatch):
    from process.tools import shell

    monkeypatch.setattr(shell.sys, "platform", "linux")


def pair_like_starfire(store, code=None, name=STARFIRE_NAME):
    from process import spake2

    code = store.start_pairing()["code"] if code is None else code
    secret = spake2.new_secret()
    app_share = spake2.share_hex(secret, code, spake2.APP)
    answer = store.begin_pair(name, app_share)
    key = spake2.shared_key(secret, code, spake2.APP, app_share, answer["share"])
    if answer["proof"] != spake2.proof(key, spake2.ARTY):
        raise AssertionError("Arty Desktop did not prove it knows the code")
    store.finish_pair(answer["session"], spake2.proof(key, spake2.APP))
    return spake2.link_token(key)


@pytest.fixture(autouse=True)
def no_real_windows_are_named_or_raised(monkeypatch):
    from process.tools import computer

    monkeypatch.setattr(computer, "_front_window_id", lambda: None)
    monkeypatch.setattr(computer, "_activate_window", lambda window_id: False)
    monkeypatch.setattr(computer, "_listed_windows", lambda: [])


@pytest.fixture(autouse=True)
def no_learning_page_seen_before_the_test():
    from process import learning_pages

    learning_pages.forget()
    yield
    learning_pages.forget()
