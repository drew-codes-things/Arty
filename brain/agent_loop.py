import json
import logging
import re
import urllib.error
from dataclasses import dataclass, field

import connections
import models
import policy
import sharing
import store
import style
import tool_guide
import window_fit
import written_calls

from agent_sessions import AgentSessions
from api_support import ApiError
from archive import message_text
from settings import OLLAMA_URL
from native_tools import NATIVE_TOOLS, BACKGROUND_ONLY_EXCLUDED
from reply_rules import (PROMPT_LEAK, FUMBLED_CALL, ACTION_REQUEST, CLAIMED, CLARIFYING, SEARCH_REQUEST, PROMISE,
    TOOL_TALK, DO_REQUEST, PRETEND_TOOLS, TOOL_FAILED, FAILED_NOTE, ANSWERED_ALREADY_CHARS, without_app_notes)
from steering import (LOOKUP_TEMPERATURE, WRITE_ONLY_REQUEST, RUN_REQUEST, RUN_TOOLS, WRITE_ONLY_NOTE,
    WRITE_AND_RUN_NOTE, READ_LINK_NOTE, asks_to_read_a_link, thinking_for, HOW_TO_NOTE, asks_how_to,
    steer_last_request, steer_away_from_repeats, with_thinking, with_context, parse_arguments, DESKTOP_TOOLS,
    NOTHING_TO_SIGN_IN, question_for_user)
from connections import TOOL_RESULT_CHARS
from prompting import system_prompt, with_tool_guide, current_context
from native_calls import run_native_safely
from honesty import client_tool_runs

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


def private_upload(name: str, arguments: dict) -> str | None:
    path = str(arguments.get("path") or "")
    if name != "workspace__browser_upload" or not sharing.is_private(path):
        return None
    return f"Refused: {path} is private (keys, passwords or a browser profile), so it is never uploaded anywhere."


def hold_for_approval(reason, call, calls, background, state, requested, add, tool_log, raw):
    function = call.get("function", {})
    name = function.get("name") or ""
    arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
    state["groups"] = sorted(requested)
    if background:
        return {"status": "waiting", "waiting": {"kind": "approval", "message": reason, "call": {"name": name, "arguments": arguments}},
                "tools_used": tool_log, "raw": raw}
    for unanswered in calls[calls.index(call):]:
        add({"role": "tool", "content": NOT_RUN_YET, "tool_name": unanswered.get("function", {}).get("name") or ""})
    return {"status": "done", "answer": policy.approval_question(reason), "tools_used": tool_log, "raw": raw}


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


WINDOW_OVERFLOW = re.compile(r"context (size|length|window)|exceeds the available context|longer than the context", re.IGNORECASE)
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


def ollama_error_text(error: Exception) -> str:
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


def model_reply(body: dict, config: dict, draft, risky, on_thinking, agent: bool) -> dict:
    try:
        if draft:
            return connections.stream_chat(body, lambda piece: draft.feed(piece, risky), on_thinking=on_thinking)
        return connections.http_json(f"{OLLAMA_URL}/api/chat", body=body)
    except (urllib.error.HTTPError, RuntimeError) as error:
        reason = ollama_error_text(error)
        if agent and WINDOW_OVERFLOW.search(reason):
            log.warning("an agent conversation did not fit %s's window: %s", config["model"], reason)
            raise WindowOverflow(config["model"], models.context_tokens(config, config["model"])) from None
        raise


def written_reply(body: dict) -> str:
    try:
        shown = connections.http_json(f"{OLLAMA_URL}/api/chat", body={**body, "stream": False, "_debug_render_only": True})
        replay = {"model": body["model"], "prompt": shown["_debug_info"]["rendered_template"], "raw": True, "stream": False,
                  "options": body.get("options") or {}}
        replay.update({key: body[key] for key in ("keep_alive", "shift") if key in body})
        return connections.http_json(f"{OLLAMA_URL}/api/generate", body=replay).get("response") or ""
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError) as error:
        log.warning("couldn't replay an empty model reply to see what it wrote: %s", error)
        return ""


def fitted_to_window(config: dict, body: dict) -> dict:
    fitted, left_out = window_fit.fit_to_window(config, body)
    if left_out:
        log.info("left out %d earlier messages so the prompt and a %d-token reply fit %s's window", left_out,
                 window_fit.reply_room(body), config["model"])
    return fitted


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
    replays: int = 0
    searched_for_it: bool = False
    answer_from_lookups: bool = False

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


def run_agent(config, state, run: AgentRun = AgentRun()):
    """The tool loop. state = {"messages": [...], "context": {...}, "groups": [...]} and is updated in place.
    Returns {"status": "done", "answer": ...} or {"status": "waiting", "waiting": {...}}.
    With a draft (drafts.Draft), the answer's safe sentences go out while the model writes."""
    turn = start_turn(config, state, run)
    for round_number in range(turn.max_rounds):
        if is_cancelled(run.task):
            return turn.outcome("cancelled")
        outcome = agent_round(turn, round_number)
        if outcome is not None:
            return outcome
    turn.keep_groups()
    return turn.outcome("done", answer="I stopped after too many tool steps. Tell me how you'd like me to continue.")


def start_turn(config: dict, state: dict, run: AgentRun) -> AgentTurn:
    connections.hub.refresh()
    config = {**config, "tool_budget": models.budget_for(config, config["model"])}
    messages = state["messages"]
    context = state.setdefault("context", {})
    requested = set(state.setdefault("groups", []))
    model_info = models.find(config["model"])
    toolless = bool(model_info) and not model_info["tools"]
    instructions = run.client_system if run.agent and run.client_system else system_prompt(config, run.client_system,
                                                                                           run.background, toolless)
    history = [{"role": "system", "content": instructions}] + messages
    looked_up = run.looked_up or ([], "", None)
    add_turn_notes(config, run, history, messages, looked_up)
    all_tools = tools_for_turn(config, run, toolless, looked_up)
    request_text = " ".join(message_text(m) for m in messages if m.get("role") == "user")[-2000:]
    rules = config.get("approval", {})
    steps_this_turn = client_tool_runs(messages)[0]
    policy.remember_client_results(context, steps_this_turn, rules)
    context["failed"] = []  # tools that didn't work this turn (see be_honest)
    last_request = newest_request(messages)
    user_texts = [without_app_notes(message_text(m)) for m in messages if m.get("role") == "user"]
    turn = AgentTurn(
        config=config, state=state, run=run, messages=messages, context=context, requested=requested, toolless=toolless,
        instructions=instructions, history=history, all_tools=all_tools, hidden=config.get("hidden_tools") or [],
        request_text=request_text, rules=rules, max_rounds=max_tool_rounds(config, run.background),
        tool_log=list(looked_up[0]), steps_this_turn=steps_this_turn,
        client_names={tool["function"]["name"] for tool in run.client_tools or []}, last_request=last_request,
        think=thinking_for(config, agent_sessions.deciding_request(user_texts) if run.agent else last_request),
        shown_thinking=run.on_thinking if run.agent and run.client_system else None, chat_approved=run.chat_approved)
    steer_the_request(turn)
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
    voice = style.voice_note() if style.is_writing_request(request) else ""
    if not run.agent:
        with_context(history, current_context(config, request) + looked_up[1] + voice)
        steer_away_from_repeats(history, messages, request, run.background)


def tools_for_turn(config: dict, run: AgentRun, toolless: bool, looked_up: tuple) -> list:
    native = [models.handoff_tool(config, definition) if name == "ask_other_model" else definition
              for name, definition in NATIVE_TOOLS.items() if not (run.background and name in BACKGROUND_ONLY_EXCLUDED)]
    all_tools = [] if toolless or run.agent else connections.hub.tools + native
    hidden = config.get("hidden_tools") or []
    all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], hidden)]
    if run.skip_groups:
        # The calling app does these itself (e.g. the desktop app drives the user's own browser).
        skipped = [pattern for group in run.skip_groups for pattern in config.get("tool_groups", {}).get(group, {}).get("tools", [])]
        all_tools = [t for t in all_tools if not policy.matches(t["function"]["name"], skipped)]
    if looked_up[0]:
        all_tools = [t for t in all_tools if t["function"]["name"] not in looked_up[0]]  # its result is already in the notes
    return all_tools


def steer_the_request(turn: AgentTurn):
    last_request = turn.last_request
    if turn.is_chat and asks_how_to(last_request):
        turn.all_tools = without_run_tools(turn.all_tools)
        steer_last_request(turn.history, HOW_TO_NOTE)
    elif turn.is_chat and WRITE_ONLY_REQUEST.search(last_request):
        if RUN_REQUEST.search(last_request):
            steer_last_request(turn.history, WRITE_AND_RUN_NOTE)
        else:
            turn.all_tools = without_run_tools(turn.all_tools)
            steer_last_request(turn.history, WRITE_ONLY_NOTE)
    link_reader = written_calls.offered_name("fetch", [tool["function"]["name"] for tool in turn.all_tools])
    if turn.is_chat and link_reader and asks_to_read_a_link(last_request):
        steer_last_request(turn.history, READ_LINK_NOTE.format(tool=link_reader))


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
    turn.raw = model_reply(body, turn.config, turn.run.draft, risky, turn.shown_thinking, turn.run.agent)
    log_model_timings(turn.raw)
    if turn.run.agent:
        turn.state.setdefault("rounds", []).append(round_timings(turn.config["model"], turn.raw, body.get("think")))
    message = turn.raw["message"]
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
    if turn.run.client_tools and not turn.toolless:
        tools = tools + turn.run.client_tools
    if turn.answer_from_lookups and round_number == 0:
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
    if tools:  # Ollama rejects a tools list for models without tool support
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
    draft.start_round(allowed=not (may_nudge and ACTION_REQUEST.search(turn.last_request))
                      and not (may_search and SEARCH_REQUEST.search(turn.last_request)))

    def risky(text, may_nudge=may_nudge, may_search=may_search):
        if agent:
            return bool(PROMPT_LEAK.search(text)) or written_calls.has_call_markup(text)
        if PROMPT_LEAK.search(text) or TOOL_TALK.search(text) or written_calls.has_call_markup(text):
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
        log.info("Ollama dropped the model's call to %s; using the call it wrote", ", ".join(renamed))
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
        search_for_the_model(turn, answer)
        return None
    if claims_without_tools(turn, tools, answer):
        nudge_to_use_tools(turn, tools, answer)
        return None
    if turn.is_chat and not turn.nudged and PRETEND_TOOLS.search(answer):
        nudge_away_from_pretend_tools(turn, answer)
        return None
    if not turn.nudged and tools and answer.count("```") % 2 == 1 and FUMBLED_CALL.search(answer):
        nudge_to_finish_the_call(turn, answer)
        return None
    if not answer.strip() and not turn.nudged:
        nudge_after_an_empty_reply(turn)
        return None
    if turn.raw.get("done_reason") == "length":
        answer += cut_short_note(turn.config, turn.raw, turn.run.agent)
    turn.keep_groups()
    return turn.outcome("done", answer=answer)


def wait_for_sign_in(turn: AgentTurn) -> dict:
    # Small models tend to report "please sign in" and stop; pause and ask properly instead.
    host = turn.context.pop("blocked")
    turn.keep_groups()
    return turn.outcome("waiting", waiting={"kind": "user", "message": f"Please sign in (or pass the bot check) on {host} in the browser on my desktop.",
                                            "call": {"name": "ask_user", "arguments": {}}})


def should_search_for_the_model(turn: AgentTurn, answer: str) -> bool:
    return bool(turn.is_chat and turn.nudged and turn.used_no_tools() and not turn.searched_for_it
                and "research__web_search" in connections.hub.routes
                and (CLARIFYING.search(answer) or "web_search" in answer or SEARCH_REQUEST.search(turn.last_request)))


def search_for_the_model(turn: AgentTurn, answer: str):
    # Sent back once and still no search: run it ourselves and have the model answer from the results.
    turn.searched_for_it = True
    results = connections.hub.call("research__web_search", {"query": turn.last_request[:200]})
    turn.tool_log.append("research__web_search")
    log.info("searched for the model: %s", turn.last_request[:80])
    send_back(turn, answer, f"(Web search results for my message:\n{results[:TOOL_RESULT_CHARS]}\n\n"
              "Now answer my message from these results, in your own words, briefly. Say which source if it helps.)")


def claims_without_tools(turn: AgentTurn, tools: list, answer: str) -> bool:
    return bool(turn.is_chat and not turn.nudged and tools and turn.used_no_tools()
                and (PROMISE.search(answer) or CLAIMED.search(answer) or CLARIFYING.search(answer)
                     or ACTION_REQUEST.search(turn.last_request)))


def nudge_to_use_tools(turn: AgentTurn, tools: list, answer: str):
    # Small models describe doing things ("I've set a reminder", "here are my findings") without
    # calling a tool; send them back once to actually do it.
    turn.nudged = True
    send_back(turn, answer, "(You haven't used any tools, so none of that has actually happened. "
              f"{tool_guide.nudge_tools(tool_guide.offered_names(tools))} Only then answer, and only with what "
              "the tools gave you. If none of your tools can do it, say so plainly in a sentence instead: never "
              "pretend it's done.)")


def nudge_away_from_pretend_tools(turn: AgentTurn, answer: str):
    turn.nudged = True
    log.info("reply played at using tools; sent back once")
    send_back(turn, answer, "(Don't write out tool calls, function names or made-up tool results. "
              "Answer my message in plain words, from your notes if they cover it.)")


def nudge_to_finish_the_call(turn: AgentTurn, answer: str):
    # It started writing a tool call as text and stopped: have it make the call properly.
    turn.nudged = True
    log.info("reply ended in a half-written tool call; sent back once")
    send_back(turn, answer, "(Your reply stopped at the start of a code block instead of using a tool. "
              "If you need a tool, call it properly now; otherwise answer my message.)")


def nudge_after_an_empty_reply(turn: AgentTurn):
    # Small models occasionally return nothing at all; one nudge almost always gets a real answer.
    turn.nudged = True
    turn.history.append({"role": "user", "content": "(Your reply was empty. Answer my last message, using your tools if needed.)"})


def act_on_calls(turn: AgentTurn, message: dict, calls: list) -> dict | None:
    written = written_calls.without_call_markup(message.get("content") or "").strip()
    if answered_before_unlocking(turn, calls, written):
        # It answered a plain question, then asked for tools it doesn't need (the next round would only
        # chat about the unlocked tools): keep the answer. Not when it's about to actually do something.
        turn.keep_groups()
        return turn.outcome("done", answer=written)
    if turn.run.draft:
        turn.run.draft.before_tools()
    client_calls = calls if turn.run.agent else [call for call in calls if call.get("function", {}).get("name") in turn.client_names]
    if client_calls:
        turn.keep_groups()
        return {"status": "client_tools", "calls": client_calls,
                "content": written_calls.without_call_markup(message.get("content") or ""),
                "tools_used": turn.tool_log + [c["function"]["name"] for c in client_calls], "raw": turn.raw}
    turn.add({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
    for call in calls:
        outcome = run_tool_call(turn, call, calls)
        if outcome is not None:
            return outcome
    return None


def answered_before_unlocking(turn: AgentTurn, calls: list, written: str) -> bool:
    return bool(turn.is_chat and all(call.get("function", {}).get("name") == "use_tools" for call in calls)
                and len(written) >= ANSWERED_ALREADY_CHARS and not ACTION_REQUEST.search(turn.last_request)
                and not (PROMISE.search(written) or CLAIMED.search(written)))


def run_tool_call(turn: AgentTurn, call: dict, calls: list) -> dict | None:
    function = call.get("function", {})
    name = function.get("name") or ""
    arguments = repair_arguments(name, parse_arguments(function.get("arguments")))
    turn.tool_log.append(name)
    if name == "use_tools":
        result = unlock_groups(turn, arguments)
    elif policy.matches(name, turn.hidden):
        result = HIDDEN_TOOL_RESULT
    elif name in NATIVE_TOOLS and (held := policy.untrusted_reason(name, arguments, turn.context, turn.rules)) \
            and not policy.covers(turn.chat_approved, held):
        return wait_for_approval(turn, held, call, calls)
    elif name in NATIVE_TOOLS:
        result, outcome = run_native_call(turn, name, arguments)
        if outcome is not None:
            return outcome
    elif refused := private_upload(name, arguments):
        result = refused
    else:
        result, outcome = run_hub_call(turn, call, calls, name, arguments)
        if outcome is not None:
            return outcome
    record_tool_result(turn, name, arguments, result)
    return None


def unlock_groups(turn: AgentTurn, arguments: dict) -> str:
    wanted = [g for g in arguments.get("groups") or [] if g in turn.config.get("tool_groups", {})]
    turn.requested.update(wanted)
    return f"Unlocked: {', '.join(wanted) or 'nothing (unknown group)'}. Those tools are available from your next step."


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


def run_hub_call(turn: AgentTurn, call: dict, calls: list, name: str, arguments: dict) -> tuple[str | None, dict | None]:
    know_the_page(name, turn.context)
    reason = policy.approval_reason(name, arguments, turn.context, turn.rules)
    if reason and (turn.run.background or not policy.covers(turn.chat_approved, reason)):
        # Ask in Arty's own words rather than handing the model an instruction it might echo.
        return None, wait_for_approval(turn, reason, call, calls)
    snapshot_before_desktop_work(turn, name)
    result = connections.hub.call(name, arguments)
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


agent_sessions = AgentSessions()
