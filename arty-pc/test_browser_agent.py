import importlib
import io
import json
import os
import stat
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))


class FakePlaywrightError(Exception):
    pass


class FakePlaywright:
    def start(self):
        return self

    def stop(self):
        pass


def load_agent():
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError
    sync_api.sync_playwright = FakePlaywright
    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.sync_api"] = sync_api
    sys.modules.pop("browser_agent", None)
    return importlib.import_module("browser_agent")


agent = load_agent()
TOKEN = "t" * 43


def headers(**extra):
    base = {"Host": f"127.0.0.1:{agent.PORT}", "Content-Type": "application/json", agent.TOKEN_HEADER: TOKEN}
    base.update(extra)
    return {key: value for key, value in base.items() if value is not None}


class TestRequestChecks:
    def test_a_good_request_passes(self):
        assert agent.refusal(headers(), agent.PORT, TOKEN) is None

    def test_localhost_host_passes(self):
        assert agent.refusal(headers(Host=f"localhost:{agent.PORT}"), agent.PORT, TOKEN) is None

    def test_missing_token_is_refused(self):
        assert agent.refusal(headers(**{agent.TOKEN_HEADER: None}), agent.PORT, TOKEN)[0] == 401

    def test_wrong_token_is_refused(self):
        assert agent.refusal(headers(**{agent.TOKEN_HEADER: "x" * 43}), agent.PORT, TOKEN)[0] == 401

    def test_non_ascii_token_is_refused_not_raised(self):
        assert agent.refusal(headers(**{agent.TOKEN_HEADER: "é" * 43}), agent.PORT, TOKEN)[0] == 401

    def test_rebound_host_name_is_refused(self):
        assert agent.refusal(headers(Host=f"evil.example:{agent.PORT}"), agent.PORT, TOKEN)[0] == 403

    def test_another_port_in_host_is_refused(self):
        assert agent.refusal(headers(Host="127.0.0.1:80"), agent.PORT, TOKEN)[0] == 403

    def test_a_web_page_origin_is_refused(self):
        assert agent.refusal(headers(Origin="https://evil.example"), agent.PORT, TOKEN)[0] == 403

    def test_null_origin_is_refused(self):
        assert agent.refusal(headers(Origin="null"), agent.PORT, TOKEN)[0] == 403

    def test_extension_origin_only_where_allowed(self):
        extension = headers(Origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop")
        assert agent.refusal(extension, agent.PORT, TOKEN)[0] == 403
        assert agent.refusal(extension, agent.PORT, TOKEN, extension_origin_allowed=True) is None

    def test_text_plain_is_refused(self):
        assert agent.refusal(headers(**{"Content-Type": "text/plain"}), agent.PORT, TOKEN)[0] == 415

    def test_json_with_charset_passes(self):
        assert agent.refusal(headers(**{"Content-Type": "application/json; charset=utf-8"}), agent.PORT, TOKEN) is None


class TestToken:
    def test_token_is_made_once_with_owner_only_access(self, tmp_path):
        token_file = tmp_path / "token"
        first = agent.load_or_make_token(str(token_file))
        assert len(first) >= 40
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600
        assert agent.load_or_make_token(str(token_file)) == first

    def test_a_loose_existing_token_file_is_tightened(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("a" * 43)
        os.chmod(token_file, 0o644)
        assert agent.load_or_make_token(str(token_file)) == "a" * 43
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600

    def test_a_short_token_is_replaced(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("short")
        assert agent.load_or_make_token(str(token_file)) != "short"

    def test_curl_header_and_extension_copy_are_written_private(self, tmp_path):
        extension = tmp_path / "gmail-extension"
        extension.mkdir()
        header_file = tmp_path / "token.header"
        agent.share_token(TOKEN, str(header_file), str(extension / "agent-token.json"))
        assert header_file.read_text() == f"{agent.TOKEN_HEADER}: {TOKEN}\n"
        assert json.loads((extension / "agent-token.json").read_text()) == {"token": TOKEN}
        assert stat.S_IMODE(os.stat(header_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(extension / "agent-token.json").st_mode) == 0o600

    def test_no_extension_folder_means_no_extension_copy(self, tmp_path):
        agent.share_token(TOKEN, str(tmp_path / "token.header"), str(tmp_path / "missing" / "agent-token.json"))
        assert not (tmp_path / "missing").exists()


class TestGmailJobs:
    def make_window(self, monkeypatch):
        window = agent.GmailWindow()
        monkeypatch.setattr(window, "make_sure_open", lambda: None)
        monkeypatch.setattr(agent, "GMAIL_JOB_SECONDS", 0.4)
        monkeypatch.setattr(agent, "GMAIL_POLL_SECONDS", 0.3)
        return window

    def test_job_ids_cannot_be_guessed(self, monkeypatch):
        window = self.make_window(monkeypatch)
        threading.Thread(target=lambda: self.swallow(window.ask, "gmail_list", {}), daemon=True).start()
        job = window.next_job()
        assert not str(job["id"]).isdigit()
        assert len(str(job["id"])) >= 16

    def test_an_answer_for_an_unknown_job_is_refused(self, monkeypatch):
        window = self.make_window(monkeypatch)
        assert window.answer("1", {"emails": [{"subject": "forged"}]}) is False
        assert window.results == {}

    def test_a_real_answer_is_accepted_and_nothing_is_left_behind(self, monkeypatch):
        window = self.make_window(monkeypatch)
        answers = []
        asker = threading.Thread(target=lambda: answers.append(window.ask("gmail_list", {})))
        asker.start()
        job = window.next_job()
        assert window.answer(job["id"], {"emails": []}) is True
        asker.join(2)
        assert answers == [{"emails": []}]
        assert window.results == {} and window.waiting == set()

    def test_a_late_answer_after_giving_up_is_refused_and_not_kept(self, monkeypatch):
        window = self.make_window(monkeypatch)
        with pytest.raises(RuntimeError):
            window.ask("gmail_list", {})
        job = window.next_job()
        assert job is None or window.answer(job["id"], {"emails": []}) is False
        assert window.results == {} and window.waiting == set()

    def test_a_second_answer_to_a_requeued_job_is_not_kept(self, monkeypatch):
        window = self.make_window(monkeypatch)
        answers = []
        asker = threading.Thread(target=lambda: answers.append(window.ask("gmail_list", {})))
        asker.start()
        job = window.next_job()
        window.answer(job["id"], {"emails": []})
        asker.join(2)
        assert window.answer(job["id"], {"emails": ["again"]}) is False
        assert window.results == {}

    def test_jobs_given_up_on_are_not_handed_out(self, monkeypatch):
        window = self.make_window(monkeypatch)
        with pytest.raises(RuntimeError):
            window.ask("gmail_list", {})
        assert window.next_job() is None

    @staticmethod
    def swallow(function, *args):
        try:
            function(*args)
        except RuntimeError:
            pass


class FakeLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class FakePage:
    def __init__(self, url, password_field=False, challenge_frame=False):
        self.url = url
        self.password_field = password_field
        self.challenge_frame = challenge_frame

    def evaluate(self, script, *args):
        if script == agent.VISIBLE_CHALLENGE_JS:
            return self.challenge_frame
        if script == agent.VISIBLE_PASSWORD_JS:
            return self.password_field
        raise AssertionError("unexpected script")


LONG_ARTICLE = "Lorem ipsum dolor sit amet. " * 200


class TestBlockedReason:
    def test_docs_page_under_auth_path_is_not_a_login_wall(self):
        page = FakePage("https://docs.example.com/auth/overview")
        assert agent.blocked_reason(page, "Auth overview", LONG_ARTICLE) is None

    def test_a_login_page_with_a_password_field_is_a_login_wall(self):
        page = FakePage("https://example.com/login", password_field=True)
        assert agent.blocked_reason(page, "Log in", "Email Password Log in") == "login required"

    def test_google_sign_in_is_always_a_login_wall(self):
        page = FakePage("https://accounts.google.com/v3/signin/identifier")
        assert agent.blocked_reason(page, "Sign in", "Email or phone") == "login required"

    def test_a_link_that_only_mentions_google_sign_in_is_not_a_login_wall(self):
        page = FakePage("https://search.example.com/?q=accounts.google.com+help")
        assert agent.blocked_reason(page, "Results", LONG_ARTICLE) is None

    def test_a_lookalike_host_is_not_google_sign_in(self):
        page = FakePage("https://accounts.google.com.example.net/article")
        assert agent.blocked_reason(page, "Article", LONG_ARTICLE) is None

    def test_a_login_word_in_the_query_alone_is_not_a_login_wall(self):
        page = FakePage("https://shop.example.com/cart?next=/login", password_field=True)
        assert agent.blocked_reason(page, "Cart", LONG_ARTICLE) is None

    def test_an_article_mentioning_access_denied_is_not_a_bot_check(self):
        page = FakePage("https://news.example.com/story")
        text = "Access denied: how a security check failed at the airport. " + LONG_ARTICLE
        assert agent.blocked_reason(page, "News", text) is None

    def test_a_coding_challenges_page_is_not_a_bot_check(self):
        page = FakePage("https://example.com/challenges/weekly")
        assert agent.blocked_reason(page, "Weekly challenges", LONG_ARTICLE) is None

    def test_a_short_interstitial_is_a_bot_check(self):
        page = FakePage("https://example.com/")
        assert agent.blocked_reason(page, "Just a moment...", "Checking your browser before accessing example.com.") \
            == "bot check (CAPTCHA or human verification)"

    def test_a_visible_challenge_frame_is_a_bot_check_on_any_page(self):
        page = FakePage("https://example.com/", challenge_frame=True)
        assert agent.blocked_reason(page, "Shop", LONG_ARTICLE) == "bot check (CAPTCHA or human verification)"

    def test_google_sorry_page_is_a_bot_check(self):
        page = FakePage("https://www.google.com/sorry/index?continue=x")
        assert agent.blocked_reason(page, "Sorry", "Our systems have detected unusual traffic from your computer network.") \
            == "bot check (CAPTCHA or human verification)"


class FakeHandler:
    def __init__(self, handler_class, path, body, request_headers):
        self.path = path
        self.headers = request_headers
        self.rfile = io.BytesIO(body)
        self.sent = []
        self.handler_class = handler_class

    def run(self):
        instance = self.handler_class.__new__(self.handler_class)
        instance.path = self.path
        instance.headers = self.headers
        instance.rfile = self.rfile
        instance.reply = lambda status, result: self.sent.append((status, result))
        instance.do_POST()
        return self.sent


def post(handler_class, path, body, request_headers):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    merged = {**request_headers, "Content-Length": str(len(raw))}
    return FakeHandler(handler_class, path, raw, merged).run()


class TestHandler:
    def test_requests_without_the_token_never_reach_the_browser(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        monkeypatch.setattr(agent.browser, "handle", lambda *a: pytest.fail("browser was driven"))
        sent = post(agent.Handler, "/open", {"url": "https://example.com"}, headers(**{agent.TOKEN_HEADER: None}))
        assert sent[0][0] == 401

    def test_a_body_that_is_not_an_object_is_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        monkeypatch.setattr(agent.browser, "handle", lambda *a: pytest.fail("browser was driven"))
        sent = post(agent.Handler, "/open", [1, 2], headers())
        assert sent[0][0] == 400 and "object" in sent[0][1]["error"]

    def test_unexpected_errors_are_answered_not_dropped(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)

        def broken(action, args):
            raise OSError("No space left on device")
        monkeypatch.setattr(agent.browser, "handle", broken)
        sent = post(agent.Handler, "/download", {"target": "x"}, headers())
        assert sent[0][0] == 500 and "No space left" in sent[0][1]["error"]

    def test_wrong_argument_types_are_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)

        def typed(action, args):
            return args["url"].startswith("http")
        monkeypatch.setattr(agent.browser, "handle", typed)
        sent = post(agent.Handler, "/open", {"url": 5}, headers())
        assert sent[0][0] == 400

    def test_gmail_result_needs_the_token(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        gmail_headers = headers(Host=f"127.0.0.1:{agent.GMAIL_PORT}")
        gmail_headers.pop(agent.TOKEN_HEADER)
        sent = post(agent.GmailHandler, "/result", {"id": "1", "result": {}}, gmail_headers)
        assert sent[0][0] == 401

    def test_gmail_job_from_a_web_page_is_refused(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        monkeypatch.setattr(agent.gmail, "next_job", lambda: pytest.fail("a job was handed out"))
        sent = post(agent.GmailHandler, "/job", {}, headers(Host=f"127.0.0.1:{agent.GMAIL_PORT}", Origin="https://evil.example"))
        assert sent[0][0] == 403

    def test_gmail_result_for_an_unknown_job_is_404(self, monkeypatch):
        monkeypatch.setattr(agent, "AGENT_TOKEN", TOKEN)
        sent = post(agent.GmailHandler, "/result", {"id": "nope", "result": {}},
                    headers(Host=f"127.0.0.1:{agent.GMAIL_PORT}", Origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop"))
        assert sent[0][0] == 404


class TestPrivateUploads:
    def private_setup(self, tmp_path, monkeypatch):
        profile = tmp_path / ".gmail-chrome"
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_text("secret")
        monkeypatch.setattr(agent, "PRIVATE_FOLDERS", (str(profile),))
        return profile

    def test_files_in_the_browser_profiles_are_never_uploaded(self, tmp_path, monkeypatch):
        profile = self.private_setup(tmp_path, monkeypatch)
        assert agent.private_upload_reason(str(profile / "Default" / "Cookies"))

    def test_a_link_pointing_into_a_profile_is_refused_too(self, tmp_path, monkeypatch):
        profile = self.private_setup(tmp_path, monkeypatch)
        link = tmp_path / "harmless.txt"
        link.symlink_to(profile / "Default" / "Cookies")
        assert agent.private_upload_reason(str(link))

    def test_ordinary_files_can_be_uploaded(self, tmp_path, monkeypatch):
        self.private_setup(tmp_path, monkeypatch)
        ordinary = tmp_path / "cv.pdf"
        ordinary.write_text("cv")
        assert agent.private_upload_reason(str(ordinary)) is None

    def test_the_default_private_folders_cover_both_profiles_the_agent_and_ssh(self):
        names = {os.path.basename(folder) for folder in agent.PRIVATE_FOLDERS}
        assert {".browser-profile", ".gmail-chrome", "browser-agent", ".ssh"} <= names

    def test_upload_refuses_before_touching_the_page(self, tmp_path, monkeypatch):
        profile = self.private_setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent.browser, "page", lambda: pytest.fail("the page was used"))
        with pytest.raises(ValueError, match="private"):
            agent.browser.handle("upload", {"path": str(profile / "Default" / "Cookies"), "target": "1"})
