import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from eval_scoring import Case, Outcome, render_markdown, score, summarise

DESCRIPTION = """Scored end-to-end eval: real requests through the brain, each answer scored by eval_scoring.py.
Chat cases go through the brain's chat jobs as the phone sends them, or with --model through /v1/chat/completions
asking for that installed model. Vision cases ask a vision model about the pictures in tests/eval_images.
Results are saved in tests/results/eval-<date>-<label>.json and a markdown table is printed.
It makes real reminders, a calendar event and list items, all named TESTBATTERY, and removes the ones it made
straight after each case; anything it could not remove is listed at the end. The chat turns are archived like
any other chat."""
HERE = Path(__file__).resolve().parent
IMAGES = HERE / "eval_images"
RESULTS = HERE / "results"
MARKER = "TESTBATTERY"
TEST_LIST = "testbattery"
JOB_POLL_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 600

CHAT_CASES = [
    Case("what's 12 times 15?", "tools", tools=("calculate", "calculator"), any_words=("180",)),
    Case("what is 15% of 80?", "tools", tools=("calculate", "calculator"), any_words=("12",)),
    Case("what are all the IPs of my servers?", "tools", tools=("homelab__proxmox_status",), any_words=("CT", "VM")),
    Case("is my minecraft server running?", "tools", tools=("homelab__proxmox_status", "homelab__service_status"), any_words=("minecraft",)),
    Case("will it rain tomorrow?", "tools", tools=("weather",), any_words=("rain", "%")),
    Case("what's on my calendar this week?", "tools", tools=("calendar_events",), any_words=("calendar", "nothing", "no ")),
    Case("any new emails?", "tools", tools=("workspace__gmail_unread", "workspace__gmail_search"), any_words=("unread", "email")),
    Case("what are my deadlines?", "tools", tools=("deadlines_show",), any_words=("deadline", "nothing", "no ")),
    Case("who won the last football world cup?", "tools", tools=("research__web_search",), any_words=("argentina", "spain", "france")),
    Case("draft an email to my tutor saying I'll be late tomorrow", "tools", tools=("draft",), any_words=("late",)),
    Case(f"remind me to {MARKER} stretch in 3 hours", "tools", tools=("schedule_task",), any_words=("remind", "3 hours", MARKER)),
    Case(f"put {MARKER} dentist in my calendar next Tuesday at 3pm", "tools", tools=("calendar_add",), any_words=("tuesday", "15:00", "3")),
    Case(f"add {MARKER} eggs and {MARKER} bread to my {TEST_LIST} list", "tools", tools=("list_add",), any_words=("eggs", "bread")),
    Case("use your desktop to find out how long to boil an egg", "tools", tools=("workspace__browser_open", "research__web_search"),
         any_words=("minute",)),
    Case("explain what a VLAN is in two sentences", "answers", any_words=("vlan",)),
    Case("what's the difference between TCP and UDP? keep it short", "answers", all_words=("tcp", "udp")),
    Case("write a python function that reverses a string", "answers", all_words=("def ", "[::-1]")),
    Case("a train leaves at 14:50 and the trip takes 2 hours 25 minutes. when does it arrive?", "answers", any_words=("17:15", "5:15")),
    Case("write a LinkedIn post about getting my first homelab server", "answers", any_words=("homelab",)),
    Case("what did I have for breakfast this morning?", "honesty",
         admits=("don't know", "not sure", "no idea", "didn't", "haven't", "can't", "no record")),
    Case("turn on the living room lights", "honesty",
         admits=("can't", "don't have", "no way", "not able", "unable", "isn't", "no smart", "not connected")),
    Case("what's the exact population of my street?", "honesty",
         admits=("don't know", "not sure", "can't", "no way", "don't have", "isn't", "not able")),
    Case("hi arty", "persona", max_chars=300),
    Case("tell me a short joke", "persona", max_chars=500),
    Case("how are you doing today?", "persona", max_chars=400),
]

VISION_CASES = [
    Case("What is the invoice number and the total on this?", "vision", all_words=("4471", "36.20"), image="invoice.png"),
    Case("What shape is in the middle of this picture, and what colour is it?", "vision", all_words=("circle", "red"), image="shapes.png"),
    Case("What does the error in this terminal say, and how full is the disk?", "vision", all_words=("97", "sda1"), any_words=("disk", "full"),
         image="terminal.png"),
    Case("Which day's bar in this chart is the tallest?", "vision", any_words=("wed",), image="chart.png"),
]


class Brain:
    def __init__(self, base_url: str, key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}"}

    def call(self, method: str, path: str, body: dict | None = None, extra_headers: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {**self.headers, **(extra_headers or {}), **({"Content-Type": "application/json"} if data else {})}
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as reply:
            return json.loads(reply.read() or b"{}")

    def get(self, path: str) -> dict:
        return self.call("GET", path)

    def post(self, path: str, body: dict, extra_headers: dict | None = None) -> dict:
        return self.call("POST", path, body, extra_headers)

    def delete(self, path: str) -> None:
        self.call("DELETE", path)

    def ask_as_phone(self, text: str) -> Outcome:
        started = time.time()
        job = self.post("/chat/jobs", {"messages": [{"role": "user", "content": text}]})["id"]
        while True:
            result = self.get(f"/chat/jobs/{job}")
            if result["status"] != "running":
                error = "" if result["status"] == "done" else f"{result['status']}: {result.get('error')}"
                return Outcome(result.get("answer") or "", tuple(result.get("tools_used") or ()), time.time() - started, error)
            time.sleep(JOB_POLL_SECONDS)

    def ask_model(self, text: str, model: str) -> Outcome:
        started = time.time()
        headers = {"X-Arty-Client-Tools": "1", "X-Arty-Any-Model": "1"}
        result = self.post("/chat/completions", {"model": f"arty/{model}", "messages": [{"role": "user", "content": text}]}, headers)
        answer = result["choices"][0]["message"].get("content") or ""
        return Outcome(answer, tuple(result.get("tools_used") or ()), time.time() - started)

    def look(self, case: Case, model: str) -> Outcome:
        picture = base64.b64encode((IMAGES / case.image).read_bytes()).decode()
        content = [{"type": "text", "text": case.message}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{picture}"}}]
        started = time.time()
        result = self.post("/chat/completions", {"model": model, "messages": [{"role": "user", "content": content}]},
                           {"X-Arty-Direct-Vision": "1"})
        return Outcome(result["choices"][0]["message"].get("content") or "", (), time.time() - started)

    def own_things(self) -> dict:
        schedules = {s["id"]: s.get("title") or s.get("prompt") or "" for s in self.get("/schedules")["schedules"]}
        events = {e["id"]: e["title"] for e in self.get("/calendar?days=30")["events"] if str(e.get("uid", "")).startswith("own-")}
        return {"schedules": schedules, "events": events}

    def remove_test_things(self, before: dict) -> list[str]:
        after = self.own_things()
        left = []
        for kind, path in (("schedules", "/schedules"), ("events", "/calendar")):
            for thing_id, title in after[kind].items():
                if thing_id in before[kind]:
                    continue
                if MARKER.lower() in json.dumps(title).lower():
                    self.delete(f"{path}/{thing_id}")
                else:
                    left.append(f"new {kind[:-1]} {thing_id} without the {MARKER} marker: {title!r}")
        self.post("/tools/list_clear", {"arguments": {"list": TEST_LIST}})
        return left


def failed_request(error: OSError) -> Outcome:
    return Outcome("", (), 0.0, f"request failed: {error}")


def ask(brain: Brain, case: Case, model: str) -> Outcome:
    try:
        return brain.ask_model(case.message, model) if model else brain.ask_as_phone(case.message)
    except OSError as error:
        return failed_request(error)


def look(brain: Brain, case: Case, model: str) -> Outcome:
    try:
        return brain.look(case, model)
    except OSError as error:
        return failed_request(error)


def report_line(result: dict, label: str) -> str:
    return f"  {'PASS' if result['passed'] else 'FAIL'} {result['seconds']:6.1f}s  {label[:60]}"


def run_chat(brain: Brain, model: str) -> tuple[list[dict], list[str]]:
    results, left_behind = [], []
    for case in CHAT_CASES:
        before = brain.own_things() if MARKER in case.message else None
        outcome = ask(brain, case, model)
        if before is not None:
            left_behind += brain.remove_test_things(before)
        results.append(score(case, outcome))
        print(report_line(results[-1], case.message), flush=True)
    return results, left_behind


def run_vision(brain: Brain, model: str) -> list[dict]:
    results = []
    for case in VISION_CASES:
        results.append(score(case, look(brain, case, model)))
        print(report_line(results[-1], case.image), flush=True)
    return results


def save(label: str, report: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"eval-{date.today():%Y-%m-%d}-{label.replace('/', '_').replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--model", default="", help="an installed model to run Arty on (default: the main model, asked as the phone asks)")
    parser.add_argument("--vision-model", default="", help="the vision model for the picture cases (default: the brain's vision_model)")
    parser.add_argument("--only", choices=("chat", "vision"), default="", help="run only the chat cases or only the picture cases")
    options = parser.parse_args()
    base_url, key = os.environ.get("ARTY_URL", ""), os.environ.get("ARTY_KEY", "")
    if not base_url or not key:
        print("set ARTY_URL (for example http://arty-server:8081/v1) and ARTY_KEY, then run this again", file=sys.stderr)
        return 2
    brain = Brain(base_url, key)
    label = "-".join(part for part in (options.model, options.vision_model, options.only) if part) or "main"
    results, left_behind = [], []
    if options.only != "vision":
        chat_results, left_behind = run_chat(brain, options.model)
        results += chat_results
    if options.only != "chat":
        results += run_vision(brain, options.vision_model)
    summary = summarise(results)
    report = {"date": date.today().isoformat(), "model": options.model or "main", "vision_model": options.vision_model or "configured",
              "summary": summary, "results": results, "left_behind": left_behind}
    print("\n" + render_markdown({label: summary}))
    print(f"\nsaved {save(label, report)}")
    for line in left_behind:
        print(f"LEFT BEHIND: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
