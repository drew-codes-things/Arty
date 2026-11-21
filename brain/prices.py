import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article", "header", "footer", "nav",
              "aside", "main", "form", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "dt", "dd", "blockquote", "pre"}
LINE_BREAK = "\n"
HIDDEN_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
JSON_LD_TYPE = "application/ld+json"
TEXT_IDS = {"productTitle": "title", "title": "title", "availability": "availability", "merchant-info": "seller",
            "sellerProfileTriggerId": "seller", "merchantInfoFeature_feature_div": "seller",
            "priceblock_ourprice": "price", "priceblock_dealprice": "price", "price_inside_buybox": "price",
            "corePrice_feature_div": "price", "corePriceDisplay_desktop_feature_div": "price"}
PRICE_CLASS_WORDS = ("price", "a-offscreen")
NOT_THE_PRICE_WORDS = ("old", "was", "strike", "rrp", "list", "text-price", "save", "unit", "per-", "range", "filter")
RESULT_MARKER = ("data-component-type", "s-search-result")
SPONSORED_WORDS = re.compile(r"\bsponsored\b", re.I)
MAX_JSON_LD_BLOCKS = 20
MAX_JSON_LD_CHARS = 200_000
MAX_WALK_DEPTH = 8
MAX_FIELD_CHARS = 200
MAX_TEXT_CHARS = 400_000
MAX_LINKS = 400

CURRENCY_SIGNS = {"£": "GBP", "€": "EUR", "US$": "USD", "$": "USD", "¥": "JPY", "₹": "INR"}
SIGN_FOR = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥", "INR": "₹"}
CURRENCY_CODES = ("GBP", "EUR", "USD", "JPY", "INR", "AUD", "CAD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN")
NUMBER = r"\d{1,3}(?:[,.\xa0]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
SIGN_PATTERN = "|".join(re.escape(sign) for sign in sorted(CURRENCY_SIGNS, key=len, reverse=True))
CODE_PATTERN = "|".join(CURRENCY_CODES)
PRICE_IN_TEXT = re.compile(
    rf"(?P<sign>{SIGN_PATTERN})\s?(?P<signed>{NUMBER})"
    rf"|(?P<before>{NUMBER})\s?(?P<after_sign>{SIGN_PATTERN})"
    rf"|\b(?P<code>{CODE_PATTERN})\s?(?P<coded>{NUMBER})"
    rf"|(?P<before_code>{NUMBER})\s?(?P<after_code>{CODE_PATTERN})\b")
SOLD_BY = re.compile(r"\b(?:sold by|seller:)\s*(?P<seller>[^\n.|]{2,60}?)(?=\s*(?:\.|\||\n|and fulfilled|$))", re.I)
AVAILABILITY_TEXT = re.compile(r"\b(in stock|out of stock|currently unavailable|temporarily out of stock|"
                               r"only \d+ left in stock|pre-?order|available to order|sold out|back order)\b", re.I)
SCHEMA_AVAILABILITY = {"instock": "in stock", "outofstock": "out of stock", "preorder": "pre-order",
                       "presale": "pre-order", "backorder": "back order", "limitedavailability": "limited stock",
                       "soldout": "sold out", "discontinued": "discontinued", "instoreonly": "in store only",
                       "onlineonly": "in stock (online only)", "madetoorder": "made to order"}
META_TITLE = ("og:title", "twitter:title")
META_PRICE = ("product:price:amount", "og:price:amount", "product:sale_price:amount")
META_CURRENCY = ("product:price:currency", "og:price:currency", "product:sale_price:currency")
META_AVAILABILITY = ("product:availability", "og:availability")
META_URL = ("og:url",)

BOT_CHECK_STRONG = re.compile(
    r"enter the characters you see below|type the characters you see in this image|/errors/validatecaptcha|"
    r"to discuss automated access to amazon data|are you a robot|verify (?:that )?you are (?:a )?human|"
    r"unusual traffic from your (?:computer|network)|checking your browser before accessing|"
    r"attention required! \| cloudflare|cf-chl-|just a moment\.\.\.|please enable cookies and javascript|"
    r"press (?:&amp; |and |& )hold to confirm|px-captcha", re.I)
BLOCKED_PAGE = re.compile(r"request blocked|access denied|automated access|too many requests", re.I)
CAPTCHA_WORD = re.compile(r"captcha", re.I)
SHORT_PAGE_CHARS = 1500
JAVASCRIPT_SHELL_CHARS = 250
BARE_TEXT_CHARS = 40
ENABLE_JAVASCRIPT = re.compile(r"enable javascript|javascript is (?:disabled|required)|requires javascript", re.I)

PRODUCT_PATH = re.compile(r"/(?:dp|gp/product)/[A-Z0-9]{10}\b|/product/|/products/|/p/[\w-]+|/itm/|/ip/|/prd/", re.I)
ASIN = re.compile(r"^[A-Z0-9]{10}$")
QUERY_WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = {"the", "a", "an", "and", "or", "for", "with", "of", "to", "in", "on", "by", "price", "cheapest", "best", "buy",
              "new", "uk", "how", "much", "is"}
MIN_RESULT_TITLE_CHARS = 8


@dataclass(frozen=True)
class Offer:
    title: str
    price: Decimal | None
    currency: str
    availability: str
    seller: str
    url: str
    source: str
    read_at: str

    def price_text(self) -> str:
        if self.price is None:
            return "no price shown"
        amount = f"{self.price:,.2f}"
        sign = SIGN_FOR.get(self.currency)
        return f"{sign}{amount}" if sign else f"{amount} {self.currency}".strip()

    def as_dict(self) -> dict:
        return {"title": self.title, "price": None if self.price is None else str(self.price), "currency": self.currency,
                "availability": self.availability, "seller": self.seller, "url": self.url, "source": self.source,
                "read_at": self.read_at}


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    price_text: str
    sponsored: bool


class _Capture:
    def __init__(self, key, into):
        self.key, self.into, self.parts = key, into, []

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class PageParts(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts, self.text_parts = [], []
        self.meta, self.itemprops, self.texts = {}, {}, {}
        self.json_ld, self.links, self.results = [], [], []
        self.canonical = ""
        self.has_noscript = False
        self.script_count = 0
        self._stack = []
        self._hidden = 0
        self._in_title = False
        self._in_json_ld = False
        self._json_parts = []
        self._text_chars = 0

    def _open_captures(self, tag, attributes):
        found = []
        element_id = attributes.get("id") or ""
        classes = set((attributes.get("class") or "").split())
        result = self._current_result()
        if attributes.get(RESULT_MARKER[0]) == RESULT_MARKER[1] and attributes.get("data-asin"):
            entry = {"asin": attributes["data-asin"], "title": [], "prices": [], "links": [], "text": [],
                     "sponsored": "AdHolder" in classes}
            self.results.append(entry)
            found.append(_Capture("result", entry))
        elif result is not None and tag == "h2":
            found.append(_Capture("result_title", result))
        elif result is not None and "a-offscreen" in classes:
            found.append(_Capture("result_price", result))
        if tag == "a" and attributes.get("href") and len(self.links) < MAX_LINKS:
            entry = [attributes["href"], ""]
            self.links.append(entry)
            found.append(_Capture("link", entry))
            if result is not None:
                result["links"].append(attributes["href"])
        if element_id in TEXT_IDS:
            found.append(_Capture(TEXT_IDS[element_id], self.texts))
        elif is_price_class(classes):
            found.append(_Capture("price_class", self.texts))
        if tag == "h1":
            found.append(_Capture("h1", self.texts))
        prop = attributes.get("itemprop")
        if prop and prop not in self.itemprops:
            value = attributes.get("content") or attributes.get("href") or attributes.get("src")
            if value is not None:
                self.itemprops[prop] = value.strip()
            elif tag not in VOID_TAGS:
                found.append(_Capture(f"itemprop:{prop}", self.itemprops))
        return found

    def _current_result(self):
        for _tag, captures in reversed(self._stack):
            for capture in captures:
                if capture.key == "result":
                    return capture.into
        return None

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "meta":
            self._note_meta(attributes)
            return
        if tag == "link":
            self._note_link_tag(attributes)
            return
        if tag == "script":
            self.script_count += 1
            self._in_json_ld = attributes.get("type", "").lower() == JSON_LD_TYPE and len(self.json_ld) < MAX_JSON_LD_BLOCKS
            self._json_parts = []
        if tag == "noscript":
            self.has_noscript = True
        if tag in BLOCK_TAGS:
            self.text_parts.append(LINE_BREAK)
        if tag == "title" and not self._stack_has("svg"):
            self._in_title = True
        if tag in VOID_TAGS:
            return
        if tag in HIDDEN_TAGS:
            self._hidden += 1
        self._stack.append((tag, self._open_captures(tag, attributes)))

    def _stack_has(self, tag):
        return any(open_tag == tag for open_tag, _ in self._stack)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_parts)[:MAX_JSON_LD_CHARS])
            self._in_json_ld = False
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self.text_parts.append(LINE_BREAK)
        if not any(open_tag == tag for open_tag, _ in self._stack):
            return
        while self._stack:
            open_tag, captures = self._stack.pop()
            if open_tag in HIDDEN_TAGS:
                self._hidden -= 1
            for capture in captures:
                self._finish(capture)
            if open_tag == tag:
                break

    def _finish(self, capture):
        text = capture.text()[:MAX_FIELD_CHARS]
        if capture.key == "result":
            capture.into["text"].append(text)
        elif capture.key == "result_title":
            capture.into["title"].append(text)
        elif capture.key == "result_price":
            capture.into["prices"].append(text)
        elif capture.key == "link":
            capture.into[1] = text
        elif capture.key == "price_class":
            capture.into.setdefault("price_class", text)
        elif text:
            capture.into.setdefault(capture.key.removeprefix("itemprop:"), text)

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_parts.append(data)
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._hidden:
            return
        for _tag, captures in self._stack:
            for capture in captures:
                capture.parts.append(data)
        if self._text_chars < MAX_TEXT_CHARS:
            self.text_parts.append(data)
            self._text_chars += len(data)

    def _note_meta(self, attributes):
        key = (attributes.get("property") or attributes.get("name") or "").lower()
        if key and key not in self.meta and "content" in attributes:
            self.meta[key] = attributes["content"].strip()
        prop = attributes.get("itemprop")
        if prop and prop not in self.itemprops and "content" in attributes:
            self.itemprops[prop] = attributes["content"].strip()

    def _note_link_tag(self, attributes):
        if "canonical" in (attributes.get("rel") or "").lower().split() and not self.canonical:
            self.canonical = attributes.get("href", "").strip()
        prop = attributes.get("itemprop")
        if prop and prop not in self.itemprops and attributes.get("href"):
            self.itemprops[prop] = attributes["href"].strip()

    def close(self):
        super().close()
        while self._stack:
            _tag, captures = self._stack.pop()
            for capture in captures:
                self._finish(capture)

    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in " ".join(self.text_parts).split(LINE_BREAK))
        return LINE_BREAK.join(line for line in lines if line)


def is_price_class(classes: set[str]) -> bool:
    lowered = [name.lower() for name in classes]
    return (any(word in name for name in lowered for word in PRICE_CLASS_WORDS)
            and not any(word in name for name in lowered for word in NOT_THE_PRICE_WORDS))


def parse(html: str, base_url: str) -> PageParts:
    parts = PageParts(base_url)
    parts.feed(html or "")
    parts.close()
    return parts


def parse_amount(text) -> Decimal | None:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        text = str(text)
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned or not cleaned[0].isdigit():
        return None
    if "," in cleaned and "." in cleaned:
        decimal_mark = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
    elif cleaned.count(",") == 1 and re.search(r",\d{1,2}$", cleaned):
        decimal_mark = ","
    else:
        decimal_mark = "."
    thousands = "." if decimal_mark == "," else ","
    whole = cleaned.replace(thousands, "")
    if whole.count(decimal_mark) > 1:
        return None
    try:
        amount = Decimal(whole.replace(decimal_mark, "."))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def first_price(text: str) -> tuple[Decimal, str] | None:
    for match in PRICE_IN_TEXT.finditer(text or ""):
        sign = match.group("sign") or match.group("after_sign")
        code = match.group("code") or match.group("after_code")
        number = match.group("signed") or match.group("before") or match.group("coded") or match.group("before_code")
        amount = parse_amount(number)
        if amount is not None and amount > 0:
            return amount, CURRENCY_SIGNS.get(sign, "") if sign else code
    return None


def _schema_type(node: dict) -> set[str]:
    kinds = node.get("@type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    return {str(kind).rsplit("/", 1)[-1].lower() for kind in kinds if kind}


def _walk(node, depth=0):
    if depth > MAX_WALK_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("@graph", "mainEntity", "itemListElement", "item"):
        if key in node:
            yield from _walk(node[key], depth + 1)


def _json_blocks(parts: PageParts) -> list:
    blocks = []
    for raw in parts.json_ld:
        try:
            blocks.append(json.loads(raw.strip().rstrip(";")))
        except (ValueError, RecursionError):
            continue
    return blocks


def _products(parts: PageParts) -> list[dict]:
    return [node for block in _json_blocks(parts) for node in _walk(block)
            if _schema_type(node) & {"product", "productgroup", "individualproduct", "productmodel", "car", "book"}]


def _name(value) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, (str, dict))), "")
        return _name(value)
    return _clean(value)


def _clean(value) -> str:
    return " ".join(unescape(str(value or "")).split())[:MAX_FIELD_CHARS]


def availability_words(value) -> str:
    text = _clean(value)
    key = text.rsplit("/", 1)[-1].replace(" ", "").replace("_", "").lower()
    if key in SCHEMA_AVAILABILITY:
        return SCHEMA_AVAILABILITY[key]
    found = AVAILABILITY_TEXT.search(text)
    return found.group(1).lower() if found else text[:60].lower()


def _offers(product: dict) -> list[dict]:
    offers = product.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    found = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        found.append(offer)
        nested = offer.get("offers")
        if isinstance(nested, list):
            found += [item for item in nested if isinstance(item, dict)]
    return found


def _offer_price(offer: dict) -> Decimal | None:
    for key in ("price", "lowPrice", "highPrice"):
        amount = parse_amount(offer.get(key))
        if amount is not None:
            return amount
    spec = offer.get("priceSpecification")
    spec = spec[0] if isinstance(spec, list) and spec else spec
    return parse_amount(spec.get("price")) if isinstance(spec, dict) else None


def _offer_currency(offer: dict) -> str:
    spec = offer.get("priceSpecification")
    spec = spec[0] if isinstance(spec, list) and spec else spec
    currency = offer.get("priceCurrency") or (spec.get("priceCurrency") if isinstance(spec, dict) else "")
    return _clean(currency).upper()[:5]


def _from_structured(parts: PageParts) -> dict:
    for product in _products(parts):
        fields = {"title": _name(product.get("name"))}
        priced = [offer for offer in _offers(product) if _offer_price(offer) is not None]
        offer = min(priced, key=_offer_price) if priced else (_offers(product) or [{}])[0]
        if priced:
            fields.update(price=_offer_price(offer), currency=_offer_currency(offer))
        fields["availability"] = availability_words(offer.get("availability")) if offer.get("availability") else ""
        fields["seller"] = _name(offer.get("seller") or offer.get("offeredBy"))
        fields["url"] = _clean(offer.get("url") or product.get("url"))
        if fields.get("title") or fields.get("price") is not None:
            return fields
    props = parts.itemprops
    amount = parse_amount(props.get("price") or props.get("lowPrice"))
    if amount is None and not props.get("name"):
        return {}
    return {"title": _clean(props.get("name")), "price": amount, "currency": _clean(props.get("priceCurrency")).upper()[:5],
            "availability": availability_words(props.get("availability")) if props.get("availability") else "",
            "seller": _clean(props.get("seller")), "url": _clean(props.get("url"))}


def _first_meta(parts: PageParts, keys) -> str:
    return next((parts.meta[key] for key in keys if parts.meta.get(key)), "")


def _from_meta(parts: PageParts) -> dict:
    return {"title": _clean(_first_meta(parts, META_TITLE)), "price": parse_amount(_first_meta(parts, META_PRICE)),
            "currency": _clean(_first_meta(parts, META_CURRENCY)).upper()[:5],
            "availability": availability_words(_first_meta(parts, META_AVAILABILITY)) if _first_meta(parts, META_AVAILABILITY) else "",
            "url": _clean(_first_meta(parts, META_URL))}


def _from_text(parts: PageParts) -> dict:
    page_text = parts.text()
    price = first_price(parts.texts.get("price", "")) or first_price(parts.texts.get("price_class", ""))
    seller_text = parts.texts.get("seller", "")
    sold_by = SOLD_BY.search(seller_text) or SOLD_BY.search(page_text)
    availability = parts.texts.get("availability", "") or (AVAILABILITY_TEXT.search(page_text) or [""])[0]
    return {"title": _clean(parts.texts.get("title") or parts.texts.get("h1") or parts.title()),
            "price": price[0] if price else None, "currency": price[1] if price else "",
            "availability": availability_words(availability) if availability else "",
            "seller": _clean(sold_by.group("seller") if sold_by else seller_text)[:80]}


def same_site(url: str, other: str) -> bool:
    return registrable_name(url) != "" and registrable_name(url) == registrable_name(other)


TWO_PART_SUFFIXES = {"co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk", "nhs.uk", "sch.uk",
                     "police.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "org.nz", "net.nz", "ac.nz",
                     "govt.nz", "co.jp", "ne.jp", "or.jp", "ac.jp", "co.in", "net.in", "org.in", "com.br", "com.mx",
                     "co.za", "org.za", "com.tr", "com.sg", "com.cn", "com.hk", "com.tw", "co.kr", "co.il", "com.ar",
                     "com.my", "com.ph", "com.pk", "com.ng", "co.id", "com.vn", "com.ua", "com.pl", "com.es", "co.th",
                     "github.io", "gitlab.io", "pages.dev", "workers.dev", "netlify.app", "vercel.app", "web.app",
                     "firebaseapp.com", "herokuapp.com", "appspot.com", "blogspot.com", "wordpress.com", "tumblr.com",
                     "substack.com", "wixsite.com", "azurewebsites.net", "cloudfront.net", "onrender.com", "fly.dev",
                     "glitch.me", "surge.sh", "neocities.org", "ngrok.io", "ngrok-free.app", "repl.co", "deno.dev",
                     "readthedocs.io", "gitbook.io", "notion.site", "myshopify.com", "square.site", "carrd.co"}
THREE_PART_SUFFIXES = {"s3.amazonaws.com", "blob.core.windows.net"}


def registrable_name(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    labels = host.split(".")
    for size, suffixes in ((3, THREE_PART_SUFFIXES), (2, TWO_PART_SUFFIXES)):
        if len(labels) > size and ".".join(labels[-size:]) in suffixes:
            return ".".join(labels[-(size + 1):])
    return ".".join(labels[-2:])


def _page_link(parts: PageParts, page_url: str, *candidates: str) -> str:
    for candidate in (parts.canonical, *candidates):
        if not candidate:
            continue
        absolute = urljoin(page_url, candidate)
        if urlsplit(absolute).scheme in ("http", "https") and same_site(absolute, page_url):
            return absolute
    return page_url


def read_offer(html: str, url: str, read_at: datetime | None = None, parts: PageParts | None = None) -> Offer | None:
    parts = parts or parse(html, url)
    layers = [("structured data", _from_structured(parts)), ("page tags", _from_meta(parts)), ("page text", _from_text(parts))]
    merged, source = {}, ""
    for name, fields in layers:
        if not source and fields.get("price") is not None:
            source = name
            merged["price"], merged["currency"] = fields["price"], fields.get("currency", "")
        for key, value in fields.items():
            if key not in ("price", "currency") and value and not merged.get(key):
                merged[key] = value
    if merged.get("price") is None and not merged.get("title"):
        return None
    stamp = (read_at or datetime.now().astimezone()).isoformat(timespec="seconds")
    return Offer(title=merged.get("title", ""), price=merged.get("price"), currency=merged.get("currency", ""),
                 availability=merged.get("availability", ""), seller=merged.get("seller", ""),
                 url=_page_link(parts, url, merged.get("url", "")), source=source or "no price found", read_at=stamp)


def offer_from_text(text: str, url: str, read_at: datetime | None = None) -> Offer | None:
    price = first_price(text)
    if price is None:
        return None
    availability = AVAILABILITY_TEXT.search(text or "")
    sold_by = SOLD_BY.search(text or "")
    stamp = (read_at or datetime.now().astimezone()).isoformat(timespec="seconds")
    return Offer(title="", price=price[0], currency=price[1], availability=availability.group(1).lower() if availability else "",
                 seller=_clean(sold_by.group("seller")) if sold_by else "", url=url, source="page text", read_at=stamp)


def bot_check(html: str, parts: PageParts | None = None) -> str:
    parts = parts or parse(html, "")
    seen = f"{parts.title()} {parts.text()[:SHORT_PAGE_CHARS * 2]}"
    is_short = len(parts.text()) < SHORT_PAGE_CHARS
    marked = BOT_CHECK_STRONG.search(seen) or (is_short and BOT_CHECK_STRONG.search((html or "")[:20000]))
    if marked or (is_short and CAPTCHA_WORD.search(seen)):
        return "a CAPTCHA" if CAPTCHA_WORD.search(html or "") else "a bot check"
    if is_short and BLOCKED_PAGE.search(seen):
        return "a block on automated visits"
    return ""


def needs_javascript(html: str, parts: PageParts | None = None) -> bool:
    parts = parts or parse(html, "")
    text = parts.text()
    if len(text) >= JAVASCRIPT_SHELL_CHARS or parts.json_ld:
        return False
    return bool(ENABLE_JAVASCRIPT.search(html or "")) or (parts.script_count > 0 and len(text) < BARE_TEXT_CHARS)


def query_words(text: str) -> set[str]:
    return {word for word in QUERY_WORD.findall(str(text or "").lower()) if word not in STOP_WORDS}


def matches_query(title: str, words: set[str]) -> bool:
    if not words:
        return True
    shared = len(words & query_words(title))
    return shared >= min(len(words), max(1, (len(words) + 1) // 2))


def _clean_product_link(base_url: str, href: str, asin: str = "") -> str:
    parts = urlsplit(base_url)
    if asin and ASIN.match(asin):
        return urlunsplit((parts.scheme, parts.netloc, f"/dp/{asin}", "", ""))
    absolute = urljoin(base_url, href)
    split = urlsplit(absolute)
    if split.scheme not in ("http", "https"):
        return ""
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))


def _marked_results(parts: PageParts, base_url: str) -> list[SearchResult]:
    found = []
    for entry in parts.results:
        title = next((text for text in entry["title"] if len(text) >= MIN_RESULT_TITLE_CHARS), "")
        link = _clean_product_link(base_url, next((href for href in entry["links"] if PRODUCT_PATH.search(href)), ""),
                                   entry["asin"])
        if title and link:
            price = first_price(" ".join(entry["prices"]))
            sponsored = entry["sponsored"] or bool(SPONSORED_WORDS.search(" ".join(entry["text"])[:300]))
            found.append(SearchResult(title=title, url=link, sponsored=sponsored,
                                      price_text=Offer("", price[0], price[1], "", "", "", "", "").price_text() if price else ""))
    return found


def _linked_results(parts: PageParts, base_url: str) -> list[SearchResult]:
    labels = _link_labels(parts)
    found, seen = [], set()
    for href, label in labels:
        link = _clean_product_link(base_url, href)
        if not link or link in seen or not PRODUCT_PATH.search(urlsplit(link).path) or not same_site(link, base_url):
            continue
        if len(label) < MIN_RESULT_TITLE_CHARS:
            continue
        seen.add(link)
        found.append(SearchResult(title=label, url=link, price_text="", sponsored=False))
    return found


def _link_labels(parts: PageParts) -> list[tuple[str, str]]:
    return [(href, label) for href, label in parts.links]


def search_results(html: str, base_url: str, query: str = "", limit: int = 5) -> list[SearchResult]:
    parts = parse(html, base_url)
    found = _marked_results(parts, base_url) or _linked_results(parts, base_url)
    words = query_words(query)
    relevant = [result for result in found if matches_query(result.title, words)]
    relevant.sort(key=lambda result: result.sponsored)
    return relevant[:limit]


def page_links(parts: PageParts, base_url: str, limit: int) -> list[tuple[str, str]]:
    found, seen = [], set()
    for href, label in _link_labels(parts):
        absolute = urljoin(base_url, href)
        split = urlsplit(absolute)
        if split.scheme not in ("http", "https") or not label:
            continue
        absolute = urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append((label[:80], absolute))
        if len(found) >= limit:
            break
    return found
