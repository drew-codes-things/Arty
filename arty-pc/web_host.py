import re
import unicodedata
import urllib.parse

UNCLEAR_CHARACTERS = re.compile(r"[\\\x00-\x1f\x7f]")
PLAIN_HOST_CHARACTERS = re.compile(r"[a-z0-9._:-]+")
DOT_LOOKALIKES = str.maketrans({"。": ".", "．": ".", "｡": "."})


WEB_SCHEME = re.compile(r"https?:", re.I)
PLAIN_WEB_START = re.compile(r"https?://[^/]", re.I)


class UnclearAddress(ValueError):
    pass


def canonical_host(host) -> str:
    text = unicodedata.normalize("NFKC", urllib.parse.unquote(str(host or "")))
    text = text.translate(DOT_LOOKALIKES).strip().rstrip(".").lower()
    if not text:
        return ""
    try:
        plain = text.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnclearAddress(f"the address host {host!r} can't be read plainly, so it isn't used") from None
    if not PLAIN_HOST_CHARACTERS.fullmatch(plain):
        raise UnclearAddress(f"the address host {host!r} can't be read plainly, so it isn't used")
    return plain


def host_of(url) -> str:
    text = str(url or "").strip()
    if UNCLEAR_CHARACTERS.search(text):
        raise UnclearAddress("the address holds a backslash or a control character, so it isn't used")
    if WEB_SCHEME.match(text) and not PLAIN_WEB_START.match(text):
        raise UnclearAddress("the address doesn't start with http:// or https:// and a host, so it isn't used")
    try:
        parts = urllib.parse.urlsplit(text)
        host = parts.hostname or ""
    except ValueError:
        raise UnclearAddress("the address can't be read plainly, so it isn't used") from None
    if "@" in unicodedata.normalize("NFKC", parts.netloc):
        raise UnclearAddress("the address has a sign-in part before its host, so it isn't used")
    return canonical_host(host)


def is_at_or_under(host: str, hosts) -> bool:
    return bool(host) and any(host == own or host.endswith("." + own) for own in hosts)


def is_own_app_address(url, hosts) -> bool:
    return is_at_or_under(host_of(url), {canonical_host(own) for own in hosts})
