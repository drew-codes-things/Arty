import logging

import attachments
import connections
import content_rules
import content_screen
import desktop_look
import drafts
import hidden_notes
import model_choice
import policy
import safety
import screen_review

from archive import message_text
from reply_rules import user_words
from replies import as_written, never_an_offer, without_em_dashes
from model_choice import AGENT_QUIET_SECONDS, use_requested_model, agent_model
from attachments import expand_attachments, to_engine_messages, typed_texts
from agent_loop import ROUNDS_KEY, AgentRun, run_agent
from honesty import client_tool_runs, some_tool_worked, refused_a_step, be_honest

log = logging.getLogger("brain")


def unanswered_user_words(messages: list, client: str) -> str:
    last_reply = next((index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "assistant"), -1)
    asked = [user_words(message_text(message), client) for message in reversed(messages[last_reply + 1:])
             if message.get("role") == "user"]
    return "\n".join(text for text in asked if text)


def agent_turn(messages, requested_model, client_tools, on_text, any_installed, on_thinking=None, client=""):
    config = connections.load_config()
    main_config = dict(config)
    turn_model = agent_model(config, requested_model)
    holds_another_model = use_requested_model(config, turn_model, any_installed, session=True,
                                              by_hand=turn_model == requested_model)
    attachments.turn_runs_on(config["model"])
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
        earlier = content_screen.earlier_text(message_text(m) for m in messages
                                           if m is not last_user and m.get("role") in content_screen.GUARD_ROLES)
        asked = unanswered_user_words(messages, client)
        if declined := safety.check(asked, config.get("crisis_help"), content_rules.safety_checks(config, config["model"]), earlier) \
                or screen_review.declined(config, config["model"], asked, earlier):
            kind, reply = declined
            log.info("declined a %s request from an agent", kind)
            return reply, [f"safety_{kind}"], {}, None
    typed = typed_texts(messages)
    messages, attached = expand_attachments(messages)
    conversation = to_engine_messages([m for m in messages if m.get("role") != "system"])
    client_system = "\n".join(message_text(m) for m in messages if m.get("role") == "system").strip()
    state = {"messages": conversation, "context": policy.starting_context(attached), "groups": ["files"] if attached else [],
             "typed": typed}
    tidy = without_em_dashes if content_rules.is_remote(config["model"]) else as_written
    draft = drafts.Draft(on_text, tidy, never_an_offer) if on_text else None
    connections.hub.refresh()
    outcome = run_agent(config, state, run=AgentRun(client_system=client_system, client_tools=client_tools, draft=draft,
                                                    agent=True, on_thinking=on_thinking,
                                                    client_texts=hidden_notes.known_to_the_client(messages, client_tools)))
    outcome["raw"] = {**(outcome.get("raw") or {}), ROUNDS_KEY: state.get("rounds", [])}
    earlier = content_screen.earlier_text(message_text(m) for m in content_screen.system_messages(messages) + conversation)
    if outcome["status"] == "client_tools":
        if content_screen.calls_cross_the_line(outcome["content"], outcome["calls"], earlier, said):
            log.info("held back agent tool calls that broke the minors rule")
            return content_screen.MINORS_REPLY, outcome["tools_used"] + ["safety_minors"], outcome["raw"], None
        content = tidy(outcome["content"])
        content = draft.finish(content) if draft else content
        return content, outcome["tools_used"], outcome["raw"], outcome["calls"]
    if outcome["status"] == "waiting":
        outcome = {"answer": outcome["waiting"]["message"], "tools_used": outcome["tools_used"], "raw": outcome["raw"]}
    answer = outcome.get("answer") or ""
    client_used, client_failed = client_tool_runs(conversation, by_last_result=True, whole_conversation=True)
    if refused_a_step(conversation) or not some_tool_worked(client_used, client_failed):
        answer = be_honest(answer, client_used + outcome["tools_used"], client_failed, draft.everything() if draft else "",
                           said, config["model"], config.get("keep_alive"), None, outcome.get("sandbox_line", ""))
    answer = tidy(answer)
    if declined_answer := content_screen.declined_reply(answer, earlier, said):
        log.info("held back an agent reply that broke the minors rule")
        answer = declined_answer
        outcome["tools_used"].append("safety_minors")
    if draft:
        answer = draft.finish(answer)
    return answer, outcome["tools_used"], outcome["raw"], None
