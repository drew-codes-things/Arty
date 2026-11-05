import sys
from pathlib import Path

import pytest
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import config
from process import connection

STARTER = """brain:
  base_url: ""
  remote_url: ""               # used away from home
  access_client_id: ""
  access_client_secret: ""
  api_key: ""                 # the Arty API key
  model: "assistant-core"
"""
KEY = "sk-arty-test"
ARTY_MODELS = {"object": "list", "data": [{"id": "assistant-core"}]}


class Answer:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class FakeBrain:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, url, headers, timeout):
        self.asked.append((url, headers))
        answer = self.answers.get(url.removesuffix("/models"))
        if answer is None:
            raise requests.ConnectionError(url)
        return answer


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "arty_config.yaml"
    path.write_text(STARTER)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_value_overrides", {})
    config.load_config.cache_clear()
    yield path
    config.load_config.cache_clear()


def test_a_starter_config_without_a_key_is_not_connected(config_file):
    assert connection.is_configured() is False
    config.set_value("brain", "api_key", KEY)
    assert connection.is_configured() is True


def test_the_address_gets_v1_and_loses_a_trailing_slash():
    entered = connection.cleaned(connection.Connection(" http://192.168.1.50:8081/ ", f" {KEY} "))
    assert entered.base_url == "http://192.168.1.50:8081/v1"
    assert entered.api_key == KEY
    kept = connection.cleaned(connection.Connection("http://arty.lan:8081/v1/", KEY))
    assert kept.base_url == "http://arty.lan:8081/v1"


@pytest.mark.parametrize("address", ["", "192.168.1.50:8081", "ftp://arty.lan/v1", "http:///v1", "javascript:alert(1)"])
def test_an_address_that_is_not_http_is_refused(address):
    with pytest.raises(connection.ConnectionProblem, match="http"):
        connection.cleaned(connection.Connection(address, KEY))


def test_a_missing_key_is_refused():
    with pytest.raises(connection.ConnectionProblem, match="API key"):
        connection.cleaned(connection.Connection("http://arty.lan:8081/v1", "  "))


def test_the_remote_address_must_be_https():
    with pytest.raises(connection.ConnectionProblem, match="https"):
        connection.cleaned(connection.Connection("http://arty.lan:8081/v1", KEY, remote_url="http://arty.example.com/v1"))


def test_a_cloudflare_id_needs_its_secret():
    with pytest.raises(connection.ConnectionProblem, match="both"):
        connection.cleaned(connection.Connection("http://arty.lan:8081/v1", KEY, remote_url="https://arty.example.com/v1",
                                                 access_client_id="abc.access"))


def test_a_line_break_in_a_value_is_refused():
    with pytest.raises(connection.ConnectionProblem, match="one line"):
        connection.cleaned(connection.Connection("http://arty.lan:8081/v1", "sk-arty\nbrain:"))


def test_the_check_sends_the_key_and_returns_the_address_that_answered():
    brain = FakeBrain({"http://arty.lan:8081/v1": Answer(200, ARTY_MODELS)})
    reached = connection.check(connection.Connection("http://arty.lan:8081/v1", KEY), get=brain)
    assert reached == "http://arty.lan:8081/v1"
    assert brain.asked == [("http://arty.lan:8081/v1/models", {"Authorization": f"Bearer {KEY}"})]


def test_away_from_home_the_remote_address_is_tried_with_the_access_token():
    brain = FakeBrain({"https://arty.example.com/v1": Answer(200, ARTY_MODELS)})
    entered = connection.Connection("http://arty.lan:8081/v1", KEY, "https://arty.example.com/v1", "abc.access", "shh")
    assert connection.check(entered, get=brain) == "https://arty.example.com/v1"
    assert brain.asked[1] == ("https://arty.example.com/v1/models", {
        "Authorization": f"Bearer {KEY}", "CF-Access-Client-Id": "abc.access", "CF-Access-Client-Secret": "shh"})


def test_a_refused_key_says_so():
    brain = FakeBrain({"http://arty.lan:8081/v1": Answer(401, {"error": "no"})})
    with pytest.raises(connection.ConnectionProblem, match="refused the API key"):
        connection.check(connection.Connection("http://arty.lan:8081/v1", KEY), get=brain)


def test_an_unreachable_brain_names_the_address():
    with pytest.raises(connection.ConnectionProblem, match="Can't reach http://arty.lan:8081/v1"):
        connection.check(connection.Connection("http://arty.lan:8081/v1", KEY), get=FakeBrain({}))


def test_something_that_is_not_arty_is_refused():
    brain = FakeBrain({"http://router.lan/v1": Answer(200, None)})
    with pytest.raises(connection.ConnectionProblem, match="isn't Arty"):
        connection.check(connection.Connection("http://router.lan/v1", KEY), get=brain)


def test_save_writes_every_field_and_keeps_the_rest(config_file):
    connection.save(connection.Connection("http://arty.lan:8081/v1", KEY, "https://arty.example.com/v1", "abc.access", "shh"))
    text = config_file.read_text()
    saved = yaml.safe_load(text)["brain"]
    assert saved == {"base_url": "http://arty.lan:8081/v1", "remote_url": "https://arty.example.com/v1",
                     "access_client_id": "abc.access", "access_client_secret": "shh", "api_key": KEY,
                     "model": "assistant-core"}
    assert "# the Arty API key" in text


def test_the_addresses_shown_never_include_secrets(config_file):
    connection.save(connection.Connection("http://arty.lan:8081/v1", KEY, "https://arty.example.com/v1", "abc.access", "shh"))
    shown = connection.addresses()
    assert shown == {"base_url": "http://arty.lan:8081/v1", "remote_url": "https://arty.example.com/v1"}


def test_the_window_saves_a_working_connection_and_the_service_restarts(config_file, monkeypatch):
    from fastapi.testclient import TestClient
    import arty_server

    restarts = []
    monkeypatch.setattr(arty_server, "restart_service", lambda: restarts.append(True))
    monkeypatch.setattr(connection.requests, "get", FakeBrain({"http://arty.lan:8081/v1": Answer(200, ARTY_MODELS)}))
    client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
    window = {"X-Arty-Token": arty_server.TOKEN}
    assert client.get("/connection", headers=window).json() == {"configured": False, "base_url": "", "remote_url": ""}
    saved = client.post("/connection", headers=window, json={"base_url": "http://arty.lan:8081", "api_key": KEY})
    assert saved.json() == {"ok": True, "reached": "http://arty.lan:8081/v1"}
    assert restarts == [True]
    assert yaml.safe_load(config_file.read_text())["brain"]["api_key"] == KEY


def test_a_bad_connection_is_not_saved(config_file, monkeypatch):
    from fastapi.testclient import TestClient
    import arty_server

    monkeypatch.setattr(arty_server, "restart_service", lambda: pytest.fail("restarted after a failed check"))
    monkeypatch.setattr(connection.requests, "get", FakeBrain({"http://arty.lan:8081/v1": Answer(401, {})}))
    client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
    refused = client.post("/connection", headers={"X-Arty-Token": arty_server.TOKEN},
                          json={"base_url": "http://arty.lan:8081/v1", "api_key": KEY})
    assert refused.status_code == 400
    assert "refused the API key" in refused.json()["error"]
    assert yaml.safe_load(config_file.read_text())["brain"]["api_key"] == ""


def test_the_chrome_extension_cannot_change_the_connection(config_file, monkeypatch):
    from fastapi.testclient import TestClient
    import arty_server

    monkeypatch.setattr(arty_server, "restart_service", lambda: pytest.fail("restarted for the extension"))
    client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
    response = client.post("/connection", headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
                           json={"base_url": "http://evil.example/v1", "api_key": KEY})
    assert response.status_code == 403
    assert yaml.safe_load(config_file.read_text())["brain"]["base_url"] == ""
