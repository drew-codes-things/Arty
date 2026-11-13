import logging
import re
import sqlite3
import urllib.error
from datetime import datetime

import calendar_feed
import connections
import docs_reading
import media
import models
import notify
import personal
import reckon
import research
import safety
import sharing
import store
import weather

from api_support import optional_int
from archive import archive_records, recall
from native_tools import NATIVE_TOOLS
from reply_rules import DO_REQUEST, APP_NOTES
from schedules import cancel_active, create_schedule
from steering import (LOOKUP_TOOLS, MESSAGE_PLACEHOLDER, QUERY_PADDING, MAX_LOOKUPS, LOOKUP_MAX_MESSAGE_CHARS,
    LOOKUP_CHARS, LOOKUP_FAILED, sign_in_help)
from connections import TOOL_RESULT_CHARS
from prompting import local_zone
from model_choice import session_keep_alive

log = logging.getLogger("brain")
WEB_SEARCH_TOOL = "research__web_search"


NATIVE_TOOL_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error, urllib.error.URLError)


def with_home_zone(name: str, arguments: dict, config) -> dict:
    if name != "place_time" or arguments.get("home"):
        return arguments
    return {**arguments, "home": local_zone(config).key}


def found_no_place(name: str, result) -> bool:
    return name == "place_time" and reckon.NO_PLACE_FOUND in str(result)


def look_up_first(config, said: str) -> tuple[list[str], str, str | None]:
    """Runs the read-only tools a message plainly needs ("what are the IPs", "will it rain") before the
    model answers, so a small model only has to put the result into words instead of picking among dozens
    of tools (which it often gets wrong). Rules: "lookups" in brain.json. Returns (tool names, notes text,
    and a reply to give instead when a lookup hit a sign-in wall)."""
    used, results, blocked, failed = [], [], None, []
    if len(said) > LOOKUP_MAX_MESSAGE_CHARS:
        return [], "", None
    for rule in config.get("lookups", []):
        name = rule.get("tool", "")
        if len(used) >= MAX_LOOKUPS or name in used or name not in LOOKUP_TOOLS:
            continue
        if rule.get("skip_for_changes") and DO_REQUEST.search(said):
            continue
        if name == "weather" and weather.not_a_home_forecast(said, config.get("weather")):
            continue

        if not re.search(rule.get("pattern", "(?!)"), said, re.I):
            continue
        query = QUERY_PADDING.sub("", said).strip(" ?.!")[:200]
        arguments = {key: query if value == MESSAGE_PLACEHOLDER else value for key, value in (rule.get("arguments") or {}).items()}
        try:
            result = run_native(name, arguments, config, False, None)[0] if name in NATIVE_TOOLS else connections.hub.call(name, arguments)
        except Exception as error:  # a failed lookup just leaves the model to use its tools
            log.warning("lookup %s failed: %s", name, error)
            continue
        if str(result).startswith("BLOCKED:"):
            # Small models turn this into vague advice; say exactly what's wrong and what to do.
            blocked = sign_in_help(config) if name.startswith("workspace__gmail") else str(result).split(". Do not")[0]
            continue
        if found_no_place(name, result):
            log.info("lookup %s found no place in the message, so it is left to the model", name)
            continue
        if name == WEB_SEARCH_TOOL:
            result = research.relevant_results(str(result), said)
        if LOOKUP_FAILED.search(str(result)) or (name == WEB_SEARCH_TOOL and research.found_nothing(result)):
            # Left to the model, a failed check becomes "nothing new": say it couldn't be checked, and why.
            log.warning("lookup %s didn't work: %s", name, str(result)[:200])
            failed.append(str(result).strip())
            continue
        used.append(name)
        results.append(f"[{name}]\n{str(result)[:LOOKUP_CHARS]}")
        log.info("looked up %s before answering", name)
        if name == WEB_SEARCH_TOOL and (page := top_page_for(str(result), said)):
            used.append(docs_reading.FETCH_TOOL)
            results.append(page)
    if blocked and not results:
        return [], "", blocked
    if not results and not failed:
        return [], "", None
    results += [f"[couldn't be checked; if the user asked about this, tell them it didn't work and why]\n{reason}" for reason in failed]
    return used, ("\n\nAlready looked up for this message. Answer the message from this, and only this, unless it "
                  "asks for something else (don't call these tools again):\n"
                  + "\n\n".join(results)), blocked


def top_page_for(search_text: str, said: str) -> str:
    try:
        return docs_reading.read_top_page(search_text, said)
    except Exception as error:
        log.warning("couldn't read the top page for %r: %r", said[:80], error)
        return ""


def declined_reply(config: dict, *texts: str) -> str | None:
    declined = safety.check_each(texts, config.get("crisis_help"))
    if declined is None:
        return None
    log.info("declined a %s request in a task or handoff", declined[0])
    return declined[1]


def weather_for(arguments: dict, home: dict | None) -> str:
    asked = str(arguments.get("place") or "").strip()
    place = home if not asked or weather.is_home(asked, home) else weather.find_place(asked)
    if place is None:
        return f"Couldn't find a place called {asked}, so there's no forecast for it."
    return weather.forecast(arguments.get("days", 1), place)


def run_native(name, arguments, config, background, task):
    """Run one of the brain's own tools. Returns (result text, waiting dict or None)."""
    if name == "recall_conversations":
        return recall(config, arguments.get("query", ""), arguments.get("limit", 5)), None
    if name == "ask_user":
        message = str(arguments.get("message", "")).strip() or "Arty needs your help"
        # Chat ends the turn and shows the question itself; background pauses the task.
        return "", {"kind": "user" if background else "chat_question", "message": message}
    if name == "notify_user":
        return notify.send(str(arguments.get("title") or "Arty"), str(arguments.get("message", "")), category=notify.OTHER), None
    if name == "start_background_task":
        instructions = str(arguments.get("instructions", "")).strip()
        if not instructions:
            return "Give the task's instructions.", None
        title = str(arguments.get("title") or instructions[:60])
        if declined := declined_reply(config, title, instructions):
            return declined, None
        created = store.create_task(title, instructions, "chat")
        return f"Started background task #{created['id']}. You'll get a notification when it's done.", None
    if name == "schedule_task":
        return create_schedule(arguments, config.get("crisis_help"))[1], None
    if name == "list_scheduled":
        rows = store.list_schedules()
        if not rows:
            return "Nothing is scheduled.", None
        return "\n".join(f"#{s['id']} {s['title']}: {s['description']}, next {datetime.fromtimestamp(s['next_run']):%a %d %b %H:%M}" for s in rows if s["next_run"]), None
    if name == "cancel_scheduled":
        return cancel_active(optional_int(arguments.get("schedule_id")) or 0), None
    if name == "list_tasks":
        rows = store.list_tasks(limit=10)
        if not rows:
            return "No background tasks yet.", None
        return "\n".join(f"#{t['id']} [{t['status']}] {t['title']}" + (f": {t['result'][:200]}" if t["result"] else "") for t in rows), None
    if name == "list_models":
        return models.list_text(config["model"]), None
    if name == "ask_other_model":
        prompt = str(arguments.get("prompt", ""))
        if declined := declined_reply(config, prompt):
            return declined, None
        wanted = str(arguments.get("model", ""))
        return models.ask(wanted, prompt, config, session_keep_alive(wanted, 0)), None
    if name in personal.TOOLS:
        return personal.TOOLS[name][0](arguments), None
    if name in reckon.TOOLS:
        return reckon.TOOLS[name][0](with_home_zone(name, arguments, config)), None
    if name in sharing.TOOLS:
        return sharing.TOOLS[name][0](arguments), None
    if name == "weather":
        return weather_for(arguments, config.get("weather")), None
    if name == "week_recap":
        return week_recap(config), None
    if name == "calendar_events":
        return calendar_feed.events(arguments.get("days", 1)), None
    if name == "calendar_add":
        return calendar_feed.add(arguments.get("title"), arguments.get("when"), arguments.get("duration_minutes"),
                                 arguments.get("location", ""), arguments.get("notes", "")), None
    if name == "calendar_change":
        return calendar_feed.change(arguments.get("event_id"), arguments.get("title"), arguments.get("when"),
                                    arguments.get("duration_minutes"), arguments.get("location")), None
    if name == "calendar_remove":
        return calendar_feed.remove(arguments.get("event_id")), None
    if name == "convert_file":
        try:
            made = media.convert(str(arguments.get("file_id", "")), str(arguments.get("format", "")),
                                 optional_int(arguments.get("width")), optional_int(arguments.get("height")), optional_int(arguments.get("quality")))
        except media.MediaError as error:
            return f"Couldn't convert it: {error}.", None
        return (f"Converted. Give the user this download link exactly as written (it works for 24 hours): "
                f"{media.link(made)}\nIf the user's message also asked something else (e.g. what's in the file), answer that too."), None
    if name == "switch_model":
        return models.switch(str(arguments.get("model", "")), config["model"], notify.send), None
    return f"unknown tool {name}", None


def capped_tool_result(result):
    if not isinstance(result, str) or len(result) <= TOOL_RESULT_CHARS:
        return result
    return result[:TOOL_RESULT_CHARS] + f"\n(cut: the result was {len(result)} characters; ask for less to see the rest)"


def run_native_safely(name, arguments, config, background, task):
    try:
        result, waiting = run_native(name, arguments, config, background, task)
        return capped_tool_result(result), waiting
    except NATIVE_TOOL_ERRORS as error:
        log.warning("native tool %s failed: %r", name, error)
        return f"tool error: {name} didn't work ({type(error).__name__}: {error}). Check what you gave it and try again.", None


def week_recap(config) -> str:
    """The last 7 days: what the user asked (one line each) and the background jobs that finished."""
    cutoff = datetime.now().timestamp() - 7 * 86400
    asked = []
    for record in archive_records(config):
        try:
            if datetime.fromisoformat(record["time"]).timestamp() < cutoff:
                break
        except ValueError:
            continue
        if not record["user"].startswith("[background task"):
            asked.append(f"- {record['time'][:10]}: {APP_NOTES.sub('', record['user'])[:150]}")
    jobs = [f"- {t['title']}" for t in store.list_tasks("done", 30) if t["updated"] >= cutoff]
    return ("What the user asked this week (newest first):\n" + "\n".join(asked[:60] or ["- nothing"])
            + "\n\nBackground jobs finished:\n" + "\n".join(jobs or ["- none"])
            + "\n\nSum this up talking to the user as \"you\" (\"You asked me about...\", \"We sorted out...\"). "
              "Don't write it as their own post or in their voice unless they ask for a post.")


CONVERT_REQUEST = re.compile(r"\bconvert (?:it|this|that|them|the (?:file|image|picture|photo|video|audio|recording) )?\s*(?:in)?to (?:an? )?\.?(?P<format>[a-z0-9]{2,5})\b",
                             re.I)
ATTACHED_FILE_ID = re.compile(r"\bfile id (?P<id>[\w-]+)\.\)")


def convert_attachment(said: str, expanded_text: str) -> str | None:
    request = CONVERT_REQUEST.search(said)
    attached = ATTACHED_FILE_ID.findall(expanded_text)
    if not request or len(attached) != 1:
        return None
    return run_native("convert_file", {"file_id": attached[0], "format": request.group("format")}, connections.load_config(), False, None)[0]
