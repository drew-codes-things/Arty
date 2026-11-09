import pytest


@pytest.fixture
def linux(monkeypatch):
    from process.tools import shell

    monkeypatch.setattr(shell.sys, "platform", "linux")
