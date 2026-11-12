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


class FakeElement:
    def __init__(self, selector, kind, label, stable=True):
        self.selector = selector
        self.kind = kind
        self.label = label
        self.stable = stable


class FakeElements:
    def __init__(self, page, matches):
        self.page = page
        self.matches = list(matches)

    def count(self):
        return len(self.matches)

    @property
    def first(self):
        return FakeElements(self.page, self.matches[:1])

    def or_(self, other):
        return FakeElements(self.page, self.matches + [found for found in other.matches if found not in self.matches])

    def evaluate(self, script, argument=None):
        element = self.matches[0]
        if script == agent.STABLE_SELECTOR_JS:
            return {"selector": element.selector, "kind": element.kind} if element.stable else None
        if script == agent.MATCHES_STEP_JS:
            step, kind = argument
            return element.kind == kind and step in element.label.lower()
        raise AssertionError("unexpected element script")

    def click(self):
        if not self.matches:
            raise FakePlaywrightError("no element")
        if self.page.broken_clicks:
            raise FakePlaywrightError("click timed out")
        self.page.clicked.append(self.matches[0].selector)


class FakeSelectorPage:
    def __init__(self, url, elements, password_field=False, challenge_frame=False, card_field=False):
        self.url = url
        self.elements = elements
        self.password_field = password_field
        self.challenge_frame = challenge_frame
        self.card_field = card_field
        self.clicked = []
        self.broken_clicks = False
        self.frames = []

    def evaluate(self, script, *args):
        if script == agent.VISIBLE_CHALLENGE_JS:
            return self.challenge_frame
        if script == agent.VISIBLE_PASSWORD_JS:
            return self.password_field
        if script == agent.CARD_FIELD_JS:
            return self.card_field
        if script == agent.LIST_ELEMENTS_JS:
            return []
        raise AssertionError("unexpected page script")

    def matching(self, text):
        return FakeElements(self, [element for element in self.elements if text.lower() in element.label.lower()])

    def locator(self, selector):
        return FakeElements(self, [element for element in self.elements if element.selector == selector])

    def get_by_role(self, role, name):
        return FakeElements(self, [element for element in self.matching(name).matches if element.kind.startswith(role)])

    def get_by_label(self, text):
        return FakeElements(self, [])

    def get_by_placeholder(self, text):
        return FakeElements(self, [])

    def get_by_text(self, text):
        return self.matching(text)

    def wait_for_timeout(self, milliseconds):
        pass

    def wait_for_load_state(self, state):
        pass

    def inner_text(self, selector):
        return LONG_ARTICLE

    def title(self):
        return "Fixture shop"


SHOP = "http://shop.test/search"
FIND = FakeElement("form#search > button:nth-of-type(1)", "button:submit", "Find")


@pytest.fixture
def shop_browser(tmp_path, monkeypatch):
    browser = agent.Browser()
    browser.selectors = agent.SelectorCache(str(tmp_path / "selectors.json"))
    browser.current = None
    monkeypatch.setattr(browser, "page", lambda: browser.current)
    return browser


def click_on(browser, page, target="Find"):
    browser.current = page
    return browser.handle("click", {"target": target})


class TestSavedSelectors:
    def test_a_step_that_worked_is_remembered_for_the_site(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        assert shop_browser.selectors.get("shop.test", "find") == {"selector": FIND.selector, "kind": "button:submit"}

    def test_the_saved_selector_is_tried_first_next_time(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        decoy = FakeElement("header > button:nth-of-type(2)", "button:submit", "Find stores")
        later = FakeSelectorPage("http://shop.test/other", [decoy, FIND])
        result = click_on(shop_browser, later)
        assert later.clicked == [FIND.selector]
        assert result["used_saved_selector"] is True

    def test_a_first_search_says_nothing_about_saved_selectors(self, shop_browser):
        assert "used_saved_selector" not in click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))

    def test_a_saved_selector_that_now_points_elsewhere_is_replaced(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        moved_find = FakeElement("main > button:nth-of-type(3)", "button:submit", "Find")
        impostor = FakeElement(FIND.selector, "button:submit", "Delete account")
        page = FakeSelectorPage(SHOP, [impostor, moved_find])
        result = click_on(shop_browser, page)
        assert page.clicked == [moved_find.selector]
        assert "used_saved_selector" not in result
        assert shop_browser.selectors.get("shop.test", "find")["selector"] == moved_find.selector

    def test_a_saved_selector_for_a_different_kind_of_element_is_not_used(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        link_now = FakeElement(FIND.selector, "a", "Find")
        button = FakeElement("main > button:nth-of-type(1)", "button:submit", "Find")
        page = FakeSelectorPage(SHOP, [link_now, button])
        click_on(shop_browser, page)
        assert shop_browser.selectors.get("shop.test", "find")["selector"] == button.selector

    def test_a_saved_selector_that_matches_nothing_falls_back_to_searching(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        moved = FakeElement("aside > button:nth-of-type(1)", "button:submit", "Find")
        page = FakeSelectorPage(SHOP, [moved])
        click_on(shop_browser, page)
        assert page.clicked == [moved.selector]

    def test_a_saved_selector_whose_action_fails_is_forgotten(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        page = FakeSelectorPage(SHOP, [FIND])
        page.broken_clicks = True
        with pytest.raises(FakePlaywrightError):
            click_on(shop_browser, page)
        assert shop_browser.selectors.get("shop.test", "find") is None

    def test_a_step_whose_action_fails_is_not_remembered(self, shop_browser):
        page = FakeSelectorPage(SHOP, [FIND])
        page.broken_clicks = True
        with pytest.raises(FakePlaywrightError):
            click_on(shop_browser, page)
        assert shop_browser.selectors.get("shop.test", "find") is None

    def test_numbered_targets_from_a_snapshot_are_never_remembered(self, shop_browser):
        numbered = FakeElement('[data-agent-id="3"]', "button:submit", "Find")
        click_on(shop_browser, FakeSelectorPage(SHOP, [numbered]), target="3")
        assert shop_browser.selectors.load() == {}

    @pytest.mark.parametrize("target", ["Reply to sam@example.com", "Order 482913"])
    def test_steps_naming_an_address_or_long_number_are_never_remembered(self, shop_browser, target):
        mail = FakeElement("main > button:nth-of-type(1)", "button", target)
        click_on(shop_browser, FakeSelectorPage(SHOP, [mail]), target=target)
        assert shop_browser.selectors.load() == {}

    def test_an_element_without_a_stable_selector_is_not_remembered(self, shop_browser):
        loose = FakeElement("div > div > button", "button:submit", "Find", stable=False)
        click_on(shop_browser, FakeSelectorPage(SHOP, [loose]))
        assert shop_browser.selectors.load() == {}

    @pytest.mark.parametrize("page", [
        FakeSelectorPage("http://shop.test/login", [FIND], password_field=True),
        FakeSelectorPage("http://shop.test/account", [FIND], password_field=True),
        FakeSelectorPage("http://shop.test/checkout/delivery", [FIND]),
        FakeSelectorPage("http://shop.test/payment", [FIND]),
        FakeSelectorPage("http://shop.test/basket", [FIND]),
        FakeSelectorPage(SHOP, [FIND], card_field=True),
        FakeSelectorPage(SHOP, [FIND], challenge_frame=True),
        FakeSelectorPage("https://accounts.google.com/v3/signin", [FIND]),
        FakeSelectorPage("https://www.google.com/sorry/index", [FIND]),
    ])
    def test_login_payment_and_bot_check_pages_are_never_remembered(self, shop_browser, page):
        click_on(shop_browser, page)
        assert shop_browser.selectors.load() == {}

    def test_a_saved_selector_is_not_used_on_a_payment_page(self, shop_browser):
        click_on(shop_browser, FakeSelectorPage(SHOP, [FIND]))
        decoy = FakeElement("form#pay > button:nth-of-type(1)", "button:submit", "Find")
        page = FakeSelectorPage("http://shop.test/checkout", [decoy, FIND])
        result = click_on(shop_browser, page)
        assert page.clicked == [decoy.selector]
        assert "used_saved_selector" not in result


class TestSelectorCacheFile:
    def test_saved_selectors_survive_a_restart_in_a_private_file(self, tmp_path):
        path = str(tmp_path / "selectors.json")
        agent.SelectorCache(path).remember("shop.test", "find", {"selector": "form > button", "kind": "button"})
        assert agent.SelectorCache(path).get("shop.test", "find") == {"selector": "form > button", "kind": "button"}
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_the_file_holds_only_sites_steps_selectors_and_kinds(self, tmp_path):
        path = str(tmp_path / "selectors.json")
        agent.SelectorCache(path).remember("shop.test", "find", {"selector": "form > button", "kind": "button"})
        with open(path) as handle:
            assert json.load(handle) == {"entries": [["shop.test\nfind", {"selector": "form > button", "kind": "button"}]]}

    def test_the_cache_is_bounded_and_drops_the_least_recently_used(self, tmp_path):
        cache = agent.SelectorCache(str(tmp_path / "selectors.json"), limit=3)
        for step in ("one", "two", "three"):
            cache.remember("shop.test", step, {"selector": f"#{step}", "kind": "button"})
        cache.touch("shop.test", "one")
        cache.remember("shop.test", "four", {"selector": "#four", "kind": "button"})
        assert sorted(key.split("\n")[1] for key in cache.load()) == ["four", "one", "three"]

    def test_a_damaged_file_starts_an_empty_cache_instead_of_failing(self, tmp_path):
        path = tmp_path / "selectors.json"
        path.write_text("{not json")
        assert agent.SelectorCache(str(path)).get("shop.test", "find") is None

    def test_odd_entries_in_the_file_are_ignored(self, tmp_path):
        path = tmp_path / "selectors.json"
        path.write_text(json.dumps({"entries": [["shop.test\nfind", {"selector": "x" * 1000, "kind": "button"}],
                                                ["shop.test\nok", {"selector": "#ok", "kind": "button"}],
                                                ["bad"], "nonsense", ["shop.test\nnokind", {"selector": "#a"}]]}))
        assert list(agent.SelectorCache(str(path)).load()) == ["shop.test\nok"]

    def test_steps_are_matched_however_they_are_spaced_or_capitalised(self, tmp_path):
        cache = agent.SelectorCache(str(tmp_path / "selectors.json"))
        cache.remember("shop.test", agent.tidy_step("  Find   NOW "), {"selector": "#f", "kind": "button"})
        assert cache.get("shop.test", agent.tidy_step("find now")) == {"selector": "#f", "kind": "button"}
