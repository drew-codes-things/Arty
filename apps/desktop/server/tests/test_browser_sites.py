import json
import sys
import threading
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import arty_server
from process import browser, brain, config, confirm
from process.tools import registry

TABS = {"tabs": [{"id": 3, "active": False, "title": "Bank of Example: balance", "url": "https://bank.example/account?id=991"},
                 {"id": 7, "active": True, "title": "Shop", "url": "https://shop.example/basket"}]}


@pytest.fixture
def sites(tmp_path, monkeypatch):
    path = tmp_path / "arty_config.yaml"
    path.write_text("browser:\n  allowed_sites: []\n")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_value_overrides", {})
    config.load_config.cache_clear()
    browser._asked.clear()
    yield path
    config.load_config.cache_clear()


def extension(*replies):
    browser.next_command(0)

    def answer():
        for reply in replies:
            command = browser.next_command(5)
            browser.put_result(command["id"], reply(command) if callable(reply) else reply)
    threading.Thread(target=answer, daemon=True).start()


def test_reading_a_site_for_the_first_time_asks_and_names_the_site(sites):
    extension(TABS)
    question = browser.needs_ok("chrome_read", {})
    assert question and "shop.example" in question


def test_a_named_tab_is_looked_up_so_the_question_names_its_site(sites):
    extension(TABS)
    assert "bank.example" in browser.needs_ok("chrome_read", {"tab": 3})


def test_opening_a_page_asks_about_the_site_it_opens(sites):
    assert "news.example" in browser.needs_ok("chrome_open", {"url": "news.example/today"})


def test_yes_is_remembered_for_that_site_in_the_config(sites):
    extension(TABS)
    browser.needs_ok("chrome_read", {})
    browser.remember_consent("chrome_read", {})
    assert yaml.safe_load(sites.read_text())["browser"]["allowed_sites"] == ["shop.example"]
    extension(TABS)
    assert browser.needs_ok("chrome_read", {}) is None


def test_an_allowed_site_still_asks_before_acting_in_their_name(sites):
    config.set_value("browser", "allowed_sites", ["shop.example"])
    browser._last_page["text"] = "Tab 7: Shop\n[1] button: Buy now"
    extension(TABS)
    assert "Buy now" in browser.needs_ok("chrome_click", {"item": 1})


def test_page_text_from_a_site_that_was_not_allowed_is_never_returned(sites):
    extension({"tab": 3, "title": "Bank", "url": "https://bank.example/account", "text": "Balance 1,234", "items": []})
    page = browser.chrome_read(tab=3)
    assert "Balance" not in page and "bank.example" in page


def test_a_redirect_to_another_site_is_held_back_until_that_site_is_allowed(sites):
    config.set_value("browser", "allowed_sites", ["shop.example"])
    extension({"tab": 9}, {"tab": 9, "title": "Sign in", "url": "https://login.example/", "text": "Password please", "items": []})
    page = browser.chrome_open("https://shop.example/account")
    assert "Password please" not in page and "login.example" in page


def test_the_tab_list_hides_titles_and_addresses_of_sites_not_allowed(sites):
    config.set_value("browser", "allowed_sites", ["shop.example"])
    extension(TABS)
    listed = browser.chrome_tabs()
    assert "Shop" in listed and "shop.example/basket" in listed
    assert "bank.example" in listed and "balance" not in listed and "991" not in listed


def test_forgetting_a_site_makes_arty_ask_again(sites):
    config.set_value("browser", "allowed_sites", ["shop.example", "bank.example"])
    assert "Forgot" in browser.chrome_forget_site("https://shop.example/x")
    assert yaml.safe_load(sites.read_text())["browser"]["allowed_sites"] == ["bank.example"]
    extension(TABS)
    assert browser.needs_ok("chrome_read", {})


def test_when_the_site_cannot_be_told_it_asks_and_remembers_nothing(sites, monkeypatch):
    monkeypatch.setattr(browser, "connected", lambda: False)
    assert browser.needs_ok("chrome_read", {})
    browser.remember_consent("chrome_read", {})
    assert yaml.safe_load(sites.read_text())["browser"]["allowed_sites"] == []


def test_the_reply_loop_remembers_the_site_when_they_say_yes(sites, monkeypatch):
    calls = []
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: "Let Arty read shop.example?")
    monkeypatch.setattr(brain.tools, "consent_given", lambda name, arguments: calls.append((name, arguments)))
    monkeypatch.setattr(brain.tools, "run", lambda name, arguments: "page")
    monkeypatch.setattr(confirm, "wait", lambda ask, timeout=None: True)
    replies = [{"content": "", "tool_calls": [{"id": "c1", "function": {"name": "chrome_read", "arguments": "{}"}}]},
               {"content": "Done."}]
    monkeypatch.setattr(brain, "_stream", lambda messages, offer, lead: iter_reply(replies.pop(0)))
    monkeypatch.setattr(brain, "load_history", lambda: [])
    list(brain.llm_stream("read the page"))
    assert calls == [("chrome_read", "{}")]


def iter_reply(reply):
    return reply
    yield


def test_the_registry_passes_consent_to_the_browser_tools_only(sites, monkeypatch):
    seen = []
    monkeypatch.setattr(browser, "remember_consent", lambda name, args: seen.append((name, args)))
    registry.consent_given("chrome_read", json.dumps({"tab": 3}))
    registry.consent_given("computer_run_command", "{}")
    assert seen == [("chrome_read", {"tab": 3})]


def test_the_app_can_list_and_forget_allowed_sites(sites):
    config.set_value("browser", "allowed_sites", ["shop.example", "bank.example"])
    client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
    token = {"X-Arty-Token": arty_server.TOKEN}
    assert client.get("/browser/sites", headers=token).json() == {"sites": ["shop.example", "bank.example"]}
    forgot = client.post("/browser/sites/forget", headers=token, json={"site": "bank.example"})
    assert forgot.json() == {"sites": ["shop.example"]}
    assert client.get("/browser/sites").status_code == 403
