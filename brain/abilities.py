""""What can you do?": an honest list built from the tools Arty really has right now, not from the model's idea of
itself. A group of tools is listed only when at least one of its tools is installed, so a server that's down or a
tool that's removed never shows as something Arty can do.
"""
import fnmatch
import re

QUESTION = re.compile(r"\bwhat (?:else )?(?:can|could) you do\b|\bwhat are you (?:able|capable)\b|"
                      r"\bwhat are your (?:abilities|capabilities|skills|features)\b|\bwhat can i (?:ask|get) you\b|"
                      r"^\W*(?:help|commands)\W*$", re.IGNORECASE)

# How each tool group (brain.json "tool_groups") reads to the user. A group not here uses its own description.
FOR_THE_USER = {
    "email": "Gmail: pick out the important emails and tell you about new ones, search and read them, and draft "
             "replies (I never send anything)",
    "calendar": "your calendar: what's on, and adding, moving or removing events",
    "lists": "lists: shopping, to-do or any other list",
    "notes": "notes: save things and find them again",
    "study": "study: deadlines with reminders, and revision quizzes with flashcards",
    "weather": "the weather and forecasts",
    "tasks": "reminders and scheduled jobs, sent to Updates in the phone and desktop apps",
    "homelab": "your homelab: server status and IPs, websites up or down, diagnosing and restarting servers (with your OK)",
    "nextcloud": "your Nextcloud: list, search, fetch and upload files",
    "browser": "a web browser on my desktop: open sites, click, type, download, and read your LinkedIn posts",
    "screen": "my desktop: look at the screen, click, type and open apps",
    "computer": "a Linux computer of my own: run commands, read and write files, write and test code",
    "documents": "documents: read PDFs, Word, Excel and scans, and search your document library",
    "make_files": "make real files for you: Word documents, PDFs, PowerPoint slides, Excel spreadsheets and more",
    "files": "files you send me: convert pictures, audio and video to other formats",
    "models": "other AI models: hand a job to one (like a coding model) or switch my main model",
    "recap": "a recap of your week, and LinkedIn post ideas",
    "memory_admin": "my memory of you: show it, correct it or forget things",
    "thinking": "step-by-step planning for hard problems, and time zones",
    "reckoning": "exact sums, date and clock maths, unit conversions and exchange rates",
}
# Done by Arty's own code, so always there.
ALWAYS = [
    "answer questions, searching the web when it helps (\"use your desktop to...\" for a full, sourced answer)",
    "write posts, emails and messages in your style (\"learn how I write\" to update it)",
    "work things out exactly (\"what's 15% of 240\")",
    "remember what you tell me, and what we talked about before",
    "undo the last thing I set or added (\"undo that\")",
    "in the phone app: open a call or a ready-written text for you to press call or send (\"text Sam saying I'm late\")",
]
CANT = ("place calls or send texts myself (in the phone app I'll open them for you to press call or send), control "
        "smart-home devices, order, book or pay for things, or send emails or post anything without your OK")


def is_question(text: str) -> bool:
    return bool(QUESTION.search(text or ""))


def available_groups(tool_groups: dict, installed: set[str]) -> list[str]:
    """The groups that have at least one installed tool (group tool names may end in a * wildcard)."""
    return [group for group, spec in tool_groups.items()
            if any(fnmatch.filter(installed, pattern.replace("sequential_thinking", "sequential-thinking"))
                   for pattern in spec.get("tools", []))]


def describe(tool_groups: dict, installed: set[str]) -> str:
    groups = available_groups(tool_groups, installed)
    lines = [f"- {FOR_THE_USER.get(group) or tool_groups[group].get('description', group)}" for group in groups]
    lines += [f"- {thing}" for thing in ALWAYS]
    return ("Here's what I can do right now:\n\n" + "\n".join(lines)
            + f"\n\nWhat I can't do: {CANT}. If something isn't working, I'll tell you rather than pretend.")
