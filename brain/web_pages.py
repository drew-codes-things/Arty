import contextlib
import contextvars
import ipaddress
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import connections
import sharing
import web_host

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Arty"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8",
                   "Accept-Language": "en-GB,en;q=0.8"}
PAGE_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
WEB_SCHEMES = ("http", "https")
HOME_NAME_ENDINGS = (".local", ".lan", ".home.arpa", ".home", ".internal", ".localdomain", ".localhost", ".ts.net")
SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
TRANSLATED_IPV4 = ipaddress.ip_network("64:ff9b::/96")
PLAIN_WEB_ADDRESS = re.compile(r"https?://[^/?#@]+(?:[/?#].*)?", re.IGNORECASE | re.DOTALL)
NOT_PLAIN = re.compile(r"[\\\s\x00-\x1f\x7f]")
TIMEOUT_SECONDS = 15
MAX_PAGE_BYTES = 3 * 1024 * 1024
SITE_GAP_SECONDS = 2.0
PAGES_PER_MINUTE = 20
ROOM_PAGES_PER_MINUTE = 6
MINUTE_SECONDS = 60.0
PAGES_PER_REPLY = 10
REPLY_MEMORY_SECONDS = 600
REMEMBERED_REPLIES = 50

NOT_A_WEB_ADDRESS = "that isn't a web address (only http and https pages are read)"
NOT_PUBLIC = "that address is on this computer or the home network, and Arty only reads public websites"
REPLY_LIMIT = ("Arty has read {pages} web pages for this reply, which is the most it reads for one reply. Answer from "
               "what was read, and say the user can ask again to read more")
MINUTE_LIMIT = "Arty has read {pages} web pages in the last minute, which is the most it reads that fast. Try again shortly"
DOWNLOAD_REFUSAL = "that address is a file to download ({kind}), not a web page, and Arty doesn't download files"
OWN_APP = ("that address is one of the user's own apps behind the starfire sign-in ({host}), and Arty never reads "
           "those pages itself")
OWN_APPS_UNREADABLE = ("the list of the user's own app addresses can't be read, so Arty reads no web pages until it is "
                       "fixed")


class PageRefused(Exception):
    pass


@dataclass(frozen=True)
class Page:
    asked_url: str
    url: str
    status: int
    html: str
    read_at: datetime
    cut_short: bool


class ReplyBudget:
    def __init__(self, pages: int = PAGES_PER_REPLY):
        self.limit = pages
        self.used = 0
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            if self.used >= self.limit:
                raise PageRefused(REPLY_LIMIT.format(pages=self.limit))
            self.used += 1

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)

    def is_stale(self) -> bool:
        return time.monotonic() - self.started > REPLY_MEMORY_SECONDS


class SitePacing:
    def __init__(self, gap: float = SITE_GAP_SECONDS, per_minute: int = PAGES_PER_MINUTE, clock=time.monotonic,
                 sleep=time.sleep):
        self.gap, self.per_minute, self.clock, self.sleep = gap, per_minute, clock, sleep
        self._last_visit = {}
        self._recent = []
        self._lock = threading.Lock()

    def wait_for(self, site: str):
        with self._lock:
            now = self.clock()
            self._recent = [when for when in self._recent if now - when < MINUTE_SECONDS]
            if len(self._recent) >= self.per_minute:
                raise PageRefused(MINUTE_LIMIT.format(pages=self.per_minute))
            wait = self._last_visit.get(site, now - self.gap) + self.gap - now
            visit = now + max(0.0, wait)
            self._last_visit[site] = visit
            self._recent.append(visit)
        if wait > 0:
            self.sleep(wait)


_reply_budget = contextvars.ContextVar("web_page_budget", default=None)
_budgets = {}
_budgets_lock = threading.Lock()


def start_reply(reply_key: str = ""):
    now = time.monotonic()
    with _budgets_lock:
        for key, (started, _) in list(_budgets.items()):
            if now - started > REPLY_MEMORY_SECONDS:
                del _budgets[key]
        if not reply_key or reply_key not in _budgets:
            budget = ReplyBudget()
            if reply_key:
                _budgets[reply_key] = (now, budget)
                while len(_budgets) > REMEMBERED_REPLIES:
                    del _budgets[next(iter(_budgets))]
        else:
            budget = _budgets[reply_key][1]
    _reply_budget.set(budget)
    return budget


def reply_budget() -> ReplyBudget:
    budget = _reply_budget.get()
    if budget is None or budget.is_stale():
        budget = ReplyBudget()
        _reply_budget.set(budget)
    return budget


def site_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _literal_address(host: str):
    try:
        return ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except (OSError, UnicodeError, ValueError):
        return None


def _embedded_ipv4(address):
    if address.version == 6 and address in TRANSLATED_IPV4:
        return ipaddress.ip_address(int(address) & 0xFFFFFFFF)
    return getattr(address, "ipv4_mapped", None) or getattr(address, "sixtofour", None) or address


def is_home_address(url: str) -> bool:
    try:
        parts = urlsplit(str(url or "").strip())
        host = unicodedata.normalize("NFKC", parts.hostname or "").rstrip(".")
    except ValueError:
        return False
    if parts.scheme.lower() not in WEB_SCHEMES or not host:
        return False
    address = _literal_address(host)
    if address is not None:
        address = _embedded_ipv4(address)
        return not address.is_global or address in SHARED_ADDRESS_SPACE
    return host == "localhost" or "." not in host or host.endswith(HOME_NAME_ENDINGS)


def is_plain_web_address(url: str) -> bool:
    text = str(url or "").strip()
    if NOT_PLAIN.search(text) or not PLAIN_WEB_ADDRESS.fullmatch(text):
        return False
    try:
        return bool(urlsplit(text).hostname)
    except ValueError:
        return False


def address_problem(url: str) -> str | None:
    if not is_plain_web_address(url):
        return NOT_A_WEB_ADDRESS
    return NOT_PUBLIC if is_home_address(url) else None


def check_outside_own_apps(url: str):
    try:
        hosts = connections.own_app_hosts_or_refuse()
    except connections.UnreadableOwnApps:
        raise PageRefused(OWN_APPS_UNREADABLE) from None
    try:
        is_own_app = connections.is_own_app_address(url, hosts)
    except web_host.UnclearAddress:
        raise PageRefused(NOT_A_WEB_ADDRESS) from None
    if is_own_app:
        raise PageRefused(OWN_APP.format(host=connections.host_of(url)))


def check_public(url: str):
    if problem := address_problem(url):
        raise PageRefused(problem)
    check_outside_own_apps(url)
    try:
        sharing.check_public_url(url)
    except sharing.ShareError:
        raise PageRefused(NOT_PUBLIC) from None


def _redirect_handler(check):
    class CheckedRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, new_url):
            check(new_url)
            return super().redirect_request(request, response, code, message, headers, new_url)
    return CheckedRedirects


def public_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _redirect_handler(check_public),
                                       sharing.PinnedHTTPHandler, sharing.PinnedHTTPSHandler)


def _decoded(data: bytes, charset: str | None) -> str:
    for encoding in (charset, "utf-8", "cp1252"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _is_download(response) -> str:
    kind = (response.headers.get_content_type() or "").lower()
    if kind not in PAGE_TYPES:
        return kind or "unknown type"
    disposition = (response.headers.get("Content-Disposition") or "").lower()
    return "an attachment" if disposition.startswith("attachment") else ""


class Fetcher:
    def __init__(self, check=check_public, opener=None, pacing=None):
        self.check = check
        self.opener = opener or (public_opener() if check is check_public else
                                 urllib.request.build_opener(urllib.request.ProxyHandler({}), _redirect_handler(check)))
        self.pacing = pacing

    def fetch(self, url: str, budget: ReplyBudget | None = None) -> Page:
        url = str(url or "").strip()
        if not is_plain_web_address(url):
            raise PageRefused(NOT_A_WEB_ADDRESS)
        self.check(url)
        (budget or reply_budget()).take()
        (self.pacing or current_pacing()).wait_for(site_of(url))
        request = urllib.request.Request(url, headers=REQUEST_HEADERS)
        try:
            response = self.opener.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            response = error
        except PageRefused:
            raise
        except sharing.ShareError:
            raise PageRefused(NOT_PUBLIC) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise PageRefused(f"the page didn't load ({getattr(error, 'reason', error)})") from None
        with response:
            download = _is_download(response)
            if download:
                raise PageRefused(DOWNLOAD_REFUSAL.format(kind=download))
            try:
                data = response.read(MAX_PAGE_BYTES + 1)
            except (OSError, TimeoutError) as error:
                raise PageRefused(f"the page stopped loading partway ({error})") from None
            final_url = response.geturl() or url
            status = getattr(response, "status", None) or getattr(response, "code", 0) or 0
            charset = response.headers.get_content_charset()
        return Page(asked_url=url, url=final_url, status=int(status), html=_decoded(data[:MAX_PAGE_BYTES], charset),
                    read_at=datetime.now().astimezone(), cut_short=len(data) > MAX_PAGE_BYTES)


PACING = SitePacing()
ROOM_PACING = SitePacing(per_minute=ROOM_PAGES_PER_MINUTE)
_pacing = contextvars.ContextVar("web_page_pacing", default=None)


def current_pacing() -> SitePacing:
    return _pacing.get() or PACING


@contextlib.contextmanager
def paced_by(pacing: SitePacing):
    token = _pacing.set(pacing)
    try:
        yield
    finally:
        _pacing.reset(token)
