"""The window's calendar view goes through the local service to Arty's calendar on the brain."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import brain

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765", headers={"X-Arty-Token": arty_server.TOKEN})


def test_events_pass_through_and_the_day_range_is_kept_sensible(monkeypatch):
    calls = []
    monkeypatch.setattr(brain, "calendar_call", lambda method, path="", body=None: calls.append((method, path)) or (200, {"events": []}))
    assert client.get("/calendar?days=5000").json() == {"events": []}
    assert calls == [("GET", "?days=366")]


def test_adding_passes_the_brains_answer_and_status_back(monkeypatch):
    monkeypatch.setattr(brain, "calendar_call", lambda method, path="", body=None: (400, {"message": f"Couldn't add it: {body['when']}"}))
    response = client.post("/calendar", json={"title": "Dentist", "when": "blorp"})
    assert response.status_code == 400
    assert response.json() == {"message": "Couldn't add it: blorp"}


def test_removing_only_takes_an_event_number(monkeypatch):
    monkeypatch.setattr(brain, "calendar_call", lambda method, path="", body=None: (200, {"ok": True, "path": path}))
    assert client.delete("/calendar/7").json() == {"ok": True, "path": "/7"}
    assert client.delete("/calendar/..%2Fmodels").status_code in (404, 422)


def test_an_unreachable_brain_is_reported_not_hidden(monkeypatch):
    def unreachable(*args, **kwargs):
        raise brain.BrainError("Couldn't reach Arty's calendar: timed out")
    monkeypatch.setattr(brain, "calendar_call", unreachable)
    response = client.get("/calendar")
    assert response.status_code == 502
    assert "timed out" in response.json()["error"]


def test_the_calendar_needs_the_token_like_everything_else():
    assert TestClient(arty_server.app, base_url="http://127.0.0.1:8765").get("/calendar").status_code == 403
