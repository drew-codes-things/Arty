import difflib
import logging
import re
import unicodedata
from urllib.parse import urlparse

import notify
import web_pages
from briefing import GUEST
from job_gate import quiet_now
from job_types import JobFailed, Run
from reply_rules import TOOL_FAILED

PAGE_TOOL = "fetch__fetch"
SERVER_TOOL = "homelab__service_status"
PAGE_CHARS = 50000
KEPT_CHARS = 20000
DIFF_WORDS = 1500
SNIPPET_CHARS = 100
MIN_SECONDS_BETWEEN_CHANGE_ALERTS = 3600
DOWN_CHECKS_BEFORE_ALERT = 2
MAX_URL_CHARS = 500
MAX_PHRASE_CHARS = 100
LABEL_REACH_CHARS = 200
PAGE_HEADER = re.compile(r"\AContents of [^\n]*:\n")
PRICE = re.compile(r"(?P<symbol>[£$€])\s?(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
KUMA_LINE = re.compile(r"^(?P<name>.+?): (?P<state>DOWN|up|pending|maintenance)\b", re.MULTILINE | re.IGNORECASE)
WEB_SCHEMES = ("http", "https")
log = logging.getLogger("brain.watchers")


def plain(text: str, limit: int) -> str:
    kept = "".join(" " if character.isspace() else character for character in str(text)
                   if character.isspace() or not unicodedata.category(character).startswith("C"))
    return " ".join(kept.split())[:limit]


def link_problem(url: str) -> str | None:
    if not isinstance(url, str) or len(url) > MAX_URL_CHARS:
        return "Give the page's address (a web link under 500 characters)."
    parsed = urlparse(url.strip())
    if parsed.scheme not in WEB_SCHEMES or not parsed.hostname:
        return "A watcher needs a web link starting with http:// or https://."
    if parsed.username or parsed.password:
        return "A link with a name or password in it can't be watched. Leave those out."
    if web_pages.address_problem(url.strip()):
        return "A watcher only reads public websites: give the page's plain web address, not one on this computer or the home network."
    return None


def host_of(url: str) -> str:
    return urlparse(url).hostname or "the page"


def read_page(run: Run) -> str:
    url = run.spec["url"]
    result = str(run.call(PAGE_TOOL, {"url": url, "max_length": PAGE_CHARS}))
    if not result.strip() or TOOL_FAILED.search(result):
        reason = plain(result, 100) or "no answer"
        raise JobFailed(f"couldn't read {host_of(url)} ({reason})")
    return plain(PAGE_HEADER.sub("", result, count=1), PAGE_CHARS)


def may_alert_now(run: Run) -> bool:
    return not quiet_now(run.services.settings, run.now)


def gap_has_passed(run: Run) -> bool:
    return run.now.timestamp() - run.state.get("last_alert", 0) >= MIN_SECONDS_BETWEEN_CHANGE_ALERTS


def change_summary(before: str, after: str) -> str:
    old_words, new_words = before.split()[:DIFF_WORDS], after.split()[:DIFF_WORDS]
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=True)
    added = removed = 0
    first_added = ""
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += old_end - old_start
        added += new_end - new_start
        if not first_added and new_end > new_start:
            first_added = " ".join(new_words[new_start:new_end])
    counts = f"about {added} words added and {removed} removed"
    return f'{counts}. New text starts: "{plain(first_added, SNIPPET_CHARS)}"' if first_added else counts


def check_page(run: Run) -> None:
    text = read_page(run)
    phrase = run.spec.get("phrase")
    if phrase:
        watch_phrase(run, text, phrase)
    else:
        watch_changes(run, text)


def watch_phrase(run: Run, text: str, phrase: str) -> None:
    present = phrase.lower() in text.lower()
    was_present = run.state.get("present", False)
    if not present:
        run.state["present"] = False
        return
    if was_present or not may_alert_now(run):
        return
    run.state["present"] = True
    run.tell("Page now says it", f'{host_of(run.spec["url"])} now mentions "{plain(phrase, MAX_PHRASE_CHARS)}". {run.spec["url"]}')


def watch_changes(run: Run, text: str) -> None:
    current = text[:KEPT_CHARS]
    baseline = run.state.get("text")
    if baseline is None:
        run.state["text"] = current
        return
    if current == baseline:
        run.state["pending"] = False
        return
    run.state["pending"] = True
    if not (gap_has_passed(run) and may_alert_now(run)):
        return
    run.tell("Page changed", f"{host_of(run.spec['url'])} changed: {change_summary(baseline, current)}\n{run.spec['url']}")
    run.state.update(text=current, pending=False, last_alert=run.now.timestamp())


def find_price(text: str, label: str | None) -> tuple[str, float] | None:
    if label:
        start = text.lower().find(label.lower())
        if start < 0:
            return None
        text = text[start:start + len(label) + LABEL_REACH_CHARS]
    found = PRICE.search(text)
    return (found["symbol"], float(found["amount"].replace(",", ""))) if found else None


CURRENCY_CODE = re.compile(r"[A-Z]{3}")
SIGN_FOR_CURRENCY = {"GBP": "£", "USD": "$", "EUR": "€"}


def shown_currency(currency: str) -> str:
    code = str(currency or "").strip().upper()
    if code in SIGN_FOR_CURRENCY:
        return SIGN_FOR_CURRENCY[code]
    return f"{code} " if CURRENCY_CODE.fullmatch(code) else ""


def structured_price(run: Run) -> tuple[tuple[str, float] | None, str]:
    reader = run.services.read_price
    if reader is None or run.spec.get("label"):
        return None, ""
    try:
        reading = reader(run.spec["url"])
    except Exception as error:
        log.warning("reading the price data of %s failed: %s: %s", host_of(run.spec["url"]), type(error).__name__, error)
        return None, "its price data couldn't be read"
    offer = reading.offer
    if reading.problem or offer is None or offer.price is None:
        return None, plain(reading.problem, MAX_PHRASE_CHARS)
    return (shown_currency(offer.currency), float(offer.price)), ""


def price_from_text(run: Run, structured_problem: str) -> tuple[str, float]:
    label = run.spec.get("label")
    price = find_price(read_page(run), label)
    if price is not None:
        return price
    near = f' near "{plain(label, MAX_PHRASE_CHARS)}"' if label else ""
    why = f" ({structured_problem})" if structured_problem else ""
    raise JobFailed(f"couldn't find a price{near} on {host_of(run.spec['url'])}{why}")


def check_price(run: Run) -> None:
    price, structured_problem = structured_price(run)
    symbol, amount = price or price_from_text(run, structured_problem)
    below = float(run.spec["below"])
    run.state["last_price"] = amount
    if amount >= below:
        run.state["alerted"] = False
        return
    if run.state.get("alerted") or not may_alert_now(run):
        return
    run.state["alerted"] = True
    run.tell("Price dropped", f"{host_of(run.spec['url'])} is now {symbol}{amount:.2f}, under your {symbol}{below:.2f}. {run.spec['url']}")


def server_states(status: str) -> dict[str, bool | None]:
    states: dict[str, bool | None] = {}
    for line in KUMA_LINE.finditer(status):
        word = line["state"].lower()
        states[line["name"].strip().lower()] = True if word == "up" else False if word == "down" else None
    for guest in GUEST.finditer(status):
        states[guest["name"].lower()] = guest["state"] == "running"
    return states


def matching_server(states: dict[str, bool | None], wanted: str) -> str:
    wanted = wanted.strip().lower()
    if wanted in states:
        return wanted
    partial = [name for name in states if wanted in name]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise JobFailed(f"{wanted} matches several servers ({', '.join(sorted(partial)[:5])}); say which one")
    raise JobFailed(f"no server or monitor called {wanted}")


def check_server(run: Run) -> None:
    states = server_states(str(run.call(SERVER_TOOL, {})))
    if not states:
        raise JobFailed("the server status didn't list any machines")
    name = matching_server(states, run.spec["target"])
    up = states[name]
    if up is None:
        return
    if up:
        run.state["down_checks"] = 0
        if run.state.pop("reported_down", False):
            run.tell("Server back up", f"{name} is answering again.", category=notify.SERVER)
        return
    run.state["down_checks"] = run.state.get("down_checks", 0) + 1
    if run.state["down_checks"] >= DOWN_CHECKS_BEFORE_ALERT and not run.state.get("reported_down"):
        run.state["reported_down"] = True
        run.tell("Server down", f"{name} isn't answering.", urgent=True, category=notify.SERVER)
