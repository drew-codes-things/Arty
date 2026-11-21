import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import remote
import workspace_server as ws


class FakeDesktop:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def __call__(self, command, stdin=None, timeout=None):
        action = command.split(f"{ws.AGENT_URL}/", 1)[1].split()[0]
        self.sent.append((action, json.loads(stdin or b"{}")))
        answer = self.answers.pop(0)
        return 0, answer if isinstance(answer, str) else json.dumps(answer)


@pytest.fixture
def desktop(monkeypatch):
    def install(*answers):
        fake = FakeDesktop(*answers)
        monkeypatch.setattr(ws, "remote_text", fake)
        return fake
    return install


def test_an_app_call_goes_to_the_browser_agent_and_comes_back_as_json(desktop):
    fake = desktop({"app": "calendar", "origin": "https://calendar.example.org", "status": 201,
                    "body": {"calendarId": "home", "itemId": "a.ics"}, "time_zone": "Europe/London"})
    result = json.loads(ws.own_app_request("calendar", "POST", "/api/events", json.dumps({"title": "Dinner"})))
    assert result["status"] == 201 and result["body"]["itemId"] == "a.ics"
    assert result["time_zone"] == "Europe/London"
    assert fake.sent == [("app_call", {"app": "calendar", "method": "POST", "path": "/api/events",
                                       "json": {"title": "Dinner"}, "if_match": ""})]


def test_signed_out_says_where_to_sign_in_and_leads_to_ask_user(desktop):
    desktop({"app": "drive", "origin": "https://drive.example.org", "signed_out": True})
    result = ws.own_app_request("drive", "GET", "/api/folders/0")
    assert result.startswith("BLOCKED:")
    assert "https://drive.example.org" in result and "ask_user" in result
    assert "Do not try to sign in yourself" in result
    assert "sign in to starfire on your desktop" in result
    assert "'Sign in to Starfire apps'" in result and "Sign in to websites" not in result


def test_an_unknown_app_or_bad_json_never_reaches_the_desktop(desktop):
    fake = desktop()
    assert "app must be one of" in ws.own_app_request("photos", "GET", "/api/x")
    assert ws.own_app_request("drive", "POST", "/api/shares", "{not json") == "body_json must be JSON"
    assert fake.sent == []


def test_an_agent_refusal_is_a_browser_error(desktop):
    desktop({"error": "POST /api/app-passwords isn't a call Arty makes to the calendar app"})
    assert ws.own_app_request("calendar", "POST", "/api/app-passwords").startswith("browser error:")


def test_upload_by_bytes_or_desktop_path_and_never_from_a_private_folder(desktop):
    fake = desktop({"app": "drive", "origin": "https://drive.example.org", "status": 200, "body": {"id": 41}},
                   {"app": "drive", "origin": "https://drive.example.org", "status": 200, "body": {"id": 42}})
    assert json.loads(ws.drive_upload("a.txt", 5, data_base64="aGk="))["body"]["id"] == 41
    assert json.loads(ws.drive_upload("b.txt", path="~/Documents/Arty/b.txt"))["body"]["id"] == 42
    assert fake.sent[0] == ("app_upload", {"name": "a.txt", "parent_id": 5, "data_base64": "aGk="})
    assert fake.sent[1][1]["path"].endswith("Documents/Arty/b.txt")
    assert ws.drive_upload("c", path="~/.browser-profile/Default/Cookies") == remote.PRIVATE_REFUSAL
    assert ws.drive_upload("d") == "give either data_base64 or path"
    assert len(fake.sent) == 2


def test_gmail_send_passes_the_approved_email_and_reports_how_it_was_confirmed(desktop):
    fake = desktop({"sent": True, "confirmed": "Gmail said the message was sent"})
    result = json.loads(ws.gmail_send("sam@example.org, jo@example.org", "Late", "Running late.", cc=""))
    assert result == {"sent": True, "confirmed": "Gmail said the message was sent"}
    assert fake.sent == [("gmail_send", {"to": ["sam@example.org", "jo@example.org"], "cc": [], "subject": "Late",
                                         "body": "Running late."})]


def test_gmail_send_that_did_not_go_is_never_called_sent(desktop):
    desktop({"error": "the To addresses weren't the approved ones, so nothing was sent"})
    assert ws.gmail_send("sam@example.org", "x", "y").startswith("browser error:")
    desktop({"blocked": "the Gmail window on my desktop isn't signed in to Google"})
    blocked = ws.gmail_send("sam@example.org", "x", "y")
    assert blocked.startswith("BLOCKED:") and "ask_user" in blocked


def test_gmail_message_gives_the_newest_sender_for_a_reply(desktop):
    desktop({"subject": "Dinner?", "messages": [
        {"sender": "Me", "email": "drew@example.org", "when": "Mon", "body": "x"},
        {"sender": "Sam", "email": "sam@example.org", "when": "Tue", "body": "Are you free?"}]})
    assert json.loads(ws.gmail_message("from:sam", 1)) == {"subject": "Dinner?", "sender": "Sam",
                                                            "email": "sam@example.org", "when": "Tue"}


def test_the_new_tools_say_they_are_not_for_the_model_to_call():
    for tool in ("own_app_request", "drive_upload", "gmail_send", "gmail_message"):
        described = ws.mcp._tool_manager.get_tool(tool).description
        assert described.startswith(ws.ONLY_FOR_APP_ACTIONS)
