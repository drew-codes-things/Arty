import json

from arty_code import permissions, tools

MAX_ROUNDS = 40
STUCK_REPEATS = 3
MAX_EXPLORE_ANSWER_CHARS = 8_000
REFUSED = permissions.REFUSED
RECORDED_TOOLS = tools.CHANGING_TOOLS + ("run_command",)
STEP_LIMIT_REACHED = "I stopped after a lot of steps. Tell me if you want me to keep going."
EXPLORE_TOOLS = ("read_file", "list_files", "search", "web_fetch", "run_command")
EXPLORE_DEFINITION = tools.function(
    "explore",
    "Hand a looking-only job to a helper with a fresh conversation, e.g. 'find where sessions are saved and how' "
    "or 'what calls parse_config?'. It reads and searches on its own and reports back, keeping this conversation "
    "short. It can't change anything.",
    {"task": tools.TEXT}, ["task"])
EXPLORER_PROMPT = ("You are a helper inside arty-code. Answer the one question you are given by looking through the "
                   "project with your tools. Only look; never try to change anything. Reply briefly with what you "
                   "found, naming files and line numbers.")


def parsed_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    arguments = json.loads(raw or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("the arguments should be a JSON object")
    return arguments


def activity_line(name: str, arguments: dict) -> str:
    target = (arguments.get("path") or arguments.get("pattern") or arguments.get("command") or arguments.get("url")
              or arguments.get("task") or arguments.get("job") or "")
    return f"{name} {target}".strip()


def explorer_definitions() -> list[dict]:
    return [definition for definition in tools.DEFINITIONS if definition["function"]["name"] in EXPLORE_TOOLS]


class Quiet:
    def __init__(self, screen):
        self.screen = screen

    def show_text(self, text: str) -> None:
        pass

    def show_activity(self, line: str) -> None:
        self.screen.show_activity(f"  {line}")


def explore(task: str, brain, workspace, gate, screen) -> str:
    helper_gate = permissions.Gate(workspace, gate.ask, mode=permissions.PLAN)
    helper_gate.always = gate.always
    messages = [{"role": "system", "content": EXPLORER_PROMPT}, {"role": "user", "content": task}]
    answer = run_turn(messages, brain, workspace, helper_gate, Quiet(screen), offered=explorer_definitions(),
                      can_explore=False)
    return answer[:MAX_EXPLORE_ANSWER_CHARS] or "The helper found nothing to report."


def result_of(call: dict, brain, workspace, gate, screen, can_explore: bool, done: list) -> str:
    function = call.get("function") or {}
    name = function.get("name", "")
    try:
        arguments = parsed_arguments(function.get("arguments"))
    except ValueError as error:
        return f"Error: the arguments for {name} weren't valid JSON ({error}); send them again"
    screen.show_activity(activity_line(name, arguments))
    if name == "explore":
        if not can_explore:
            return "Error: a helper can't start another helper; look for it yourself"
        return explore(str(arguments.get("task", "")), brain, workspace, gate, screen)
    refusal = gate.check(name, arguments)
    if refusal:
        return refusal
    recorded = name in RECORDED_TOOLS
    try:
        result = tools.run(name, arguments, workspace)
    except KeyboardInterrupt:
        if recorded:
            done.append(f"{activity_line(name, arguments)} (stopped part way)")
        raise
    if recorded and (name == "run_command" or not result.startswith("Error")):
        done.append(activity_line(name, arguments))
    return result


def signature_of(calls: list[dict]) -> str:
    return json.dumps([(call.get("function") or {}).get("name") for call in calls]
                      + [(call.get("function") or {}).get("arguments") for call in calls])


def run_turn(messages: list[dict], brain, workspace, gate, screen, offered=None, can_explore=True, done=None,
             squeeze=None) -> str:
    offered = (tools.DEFINITIONS + [EXPLORE_DEFINITION]) if offered is None else offered
    done = [] if done is None else done
    recent = []
    for _round in range(MAX_ROUNDS + 1):
        reply = brain.reply(messages, tools=offered, on_text=screen.show_text)
        content = reply.get("content") or ""
        calls = reply.get("tool_calls") or []
        if not calls:
            messages.append({"role": "assistant", "content": content})
            return content
        messages.append({"role": "assistant", "content": content or None, "tool_calls": calls})
        for call in calls:
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                             "content": result_of(call, brain, workspace, gate, screen, can_explore, done)})
        if squeeze:
            squeeze(messages)
        recent = (recent + [signature_of(calls)])[-STUCK_REPEATS:]
        if len(recent) == STUCK_REPEATS and len(set(recent)) == 1:
            offered = []
    messages.append({"role": "assistant", "content": STEP_LIMIT_REACHED})
    return STEP_LIMIT_REACHED
