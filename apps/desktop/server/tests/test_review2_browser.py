import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
import yaml

from process import brain, browser, confirm, config
from process.tools import computer, registry
from test_browser_sites import extension, iter_reply, sites

EVIL_IN_FRONT = {"tabs": [{"id": 4, "active": True, "title": "Deals", "url": "https://evil.example/deal"}]}
SHOP_IN_FRONT = {"tabs": [{"id": 7, "active": True, "title": "Shop", "url": "https://shop.example/basket"}]}
BANK_TAB = {"tabs": [{"id": 3, "active": False, "title": "Inbox (3) - me@mail", "url": "https://bank.example/inbox"},
                     {"id": 7, "active": True, "title": "Shop", "url": "https://shop.example/basket"}]}
BANK_IN_FRONT = {"tabs": [{"id": 3, "active": True, "title": "Inbox (3) - me@mail", "url": "https://bank.example/inbox"}]}
BUY = {"text": "Buy now"}


@pytest.fixture(autouse=True)
def buttons_read_from_the_page():
    browser._remember_items({"items": [{"n": 1, "kind": "button", "label": "Buy now"}, {"n": 2, "kind": "link", "label": "Next"}]})


def stored_sites(path):
    return yaml.safe_load(path.read_text())["browser"]["allowed_sites"]


def allow(*names):
    config.set_value("browser", "allowed_sites", list(names))


def test_a_refused_site_is_not_saved_by_a_later_yes_on_an_allowed_site(sites):
    allow("shop.example")
    extension(EVIL_IN_FRONT)
    assert "evil.example" in browser.needs_ok("chrome_click", BUY)
    extension(SHOP_IN_FRONT)
    question = browser.needs_ok("chrome_click", BUY)
    assert "Buy now" in question and "evil.example" not in question
    browser.remember_consent("chrome_click", BUY)
    assert stored_sites(sites) == ["shop.example"]


def test_saying_no_drops_the_pending_site(sites):
    extension(EVIL_IN_FRONT)
    assert browser.needs_ok("chrome_click", BUY)
    registry.consent_refused("chrome_click", json.dumps(BUY))
    assert browser._asked == {}


def test_the_reply_loop_tells_the_tools_when_they_say_no(sites, monkeypatch):
    refused = []
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: "Let Arty read evil.example?")
    monkeypatch.setattr(brain.tools, "consent_refused", lambda name, arguments: refused.append(name), raising=False)
    monkeypatch.setattr(brain.tools, "run", lambda name, arguments: "page")
    monkeypatch.setattr(confirm, "wait", lambda ask, timeout=None: False)
    replies = [{"content": "", "tool_calls": [{"id": "c1", "function": {"name": "chrome_read", "arguments": "{}"}}]},
               {"content": "Okay."}]
    monkeypatch.setattr(brain, "_stream", lambda messages, offer, lead: iter_reply(replies.pop(0)))
    monkeypatch.setattr(brain, "load_history", lambda: [])
    list(brain.llm_stream("read the page"))
    assert refused == ["chrome_read"]


def test_switching_or_reloading_a_tab_on_a_site_not_allowed_asks(sites):
    allow("shop.example")
    extension(BANK_TAB)
    question = browser.needs_ok("chrome_tab", {"action": "reload", "tab": 3})
    assert question and "bank.example" in question


def test_a_tab_action_that_lands_on_a_site_not_allowed_hides_its_title(sites):
    allow("shop.example")
    extension(SHOP_IN_FRONT, {"title": "Inbox (3) - me@mail", "url": "https://bank.example/inbox"})
    done = browser.chrome_tab("back")
    assert "Inbox" not in done and "me@mail" not in done


def test_a_tab_action_on_an_allowed_site_still_names_it(sites):
    allow("shop.example")
    extension(SHOP_IN_FRONT, {"title": "Shop basket", "url": "https://shop.example/basket"})
    assert "Shop basket" in browser.chrome_tab("reload")


def test_a_click_that_lands_on_a_site_not_allowed_hides_where_it_went(sites):
    allow("shop.example")
    extension(SHOP_IN_FRONT, {"clicked": "Next", "title": "Inbox (3) - me@mail", "url": "https://bank.example/inbox?id=991"})
    clicked = browser.chrome_click(text="Next")
    assert "Inbox" not in clicked and "991" not in clicked and "bank.example" in clicked


def test_a_click_is_refused_when_the_front_tab_moved_to_a_site_not_allowed(sites):
    allow("shop.example")
    extension(BANK_IN_FRONT)
    clicked = browser.chrome_click(text="Next")
    assert "bank.example" in clicked and "Clicked" not in clicked
    assert browser.next_command(0) is None


def test_actions_carry_the_checked_site_so_the_extension_can_refuse_a_moved_tab(sites):
    allow("shop.example")
    sent = []
    extension(SHOP_IN_FRONT, lambda command: sent.append(command) or {"field": "search"})
    browser.chrome_type("socks", label="search")
    assert sent[0]["site"] == "shop.example"


def test_a_backslash_before_an_at_sign_is_not_read_as_the_allowed_site(sites, monkeypatch):
    allow("shop.example")
    asked = []
    monkeypatch.setattr(browser, "_ask", lambda action, **arguments: asked.append(action) or {})
    for address in ("https://evil.example\\@shop.example/", "evil.example\\@shop.example", "https://me@shop.example/",
                    "https://shop.example\t.evil.example/", "https://shop%2eexample/"):
        assert "Couldn't" in browser.chrome_open(address), address
    assert asked == []


def test_consent_is_per_scheme_and_port_not_just_the_host_name(sites):
    allow("localhost", "shop.example")
    assert "localhost:8080" in browser.needs_ok("chrome_open", {"url": "http://localhost:8080/admin"})
    assert "http://shop.example" in browser.needs_ok("chrome_open", {"url": "http://shop.example/"})
    assert browser.needs_ok("chrome_open", {"url": "https://shop.example:443/x"}) is None


def test_international_names_are_asked_and_stored_the_way_chrome_reports_them(sites):
    question = browser.needs_ok("chrome_open", {"url": "https://bücher.example/"})
    assert "xn--bcher-kva.example" in question and "bücher" not in question
    browser.remember_consent("chrome_open", {"url": "https://bücher.example/"})
    extension({"tab": 2, "title": "Books", "url": "https://xn--bcher-kva.example/", "text": "Welcome", "items": []})
    assert "Welcome" in browser.chrome_read(tab=2)


def test_words_with_spaces_still_become_a_search(sites, monkeypatch):
    opened = []
    monkeypatch.setattr(browser, "_ask", lambda action, **arguments: opened.append(arguments) or {"tab": 1, "url": "https://www.google.com/"})
    browser.chrome_open("weather in Leeds")
    assert opened[0]["url"] == "https://www.google.com/search?q=weather+in+Leeds"


@pytest.mark.parametrize("name, args", [
    ("chrome_click", {"text": "x" * 500}),
    ("chrome_forget_site", {"site": "y" * 500}),
    ("chrome_open", {"url": "z" * 500}),
    ("desktop_window", {"action": "close", "window": "w" * 500}),
    ("computer_click", {"target": "t" * 500}),
])
def test_every_tool_label_is_capped(name, args):
    label, _where = registry.describe(name, json.dumps(args))
    assert len(label) <= registry.MAX_LABEL_CHARS


def test_only_so_many_actions_are_kept_with_a_reply(monkeypatch):
    saved = []
    monkeypatch.setattr(brain, "load_history", lambda: [])
    monkeypatch.setattr(brain, "_save_history", lambda history: saved.append(history))
    brain.save_turn("do lots", "done", actions=[f"step {n}" for n in range(500)])
    assert len(saved[0][-1]["actions"]) <= brain.MAX_SAVED_ACTIONS


def test_a_yes_to_running_a_program_does_not_grant_the_mouse_and_keyboard():
    question = "Let Arty run the program “poweroff” on your computer? It isn't one of your installed apps."
    assert registry.consent_key("computer_open", question) != registry.consent_key("computer_click")
    assert registry.consent_key("computer_open", "Let Arty use your mouse and keyboard for this?") == computer.consent_key("computer_click")


def test_the_reply_loop_remembers_the_question_that_was_answered(sites, monkeypatch):
    keys = []
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: "Let Arty run the program “x” on your computer?")
    monkeypatch.setattr(brain.tools, "consent_key", lambda name, question=None: keys.append((name, question)) or "k")
    monkeypatch.setattr(brain.tools, "run", lambda name, arguments: "ok")
    monkeypatch.setattr(confirm, "wait", lambda ask, timeout=None: True)
    replies = [{"content": "", "tool_calls": [{"id": "c1", "function": {"name": "computer_open", "arguments": "{}"}}]},
               {"content": "Done."}]
    monkeypatch.setattr(brain, "_stream", lambda messages, offer, lead: iter_reply(replies.pop(0)))
    monkeypatch.setattr(brain, "load_history", lambda: [])
    list(brain.llm_stream("open x"))
    assert keys == [("computer_open", "Let Arty run the program “x” on your computer?")]
