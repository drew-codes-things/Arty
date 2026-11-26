import functools
import importlib
import urllib.error
from dataclasses import dataclass

from api_support import ApiError

ADDON = "local_addon"
ROUTER_ATTRIBUTE = "model_router"
MAIN, FALLBACK = "main", "fallback"
SWITCH_ERRORS = (ApiError, OSError, ValueError, urllib.error.URLError, TimeoutError)
NO_ROUTER_REASON = "the room uses the main model"


class NoSafeModel(Exception):
    pass


@dataclass(frozen=True)
class RoomPick:
    model: str
    role: str
    reason: str


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


@functools.cache
def router():
    try:
        addon = importlib.import_module(ADDON)
    except ModuleNotFoundError as error:
        if error.name != ADDON:
            raise
        return None
    return getattr(addon, ROUTER_ATTRIBUTE, None)


def is_on(config: dict) -> bool:
    return bool(router()) and router().is_on(config)


def choose(config: dict, kind: str, skill: str, needed_tokens: int, seconds_left: float, reply_tokens: int) -> RoomPick:
    if found := router():
        return found.choose(config, kind, skill, needed_tokens, seconds_left, reply_tokens)
    return RoomPick(config["model"], MAIN, NO_ROUTER_REASON)


def safe_main(config: dict) -> str:
    found = router()
    return found.safe_main(config) if found else config["model"]


def use(config: dict, pick: RoomPick) -> bool:
    found = router()
    return found.use(config, pick) if found else False


def give_back(main_config: dict) -> None:
    if found := router():
        found.give_back(main_config)


def free_for_outside_work(main_config: dict) -> None:
    if found := router():
        found.free_for_outside_work(main_config)


def applied(config: dict) -> dict:
    found = router()
    return found.applied(config) if found else config


def with_saved_seconds(config: dict) -> dict:
    found = router()
    return found.with_saved_seconds(config) if found else config
