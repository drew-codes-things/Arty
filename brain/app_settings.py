import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

LOOPBACK_NAMES = ("localhost",)


def settings_file(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable, default))


def read_settings(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def is_local_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def is_safe_address(url: str) -> bool:
    parts = urlsplit(url or "")
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and is_local_host(parts.hostname or "")
