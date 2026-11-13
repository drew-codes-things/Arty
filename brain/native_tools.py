import personal
import reckon
import sharing
import timing


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


NATIVE_TOOLS = {
    "recall_conversations": _tool(
        "recall_conversations",
        "Search every past conversation with the user by a few key words. Returns dated excerpts, newest first.",
        {"query": {"type": "string", "description": "a few key words to look for"},
         "limit": {"type": "integer", "description": "how many excerpts to return (default 5)"}},
        ["query"]),
    "ask_user": _tool(
        "ask_user",
        "Only for things a human must do on your desktop: signing in to a website, or passing a CAPTCHA or bot check. "
        "Not for permission or questions: ask those in your normal reply. In a background task this pauses until the user has done it.",
        {"message": {"type": "string", "description": "what the user needs to do, written to him as 'you', e.g. 'Please sign in to LinkedIn in the browser on my desktop.'"}},
        ["message"]),
    "notify_user": _tool(
        "notify_user",
        "Send the user a notification right now (to Updates in their phone and desktop apps). Not for reminders: anything set with "
        "schedule_task already arrives there by itself at the right time.",
        {"message": {"type": "string", "description": "the notification, written to him as 'you'"}, "title": {"type": "string", "description": "short title (optional)"}},
        ["message"]),
    "start_background_task": _tool(
        "start_background_task",
        "Start a job that you work on by yourself in the background, so the user doesn't have to wait in the chat. "
        "He gets a phone notification with the result.",
        {"instructions": {"type": "string", "description": "everything needed to do the job, written as instructions to yourself"},
         "title": {"type": "string", "description": "a short name for the job"}},
        ["instructions"]),
    "schedule_task": _tool(
        "schedule_task",
        "Set a reminder, or schedule a job for later or on repeat. For a reminder (\"remind me ...\"), give reminder_text: "
        "that exact text is sent to the user's phone and desktop apps at that time. For a job (daily check, report), give instructions: "
        "you do it in the background when it runs and the user gets the result. Call this whenever the user asks to be reminded.",
        {"reminder_text": {"type": "string", "description": "for reminders: the exact message to send, written to the user, e.g. 'Time to stretch.'"},
         "instructions": {"type": "string", "description": "for jobs: what to do each time, as instructions to yourself"},
         "when": {"type": "string", "description": "copy the user's own words for the time, e.g. 'in 2 minutes', 'tomorrow 08:00', 'weekdays 07:30'. " + timing.HELP},
         "title": {"type": "string", "description": "a short name"}},
        ["when"]),
    "list_scheduled": _tool("list_scheduled", "List the scheduled jobs and when each runs next.", {}, []),
    "cancel_scheduled": _tool("cancel_scheduled", "Cancel a scheduled job by its number.",
                              {"schedule_id": {"type": "integer"}}, ["schedule_id"]),
    "list_tasks": _tool("list_tasks", "List recent background tasks and their status/results.", {}, []),
    "list_models": _tool("list_models", "List the AI models you can run on, and which is your main one.", {}, []),
    "ask_other_model": _tool(
        "ask_other_model",
        "Hand one self-contained job to another installed AI model and get its answer back. "
        "You stay on your own model. list_models shows what's installed.",
        {"model": {"type": "string", "description": "installed model name, e.g. qwen3:14b"},
         "prompt": {"type": "string", "description": "the full job, with everything the other model needs to know"}},
        ["model", "prompt"]),
    "calendar_events": _tool(
        "calendar_events",
        "The user's calendar: their events for today or the next few days (your own calendar, plus Google Calendar if connected). "
        "Your own events have #numbers for calendar_change and calendar_remove.",
        {"days": {"type": "integer", "description": "how many days ahead, from now (default 1 = today)"}},
        []),
    "calendar_add": _tool(
        "calendar_add",
        "Put an event in the user's calendar (your own calendar, which you remind them about 15 minutes before). "
        "Use for appointments, plans and birthdays; use schedule_task for plain reminders.",
        {"title": {"type": "string", "description": "what it is, e.g. 'Dentist'"},
         "when": {"type": "string", "description": "copy the user's words: 'Tuesday 3pm', '25 Dec 14:00', '24/12' (all day), "
                                                   "'tomorrow 18:00', 'every Monday 6pm' (repeats)"},
         "duration_minutes": {"type": "integer", "description": "how long (default 60, or all day for a date with no time)"},
         "location": {"type": "string"}, "notes": {"type": "string"}},
        ["title", "when"]),
    "calendar_change": _tool(
        "calendar_change",
        "Move, rename or change the length or place of one of your own calendar events, by its #number from calendar_events.",
        {"event_id": {"type": "integer"}, "title": {"type": "string"}, "when": {"type": "string"},
         "duration_minutes": {"type": "integer"}, "location": {"type": "string"}},
        ["event_id"]),
    "calendar_remove": _tool(
        "calendar_remove",
        "Delete one of your own calendar events by its #number from calendar_events (a repeating event stops repeating).",
        {"event_id": {"type": "integer"}},
        ["event_id"]),
    "weather": _tool(
        "weather",
        "The weather forecast for the user's home town (set in brain.json), or for another town or city: now, today, or the "
        "next few days.",
        {"days": {"type": "integer", "description": "1 = today (default), up to 7"},
         "place": {"type": "string", "description": "a town or city other than the user's home; leave out for home"}},
        []),
    "week_recap": _tool(
        "week_recap",
        "Looking back: what the user asked you about and what you did together over the last 7 days, for summaries and "
        "LinkedIn post ideas. Not for what's coming up (that's calendar_events).",
        {}, []),
    "convert_file": _tool(
        "convert_file",
        "Convert a file the user attached (or one you made) to another format, and get a download link for the result. "
        "Images: png, jpg, webp, gif, bmp, tiff, ico, avif, heic (optionally resized). Audio: mp3, wav, ogg, flac, m4a, aac, opus. "
        "Video: mp4, webm, mkv, mov, gif, or any audio format to pull out the sound.",
        {"file_id": {"type": "string", "description": "the file id shown with the attachment"},
         "format": {"type": "string", "description": "the target format, e.g. png, mp3, mp4"},
         "width": {"type": "integer", "description": "optional new width in pixels (images and video)"},
         "height": {"type": "integer", "description": "optional new height in pixels (images and video)"},
         "quality": {"type": "integer", "description": "optional image quality 1-100 (jpg/webp), lower is smaller"}},
        ["file_id", "format"]),
    "switch_model": _tool(
        "switch_model",
        "ONLY when the user explicitly asks you to change your main model: switch to it, e.g. 'qwen3:14b'. Downloads it first if needed.",
        {"model": {"type": "string", "description": "Ollama model name, e.g. qwen3:14b"}},
        ["model"]),
}
NATIVE_TOOLS.update({name: definition for name, (_, definition) in personal.TOOLS.items()})
NATIVE_TOOLS.update({name: definition for name, (_, definition) in reckon.TOOLS.items()})
NATIVE_TOOLS.update({name: definition for name, (_, definition) in sharing.TOOLS.items()})
BACKGROUND_ONLY_EXCLUDED = {"start_background_task", "schedule_task", "cancel_scheduled", "switch_model"}
API_CALLABLE_NATIVE = {*personal.TOOLS, *sharing.TOOLS, "weather", "week_recap", "calendar_events", "calendar_add", "calendar_change", "calendar_remove", "convert_file", "recall_conversations", "notify_user", "start_background_task", "schedule_task", "list_scheduled", "cancel_scheduled", "list_tasks", "list_models", "switch_model", "ask_other_model"}
