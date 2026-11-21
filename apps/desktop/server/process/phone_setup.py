import json
from dataclasses import fields
from urllib.parse import quote

import requests
import segno

from process import config, connection, route

FORMAT_VERSION = 2
QR_ERROR_CORRECTION = "m"
QR_SCALE = 8
QR_BORDER = 4
QR_DARK = "#000000"
QR_LIGHT = "#ffffff"
BRAIN_TIMEOUT_SECONDS = 15
OFFERS_PATH = "/phone-link/offers"
DEVICES_PATH = "/devices"
PHONE_KIND = "phone"
FIRST_ERROR_STATUS = 400
SHOWN_PHONE_FIELDS = ("id", "name", "created", "last_used")


class NothingToShare(ValueError):
    pass


class BrainRefused(ValueError):
    pass


def saved_connection() -> connection.Connection:
    values = {field.name: str(config.setting(connection.SECTION, field.name, "") or "") for field in fields(connection.Connection)}
    return connection.Connection(**values)


def phone_address(saved: connection.Connection) -> str:
    address = (saved.remote_url or saved.base_url).strip().rstrip("/")
    return address.removesuffix(connection.API_PATH)


def access_pair(saved: connection.Connection) -> dict:
    client_id, secret = saved.access_client_id.strip(), saved.access_client_secret.strip()
    if not saved.remote_url.strip() or not client_id or not secret:
        return {}
    return {"id": client_id, "secret": secret}


def home_address(saved: connection.Connection) -> str:
    return saved.base_url.strip().rstrip("/").removesuffix(connection.API_PATH)


def setup_text(saved: connection.Connection, offer: dict) -> str:
    code = {"arty": FORMAT_VERSION, "url": phone_address(saved), "offer": offer["offer"], "code": offer["code"]}
    if home_address(saved) != code["url"]:
        code["home"] = home_address(saved)
    return json.dumps(code, separators=(",", ":"))


def setup_qr(text: str):
    return segno.make(text, error=QR_ERROR_CORRECTION, micro=False)


def setup_image(text: str) -> str:
    return setup_qr(text).svg_data_uri(scale=QR_SCALE, border=QR_BORDER, dark=QR_DARK, light=QR_LIGHT)


def refusal_of(response) -> str:
    try:
        message = response.json().get("error", {}).get("message")
    except (ValueError, AttributeError):
        message = None
    return message or f"Arty's brain answered {response.status_code}."


def brain_call(method: str, path: str, body=None, send=None) -> dict:
    send = send or requests.request
    headers = {"Authorization": f"Bearer {route.API_KEY}", **route.access_headers()}
    try:
        response = send(method, f"{route.base()}{path}", json=body, headers=headers, timeout=BRAIN_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise BrainRefused(f"Couldn't reach Arty's brain: {error}") from None
    if response.status_code >= FIRST_ERROR_STATUS:
        raise BrainRefused(refusal_of(response))
    try:
        return response.json()
    except ValueError:
        raise BrainRefused("Arty's brain sent an answer this app can't read.") from None


def phone_code() -> dict:
    saved = saved_connection()
    if not saved.api_key.strip():
        raise NothingToShare("Connect this computer to Arty first: the phone link code is made through its connection.")
    access = access_pair(saved)
    made = brain_call("POST", OFFERS_PATH, {"access": access} if access else {})
    return {"image": setup_image(setup_text(saved, made)), "seconds": made["expires_in"], "address": phone_address(saved),
            "offer": made["offer"]}


def offer_status(offer_id: str) -> dict:
    return brain_call("GET", f"{OFFERS_PATH}/{quote(offer_id, safe='')}")


def linked_phones() -> list[dict]:
    listed = brain_call("GET", DEVICES_PATH).get("devices") or []
    return [{name: device.get(name) for name in SHOWN_PHONE_FIELDS} for device in listed if device.get("kind") == PHONE_KIND]


def remove_phone(device_id: int) -> None:
    if device_id not in {phone["id"] for phone in linked_phones()}:
        raise BrainRefused("That phone isn't linked any more.")
    brain_call("DELETE", f"{DEVICES_PATH}/{device_id}")
