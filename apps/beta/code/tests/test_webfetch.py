import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arty_code import webfetch

PAGE = b"""<html><head><title>Docs</title><style>p{color:red}</style><script>var x = 1;</script></head>
<body><h1>Install</h1><p>Run <code>pip install thing</code>.</p><nav>skip me?</nav></body></html>"""


def test_a_page_becomes_readable_text():
    text = webfetch.page_text(PAGE.decode())
    assert "Install" in text and "pip install thing" in text
    assert "color:red" not in text and "var x" not in text


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/docs"
    server.shutdown()


def test_fetching_a_page_returns_its_text(site):
    text = webfetch.fetch(site)
    assert text.startswith(site)
    assert "pip install thing" in text


def test_only_web_addresses_are_fetched():
    with pytest.raises(webfetch.FetchError, match="http"):
        webfetch.fetch("file:///etc/passwd")


class Moving(BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", Moving.target)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def moving():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Moving)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_a_fetched_page_is_marked_as_data_not_instructions(site):
    text = webfetch.fetch(site)
    assert "not instructions" in text
    assert text.index("not instructions") < text.index("pip install thing")


def test_a_redirect_to_another_site_is_not_followed(moving, site):
    Moving.target = site.replace("127.0.0.1", "localhost")
    with pytest.raises(webfetch.FetchError, match="localhost"):
        webfetch.fetch(f"http://127.0.0.1:{moving}/old")


def test_a_redirect_on_the_same_site_is_followed(moving, site):
    Moving.target = site
    assert "pip install thing" in webfetch.fetch(f"http://127.0.0.1:{moving}/old")
