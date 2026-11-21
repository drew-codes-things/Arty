import devices
import phone_link
import remote_sessions
import store

from api_support import ApiError, same_secret
from environment import BRAIN_KEY, APP_KEY, ROOM_KEY, HUB_KEY

SHARED_ROOM_KEY = "ROOM_API_KEY must not be the same as BRAIN_API_KEY, ARTY_API_KEY or MCPO_API_KEY"


def issue_device_key(request: devices.Request) -> str:
    key = devices.new_device_key()
    store.add_device(request.name, request.address, devices.key_hash(key))
    return key


def make_device_key(name: str, address: str) -> dict:
    if len(store.list_devices()) >= devices.MAX_ACTIVE_DEVICES:
        raise ApiError(429, "too many device keys are live; turn some off in Approved devices first")
    named = devices.cleaned_name(name)
    key = devices.new_device_key()
    made = store.add_device(named, address, devices.key_hash(key))
    return {"id": made, "name": named, "api_key": key}


def add_phone(name: str, address: str, key: str) -> int:
    if len(store.list_devices()) >= devices.MAX_ACTIVE_DEVICES:
        raise phone_link.LinkRefused(429, "Arty has too many linked devices. Remove some in Arty Desktop first.")
    return store.add_device(name, address, devices.key_hash(key), phone_link.PHONE_KIND)


def is_main_key(supplied: str) -> bool:
    return any(same_secret(supplied, f"Bearer {key}") for key in (BRAIN_KEY, APP_KEY) if key)


def room_key_is_shared(room_key: str = ROOM_KEY) -> bool:
    return bool(room_key) and any(same_secret(room_key, key) for key in (BRAIN_KEY, APP_KEY, HUB_KEY) if key)


def refuse_a_shared_room_key(room_key: str = ROOM_KEY) -> None:
    if room_key_is_shared(room_key):
        raise SystemExit(SHARED_ROOM_KEY)


def is_room_key(supplied: str) -> bool:
    return bool(ROOM_KEY) and not room_key_is_shared(ROOM_KEY) and same_secret(supplied, f"Bearer {ROOM_KEY}")


def is_device_key(supplied: str) -> bool:
    token = supplied.removeprefix("Bearer ")
    if not token.startswith(devices.DEVICE_KEY_PREFIX):
        return False
    return store.active_device(devices.key_hash(token)) is not None


def remote_caller(supplied: str) -> remote_sessions.Caller:
    if is_main_key(supplied):
        return remote_sessions.Caller(remote_sessions.MAIN_KEY_ID, "Arty's own key", True)
    token = supplied.removeprefix("Bearer ")
    device = store.active_device(devices.key_hash(token)) if token.startswith(devices.DEVICE_KEY_PREFIX) else None
    if device is None:
        raise ApiError(401, "invalid API key")
    return remote_sessions.Caller(f"device:{device['id']}", device["name"], bool(device["remote_control"]))
