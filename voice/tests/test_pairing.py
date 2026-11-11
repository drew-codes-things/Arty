import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import pytest
from aiohttp.test_utils import TestClient, TestServer

import headset
import pairing


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_new_code_is_eight_easy_characters_in_two_groups():
    code = pairing.PairingCodes(Clock()).issue()
    assert len(code) == 9 and code[4] == "-"
    assert all(character in pairing.ALPHABET for character in code.replace("-", ""))


def test_a_code_works_once_however_it_is_typed():
    codes = pairing.PairingCodes(Clock())
    code = codes.issue()
    assert codes.redeem(code.lower().replace("-", " ")) == pairing.OK
    assert codes.redeem(code) == pairing.WRONG


def test_a_code_stops_working_after_ten_minutes():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    code = codes.issue()
    clock.now += pairing.LIFETIME_SECONDS + 1
    assert codes.redeem(code) == pairing.WRONG


def test_ten_wrong_guesses_lock_that_device_out_for_a_while():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    code = codes.issue()
    for _ in range(pairing.MAX_WRONG_TRIES):
        assert codes.redeem("AAAA-AAAA") == pairing.WRONG
    assert codes.redeem(code) == pairing.LOCKED
    clock.now += pairing.LOCKOUT_SECONDS + 1
    assert codes.redeem(code) == pairing.OK


def test_only_a_few_codes_are_live_at_once_and_the_oldest_goes_first():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    issued = []
    for _ in range(pairing.MAX_LIVE_CODES + 1):
        clock.now += 1
        issued.append(codes.issue())
    assert codes.redeem(issued[0]) == pairing.WRONG
    assert codes.redeem(issued[-1]) == pairing.OK


def served(monkeypatch):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", "right-key")
    return TestClient(TestServer(headset.build_app(None, None)))


def test_only_the_key_holder_can_make_a_code_and_anyone_with_the_code_gets_the_key_once(monkeypatch):
    async def run():
        client = served(monkeypatch)
        await client.start_server()
        try:
            refused = await client.get("/pair/new")
            wrong_key = await client.get("/pair/new", headers={"Authorization": "Bearer nope"})
            made = await client.get("/pair/new", headers={"Authorization": "Bearer right-key"})
            code = (await made.json())["code"]
            plain = await client.get("/pair/new", params={"format": "text"}, headers={"Authorization": "Bearer right-key"})
            used = await client.post("/pair/redeem", json={"code": code})
            again = await client.post("/pair/redeem", json={"code": code})
            junk = await client.post("/pair/redeem", data="not json")
            return refused.status, wrong_key.status, made.status, len((await plain.text()).strip()), used.status, await used.json(), again.status, junk.status
        finally:
            await client.close()

    refused, wrong_key, made, plain_length, used, body, again, junk = asyncio.run(run())
    assert (refused, wrong_key, made) == (401, 401, 200)
    assert plain_length == 9
    assert used == 200 and body == {"token": "right-key"}
    assert again == 400 and junk == 400


def test_too_many_wrong_codes_are_answered_with_try_later(monkeypatch):
    async def run():
        client = served(monkeypatch)
        await client.start_server()
        try:
            statuses = [(await client.post("/pair/redeem", json={"code": "AAAA-AAAA"})).status for _ in range(pairing.MAX_WRONG_TRIES + 1)]
            return statuses
        finally:
            await client.close()

    statuses = asyncio.run(run())
    assert statuses[:pairing.MAX_WRONG_TRIES] == [400] * pairing.MAX_WRONG_TRIES and statuses[-1] == 429


def test_wrong_guesses_spread_over_a_long_time_never_lock_anyone_out():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    code = codes.issue()
    for _ in range(pairing.MAX_WRONG_TRIES * 3):
        assert codes.redeem("AAAA-AAAA") == pairing.WRONG
        clock.now += pairing.WRONG_WINDOW_SECONDS / 2
    assert codes.redeem(code) == pairing.WRONG
