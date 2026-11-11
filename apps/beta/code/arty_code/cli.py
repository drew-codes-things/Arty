import argparse
import getpass
import json
import sys
from dataclasses import replace
from pathlib import Path

from arty_code import (__version__, agent, brain, commands, memory, mentions, permissions, prompts, sessions, settings,
                       tools)

NOT_SET_UP = 2
FAILED = 1
PROMPT = "› "
CONTINUATION_PROMPT = "… "
LINE_CONTINUES = "\\"
NOTE_MARK = "#"
SHELL_MARK = "!"
SESSIONS_FOLDER = "sessions"
COMPACT_AT_CHARS = 48_000
SUMMARY_TOOL_CHARS = 2_000
TRIMMED_NOTE = "\n... (cut for the summary)"
SUMMARY_LEAD = "Summary of our conversation so far:"
SUMMARY_ACK = "Got it, carrying on from there."
CARRY_ON = "Carry on with the work from there."
STOPPED = "That request stopped before it finished."
TRUST_QUESTION = ("This folder has an ARTY.md with notes Arty would follow, and may have its own /commands. "
                  "Only say yes if you trust whoever wrote them. Trust this folder? [y/n]: ")
SUMMARY_REQUEST = ("Summarise this conversation for yourself so the work can carry on with only the summary: what "
                   "the user wants, decisions made, files changed and how, commands run and their results, and "
                   "what is left to do. Be specific with file paths. Plain text, no tools.")
COMMANDS = {
    "/help": "show this list",
    "/model [name]": "show or change the model, e.g. /model arty/qwen3:14b",
    "/mode [name]": f"show or change how much is asked first: {', '.join(permissions.MODES)}",
    "/plan": "switch plan mode on or off (look and plan, change nothing)",
    "/status": "model, brain, mode, project and conversation size",
    "/todos": "show the current checklist",
    "/memory": "show which ARTY.md notes are loaded",
    "/compact": "replace the conversation with a summary to free up room",
    "/clear": "start a fresh conversation",
    "/resume": "list this project's earlier conversations",
    "/exit": "leave (Ctrl-D works too)",
}
SHORTCUTS = {
    "@path": "attach a file (pictures too)",
    "# note": "add a note to this project's ARTY.md",
    "!command": "run a shell command yourself; Arty sees the output",
    "line \\": "carry on typing on the next line",
}
COLOURS = {"dim": "\033[2m", "red": "\033[31m", "green": "\033[32m", "cyan": "\033[36m", "reset": "\033[0m"}
ANSWERS = {"y": permissions.YES, "yes": permissions.YES, "n": permissions.NO, "no": permissions.NO,
           "a": permissions.ALWAYS, "always": permissions.ALWAYS}
TODO_MARKS = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}


def is_interactive() -> bool:
    return sys.stdin.isatty()


class Terminal:
    def __init__(self, out=None, err=None):
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.mid_line = False
        self.colour = self.err.isatty()

    def paint(self, text: str, colour: str) -> str:
        return f"{COLOURS[colour]}{text}{COLOURS['reset']}" if self.colour else text

    def end_line(self) -> None:
        if self.mid_line:
            self.out.write("\n")
            self.out.flush()
            self.mid_line = False

    def show_text(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()
        self.mid_line = not text.endswith("\n")

    def show_activity(self, line: str) -> None:
        self.end_line()
        self.err.write(self.paint(f"  · {line}", "dim") + "\n")
        self.err.flush()

    def note(self, line: str, colour: str = "dim") -> None:
        self.end_line()
        self.err.write(self.paint(line, colour) + "\n")
        self.err.flush()

    def show_diff(self, preview: str) -> None:
        for line in preview.splitlines():
            colour = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
            self.err.write(self.paint(line, colour) + "\n")

    def ask(self, question: str, preview: str = "") -> str:
        if not is_interactive():
            self.note(f"Refused without asking (no terminal to ask in): {question}", "red")
            return permissions.NO
        self.end_line()
        if preview:
            self.show_diff(preview)
        while True:
            try:
                answer = input(self.paint(f"{question} [y]es / [n]o / [a]lways: ", "cyan")).strip().lower()
            except EOFError:
                self.end_line()
                return permissions.NO
            if answer in ANSWERS:
                return ANSWERS[answer]


def parse_arguments(argv):
    parser = argparse.ArgumentParser(prog="arty-code", description="Arty in your terminal: reads, edits and runs "
                                     "code in the current folder, asking before it changes anything.")
    parser.add_argument("words", nargs="*", help="`setup` to connect to your Arty, or a question to ask once")
    parser.add_argument("-p", "--print", dest="once", help="ask once, print the answer and exit")
    parser.add_argument("-c", "--continue", dest="carry_on", action="store_true",
                        help="carry on with the last conversation in this project")
    parser.add_argument("-r", "--resume", metavar="ID", help="carry on with an earlier conversation (see /resume)")
    parser.add_argument("--model", help="model for this session, e.g. arty/qwen2.5-coder:14b")
    parser.add_argument("--project", type=Path, default=None, help="project folder (default: here)")
    parser.add_argument("--permission-mode", choices=permissions.MODES, default=permissions.DEFAULT,
                        help="how much to ask first (allow-all asks nothing: only in a throwaway folder)")
    parser.add_argument("--auto-edit", action="store_true", help="same as --permission-mode auto-edit")
    parser.add_argument("--version", action="version", version=f"arty-code {__version__}")
    return parser.parse_args(argv)


def asked(label: str, current: str, secret: bool = False) -> str:
    shown = "(saved)" if secret and current else current
    prompt = f"{label}{f' [{shown}]' if shown else ''}: "
    answer = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    return answer or current


def setup(terminal: Terminal) -> int:
    current = settings.load()
    terminal.note("Connect arty-code to your Arty brain. Press Enter to keep a value.", "cyan")
    updated = settings.Settings(
        base_url=asked("Brain address on your home network (http://host:8081/v1)", current.base_url),
        remote_url=asked("Brain address from anywhere (https://..., optional)", current.remote_url),
        api_key=asked("Brain API key", current.api_key, secret=True),
        access_client_id=asked("Cloudflare Access client id (optional)", current.access_client_id),
        access_client_secret=asked("Cloudflare Access client secret (optional)", current.access_client_secret, secret=True),
        model=asked("Model", current.model),
    )
    terminal.note(f"Saved to {settings.save(updated)}", "green")
    return 0


def conversation_size(messages: list[dict]) -> int:
    return len(json.dumps(messages))


def stopped_note(done: list[str]) -> str:
    return f"{STOPPED} Already done: {'; '.join(done)}." if done else STOPPED


def project_notes_allowed(workspace, personal: Path, terminal: Terminal) -> bool:
    notes = workspace.root / memory.MEMORY_FILE
    commands_folder = workspace.root / commands.PROJECT_COMMANDS
    if not (notes.is_file() or commands_folder.is_dir()) or memory.is_trusted(personal, workspace.root):
        return True
    if not is_interactive():
        terminal.note(f"Not following {notes.name} or the project's /commands: this folder isn't trusted yet. "
                      "Start arty-code here in a terminal once to trust it.", "red")
        return False
    try:
        answer = input(terminal.paint(TRUST_QUESTION, "cyan")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer in ("y", "yes"):
        memory.trust(personal, workspace.root)
        return True
    terminal.note("Leaving this folder's notes and /commands out.")
    return False


class Session:
    def __init__(self, client, workspace, gate, terminal, saved, project_trusted=True):
        self.client = client
        self.workspace = workspace
        self.gate = gate
        self.terminal = terminal
        self.saved = saved
        self.personal = settings.config_dir()
        self.project_trusted = project_trusted
        self.turn_start = 0
        self.compacted_in_turn = False
        self.messages = saved.messages or self.fresh()

    def fresh(self) -> list[dict]:
        notes = memory.instructions(self.workspace, self.personal, self.project_trusted)
        return [{"role": "system", "content": prompts.system_prompt(self.workspace, notes)}]

    def remember(self) -> None:
        self.saved.save(self.messages, self.client.model)

    def ask(self, text: str) -> bool:
        if conversation_size(self.messages) > COMPACT_AT_CHARS:
            self.terminal.note("The conversation is long, so it's being summarised first.")
            self.compact()
        self.turn_start, self.compacted_in_turn = len(self.messages), False
        self.messages.append({"role": "user", "content": mentions.expand(text, self.workspace)})
        done = []
        try:
            agent.run_turn(self.messages, self.client, self.workspace, self.gate, self.terminal, done=done,
                           squeeze=self.squeeze)
        except KeyboardInterrupt:
            self.abandon_turn(done, "Stopped.")
            return False
        except brain.BrainError as error:
            self.abandon_turn(done, f"{error} Ask again when it's back.")
            return False
        self.terminal.end_line()
        self.remember()
        return True

    def abandon_turn(self, done: list[str], reason: str) -> None:
        if not done and not self.compacted_in_turn:
            del self.messages[self.turn_start:]
            self.terminal.note(f"{reason} That request was dropped from the conversation.", "red")
            return
        del self.messages[self.turn_start + 1:]
        self.messages.append({"role": "assistant", "content": stopped_note(done)})
        self.remember()
        self.terminal.note(f"{reason} What already ran is noted in the conversation.", "red")

    def summary_of(self, messages: list[dict]) -> str:
        request = trimmed_for_summary(messages) + [{"role": "user", "content": SUMMARY_REQUEST}]
        return (self.client.reply(request, tools=[], on_text=lambda text: None).get("content") or "").strip()

    def squeeze(self, messages: list[dict]) -> None:
        if conversation_size(messages) <= COMPACT_AT_CHARS:
            return
        self.terminal.note("The conversation is long, so it's being summarised before the next step.")
        summary = self.summary_of(messages)
        messages[:] = self.fresh() + [{"role": "user", "content": f"{SUMMARY_LEAD}\n{summary}\n\n{CARRY_ON}"}]
        self.turn_start, self.compacted_in_turn = len(messages) - 1, True
        self.remember()

    def compact(self) -> None:
        summary = self.summary_of(self.messages)
        self.messages = self.fresh() + [{"role": "user", "content": f"{SUMMARY_LEAD}\n{summary}"},
                                        {"role": "assistant", "content": SUMMARY_ACK}]
        self.remember()

    def run_shell(self, command: str) -> None:
        output = tools.run("run_command", {"command": command}, self.workspace)
        self.terminal.show_text(output + "\n")
        self.messages.append({"role": "user", "content": f"(I ran `{command}` myself. Output:)\n{output}"})
        self.messages.append({"role": "assistant", "content": "Noted."})
        self.remember()

    def add_note(self, note: str) -> None:
        path = memory.add_note(self.workspace, note)
        memory.trust(self.personal, self.workspace.root)
        self.project_trusted = True
        self.messages[0] = self.fresh()[0]
        self.terminal.note(f"Added to {path}", "green")

    def show_status(self) -> None:
        self.terminal.note(f"Model: {self.client.model}")
        self.terminal.note(f"Brain: {self.client.address() or 'not contacted yet'}")
        self.terminal.note(f"Mode: {self.gate.mode}")
        self.terminal.note(f"Project: {self.workspace.root}")
        self.terminal.note(f"Conversation: {len(self.messages)} messages, {conversation_size(self.messages)} characters "
                           f"(summarised past {COMPACT_AT_CHARS}), saved as {self.saved.id}")

    def show_todos(self) -> None:
        if not self.workspace.todos:
            self.terminal.note("No checklist yet.")
        for item in self.workspace.todos:
            self.terminal.note(f"{TODO_MARKS.get(item.get('status'), '[ ]')} {item.get('text', '')}")

    def show_memory(self) -> None:
        files = memory.memory_files(self.workspace, self.personal, self.project_trusted)
        for path in files:
            self.terminal.note(f"{path} ({len(path.read_text(encoding='utf-8', errors='replace'))} characters)")
        if not files:
            self.terminal.note(f"No notes yet. /init writes {self.workspace.root / memory.MEMORY_FILE}; "
                               f"'# note' adds one line; {self.personal / memory.MEMORY_FILE} holds notes for every project.")

    def show_resumable(self) -> None:
        listed = sessions.Store(self.personal / SESSIONS_FOLDER).listed(self.workspace.root)
        if not listed:
            self.terminal.note("No earlier conversations in this project.")
        for entry in listed[:20]:
            self.terminal.note(f"{entry.id}  {entry.first_request}")
        if listed:
            self.terminal.note("Carry one on with: arty-code --resume <id>")

    def set_mode(self, mode: str) -> None:
        if mode not in permissions.MODES:
            self.terminal.note(f"Modes: {', '.join(permissions.MODES)}", "red")
            return
        self.gate.mode = mode
        self.terminal.note(f"Mode: {mode}")

    def show_help(self, custom: dict) -> None:
        for usage, meaning in {**COMMANDS, **SHORTCUTS}.items():
            self.terminal.note(f"{usage:16} {meaning}")
        for name, command in custom.items():
            self.terminal.note(f"{name:16} ({command.source}) {command.template.strip().splitlines()[0][:60]}")

    def command(self, line: str) -> bool:
        name, _, rest = line.partition(" ")
        custom = commands.find(self.workspace, self.personal, self.project_trusted)
        actions = {
            "/clear": lambda: (setattr(self, "messages", self.fresh()), self.terminal.note("Fresh conversation.")),
            "/model": lambda: self.change_model(rest.strip()),
            "/mode": lambda: self.set_mode(rest.strip()) if rest.strip() else self.terminal.note(f"Mode: {self.gate.mode}"),
            "/plan": lambda: self.set_mode(permissions.DEFAULT if self.gate.mode == permissions.PLAN else permissions.PLAN),
            "/status": self.show_status,
            "/todos": self.show_todos,
            "/memory": self.show_memory,
            "/compact": lambda: (self.compact(), self.terminal.note("Summarised.")),
            "/resume": self.show_resumable,
            "/help": lambda: self.show_help({k: v for k, v in custom.items() if k not in commands.BUILT_IN}),
        }
        if name in ("/exit", "/quit"):
            return False
        if name in actions:
            try:
                actions[name]()
            except brain.BrainError as error:
                self.terminal.note(str(error), "red")
        elif name in custom:
            self.ask(custom[name].prompt(rest))
        else:
            self.terminal.note(f"Unknown command {name}. Type /help for the list.", "red")
        return True

    def change_model(self, model: str) -> None:
        if model:
            self.client.model = model
        self.terminal.note(f"Model: {self.client.model}")


def read_request() -> str:
    lines = [input(PROMPT)]
    while lines[-1].rstrip().endswith(LINE_CONTINUES):
        lines[-1] = lines[-1].rstrip()[:-1].rstrip()
        lines.append(input(CONTINUATION_PROMPT))
    return "\n".join(lines).strip()


def trimmed_for_summary(messages: list[dict]) -> list[dict]:
    trimmed = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "tool" and isinstance(content, str) and len(content) > SUMMARY_TOOL_CHARS:
            message = {**message, "content": content[:SUMMARY_TOOL_CHARS] + TRIMMED_NOTE}
        trimmed.append(message)
    return trimmed


def handle(session: Session, line: str) -> bool:
    if line.startswith("/"):
        return session.command(line)
    if line.startswith(SHELL_MARK) and len(line) > 1:
        session.run_shell(line[1:].strip())
    elif line.startswith(NOTE_MARK) and len(line) > 1:
        session.add_note(line[1:].strip())
    else:
        session.ask(line)
    return True


def chat(session: Session) -> None:
    terminal = session.terminal
    terminal.note(f"arty-code {__version__} · {session.client.model} · {session.gate.mode} · {session.workspace.root}", "cyan")
    if session.gate.mode == permissions.ALLOW_ALL:
        terminal.note("allow-all: nothing is asked first. Use it only in a folder you can throw away.", "red")
    terminal.note("Ask for anything in this project. /help for commands and shortcuts, Ctrl-D to leave.")
    while True:
        try:
            line = read_request()
        except EOFError:
            terminal.end_line()
            return
        except KeyboardInterrupt:
            terminal.note("")
            continue
        if not line:
            continue
        try:
            if not handle(session, line):
                return
        except KeyboardInterrupt:
            terminal.note("Stopped.", "red")
        except EOFError:
            terminal.end_line()
            return


def ready_settings(arguments, terminal: Terminal):
    config = settings.load()
    if not config.is_ready() and is_interactive():
        terminal.note("First run: arty-code needs your Arty brain's address and API key.", "cyan")
        setup(terminal)
        config = settings.load()
    if arguments.model:
        config = replace(config, model=arguments.model)
    return config


def saved_conversation(arguments, store: sessions.Store, project: Path, terminal: Terminal):
    if arguments.resume:
        found = store.load(project, arguments.resume)
        if found is None:
            terminal.note(f"No conversation {arguments.resume} in this project; starting a new one.", "red")
        return found or store.start(project)
    if arguments.carry_on:
        return store.latest(project) or store.start(project)
    return store.start(project)


def main(argv=None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    terminal = Terminal()
    try:
        if arguments.words == ["setup"]:
            return setup(terminal)
        config = ready_settings(arguments, terminal)
        if not config.is_ready():
            terminal.note("arty-code isn't connected to an Arty yet. Run `arty-code setup` first.", "red")
            return NOT_SET_UP
        workspace = tools.Workspace(arguments.project or Path.cwd())
        try:
            return run_session(arguments, config, workspace, terminal)
        finally:
            workspace.stop_jobs()
    except (brain.BrainError, settings.SettingsError) as error:
        terminal.note(str(error), "red")
        return FAILED


def run_session(arguments, config, workspace, terminal: Terminal) -> int:
    mode = permissions.AUTO_EDIT if arguments.auto_edit else arguments.permission_mode
    gate = permissions.Gate(workspace, terminal.ask, mode=mode)
    store = sessions.Store(settings.config_dir() / SESSIONS_FOLDER)
    saved = saved_conversation(arguments, store, workspace.root, terminal)
    client = brain.Brain(config)
    if saved.model and not arguments.model:
        client.model = saved.model
    trusted = project_notes_allowed(workspace, settings.config_dir(), terminal)
    session = Session(client, workspace, gate, terminal, saved, project_trusted=trusted)
    once = arguments.once or " ".join(arguments.words)
    if once:
        return 0 if handle(session, once) is not False and session.messages[-1]["role"] == "assistant" else FAILED
    chat(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
