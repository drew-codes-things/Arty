import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process.tools import computer, portal, session

pytestmark = pytest.mark.usefixtures("linux")


def asks_before_mouse_and_keyboard(monkeypatch, on=True):
    real = computer.setting
    monkeypatch.setattr(computer, "setting", lambda section, key, default=None:
                        on if (section, key) == ("computer", "ask_before_mouse_and_keyboard") else real(section, key, default))


def test_opening_an_app_with_a_new_document_asks_like_any_key_press(monkeypatch):
    asks_before_mouse_and_keyboard(monkeypatch)
    assert computer.needs_ok("computer_open", {"app": "kate", "new_document": True}, set())
    assert computer.needs_ok("computer_open", {"app": "kate", "new_document": True}, {"gui"}) is None
    assert computer.needs_ok("computer_open", {"app": "kate"}, set()) is None
    assert computer.consent_key("computer_open") == "gui"


def test_scrolling_in_a_secrets_app_is_refused(monkeypatch):
    monkeypatch.setattr(computer, "can_control", lambda: True)
    monkeypatch.setattr(session, "front_window", lambda: ("KeePassXC", "Passwords.kdbx"))
    assert "passwords live" in computer.computer_scroll("down", 3)


def failing_start(error):
    def start(connection):
        raise error
    return start


class FakeConnection:
    def close(self):
        pass


@pytest.fixture
def portal_without_desktop(monkeypatch):
    forgotten = []
    monkeypatch.setattr(portal, "open_dbus_connection", lambda bus: FakeConnection(), raising=False)
    monkeypatch.setattr(portal, "_forget_token", lambda: forgotten.append(True))
    monkeypatch.setattr(portal, "_open", {"connection": None, "session": None, "stream": None, "size": None})
    return forgotten


def test_an_unanswered_permission_prompt_keeps_the_saved_permission(monkeypatch, portal_without_desktop):
    monkeypatch.setattr(portal, "_start_session", failing_start(portal.PortalError("The desktop's permission prompt wasn't answered in time.")))
    with pytest.raises(portal.PortalError):
        portal._session()
    assert portal_without_desktop == []


def test_a_refusal_forgets_the_saved_permission(monkeypatch, portal_without_desktop):
    monkeypatch.setattr(portal, "_start_session", failing_start(portal.PortalRefused("The desktop refused Start (code 2).")))
    with pytest.raises(portal.PortalError):
        portal._session()
    assert portal_without_desktop == [True]
