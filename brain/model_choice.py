import logging
import threading
import time
import urllib.error

import coding
import connections
import models

from api_support import ApiError
from settings import OLLAMA_URL

log = logging.getLogger("brain")


MODEL_ID = "Arty"


def reload_main_model(config: dict):
    def load():
        models.make_room(config["model"], models.vram_budget(config))
        try:
            connections.http_json(f"{OLLAMA_URL}/api/generate", body=models.for_runner(config, {"model": config["model"], "keep_alive": config.get("keep_alive", "10m")}),
                      timeout=300)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log.warning("could not bring %s back after another model ran: %s", config["model"], error)
    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    return thread


OLLAMA_DOWN = "the AI server's model runner isn't answering; try again in a minute"
OTHER_MODEL_KEEP_ALIVE = "3m"
MAIN_RELOAD_DELAY_SECONDS = 200
AGENT_KEEP_ALIVE = "15m"
AGENT_QUIET_SECONDS = 600
VISION_KEEP_ALIVE = "1m"
VISION_MAIN_RETURN_SECONDS = 30


def found_model(name: str) -> dict | None:
    try:
        return models.find(name)
    except (urllib.error.URLError, TimeoutError) as error:
        log.warning("could not list Ollama models: %s", error)
        raise ApiError(503, OLLAMA_DOWN) from error


def chosen_model(main: str, requested: str, any_installed: bool) -> str:
    if not requested.startswith(models.DROPDOWN_PREFIX):
        return main
    name = requested[len(models.DROPDOWN_PREFIX):]
    chosen = found_model(name)
    if models.usable(chosen):
        return chosen["name"]
    if not any_installed:
        return main
    if chosen is None:
        raise ApiError(400, f"{name} isn't downloaded on the AI server; pick one from /v1/models?all=1")
    if chosen["embedding"]:
        raise ApiError(400, f"{name} is an embedding model and can't chat")
    if models.kept_solo(chosen):
        raise ApiError(400, f"{name} is kept for plain chats (solo_models in brain.json), so it can't use Arty's tools")
    return chosen["name"]


class MainModelReturn:
    def __init__(self):
        self.lock = threading.Lock()
        self.timer = None
        self.running = 0
        self.due = 0.0
        self.kept_model = ""

    def hold(self, model: str):
        with self.lock:
            self.running += 1
            self.kept_model = model
            self._stop_timer()

    def release(self, main_config: dict, quiet_seconds: float):
        with self.lock:
            self.running = max(0, self.running - 1)
            self._return_after(main_config, quiet_seconds)

    def schedule(self, main_config: dict, delay: float = MAIN_RELOAD_DELAY_SECONDS):
        with self.lock:
            self._return_after(main_config, delay)

    def holds_a_session(self) -> bool:
        with self.lock:
            return self.running > 0

    def keeps(self, model: str) -> bool:
        with self.lock:
            has_session = self.running > 0 or self.timer is not None
            return has_session and bool(model) and model == self.kept_model

    def _stop_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _return_after(self, main_config: dict, delay: float):
        self.due = max(self.due, time.monotonic() + delay)
        if self.running:
            return
        self._stop_timer()
        timer = threading.Timer(max(0.0, self.due - time.monotonic()), self._bring_main_back, args=(main_config,))
        timer.daemon = True
        self.timer = timer
        timer.start()

    def _bring_main_back(self, main_config: dict):
        with self.lock:
            if self.running or self.timer is not threading.current_thread():
                return
            self.timer, self.due, self.kept_model = None, 0.0, ""
        reload_main_model(main_config)


main_model_return = MainModelReturn()


def schedule_main_reload(main_config: dict):
    main_model_return.schedule(main_config)


def session_keep_alive(model: str, otherwise):
    return AGENT_KEEP_ALIVE if main_model_return.keeps(model) else otherwise


def use_requested_model(config: dict, requested: str, any_installed: bool, session: bool = False) -> bool:
    main_config = dict(config)
    config["model"] = chosen_model(config["model"], requested, any_installed)
    if config["model"] == main_config["model"]:
        return False
    config["keep_alive"] = AGENT_KEEP_ALIVE if session else session_keep_alive(config["model"], OTHER_MODEL_KEEP_ALIVE)
    models.make_room(config["model"], models.vram_budget(config))
    if session:
        main_model_return.hold(config["model"])
    else:
        schedule_main_reload(main_config)
    return True


def is_downloaded(name: str) -> bool | None:
    try:
        return models.find(name) is not None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log.warning("couldn't check whether %s is downloaded: %s", name, error)
        return None


def coding_model_or(config: dict, fallback: str) -> str:
    coding_name = config.get("coding_model", "")
    if not coding_name:
        return fallback
    if not is_downloaded(coding_name):
        log.warning("the coding model %s isn't available, using the main model", coding_name)
        return fallback
    return f"{models.DROPDOWN_PREFIX}{coding_name}"


def agent_model(config: dict, requested: str) -> str:
    if requested.startswith(models.DROPDOWN_PREFIX):
        named = requested[len(models.DROPDOWN_PREFIX):]
        if is_downloaded(named) is not False:
            return requested
        log.warning("an agent asked for %s, which isn't downloaded; choosing the model for it instead", named)
        requested = MODEL_ID
    return coding_model_or(config, requested)


RETIRED_MODEL_ID = "assistant-core"
MAIN_ENTRY_IDS = {MODEL_ID, RETIRED_MODEL_ID, ""}


def arty_description(config: dict, main: dict | None) -> str:
    if main is None:
        return ""
    coding_name, vision_name = config.get("coding_model", ""), config.get("vision_model", "")
    if not coding_name and not vision_name:
        return models.summary(main)
    if coding_name == main["name"]:
        coding_name = ""
        jobs = [f"{main['name']} for chat and code"]
    else:
        jobs = [f"{main['name']} for chat"]
    if coding_name and coding_name == vision_name:
        jobs.append(f"{coding_name} for code and pictures")
    else:
        jobs += [f"{name} for {job}" for name, job in ((coding_name, "code"), (vision_name, "pictures")) if name]
    return "Picks the model for each request: " + ", ".join(jobs)


def routes_picture_chats(config: dict) -> bool:
    return bool(config.get("vision_model")) and bool(config.get("route_picture_chats", True))


def picture_model_or(config: dict, fallback: str) -> str:
    name = config.get("vision_model", "")
    try:
        looker = models.find(name)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log.warning("could not check the vision model %s: %s", name, error)
        return fallback
    if looker is None:
        log.warning("the vision model %s isn't available, using the main model for pictures", name)
        return fallback
    if not (looker.get("vision") and looker.get("tools")):
        return fallback
    return f"{models.DROPDOWN_PREFIX}{looker['name']}"


def warm_picture_model(config: dict):
    if not routes_picture_chats(config) or main_model_return.holds_a_session():
        return None
    looker = picture_model_or(config, "").removeprefix(models.DROPDOWN_PREFIX)
    if not looker or models.is_loaded(looker):
        return None

    def load():
        models.make_room(looker, models.vram_budget(config))
        try:
            connections.http_json(f"{OLLAMA_URL}/api/generate", body=models.for_runner(config, {"model": looker, "keep_alive": OTHER_MODEL_KEEP_ALIVE}),
                                  timeout=300)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log.warning("could not load %s ahead of a picture question: %s", looker, error)
        main_model_return.schedule(config)
    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    return thread


def chat_model(config: dict, requested: str, said: str, new_picture: bool = False) -> str:
    if requested in MAIN_ENTRY_IDS and new_picture and routes_picture_chats(config):
        return picture_model_or(config, requested)
    routes_coding_chats = bool(config.get("coding_model")) and bool(config.get("route_coding_chats", True))
    if requested not in MAIN_ENTRY_IDS or not routes_coding_chats or not coding.is_request(said):
        return requested
    if not coding.is_big_job(said) and not models.is_loaded(config["coding_model"]):
        return requested
    return coding_model_or(config, requested)
