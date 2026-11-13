import devices
import remote_sessions
import store

from api_support import ApiError, same_secret
from environment import BRAIN_KEY, APP_KEY


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


def is_main_key(supplied: str) -> bool:
    return any(same_secret(supplied, f"Bearer {key}") for key in (BRAIN_KEY, APP_KEY) if key)


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
