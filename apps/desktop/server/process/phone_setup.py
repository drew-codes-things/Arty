import json
from dataclasses import fields

import segno

from process import config, connection

FORMAT_VERSION = 1
SHOWN_FOR_SECONDS = 120
QR_ERROR_CORRECTION = "m"
QR_SCALE = 8
QR_BORDER = 4
QR_DARK = "#000000"
QR_LIGHT = "#ffffff"


class NothingToShare(ValueError):
    pass


def saved_connection() -> connection.Connection:
    values = {field.name: str(config.setting(connection.SECTION, field.name, "") or "") for field in fields(connection.Connection)}
    return connection.Connection(**values)


def phone_address(saved: connection.Connection) -> str:
    address = (saved.remote_url or saved.base_url).strip().rstrip("/")
    return address.removesuffix(connection.API_PATH)


def setup_text(saved: connection.Connection) -> str:
    code = {"arty": FORMAT_VERSION, "url": phone_address(saved), "key": saved.api_key.strip()}
    if saved.access_client_id and saved.access_client_secret:
        code |= {"id": saved.access_client_id.strip(), "secret": saved.access_client_secret.strip()}
    return json.dumps(code, separators=(",", ":"))


def setup_qr(text: str):
    return segno.make(text, error=QR_ERROR_CORRECTION, micro=False)


def setup_image(text: str) -> str:
    return setup_qr(text).svg_data_uri(scale=QR_SCALE, border=QR_BORDER, dark=QR_DARK, light=QR_LIGHT)


def phone_code() -> dict:
    saved = saved_connection()
    if not saved.api_key.strip():
        raise NothingToShare("Connect this computer to Arty first: the phone setup code is made from its connection.")
    return {"image": setup_image(setup_text(saved)), "seconds": SHOWN_FOR_SECONDS, "address": phone_address(saved)}
