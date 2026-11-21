import json
import logging
import threading
from datetime import datetime

import briefing
import inbox_tidy
import notify
import safety
import store
import timing
import watchers
from job_types import JobFailed, Run, Services

log = logging.getLogger("brain.jobs")

WATCH_PAGE = "watch_page"
WATCH_PRICE = "watch_price"
WATCH_SERVER = "watch_server"
BRIEFING = "briefing"
INBOX_TIDY = "inbox_tidy"
WATCHER_KINDS = (WATCH_PAGE, WATCH_PRICE, WATCH_SERVER)
KINDS = WATCHER_KINDS + (BRIEFING, INBOX_TIDY)
NAMES = {WATCH_PAGE: "Page watcher", WATCH_PRICE: "Price watcher", WATCH_SERVER: "Server watcher",
         BRIEFING: "Daily briefing", INBOX_TIDY: "Inbox tidy"}
MARKER = "JOB:"
DEFAULT_MAX_JOBS = 15
DEFAULT_MAX_WATCHERS = 8
MIN_CHECK_MINUTES = {WATCH_PAGE: 15, WATCH_PRICE: 15, WATCH_SERVER: 5}
SHORTEST_GAP_SAMPLES = 8
FAILURES_BEFORE_PAUSE = 12
MAX_LATE_SECONDS = 90 * 60
LOCK_WAIT_SECONDS = 120
MAX_TITLE_CHARS = 60
SECTIONS = ("news", "unread")
STATE_CLEARED_BY_RESUME = ("failures", "failure_reported", "last_error", "stopped")
ALLOWED_TOOLS = {
    WATCH_PAGE: frozenset({watchers.PAGE_TOOL}),
    WATCH_PRICE: frozenset({watchers.PAGE_TOOL}),
    WATCH_SERVER: frozenset({watchers.SERVER_TOOL}),
    BRIEFING: frozenset({watchers.SERVER_TOOL, briefing.NEWS_TOOL, "workspace__gmail_search"}),
    INBOX_TIDY: frozenset(),
}
EXPLICIT_TIMED = True
TOOL_REFUSED = "BLOCKED: this job is not allowed to use {name}."

_one_job_at_a_time = threading.Lock()


def job_of(schedule: dict) -> dict | None:
    raw = schedule.get("job")
    if not raw:
        return None
    try:
        job = json.loads(raw)
    except ValueError:
        return None
    return job if isinstance(job, dict) and job.get("kind") in KINDS else None


def restricted(call_tool, kind: str):
    allowed = ALLOWED_TOOLS[kind]

    def call(name: str, arguments: dict) -> str:
        if name not in allowed:
            log.warning("%s tried to use %s, which it is not allowed to", kind, name)
            return TOOL_REFUSED.format(name=name)
        return call_tool(name, arguments)

    return call


def shortest_gap_minutes(cron: str, now: datetime) -> float:
    runs = [timing.next_run(cron, now)]
    for _ in range(SHORTEST_GAP_SAMPLES):
        runs.append(timing.next_run(cron, runs[-1]))
    return min((later - earlier).total_seconds() / 60 for earlier, later in zip(runs, runs[1:]))


def limit_problem(kind: str, settings: dict) -> str | None:
    existing = [job_of(row) for row in store.list_job_schedules()]
    existing = [job for job in existing if job]
    if len(existing) >= int(settings.get("max_jobs", DEFAULT_MAX_JOBS)):
        return f"You already have {len(existing)} background jobs, the most I keep. Delete one first (ask me to list them)."
    watching = sum(1 for job in existing if job["kind"] in WATCHER_KINDS)
    if kind in WATCHER_KINDS and watching >= int(settings.get("max_watchers", DEFAULT_MAX_WATCHERS)):
        return f"You already have {watching} watchers, the most I run. Delete one first (ask me to list them)."
    if kind == INBOX_TIDY and any(job["kind"] == INBOX_TIDY for job in existing):
        return "Inbox tidy is already set up. Delete it first to change its time."
    return None


def schedule_problem(kind: str, cron: str | None, now: datetime) -> str | None:
    if kind in WATCHER_KINDS and cron is None:
        return "A watcher has to repeat, e.g. 'every 30 minutes' or 'hourly'."
    if kind in WATCHER_KINDS and shortest_gap_minutes(cron, now) < MIN_CHECK_MINUTES[kind]:
        return f"I check a {NAMES[kind].lower()} at most every {MIN_CHECK_MINUTES[kind]} minutes, so it doesn't hammer anything."
    return None


def create(kind: str, spec: dict, when: str, title: str, settings: dict, crisis_help: str | None = None,
           now: datetime | None = None) -> tuple[dict | None, str]:
    now = now or datetime.now()
    if kind not in KINDS:
        return None, "That isn't a kind of job I run."
    texts = [title, *[value for value in spec.values() if isinstance(value, str)]]
    if declined := safety.check_each(texts, crisis_help):
        log.info("declined a %s request in a job", declined[0])
        return None, declined[1]
    if problem := limit_problem(kind, settings):
        return None, problem
    try:
        cron, first, description = timing.parse_when(when, now)
    except ValueError as error:
        return None, f"Couldn't schedule it: {error}"
    if problem := schedule_problem(kind, cron, now):
        return None, problem
    title = (title or NAMES[kind]).strip()[:MAX_TITLE_CHARS]
    created = store.create_schedule(title, f"{MARKER}{kind}", cron, description, first.timestamp(), None,
                                    {"kind": kind, "spec": spec, "state": {}})
    return created, f"{NAMES[kind]} #{created['id']} set up ({description}). First run {first:%a %d %b at %H:%M}."


def job_rows() -> list[tuple[dict, dict]]:
    return [(row, job) for row in store.list_job_schedules() if (job := job_of(row))]


def status_of(row: dict, job: dict) -> str:
    state = job.get("state", {})
    if not row["enabled"]:
        return "paused" + (f" ({state['last_error']})" if state.get("failures") else "")
    if state.get("failures"):
        return f"failing ({state.get('last_error', 'unknown problem')})"
    return "active"


def listing() -> str:
    rows = job_rows()
    if not rows:
        return "You have no background jobs."
    return "\n".join(f"#{row['id']} {row['title']} [{NAMES[job['kind']]}, {row['description']}]: {status_of(row, job)}"
                     for row, job in rows)


def find(job_id) -> tuple[dict, dict] | None:
    return next(((row, job) for row, job in job_rows() if row["id"] == job_id), None)


def not_found(job_id) -> str:
    return f"I have no background job #{job_id}. Your jobs:\n{listing()}"


def pause(job_id: int) -> str:
    found = find(job_id)
    if found is None:
        return not_found(job_id)
    store.update_schedule(job_id, enabled=0)
    return f"Paused #{job_id} ({found[0]['title']}). Ask me to resume it any time."


def resume(job_id: int, now: datetime | None = None) -> str:
    found = find(job_id)
    if found is None:
        return not_found(job_id)
    row, job = found
    now = now or datetime.now()
    next_run = timing.next_run(row["cron"], now) if row["cron"] else now
    job["state"] = {key: value for key, value in job.get("state", {}).items()
                    if key not in STATE_CLEARED_BY_RESUME}
    store.save_job(job_id, job)
    store.update_schedule(job_id, enabled=1, next_run=next_run.timestamp())
    return f"Resumed #{job_id} ({row['title']}). Next check {next_run:%a %d %b at %H:%M}."


def delete(job_id: int) -> str:
    found = find(job_id)
    if found is None:
        return not_found(job_id)
    row, job = found
    open_task = store.get_task(job["state"]["open_task"]) if job.get("state", {}).get("open_task") else None
    if open_task and open_task["status"] == "waiting":
        store.update_task(open_task["id"], status="cancelled", waiting=None)
    store.delete_schedule(job_id)
    return f"Deleted #{job_id} ({row['title']})."


def trust_rule(sender: str, action: str, label: str, remove: bool = False) -> str:
    found = next(((row, job) for row, job in job_rows() if job["kind"] == INBOX_TIDY), None)
    if found is None:
        return "Set up inbox tidy first, then I can remember which changes you always allow."
    if problem := inbox_tidy.rule_problem(sender.lower(), action, label):
        return problem
    row, job = found
    sender = sender.lower()
    rule = {"sender": sender, "action": action, "label": label}
    rules = [existing for existing in job["spec"].get("preapproved", []) if existing != rule]
    if remove:
        job["spec"]["preapproved"] = rules
        store.save_job(row["id"], job)
        return "That rule is removed. Changes like it will ask you first again."
    if len(rules) >= inbox_tidy.MAX_RULES:
        return f"I keep at most {inbox_tidy.MAX_RULES} always-allowed rules. Remove one first."
    job["spec"]["preapproved"] = [*rules, rule]
    store.save_job(row["id"], job)
    what = f'label it "{label}"' if action == inbox_tidy.LABEL_KIND else "archive it"
    return f"From now on I'll {what} without asking when mail comes from exactly {sender}. Anything else still asks first."


def is_stale(job: dict, schedule: dict, now: datetime) -> bool:
    due = schedule.get("next_run")
    return job["kind"] in (BRIEFING, INBOX_TIDY) and due is not None and now.timestamp() - due > MAX_LATE_SECONDS


def run_briefing(run: Run) -> None:
    skipped: list[str] = []

    def complete(prompt: str) -> str:
        if refusal := run.services.gate.refusal(run.now, EXPLICIT_TIMED):
            skipped.append(refusal)
            return ""
        return run.services.complete(prompt)

    extras = frozenset(section for section in SECTIONS if run.spec.get(section, True))
    parts = briefing.compose(run.spec.get("period", "morning"), run.call, complete, run.services.place, run.now.timestamp(), extras,
                             run.spec.get("news_query") or briefing.NEWS_QUERY)
    note = [f"I left the AI model out of the email check because {skipped[0]}."] if skipped else []
    text = briefing.joined(parts) + "".join(f"\n\n{line}" for line in note)
    run.services.send(run.title, text, category=notify.ARTY)
    seen_in_chat = briefing.joined(parts, briefing.UNTRUSTED_PARTS) + ("\n\n(News headlines were sent to Updates.)" if any(
        name in briefing.UNTRUSTED_PARTS for name, _ in parts) else "")
    run.services.record_chat("[daily briefing]", seen_in_chat)


HANDLERS = {WATCH_PAGE: watchers.check_page, WATCH_PRICE: watchers.check_price, WATCH_SERVER: watchers.check_server,
            BRIEFING: run_briefing, INBOX_TIDY: inbox_tidy.run_tidy}


def record_success(job: dict) -> None:
    for key in ("failures", "failure_reported", "last_error"):
        job["state"].pop(key, None)


def record_failure(run: Run, job: dict, reason: str) -> bool:
    state = job["state"]
    state["failures"] = state.get("failures", 0) + 1
    state["last_error"] = reason[:200]
    if not state.get("failure_reported"):
        state["failure_reported"] = True
        run.tell(f"{run.title}: can't check", f"I couldn't run this job: {reason}. I'll keep trying quietly and say if it stops for good.",
                 category=notify.TASKS)
    return state["failures"] >= FAILURES_BEFORE_PAUSE


def save_state(schedule_id: int, state: dict) -> None:
    current = store.get_schedule(schedule_id)
    latest = job_of(current) if current else None
    if latest is not None:
        store.save_job(schedule_id, {**latest, "state": state})


def run(schedule: dict, services: Services, now: datetime) -> None:
    job = job_of(schedule)
    if job is None:
        return
    if is_stale(job, schedule, now):
        log.info("job %s skipped: its time passed more than %d minutes ago", schedule["id"], MAX_LATE_SECONDS // 60)
        return
    if not _one_job_at_a_time.acquire(timeout=LOCK_WAIT_SECONDS):
        log.warning("job %s skipped: another job held the runner for too long", schedule["id"])
        return
    try:
        fresh = store.get_schedule(schedule["id"])
        stored = job_of(fresh) if fresh else None
        if stored is None or stored.get("state", {}).get("stopped"):
            return
        job = {**stored, "state": dict(stored.get("state", {}))}
        context = Run(schedule["id"], schedule["title"], job["spec"], job["state"], services, now,
                      restricted(services.call_tool, job["kind"]))
        stop = False
        try:
            HANDLERS[job["kind"]](context)
            record_success(job)
        except JobFailed as error:
            stop = record_failure(context, job, str(error))
        except Exception as error:
            log.exception("job %s (%s) crashed", schedule["id"], job["kind"])
            stop = record_failure(context, job, f"unexpected {type(error).__name__}")
        if stop:
            job["state"]["stopped"] = True
            store.update_schedule(schedule["id"], enabled=0)
            context.tell(f"{context.title}: paused", f"It failed {FAILURES_BEFORE_PAUSE} checks in a row, so I paused it. "
                         "Ask me to resume it when the problem is fixed.", category=notify.TASKS)
        save_state(schedule["id"], job["state"])
    finally:
        _one_job_at_a_time.release()
