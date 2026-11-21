import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import connections  # noqa: E402
import docs_reading  # noqa: E402
import sharing  # noqa: E402
import shopping  # noqa: E402
import web_pages  # noqa: E402

OWN_APPS = {"drive": "https://drive.example.org", "calendar": "https://calendar.example.org/", "starfire": ""}
STARFIRE = "https://Chat.Example.org/"
OWN_APP_ADDRESSES = ("https://drive.example.org/files/0", "https://CALENDAR.example.org./api/events",
                     "https://api.drive.example.org/x", "https://chat.example.org/c/1", "http://drive.example.org:8443/")


OWN_APP_WRITTEN_OTHER_WAYS = ("https://\uff44rive.example.org/api/session", "https://drive\u3002example\u3002org/api/session",
                              "https://drive\uff0eexample.org/api/session", "https://drive%2Eexample.org/api/session",
                              "https://DRIVE.EXAMPLE.ORG/api/session")
UNCLEAR_ADDRESSES = ("https://drive.example.org\\api/session", "https://news.example.com\\@drive.example.org/",
                     "https://drive.example.org/\x00x")
BROWSER_REACHABLE_FORMS = ("https://evil.example\uff0f@drive.example.org/x", "https://evil.example\uff03@drive.example.org/x",
                           "https://evil.example\uff1f@drive.example.org/x", "https://evil.example\uff1a80@drive.example.org/x",
                           "https://a\u2100b@drive.example.org/x", "https://\uff20@drive.example.org/x",
                           "https:///drive.example.org/x", "https:/drive.example.org/x", "https:drive.example.org/x",
                           "HTTPS:////drive.example.org/x", "https://@drive.example.org/x", "https://:@drive.example.org/x",
                           "http://drive.example.org:80\uff20evil.example/", "https://news.example.com@drive.example.org/",
                           "https://[drive.example.org/x")


class NoNetwork:
    def __init__(self):
        self.opened = []

    def open(self, request, timeout=None):
        self.opened.append(request.full_url)
        raise AssertionError(f"nothing should be fetched, but {request.full_url} was")


class OwnAppsAreNeverFetched(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="own-app-fetch-"))
        self.addCleanup(lambda: [path.unlink() for path in folder.iterdir()] and folder.rmdir())
        self.listed = folder / "own-apps.json"
        self.listed.write_text(json.dumps(OWN_APPS))
        for name, value in (("OWN_APPS_FILE", str(self.listed)), ("STARFIRE_URL", STARFIRE)):
            patcher = unittest.mock.patch.object(connections, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetched_text(self, url):
        network = NoNetwork()
        fetcher = web_pages.Fetcher(opener=network)
        text = shopping.fetch_tool_text({"url": url}, fetcher=fetcher, budget=web_pages.ReplyBudget())
        self.assertEqual(network.opened, [])
        return text

    def test_the_fetch_tool_refuses_every_own_app_and_the_starfire_host(self):
        for url in OWN_APP_ADDRESSES:
            with self.subTest(url=url):
                self.assertIn("one of the user's own apps", self.fetched_text(url))

    def test_the_refusal_names_the_host_and_not_the_page(self):
        text = self.fetched_text("https://drive.example.org/files/0?token=abc")
        self.assertIn("(drive.example.org)", text.split(":", 2)[2])
        self.assertNotIn("token=abc", text.split(":", 2)[2])

    def test_lookalike_hosts_are_not_own_apps(self):
        for url in ("https://drive.example.org.evil.example/", "https://notdrive.example.org/", "https://example.org/"):
            with self.subTest(url=url):
                web_pages.check_outside_own_apps(url)

    def test_a_redirect_into_an_own_app_is_refused(self):
        handler = web_pages._redirect_handler(web_pages.check_public)()
        start = urllib.request.Request("https://news.example.com/story")
        with self.assertRaises(web_pages.PageRefused) as refused:
            handler.redirect_request(start, None, 302, "Found", {}, "https://chat.example.org/login")
        self.assertIn("one of the user's own apps", str(refused.exception))

    def test_docs_reading_refuses_own_apps(self):
        self.assertIn("one of the user's own apps", docs_reading.fetch_page("https://calendar.example.org/api/events"))

    def test_the_model_fetch_tool_through_the_hub_never_reaches_the_fetch_server(self):
        hub = connections.ToolHub()
        hub.routes = {"fetch__fetch": "http://127.0.0.1:8000/fetch/fetch"}
        with unittest.mock.patch.object(connections, "http_json", side_effect=AssertionError("the fetch server was called")):
            result = hub.call("fetch__fetch", {"url": "https://chat.example.org/c/1"})
        self.assertIn("one of the user's own apps", result)

    def test_an_own_app_written_another_way_is_still_refused(self):
        for url in OWN_APP_WRITTEN_OTHER_WAYS:
            with self.subTest(url=url):
                self.assertRegex(self.fetched_text(url), "one of the user's own apps|on this computer or the home network")
        self.assertIn("one of the user's own apps", self.fetched_text(OWN_APP_WRITTEN_OTHER_WAYS[0]))

    def test_an_address_with_a_backslash_or_control_character_is_not_fetched(self):
        for url in UNCLEAR_ADDRESSES:
            with self.subTest(url=url):
                with self.assertRaises(web_pages.PageRefused):
                    web_pages.check_public(url)

    def test_a_picture_from_an_own_app_is_never_downloaded(self):
        for url in OWN_APP_ADDRESSES + OWN_APP_WRITTEN_OTHER_WAYS:
            with self.subTest(url=url), unittest.mock.patch.object(sharing, "_opener", NoNetwork()), \
                    unittest.mock.patch.object(sharing, "is_public_host", return_value=True):
                with self.assertRaises(sharing.ShareError) as refused:
                    sharing.fetch_media(url)
                self.assertIn("own apps", str(refused.exception))

    def test_a_picture_redirected_into_an_own_app_is_refused(self):
        handler = sharing.PublicOnlyRedirects()
        start = urllib.request.Request("https://images.example.com/cat.jpg")
        with unittest.mock.patch.object(sharing, "is_public_host", return_value=True):
            with self.assertRaises(sharing.ShareError):
                handler.redirect_request(start, None, 302, "Found", {}, "https://\uff44rive.example.org/api/files/1")

    def test_an_ordinary_picture_address_still_passes_the_check(self):
        with unittest.mock.patch.object(sharing, "is_public_host", return_value=True):
            sharing.check_public_url("https://images.example.com/cat.jpg")

    def test_without_a_list_only_the_starfire_host_is_refused(self):
        self.listed.unlink()
        self.assertIn("one of the user's own apps", self.fetched_text("https://chat.example.org/"))
        web_pages.check_outside_own_apps("https://drive.example.org/files/0")

    def test_an_unreadable_list_refuses_every_fetch(self):
        self.listed.write_text("{broken")
        for url in ("https://news.example.com/story", "https://drive.example.org/files/0"):
            with self.subTest(url=url):
                self.assertIn("can't be read", self.fetched_text(url))


class BrowserReachableForms(OwnAppsAreNeverFetched):
    def test_every_form_a_browser_reads_as_an_own_app_is_unclear(self):
        web_pages.check_outside_own_apps("https://news.example.com/story")
        for url in BROWSER_REACHABLE_FORMS:
            with self.subTest(url=url.encode("unicode_escape").decode()):
                with self.assertRaises(connections.web_host.UnclearAddress):
                    connections.web_host.is_own_app_address(url, ["drive.example.org"])
                with self.assertRaises(web_pages.PageRefused):
                    web_pages.check_outside_own_apps(url)

    def test_plain_addresses_still_read(self):
        for url, host in (("https://news.example.com/story", "news.example.com"), ("HTTP://Example.org", "example.org"),
                          (" https://drive.example.org.evil.example/ ", "drive.example.org.evil.example"),
                          ("about:blank", ""), ("", ""), ("/relative/path", "")):
            with self.subTest(url=url):
                self.assertEqual(connections.web_host.host_of(url), host)


if __name__ == "__main__":
    unittest.main()
