"""End-to-end battery: real requests through the brain's chat jobs (as the phone sends them), each answer
checked for the failure types seen in testing. Prints a table, then each answer to read.

It makes real reminders, a calendar event and list items named TESTBATTERY, and archives the turns: remove
them afterwards (the reminders would go off). Needs the requests package."""
import os
import re
import time

import requests

BASE_URL = os.environ.get("ARTY_URL", "").rstrip("/")
if not BASE_URL or "ARTY_KEY" not in os.environ:
    raise SystemExit("set ARTY_URL (for example http://arty-server:8081/v1) and ARTY_KEY, then run this again")
H = {"Authorization": f"Bearer {os.environ['ARTY_KEY']}"}
CASES = [
    # (message, tools one of which must be used (or None), words the answer should mention (any))
    ("what's 12 times 15?", ["calculator"], ["180"]),
    ("what is 15% of 80?", ["calculator"], ["12"]),
    ("what are all the IPs of my servers?", ["homelab__proxmox_status"], ["CT", "VM"]),
    ("is my minecraft server running?", ["homelab__proxmox_status", "homelab__service_status"], ["minecraft"]),
    ("will it rain tomorrow?", ["weather"], ["rain", "%"]),
    ("what's on my calendar this week?", ["calendar_events"], ["calendar", "nothing", "no "]),
    ("any new emails?", ["workspace__gmail_unread", "workspace__gmail_search"], ["unread", "email"]),
    ("any important emails?", ["workspace__gmail_search"], ["important", "nothing"]),
    ("what are my deadlines?", ["deadlines_show"], ["deadline", "nothing", "no "]),
    ("quiz me on networking", ["quiz"], ["?"]),
    ("stop the quiz", ["quiz"], []),
    ("what's the best way to learn Python as a beginner?", ["research__web_search"], ["python"]),
    ("who won the last football world cup?", ["research__web_search"], ["argentina", "spain", "france"]),
    ("write a LinkedIn post about getting my first homelab server", ["draft"], ["homelab"]),
    ("draft an email to my tutor saying I'll be late tomorrow", ["draft"], ["late"]),
    ("remind me to TESTBATTERY stretch in 3 hours", ["schedule_task"], ["remind", "3 hours", "TESTBATTERY"]),
    ("put TESTBATTERY dentist in my calendar next Tuesday at 3pm", ["calendar_add"], ["tuesday", "15:00", "3"]),
    ("add TESTBATTERY milk to my shopping list", ["list_add"], ["milk"]),
    ("add TESTBATTERY eggs and TESTBATTERY bread to my shopping list", ["list_add"], ["eggs", "bread"]),
    ("remind me in 10 minutes to TESTBATTERY drink water", ["schedule_task"], ["TESTBATTERY"]),
    ("use your desktop to find out how long to boil an egg", ["workspace__browser_open", "research__web_search"], ["minute"]),
    ("explain what a VLAN is in two sentences", None, ["vlan"]),
    ("tell me a short joke", None, []),
    ("what did we talk about earlier today?", None, []),
    ("hi arty", None, []),
]
LEAKS = re.compile(r"</?notes>|\[(weather|calendar_events|homelab__\w+|research__\w+)\]|\bArty \(you\):|\[\d{4}-\d\d-\d\d \d\d:\d\d\] User:|"
                   r"\b\w+__\w+\b|BACKGROUND ONLY|FACTS YOU ALREADY KNOW|# Tools|<tool_call>", re.I)
CLAIM = re.compile(r"\bI('ve| have) (set|scheduled|added|created|booked)\b", re.I)


def ask(text):
    started = time.time()
    job = requests.post(f"{BASE_URL}/chat/jobs", headers=H, timeout=30,
                        json={"messages": [{"role": "user", "content": text}]}).json()["id"]
    while True:
        result = requests.get(f"{BASE_URL}/chat/jobs/{job}", headers=H, timeout=30).json()
        if result["status"] != "running":
            return result, time.time() - started
        time.sleep(1)


def problems(case, result):
    text, wanted_tools, words = case
    answer = result.get("answer") or ""
    used = result.get("tools_used") or []
    found = []
    if result["status"] != "done":
        found.append(f"status {result['status']}: {result.get('error')}")
    if not answer.strip():
        found.append("empty answer")
    if answer.count("```") % 2:
        found.append("unclosed code block")
    if leak := LEAKS.search(answer):
        found.append(f"leak: {leak.group()!r}")
    if wanted_tools and not set(wanted_tools) & set(used):
        found.append(f"didn't use {wanted_tools} (used {used})")
    if CLAIM.search(answer) and not set(used) & {"schedule_task", "calendar_add", "list_add", "note_save", "deadline_add", "card_add"}:
        found.append("claims to have done something without the tool")
    if words and not any(w.lower() in answer.lower() for w in words):
        found.append(f"none of {words} in the answer")
    return found


if __name__ == "__main__":
    rows, answers = [], []
    for case in CASES:
        result, seconds = ask(case[0])
        found = problems(case, result)
        rows.append((case[0][:52], f"{seconds:5.1f}s", ",".join(result.get("tools_used") or [])[:44], "OK" if not found else "; ".join(found)))
        answers.append((case[0], result.get("answer") or result.get("error")))
    for row in rows:
        print(f"{row[0]:54} {row[1]}  {row[2]:46} {row[3]}")
    print("\n" + "=" * 100)
    for question, answer in answers:
        print(f"\n### {question}\n{answer}")
