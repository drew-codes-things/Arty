import json
import logging
import re

import code_sandbox
import connections
import models

from archive import message_text
from settings import ARTY_ENGINE_URL
from reply_rules import TOOL_FAILED, NOT_ACTIONS, LOOK_ONLY_TOOLS, HELD_CALL_RESULTS
from steering import LOOKUP_TOOLS
from memory_facts import prefix_for

log = logging.getLogger("brain")


CLAIM_CHECK_PROMPT = """You check an assistant's reply for honesty. The assistant can only do or change things through tools, and these are all the tools that ran while it wrote this reply:
%s

What the user said:
<<<
%s
>>>

The reply:
<<<
%s
>>>

Find every sentence where the reply says or implies the assistant did, changed, sent, set, saved, noted, made, started or booked something that no tool above did (or that a tool above tried but failed). Only actions count. Stating a fact is never a claim, even a fact about the user, their plans, their likes or what they said earlier ("Your favourite colour is blue.", "Your appointment is at 3pm.", "You said 5pm."), and neither is describing what it saw, answering, explaining, suggesting, offering or asking. Wording and language don't matter, only meaning.
Copy each such sentence exactly as it is in the reply. For each one write a short first-person correction in the reply's language that says what is actually true about that action (e.g. that it didn't do it, or that it was already like that), or "" when the sentence should simply go.
Answer only with JSON: {"false_claims": [{"text": "...", "correction": "..."}]}"""
CLAIM_CHECK_TOKENS = 400
CLAIM_CONFIRM_PROMPT = """The person said: "%s"
The assistant replied with this sentence: "%s"

Which is the sentence?
A: the assistant reporting that it carried something out, including saying that what the person asked for is now done
B: the assistant answering a question, giving information, or repeating what the person said

Answer only with JSON: {"kind": "A" or "B"}"""
CLAIM_CONFIRM_TOKENS = 20
CLAIM_CHECK_CHARS = 6000
JUDGE_SECONDS = 600
CLAIM_CHECK_REQUEST_CHARS = 2000
HONEST_NOTHING_DONE = ("I didn't actually do that: I don't have a way to do it from here, so nothing has changed. "
                       "If you tell me how you'd like it done, I'll help where I can.")


def tools_record(tools_used: list[str], failed: list[tuple[str, str]]) -> str:
    lines = []
    for tool in dict.fromkeys(tools_used):
        if tool in NOT_ACTIONS:
            continue
        runs = tools_used.count(tool)
        errors = [error for name, error in failed if name == tool]
        if not errors:
            lines.append(f"- {tool}: worked" + (f" ({runs} times)" if runs > 1 else ""))
        elif len(errors) >= runs:
            lines.append(f"- {tool}: failed ({errors[-1][:200]})")
        else:
            lines.append(f"- {tool}: ran {runs} times, {len(errors)} failed (last error: {errors[-1][:200]})")
    return "\n".join(lines) or "- none"


def was_held_back(result: str) -> bool:
    return result.lstrip().startswith(HELD_CALL_RESULTS)


def did_not_work(result: str) -> bool:
    return bool(TOOL_FAILED.search(result)) or was_held_back(result)


def tool_results(conversation: list[dict], whole_conversation: bool = False) -> list[dict]:
    last_request = max((index for index, message in enumerate(conversation) if message.get("role") == "user"), default=-1)
    counted_from = 0 if whole_conversation else last_request + 1
    return [message for message in conversation[counted_from:] if message.get("role") == "tool" and message.get("tool_name")]


def refused_a_step(conversation: list[dict]) -> bool:
    return any(was_held_back(message_text(message)) for message in tool_results(conversation))


def client_tool_runs(conversation: list[dict], by_last_result: bool = False,
                     whole_conversation: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    results = tool_results(conversation, whole_conversation)
    used = [message["tool_name"] for message in results]
    judged = {message["tool_name"]: message for message in results}.values() if by_last_result else results
    failed = [(message["tool_name"], message_text(message)[:300]) for message in judged if did_not_work(message_text(message))]
    return used, failed


LOST_TRAIN_OF_THOUGHT = "Sorry, I lost my train of thought there. Could you ask that again?"
LOST_TRACK_START = "Sorry, I lost track before I could answer"
LOST_TRACK_AFTER_STEPS = (LOST_TRACK_START + ", after {steps} on your computer. Have a look at where it got to, and tell "
                          "me what you'd like next.")


def empty_reply(conversation: list[dict]) -> str:
    steps = [message for message in tool_results(conversation) if not was_held_back(message_text(message))]
    if not steps:
        return LOST_TRAIN_OF_THOUGHT
    return LOST_TRACK_AFTER_STEPS.format(steps="1 step" if len(steps) == 1 else f"{len(steps)} steps")


def some_tool_worked(tools_used: list[str], failed: list[tuple[str, str]]) -> bool:
    failed_names = {name for name, _ in failed}
    return any(tool not in failed_names for tool in tools_used)


def ask_judge(prompt: str, max_tokens: int, model: str = "", keep_alive=None, prefix=None,
              seconds: float = JUDGE_SECONDS) -> dict:
    config = connections.load_config()
    judge_model = model or config["model"]
    judge_keep_alive = keep_alive if model else config.get("keep_alive", "10m")
    shared = prefix_for(judge_model, prefix)
    asked = {"model": judge_model, "stream": False, "format": "json", "think": False, "keep_alive": judge_keep_alive,
             "options": {"temperature": 0, "num_predict": max_tokens},
             "messages": [*(shared.get("messages") or []), {"role": "user", "content": prompt}]}
    if shared.get("tools"):
        asked["tools"] = shared["tools"]
    body = models.for_runner(config, asked)
    return json.loads(connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body=body, timeout=seconds)["message"]["content"])


def false_claims(answer: str, tools_used: list[str], failed: list[tuple[str, str]], request: str = "", model: str = "",
                 keep_alive=None, prefix=None) -> list[dict]:
    prompt = CLAIM_CHECK_PROMPT % (tools_record(tools_used, failed), request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)",
                                   answer[:CLAIM_CHECK_CHARS])
    try:
        found = ask_judge(prompt, CLAIM_CHECK_TOKENS, model, keep_alive, prefix).get("false_claims") or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim check failed, reply left as written: %s", error)
        return []
    candidates = [claim for claim in found if isinstance(claim, dict) and isinstance(claim.get("text"), str)
                  and claim["text"].strip() and claim["text"].strip() in answer]
    return [claim for claim in candidates if reports_an_action(request, claim["text"].strip(), model, keep_alive, prefix)]


def reports_an_action(request: str, sentence: str, model: str = "", keep_alive=None, prefix=None) -> bool:
    prompt = CLAIM_CONFIRM_PROMPT % (request[:CLAIM_CHECK_REQUEST_CHARS] or "(not given)", sentence)
    try:
        kind = ask_judge(prompt, CLAIM_CONFIRM_TOKENS, model, keep_alive, prefix).get("kind")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("claim confirmation failed, the sentence is treated as a claim: %s", error)
        return True
    return kind != "B"


def be_honest(answer: str, tools_used: list[str], failed: list[tuple[str, str]], already_sent: str = "",
              request: str = "", model: str = "", keep_alive=None, prefix=None, sandbox_line: str = "") -> str:
    failed_names = {name for name, _ in failed}
    acted = [tool for tool in tools_used if tool not in NOT_ACTIONS and tool not in LOOKUP_TOOLS and tool not in LOOK_ONLY_TOOLS]
    if acted and not failed_names & set(acted):
        return answer
    body, sandbox_line = code_sandbox.without_summary(answer, sandbox_line)
    pieces = prose_and_code(body)
    prose = "\n".join(text for text, is_code in pieces if not is_code)
    claims = [claim for claim in false_claims(body, tools_used, failed, request, model, keep_alive, prefix)
              if claim["text"].strip() in prose]
    if not claims:
        return answer
    log.warning("reply claimed %d thing(s) the tools didn't do: corrected %s", len(claims),
                "; ".join(repr(claim["text"].strip()[:120]) for claim in claims))
    afterwards = []
    for claim in claims:
        text, correction = claim["text"].strip(), str(claim.get("correction") or "").strip()
        if text in already_sent:
            afterwards.append(correction or HONEST_NOTHING_DONE)
        else:
            pieces = replaced_in_prose(pieces, text, correction)
    body = "".join(text if is_code else re.sub(r"[ \t]{2,}", " ", text) for text, is_code in pieces).strip()
    if afterwards:
        body = f"{body}\n\n{' '.join(afterwards)}".strip()
    body = body or HONEST_NOTHING_DONE
    return f"{body}\n\n{sandbox_line}" if sandbox_line else body


def prose_and_code(text: str) -> list[tuple[str, bool]]:
    pieces, position = [], 0
    for start, end in code_sandbox.code_spans(text):
        pieces += [(text[position:start], False), (text[start:end], True)]
        position = end
    return pieces + [(text[position:], False)]


def replaced_in_prose(pieces: list[tuple[str, bool]], text: str, correction: str) -> list[tuple[str, bool]]:
    for index, (piece, is_code) in enumerate(pieces):
        if not is_code and text in piece:
            return pieces[:index] + [(piece.replace(text, correction, 1), False)] + pieces[index + 1:]
    return pieces
