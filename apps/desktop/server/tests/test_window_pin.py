import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import PassThroughControl
from process import screen_control
from process.tools import computer, registry, session

ARTY = "0x9"
FIREFOX = "0x1"
BANK = "0x2"
DISCORD = "0x3"


class FakeDesk:
    def __init__(self):
        self.windows = {FIREFOX: ("firefox", "Inbox (1) - Mozilla Firefox"), BANK: ("firefox", "My Bank - Mozilla Firefox"),
                        DISCORD: ("discord", "general"), ARTY: ("arty", "Arty")}
        self.front = FIREFOX
        self.activations = []
        self.refuses_to_activate = False
        self.done = []

    def front_window(self):
        return self.windows[self.front]

    def front_text(self):
        return ": ".join(part for part in self.front_window() if part)

    def activate(self, window_id):
        self.activations.append(window_id)
        if self.refuses_to_activate:
            return False
        self.front = window_id
        return True

    def listed(self):
        return [(window_id, app, title) for window_id, (app, title) in self.windows.items()]


class SplitControl(PassThroughControl):
    def __init__(self, desk, switch_to=None):
        self.desk = desk
        self.switch_to = switch_to
        self.injected = 0

    def chunks(self, text):
        return [text[:3], text[3:]]

    def inject(self, action):
        self.injected += 1
        result = action()
        if self.switch_to:
            self.desk.front = self.switch_to
        return result


@pytest.fixture
def desk(monkeypatch):
    fake = FakeDesk()
    monkeypatch.setattr(computer, "can_control", lambda: True)
    monkeypatch.setattr(computer, "can_see", lambda: True)
    monkeypatch.setattr(computer, "setting", lambda section, key, default=None: default)
    monkeypatch.setattr(session, "front_window", fake.front_window)
    monkeypatch.setattr(computer, "_front_text", fake.front_text)
    monkeypatch.setattr(computer, "_front_window_id", lambda: fake.front)
    monkeypatch.setattr(computer, "_activate_window", fake.activate)
    monkeypatch.setattr(computer, "_listed_windows", fake.listed)
    monkeypatch.setattr(computer, "_type_route", lambda text: fake.done.append(("type", text)) or [])
    monkeypatch.setattr(computer, "_press_route", lambda mods, key, named: fake.done.append(("key", key)))
    monkeypatch.setattr(computer, "_click_at", lambda px, py, right, double: fake.done.append(("click", px, py, right, double)))
    monkeypatch.setattr(computer, "TYPE_APP_WAIT_SECONDS", 0)
    monkeypatch.setattr(computer, "ACTIVATE_WAIT_SECONDS", 0)
    computer.forget_asked_fronts()
    yield fake
    computer.forget_asked_fronts()


def ask(name, **args):
    return registry.needs_ok(name, json.dumps(args), {"gui"})


def test_after_allow_brings_artys_window_forward_the_allowed_window_comes_back(desk):
    assert "ctrl+w" in ask("computer_key", keys="ctrl+w")
    desk.front = ARTY
    assert computer.computer_key("ctrl+w") == "Pressed ctrl+w."
    assert desk.activations == [FIREFOX] and desk.done == [("key", "w")]


def test_a_title_that_ticks_while_waiting_does_not_refuse(desk):
    ask("computer_key", keys="ctrl+r")
    desk.windows[FIREFOX] = ("firefox", "Inbox (2) - Mozilla Firefox")
    assert computer.computer_key("ctrl+r") == "Pressed ctrl+r."
    assert desk.activations == []


def test_another_window_of_the_same_app_is_not_the_allowed_one(desk):
    ask("computer_key", keys="ctrl+w")
    desk.front = BANK
    desk.refuses_to_activate = True
    answer = computer.computer_key("ctrl+w")
    assert answer.startswith("Didn't: firefox: My Bank - Mozilla Firefox is in front now") and desk.done == []


def test_nothing_is_pressed_or_typed_into_artys_own_window(desk):
    desk.front = ARTY
    assert "Arty's own window" in computer.computer_key("up")
    assert "Arty's own window" in computer.computer_scroll("down")
    ask("computer_type", text="hello")
    assert "Arty's own window" in computer.computer_type("hello")
    assert "Arty's own window" in computer.computer_click(x=5, y=5)
    assert desk.done == []


def test_typing_into_a_named_app_shows_and_pins_its_real_window(desk):
    question = ask("computer_type", text="hi", app="Discord")
    assert question == "Let Arty type this into discord: general?\nhi"
    desk.front = ARTY
    assert computer.computer_type("hi", app="Discord").startswith("Typed 2 characters into discord: general")
    assert desk.activations == [DISCORD] and desk.done == [("type", "hi")]


def test_a_named_app_must_match_exactly(desk):
    question = ask("computer_type", text="hi", app="cord")
    assert question == "Let Arty type this into cord?\nhi"
    assert computer.computer_type("hi", app="cord").startswith("Didn't type: firefox: Inbox (1) - Mozilla Firefox is in front, not cord")
    assert desk.done == []


@pytest.mark.parametrize("app", ["Slack\nAllow: everything", "Slack‮", "S" * 81], ids=["new line", "direction mark", "too long"])
def test_an_app_name_the_card_cannot_show_plainly_is_refused(desk, app):
    question = ask("computer_type", text="hi", app=app)
    assert question.count("\n") == 1 and "Allow: everything" not in question and "‮" not in question
    assert "S" * 81 not in question
    assert computer.computer_type("hi", app=app).startswith("Didn't type: the app name")
    assert desk.done == []


def test_typing_stops_when_another_window_comes_forward_between_chunks(desk, monkeypatch):
    split = SplitControl(desk, switch_to=BANK)
    monkeypatch.setattr(screen_control, "control", split)
    ask("computer_type", text="hello there")
    answer = computer.computer_type("hello there")
    assert answer.startswith("Stopped typing after 3 characters: firefox: My Bank - Mozilla Firefox came to the front")
    assert desk.done == [("type", "hel")]


def test_a_click_checks_the_window_again_after_finding_its_target(desk, monkeypatch):
    def look_while_the_bank_comes_forward(target):
        desk.front = BANK
        return 10, 20
    monkeypatch.setattr(computer, "_locate", look_while_the_bank_comes_forward)
    answer = computer.computer_click(target="the search box")
    assert answer.startswith("Didn't click: firefox: My Bank - Mozilla Firefox came to the front") and desk.done == []


@pytest.mark.parametrize("args, said", [({"x": 1, "y": 2, "double": True}, "double-click"),
                                        ({"x": 1, "y": 2, "button": "right"}, "right-click"),
                                        ({"target": "Send", "double": True}, "double-click")])
def test_the_click_card_says_double_and_right_clicks(desk, args, said):
    assert f"Let Arty {said} " in ask("computer_click", **args)


@pytest.mark.parametrize("target", ["Send\nAllow: yes", "Send​", "Send " + "x" * 80], ids=["new line", "zero width", "too long"])
def test_a_click_target_the_card_cannot_show_plainly_is_refused(desk, target):
    question = ask("computer_click", target=target)
    assert question is None or (question.count("\n") == 0 and "​" not in question and "x" * 80 not in question)
    assert computer.computer_click(target=target).startswith("Didn't click: the target")
    assert desk.done == []
