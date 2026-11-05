import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import workspace_server as ws


@pytest.mark.parametrize("command, url", [
    ("firefox-esr https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
    ("/usr/bin/chromium --new-window http://example.com", "http://example.com"),
    ("xdg-open 'https://example.com/a b'", "https://example.com/a b"),
    ("firefox-esr", None),
    ("mousepad https://not-a-browser.example", None),
    ("firefox 'unbalanced", None),
])
def test_website_in(command, url):
    assert ws.website_in(command) == url


def test_open_app_sends_websites_to_the_visible_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(ws, "_browser", lambda action, **kw: calls.append((action, kw)) or "YouTube | https://www.youtube.com/")
    monkeypatch.setattr(ws, "_gui", lambda command: pytest.fail("should not launch a separate browser"))
    result = ws.open_app("firefox-esr https://www.youtube.com/")
    assert calls == [("open", {"url": "https://www.youtube.com/"})]
    assert "visible browser" in result


def test_open_app_still_starts_other_apps(monkeypatch):
    launched = []
    monkeypatch.setattr(ws, "_gui", lambda command: launched.append(command) or "ok")
    assert ws.open_app("thunar") == "ok" and "thunar" in launched[0]


def test_bare_browser_command_points_at_chrome(monkeypatch):
    monkeypatch.setattr(ws, "_gui", lambda command: pytest.fail("should not launch another browser"))
    assert "Google Chrome" in ws.open_app("firefox-esr")
    assert "Google Chrome" in ws.open_app("chromium")


def test_first_youtube_video_skips_ads_and_shelves():
    html = '{"adSlot":{"videoId":"AAAAAAAAAAA"}} ... {"videoRenderer":{"videoId":"r7qovpFAGrQ","thumbnail":{}}}'
    assert ws.first_youtube_video(html) == "r7qovpFAGrQ"
    assert ws.first_youtube_video("<html>consent</html>") is None


class FakeResults:
    def __init__(self, html):
        self.html = html.encode()

    def __enter__(self):
        return self

    def __exit__(self, *details):
        return False

    def read(self):
        return self.html


def test_youtube_play_opens_the_first_video_and_reports_the_player(monkeypatch):
    calls = []
    monkeypatch.setattr(ws.urllib.request, "urlopen",
                        lambda request, timeout: FakeResults('{"videoRenderer":{"videoId":"r7qovpFAGrQ"}}'))

    def browser(action, **kw):
        calls.append((action, kw))
        if action == "open":
            return "Old Town Road - YouTube | https://www.youtube.com/watch?v=r7qovpFAGrQ"
        return "Old Town Road - YouTube | https://www.youtube.com/watch?v=r7qovpFAGrQ\n\nPlayer: playing, at 0:02 of 2:37\n\nClickable/typeable items:\n1. Like"

    monkeypatch.setattr(ws, "_browser", browser)
    result = ws.youtube_play("  old town road ")
    assert calls[0] == ("open", {"url": "https://www.youtube.com/watch?v=r7qovpFAGrQ"})
    assert calls[1] == ("play_media", {})
    assert "Player: playing" in result
    assert "Clickable" not in result


def test_youtube_play_stops_when_nothing_is_found_or_the_browser_is_down(monkeypatch):
    monkeypatch.setattr(ws.urllib.request, "urlopen", lambda request, timeout: FakeResults("no results"))
    monkeypatch.setattr(ws, "_browser", lambda action, **kw: pytest.fail("should not open the browser"))
    assert "found no video" in ws.youtube_play("zzzz")
    assert ws.youtube_play(" ") == "Say what to play."
    monkeypatch.setattr(ws.urllib.request, "urlopen", lambda request, timeout: FakeResults('"videoRenderer":{"videoId":"r7qovpFAGrQ"'))
    monkeypatch.setattr(ws, "_browser", lambda action, **kw: "The browser isn't running on the desktop.")
    assert ws.youtube_play("x").startswith("The browser isn't running")


def test_desktop_actions_report_the_front_window(monkeypatch):
    commands = []
    monkeypatch.setattr(ws, "remote_text", lambda command, timeout: commands.append(command) or (0, "Old Town Road - YouTube\n"))
    assert ws.press_keys("Return") == "done. The window in front is now: Old Town Road - YouTube"
    assert "{ xdotool key --delay 60 Return\n}" in commands[0]
    monkeypatch.setattr(ws, "remote_text", lambda command, timeout: (1, "Can't open display"))
    assert ws.press_keys("Return") == "error: Can't open display"


VTT = """WEBVTT
Kind: captions
Language: en

00:00:13.113 --> 00:00:17.150
<c>Yeah, I'm gonna take</c> my horse&nbsp;

00:00:17.150 --> 00:00:20.854 align:start
Yeah, I'm gonna take my horse
to the old town road
"""


def test_transcript_text_drops_timings_tags_and_rolling_repeats():
    assert ws.transcript_text(VTT) == "Yeah, I'm gonna take my horse\nto the old town road"


def test_youtube_info_reads_details_and_saves_the_transcript(monkeypatch):
    commands = []
    details = json.dumps({"title": "Old Town Road", "channel": "Lil Nas X", "duration_string": "2:37",
                          "upload_date": "20190516", "view_count": 1234567, "description": "Official video"})

    def remote(command, stdin=None, timeout=None):
        commands.append((command, stdin))
        return (0, f"{details}\n@@CAPTIONS@@\n{VTT}") if "yt-dlp" in command else (0, "")

    monkeypatch.setattr(ws, "remote_text", remote)
    result = ws.youtube_info("https://youtu.be/r7qovpFAGrQ")
    assert "Old Town Road | https://youtu.be/r7qovpFAGrQ" in result
    assert "Uploaded: 2019-05-16 | Views: 1,234,567" in result
    assert "to the old town road" in result
    saved = [c for c in commands if c[0].startswith("mkdir")]
    assert saved and saved[0][1].endswith(b"old town road")
    assert "Old-Town-Road-transcript.txt" in saved[0][0]


def test_youtube_info_without_captions_or_with_a_broken_video(monkeypatch):
    details = json.dumps({"title": "Silent film"})
    monkeypatch.setattr(ws, "remote_text", lambda command, stdin=None, timeout=None: (0, f"{details}\n@@CAPTIONS@@\n"))
    assert "no captions" in ws.youtube_info("https://www.youtube.com/watch?v=abcdefghijk")
    monkeypatch.setattr(ws, "remote_text", lambda command, stdin=None, timeout=None: (1, "ERROR: Video unavailable"))
    assert ws.youtube_info("https://www.youtube.com/watch?v=abcdefghijk") == "Couldn't read that video: ERROR: Video unavailable"
