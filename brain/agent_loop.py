import hashlib
import json
import logging
import re
import time
import urllib.error
from dataclasses import dataclass, field, replace

import app_actions
import browser_apps
import code_check
import code_sandbox
import coding
import connections
import content_rules
import docs_reading
import example_checks
import fresh_answer
import hidden_notes
import main_calls
import models
import policy
import private_check
import private_text
import run_requests
import sharing
import shopping
import store
import style
import tool_guide
import web_pages
import window_fit
import written_calls

from agent_sessions import AgentSessions
from api_support import ApiError
from archive import message_text
from settings import ARTY_ENGINE_URL
from native_tools import NATIVE_TOOLS, BACKGROUND_ONLY_EXCLUDED
from reply_rules import (PROMPT_LEAK, FUMBLED_CALL, ACTION_REQUEST, CLAIMED, CLARIFYING, SEARCH_REQUEST, PROMISE,
    TOOL_TALK, DO_REQUEST, PRETEND_TOOLS, TOOL_FAILED, FAILED_NOTE, ANSWERED_ALREADY_CHARS, NOT_ACTIONS, without_app_notes,
    PRIVATE_CALL_NOT_RUN, SIBLING_CALL_NOT_RUN, HELD_CALL_RESULTS, ACTION_CLAIM, SCHEDULE_REQUEST, OFFERED_A_SCHEDULE,
    SCHEDULE_NOT_ASKED, is_client_refusal, has_a_deflection, is_only_a_deflection, without_deflection, is_plain_identity_question,
    is_needless_coursework_refusal, asks_to_disguise_harm, is_coursework_request, asks_for_revision)
from steering import (DESKTOP_RUN_TOOLS, LOOKUP_TEMPERATURE, RUN_TOOLS, WRITE_ONLY_NOTE, asks_for_code, asks_for_the_computer,
    WRITE_AND_RUN_NOTE, READ_LINK_NOTE, asks_to_read_a_link, thinking_for, HOW_TO_NOTE, asks_how_to,
    steer_last_request, steer_away_from_repeats, with_thinking, with_context, parse_arguments, DESKTOP_TOOLS,
    NOTHING_TO_SIGN_IN, question_for_user, RUN_COMMAND_TOOLS, DESKTOP_RUN_COMMAND_TOOLS, RUN_THE_CODE_NOTE, DONT_RUN_NOTE,
    asks_to_install, WRITE_ONLY_UNCHECKED_NOTE, DONT_RUN_UNCHECKED_NOTE, SEARCH_RESULTS_NOTE, NOTHING_DONE_NOTE,
    PRETEND_TOOLS_NOTE, HALF_WRITTEN_CALL_NOTE, EMPTY_REPLY_NOTE, COURSEWORK_NOTE, UNASKED_PRIVACY_NOTE,
    HARMFUL_REWRITE_NOTE, STUDY_TOOLS, REMOTE_COMPUTER_NOTE)
from connections import TOOL_RESULT_CHARS
from prompting import system_prompt, with_memories_left_out, with_persona_left_out, with_tool_guide, current_context
from native_calls import run_native_safely
from honesty import client_tool_runs, did_not_work, tool_results, acted_for_sure

log = logging.getLogger("brain")


MAX_TOOL_ROUNDS = 12
PAGE_READER = "workspace__browser_read"


def tool_parameters(name: str) -> dict:
    """The parameters a tool takes (its JSON schema's properties), or {} when unknown."""
    definition = NATIVE_TOOLS.get(name) or next((t for t in connections.hub.tools if t["function"]["name"] == name), None)
    return ((definition or {}).get("function", {}).get("parameters") or {}).get("properties") or {}


def repair_arguments(name: str, arguments: dict) -> dict:
    """Small models sometimes nest a call's arguments under a made-up key ({"title: when": {"title": ...}}).
    Lifts the tool's real parameters out of any nested object; anything already right is left alone."""
    known = tool_parameters(name)
    if not known or all(key in known for key in arguments):
        return arguments
    # Stray punctuation in a key ({"url: ": ...}) is the same mistake in another form.
    arguments = {(cleaned if (cleaned := str(key).strip(" :\"'")) in known else key): value for key, value in arguments.items()}
    repaired = {key: value for key, value in arguments.items() if key in known}
    for value in arguments.values():
        if isinstance(value, dict):
            repaired.update({key: inner for key, inner in value.items() if key in known and key not in repaired})
    if repaired != arguments:
        log.info("repaired %s arguments: %s -> %s", name, json.dumps(arguments)[:200], json.dumps(repaired)[:200])
    return repaired or arguments


TOOL_UNLOCKS = {"start_screen_recording": sharing.RECORDING_UNLOCKS}
NOT_RUN_YET = "Not run: waiting for the user's OK."


def know_the_page(name: str, context: dict):
    if not policy.page_unknown(name, context) or PAGE_READER not in connections.hub.routes:
        return
    policy.remember_tool_result(context, PAGE_READER, connections.hub.call(PAGE_READER, {}))


def home_network_fetch(name: str, arguments: dict) -> str | None:
    url = str(arguments.get("url") or "")
    if name != docs_reading.FETCH_TOOL or not (problem := web_pages.address_problem(url)):
        return None
    return f"{REFUSED_RESULT} {url[:200]}: {problem}."


def private_upload(name: str, arguments: dict) -> str | None:
    path = str(arguments.get("path") or "")
    if name != "workspace__browser_upload" or not sharing.is_private(path):
        return None
    return f"{REFUSED_RESULT} {path} is private (keys, passwords or a browser profile), so it is never uploaded anywhere."


def hold_for_approval(reason, call, calls, background, state, requested, add, tool_log, raw):
    function = call.get("function", {})
    name = function.get("name") or ""
    arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
    state["groups"] = sorted(requested)
    if policy.pretends_to_ask(reason):
        log.warning("refused %s: its card holds the approval question's own words", name)
        return {"status": "done", "answer": policy.PRETEND_QUESTION_REFUSAL, "tools_used": tool_log, "raw": raw}
    if background:
        return {"status": "waiting", "waiting": {"kind": "approval", "message": reason, "call": {"name": name, "arguments": arguments}},
                "tools_used": tool_log, "raw": raw}
    for unanswered in calls[calls.index(call):]:
        add({"role": "tool", "content": NOT_RUN_YET, "tool_name": unanswered.get("function", {}).get("name") or ""})
    return {"status": "done", "answer": policy.approval_question(reason), "asks_approval": True, "tools_used": tool_log,
            "raw": raw}


def save_progress(task, state, requested):
    state["groups"] = sorted(requested)
    store.update_task(task["id"], history=state)


FRESH_LOAD_NANOSECONDS = 1_000_000_000
ROUND_TIMING_FIELDS = ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")
CACHED_PROMPT_FIELD = "prompt_eval_cached_count"
ROUNDS_KEY = "arty_rounds"


def round_timings(model: str, raw: dict, think: bool = False) -> dict:
    timings = {"model": model, **{field: int(raw.get(field) or 0) for field in ROUND_TIMING_FIELDS}}
    if CACHED_PROMPT_FIELD in raw:
        timings[CACHED_PROMPT_FIELD] = int(raw[CACHED_PROMPT_FIELD] or 0)
    timings["loaded_fresh"] = timings["load_duration"] >= FRESH_LOAD_NANOSECONDS
    timings["think"] = bool(think)
    return timings


def turn_rounds(raw: dict | None) -> list[dict]:
    return list((raw or {}).get(ROUNDS_KEY) or [])


ENGINE_FAULT_STATUS = 500
REPLY_SECONDS = 1800
MODEL_FAILED_ANSWER = "Sorry, the model failed while writing this answer. Please try again."
AFTER_TOOLS_FAILED_ANSWER = ("Sorry, the model failed while writing this answer{reason}. These steps had already run, "
                             "so I have not repeated them:\n{steps}")
RAN_RESULT_CHARS = 200
PLAIN_REASON = re.compile(r"[\w ,.:;'()/-]{1,120}")
REFUSED_RESULT = "Refused:"
ENGINE_FAILED_KEY = "engine_failed"
WINDOW_OVERFLOW = main_calls.WINDOW_OVERFLOW
OVERFLOW_STATUS = 413
OVERFLOW_TYPE = "context_length_exceeded"


REPLY_CAP_NOTE = "\n\n(I hit my reply length limit, so this was cut short. Ask me to continue.)"
WINDOW_END_NOTE = ("\n\n(This conversation filled my context window, so this reply was cut short. Send a shorter message or "
                   "start a new conversation so I have room to finish.)")
WINDOW_END_MARGIN_TOKENS = 16


def overflow_message(model: str, window: int, reply_cut: bool) -> str:
    if reply_cut:
        return (f"{model}'s context window of {window} tokens filled up before the reply finished, so it was cut short. "
                "Compact the conversation and send it again.")
    return (f"This conversation is longer than {model}'s context window of {window} tokens, so the model did not read it. "
            "Shorten or compact the conversation and send it again.")


class WindowOverflow(ApiError):
    def __init__(self, model: str, window: int, reply_cut: bool = False):
        super().__init__(OVERFLOW_STATUS, overflow_message(model, window, reply_cut))
        self.model = model
        self.window = window

    def details(self) -> dict:
        return {"message": str(self), "type": OVERFLOW_TYPE, "code": OVERFLOW_STATUS, "model": self.model,
                "context_tokens": self.window}


TOO_LONG_COUNTS = re.compile(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")
TOO_LONG_ANSWER = "That message is too long for me to read in one go ({sizes}). Try sending a shorter part."
OVERFLOW_AFTER_STEPS = "the step results did not fit in what I can read at once"


def too_long_sizes(window: int, prompt_tokens: int | None) -> str:
    if prompt_tokens is None:
        return f"I can take about {window:,} tokens at once"
    return f"with it this chat comes to about {prompt_tokens:,} tokens; I can take about {window:,} at once"


class MessageTooLong(Exception):
    def __init__(self, window: int, prompt_tokens: int | None = None):
        super().__init__(TOO_LONG_ANSWER.format(sizes=too_long_sizes(window, prompt_tokens)))
        self.window = window
        self.prompt_tokens = prompt_tokens


def message_too_long(config: dict, reason: str) -> MessageTooLong:
    counts = TOO_LONG_COUNTS.search(reason)
    if counts:
        return MessageTooLong(int(counts.group(2)), int(counts.group(1)))
    return MessageTooLong(models.context_tokens(config, config["model"]))


def engine_error_text(error: Exception) -> str:
    if not isinstance(error, urllib.error.HTTPError):
        return str(error)
    try:
        found = json.loads(error.read() or b"{}").get("error")
    except (ValueError, AttributeError, OSError):
        return str(error)
    return str(found or error)


def ran_into_window_end(config: dict, raw: dict) -> bool:
    prompt, written = raw.get("prompt_eval_count"), raw.get("eval_count")
    if not isinstance(prompt, int) or not isinstance(written, int):
        return False
    return prompt + written >= models.context_tokens(config, config["model"]) - WINDOW_END_MARGIN_TOKENS


def cut_short_note(config: dict, raw: dict, agent: bool) -> str:
    if not ran_into_window_end(config, raw):
        log.warning("reply hit the num_predict cap and was cut off")
        return REPLY_CAP_NOTE
    window = models.context_tokens(config, config["model"])
    if agent:
        log.warning("an agent reply ran into the end of %s's %d-token window", config["model"], window)
        raise WindowOverflow(config["model"], window, reply_cut=True)
    log.warning("a reply ran into the end of %s's %d-token window and was cut off", config["model"], window)
    return WINDOW_END_NOTE


def engine_reply(body: dict, draft, risky, on_thinking) -> dict:
    with main_calls.writing_the_reply():
        if draft:
            return connections.stream_chat(body, lambda piece: draft.feed(piece, risky), timeout=REPLY_SECONDS,
                                           on_thinking=on_thinking)
        return connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body=body, timeout=REPLY_SECONDS)


def acted(message: dict) -> bool:
    if message.get("role") != "tool" or message.get("tool_name") in NOT_ACTIONS:
        return False
    result = message_text(message).strip()
    return result not in (NOT_RUN_YET, HIDDEN_TOOL_RESULT) and not result.startswith(
        (written_calls.NOTHING_HAPPENED, REFUSED_RESULT, *HELD_CALL_RESULTS)) and not is_client_refusal(result)


def tools_that_ran(messages: list) -> list[tuple[str, str]]:
    last_request = max((index for index, message in enumerate(messages) if message.get("role") == "user"), default=-1)
    return [(message.get("tool_name") or "a tool", message_text(message)) for message in messages[last_request + 1:]
            if acted(message)]


def shown_reason(reason: str) -> str:
    line = first_line(reason)
    return f" ({line})" if PLAIN_REASON.fullmatch(line) else ""


def is_an_engine_fault(error: Exception) -> bool:
    return isinstance(error, urllib.error.HTTPError) and error.code >= ENGINE_FAULT_STATUS


def failed_model_reply() -> dict:
    return {"message": {"role": "assistant", "content": MODEL_FAILED_ANSWER, "tool_calls": []}, "done": True,
            "done_reason": "stop"}


def first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0][:RAN_RESULT_CHARS] if lines else "no result"


def failed_after_tools_reply(ran: list[tuple[str, str]], reason: str) -> dict:
    steps = "\n".join(f"- {name}: {first_line(result)}" for name, result in ran)
    answer = AFTER_TOOLS_FAILED_ANSWER.format(reason=shown_reason(reason), steps=steps)
    return {"message": {"role": "assistant", "content": answer, "tool_calls": []}, "done": True, "done_reason": "stop",
            ENGINE_FAILED_KEY: True}


def overflowed_reply(config: dict, reason: str, ran: list[tuple[str, str]]) -> dict:
    if ran:
        log.warning("the prompt outgrew %s's window after %d tool call(s) ran (%s); answering with the steps that ran",
                    config["model"], len(ran), reason)
        return failed_after_tools_reply(ran, OVERFLOW_AFTER_STEPS)
    log.warning("a message did not fit %s's window (%s); answering that it is too long", config["model"], reason)
    raise message_too_long(config, reason)


def without_oldest_turns(messages) -> list | None:
    if not isinstance(messages, list):
        return None
    lead = 0
    while lead < len(messages) and isinstance(messages[lead], dict) and messages[lead].get("role") == "system":
        lead += 1
    turns = messages[lead:]
    kept = turns[len(turns) // 2:]
    while kept and not (isinstance(kept[0], dict) and kept[0].get("role") == "user"):
        kept = kept[1:]
    if not kept or len(kept) >= len(turns):
        return None
    return messages[:lead] + kept


def trimmed_reply(body: dict, config: dict, draft, risky, on_thinking, reason: str) -> dict | None:
    shorter = without_oldest_turns(body.get("messages"))
    if shorter is None:
        return None
    log.warning("a chat did not fit %s's window (%s); sending it again without its oldest %d message(s)",
                config["model"], reason, len(body["messages"]) - len(shorter))
    try:
        return engine_reply({**body, "messages": shorter}, draft, risky, on_thinking)
    except (urllib.error.HTTPError, RuntimeError) as error:
        if not WINDOW_OVERFLOW.search(engine_error_text(error)):
            raise
        return None


def model_reply(body: dict, config: dict, draft, risky, on_thinking, agent: bool, ran: list[tuple[str, str]]) -> dict:
    try:
        return engine_reply(body, draft, risky, on_thinking)
    except main_calls.MainModelDown as down:
        if ran:
            log.warning("%s could not answer after %d tool call(s) ran (%s)", config["model"], len(ran), down.reason)
            return failed_after_tools_reply(ran, down.reason)
        if not down.server_fault:
            raise
        log.warning("%s could not answer (%s); asking it once more", config["model"], down.reason)
    except (urllib.error.HTTPError, RuntimeError) as error:
        reason = engine_error_text(error)
        overflowed = bool(WINDOW_OVERFLOW.search(reason))
        if overflowed and agent:
            log.warning("an agent conversation did not fit %s's window: %s", config["model"], reason)
            raise WindowOverflow(config["model"], models.context_tokens(config, config["model"])) from None
        if overflowed and not ran and (trimmed := trimmed_reply(body, config, draft, risky, on_thinking, reason)):
            return trimmed
        if overflowed:
            return overflowed_reply(config, reason, ran)
        if not is_an_engine_fault(error) or draft and draft.raw:
            raise
        if ran:
            log.warning("the engine failed a reply from %s (%s) after %d tool call(s) ran; not retrying",
                        config["model"], reason, len(ran))
            return failed_after_tools_reply(ran, reason)
        log.warning("the engine failed a reply from %s (%s); trying once more", config["model"], reason)
    try:
        return engine_reply(body, draft, risky, on_thinking)
    except main_calls.MainModelDown:
        raise
    except (urllib.error.HTTPError, RuntimeError, OSError) as error:
        log.warning("the engine failed the retry too (%s); answering that the model failed", engine_error_text(error))
        return failed_model_reply()


def written_reply(body: dict) -> str:
    try:
        shown = connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body={**body, "stream": False, "_debug_render_only": True})
        replay = {"model": body["model"], "prompt": shown["_debug_info"]["rendered_template"], "raw": True, "stream": False,
                  "options": body.get("options") or {}}
        replay.update({key: body[key] for key in ("keep_alive", "shift") if key in body})
        return connections.http_json(f"{ARTY_ENGINE_URL}/api/generate", body=replay).get("response") or ""
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError) as error:
        log.warning("couldn't replay an empty model reply to see what it wrote: %s", error)
        return ""


def fitted_to_window(config: dict, body: dict) -> dict:
    fitted, left_out = window_fit.fit_to_window(config, body)
    if left_out:
        log.info("left out %d earlier messages so the prompt and a %d-token reply fit %s's window", left_out,
                 window_fit.reply_room(body), config["model"])
    return fitted


SEARCH_ONLY = "everything_but_search"
SEARCH_ONLY_TOOLS = ("research__web_search",)


@dataclass(frozen=True)
class AgentRun:
    background: bool = False
    chat_approved: str | None = None
    task: dict | None = None
    client_system: str = ""
    client_tools: list | None = None
    skip_groups: tuple = ()
    draft: object = None
    looked_up: tuple | None = None
    agent: bool = False
    on_thinking: object = None
    client_texts: private_text.KnownText = private_text.NOTHING_KNOWN
    allowed_tools: frozenset | None = None
    room: bool = False
    deadline: float | None = None
    is_interrupted: object = None


@dataclass
class AgentTurn:
    config: dict
    state: dict
    run: AgentRun
    messages: list
    context: dict
    requested: set
    toolless: bool
    instructions: str
    history: list
    all_tools: list
    hidden: list
    request_text: str
    rules: dict
    max_rounds: int
    tool_log: list
    steps_this_turn: list
    client_names: set
    last_request: str
    think: object
    shown_thinking: object
    chat_approved: str | None
    raw: dict = field(default_factory=dict)
    nudged: bool = False
    refusal_sent_back: bool = False
    replays: int = 0
    searched_for_it: bool = False
    answer_from_lookups: bool = False
    code_checked: bool = False
    code_rewrites: int = 0
    sandbox_seconds: float = 0.0
    sandbox_report: object = None
    output_mismatches: list = field(default_factory=list)
    output_rewritten: bool = False
    example_checks: str = ""
    examples_written: bool = False
    example_outcome: object = None
    private: object = private_text.NOTHING_HIDDEN
    refused_steps: int = 0
    asked_about_set_up: bool = False
    user_requests: str = ""
    private_set_up: str = ""
    approval_key: str | None = None
    withheld: frozenset = frozenset()
    approvals: bool = True

    @property
    def is_chat(self) -> bool:
        return not self.run.background and not self.run.agent

    def used_no_tools(self) -> bool:
        return not (self.tool_log or self.steps_this_turn)

    def add(self, message):
        self.messages.append(message)
        self.history.append(message)

    def keep_groups(self):
        self.state["groups"] = sorted(self.requested)

    def outcome(self, status: str, **fields) -> dict:
        return {"status": status, **fields, "tools_used": self.tool_log, "raw": self.raw}


BACKGROUND_TOOL_ROUNDS = 30
OUT_OF_TIME_ANSWER = "I ran out of time on this one. Ask me again, perhaps a shorter way."
TOO_MANY_STEPS_ANSWER = "I stopped after too many tool steps. Tell me how you'd like me to continue."
ROOM_UNSEEN_LINK = ("Not read: in the room you never open web pages, whoever names them. Answer from your search "
                    "results, or without them.")


class TurnEnded(RuntimeError):
    pass


def run_agent(config, state, run: AgentRun = AgentRun()):
    """The tool loop. state = {"messages": [...], "context": {...}, "groups": [...]} and is updated in place.
    Returns {"status": "done", "answer": ...} or {"status": "waiting", "waiting": {...}}.
    With a draft (drafts.Draft), the answer's safe sentences go out while the model writes."""
    try:
        return run_rounds(config, state, run)
    except (Exception, KeyboardInterrupt):
        raise
    except BaseException as error:
        log.exception("the turn was ended by %r; answering with an error instead", error)
        raise TurnEnded(f"the turn was ended by {error!r}") from error


ROOM_REFUSES_MODEL = "this model is never used in the room"
ROOM_REFUSAL_STATUS = 503


def refuse_an_unrestricted_room_turn(config: dict, run: AgentRun) -> None:
    if run.room and content_rules.is_unrestricted(config, config["model"]):
        log.warning("a room turn was routed to %s, which has no safety rules; refused", config["model"])
        raise ApiError(ROOM_REFUSAL_STATUS, ROOM_REFUSES_MODEL)


def run_rounds(config, state, run: AgentRun) -> dict:
    refuse_an_unrestricted_room_turn(config, run)
    turn = start_turn(config, state, run)
    for round_number in range(turn.max_rounds):
        if is_cancelled(run.task):
            return turn.outcome("cancelled")
        if has_run_out_of_time(run):
            return out_of_time(turn)
        outcome = agent_round(turn, round_number)
        if outcome is not None:
            return without_private_text(turn, outcome)
    turn.keep_groups()
    return turn.outcome("done", answer=TOO_MANY_STEPS_ANSWER)


def has_run_out_of_time(run: AgentRun) -> bool:
    if run.deadline is not None and time.monotonic() >= run.deadline:
        return True
    return bool(run.is_interrupted and run.is_interrupted())


def out_of_time(turn: AgentTurn) -> dict:
    turn.keep_groups()
    return turn.outcome("done", answer=OUT_OF_TIME_ANSWER, out_of_time=True)


def without_private_text(turn: AgentTurn, outcome: dict) -> dict:
    for key in ("answer", "content"):
        if isinstance(outcome.get(key), str):
            outcome[key] = checked_for_the_set_up(turn, private_text.without_hidden_text(outcome[key], turn.private))
    waiting = outcome.get("waiting")
    if isinstance(waiting, dict) and isinstance(waiting.get("message"), str):
        waiting["message"] = checked_for_the_set_up(turn, private_text.without_hidden_text(waiting["message"], turn.private))
    return outcome


def has_a_private_set_up(turn: AgentTurn) -> bool:
    return run_has_a_private_set_up(turn.run)


def run_has_a_private_set_up(run: AgentRun) -> bool:
    return not (run.agent and run.client_system)


def checked_for_the_set_up(turn: AgentTurn, text: str) -> str:
    if not has_a_private_set_up(turn):
        return text
    return private_check.checked_reply(text, turn.user_requests, turn.private, turn.asked_about_set_up, set_up_checker(turn))


def set_up_checker(turn: AgentTurn) -> private_check.Checker:
    return private_check.Checker(turn.config["model"], turn.config.get("keep_alive"), turn.private_set_up)


def call_gives_away_the_set_up(turn: AgentTurn, call: dict) -> bool:
    if call_holds_private_text(call, turn.private):
        return True
    if not has_a_private_set_up(turn):
        return False
    texts = argument_texts((call.get("function") or {}).get("arguments"))
    return private_check.tool_text_gives_away(texts, turn.user_requests, turn.private, turn.asked_about_set_up,
                                              set_up_checker(turn))


def texts_in(value) -> list[str]:
    texts, waiting = [], [value]
    while waiting:
        item = waiting.pop()
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            waiting.extend(reversed([part for pair in item.items() for part in pair]))
        elif isinstance(item, (list, tuple)):
            waiting.extend(reversed(item))
    return texts


def argument_texts(raw) -> list[str]:
    if not isinstance(raw, str):
        return texts_in(raw)
    try:
        return texts_in(json.loads(raw, object_pairs_hook=list))
    except (ValueError, RecursionError):
        return [raw]


def call_holds_private_text(call: dict, hidden: private_text.HiddenText) -> bool:
    texts = argument_texts((call.get("function") or {}).get("arguments"))
    if private_text.repeats_hidden_text("\n".join(texts), hidden):
        return True
    return len(texts) > 1 and any(private_text.decoded_leaks(text, hidden) for text in texts)


def with_held_arguments_left_out(call: dict) -> dict:
    return {**call, "function": {**(call.get("function") or {}), "arguments": dict(HELD_ARGUMENTS)}}


def for_the_model(config: dict, run: AgentRun) -> AgentRun:
    return replace(run, client_tools=content_rules.client_tools_for_model(config["model"], run.client_tools))


def own_instructions(config: dict, run: AgentRun, toolless: bool) -> str:
    if run_has_a_private_set_up(run):
        return system_prompt(config, run.client_system, run.background, toolless)
    if content_rules.is_remote(config["model"]):
        return content_rules.client_system_with_rules(config, run.client_system)
    return run.client_system


def start_turn(config: dict, state: dict, run: AgentRun) -> AgentTurn:
    connections.hub.refresh()
    asked_for_client_tools = bool(run.client_tools)
    run = for_the_model(config, run)
    config = {**config, "tool_budget": models.budget_for(config, config["model"])}
    messages = state["messages"]
    approval_key = policy.approval_key(messages, message_text) if run.chat_approved else None
    context = state.setdefault("context", {})
    requested = set(state.setdefault("groups", []))
    model_info = models.find(config["model"])
    toolless = bool(model_info) and not model_info["tools"]
    user_texts = [without_app_notes(message_text(m)) for m in messages if m.get("role") == "user"]
    asked_about_set_up = private_check.asks_about_the_set_up(user_texts)
    instructions = own_instructions(config, run, toolless)
    private_turn = asked_about_set_up and run_has_a_private_set_up(run)
    seen = system_prompt(with_persona_left_out(config), run.client_system, run.background, toolless) \
        if private_turn else instructions
    asked_for_set_up_itself = private_turn and private_check.asks_for_the_set_up_itself(user_texts)
    history = [{"role": "system", "content": seen}] + messages
    looked_up = run.looked_up or ([], "", None)
    add_turn_notes(with_memories_left_out(config) if asked_for_set_up_itself else config, run, history, messages, looked_up)
    all_tools = content_rules.tools_for_model(config, config["model"], tools_for_turn(config, run, toolless, looked_up))
    request_text = " ".join(message_text(m) for m in messages if m.get("role") == "user")[-2000:]
    rules = config.get("approval", {})
    steps_this_turn = client_tool_runs(messages)[0]
    policy.remember_client_results(context, steps_this_turn, rules)
    policy.remember_client_links(context, [(m["tool_name"], message_text(m)) for m in tool_results(messages)], rules)
    web_pages.start_reply(hashlib.sha256(request_text.encode("utf-8", "surrogateescape")).hexdigest())
    context["failed"] = []  # tools that didn't work this turn (see be_honest)
    last_request = newest_request(messages)
    private = hidden_notes.private_text_for(with_tool_guide(instructions, all_tools), all_tools, run.client_texts)
    shows_thinking = bool(run.agent and run.client_system and run.on_thinking)
    turn = AgentTurn(
        config=config, state=state, run=run, messages=messages, context=context, requested=requested, toolless=toolless,
        instructions=seen, history=history, all_tools=all_tools, hidden=hidden_tools(config),
        request_text=request_text, rules=rules, max_rounds=max_tool_rounds(config, run.background),
        tool_log=list(looked_up[0]), steps_this_turn=steps_this_turn,
        client_names={tool["function"]["name"] for tool in run.client_tools or []}, last_request=last_request,
        think=thinking_for(config, agent_sessions.deciding_request(user_texts) if run.agent else last_request),
        shown_thinking=private_text.GuardedStream(run.on_thinking, private) if shows_thinking else None,
        chat_approved=run.chat_approved, private=private,
        asked_about_set_up=asked_about_set_up, user_requests="\n\n".join(user_texts), private_set_up=instructions,
        approval_key=approval_key, approvals=content_rules.approvals_apply(config, config["model"]))
    steer_the_request(turn)
    if asked_for_client_tools and not run.client_tools:
        steer_last_request(turn.history, REMOTE_COMPUTER_NOTE)
    # A plain question whose answer was looked up first: the model answers from that, with no tools to
    # wander off with (small models otherwise pick unrelated ones). Asked to do something, it keeps them.
    turn.answer_from_lookups = bool(looked_up[0]) and not run.background and not ACTION_REQUEST.search(last_request) \
        and not DO_REQUEST.search(last_request)
    return turn


def newest_request(messages: list) -> str:
    return without_app_notes(message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {})))


def max_tool_rounds(config: dict, background: bool) -> int:
    if background:
        return int(config.get("background", {}).get("max_rounds", BACKGROUND_TOOL_ROUNDS))
    return int(config.get("max_tool_rounds", MAX_TOOL_ROUNDS))


def add_turn_notes(config: dict, run: AgentRun, history: list, messages: list, looked_up: tuple):
    request = newest_request(messages)
    # Asked for something written (a post, an email): in the user's own voice (style.py).
    in_owner_voice = config.get("writes_in_owner_voice", True) and style.is_writing_request(request)
    voice = style.voice_note() if in_owner_voice else ""
    if not run.agent:
        with_context(history, current_context(config, request) + looked_up[1] + voice)
        steer_away_from_repeats(history, messages, request, run.background)


def hidden_tools(config: dict) -> list[str]:
    return [*(config.get("hidden_tools") or []), *sorted(browser_apps.TOOLS)]


def tools_for_turn(config: dict, run: AgentRun, toolless: bool, looked_up: tuple) -> list:
    connected = app_actions.connected_apps()
    native = [models.handoff_tool(config, definition) if name == "ask_other_model" else definition
              for name, definition in NATIVE_TOOLS.items()
              if not (run.background and name in BACKGROUND_ONLY_EXCLUDED) and app_actions.is_offered(name, connected)]
    leaves_tools_to_the_app = run.agent and not content_rules.is_remote(config["model"])
    all_tools = [] if toolless or leaves_tools_to_the_app else connections.hub.tools + native
    hidden = hidden_tools(config)
    all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], hidden)]
    if run.allowed_tools is not None:
        all_tools = [t for t in all_tools if t["function"]["name"] in run.allowed_tools]
    if SEARCH_ONLY in run.skip_groups:
        return [t for t in all_tools if t["function"]["name"] in SEARCH_ONLY_TOOLS]
    if run.skip_groups:
        # The calling app does these itself (e.g. the desktop app drives the user's own browser).
        skipped = [pattern for group in run.skip_groups for pattern in config.get("tool_groups", {}).get(group, {}).get("tools", [])]
        all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], skipped)]
    if looked_up[0]:
        all_tools = [t for t in all_tools if t["function"]["name"] not in looked_up[0]]  # its result is already in the notes
    return all_tools


def steer_the_request(turn: AgentTurn):
    last_request = turn.last_request
    if turn.is_chat and content_rules.keeps_safety_rules(turn.config, turn.config["model"]) and asks_to_disguise_harm(last_request):
        steer_last_request(turn.history, HARMFUL_REWRITE_NOTE)
    if turn.is_chat and is_coursework_request(last_request) and not asks_for_revision(last_request):
        turn.withheld = turn.withheld | STUDY_TOOLS
    if turn.is_chat and asks_how_to(last_request):
        withhold_run_tools(turn)
        steer_last_request(turn.history, HOW_TO_NOTE)
    elif turn.is_chat and asks_for_code(last_request):
        steer_a_code_request(turn, last_request)
    elif turn.is_chat and run_requests.follows_code(turn.messages):
        steer_a_follow_up_to_code(turn, last_request)
    link_reader = written_calls.offered_name("fetch", [tool["function"]["name"] for tool in turn.all_tools])
    if turn.is_chat and link_reader and asks_to_read_a_link(last_request):
        steer_last_request(turn.history, READ_LINK_NOTE.format(tool=link_reader))


def judged_run_request(turn: AgentTurn, request: str, after_code: bool) -> bool | None:
    if turn.run.room:
        return False
    return run_requests.judge_says_run(request, after_code=after_code, model=turn.config["model"],
                                       keep_alive=turn.config.get("keep_alive"))


def steer_a_code_request(turn: AgentTurn, request: str):
    deferred = run_requests.defers_running(request)
    verdict = False if deferred else judged_run_request(turn, request, after_code=False)
    unchecked = verdict is None
    if verdict:
        steer_last_request(turn.history, WRITE_AND_RUN_NOTE)
    elif not asks_for_the_computer(request):
        withhold_run_tools(turn)
        steer_last_request(turn.history, WRITE_ONLY_UNCHECKED_NOTE if unchecked else WRITE_ONLY_NOTE)
    elif not asks_to_install(request):
        withhold_run_command(turn)
        steer_last_request(turn.history, DONT_RUN_UNCHECKED_NOTE if unchecked else DONT_RUN_NOTE)
    elif deferred or unchecked:
        steer_last_request(turn.history, DONT_RUN_UNCHECKED_NOTE if unchecked else DONT_RUN_NOTE)


def steer_a_follow_up_to_code(turn: AgentTurn, request: str):
    if run_requests.defers_running(request):
        withhold_run_command(turn)
        steer_last_request(turn.history, DONT_RUN_NOTE)
        return
    verdict = judged_run_request(turn, request, after_code=True)
    if verdict:
        steer_last_request(turn.history, RUN_THE_CODE_NOTE)
    elif run_requests.mentions_running(request):
        withhold_run_command(turn)
        steer_last_request(turn.history, DONT_RUN_UNCHECKED_NOTE if verdict is None else DONT_RUN_NOTE)


def withhold_run_command(turn: AgentTurn):
    turn.all_tools = [tool for tool in turn.all_tools if tool["function"]["name"] not in RUN_COMMAND_TOOLS]
    turn.client_names = turn.client_names - DESKTOP_RUN_COMMAND_TOOLS


def withhold_run_tools(turn: AgentTurn):
    turn.all_tools = without_run_tools(turn.all_tools)
    turn.client_names = turn.client_names - DESKTOP_RUN_TOOLS


def without_run_tools(tools: list) -> list:
    return [tool for tool in tools if tool["function"]["name"] not in RUN_TOOLS]


def is_cancelled(task: dict | None) -> bool:
    return bool(task) and (store.get_task(task["id"]) or {}).get("status") == "cancelled"


def agent_round(turn: AgentTurn, round_number: int) -> dict | None:
    tools = offered_tools(turn, round_number)
    body = request_body(turn, tools)
    turn.state["prefix"] = {"model": body["model"], "messages": [m for m in body["messages"][:1] if m.get("role") == "system"],
                            "tools": body.get("tools") or []}
    risky = start_draft_round(turn, tools)
    turn.raw = model_reply(body, turn.config, turn.run.draft, risky, turn.shown_thinking, turn.run.agent,
                           tools_that_ran(turn.messages))
    log_model_timings(turn.raw)
    if turn.run.agent:
        turn.state.setdefault("rounds", []).append(round_timings(turn.config["model"], turn.raw, body.get("think")))
    message = turn.raw["message"]
    if turn.raw.get(ENGINE_FAILED_KEY):
        turn.keep_groups()
        return turn.outcome("done", answer=message["content"])
    calls = message.get("tool_calls") or []
    if written_calls.looks_swallowed(turn.raw, tools) and turn.replays < written_calls.REPLAY_LIMIT:
        recovered = recover_swallowed_calls(turn, body, tools, message, calls)
        if recovered is None:
            return None
        message, calls = recovered
    if not calls:
        return answer_without_calls(turn, tools, message)
    return act_on_calls(turn, message, calls)


def offered_tools(turn: AgentTurn, round_number: int) -> list:
    tools = policy.select_tools(turn.all_tools, turn.config, turn.request_text, turn.requested)
    if len(tools) < len(turn.all_tools):
        tools = [policy.use_tools_definition(turn.config)] + tools  # first: it never changes, so it stays cached
    tools = [tool for tool in tools if tool["function"]["name"] not in turn.withheld]
    if turn.run.client_tools and not turn.toolless:
        tools = tools + [tool for tool in turn.run.client_tools if tool["function"]["name"] in turn.client_names]
    if turn.answer_from_lookups and (round_number == 0 or turn.code_checked):
        tools = []
    if turn.code_rewrites:
        tools = []
    return tools


def request_body(turn: AgentTurn, tools: list) -> dict:
    config, agent = turn.config, turn.run.agent
    if not (agent and turn.run.client_system):
        turn.history[0] = {"role": "system", "content": with_tool_guide(turn.instructions, tools)}
    options = models.agent_options(config, config["model"], bool(turn.think)) if agent else config.get("options", {})
    if turn.answer_from_lookups:
        options = {**options, "temperature": LOOKUP_TEMPERATURE}  # stick to the looked-up facts
    body = models.for_runner(config, with_thinking({"model": config["model"], "messages": turn.history, "stream": False,
                                                    "options": options, "keep_alive": config.get("keep_alive", "10m")}, turn.think,
                                                   raises_reply_size=not agent))
    if tools:
        body["tools"] = tools
    if agent:
        body["truncate"] = False
        return body
    return fitted_to_window(config, body)


def start_draft_round(turn: AgentTurn, tools: list):
    draft, agent = turn.run.draft, turn.run.agent
    if not draft:
        return None
    # Hold back what the checks below might still throw away: a reply that will be sent back to use
    # its tools, or replaced by a web search, mustn't have been shown (or said out loud) already.
    may_nudge = not agent and not turn.nudged and bool(tools) and turn.used_no_tools()
    may_search = not agent and turn.nudged and turn.used_no_tools() and not turn.searched_for_it \
        and "research__web_search" in connections.hub.routes
    draft.start_round(allowed=not turn.asked_about_set_up and not (may_nudge and asks_for_action(turn))
                      and not (may_search and asks_for_a_search(turn))
                      and not page_to_check_code_against(turn) and not sandbox_may_check(turn),
                      holds_line=code_sandbox.may_become_a_summary_line)
    watch, talk = private_text.Watch(turn.private), private_check.TalkWatch()

    def risky(text, may_nudge=may_nudge, may_search=may_search):
        if watch.repeats(text) or code_sandbox.has_lookalike_summary(text) or talk.sounds_like_set_up_talk(text):
            return True
        if agent:
            return bool(PROMPT_LEAK.search(text)) or written_calls.has_call_markup(text)
        if PROMPT_LEAK.search(text) or TOOL_TALK.search(text) or written_calls.has_call_markup(text):
            return True
        if ACTION_CLAIM.search(text) and not acted_for_sure(turn.tool_log, turn.context.get("failed", [])):
            return True
        if may_turn_down_needlessly(turn, text):
            return True
        if may_nudge and (PROMISE.search(text) or CLAIMED.search(text)):
            return True
        return (may_nudge or may_search) and bool(CLARIFYING.search(text) or "web_search" in text)
    return risky


def log_model_timings(raw: dict):
    log.info("model: loaded in %.1fs, read the %s-token prompt in %.1fs (cached parts are free), wrote %s in %.1fs",
             raw.get("load_duration", 0) / 1e9,
             raw.get("prompt_eval_count", "?"), raw.get("prompt_eval_duration", 0) / 1e9,
             raw.get("eval_count", "?"), raw.get("eval_duration", 0) / 1e9)


def recover_swallowed_calls(turn: AgentTurn, body: dict, tools: list, message: dict, calls: list) -> tuple | None:
    turn.replays += 1
    replayed_text = written_reply(body)
    offered = [tool["function"]["name"] for tool in tools]
    found = written_calls.calls_written_in(replayed_text)
    renamed = [written_calls.offered_name(call["function"]["name"], offered) for call in found]
    if found and all(renamed):
        calls = [{"function": {**call["function"], "name": name}} for call, name in zip(found, renamed)]
        log.info("the engine dropped the model's call to %s; using the call it wrote", ", ".join(renamed))
        return {**message, "tool_calls": calls}, calls
    if found:
        missing = [call["function"]["name"] for call, name in zip(found, renamed) if not name]
        log.warning("the model called %s, which it wasn't offered; told it which tools it has", ", ".join(missing))
        send_back(turn, replayed_text.strip(), written_calls.missing_tool_note(missing, offered))
        return None
    return {**message, "content": written_calls.spoken_text(replayed_text)}, calls


def send_back(turn: AgentTurn, reply: str, note: str):
    turn.history.append({"role": "assistant", "content": reply})
    turn.history.append({"role": "user", "content": note})


def answer_without_calls(turn: AgentTurn, tools: list, message: dict) -> dict | None:
    answer = written_calls.without_call_markup(message.get("content") or "")
    if turn.run.background and turn.context.get("blocked"):
        return wait_for_sign_in(turn)
    if should_search_for_the_model(turn, answer):
        if has_run_out_of_time(turn.run):
            return out_of_time(turn)
        search_for_the_model(turn, answer)
        return None
    if claims_without_tools(turn, tools, answer):
        nudge_to_use_tools(turn, tools, answer)
        return None
    if plays_at_tools(turn, tools, answer):
        nudge_away_from_pretend_tools(turn, answer)
        return None
    if not turn.nudged and tools and answer.count("```") % 2 == 1 and FUMBLED_CALL.search(answer):
        nudge_to_finish_the_call(turn, answer)
        return None
    if not answer.strip() and not turn.nudged:
        nudge_after_an_empty_reply(turn)
        return None
    if note := needless_refusal_note(turn, answer):
        send_back_a_needless_refusal(turn, answer, note)
        return None
    answer = without_an_unasked_deflection(turn, answer)
    if rewrite_unconfirmed_code(turn, answer):
        return None
    if rewrite_failing_code(turn, answer):
        return None
    if rewrite_failing_examples(turn, answer):
        return None
    if turn.raw.get("done_reason") == "length" and not turn.run.room:
        answer += cut_short_note(turn.config, turn.raw, turn.run.agent)
    turn.keep_groups()
    answer = reads_fresh(turn, code_sandbox.without_lookalike_summaries(with_real_output(turn, answer)))
    sandbox_line = sandbox_summary(turn)
    return turn.outcome("done", answer=f"{answer.rstrip()}\n\n{sandbox_line}" if sandbox_line else answer, sandbox_line=sandbox_line)


def page_to_check_code_against(turn: AgentTurn) -> str:
    if not turn.is_chat or turn.run.room or turn.code_checked or not turn.run.looked_up:
        return ""
    return docs_reading.page_read_in(turn.run.looked_up[1])


def rewrite_unconfirmed_code(turn: AgentTurn, answer: str) -> bool:
    page = page_to_check_code_against(turn)
    if not page:
        return False
    turn.code_checked = True
    try:
        unconfirmed = code_check.unconfirmed_calls(answer, turn.run.looked_up[1])
    except Exception as error:
        log.warning("couldn't check the answer's code against %s: %r", page, error)
        return False
    if not unconfirmed:
        return False
    log.info("the answer's code calls %s, which %s doesn't show; sent back once to rewrite it", unconfirmed, page)
    turn.code_rewrites += 1
    send_back(turn, answer, code_check.rewrite_note(page, unconfirmed))
    return True


def sandbox_may_check(turn: AgentTurn) -> bool:
    return turn.is_chat and not turn.run.room and not content_rules.is_remote(turn.config["model"]) and code_sandbox.is_on() \
        and coding.is_request(turn.last_request)


def rewrite_failing_code(turn: AgentTurn, answer: str) -> bool:
    turn.sandbox_report = None
    turn.output_mismatches = []
    if not sandbox_may_check(turn) or turn.raw.get("done_reason") == "length":
        return False
    started = time.monotonic()
    try:
        turn.sandbox_report = code_sandbox.report_for(answer, turn.last_request, code_sandbox.TURN_SECONDS - turn.sandbox_seconds)
        failures = turn.sandbox_report.failures() if turn.sandbox_report else []
        if not failures:
            turn.output_mismatches = code_sandbox.output_mismatches(
                answer, turn.sandbox_report, code_sandbox.TURN_SECONDS - turn.sandbox_seconds - (time.monotonic() - started))
    finally:
        turn.sandbox_seconds += time.monotonic() - started
    if not failures:
        return rewrite_wrong_output(turn, answer)
    if turn.code_rewrites >= code_sandbox.MAX_REWRITES:
        return False
    turn.code_rewrites += 1
    turn.think = thinking_for(turn.config, turn.last_request, writing_job=True)
    log.info("the sandbox found errors in %s; sent back to fix them (rewrite %d of %d)",
             ", ".join(unit.label for unit, _ in failures), turn.code_rewrites, code_sandbox.MAX_REWRITES)
    send_back(turn, answer, code_sandbox.rewrite_note(failures))
    return True


def rewrite_wrong_output(turn: AgentTurn, answer: str) -> bool:
    if not turn.output_mismatches or turn.output_rewritten or turn.code_rewrites >= code_sandbox.MAX_REWRITES:
        return False
    turn.output_rewritten = True
    turn.code_rewrites += 1
    log.info("the answer shows output its code doesn't print; sent back once to fix it")
    send_back(turn, answer, code_sandbox.output_note(turn.output_mismatches))
    return True


def examples_may_be_checked(turn: AgentTurn) -> bool:
    return bool(turn.config.get("example_checks")) and sandbox_may_check(turn) and not turn.output_mismatches \
        and turn.raw.get("done_reason") != "length"


def rewrite_failing_examples(turn: AgentTurn, answer: str) -> bool:
    turn.example_outcome = None
    if not examples_may_be_checked(turn):
        return False
    unit = example_checks.checkable_unit(turn.sandbox_report)
    if unit is None or unit.language not in example_checks.checked_languages(turn.config) \
            or not example_checks.is_worth_checking(turn.last_request, unit):
        return False
    started = time.monotonic()
    try:
        if not turn.examples_written and turn.code_rewrites < code_sandbox.MAX_REWRITES:
            turn.examples_written = True
            turn.example_checks = example_checks.written_checks(turn.config, turn.last_request, unit)
        if turn.example_checks:
            turn.example_outcome = example_checks.run_checks(
                unit, turn.example_checks, turn.last_request, code_sandbox.TURN_SECONDS - turn.sandbox_seconds - (time.monotonic() - started))
    finally:
        turn.sandbox_seconds += time.monotonic() - started
    outcome = turn.example_outcome
    if outcome is None or not outcome.is_failing or turn.code_rewrites >= code_sandbox.MAX_REWRITES:
        return False
    turn.code_rewrites += 1
    turn.think = thinking_for(turn.config, turn.last_request, writing_job=True)
    log.info("the code failed checks from the question's examples; sent back to fix it (rewrite %d of %d)",
             turn.code_rewrites, code_sandbox.MAX_REWRITES)
    send_back(turn, answer, example_checks.rewrite_note(unit, turn.example_checks, outcome))
    return True


def reads_fresh(turn: AgentTurn, answer: str) -> str:
    if not fresh_answer.hides_rewrites(turn.code_rewrites, turn.last_request):
        return answer
    return fresh_answer.without_rewrite_talk(answer, code_sandbox.code_spans(answer))


def with_real_output(turn: AgentTurn, answer: str) -> str:
    if not turn.output_mismatches:
        return answer
    log.info("replaced output the answer claimed with what the code really printed")
    return code_sandbox.with_real_output(answer, turn.output_mismatches)


def sandbox_summary(turn: AgentTurn) -> str:
    report = turn.sandbox_report
    if report is None:
        return ""
    if report.ran_anything():
        turn.tool_log.append(code_sandbox.TOOL_NAME)
    examples = example_checks.summary_part(turn.example_outcome, turn.last_request)
    return code_sandbox.summary(report, bool(turn.output_mismatches), examples)


def wait_for_sign_in(turn: AgentTurn) -> dict:
    # Small models tend to report "please sign in" and stop; pause and ask properly instead.
    host = turn.context.pop("blocked")
    turn.keep_groups()
    return turn.outcome("waiting", waiting={"kind": "user", "message": f"Please sign in (or pass the bot check) on {host} in the browser on my desktop.",
                                            "call": {"name": "ask_user", "arguments": {}}})


def room_may_search(turn: AgentTurn) -> bool:
    return not turn.run.room or "research__web_search" in (turn.run.allowed_tools or ())


def asks_for_action(turn: AgentTurn) -> bool:
    return room_may_search(turn) and bool(ACTION_REQUEST.search(turn.last_request))


def asks_for_a_search(turn: AgentTurn) -> bool:
    return room_may_search(turn) and bool(SEARCH_REQUEST.search(turn.last_request))


def should_search_for_the_model(turn: AgentTurn, answer: str) -> bool:
    return bool(turn.is_chat and turn.nudged and turn.used_no_tools() and not turn.searched_for_it
                and room_may_search(turn) and "research__web_search" in connections.hub.routes
                and (CLARIFYING.search(answer) or "web_search" in answer or asks_for_a_search(turn)))


def search_for_the_model(turn: AgentTurn, answer: str):
    # Sent back once and still no search: run it ourselves and have the model answer from the results.
    turn.searched_for_it = True
    results = connections.hub.call("research__web_search", {"query": turn.last_request[:200]})
    turn.tool_log.append("research__web_search")
    log.info("searched for the model: %s", turn.last_request[:80])
    send_back(turn, answer, SEARCH_RESULTS_NOTE.format(results=results[:TOOL_RESULT_CHARS]))


def claims_without_tools(turn: AgentTurn, tools: list, answer: str) -> bool:
    return bool(turn.is_chat and not turn.nudged and tools and turn.used_no_tools()
                and (PROMISE.search(answer) or CLAIMED.search(answer) or CLARIFYING.search(answer)
                     or asks_for_action(turn)))


def nudge_to_use_tools(turn: AgentTurn, tools: list, answer: str):
    # Small models describe doing things ("I've set a reminder", "here are my findings") without
    # calling a tool; send them back once to actually do it.
    turn.nudged = True
    send_back(turn, answer, NOTHING_DONE_NOTE.format(nudge=tool_guide.nudge_tools(tool_guide.offered_names(tools))))


def plays_at_tools(turn: AgentTurn, tools: list, answer: str) -> bool:
    room_turn_without_tools = turn.run.room and not tools
    return turn.is_chat and not turn.nudged and not room_turn_without_tools and bool(PRETEND_TOOLS.search(answer))


def nudge_away_from_pretend_tools(turn: AgentTurn, answer: str):
    turn.nudged = True
    log.info("reply played at using tools; sent back once")
    send_back(turn, answer, PRETEND_TOOLS_NOTE)


def nudge_to_finish_the_call(turn: AgentTurn, answer: str):
    # It started writing a tool call as text and stopped: have it make the call properly.
    turn.nudged = True
    log.info("reply ended in a half-written tool call; sent back once")
    send_back(turn, answer, HALF_WRITTEN_CALL_NOTE)


def may_be_sent_back_for_a_refusal(turn: AgentTurn) -> bool:
    return turn.is_chat and not turn.run.room and not turn.refusal_sent_back and turn.used_no_tools()


def turns_down_coursework(turn: AgentTurn, answer: str) -> bool:
    return is_needless_coursework_refusal(turn.last_request, answer, typed_by_user(turn))


def deflects_a_plain_question(turn: AgentTurn, answer: str) -> bool:
    return (not turn.asked_about_set_up and is_plain_identity_question(turn.last_request)
            and has_a_deflection(answer))


def may_turn_down_needlessly(turn: AgentTurn, text: str) -> bool:
    return turn.is_chat and not turn.run.room and (deflects_a_plain_question(turn, text)
                             or (may_be_sent_back_for_a_refusal(turn) and turns_down_coursework(turn, text)))


def needless_refusal_note(turn: AgentTurn, answer: str) -> str | None:
    if not may_be_sent_back_for_a_refusal(turn):
        return None
    if turns_down_coursework(turn, answer):
        return COURSEWORK_NOTE
    if deflects_a_plain_question(turn, answer) and is_only_a_deflection(answer):
        return UNASKED_PRIVACY_NOTE
    return None


def send_back_a_needless_refusal(turn: AgentTurn, answer: str, note: str):
    turn.refusal_sent_back = True
    log.info("reply turned down a request it should have helped with; sent back once")
    send_back(turn, answer, note)


def without_an_unasked_deflection(turn: AgentTurn, answer: str) -> str:
    if not turn.is_chat or not deflects_a_plain_question(turn, answer):
        return answer
    return without_deflection(answer) or answer


def nudge_after_an_empty_reply(turn: AgentTurn):
    # Small models occasionally return nothing at all; one nudge almost always gets a real answer.
    turn.nudged = True
    turn.history.append({"role": "user", "content": EMPTY_REPLY_NOTE})


def runs_every_call_in_the_app(turn: AgentTurn) -> bool:
    return turn.run.agent and not content_rules.is_remote(turn.config["model"])


def act_on_calls(turn: AgentTurn, message: dict, calls: list) -> dict | None:
    held = [call_gives_away_the_set_up(turn, call) for call in calls]
    if any(held):
        return refuse_calls_holding_private_text(turn, message, calls, held)
    written = written_calls.without_call_markup(message.get("content") or "").strip()
    if answered_before_unlocking(turn, calls, written):
        # It answered a plain question, then asked for tools it doesn't need (the next round would only
        # chat about the unlocked tools): keep the answer. Not when it's about to actually do something.
        turn.keep_groups()
        return turn.outcome("done", answer=code_sandbox.without_lookalike_summaries(written))
    if turn.run.draft:
        turn.run.draft.before_tools()
    client_calls = calls if runs_every_call_in_the_app(turn) else \
        [call for call in calls if call.get("function", {}).get("name") in turn.client_names]
    if client_calls:
        turn.keep_groups()
        return {"status": "client_tools", "calls": client_calls,
                "content": code_sandbox.without_lookalike_summaries(written_calls.without_call_markup(message.get("content") or "")),
                "tools_used": turn.tool_log + [c["function"]["name"] for c in client_calls], "raw": turn.raw}
    turn.add({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
    for call in calls:
        if has_run_out_of_time(turn.run):
            return out_of_time(turn)
        outcome = run_tool_call(turn, call, calls)
        if outcome is not None:
            return outcome
    return None


def refuse_calls_holding_private_text(turn: AgentTurn, message: dict, calls: list, held: list) -> dict | None:
    turn.refused_steps += 1
    log.info("not running %d tool call(s): %d held private text (refused step %d)", len(calls), sum(held), turn.refused_steps)
    if turn.run.draft:
        turn.run.draft.before_tools()
    kept = [with_held_arguments_left_out(call) if holds else call for call, holds in zip(calls, held)]
    turn.add({"role": "assistant", "content": message.get("content") or "", "tool_calls": kept})
    for call, holds in zip(kept, held):
        function = call.get("function") or {}
        record_tool_result(turn, function.get("name") or "", parse_arguments(function.get("arguments")),
                           PRIVATE_CALL_NOT_RUN if holds else SIBLING_CALL_NOT_RUN)
    if turn.refused_steps < REFUSED_STEPS_BEFORE_STOPPING:
        return None
    turn.keep_groups()
    names = [(call.get("function") or {}).get("name") or "" for call, holds in zip(calls, held) if holds]
    return turn.outcome("done", answer=stop_answer(turn, names))


def steps_taken_first(turn: AgentTurn) -> list[str]:
    return list(turn.run.looked_up[0]) if turn.run.looked_up else []


def stop_answer(turn: AgentTurn, names: list[str]) -> str:
    taken_first, ran = steps_taken_first(turn), tools_that_ran(turn.messages)
    worked = taken_first + [name for name, result in ran if not did_not_work(result)]
    return held_calls_answer(names, worked, anything_ran=bool(taken_first or ran))


def plain_tool_name(name: str) -> str:
    return name.rsplit("__", 1)[-1].replace("_", " ")


def plain_tool_names(names: list[str]) -> list[str]:
    return list(dict.fromkeys(plain_tool_name(name) or UNNAMED_TOOL for name in names))


def held_calls_answer(names: list[str], worked: list[str] = (), anything_ran: bool = False) -> str:
    held, steps = plain_tool_names(names), plain_tool_names(worked)
    calls, plural = " and ".join(held), "s" if len(held) > 1 else ""
    if not (steps or anything_ran):
        return HELD_CALLS_ANSWER.format(calls=calls, plural=plural)
    not_made = HELD_CALLS_NOT_MADE.format(calls=calls, plural=plural, held="they" if plural else "it")
    return not_made + (STEPS_THAT_WORKED.format(steps=", ".join(steps)) if steps else "") + ASK_AGAIN


def answered_before_unlocking(turn: AgentTurn, calls: list, written: str) -> bool:
    return bool(turn.is_chat and all(call.get("function", {}).get("name") == "use_tools" for call in calls)
                and len(written) >= ANSWERED_ALREADY_CHARS and not ACTION_REQUEST.search(turn.last_request)
                and not (PROMISE.search(written) or CLAIMED.search(written)))


def run_tool_call(turn: AgentTurn, call: dict, calls: list) -> dict | None:
    function = call.get("function", {})
    name = function.get("name") or ""
    arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
    if not was_offered(turn, name):
        log.warning("the model called %s, which it wasn't offered this turn; not run", name)
        record_tool_result(turn, name, arguments, written_calls.missing_tool_note([name], offered_names(turn)))
        return None
    if name == "schedule_task" and not turn.run.background and not asked_to_schedule(turn):
        log.warning("the model called schedule_task but the message didn't ask for a reminder or schedule; not run")
        record_tool_result(turn, name, arguments, SCHEDULE_NOT_ASKED)
        return None
    turn.tool_log.append(name)
    if name == "use_tools":
        result = unlock_groups(turn, arguments)
    elif policy.matches(name, turn.hidden):
        result = HIDDEN_TOOL_RESULT
    elif name in app_actions.ASKS_FIRST:
        result, outcome = run_app_action(turn, call, calls, name, arguments)
        if outcome is not None:
            return outcome
    elif refused := home_network_fetch(name, arguments):
        result = refused
    elif turn.run.room and name in policy.WEB_READS:
        result = ROOM_UNSEEN_LINK
    elif (held := policy.web_read_reason(name, arguments, turn.context, own_request(turn))) \
            and holds_for_approval(turn, held):
        return wait_for_approval(turn, held, call, calls)
    elif name in NATIVE_TOOLS and (held := policy.native_reason(name, arguments, turn.context, turn.rules)) \
            and holds_for_approval(turn, held, background_holds=False):
        return wait_for_approval(turn, held, call, calls)
    elif name in NATIVE_TOOLS and held and turn.approvals and (done_before := spent_already(turn, held)):
        return done_before
    elif name in NATIVE_TOOLS:
        result, outcome = run_native_call(turn, name, arguments)
        if outcome is not None:
            return outcome
        policy.remember_tool_result(turn.context, name, result, turn.rules)
    elif refused := private_upload(name, arguments):
        result = refused
    else:
        result, outcome = run_hub_call(turn, call, calls, name, arguments)
        if outcome is not None:
            return outcome
    note_a_desktop_sign_in(turn, name, result)
    record_tool_result(turn, name, arguments, result)
    return None


def note_a_desktop_sign_in(turn: AgentTurn, name: str, result: str):
    if name in app_actions.TOOLS and browser_apps.needs_sign_in(result):
        turn.tool_log.append(browser_apps.REQUEST_TOOL)


def reply_before_the_request(messages: list) -> str:
    last_request = max((index for index, message in enumerate(messages) if message.get("role") == "user"), default=0)
    return message_text(next((message for message in reversed(messages[:last_request]) if message.get("role") == "assistant"), {}))


def asked_to_schedule(turn: AgentTurn) -> bool:
    if SCHEDULE_REQUEST.search(turn.last_request):
        return True
    return policy.is_plain_approval(turn.last_request) and bool(OFFERED_A_SCHEDULE.search(reply_before_the_request(turn.messages)))


def offered_names(turn: AgentTurn) -> list[str]:
    return [tool["function"]["name"] for tool in turn.all_tools if tool["function"]["name"] not in turn.withheld]


def was_offered(turn: AgentTurn, name: str) -> bool:
    return name == "use_tools" or policy.matches(name, turn.hidden) or name in offered_names(turn)


def unlock_groups(turn: AgentTurn, arguments: dict) -> str:
    wanted = [g for g in arguments.get("groups") or [] if g in turn.config.get("tool_groups", {})]
    turn.requested.update(wanted)
    return f"Unlocked: {', '.join(wanted) or 'nothing (unknown group)'}. Those tools are available from your next step."


def holds_for_approval(turn: AgentTurn, reason: str, background_holds: bool = True) -> bool:
    if not turn.approvals:
        return False
    return (background_holds and turn.run.background) or not policy.covers(turn.chat_approved, reason)


def wait_for_approval(turn: AgentTurn, reason: str, call: dict, calls: list) -> dict:
    return hold_for_approval(reason, call, calls, turn.run.background, turn.state, turn.requested, turn.add, turn.tool_log,
                             turn.raw)


def run_native_call(turn: AgentTurn, name: str, arguments: dict) -> tuple[str, dict | None]:
    result, waiting = run_native_safely(name, arguments, turn.config, turn.run.background, turn.run.task)
    turn.requested.update(TOOL_UNLOCKS.get(name, ()))
    if waiting and waiting["kind"] == "chat_question" and not any(used.startswith(DESKTOP_TOOLS) for used in turn.tool_log):
        # Nothing on the desktop was tried, so no website can have blocked it: the model reached for this
        # when unsure (it once answered "how do I kill someone" with the tool's sign-in example).
        log.info("ignored ask_user with no desktop tool used: %s", waiting["message"][:120])
        result, waiting = NOTHING_TO_SIGN_IN, None
    if waiting and waiting["kind"] == "chat_question":
        turn.keep_groups()
        return result, turn.outcome("done", answer=question_for_user(waiting["message"], turn.config))
    if waiting:
        waiting["call"] = {"name": name, "arguments": arguments}
        turn.keep_groups()
        return result, turn.outcome("waiting", waiting=waiting)
    return result, None


def typed_by_user(turn: AgentTurn) -> list[str]:
    typed = turn.state.get("typed")
    if typed is None:
        typed = [message_text(message) for message in turn.messages if message.get("role") == "user"]
    return [without_app_notes(text) for text in typed]


def own_request(turn: AgentTurn) -> str:
    typed = typed_by_user(turn)
    return typed[-1] if typed else ""


def run_app_action(turn: AgentTurn, call: dict, calls: list, name: str, arguments: dict) -> tuple[str | None, dict | None]:
    if turn.run.background:
        return app_actions.BACKGROUND_REFUSAL, None
    prepared = app_actions.prepare(name, arguments, typed_by_user(turn), turn.config)
    if isinstance(prepared, str):
        return prepared, None
    if holds_for_approval(turn, prepared.card, background_holds=False):
        return None, wait_for_approval(turn, prepared.card, call, calls)
    turn.chat_approved = None
    if turn.approvals and (done_before := spent_already(turn, prepared.card)):
        return None, done_before
    return app_actions.carry_out(name, prepared), None


def spent_already(turn: AgentTurn, card: str) -> dict | None:
    try:
        done_at = app_actions.spend_approval(app_actions.spent_key(card, turn.approval_key or ""))
    except OSError as error:
        log.warning("couldn't note an approval as used, so it wasn't carried out: %s", error)
        return turn.outcome("done", answer=app_actions.CANT_NOTE_APPROVAL)
    if done_at is None:
        return None
    log.warning("a yes that was already used was sent again; not carrying out the same card twice")
    return turn.outcome("done", answer=app_actions.already_done(done_at, turn.config))


def run_hub_call(turn: AgentTurn, call: dict, calls: list, name: str, arguments: dict) -> tuple[str | None, dict | None]:
    know_the_page(name, turn.context)
    reason = policy.approval_reason(name, arguments, turn.context, turn.rules)
    if reason and holds_for_approval(turn, reason):
        # Ask in Arty's own words rather than handing the model an instruction it might echo.
        return None, wait_for_approval(turn, reason, call, calls)
    if reason and turn.approvals and (done_before := spent_already(turn, reason)):
        return None, done_before
    snapshot_before_desktop_work(turn, name)
    result = shopping.fetch_tool_text(arguments) if name == docs_reading.FETCH_TOOL else connections.hub.call(name, arguments)
    policy.remember_tool_result(turn.context, name, result, turn.rules)
    if reason:
        turn.chat_approved = None
    return result, None


def snapshot_before_desktop_work(turn: AgentTurn, name: str):
    task, config = turn.run.task, turn.config
    if turn.run.background and task and name.startswith("workspace__") and not task.get("snapshot_taken") \
            and config.get("background", {}).get("snapshot_before_desktop_work") and "homelab__snapshot_desktop" in connections.hub.routes:
        log.info("task %s: snapshot before desktop work: %s", task["id"], connections.hub.call("homelab__snapshot_desktop", {"note": f"before task #{task['id']}: {task['title']}"}))
        task["snapshot_taken"] = 1
        store.update_task(task["id"], snapshot_taken=1)


def record_tool_result(turn: AgentTurn, name: str, arguments: dict, result: str):
    log.info("tool %s(%s) -> %d chars", name, json.dumps(arguments)[:200], len(result))
    if name != "use_tools" and TOOL_FAILED.search(result):
        turn.context["failed"].append((name, result.strip().splitlines()[0][:200] if result.strip() else "no result"))
        result += FAILED_NOTE
    turn.add({"role": "tool", "content": result, "tool_name": name})
    if turn.run.background and turn.run.task:
        save_progress(turn.run.task, turn.state, turn.requested)


HIDDEN_TOOL_RESULT = "That tool isn't available here."
REFUSED_STEPS_BEFORE_STOPPING = 2
HELD_ARGUMENTS = {"left_out": "these arguments held part of Arty's own private instructions"}
UNNAMED_TOOL = "tool"
ASK_AGAIN = " Ask me again and I'll do it without that part."
HELD_CALLS_ANSWER = ("I stopped there and did nothing: my {calls} call{plural} still included part of my own private "
                     "instructions, which I never write, send or run." + ASK_AGAIN)
HELD_CALLS_NOT_MADE = ("I stopped there without making my {calls} call{plural}: {held} still included part of my own "
                       "private instructions, which I never write, send or run.")
STEPS_THAT_WORKED = " Before that, these steps did run: {steps}."


agent_sessions = AgentSessions()
