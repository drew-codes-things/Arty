import io
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

import engine_guard
import models

from settings import CONFIG_FILE

log = logging.getLogger("brain")

MODEL_SETTING = "model"
ARTY_MODELS_SETTING = "arty_models"
LABELS_SETTING = "model_labels"
GENERATION_PATHS = ("/api/chat", "/api/generate")
LATEST_TAG = ":latest"
SERVER_FAULT_STATUS = 500
STREAM_ERROR_START = "model error:"
STREAM_ERROR_STATUS = re.compile(r"engine answered (\d{3})")
ENGINE_BUSY = re.compile(r"every loaded model is busy|is still answering another request")
BUSY_WAIT_SECONDS = 600
BUSY_PAUSE_SECONDS = 5
WINDOW_OVERFLOW = re.compile(r"context (size|length|window)|exceeds the available context|longer than the context"
                             r"|exceed_context_size",
                             re.IGNORECASE)
NOT_ANSWERED = "The main model couldn't answer just now ({reason}). Try again in a moment{pick}."
PICK_ANOTHER = ", or pick {names} in the model list if you want a different model"
REASON_CHARS = 160
UNREACHABLE_REASON = "the AI engine could not be reached"
NO_DETAILS = "no details given"


@dataclass(frozen=True)
class MainModel:
    name: str
    others: tuple = ()
    labels: dict = field(default_factory=dict)


def main_from(config) -> MainModel | None:
    if not isinstance(config, dict) or not isinstance(config.get(MODEL_SETTING), str):
        return None
    main = config[MODEL_SETTING].strip()
    if not main:
        return None
    listed = config.get(ARTY_MODELS_SETTING)
    names = [name.strip() for name in listed if isinstance(name, str) and name.strip()] if isinstance(listed, list) else []
    others = tuple(dict.fromkeys(name for name in names if not same_model(name, main)))
    labels = config.get(LABELS_SETTING)
    named = {key: value for key, value in labels.items() if isinstance(value, str) and value.strip()} \
        if isinstance(labels, dict) else {}
    return MainModel(main, others, named)


def read_main(path: str = CONFIG_FILE) -> MainModel | None:
    try:
        with open(path) as handle:
            return main_from(json.load(handle))
    except (OSError, ValueError):
        return None


def label(main: MainModel, name: str) -> str:
    return main.labels.get(name, name)


def plain_reason(text: str) -> str:
    words = " ".join("".join(character if character.isprintable() else " " for character in text).split())
    return words[:REASON_CHARS].rstrip() or NO_DETAILS


def reason_of(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        said = said_by(error)
        try:
            found = json.loads(said)
        except ValueError:
            found = None
        told = found.get("error") if isinstance(found, dict) and isinstance(found.get("error"), str) else said
        return plain_reason(f"the engine answered {error.code}: {told}")
    if isinstance(error, (urllib.error.URLError, ConnectionError)):
        return UNREACHABLE_REASON
    return plain_reason(str(error).removeprefix(STREAM_ERROR_START))


def not_answered_text(main: MainModel, reason: str) -> str:
    names = " or ".join(label(main, name) for name in main.others)
    return NOT_ANSWERED.format(reason=reason, pick=PICK_ANOTHER.format(names=names) if names else "")


class MainModelDown(OSError):
    def __init__(self, main: MainModel, reason: str, server_fault: bool = False):
        super().__init__(not_answered_text(main, reason))
        self.model = main.name
        self.reason = reason
        self.server_fault = server_fault

    def __str__(self) -> str:
        return self.args[0]


def same_model(name: str, wanted: str) -> bool:
    return name in (wanted, f"{wanted}{LATEST_TAG}") or wanted == f"{name}{LATEST_TAG}"


def names_the_main(main: MainModel, name) -> bool:
    return isinstance(name, str) and same_model(name, main.name)


def is_generation_call(url: str) -> bool:
    return urllib.parse.urlsplit(str(url)).path.endswith(GENERATION_PATHS)


def main_for(url: str, body) -> MainModel | None:
    if not isinstance(body, dict) or not is_generation_call(url):
        return None
    main = read_main()
    return main if main is not None and names_the_main(main, body.get("model")) else None


_local = threading.local()


@contextmanager
def writing_the_reply():
    earlier = getattr(_local, "replying", False)
    _local.replying = True
    try:
        yield
    finally:
        _local.replying = earlier


@contextmanager
def sharing_the_card():
    earlier = getattr(_local, "sharing", False)
    _local.sharing = True
    try:
        yield
    finally:
        _local.sharing = earlier


def may_use_the_card_mate() -> bool:
    return (getattr(_local, "sharing", False) and not getattr(_local, "replying", False)
            and engine_guard.current() is None)


@contextmanager
def only_the_main():
    earlier = getattr(_local, "pinned", False)
    _local.pinned = True
    try:
        yield
    finally:
        _local.pinned = earlier


def is_pinned_to_the_main() -> bool:
    return getattr(_local, "pinned", False)


def is_a_reply() -> bool:
    return getattr(_local, "replying", False)


_card_mates: list[Callable[[], set[str]]] = []


def share_the_card(listed: Callable[[], set[str]]) -> None:
    if listed not in _card_mates:
        _card_mates.append(listed)


def card_mates() -> list[str]:
    names = []
    for listed in _card_mates:
        try:
            names += sorted(listed())
        except (OSError, ValueError) as error:
            log.warning("couldn't read the models that share the card: %s", error)
    return names


def loaded_card_mate(main: MainModel) -> str:
    mates = card_mates()
    if not mates or models.is_loaded(main.name):
        return ""
    return next((name for name in mates if models.is_loaded(name)), "")


def read_safely(error: urllib.error.HTTPError) -> bytes:
    kept = getattr(error, "kept_body", None)
    if isinstance(kept, bytes):
        return kept
    try:
        body = error.read() or b""
    except (OSError, ValueError, AttributeError):
        return b""
    error.kept_body = body
    return body


def readable_copy(error: BaseException) -> BaseException:
    if not isinstance(error, urllib.error.HTTPError):
        return error
    return urllib.error.HTTPError(error.url, error.code, error.msg, error.hdrs, io.BytesIO(read_safely(error)))


def is_slow(error: BaseException) -> bool:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return isinstance(reason, TimeoutError)


def is_busy_text(text: str) -> bool:
    return bool(ENGINE_BUSY.search(text))


def said_by(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return read_safely(error).decode(errors="replace")
    return str(error)


def is_busy(error: BaseException, said: str) -> bool:
    is_engine_answer = isinstance(error, urllib.error.HTTPError) or (
        isinstance(error, RuntimeError) and said.startswith(STREAM_ERROR_START))
    return is_engine_answer and is_busy_text(said)


def model_error_status(text: str) -> int:
    found = STREAM_ERROR_STATUS.search(text)
    return int(found.group(1)) if found else SERVER_FAULT_STATUS


def failure_of(error: BaseException) -> tuple[bool, BaseException]:
    if isinstance(error, urllib.error.HTTPError):
        said = said_by(error)
        is_a_fault = error.code >= SERVER_FAULT_STATUS and not WINDOW_OVERFLOW.search(said) and not is_busy_text(said)
        return is_a_fault, readable_copy(error)
    if is_slow(error):
        return False, error
    if isinstance(error, (urllib.error.URLError, ConnectionError)):
        return True, error
    text = str(error)
    is_a_model_error = isinstance(error, RuntimeError) and text.startswith(STREAM_ERROR_START)
    is_a_fault = model_error_status(text) >= SERVER_FAULT_STATUS and not is_busy_text(text)
    return is_a_model_error and is_a_fault and not WINDOW_OVERFLOW.search(text), error


def is_a_reset(error: BaseException) -> bool:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return isinstance(reason, ConnectionResetError)


def sent_with_one_retry(body: dict, send, may_send_again: Callable[[], bool]):
    try:
        return send(body)
    except Exception as error:
        if not is_a_reset(error) or not may_send_again():
            raise
        log.warning("the engine dropped a call to %s (%s); sending it once more", body.get("model"), error)
    return send(body)


pause = time.sleep


clock = time.monotonic


@contextmanager
def answering_while(client_is_here: Callable[[], bool]):
    earlier = getattr(_local, "client_is_here", None)
    _local.client_is_here = client_is_here
    try:
        yield
    finally:
        _local.client_is_here = earlier


def client_has_gone() -> bool:
    client_is_here = getattr(_local, "client_is_here", None)
    return client_is_here is not None and not client_is_here()


def sent_when_the_card_is_free(body: dict, send, may_send_again: Callable[[], bool]):
    started = clock()
    while True:
        try:
            return sent_with_one_retry(body, send, may_send_again)
        except Exception as error:
            said = said_by(error)
            out_of_time = clock() - started + BUSY_PAUSE_SECONDS > BUSY_WAIT_SECONDS
            if out_of_time or client_has_gone() or not is_busy(error, said) or not may_send_again():
                raise readable_copy(error) from None
            log.info("the engine is busy with another model (%s); asking %s again", said[:200], body.get("model"))
        pause(BUSY_PAUSE_SECONDS)


def is_a_server_fault(error: BaseException) -> bool:
    return isinstance(error, (urllib.error.HTTPError, RuntimeError))


def answered(main: MainModel, body: dict, send, nothing_heard: Callable[[], bool] = lambda: True):
    if is_pinned_to_the_main():
        return send(body)
    if may_use_the_card_mate() and (mate := loaded_card_mate(main)):
        return send({**body, "model": mate})
    try:
        return sent_when_the_card_is_free(body, send, nothing_heard)
    except Exception as error:
        failed, kept = failure_of(error)
        if failed and is_a_reply() and nothing_heard():
            reason = reason_of(kept)
            log.warning("%s could not answer a reply (%s); saying so rather than asking another model", main.name, reason)
            raise MainModelDown(main, reason, is_a_server_fault(error)) from None
        if kept is error:
            raise
        raise kept from None


def sent(url: str, body, send):
    main = main_for(url, body)
    if main is None:
        return send(body)
    return answered(main, body, send)


def streamed(url: str, body, on_text, send):
    main = main_for(url, body)
    if main is None:
        return send(body, on_text)
    heard = []

    def hearing(piece):
        heard.append(piece)
        on_text(piece)
    return answered(main, body, lambda chosen: send(chosen, hearing), nothing_heard=lambda: not heard)
