import pytest

STARFIRE_NAME = "starfire Desktop"


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
