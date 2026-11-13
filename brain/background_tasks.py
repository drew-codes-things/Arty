import json
import logging
import time

import connections
import notify
import policy
import store

from native_tools import NATIVE_TOOLS
from reply_rules import trim_offers, TOOL_FAILED
from steering import on_my_desktop, task_links
from replies import tidy_answer
from native_calls import run_native_safely
from agent_loop import AgentRun, run_agent
from archive import archive_turn

log = logging.getLogger("brain")


WORKER_IDLE_SECONDS = 3
SHORT_REPORT_CHARS = 60


# ---------- background tasks ----------

def fix_still_needed(waiting: dict) -> tuple[bool, str]:
    recheck = waiting.get("recheck")
    if not recheck:
        return True, ""
    try:
        diagnosis = json.loads(connections.hub.call("homelab__diagnose", {"address": recheck["address"], "port": recheck["port"],
                                                              "guest_name": ""}))
    except (ValueError, TypeError, KeyError) as error:
        return True, f"I couldn't check it again first ({error}), so I went ahead as approved. "
    summary = str(diagnosis.get("summary") or "")
    fix = diagnosis.get("fix")
    approved = waiting.get("call") or {}
    if not fix and TOOL_FAILED.search(summary):
        return True, f"I couldn't check it again first ({summary}), so I went ahead as approved. "
    if not fix:
        return False, (f"Not done: by the time the user approved, it no longer needed fixing ({summary}). "
                       "Tell them nothing was restarted.")
    wanted = "homelab__start_guest" if fix.get("action") == "start" else "homelab__restart_guest"
    if wanted != approved.get("name") or fix.get("vmid") != (approved.get("arguments") or {}).get("vmid"):
        return False, (f"Not done: things changed since the user approved ({summary}). Tell them, and ask again "
                       "before doing anything.")
    return True, ""


def run_approved_call(call: dict) -> str:
    name, arguments = call["name"], call.get("arguments") or {}
    if name in NATIVE_TOOLS:
        return run_native_safely(name, arguments, connections.load_config(), True, None)[0]
    return connections.hub.call(name, arguments)


def apply_decision(state: dict, waiting: dict, decision: str, note: str):
    call = waiting.get("call") or {}
    if waiting.get("kind") == "approval":
        if decision != "approve":
            result = "The user said no to this. Don't do it; carry on without it, or finish and report."
        else:
            still_needed, recheck_note = fix_still_needed(waiting)
            if still_needed:
                outcome = run_approved_call(call)
                policy.remember_tool_result(state.setdefault("context", {}), call["name"], outcome)
                result = f"The user approved. {recheck_note}Result:\n{outcome}"
            else:
                result = recheck_note
    else:
        result = "The user says he has done it" + (f": {note}" if note else ".") + " Carry on."
    state["messages"].append({"role": "tool", "content": result, "tool_name": call.get("name", "ask_user")})


INTERRUPTED_DECISION = ("Arty restarted while carrying out what the user approved, so it may or may not have happened. "
                        "Check whether it took effect before doing anything else, and never do it a second time unasked.")


def carries_out_an_approval(waiting: dict) -> bool:
    return waiting.get("kind") == "approval" and waiting.get("decision") == "approve"


def process_task(task: dict):
    config = connections.load_config()
    state = task["history"] or {"messages": [{"role": "user", "content": f"Background task #{task['id']}: {task['title']}\n\n{task['instructions']}"}]}
    waiting = task["waiting"] or {}
    if waiting.get("decision") and waiting.get("applying"):
        state["messages"].append({"role": "tool", "content": INTERRUPTED_DECISION,
                                  "tool_name": (waiting.get("call") or {}).get("name", "ask_user")})
    elif waiting.get("decision"):
        if carries_out_an_approval(waiting):
            store.update_task(task["id"], waiting={**waiting, "applying": True})
        connections.hub.refresh()
        apply_decision(state, waiting, waiting["decision"], waiting.get("note", ""))
    store.update_task(task["id"], history=state, waiting=None)
    outcome = run_agent(config, state, run=AgentRun(background=True, task=task))
    tools_used = (task["tools_used"] or []) + outcome["tools_used"]
    if outcome["status"] == "cancelled" or (store.get_task(task["id"]) or {}).get("status") == "cancelled":
        store.update_task(task["id"], history=state, tools_used=tools_used)
        log.info("task %s cancelled", task["id"])
        return
    if outcome["status"] == "waiting":
        pending = outcome["waiting"]
        store.update_task(task["id"], status="waiting", history=state, waiting=pending, tools_used=tools_used)
        refreshed = store.get_task(task["id"])
        if pending["kind"] == "approval":
            buttons = task_links(refreshed, [("Approve", "approve"), ("Deny", "deny"), ("Cancel task", "cancel")])
            notify.send(f"Arty wants approval (task #{task['id']})", f"{task['title']}\n\nArty wants to: {pending['message']}", buttons, urgent=True,
                        category=notify.APPROVALS)
        else:
            buttons = task_links(refreshed, [("Done", "done"), ("Cancel task", "cancel")])
            notify.send(f"Arty needs you (task #{task['id']})", f"{task['title']}\n\n{pending['message']}\n\n{on_my_desktop(config)}, "
                        "double-click \"Sign in to websites\", sign in, close that window, then tap Done.", buttons, urgent=True,
                        category=notify.TASKS)
        log.info("task %s waiting for the user: %s", task["id"], pending["message"])
        return
    answer = tidy_answer(trim_offers(outcome["answer"])) or "(Arty finished without writing a report.)"
    store.update_task(task["id"], status="done", history=state, result=answer, tools_used=tools_used)
    archive_turn(config, f"[background task #{task['id']}] {task['title']}: {task['instructions'][:500]}", answer, tools_used)
    # A short report ("done", "triggered") is meaningless on its own, e.g. for reminders, so include the job itself.
    message = answer if len(answer) >= SHORT_REPORT_CHARS else f"{task['instructions'][:1000]}\n\n{answer}".strip()
    notify.send(f"Arty: {task['title']}", message, category=notify.TASKS)
    log.info("task %s done", task["id"])


def fail_task(task: dict, error: Exception):
    try:
        store.update_task(task["id"], status="failed", result=f"failed: {error}")
        notify.send(f"Arty: task #{task['id']} failed", f"{task['title']}\n\n{error}", category=notify.TASKS)
    except Exception:
        log.exception("couldn't record that task %s failed", task["id"])


def worker_step() -> bool:
    try:
        task = store.claim_next_task()
    except Exception:
        log.exception("couldn't take the next task")
        return False
    if task is None:
        return False
    log.info("task %s started: %s", task["id"], task["title"])
    try:
        process_task(task)
    except Exception as error:
        log.exception("task %s failed", task["id"])
        fail_task(task, error)
    return True


def worker_loop():
    while True:
        if not worker_step():
            time.sleep(WORKER_IDLE_SECONDS)


def decide_task(task_id: int, action: str, note: str = "") -> tuple[int, str]:
    task = store.get_task(task_id)
    if task is None:
        return 404, "No such task."
    if action == "cancel":
        if task["status"] in ("done", "failed", "cancelled"):
            return 409, f"Task is already {task['status']}."
        store.update_task(task_id, status="cancelled", waiting=None)
        return 200, "Task cancelled."
    waiting = task["waiting"] or {}
    if task["status"] != "waiting":
        return 409, f"Task isn't waiting (it's {task['status']})."
    expected = ("approve", "deny") if waiting.get("kind") == "approval" else ("done",)
    if action not in expected:
        return 409, f"This task is waiting for: {' or '.join(expected)}."
    waiting.update({"decision": action, "note": note[:500]})
    store.update_task(task_id, status="queued", waiting=waiting)
    return 200, {"approve": "Approved, Arty is carrying on.", "deny": "Denied, Arty will carry on without it.", "done": "Thanks, Arty is carrying on."}[action]


def public_task(task: dict, with_history: bool = False) -> dict:
    hidden = {"token"} | (set() if with_history else {"history"})
    return {key: value for key, value in task.items() if key not in hidden}
