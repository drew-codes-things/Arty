from urllib.parse import urlsplit

from process import browser
from process import home_network
from process import link
from process import shown_text

MAX_URL_CHARS = 2000
OPENABLE_SCHEMES = ("http", "https")
HOME_NETWORK_LINE = "This page is on this computer or your home network."
MAX_HOST_CHARS = 253


class OpenRefused(ValueError):
    def __init__(self, reason, message, status_code=400):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def _parsed(url):
    try:
        parts = urlsplit(url)
        return parts, parts.hostname
    except ValueError:
        raise OpenRefused("bad_request", "That address can't be read, so it was not asked.") from None


def checked_address(url):
    if len(url) > MAX_URL_CHARS:
        raise OpenRefused("bad_request", f"That address is longer than the {MAX_URL_CHARS} characters the card can show "
                                         "in full, so it was not asked.", 413)
    if not shown_text.is_shown_exactly(url) or " " in url:
        raise OpenRefused("bad_request", "That address has spaces or characters that cannot be shown on the card, "
                                         "so it was not asked.")
    parts, host = _parsed(url)
    if parts.scheme.lower() not in OPENABLE_SCHEMES or not host:
        raise OpenRefused("bad_request", "Only http and https addresses with a site in them can be opened.")
    if not _site_shown_in_full(url, host):
        raise OpenRefused("bad_request", "That site name is too long or can't be written out plainly, so it was not asked.")
    if home_network.is_number_in_disguise(host):
        raise OpenRefused("bad_request", "That site is a number written in a way that hides what it is, so it was not asked.")
    problem = browser.address_problem(url)
    if problem:
        raise OpenRefused("bad_request", f"Not asked: {problem}.")
    return url


def _site_shown_in_full(url, host):
    try:
        ascii_host = host.encode("idna")
    except UnicodeError:
        return False
    return len(ascii_host) <= MAX_HOST_CHARS and bool(browser.site_of(url))


def chosen_browser(wanted, connected):
    if wanted and (not shown_text.is_shown_exactly(wanted) or len(wanted) > browser.MAX_ASKED_BROWSER_CHARS):
        raise OpenRefused("bad_request", "That browser name cannot be shown on the card.")
    if not connected:
        raise OpenRefused("no_browser", "No browser is connected to Arty Desktop right now.", 409)
    if not wanted:
        if len(connected) == 1:
            return connected[0]
        raise OpenRefused("which_browser", f"More than one browser is connected ({', '.join(connected)}): say which.", 409)
    name = browser.canonical_browser(wanted)
    if name not in connected:
        raise OpenRefused("not_connected", f"{name or 'That browser'} isn't connected to Arty Desktop. "
                                           f"Connected now: {', '.join(connected)}.", 409)
    return name


def card(app_name, browser_name, url):
    lines = [f"{app_name} asks to open a web page in your browser.", f"browser: {browser_name}", f"address: {url}",
             browser.site_line(url)]
    if home_network.is_home_network_host(_parsed(url)[1]):
        lines.append(HOME_NETWORK_LINE)
    return "\n".join(lines + [link.ALLOW_LINE])


def open_page(browser_name, url):
    try:
        tab = browser.open_tab(url, browser_name)
    except browser.BrowserError as error:
        raise OpenRefused("failed", f"Couldn't open it: {error}.", 502) from None
    return {"ok": True, "browser": browser_name, "tab": tab}
