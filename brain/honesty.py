import json
import logging
import re

import connections
import models

from archive import message_text
from settings import OLLAMA_URL
from reply_rules import TOOL_FAILED, NOT_ACTIONS, LOOK_ONLY_TOOLS
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


def client_tool_runs(conversation: list[dict], by_last_result: bool = False,
                     whole_conversation: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    last_request = max((index for index, message in enumerate(conversation) if message.get("role") == "user"), default=-1)
    counted_from = 0 if whole_conversation else last_request + 1
    results = [message for message in conversation[counted_from:] if message.get("role") == "tool" and message.get("tool_name")]
    used = [message["tool_name"] for message in results]
    judged = {message["tool_name"]: message for message in results}.values() if by_last_result else results
    failed = [(message["tool_name"], message_text(message)[:300]) for message in judged if TOOL_FAILED.search(message_text(message))]
    return used, failed


LOST_TRAIN_OF_THOUGHT = "Sorry, I lost my train of thought there. Could you ask that again?"
LOST_TRACK_START = "Sorry, I lost track before I could answer"
LOST_TRACK_AFTER_STEPS = (LOST_TRACK_START + ", after {steps} on your computer. Have a look at where it got to, and tell "
                          "me what you'd like next.")


def empty_reply(conversation: list[dict]) -> str:
    steps, _failed = client_tool_runs(conversation)
    if not steps:
        return LOST_TRAIN_OF_THOUGHT
    return LOST_TRACK_AFTER_STEPS.format(steps="1 step" if len(steps) == 1 else f"{len(steps)} steps")


def some_tool_worked(tools_used: list[str], failed: list[tuple[str, str]]) -> bool:
    failed_names = {name for name, _ in failed}
    return any(tool not in failed_names for tool in tools_used)


def ask_judge(prompt: str, max_tokens: int, model: str = "", keep_alive=None, prefix=None) -> dict:
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
    return json.loads(connections.http_json(f"{OLLAMA_URL}/api/chat", body=body)["message"]["content"])


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
              request: str = "", model: str = "", keep_alive=None, prefix=None) -> str:
    failed_names = {name for name, _ in failed}
    acted = [tool for tool in tools_used if tool not in NOT_ACTIONS and tool not in LOOKUP_TOOLS and tool not in LOOK_ONLY_TOOLS]
    if acted and not failed_names & set(acted):
        return answer
    claims = false_claims(answer, tools_used, failed, request, model, keep_alive, prefix)
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
            answer = answer.replace(text, correction, 1)
    answer = re.sub(r"[ \t]{2,}", " ", answer).strip()
    if afterwards:
        answer = f"{answer}\n\n{' '.join(afterwards)}".strip()
    return answer or HONEST_NOTHING_DONE
