import ipaddress
import socket

from process import connection

resolve = socket.getaddrinfo


def _is_home_address(address):
    address = getattr(address, "ipv4_mapped", None) or address
    return (address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified
            or address.is_multicast or address.is_reserved or address in connection.PRIVATE_VPN_RANGE)


def _literal_address(host):
    try:
        return ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        return None


def _number_address(host):
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except (OSError, UnicodeError, ValueError):
        return None


def is_number_in_disguise(host):
    name = str(host or "").strip().lower().rstrip(".")
    return bool(name) and _literal_address(name) is None and _number_address(name) is not None


def _resolved_addresses(name):
    try:
        found = resolve(name, None)
    except (OSError, UnicodeError, ValueError):
        return []
    return [address for address in (_literal_address(str(entry[4][0])) for entry in found) if address is not None]


def _is_home_name(name):
    return name == "localhost" or name.endswith(".localhost") or "." not in name or name.endswith(connection.HOME_NAME_ENDINGS)


def is_home_network_host(host):
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        return False
    address = _literal_address(name) or _number_address(name)
    if address is not None:
        return _is_home_address(address)
    return _is_home_name(name) or any(_is_home_address(found) for found in _resolved_addresses(name))
