import base64
import json
import os
import shutil
import subprocess
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from test_browser_agent import TOKEN, FakeChrome, FakeContext, FakeTab, agent, headers, post

NODE = shutil.which("node")
SESSION_COOKIE = "session=signed-in"
CSRF = "csrf-from-the-app"
SIGN_IN_PAGE = "https://sign-in.example/?next=app"
RUN_IN_PAGE = """
const chunks = [];
process.stdin.on('data', (chunk) => chunks.push(chunk));
process.stdin.on('end', async () => {
  const {origin, cookie, script, args} = JSON.parse(Buffer.concat(chunks).toString());
  const realFetch = globalThis.fetch;
  globalThis.fetch = (path, options = {}) => realFetch(new URL(path, origin),
    {...options, headers: {...(options.headers || {}), cookie, origin}});
  const run = eval(script);
  process.stdout.write(JSON.stringify(await run(args)));
});
"""


class FakeApp:
    def __init__(self):
        self.signed_in = True
        self.slow_seconds = 0
        self.requests = []
        self.uploads = {}
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.server = server
        self.origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status, body=None, location=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                app.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": raw})
                if self.headers.get("Cookie") != SESSION_COOKIE or not app.signed_in:
                    return self.answer(303, location=SIGN_IN_PAGE)
                if self.path == "/api/health":
                    return self.answer(200, {"ok": True})
                if self.path == "/api/session":
                    return self.answer(200, {"user": "drew", "csrfToken": CSRF, "timeZone": "Europe/London",
                                             "sharesEnabled": True})
                if self.command != "GET":
                    if self.headers.get("Origin") != app.origin:
                        return self.answer(403, {"error": "this request came from another site"})
                    if self.headers.get("X-CSRF-Token") != CSRF:
                        return self.answer(403, {"error": "missing or wrong CSRF token, reload the page"})
                return app.route(self, raw)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

        return Handler

    def route(self, handler, raw):
        path = urlsplit(handler.path).path
        time.sleep(self.slow_seconds)
        if path == "/api/uploads" and handler.command == "POST":
            self.uploads["up1"] = {"meta": json.loads(raw), "data": b""}
            return handler.answer(201, {"id": "up1", "chunkBytes": 4})
        if path == "/api/uploads/up1" and handler.command == "PUT":
            self.uploads["up1"]["data"] += raw
            return handler.answer(200, {"received": len(self.uploads["up1"]["data"])})
        if path == "/api/uploads/up1/finish":
            meta = self.uploads["up1"]["meta"]
            return handler.answer(200, {"id": 41, "name": meta["name"], "parentId": meta["parentId"], "kind": "file"})
        if path.startswith("/api/calendars/"):
            return handler.answer(200, {"calendarId": "home", "itemId": "a.ics", "etag": '"e2"'})
        return handler.answer(200, {"items": []})


def run_in_node(origin, script, args):
    payload = json.dumps({"origin": origin, "cookie": SESSION_COOKIE, "script": script, "args": args}).encode()
    done = subprocess.run([NODE, "-e", RUN_IN_PAGE], input=payload, capture_output=True, timeout=30, check=True)
    return json.loads(done.stdout)


class PageTab(FakeTab):
    def goto(self, url, wait_until=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        request = urllib.request.Request(url, headers={"Cookie": SESSION_COOKIE})
        try:
            with opener.open(request, timeout=10):
                self.url = url
        except urllib.error.HTTPError as error:
            self.url = error.headers.get("Location") or url

    def evaluate(self, script, *args):
        if script != agent.IN_PAGE_REQUEST_JS:
            return super().evaluate(script, *args)
        parts = urlsplit(self.url)
        return run_in_node(f"{parts.scheme}://{parts.netloc}", script, args[0])


class PageContext(FakeContext):
    def new_page(self):
        tab = PageTab(self)
        self.pages.append(tab)
        for handler in self.handlers.get("page", []):
            handler(tab)
        return tab


@pytest.fixture
def app(tmp_path, monkeypatch):
    if NODE is None:
        pytest.skip("node is needed to run the in-page request")
    served = FakeApp()
    listed = tmp_path / "own-apps.json"
    listed.write_text(json.dumps({"drive": served.origin, "calendar": served.origin}))
    monkeypatch.setattr(agent, "OWN_APPS_FILE", str(listed))
    yield served
    served.stop()


class AppsChrome:
    def __init__(self):
        self.launches = []
        self.contexts = []

    def launch(self, user_data_dir, **options):
        apps = PageContext(open_tabs=0)
        apps.closed = False

        def close():
            apps.closed = True
        apps.close = close
        self.launches.append((user_data_dir, options))
        self.contexts.append(apps)
        return apps


@pytest.fixture
def apps_chrome():
    return AppsChrome()


@pytest.fixture
def browser(monkeypatch, apps_chrome, tmp_path):
    context = PageContext()
    chrome = FakeChrome(context)
    driven = agent.Browser()
    driven.playwright = types.SimpleNamespace(
        chromium=types.SimpleNamespace(connect_over_cdp=lambda address: chrome,
                                       launch_persistent_context=apps_chrome.launch), stop=lambda: None)
    monkeypatch.setattr(agent, "chrome_listening", lambda: True)
    monkeypatch.setattr(agent, "APPS_SIGNING_IN_FILE", str(tmp_path / "apps-signing-in"), raising=False)
    return driven, context


def app_requests(app, method=None):
    return [request for request in app.requests if request["path"] not in ("/api/health", "/api/session")
            and (method is None or request["method"] == method)]


class TestAppCalls:
    def test_a_write_carries_the_session_and_the_csrf_token_but_the_answer_never_holds_the_token(self, app, browser):
        driven, context = browser
        working = context.pages[-1]
        answer = driven.handle("app_call", {"app": "calendar", "method": "PATCH", "path": "/api/calendars/home/events/a.ics",
                                            "json": {"start": "2026-11-21T15:00"}, "if_match": '"e1"'})
        assert answer["status"] == 200 and answer["body"]["etag"] == '"e2"'
        assert answer["time_zone"] == "Europe/London"
        assert CSRF not in json.dumps(answer)
        sent = app_requests(app, "PATCH")[0]
        assert sent["headers"]["X-CSRF-Token"] == CSRF
        assert sent["headers"]["If-Match"] == '"e1"'
        assert json.loads(sent["body"]) == {"start": "2026-11-21T15:00"}
        assert context.pages == [working]

    def test_a_read_sends_no_csrf_token(self, app, browser):
        driven, _ = browser
        driven.handle("app_call", {"app": "calendar", "method": "GET", "path": "/api/events?from=2026-11-20&to=2026-11-27"})
        assert "X-CSRF-Token" not in app_requests(app, "GET")[0]["headers"]

    def test_signed_out_is_a_clear_answer_and_nothing_is_sent(self, app, browser):
        app.signed_in = False
        driven, context = browser
        working = context.pages[-1]
        answer = driven.handle("app_call", {"app": "drive", "method": "POST", "path": "/api/shares", "json": {"nodeId": 1}})
        assert answer == {"app": "drive", "origin": app.origin, "signed_out": True}
        assert app_requests(app, "POST") == []
        assert context.pages == [working]

    def test_a_session_that_ends_between_the_page_and_the_call_is_signed_out_too(self, app, browser, monkeypatch):
        driven, _ = browser
        original = PageTab.goto

        def sign_out_after_landing(tab, url, wait_until=None):
            original(tab, url, wait_until)
            app.signed_in = False
        monkeypatch.setattr(PageTab, "goto", sign_out_after_landing)
        answer = driven.handle("app_call", {"app": "drive", "method": "POST", "path": "/api/nodes/trash", "json": {"ids": [1]}})
        assert answer.get("signed_out") is True
        assert app_requests(app, "POST") == []

    @pytest.mark.parametrize("method, path", [
        ("POST", "/api/app-passwords"),
        ("GET", "/api/arty/link"),
        ("POST", "/api/nodes/delete"),
        ("POST", "/api/bin/empty"),
        ("POST", "/api/sync/connections"),
        ("GET", "/api/calendars/../app-passwords"),
        ("DELETE", "/api/calendars/home/events/%2e%2e"),
        ("GET", "/api/calendars/home/events/..%2F..%2Fapp-passwords"),
        ("GET", "/api/calendars/home%5C..%5Capp-passwords/events/a.ics"),
        ("DELETE", "/api/calendars/home/events/a.ics%00"),
        ("DELETE", "/api/calendars/home/events/a.ics%0a"),
        ("GET", "/api/calendars/home/events/%252e%252e"),
        ("GET", "/api/calendars/home/events/a%zz"),
        ("GET", "/api/calendars/home/events/a%2"),
        ("PUT", "/api/uploads/up1?offset=0"),
        ("GET", "https://evil.example/api/folders/0"),
    ])
    def test_only_the_calls_arty_needs_reach_the_app(self, app, browser, method, path):
        driven, _ = browser
        app_name = "calendar" if "calendars" in path or "arty" in path or "passwords" in path else "drive"
        with pytest.raises(ValueError, match="isn't a call Arty makes"):
            driven.handle("app_call", {"app": app_name, "method": method, "path": path})
        assert app.requests == []

    @pytest.mark.parametrize("path", ["/api/calendars/home/events/a.ics", "/api/calendars/work%20diary/events/b%40c.ics",
                                      "/api/calendars/home/events/caf%C3%A9.ics"])
    def test_plain_or_properly_escaped_names_are_still_allowed(self, path):
        assert agent.checked_call("calendar", "GET", path) == ("GET", path)

    def test_an_app_that_never_answers_is_given_up_on_rather_than_holding_the_browser(self, app, browser, monkeypatch):
        monkeypatch.setattr(agent, "IN_PAGE_FETCH_MS", 400, raising=False)
        app.slow_seconds = 3
        driven, _ = browser
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="didn't answer the browser"):
            driven.handle("app_call", {"app": "calendar", "method": "GET", "path": "/api/calendars/home/events/a.ics"})
        assert time.monotonic() - started < 2.5

    def test_an_app_without_an_address_on_this_desktop_is_refused(self, tmp_path, monkeypatch, browser):
        monkeypatch.setattr(agent, "OWN_APPS_FILE", str(tmp_path / "missing.json"))
        driven, _ = browser
        with pytest.raises(ValueError, match="DRIVE_ADDRESS"):
            driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})

    @pytest.mark.parametrize("origin", ["http://drive.example.org", "https://drive.example.org/app",
                                        "https://user:pw@drive.example.org", "https://drive.example.org?x=1", "ftp://x"])
    def test_only_a_plain_https_origin_is_accepted(self, tmp_path, origin):
        listed = tmp_path / "own-apps.json"
        listed.write_text(json.dumps({"drive": origin}))
        with pytest.raises(ValueError):
            agent.own_app_origin("drive", str(listed))

    def test_the_handler_needs_the_token_before_any_app_call(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        monkeypatch.setattr(agent.browser, "handle", lambda *a: pytest.fail("browser was driven"))
        sent = post(agent.Handler, "/app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"},
                    headers(**{agent.TOKEN_HEADER: None}))
        assert sent[0][0] == 401

    def test_a_closed_browser_is_not_retried_for_app_calls(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        calls = []

        def closed(action, args):
            calls.append(action)
            raise agent.PlaywrightError("Target page, context or browser has been closed")
        monkeypatch.setattr(agent.browser, "handle", closed)
        sent = post(agent.Handler, "/app_call", {"app": "drive", "method": "POST", "path": "/api/shares"}, headers())
        assert sent[0][0] == 400 and calls == ["app_call"]


class TestTheAppsBrowserIsSeparate:
    def test_app_calls_run_in_their_own_profile_and_never_touch_arty_s_browser(self, app, browser, apps_chrome):
        driven, context = browser
        tabs_before = list(context.pages)
        answer = driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})
        assert answer["status"] == 200
        assert context.pages == tabs_before and all(tab.url == "https://www.example.org/" for tab in context.pages)
        [(profile, options)] = apps_chrome.launches
        assert profile == agent.APPS_PROFILE != agent.PROFILE
        assert options["headless"] is True and options["chromium_sandbox"] is True
        assert not any("remote-debugging" in argument for argument in options.get("args", []))
        assert apps_chrome.contexts[0].closed

    def test_uploads_use_the_apps_profile_too(self, app, browser, apps_chrome):
        driven, context = browser
        driven.handle("app_upload", {"name": "a.txt", "data_base64": base64.b64encode(b"abc").decode()})
        assert [profile for profile, _ in apps_chrome.launches] == [agent.APPS_PROFILE]
        assert apps_chrome.contexts[0].closed and len(context.pages) == 1

    def test_the_apps_browser_closes_even_when_the_page_fails(self, app, browser, apps_chrome, monkeypatch):
        driven, _ = browser

        def broken(tab, url, wait_until=None):
            raise agent.PlaywrightError("net::ERR_NAME_NOT_RESOLVED")
        monkeypatch.setattr(PageTab, "goto", broken)
        with pytest.raises(agent.PlaywrightError):
            driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})
        assert apps_chrome.contexts[0].closed

    def test_app_calls_wait_while_the_apps_sign_in_window_is_open(self, app, browser, apps_chrome):
        driven, _ = browser
        with open(agent.APPS_SIGNING_IN_FILE, "w") as flag:
            flag.write(str(os.getpid()))
        with pytest.raises(RuntimeError, match="sign-in window is open"):
            driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})
        assert apps_chrome.launches == [] and app.requests == []

    def test_a_sign_in_flag_left_by_a_closed_window_does_not_block(self, app, browser, apps_chrome):
        driven, _ = browser
        gone = subprocess.Popen(["true"])
        gone.wait()
        with open(agent.APPS_SIGNING_IN_FILE, "w") as flag:
            flag.write(str(gone.pid))
        assert driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})["status"] == 200


class TestDriveUpload:
    def test_bytes_go_up_in_chunks_inside_the_page_and_the_new_file_comes_back(self, app, browser, monkeypatch):
        monkeypatch.setattr(agent, "SMALLEST_UPLOAD_CHUNK_BYTES", 1)
        driven, context = browser
        data = b"milk, eggs, bread"
        answer = driven.handle("app_upload", {"name": "shopping.txt", "parent_id": 5,
                                              "data_base64": base64.b64encode(data).decode()})
        assert answer["status"] == 200 and answer["body"]["id"] == 41
        assert app.uploads["up1"]["data"] == data
        assert app.uploads["up1"]["meta"] == {"name": "shopping.txt", "size": len(data), "parentId": 5}
        puts = app_requests(app, "PUT")
        assert len(puts) == 5 and all(put["headers"]["X-CSRF-Token"] == CSRF for put in puts)
        assert len(context.pages) == 1

    def test_a_file_on_the_desktop_is_read_by_path(self, app, browser, tmp_path):
        driven, _ = browser
        made = tmp_path / "report.txt"
        made.write_bytes(b"report")
        driven.handle("app_upload", {"name": "report.txt", "path": str(made)})
        assert app.uploads["up1"]["data"] == b"report"

    def test_a_private_file_is_refused_before_the_browser_is_used(self, app, tmp_path, monkeypatch):
        profile = tmp_path / ".browser-profile"
        profile.mkdir()
        (profile / "Cookies").write_text("secret")
        monkeypatch.setattr(agent, "PRIVATE_FOLDERS", (str(profile),))
        monkeypatch.setattr(agent.browser, "page", lambda: pytest.fail("the page was used"))
        with pytest.raises(ValueError, match="private"):
            agent.browser.handle("app_upload", {"name": "x", "path": str(profile / "Cookies")})
        assert app.requests == []

    @pytest.mark.parametrize("name", ["", "../x", "a/b", "x" * 300])
    def test_a_name_with_folders_or_too_long_is_refused(self, name):
        with pytest.raises(ValueError):
            agent.checked_file_name(name)

    def test_too_big_is_refused(self, monkeypatch):
        monkeypatch.setattr(agent, "MOST_UPLOAD_BYTES", 3)
        with pytest.raises(ValueError, match="at most"):
            agent.upload_bytes({"data_base64": base64.b64encode(b"four").decode()})


class TestGmailSendRunsOnce:
    def make_window(self, monkeypatch):
        window = agent.GmailWindow()
        monkeypatch.setattr(window, "make_sure_open", lambda: None)
        monkeypatch.setattr(agent, "GMAIL_SEND_SECONDS", 0.4)
        monkeypatch.setattr(agent, "GMAIL_POLL_SECONDS", 0.2)
        return window

    def test_a_send_that_gets_no_answer_is_never_handed_out_again(self, monkeypatch):
        window = self.make_window(monkeypatch)
        handed = []
        failures = []

        def ask():
            try:
                window.ask(agent.GMAIL_SEND, {"to": ["sam@example.org"]})
            except RuntimeError as error:
                failures.append(str(error))
        asker = threading.Thread(target=ask)
        asker.start()
        handed.append(window.next_job())
        asker.join(2)
        handed.append(window.next_job())
        assert handed[0]["action"] == agent.GMAIL_SEND
        assert handed[1] is None
        assert "look in Sent" in failures[0]

    def test_a_send_the_gmail_window_never_picks_up_is_reported_not_sent_and_never_handed_out_later(self, monkeypatch):
        window = self.make_window(monkeypatch)
        monkeypatch.setattr(agent, "GMAIL_SEND_SECONDS", 30)
        monkeypatch.setattr(agent, "GMAIL_SEND_HAND_OUT_SECONDS", 0.3, raising=False)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="was not sent"):
            window.ask(agent.GMAIL_SEND, {"to": ["sam@example.org"]})
        assert time.monotonic() - started < 5
        assert window.next_job() is None

    def test_a_send_picked_up_in_time_still_waits_for_its_answer(self, monkeypatch):
        window = self.make_window(monkeypatch)
        monkeypatch.setattr(agent, "GMAIL_SEND_SECONDS", 5)
        monkeypatch.setattr(agent, "GMAIL_SEND_HAND_OUT_SECONDS", 1, raising=False)
        answers = []
        asker = threading.Thread(target=lambda: answers.append(window.ask(agent.GMAIL_SEND, {"to": ["sam@example.org"]})))
        asker.start()
        job = window.next_job()
        time.sleep(1.5)
        assert window.answer(job["id"], {"sent": True})
        asker.join(5)
        assert answers == [{"sent": True}]

    def test_a_send_whose_hand_over_failed_is_not_put_back(self, monkeypatch):
        window = self.make_window(monkeypatch)
        window.waiting.add("j1")
        window.put_back({"id": "j1", "action": agent.GMAIL_SEND, "args": {}, "expires": 9e99})
        assert window.jobs.empty()
        window.put_back({"id": "j1", "action": "gmail_list", "args": {}, "expires": 9e99})
        assert not window.jobs.empty()

    def test_send_goes_to_the_gmail_window_not_the_driven_browser(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        monkeypatch.setattr(agent.browser, "handle", lambda *a: pytest.fail("browser was driven"))
        monkeypatch.setattr(agent.gmail, "ask", lambda action, body: {"sent": True, "action": action})
        sent = post(agent.Handler, "/gmail_send", {"to": ["sam@example.org"]}, headers())
        assert sent[0] == (200, {"sent": True, "action": agent.GMAIL_SEND})


def test_the_helper_never_leaves_a_tab_behind_when_the_page_fails(app, browser, monkeypatch):
    driven, context = browser
    working = context.pages[-1]

    def broken(tab, url, wait_until=None):
        raise agent.PlaywrightError("net::ERR_NAME_NOT_RESOLVED")
    monkeypatch.setattr(PageTab, "goto", broken)
    with pytest.raises(agent.PlaywrightError):
        driven.handle("app_call", {"app": "drive", "method": "GET", "path": "/api/folders/0"})
    assert context.pages == [working]
