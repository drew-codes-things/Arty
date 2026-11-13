"""A visible Google Chrome window the assistant drives by page content instead of pixels.

Runs inside the desktop session (DISPLAY :0) with a persistent profile, so logins
made by the user in this window are kept. Listens on 127.0.0.1 only; the workspace
tools reach it over SSH. Single-threaded on purpose: Playwright's sync API isn't
thread-safe, and one browser should do one thing at a time.
"""
import hmac
import json
import os
import queue
import random
import re
import secrets
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PROFILE = os.path.expanduser("~/.browser-profile")
# Google Chrome, not Playwright's "Chrome for Testing" build: Google refuses to sign in on
# the testing build, and the "Sign in to websites" shortcut must open this same browser and profile.
CHROME = "/usr/bin/google-chrome"
DOWNLOADS = os.path.expanduser("~/Downloads")
PORT = 9333
# Chrome's debugging port, for this agent to drive it: bound to 127.0.0.1, so only the desktop VM itself can reach it.
DEBUG_PORT = 9223
CHROME_START_SECONDS = 20
DOWNLOAD_WAIT_MS = 120000
TEXT_LIMIT = 8000
ELEMENT_LIMIT = 120
ACTION_TIMEOUT_MS = 15000
MEDIA_WAIT_TRIES = 6
MEDIA_WAIT_MS = 1500
MEDIA_STATE_JS = """() => {
  const players = [...document.querySelectorAll('video, audio')]
    .sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
  const player = players[0];
  if (!player) return null;
  const box = player.getBoundingClientRect();
  return {paused: player.paused, ended: player.ended, muted: player.muted || player.volume === 0,
          time: player.currentTime, duration: Number.isFinite(player.duration) ? player.duration : 0,
          x: box.x + box.width / 2, y: box.y + box.height / 2};
}"""

LIST_ELEMENTS_JS = """
(limit) => {
  const selector = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=textbox], [role=menuitem], [role=tab], [contenteditable=true]';
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && r.bottom > 0 && r.top < innerHeight * 3; };
  document.querySelectorAll('[data-agent-id]').forEach(el => el.removeAttribute('data-agent-id'));
  const items = [];
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el)) continue;
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    const kind = el.isContentEditable ? 'textbox' : (el.getAttribute('role') || el.tagName.toLowerCase() + (el.type ? ':' + el.type : ''));
    if (!label && !['input:text','input:search','input:email','textarea','textbox'].includes(kind) && !kind.startsWith('input')) continue;
    const id = items.length + 1;
    el.setAttribute('data-agent-id', String(id));
    items.push(`[${id}] ${kind} "${label}"`);
    if (items.length >= limit) break;
  }
  return items;
}
"""


CHALLENGE_FRAMES = ("challenges.cloudflare.com", "recaptcha", "hcaptcha.com", "arkoselabs", "funcaptcha", "captcha-delivery", "perimeterx")
CHALLENGE_PHRASES = (
    "just a moment", "verify you are human", "verify you're human", "are you a robot", "not a robot",
    "unusual traffic", "security check", "security verification", "quick security check", "complete the captcha",
    "press and hold", "checking your browser", "access denied", "bot detection",
)
CHALLENGE_URLS = ("/checkpoint/", "/challenge", "captcha", "/sorry/")
SURE_CHALLENGE_URLS = ("/checkpoint/challenge", "google.com/sorry/")
POSTS_JS = """
() => Array.from(document.querySelectorAll('div.feed-shared-update-v2, div[data-urn^="urn:li:activity"]')).map(post => {
  const pick = (selector) => { const el = post.querySelector(selector); return el ? el.innerText.trim() : ''; };
  return {
    urn: post.getAttribute('data-urn') || '',
    author: pick('.update-components-actor__title span[aria-hidden="true"]') || pick('.update-components-actor__title'),
    header: pick('.update-components-header'),
    when: pick('.update-components-actor__sub-description').split('\\n')[0],
    text: pick('.update-components-text') || pick('.feed-shared-update-v2__description'),
  };
})
"""
POSTS_MAX = 50
POSTS_SCROLLS = 15
SIGN_IN_PATHS = ("/uas/login", "/authwall")
SIGN_IN_HOSTS = ("accounts.google.com",)
LOGIN_URLS = ("/login", "/signin", "/sign-in", "/auth/", "/sso")
SHORT_PAGE_CHARS = 800
VISIBLE_PASSWORD_JS = """
() => Array.from(document.querySelectorAll('input[type=password]')).some(field => {
  const box = field.getBoundingClientRect();
  return box.width > 2 && box.height > 2 && getComputedStyle(field).visibility !== 'hidden';
})
"""


VISIBLE_CHALLENGE_JS = """
(markers) => Array.from(document.querySelectorAll('iframe')).some(frame => {
  const src = (frame.src || '').toLowerCase();
  const box = frame.getBoundingClientRect();
  return markers.some(m => src.includes(m)) && box.width > 100 && box.height > 60 && getComputedStyle(frame).visibility !== 'hidden';
})
"""


CARD_FIELD_JS = """
(pattern) => Array.from(document.querySelectorAll('input, iframe')).some(el => {
  const box = el.getBoundingClientRect();
  if (box.width <= 2 || box.height <= 2 || getComputedStyle(el).visibility === 'hidden') return false;
  const described = [el.getAttribute('autocomplete'), el.getAttribute('name'), el.id, el.getAttribute('src'), el.getAttribute('title')].filter(Boolean).join(' ');
  return new RegExp(pattern, 'i').test(described);
})
"""


STABLE_SELECTOR_JS = """
(el, pattern) => {
  const kindOf = (e) => e.isContentEditable ? 'textbox' : (e.getAttribute('role') || e.tagName.toLowerCase() + (e.type ? ':' + e.type : ''));
  const described = [el.type, el.getAttribute('autocomplete'), el.getAttribute('name'), el.id].filter(Boolean).join(' ');
  if (new RegExp(pattern, 'i').test(described)) return null;
  const generated = /\\d{4,}|^[:_]|:|^[a-f0-9]{8,}$|^(ember|react|ng-|mui-|css-)/i;
  const anchorOf = (node) => {
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'name', 'id']) {
      const value = node.getAttribute(attr);
      if (!value || value.length > 60 || generated.test(value)) continue;
      return attr === 'id' ? '#' + CSS.escape(value) : node.tagName.toLowerCase() + '[' + attr + '="' + CSS.escape(value) + '"]';
    }
    return null;
  };
  const positionOf = (node) => {
    let index = 1;
    for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
      if (sibling.tagName === node.tagName) index += 1;
    }
    return node.tagName.toLowerCase() + ':nth-of-type(' + index + ')';
  };
  const pointsOnlyHere = (selector) => {
    try {
      const found = document.querySelectorAll(selector);
      return found.length === 1 && found[0] === el;
    } catch (error) {
      return false;
    }
  };
  const parts = [];
  let node = el;
  let anchored = false;
  for (let depth = 0; depth < 12 && node && node !== document.body && node !== document.documentElement; depth += 1) {
    const anchor = anchorOf(node);
    anchored = anchored || Boolean(anchor);
    parts.unshift(anchor || positionOf(node));
    if (anchored && pointsOnlyHere(parts.join(' > '))) return {selector: parts.join(' > '), kind: kindOf(el)};
    node = node.parentElement;
  }
  const rooted = 'body > ' + parts.join(' > ');
  return node === document.body && pointsOnlyHere(rooted) ? {selector: rooted, kind: kindOf(el)} : null;
}
"""


MATCHES_STEP_JS = """
(el, [step, kind]) => {
  const box = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  if (box.width <= 2 || box.height <= 2 || style.visibility === 'hidden' || style.display === 'none') return false;
  const kindNow = el.isContentEditable ? 'textbox' : (el.getAttribute('role') || el.tagName.toLowerCase() + (el.type ? ':' + el.type : ''));
  if (kindNow !== kind) return false;
  const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => id && document.getElementById(id)).filter(Boolean).map(node => node.innerText);
  const buttonValue = el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type) ? el.value : '';
  const names = [el.getAttribute('aria-label'), ...labelledBy, ...Array.from(el.labels || []).map(label => label.innerText), el.innerText, buttonValue, el.placeholder, el.title, el.getAttribute('alt')];
  return names.some(name => typeof name === 'string' && name.toLowerCase().replace(/\\s+/g, ' ').trim().includes(step));
}
"""


# Gmail has its own ordinary Google Chrome window and profile, read by an extension (extensions/gmail/): Google
# signs out a browser that a program drives through the debugging port, as this agent does with the main one.
GMAIL_PROFILE = os.path.expanduser("~/.gmail-chrome")
GMAIL_LAUNCHER = os.path.expanduser("~/.local/bin/sign-in-google")
GMAIL_PORT = 9334            # where the extension asks for jobs: 127.0.0.1 only
GMAIL_POLL_SECONDS = 20      # how long a job request from the extension is held open
GMAIL_JOB_SECONDS = 90
GMAIL_START_SECONDS = 30
GMAIL_CONNECTED_SECONDS = 45  # an extension that asked for a job this recently is running
GMAIL_UNREAD_QUERY = "in:inbox is:unread"
GMAIL_ACTIONS = ("gmail_unread", "gmail_list", "gmail_read", "gmail_tabs")
AGENT_FOLDER = os.path.expanduser("~/.local/browser-agent")
PRIVATE_FOLDERS = (PROFILE, GMAIL_PROFILE, AGENT_FOLDER, os.path.expanduser("~/.ssh"))
TOKEN_FILE = os.path.join(AGENT_FOLDER, "token")
TOKEN_HEADER_FILE = os.path.join(AGENT_FOLDER, "token.header")
EXTENSION_TOKEN_FILE = os.path.join(AGENT_FOLDER, "gmail-extension", "agent-token.json")
TOKEN_HEADER = "X-Arty-Agent-Token"
TOKEN_MIN_CHARS = 32
JSON_TYPE = "application/json"
EXTENSION_ORIGIN = "chrome-extension://"
LOCAL_HOST_NAMES = ("127.0.0.1", "localhost")
SELECTOR_CACHE_FILE = os.path.join(AGENT_FOLDER, "selectors.json")
SELECTOR_CACHE_LIMIT = 300
SELECTOR_MAX_CHARS = 300
KIND_MAX_CHARS = 40
STEP_MAX_CHARS = 120
PERSONAL_STEP = re.compile(r"@|\d{4,}")
NO_CACHE_URLS = ("checkout", "payment", "/pay/", "/pay?", "billing", "/cart", "/basket", "/wallet", "/donate")
SECRET_FIELD_PATTERN = r"password|cc-|card.?number|cardnumber|cvc|cvv|security.?code|expiry|iban|stripe|braintree|adyen|checkout"


ACCEPT_COOKIE_LABEL = (r"^(?:(?:yes,? )?i (?:accept|agree)|(?:accept|allow|agree)(?: to)?(?: all| additional| optional)?"
                       r"(?: cookies)?(?: (?:and|&) (?:continue|close))?|got it|ok|okay|consent|"
                       r"continue with recommended cookies)$")
COOKIE_SETTLE_MS = 1500
CONSENT_HINT = r"cookie|consent|gdpr|privacy|cmp|onetrust|didomi|sp_message|truste|qc-cmp|usercentrics|cookiebot"

ACCEPT_COOKIES_JS = """
([label, hint]) => {
  const consentLike = new RegExp(hint, 'i');
  const acceptLike = new RegExp(label);
  const framed = consentLike.test(location.href);
  const insideBanner = el => {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const marks = [node.id, typeof node.className === 'string' ? node.className : '', node.getAttribute('aria-label') || ''].join(' ');
      if (consentLike.test(marks)) return true;
      if (node.getAttribute('role') === 'dialog' && consentLike.test(node.innerText || '')) return true;
    }
    return false;
  };
  const buttons = [...document.querySelectorAll('button, [role=button], a, input[type=button], input[type=submit]')];
  for (const el of buttons) {
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase().replace(/\\s+/g, ' ');
    if (!acceptLike.test(text)) continue;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    if (!framed && !insideBanner(el)) continue;
    el.click();
    return true;
  }
  return false;
}
"""


def accept_cookies(page) -> bool:
    for frame in page.frames:
        try:
            if frame.evaluate(ACCEPT_COOKIES_JS, [ACCEPT_COOKIE_LABEL, CONSENT_HINT]):
                page.wait_for_timeout(COOKIE_SETTLE_MS)
                return True
        except PlaywrightError:
            continue
    return False


def page_shows(page, script, *args) -> bool:
    try:
        return bool(page.evaluate(script, *args))
    except PlaywrightError:
        return False


def looks_like_bot_check(page, url, title, text) -> bool:
    if page_shows(page, VISIBLE_CHALLENGE_JS, list(CHALLENGE_FRAMES)):
        return True
    if any(marker in url for marker in SURE_CHALLENGE_URLS):
        return True
    if len(text.strip()) >= SHORT_PAGE_CHARS:
        return False
    head = f"{title}\n{text}".lower()
    return any(marker in url for marker in CHALLENGE_URLS) or any(phrase in head for phrase in CHALLENGE_PHRASES)


def looks_like_login_wall(page, url) -> bool:
    address = urllib.parse.urlparse(url)
    if (address.hostname or "") in SIGN_IN_HOSTS or any(marker in address.path for marker in SIGN_IN_PATHS):
        return True
    return any(marker in address.path for marker in LOGIN_URLS) and page_shows(page, VISIBLE_PASSWORD_JS)


def blocked_reason(page, title, text):
    """Why a human is needed on this page (bot check or login wall), or None.
    Only visible challenge frames count: many sites embed an invisible reCAPTCHA on every page."""
    url = page.url.lower()
    if looks_like_bot_check(page, url, title, text):
        return "bot check (CAPTCHA or human verification)"
    if looks_like_login_wall(page, url):
        return "login required"
    return None


def write_private(path: str, text: str):
    temporary = f"{path}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_or_make_token(path: str = TOKEN_FILE) -> str:
    try:
        with open(path) as handle:
            token = handle.read().strip()
    except FileNotFoundError:
        token = ""
    if len(token) < TOKEN_MIN_CHARS:
        token = secrets.token_urlsafe(TOKEN_MIN_CHARS)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        write_private(path, token)
    os.chmod(path, 0o600)
    return token


def share_token(token: str, header_file: str = TOKEN_HEADER_FILE, extension_file: str = EXTENSION_TOKEN_FILE):
    write_private(header_file, f"{TOKEN_HEADER}: {token}\n")
    if os.path.isdir(os.path.dirname(extension_file)):
        write_private(extension_file, json.dumps({"token": token}))


def refusal(headers, port: int, token: str, extension_origin_allowed: bool = False) -> tuple[int, str] | None:
    if headers.get("Host", "") not in {f"{name}:{port}" for name in LOCAL_HOST_NAMES}:
        return 403, "requests must be addressed to this computer"
    origin = headers.get("Origin")
    if origin is not None and not (extension_origin_allowed and origin.startswith(EXTENSION_ORIGIN)):
        return 403, "requests from web pages are refused"
    if headers.get("Content-Type", "").split(";")[0].strip().lower() != JSON_TYPE:
        return 415, f"send {JSON_TYPE}"
    supplied = headers.get(TOKEN_HEADER, "")
    if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
        return 401, "missing or wrong token"
    return None


def read_json_object(handler) -> dict:
    body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", "0"))) or b"{}")
    if not isinstance(body, dict):
        raise ValueError("the request body must be a JSON object")
    return body


def tidy_step(target) -> str:
    return " ".join(str(target).lower().split())[:STEP_MAX_CHARS]


def site_of(url: str) -> str:
    address = urllib.parse.urlparse(url)
    if address.scheme not in ("http", "https") or not address.hostname:
        return ""
    try:
        port = address.port
    except ValueError:
        return ""
    return f"{address.hostname}:{port}" if port else address.hostname


def page_may_show(page, script, *args) -> bool:
    try:
        return bool(page.evaluate(script, *args))
    except PlaywrightError:
        return True


def on_sign_in_page(url: str) -> bool:
    address = urllib.parse.urlparse(url)
    path = address.path.lower()
    return (address.hostname or "") in SIGN_IN_HOSTS or any(marker in path for marker in SIGN_IN_PATHS + LOGIN_URLS)


def may_hold_secrets(page) -> bool:
    return (page_may_show(page, VISIBLE_PASSWORD_JS) or page_may_show(page, CARD_FIELD_JS, SECRET_FIELD_PATTERN)
            or page_may_show(page, VISIBLE_CHALLENGE_JS, list(CHALLENGE_FRAMES)))


def cacheable_page(page) -> bool:
    url = page.url.lower()
    if not site_of(url) or on_sign_in_page(url) or any(marker in url for marker in NO_CACHE_URLS + CHALLENGE_URLS):
        return False
    if may_hold_secrets(page):
        return False
    try:
        return not looks_like_bot_check(page, url, page.title(), page.inner_text("body"))
    except PlaywrightError:
        return False


def valid_selector_entry(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    selector, kind = entry.get("selector"), entry.get("kind")
    if not isinstance(selector, str) or not isinstance(kind, str):
        return None
    if not 0 < len(selector) <= SELECTOR_MAX_CHARS or not 0 < len(kind) <= KIND_MAX_CHARS:
        return None
    return {"selector": selector, "kind": kind}


def stable_selector(element) -> dict | None:
    try:
        if element.count() != 1:
            return None
        return valid_selector_entry(element.evaluate(STABLE_SELECTOR_JS, SECRET_FIELD_PATTERN))
    except PlaywrightError as error:
        print(f"could not describe an element for the selector cache: {first_line(error)}", flush=True)
        return None


def selector_key(site: str, step: str) -> str:
    return f"{site}\n{step}"


class SelectorCache:
    def __init__(self, path: str, limit: int = SELECTOR_CACHE_LIMIT):
        self.path = path
        self.limit = limit
        self.entries = None

    def load(self) -> dict:
        if self.entries is None:
            self.entries = self.read_file()
        return self.entries

    def read_file(self) -> dict:
        try:
            with open(self.path) as handle:
                rows = json.load(handle).get("entries", [])
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError) as error:
            print(f"selector cache unreadable, starting empty: {first_line(error)}", flush=True)
            return {}
        entries = {}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and (entry := valid_selector_entry(row[1])):
                entries[row[0]] = entry
        return dict(list(entries.items())[-self.limit:])

    def get(self, site: str, step: str) -> dict | None:
        return self.load().get(selector_key(site, step))

    def remember(self, site: str, step: str, entry: dict):
        entries = self.load()
        key = selector_key(site, step)
        entries.pop(key, None)
        entries[key] = entry
        while len(entries) > self.limit:
            entries.pop(next(iter(entries)))
        self.save()

    def touch(self, site: str, step: str):
        entries = self.load()
        key = selector_key(site, step)
        if key in entries:
            entries[key] = entries.pop(key)
            self.save()

    def forget(self, site: str, step: str):
        if self.load().pop(selector_key(site, step), None) is not None:
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            write_private(self.path, json.dumps({"entries": [[key, entry] for key, entry in self.entries.items()]}))
        except OSError as error:
            print(f"could not save the selector cache: {first_line(error)}", flush=True)


def chrome_listening() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1):
            return True
    except OSError:
        return False


def start_chrome():
    """Starts the Google Chrome Arty uses, in its own session so it outlives this agent."""
    subprocess.Popen(
        [CHROME, f"--user-data-dir={PROFILE}", "--password-store=basic", "--no-first-run", "--start-maximized",
         f"--remote-debugging-port={DEBUG_PORT}", "--remote-debugging-address=127.0.0.1", "--hide-crash-restore-bubble",
         # A page restored from the back/forward cache can't be read by the agent ("back" would hang).
         "--disable-back-forward-cache"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + CHROME_START_SECONDS
    while time.time() < deadline:
        if chrome_listening():
            return
        time.sleep(0.5)
    raise RuntimeError("Google Chrome didn't start")


def clock(seconds):
    minutes, seconds = divmod(int(seconds or 0), 60)
    return f"{minutes}:{seconds:02d}"


def describe_media(state):
    if state["ended"]:
        return "the video has ended"
    if state["paused"]:
        return f"paused at {clock(state['time'])} (pressing play didn't start it)"
    length = f" of {clock(state['duration'])}" if state["duration"] else ""
    return f"playing, at {clock(state['time'])}{length}" + (", but muted" if state["muted"] else "")


class Browser:
    """Arty's browser: Google Chrome on the desktop, the same as the "Sign in to websites" window, which this
    agent attaches to through Chrome's debugging port (localhost only) instead of launching it with an automation
    library's flags. Google signs out browsers that look automated, so this keeps the user's sign-ins; and the
    browser keeps running when the agent restarts (on a deploy), with its sessions still live."""

    def __init__(self):
        self.playwright = sync_playwright().start()
        self.browser = None
        self.context = None
        self.selectors = SelectorCache(SELECTOR_CACHE_FILE)
        self.pending_selector = None
        self.used_saved_selector = False
        self.crashed_tabs = set()

    def page(self):
        if self.browser is None or not self.browser.is_connected() or not self.browser.contexts:
            self.attach()
        self.close_crashed_tabs()
        if not self.context.pages or self.context.pages[-1] in self.crashed_tabs:
            self.context.new_page()
        return self.context.pages[-1]

    def watch_for_crash(self, tab):
        tab.on("crash", self.remember_crash)

    def remember_crash(self, tab):
        self.crashed_tabs.add(tab)

    def close_crashed_tabs(self):
        for tab in [tab for tab in self.context.pages if tab in self.crashed_tabs]:
            print(f"the tab on {tab.url} crashed; closing it so the next page opens in a new tab", flush=True)
            try:
                tab.close()
            except PlaywrightError as error:
                print(f"could not close the crashed tab: {first_line(error)}", flush=True)
                continue
            self.crashed_tabs.discard(tab)

    def close_other_tabs(self, working):
        try:
            self.close_tabs_except(working)
        except PlaywrightError as error:
            print(f"could not tidy away the other tabs: {first_line(error)}", flush=True)

    def close_tabs_except(self, working):
        working_session = self.context.new_cdp_session(working)
        working_id = working_session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        working_session.detach()
        session = self.browser.new_browser_cdp_session()
        try:
            for target in session.send("Target.getTargets")["targetInfos"]:
                if target["type"] == "page" and target["targetId"] != working_id:
                    self.close_target(session, target)
        finally:
            session.detach()
        self.crashed_tabs = {tab for tab in self.crashed_tabs if tab is working}

    @staticmethod
    def close_target(session, target):
        try:
            session.send("Target.closeTarget", {"targetId": target["targetId"]})
        except PlaywrightError as error:
            print(f"could not close the tab on {target['url']}: {first_line(error)}", flush=True)

    def attach(self):
        if not chrome_listening():
            start_chrome()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        self.context = self.browser.contexts[0]
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)
        self.crashed_tabs = set()
        for tab in self.context.pages:
            self.watch_for_crash(tab)
        self.context.on("page", self.watch_for_crash)

    def close(self):
        """Lets go of the browser but leaves it running, so its signed-in sessions survive the agent restarting."""
        if self.browser is not None:
            try:
                self.browser.close()  # for an attached browser this only disconnects
            except PlaywrightError:
                pass
            self.browser = self.context = None
        self.playwright.stop()
        print("let go of the browser (still running, sessions kept)", flush=True)

    def locate(self, page, target):
        target = str(target).strip()
        if target.isdigit():
            return page.locator(f'[data-agent-id="{target}"]').first
        step = tidy_step(target)
        if not step or PERSONAL_STEP.search(step) or not cacheable_page(page):
            return self.search(page, target)
        site = site_of(page.url)
        saved = self.saved_element(page, site, step)
        if saved is not None:
            self.pending_selector = (site, step, None)
            self.used_saved_selector = True
            return saved
        found = self.search(page, target)
        self.pending_selector = (site, step, stable_selector(found))
        return found

    def saved_element(self, page, site, step):
        saved = self.selectors.get(site, step)
        if saved is None:
            return None
        try:
            found = page.locator(saved["selector"])
            if found.count() == 1 and found.first.evaluate(MATCHES_STEP_JS, [step, saved["kind"]]):
                return found.first
        except PlaywrightError as error:
            print(f"saved selector on {site} failed to check: {first_line(error)}", flush=True)
        print(f"saved selector on {site} no longer fits its step, searching again", flush=True)
        self.selectors.forget(site, step)
        return None

    def search(self, page, target):
        exact = page.get_by_role("button", name=target).or_(page.get_by_role("link", name=target)).or_(page.get_by_label(target)).or_(page.get_by_placeholder(target))
        if exact.count():
            return exact.first
        return page.get_by_text(target).first

    def snapshot(self, page, with_text=True):
        page.wait_for_load_state("domcontentloaded")
        elements = page.evaluate(LIST_ELEMENTS_JS, ELEMENT_LIMIT)
        full_text = page.inner_text("body")
        title = page.title()
        result = {"title": title, "url": page.url, "text": full_text[:TEXT_LIMIT] if with_text else "", "elements": elements}
        reason = blocked_reason(page, title, full_text)
        if reason:
            result["blocked"] = reason
        return result

    def handle(self, action, args):
        self.pending_selector = None
        self.used_saved_selector = False
        try:
            result = self.act(action, args)
        except PlaywrightError:
            self.drop_saved_selector()
            raise
        self.keep_selector()
        if self.used_saved_selector and isinstance(result, dict):
            result["used_saved_selector"] = True
        return result

    def keep_selector(self):
        if self.pending_selector is None:
            return
        site, step, entry = self.pending_selector
        if self.used_saved_selector:
            self.selectors.touch(site, step)
        elif entry:
            self.selectors.remember(site, step, entry)

    def drop_saved_selector(self):
        if self.used_saved_selector and self.pending_selector is not None:
            self.selectors.forget(*self.pending_selector[:2])

    def act(self, action, args):
        if action == "upload" and (reason := private_upload_reason(os.path.expanduser(str(args.get("path", ""))))):
            raise ValueError(reason)
        page = self.page()
        if action == "open":
            self.close_other_tabs(page)
            url = args["url"]
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            accept_cookies(page)
            return self.snapshot(page)
        if action == "read":
            return self.snapshot(page)
        if action == "click":
            self.locate(page, args["target"]).click()
            page.wait_for_timeout(1200)
            accept_cookies(page)
            return self.snapshot(page, with_text=False)
        if action == "type":
            field = self.locate(page, args["target"])
            field.click()
            if args.get("replace", True):
                field.fill("") if field.evaluate("e => !e.isContentEditable") else page.keyboard.press("Control+A")
            page.keyboard.type(args["text"], delay=25)
            if args.get("submit"):
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
            return self.snapshot(page, with_text=False)
        if action == "press":
            page.keyboard.press(args["keys"])
            page.wait_for_timeout(800)
            return self.snapshot(page, with_text=False)
        if action == "scroll":
            page.mouse.wheel(0, 700 if args.get("direction", "down") == "down" else -700)
            page.wait_for_timeout(600)
            return self.snapshot(page)
        if action == "download":
            return self.download(page, str(args["target"]).strip())
        if action == "upload":
            path = os.path.expanduser(args["path"])
            if not os.path.isfile(path):
                raise ValueError(f"no such file: {path}")
            target = self.locate(page, args["target"])
            try:
                with page.expect_file_chooser(timeout=5000) as chooser:
                    target.click()
                chooser.value.set_files(path)
            except PlaywrightError:
                target.set_input_files(path)
            page.wait_for_timeout(1000)
            return self.snapshot(page, with_text=False)
        if action == "linkedin_posts":
            return self.linkedin_posts(page, args)
        if action == "play_media":
            return self.play_media(page)
        if action == "back":
            page.go_back(wait_until="domcontentloaded")
            return self.snapshot(page)
        raise ValueError(f"unknown action {action}")


    def play_media(self, page):
        page.wait_for_load_state("domcontentloaded")
        state = None
        for _ in range(MEDIA_WAIT_TRIES):
            state = page.evaluate(MEDIA_STATE_JS)
            if state:
                break
            page.wait_for_timeout(MEDIA_WAIT_MS)
        result = self.snapshot(page, with_text=False)
        if not state:
            result["media"] = "no video or audio player on this page"
            return result
        if state["paused"]:
            page.mouse.click(state["x"], state["y"])
            page.wait_for_timeout(MEDIA_WAIT_MS)
            state = page.evaluate(MEDIA_STATE_JS) or state
        result["media"] = describe_media(state)
        return result

    def linkedin_posts(self, page, args):
        """Read a LinkedIn profile's posts at a human pace: expand "see more", scroll, de-duplicate."""
        profile = str(args.get("profile") or "me").strip().rstrip("/")
        if profile.startswith("http"):
            url = profile if "/recent-activity/" in profile else f"{profile}/recent-activity/shares/"
        else:
            url = f"https://www.linkedin.com/in/{profile.split('/')[-1]}/recent-activity/shares/"
        wanted = max(1, min(int(args.get("max", 20)), POSTS_MAX))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        snapshot = self.snapshot(page, with_text=False)
        if snapshot.get("blocked"):
            return snapshot
        posts, seen, unchanged = [], set(), 0
        for _ in range(POSTS_SCROLLS):
            for button in page.locator("button.feed-shared-inline-show-more-text__see-more-less-toggle").all()[:10]:
                try:
                    if "more" in (button.inner_text() or "").lower():
                        button.click(timeout=2000)
                except PlaywrightError:
                    pass
            before = len(posts)
            for post in page.evaluate(POSTS_JS):
                key = post["urn"] or post["text"][:80]
                if post["text"] and key not in seen:
                    seen.add(key)
                    post["text"] = re.sub(r"hashtag\s*#", "#", post["text"])
                    post["repost"] = "reposted this" in post.pop("header", "").lower()
                    posts.append(post)
            unchanged = unchanged + 1 if len(posts) == before else 0
            if len(posts) >= wanted or unchanged >= 3:
                break
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(random.randint(2500, 4500))
        return {"title": page.title(), "url": page.url, "posts": posts[:wanted]}

    def download(self, page, target):
        """Saves a file into ~/Downloads: a URL, or a link on the page (by number or text). It's fetched with the
        browser's own cookies, so files behind a sign-in work; a plain Chrome would open a PDF link in its viewer
        rather than download it. Only a button that isn't a link waits for the browser's own download."""
        os.makedirs(DOWNLOADS, exist_ok=True)
        url = target if target.startswith(("http://", "https://")) else None
        if url is None:
            element = self.locate(page, target)
            href = element.get_attribute("href")
            if not href:
                with page.expect_download(timeout=DOWNLOAD_WAIT_MS) as pending:
                    element.click()
                return self.save(page, pending.value.suggested_filename, pending.value.save_as)
            url = urllib.parse.urljoin(page.url, href)
        response = self.context.request.get(url, timeout=DOWNLOAD_WAIT_MS)
        if not response.ok:
            raise ValueError(f"the download failed: {response.status} {response.status_text}")
        disposition = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', response.headers.get("content-disposition", ""))
        name = urllib.parse.unquote(disposition.group(1)) if disposition else urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
        body = response.body()

        def write(path):
            with open(path, "wb") as out:
                out.write(body)
        return self.save(page, name, write)

    def save(self, page, suggested, write):
        name = os.path.basename(suggested or "") or "download"
        stem, ext = os.path.splitext(name)
        path, copy = os.path.join(DOWNLOADS, name), 1
        while os.path.exists(path):
            path, copy = os.path.join(DOWNLOADS, f"{stem} ({copy}){ext}"), copy + 1
        write(path)
        result = self.snapshot(page, with_text=False)
        result["saved"] = path
        return result


class GmailWindow:
    """Hands Gmail jobs to the extension in the Gmail window and waits for its answers. The extension asks for work
    (POST /job, held open a while) and posts back what the page showed (POST /result), on 127.0.0.1 only."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.results = {}
        self.waiting = set()
        self.answered = threading.Condition()
        self.last_asked = 0.0

    def ask(self, action, args):
        if action == "gmail_unread":
            action, args = "gmail_list", {**args, "query": GMAIL_UNREAD_QUERY}
        if action == "gmail_tabs":  # for checking the Gmail window: its tabs, without any mail
            action = "tabs"
        self.make_sure_open()
        job_id = secrets.token_hex(16)
        job = {"id": job_id, "action": action, "args": args, "expires": time.time() + GMAIL_JOB_SECONDS}
        with self.answered:
            self.waiting.add(job_id)
        self.jobs.put(job)
        with self.answered:
            # Half the time, then once more: a job handed to an extension that had just restarted is lost without
            # an error. Gmail jobs only read, so doing one twice is harmless.
            for _ in range(2):
                if self.answered.wait_for(lambda: job_id in self.results, GMAIL_JOB_SECONDS / 2):
                    return self.collect(job_id)
                self.jobs.put(job)
            self.collect(job_id)
            raise RuntimeError("the Gmail window on my desktop didn't answer in time")

    def collect(self, job_id):
        with self.answered:
            self.waiting.discard(job_id)
            return self.results.pop(job_id, None)

    def connected(self):
        return time.time() - self.last_asked < GMAIL_CONNECTED_SECONDS

    def make_sure_open(self):
        # The window may have been closed since the extension last asked for work: check it's running first.
        running = subprocess.run(["pgrep", "-f", f"[u]ser-data-dir={GMAIL_PROFILE}"], capture_output=True).returncode == 0
        if running and self.connected():
            return
        started = time.time()
        if not running:
            open_gmail_window()
        deadline = started + GMAIL_START_SECONDS
        while time.time() < deadline:
            if self.last_asked >= started:  # the extension in the window as it is now
                return
            time.sleep(0.5)
        raise RuntimeError("the Gmail window on my desktop isn't answering (its reader extension isn't running)")

    def next_job(self):
        """The next job that hasn't been given up on, or None after GMAIL_POLL_SECONDS."""
        self.last_asked = time.time()
        deadline = time.time() + GMAIL_POLL_SECONDS
        while (left := deadline - time.time()) > 0:
            try:
                job = self.jobs.get(timeout=left)
            except queue.Empty:
                return None
            if job["expires"] > time.time() and job["id"] in self.waiting:
                return job
        return None

    def put_back(self, job):
        """A job whose hand-over failed (the extension restarted mid-request): the next request gets it."""
        self.jobs.put(job)

    def answer(self, job_id, result) -> bool:
        with self.answered:
            if job_id not in self.waiting or job_id in self.results:
                return False
            self.results[job_id] = result
            self.answered.notify_all()
        self.last_asked = time.time()
        return True


def private_upload_reason(path: str) -> str | None:
    real = os.path.realpath(path)
    for folder in PRIVATE_FOLDERS:
        root = os.path.realpath(folder)
        if real == root or real.startswith(root + os.sep):
            return f"{path} is in a private folder ({folder}), so it can't be uploaded"
    return None


def open_gmail_window(url="https://mail.google.com/mail/u/0/"):
    """The Gmail window: an ordinary Google Chrome (no debugging port) on its own profile, with the reader extension.
    Opened by the same script as the desktop's "Sign in to Google (Gmail)" icon, which holds its settings."""
    subprocess.Popen([GMAIL_LAUNCHER, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


class GmailHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        refused = refusal(self.headers, GMAIL_PORT, AGENT_TOKEN, extension_origin_allowed=True)
        if refused:
            self.reply(refused[0], {"error": refused[1]})
            return
        if self.path == "/job":
            job = gmail.next_job()
            if job is None:
                self.send_response(204)
                self.end_headers()
                return
            try:
                self.reply(200, {key: job[key] for key in ("id", "action", "args")})
            except (BrokenPipeError, ConnectionResetError):
                gmail.put_back(job)
            return
        if self.path == "/result":
            try:
                body = read_json_object(self)
                accepted = gmail.answer(str(body["id"]), body.get("result") or {})
            except (KeyError, TypeError, ValueError) as error:
                self.reply(400, {"error": str(error)})
                return
            if not accepted:
                self.reply(404, {"error": "no job is waiting with that id"})
                return
            self.reply(200, {"ok": True})
            return
        self.reply(404, {"error": "unknown path"})

    def reply(self, status, result):
        data = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


browser = Browser()
gmail = GmailWindow()
AGENT_TOKEN = ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        refused = refusal(self.headers, PORT, AGENT_TOKEN)
        if refused:
            self.reply(refused[0], {"error": refused[1]})
            return
        try:
            body = read_json_object(self)
            action = self.path.strip("/")
            if action in GMAIL_ACTIONS:  # never through the main browser: see GmailWindow
                result = gmail.ask(action, body)
                self.reply(200, result)
                return
            try:
                result = browser.handle(action, body)
            except PlaywrightError as error:
                if "has been closed" not in str(error):
                    raise
                # The window was closed under us (e.g. by the "Sign in to websites" shortcut): reopen once.
                browser.context = None
                result = browser.handle(action, body)
            status = 200
        except (PlaywrightError, KeyError, ValueError, RuntimeError, TypeError, AttributeError) as error:
            result, status = {"error": first_line(error)}, 400
        except OSError as error:
            result, status = {"error": f"the desktop couldn't do that: {first_line(error)}"}, 500
        self.reply(status, result)

    reply = GmailHandler.reply


def first_line(error: Exception) -> str:
    lines = str(error).splitlines()
    return (lines[0] if lines else type(error).__name__)[:400]


def stop(_signal, _frame):
    raise SystemExit(0)


if __name__ == "__main__":
    # Deploys and the sign-in shortcut stop the agent with SIGTERM: close the browser cleanly first.
    signal.signal(signal.SIGTERM, stop)
    AGENT_TOKEN = load_or_make_token()
    share_token(AGENT_TOKEN)
    gmail_server = ThreadingHTTPServer(("127.0.0.1", GMAIL_PORT), GmailHandler)
    gmail_server.daemon_threads = True
    threading.Thread(target=gmail_server.serve_forever, daemon=True).start()
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    finally:
        browser.close()
