import logging

import main_calls

log = logging.getLogger("brain")

JSON_TEMPERATURE = 0.2
CUT_OFF = "length"


def is_a_written_call(url: str, body) -> bool:
    return isinstance(body, dict) and main_calls.is_generation_call(url) and ("messages" in body or "prompt" in body)


def without_tools_beside_a_format(body: dict) -> dict:
    if body.get("format") is None or "tools" not in body:
        return body
    return {key: value for key, value in body.items() if key != "tools"}


def with_call_defaults(url: str, body):
    if not is_a_written_call(url, body):
        return body
    shaped = without_tools_beside_a_format(body if body.get("think") is not None else {**body, "think": False})
    options = shaped.get("options") or {}
    if shaped.get("format") is None or "temperature" in options:
        return shaped
    return {**shaped, "options": {**options, "temperature": JSON_TEMPERATURE}}


def ran_out_while_thinking(body: dict, reply) -> bool:
    message = reply.get("message") if isinstance(reply, dict) else None
    if not body.get("think") or not isinstance(message, dict) or reply.get("done_reason") != CUT_OFF:
        return False
    return not str(message.get("content") or "").strip() and not message.get("tool_calls")


def written_again_if_cut_off(body, send):
    reply = send(body)
    if not isinstance(body, dict) or not ran_out_while_thinking(body, reply):
        return reply
    log.warning("%s spent its whole reply on thinking; asking again without thinking", body.get("model"))
    return send({**body, "think": False})
