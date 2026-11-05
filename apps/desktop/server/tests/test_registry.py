import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process.tools import registry


def test_coding_requests_get_the_work_tools(monkeypatch):
    monkeypatch.setattr(registry, "_last_used", {})
    for request in ["fix the bug in my marina project", "why is the build failing?", "refactor the login function"]:
        assert registry.WORK_TOOLS <= registry.wanted_tools(request), request


def test_follow_ups_keep_the_tools_used_recently(monkeypatch):
    monkeypatch.setattr(registry, "_last_used", {})
    assert not registry.WORK_TOOLS & registry.wanted_tools("yes, go ahead")
    monkeypatch.setattr(registry, "_last_used", {"computer_edit_file": registry.time.time()})
    assert registry.WORK_TOOLS <= registry.wanted_tools("yes, go ahead")
    monkeypatch.setattr(registry, "_last_used", {"computer_edit_file": registry.time.time() - registry.STICKY_SECONDS - 1})
    assert not registry.WORK_TOOLS & registry.wanted_tools("yes, go ahead")
