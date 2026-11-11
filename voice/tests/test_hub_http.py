import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))

from aiohttp.test_utils import TestClient, TestServer

import voice_hub


def fetch_login(tmp_path, monkeypatch, headers):
    monkeypatch.setattr(voice_hub, "DATA_DIR", str(tmp_path))

    async def run():
        client = TestClient(TestServer(voice_hub.build_app()))
        await client.start_server()
        try:
            page = await client.get("/setup", allow_redirects=False)
            posted = await client.post("/login", data={"email": "a@b.co", "password": "x"},
                                       headers=headers, allow_redirects=False)
            return page.headers, posted.status, await posted.text()
        finally:
            await client.close()

    return asyncio.run(run())


def test_pages_keep_the_origin_header_for_same_site_forms(tmp_path, monkeypatch):
    headers, _, _ = fetch_login(tmp_path, monkeypatch, {})
    assert headers["Referrer-Policy"] == "same-origin"
    assert headers["Cache-Control"] == "no-cache"


def test_cross_site_form_is_refused(tmp_path, monkeypatch):
    _, status, body = fetch_login(tmp_path, monkeypatch, {"Origin": "http://evil.example"})
    assert status == 403 and "cross-site" in body


async def logged_in_client(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_hub, "DATA_DIR", str(tmp_path))
    client = TestClient(TestServer(voice_hub.build_app()))
    await client.start_server()
    password = "a long enough password 42"
    code = (tmp_path / voice_hub.SETUP_CODE_FILE).read_text().strip()
    await client.post("/setup", data={"email": "a@b.co", "password": password, "confirm": password, "setup_code": code})
    return client


def test_the_first_account_needs_the_setup_code_from_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_hub, "DATA_DIR", str(tmp_path))
    password = "a long enough password 42"
    form = {"email": "a@b.co", "password": password, "confirm": password}

    async def run():
        client = TestClient(TestServer(voice_hub.build_app()))
        await client.start_server()
        try:
            code_file = tmp_path / voice_hub.SETUP_CODE_FILE
            mode = os.stat(code_file).st_mode & 0o777
            code = code_file.read_text().strip()
            missing = await client.post("/setup", data=form, allow_redirects=False)
            wrong = await client.post("/setup", data={**form, "setup_code": "guess"}, allow_redirects=False)
            had_account = client.app["accounts"].has_accounts()
            right = await client.post("/setup", data={**form, "setup_code": code}, allow_redirects=False)
            return mode, len(code), missing.headers["Location"], wrong.headers["Location"], had_account, right.status, right.headers["Location"], code_file.exists()
        finally:
            await client.close()

    mode, length, missing, wrong, had_account, right_status, right_location, still_there = asyncio.run(run())
    assert mode == 0o600 and length >= 12
    assert missing.startswith("/setup?error=") and wrong.startswith("/setup?error=")
    assert not had_account
    assert (right_status, right_location) == (303, "/")
    assert not still_there


def test_the_session_cookie_is_secure_when_the_hub_is_served_over_https(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_hub, "PUBLIC_URL", "https://voice.example.com")

    async def scenario(client):
        return [cookie for cookie in client.session.cookie_jar]

    async def run():
        client = await logged_in_client(tmp_path, monkeypatch)
        try:
            return await scenario(client)
        finally:
            await client.close()

    cookies = asyncio.run(run())
    assert cookies and all(cookie["secure"] for cookie in cookies)


def run_with_client(tmp_path, monkeypatch, scenario):
    async def run():
        client = await logged_in_client(tmp_path, monkeypatch)
        try:
            return await scenario(client)
        finally:
            await client.close()

    return asyncio.run(run())


def test_wake_switch_starts_on_and_is_saved(tmp_path, monkeypatch):
    async def scenario(client):
        before = (await (await client.get("/api/status")).json())["wake_enabled"]
        switched = await client.post("/api/wake", json={"enabled": False})
        after = (await (await client.get("/api/status")).json())["wake_enabled"]
        return before, switched.status, after

    assert run_with_client(tmp_path, monkeypatch, scenario) == (True, 200, False)
    assert voice_hub.WakeSwitch().enabled is False


def test_wake_switch_needs_a_true_or_false(tmp_path, monkeypatch):
    async def scenario(client):
        return (await client.post("/api/wake", json={"enabled": "off"})).status

    assert run_with_client(tmp_path, monkeypatch, scenario) == 400


def test_wake_switch_needs_login_and_same_site(tmp_path, monkeypatch):
    async def scenario(client):
        cross_site = await client.post("/api/wake", json={"enabled": False}, headers={"Origin": "http://evil.example"})
        client.session.cookie_jar.clear()
        signed_out = await client.post("/api/wake", json={"enabled": False})
        return cross_site.status, signed_out.status

    assert run_with_client(tmp_path, monkeypatch, scenario) == (403, 401)
    assert voice_hub.WakeSwitch().enabled is True


def test_only_the_assistant_switches_by_voice_and_the_mic_stream_hears_it(tmp_path, monkeypatch):
    async def scenario(client):
        devices = client.app["devices"]
        _, assistant_key = devices.add("Arty", "assistant", "assistant")
        _, pc_key = devices.add("My PC", "debian", "pc")
        microphone = client.app["microphone"]

        async def quiet_subscribe():
            return asyncio.Queue()

        monkeypatch.setattr(microphone, "subscribe", quiet_subscribe)
        monkeypatch.setattr(microphone, "unsubscribe", lambda queue: None)
        ws = await client.ws_connect("/ws/mic", headers={"Authorization": f"Bearer {assistant_key}"})
        first = await ws.receive_json()
        refused = await client.post("/assistant/wake", json={"enabled": False},
                                    headers={"Authorization": f"Bearer {pc_key}"})
        allowed = await client.post("/assistant/wake", json={"enabled": False},
                                    headers={"Authorization": f"Bearer {assistant_key}"})
        pushed = await ws.receive_json(timeout=5)
        await ws.close()
        return first["wake_enabled"], refused.status, allowed.status, pushed

    assert run_with_client(tmp_path, monkeypatch, scenario) == (True, 403, 200, {"wake_enabled": False})


def test_mixer_state_and_volume_targets():
    assert voice_hub.mixer_state("  Mono: Playback 0 [0%] [-64.00dB] [off]") == (0, True)
    assert voice_hub.mixer_state("  Front Left: Playback 40 [62%] [-9.00dB] [on]") == (62, False)
    assert voice_hub.volume_target(0, True, "up", None) == (10, False)
    assert voice_hub.volume_target(95, False, "up", None) == (100, False)
    assert voice_hub.volume_target(10, False, "down", None) == (0, True)
    assert voice_hub.volume_target(0, True, "unmute", None) == (10, False)
    assert voice_hub.volume_target(40, False, "mute", None) == (40, True)
    assert voice_hub.volume_target(40, False, None, 70) == (70, False)
    assert voice_hub.volume_target(40, False, None, 0) == (0, True)


def test_only_the_assistant_changes_the_volume(tmp_path, monkeypatch):
    async def scenario(client):
        devices = client.app["devices"]
        _, assistant_key = devices.add("Arty", "assistant", "assistant")
        _, pc_key = devices.add("My PC", "debian", "pc")
        calls = []

        async def fake_amixer(*args):
            calls.append(args)
            return "Mono: Playback 32 [50%] [-6.00dB] [on]" if args[0] == "sget" else "Mono: Playback 38 [60%] [on]"

        monkeypatch.setattr(client.app["volume"], "amixer", fake_amixer)
        refused = await client.post("/assistant/volume", json={"change": "up"}, headers={"Authorization": f"Bearer {pc_key}"})
        bad = await client.post("/assistant/volume", json={"change": "loud"}, headers={"Authorization": f"Bearer {assistant_key}"})
        allowed = await client.post("/assistant/volume", json={"change": "up"}, headers={"Authorization": f"Bearer {assistant_key}"})
        return refused.status, bad.status, allowed.status, await allowed.json(), calls[-1]

    assert run_with_client(tmp_path, monkeypatch, scenario) == (
        403, 400, 200, {"level": 60, "muted": False}, ("sset", "Master", "60%", "unmute"))


def test_a_volume_request_that_is_not_an_object_is_a_bad_request(tmp_path, monkeypatch):
    async def scenario(client):
        _, assistant_key = client.app["devices"].add("Arty", "assistant", "assistant")
        headers = {"Authorization": f"Bearer {assistant_key}"}
        return [(await client.post("/assistant/volume", json=body, headers=headers)).status for body in ([1, 2], 5, "up")]

    assert run_with_client(tmp_path, monkeypatch, scenario) == [400, 400, 400]


def test_failed_logins_from_one_address_are_limited_across_emails(tmp_path, monkeypatch):
    async def scenario(client):
        client.session.cookie_jar.clear()
        statuses = []
        for attempt in range(voice_hub.MAX_FAILED_LOGINS_PER_ADDRESS + 1):
            response = await client.post("/login", data={"email": f"guess{attempt}@b.co", "password": "nope nope"},
                                         allow_redirects=False)
            statuses.append(response.headers["Location"])
        return statuses

    locations = run_with_client(tmp_path, monkeypatch, scenario)
    assert locations[-1] == "/login?error=locked"
    assert set(locations[:-1]) == {"/login?error=wrong"}


def test_the_failed_login_record_stays_small(monkeypatch):
    failures = voice_hub.FailedLogins()
    clock = [1000.0]
    monkeypatch.setattr(voice_hub.time, "time", lambda: clock[0])
    for attempt in range(voice_hub.MAX_TRACKED_LOGIN_KEYS * 3):
        failures.record(f"192.0.2.{attempt % 200}", f"user{attempt}@b.co")
        clock[0] += 1
    assert len(failures.times) <= voice_hub.MAX_TRACKED_LOGIN_KEYS


class FakeArecord:
    started = 0

    def __init__(self):
        FakeArecord.started += 1
        self.returncode = None
        self.ended = asyncio.Event()
        self.stdout = self
        self.stderr = self

    async def readexactly(self, size):
        await self.ended.wait()
        raise asyncio.IncompleteReadError(b"", size)

    async def read(self):
        return b""

    def terminate(self):
        self.returncode = -15
        self.ended.set()

    async def wait(self):
        return self.returncode


def test_a_listener_that_comes_back_while_the_mic_closes_gets_audio_at_once(monkeypatch):
    async def spawn(*args, **kwargs):
        return FakeArecord()

    monkeypatch.setattr(voice_hub.asyncio, "create_subprocess_exec", spawn)

    async def run():
        FakeArecord.started = 0
        microphone = voice_hub.Microphone()
        first = await microphone.subscribe()
        for _ in range(5):
            await asyncio.sleep(0)
        microphone.unsubscribe(first)
        second = await microphone.subscribe()
        for _ in range(50):
            await asyncio.sleep(0)
            if FakeArecord.started == 2:
                break
        restarted, error = FakeArecord.started, microphone.error
        microphone.unsubscribe(second)
        microphone.reader.cancel()
        return restarted, error

    assert asyncio.run(run()) == (2, "")
