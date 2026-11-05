"""A visible Google Chrome window the assistant drives by page content instead of pixels.

Runs inside the desktop session (DISPLAY :0) with a persistent profile, so logins
made by the user in this window are kept. Listens on 127.0.0.1 only; the workspace
tools reach it over SSH. Single-threaded on purpose: Playwright's sync API isn't
thread-safe, and one browser should do one thing at a time.
"""
import itertools
import json
import os
import queue
import random
import re
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
LOGIN_URLS = ("/login", "/signin", "/sign-in", "/uas/login", "/authwall", "accounts.google.com", "/auth/", "/sso")


VISIBLE_CHALLENGE_JS = """
(markers) => Array.from(document.querySelectorAll('iframe')).some(frame => {
  const src = (frame.src || '').toLowerCase();
  const box = frame.getBoundingClientRect();
  return markers.some(m => src.includes(m)) && box.width > 100 && box.height > 60 && getComputedStyle(frame).visibility !== 'hidden';
})
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


def blocked_reason(page, title, text):
    """Why a human is needed on this page (bot check or login wall), or None.
    Only visible challenge frames count: many sites embed an invisible reCAPTCHA on every page."""
    url = page.url.lower()
    head = f"{title}\n{text[:1500]}".lower()
    try:
        visible_challenge = page.evaluate(VISIBLE_CHALLENGE_JS, list(CHALLENGE_FRAMES))
    except PlaywrightError:
        visible_challenge = False
    if visible_challenge or any(marker in url for marker in CHALLENGE_URLS) \
            or any(phrase in head for phrase in CHALLENGE_PHRASES):
        return "bot check (CAPTCHA or human verification)"
    if any(marker in url for marker in LOGIN_URLS):
        return "login required"
    return None


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
         f"--remote-debugging-port={DEBUG_PORT}", "--remote-debugging-address=127.0.0.1",
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

    def page(self):
        if self.browser is None or not self.browser.is_connected() or not self.browser.contexts:
            self.attach()
        if not self.context.pages:
            self.context.new_page()
        return self.context.pages[-1]

    def attach(self):
        if not chrome_listening():
            start_chrome()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        self.context = self.browser.contexts[0]
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)

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
        page = self.page()
        if action == "open":
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
        self.answered = threading.Condition()
        self.ids = itertools.count(1)
        self.last_asked = 0.0

    def ask(self, action, args):
        if action == "gmail_unread":
            action, args = "gmail_list", {**args, "query": GMAIL_UNREAD_QUERY}
        if action == "gmail_tabs":  # for checking the Gmail window: its tabs, without any mail
            action = "tabs"
        self.make_sure_open()
        job_id = next(self.ids)
        job = {"id": job_id, "action": action, "args": args, "expires": time.time() + GMAIL_JOB_SECONDS}
        self.jobs.put(job)
        with self.answered:
            # Half the time, then once more: a job handed to an extension that had just restarted is lost without
            # an error. Gmail jobs only read, so doing one twice is harmless.
            for _ in range(2):
                if self.answered.wait_for(lambda: job_id in self.results, GMAIL_JOB_SECONDS / 2):
                    return self.results.pop(job_id)
                self.jobs.put(job)
            raise RuntimeError("the Gmail window on my desktop didn't answer in time")

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
            if job["expires"] > time.time():
                return job
        return None

    def put_back(self, job):
        """A job whose hand-over failed (the extension restarted mid-request): the next request gets it."""
        self.jobs.put(job)

    def answer(self, job_id, result):
        with self.answered:
            self.results[job_id] = result
            self.answered.notify_all()
        self.last_asked = time.time()


def open_gmail_window(url="https://mail.google.com/mail/u/0/"):
    """The Gmail window: an ordinary Google Chrome (no debugging port) on its own profile, with the reader extension.
    Opened by the same script as the desktop's "Sign in to Google (Gmail)" icon, which holds its settings."""
    subprocess.Popen([GMAIL_LAUNCHER, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


class GmailHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
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
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                gmail.answer(int(body["id"]), body.get("result") or {})
            except (KeyError, TypeError, ValueError) as error:
                self.reply(400, {"error": str(error)})
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
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
        except (PlaywrightError, KeyError, ValueError, RuntimeError) as error:
            result, status = {"error": str(error).splitlines()[0][:400]}, 400
        self.reply(status, result)

    reply = GmailHandler.reply


def stop(_signal, _frame):
    raise SystemExit(0)


if __name__ == "__main__":
    # Deploys and the sign-in shortcut stop the agent with SIGTERM: close the browser cleanly first.
    signal.signal(signal.SIGTERM, stop)
    gmail_server = ThreadingHTTPServer(("127.0.0.1", GMAIL_PORT), GmailHandler)
    gmail_server.daemon_threads = True
    threading.Thread(target=gmail_server.serve_forever, daemon=True).start()
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    finally:
        browser.close()
