import ipaddress
from dataclasses import astuple, dataclass, fields, replace
from urllib.parse import urlparse

import requests

from process import config, identity

SECTION = "brain"
API_PATH = "/v1"
CHECK_TIMEOUT_SECONDS = 8
LOCAL_SCHEMES = ("http", "https")
REMOTE_SCHEMES = ("https",)
REFUSED_STATUSES = (401, 403)
OK_STATUS = 200
EXAMPLE_ADDRESS = "http://arty-server:8081/v1"
EXAMPLE_REMOTE_ADDRESS = "https://arty.example.com/v1"
HOME_NAME_ENDINGS = (".local", ".lan", ".home.arpa", ".home", ".internal", ".ts.net")
PRIVATE_VPN_RANGE = ipaddress.ip_network("100.64.0.0/10")


class ConnectionProblem(ValueError):
    pass


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_key: str
    remote_url: str = ""
    access_client_id: str = ""
    access_client_secret: str = ""


def is_configured() -> bool:
    return bool(str(config.setting(SECTION, "api_key", "") or "").strip())


def addresses() -> dict:
    return {name: str(config.setting(SECTION, name, "") or "") for name in ("base_url", "remote_url")}


def _api_address(text: str, schemes: tuple[str, ...], example: str) -> str:
    address = text.strip().rstrip("/")
    parsed = urlparse(address)
    if parsed.scheme not in schemes or not parsed.hostname:
        allowed = " or ".join(f"{scheme}://" for scheme in schemes)
        raise ConnectionProblem(f"The address must start with {allowed}, like {example}")
    return address if parsed.path not in ("", "/") else address + API_PATH


def _is_home_network(host: str) -> bool:
    name = host.lower()
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return name == "localhost" or "." not in name or name.endswith(HOME_NAME_ENDINGS)
    return address.is_private or address.is_loopback or address.is_link_local or address in PRIVATE_VPN_RANGE


def _require_home_for_http(address: str) -> None:
    parsed = urlparse(address)
    if parsed.scheme == "http" and not _is_home_network(parsed.hostname or ""):
        raise ConnectionProblem("Use https:// for addresses outside your home network, so the API key isn't sent unencrypted.")


def _has_line_break(connection: Connection) -> bool:
    return any("\n" in value or "\r" in value for value in astuple(connection))


def cleaned(connection: Connection) -> Connection:
    if _has_line_break(connection):
        raise ConnectionProblem("Each setting has to be on one line.")
    tidy = Connection(*(value.strip() for value in astuple(connection)))
    if not tidy.api_key:
        raise ConnectionProblem(
            "Paste in your brain's API key: provision.sh saves it to /srv/ai/secrets/arty-api.txt"
            " on the machine running the brain."
        )
    remote_url = _api_address(tidy.remote_url, REMOTE_SCHEMES, EXAMPLE_REMOTE_ADDRESS) if tidy.remote_url else ""
    if bool(tidy.access_client_id) != bool(tidy.access_client_secret):
        raise ConnectionProblem("A Cloudflare Access token needs both its client ID and its client secret.")
    base_url = _api_address(tidy.base_url, LOCAL_SCHEMES, EXAMPLE_ADDRESS)
    _require_home_for_http(base_url)
    return replace(tidy, base_url=base_url, remote_url=remote_url)


def _attempts(connection: Connection) -> list[tuple[str, dict]]:
    key = {"Authorization": f"Bearer {connection.api_key}"}
    attempts = [(connection.base_url, key)]
    if connection.remote_url:
        access = {"CF-Access-Client-Id": connection.access_client_id,
                  "CF-Access-Client-Secret": connection.access_client_secret} if connection.access_client_id else {}
        attempts.append((connection.remote_url, {**key, **access}))
    return attempts


def _lists_models(response) -> bool:
    try:
        return isinstance(response.json().get("data"), list)
    except (ValueError, AttributeError):
        return False


def _proves_it_knows(address: str, key: str, get) -> bool:
    nonce = identity.new_nonce()
    response = get(f"{address}/identity?nonce={nonce}", headers={}, timeout=CHECK_TIMEOUT_SECONDS)
    try:
        proofs = response.json().get("proofs") if response.status_code == OK_STATUS else None
    except (ValueError, AttributeError):
        return False
    return identity.is_proven(proofs, key, nonce)


def check(connection: Connection, get=None) -> str:
    get = get or requests.get
    problem = ""
    for address, headers in _attempts(connection):
        try:
            if urlparse(address).scheme == "http" and not _proves_it_knows(address, connection.api_key, get):
                problem = f"{address} didn't prove it's your Arty, so the API key wasn't sent to it."
                continue
            response = get(f"{address}/models", headers=headers, timeout=CHECK_TIMEOUT_SECONDS)
        except requests.RequestException:
            problem = f"Can't reach {address}. Check the address and that the Arty server is on."
            continue
        if response.status_code in REFUSED_STATUSES:
            raise ConnectionProblem(f"Arty at {address} refused the API key (or the Cloudflare Access token).")
        if response.status_code == OK_STATUS and _lists_models(response):
            return address
        problem = f"{address} answered, but it isn't Arty's brain (status {response.status_code})."
    raise ConnectionProblem(problem)


def save(connection: Connection) -> None:
    for field in fields(Connection):
        config.set_value(SECTION, field.name, getattr(connection, field.name))
