import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import abilities
import app_actions
import calendar_feed
import connections
import content_rules
import content_screen
import desktop_look
import email_watch
import greetings
import memory_tidy
import models
import personal
import phone
import policy
import quick
import research
import safety
import screen_review
import shopping
import said_to_them
import style
import undo
import weather

from archive import message_text, archive_records, is_from_a_remote_model
from settings import ARTY_ENGINE_URL
from native_tools import NATIVE_TOOLS
from reply_rules import (REMIND_REQUEST, CHAT_APP, reminder_from, DO_REQUEST, ADDRESSED_PROMPT, APP_NOTES,
    without_app_notes, user_words, VOICE_MODE, ON_A_PHONE_CALL)
from schedules import CANCEL_REMINDER, cancel_by_name, set_reminder
from memory_facts import REMEMBER_REQUEST, FORGET_REQUEST
from connections import complete_text, classify_text

log = logging.getLogger("brain")


ASKED_TO_DO = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please|can you|could you|would you|will you|i want you to|i need you to)\b",
                         re.IGNORECASE)


LOCAL_MODEL_NEEDED = "That needs your own things on this server, which a remote model can't reach: pick a local model for that."


def judging_config(config: dict) -> dict:
    return connections.load_config() if content_rules.is_remote(config.get("model", "")) else config


def needs_owner_data(said: str, client: str = "") -> bool:
    asks = (personal.QUIZ_START.search, TALKED_ABOUT.search, lambda text: phone_action(text, client), camera_action,
            REMIND_REQUEST.search, CANCEL_REMINDER.match, undo.is_request, quick.asks_for_ips, quick.calendar_add, quick.list_add,
            email_watch.is_request, email_watch.is_unread_request, REMEMBER_REQUEST.match, FORGET_REQUEST.match,
            memory_tidy.is_restore_request, desktop_look.GRANT.search, desktop_look.REVOKE.search, desktop_look.is_request,
            style.is_learn_request, style.is_new_draft)
    return any(ask(said) for ask in asks)


def addressed_to_arty(config, heard: str, conversation: list[dict]) -> bool:
    previous = next((message_text(m) for m in reversed(conversation) if m.get("role") == "assistant"), "") or "(nothing yet)"
    config = judging_config(config)
    try:
        reply = connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", timeout=60, body=models.for_runner(config, {
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 20},
            "messages": [{"role": "user", "content": ADDRESSED_PROMPT % (previous[-400:], heard[:400])}]}))
        return json.loads(reply["message"]["content"]).get("for_arty") is not False
    except Exception as error:
        log.warning("couldn't tell whether a voice message was for Arty: %s", error)
        return True


# "What did we talk about earlier / today / yesterday?"
TALKED_ABOUT = re.compile(r"\bwhat (did|have) (we|i) (talk|chat|ask|say)(ed)?( you)?( about)?\b|\bwhat were we (talking|chatting) about\b"
                          r"|\bwhat (did|have) (we|i) (get|got|been) up to\b|\bwhat (did|have) we (do|done|get done)\b", re.IGNORECASE)
THIS_WEEK = re.compile(r"\b(this|the past|the last) week\b", re.IGNORECASE)
RECAP_PROMPT = """You are Arty, an assistant. Below is what the user said to you {when} (USER) and what you replied (ARTY),
oldest first:

{turns}

Write 2 to 5 short bullet points for the user, saying what they asked about and anything you did or set up for
them, like "- You asked about the weather" or "- I set a reminder to stretch at 20:18". Only use what's above.
Reply with the bullet points only."""
RECAP_TURN_CHARS = 200
RECAP_MAX_TURNS = 30
RECAP_MAX_BULLETS = 5


def recap_conversations(config, said: str) -> str:
    """What was talked about today, yesterday or this week (as asked), from the archive, summarised."""
    yesterday = "yesterday" in said.lower()
    week = bool(THIS_WEEK.search(said))
    day = (datetime.now() - timedelta(days=1 if yesterday else 0)).date().isoformat()
    week_start = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    turns = []
    for path in sorted(Path(config["conversations_dir"]).glob("*.jsonl"))[-2:]:
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            in_range = record.get("time", "") >= week_start if week else record.get("time", "").startswith(day)
            if in_range and not is_from_a_remote_model(record) and record.get("user") and not record["user"].startswith("[background task"):
                turns.append(f"USER: {APP_NOTES.sub('', record['user'])[:RECAP_TURN_CHARS]}\nARTY: {str(record.get('assistant'))[:RECAP_TURN_CHARS]}")
    if not turns:
        return "We haven't talked this week." if week else "We didn't talk yesterday." if yesterday else "We haven't talked yet today."
    when = "this week" if week else "yesterday" if yesterday else "today"
    summary = complete_text(RECAP_PROMPT.format(when=when, turns="\n\n".join(turns[-RECAP_MAX_TURNS:]))).strip()
    # Only the bullet points: small models sometimes echo the instructions or add a lead-in first.
    bullets = []
    for line in summary.splitlines():
        if line.lstrip().startswith(("-", "*", "•")) and line.strip().lower() not in {b.strip().lower() for b in bullets}:
            bullets.append(line)
    bullets = [re.sub(r"^(\s*[-*•]\s*)(the )?user\b", r"\1You", line, flags=re.I) for line in bullets]
    return "\n".join(bullets[:RECAP_MAX_BULLETS]) if bullets else summary


WEATHER_QUESTION = re.compile(r"\b(weather|forecast|rain(ing|y)?|snow(ing)?|temperature|umbrella|sunny|windy)\b", re.I)
MAKING_TOOLS = {"schedule_task", "calendar_add", "list_add", "note_save", "deadline_add", "card_add"}
TURN_SECONDS = 15 * 60
TURN_SLACK_SECONDS = 5
NOTHING_TO_UNDO = ("The last thing I did wasn't setting or adding something (a reminder, event, list item, note, deadline or "
                   "card), so there's nothing for me to undo.")


def undo_last_turn(config) -> str:
    try:
        last = next(archive_records(config), None)
        finished = datetime.fromisoformat(last["time"]).timestamp() if last else None
    except (KeyError, ValueError, OSError):
        last, finished = None, None
    if last is not None and app_actions.ASKS_FIRST & set(last.get("tools") or []):
        card, nothing = app_actions.undo_card()
        return policy.approval_question(card) if card else nothing
    if last is None or finished is None or not MAKING_TOOLS & set(last.get("tools") or []):
        return NOTHING_TO_UNDO
    return undo.undo_last(made_between=(finished - TURN_SECONDS, finished + TURN_SLACK_SECONDS))


def text_for(told: dict) -> dict:
    action = {"kind": "text", "who": told["people"][0], "body": said_to_them.text_body(told)}
    return {**action, "others": told["people"][1:]} if len(told["people"]) > 1 else action


def phone_action(said: str, client: str = "") -> dict | None:
    if told := said_to_them.tell_request(said):
        return text_for(told) if told["by_text"] else None
    return None if quick.refers_back(said) else phone.from_request(said) or phone.device_request(said, client)


def is_spoken(client_system: str) -> bool:
    return VOICE_MODE in client_system or ON_A_PHONE_CALL in client_system


def said_aloud(told: dict, client: str, client_system: str, user_name: str) -> str:
    reply = said_to_them.addressed(told, user_name)
    if is_spoken(client_system) or client not in phone.PHONE_CLIENTS:
        return reply
    return f"{reply}\n\n{said_to_them.text_offer(told)}"


def camera_action(said: str) -> dict | None:
    return None if quick.refers_back(said) else phone.camera_request(said)


def asks_for_something_else(said: str) -> bool:
    return bool(ASKED_TO_DO.search(said) or REMIND_REQUEST.search(said) or phone.from_request(said) or said_to_them.tell_request(said)
                or undo.is_request(said) or quick.calendar_add(said) or quick.list_add(said) or TALKED_ABOUT.search(said))


def quick_action(said: str, after_a_question: bool = False) -> tuple[str, str] | None:
    """(reply, what was done) for an everyday request done directly by code (reminders, calendar, lists), or None.
    A small model mangles these tool calls and then says they worked; code does them reliably."""
    if not after_a_question and greetings.is_bare_greeting(said):
        return greetings.reply(datetime.now()), "greeting"
    if REMIND_REQUEST.search(said) and (parts := reminder_from(said)):
        return set_reminder(parts, bool(CHAT_APP.search(said))), "schedule_task"
    if (cancelled := cancel_by_name(said)) is not None:
        return cancelled, "cancel_scheduled"
    if undo.is_request(said):
        return undo_last_turn(connections.load_config()), "undo"
    if abilities.is_question(said):
        connections.hub.refresh()
        return abilities.describe(connections.load_config().get("tool_groups", {}), set(connections.hub.routes) | set(NATIVE_TOOLS)), "abilities"
    if worked_out := quick.calculate(said):
        return worked_out, "calculator"
    if WEATHER_QUESTION.search(said) and not DO_REQUEST.search(said) and (forecast := weather.answer(said, connections.load_config().get("weather"))):
        return forecast, "weather"
    if quick.asks_for_ips(said):
        connections.hub.refresh()
        if listed := quick.ip_list(connections.hub.call("homelab__proxmox_status", {})):
            return "Your servers' IP addresses:\n\n" + listed, "homelab__proxmox_status"
    if found := quick.calendar_add(said):
        return calendar_feed.add(*found), "calendar_add"
    if found := quick.list_add(said):
        return personal.list_add(found[1], found[0]), "list_add"
    if not REMIND_REQUEST.search(said) and not quick.MORE_THAN_ONE_THING.search(said) and (honest := quick.cant_do(said)):
        return honest, "cant_do"
    return None


QUIZ_JUDGE_PROMPT = """You mark a student's answer to a revision question. Be fair: accept answers that mean the same thing,
even if worded differently or missing minor detail. Reply with JSON only: {"correct": true or false, "feedback": "one short sentence"}.

Question: %s
Correct answer: %s
Student's answer: %s"""


def judge_answer(config, question: str, expected: str, given: str) -> tuple[bool, str]:
    config = judging_config(config)
    try:
        reply = connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body=models.for_runner(config, {
            "model": config["model"], "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 120},
            "messages": [{"role": "user", "content": QUIZ_JUDGE_PROMPT % (question, expected, given[:500])}]}))
        marked = json.loads(reply["message"]["content"])
        return bool(marked.get("correct")), str(marked.get("feedback") or "")[:200]
    except (OSError, ValueError, KeyError) as error:
        log.warning("quiz marking failed: %s", error)
        return False, "(I couldn't mark that one properly, so it'll come back soon.)"


def researched_answer(said: str, quick: bool) -> tuple[str, list[str]]:
    if quick:
        return research.run(said, shopping.with_pinned_fetch(connections.hub.call), complete_text, pages=research.QUICK_PAGES,
                            browser=False, classify=classify_text)
    return research.run(said, shopping.with_pinned_fetch(connections.hub.call), complete_text, classify=classify_text)


def solo_conversation(messages):
    conversation = []
    for message in messages:
        if message.get("role") in ("user", "assistant"):
            conversation.append({"role": message["role"], "content": without_app_notes(message_text(message))})
    return conversation


REMOTE_SOLO_REFUSAL = "Remote models only answer through Arty: pick it as an Arty model, not as a model on its own."


def solo_turn(model_name, messages, client=""):
    if content_rules.is_remote(model_name):
        return REMOTE_SOLO_REFUSAL, [], {}, None
    config = connections.load_config()
    said = user_words(message_text(next((m for m in reversed(messages) if m.get("role") == "user"), {})), client)
    earlier = content_screen.earlier_text(message_text(m) for m in messages[:-1] if m.get("role") in content_screen.GUARD_ROLES)
    if declined := safety.check(said, config.get("crisis_help"), content_rules.safety_checks(config, model_name), earlier) \
            or screen_review.declined(config, model_name, said, earlier):
        kind, reply = declined
        return reply, [f"safety_{kind}"], {}, None
    body = models.for_runner(config, {"model": model_name, "messages": solo_conversation(messages), "stream": False,
                                      "keep_alive": config.get("keep_alive", "10m")})
    reply = connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body=body)
    answer = ((reply.get("message") or {}).get("content") or "").strip()
    if declined_answer := content_screen.declined_reply(answer, content_screen.earlier_text([earlier, said]), said):
        return declined_answer, ["safety_minors"], {}, None
    return answer, [], reply, None
