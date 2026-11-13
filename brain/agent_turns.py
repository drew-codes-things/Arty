import logging

import connections
import desktop_look
import drafts
import model_choice
import safety

from archive import message_text
from reply_rules import user_words
from replies import as_written, never_an_offer
from model_choice import AGENT_QUIET_SECONDS, use_requested_model, agent_model
from attachments import expand_attachments, to_ollama_messages
from agent_loop import ROUNDS_KEY, AgentRun, run_agent
from honesty import client_tool_runs, some_tool_worked, be_honest

log = logging.getLogger("brain")


def agent_turn(messages, requested_model, client_tools, on_text, any_installed, on_thinking=None, client=""):
    config = connections.load_config()
    main_config = dict(config)
    holds_another_model = use_requested_model(config, agent_model(config, requested_model), any_installed, session=True)
    try:
        return agent_reply(config, messages, client_tools, on_text, on_thinking, client)
    finally:
        if holds_another_model:
            model_choice.main_model_return.release(main_config, AGENT_QUIET_SECONDS)


def agent_reply(config, messages, client_tools, on_text, on_thinking, client=""):
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    said = user_words(message_text(last_user), client) if last_user is not None else ""
    if last_user is not None:
        desktop_look.user_active()
        if declined := safety.check(said, config.get("crisis_help")):
            kind, reply = declined
            log.info("declined a %s request from an agent", kind)
            return reply, [f"safety_{kind}"], {}, None
    messages, _ = expand_attachments(messages)
    conversation = to_ollama_messages([m for m in messages if m.get("role") != "system"])
    client_system = "\n".join(message_text(m) for m in messages if m.get("role") == "system").strip()
    state = {"messages": conversation, "context": {}, "groups": []}
    draft = drafts.Draft(on_text, as_written, never_an_offer) if on_text else None
    connections.hub.refresh()
    outcome = run_agent(config, state, run=AgentRun(client_system=client_system, client_tools=client_tools, draft=draft,
                                                    agent=True, on_thinking=on_thinking))
    outcome["raw"] = {**(outcome.get("raw") or {}), ROUNDS_KEY: state.get("rounds", [])}
    if outcome["status"] == "client_tools":
        content = draft.finish(outcome["content"]) if draft else outcome["content"]
        return content, outcome["tools_used"], outcome["raw"], outcome["calls"]
    if outcome["status"] == "waiting":
        outcome = {"answer": outcome["waiting"]["message"], "tools_used": outcome["tools_used"], "raw": outcome["raw"]}
    answer = outcome.get("answer") or ""
    client_used, client_failed = client_tool_runs(conversation, by_last_result=True, whole_conversation=True)
    if not some_tool_worked(client_used, client_failed):
        answer = be_honest(answer, client_used + outcome["tools_used"], client_failed, draft.everything() if draft else "",
                           said, config["model"], config.get("keep_alive"))
    if draft:
        answer = draft.finish(answer)
    return answer, outcome["tools_used"], outcome["raw"], None
