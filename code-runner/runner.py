import base64
import glob
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import languages

log = logging.getLogger("code-runner")

LISTEN = os.environ.get("RUNNER_LISTEN", "127.0.0.1:8790")
TOKEN = os.environ.get("RUNNER_TOKEN", "")
MIN_TOKEN_CHARS = 32
BEARER = "Bearer "
RUNS = Path(os.environ.get("RUNNER_RUNS", "/srv/runs"))
GO_WARM_CACHE = Path(os.environ.get("RUNNER_GO_CACHE", "/var/cache/code-runner/go"))
SANDBOX_USER = os.environ.get("RUNNER_USER", "sandbox")
PARALLEL_RUNS = int(os.environ.get("RUNNER_PARALLEL", "2"))
QUEUE_SECONDS = 30
MAX_BODY_BYTES = 256_000
MAX_CODE_CHARS = 100_000
MAX_INPUT_CHARS = 10_000
MAX_ARGUMENTS = 20
MAX_ARGUMENT_CHARS = 200
OUTPUT_BYTES = 6_000
EXCERPT_CHARS = 1_800
STDOUT_CHARS = 4_000
SANITIZER_REPORT = re.compile(r"^==\d+==ERROR: \w+Sanitizer", re.M)
GRACE_SECONDS = 10
VARIANT = re.compile(r"^[\w.+# -]{0,24}$")
SUMMARY_RESULT = re.compile(r"Finished with result: ([\w-]+)")
SUMMARY_EXIT = re.compile(r"Main processes terminated with: code=(\w+), status=(\d+)")
CPU_LIMIT_SIGNALS = (9, 24)
UNFINISHED_RESULTS = frozenset({"timeout", "oom-kill", "unknown"})
WORK = "/work"
GO_CACHE_MOUNT = "/cache/go"
SANDBOX_PROPERTIES = (
    f"User={SANDBOX_USER}", f"Group={SANDBOX_USER}", "PrivateNetwork=yes", "PrivateDevices=yes", "PrivateIPC=yes",
    "ProtectSystem=strict", "ProtectHome=tmpfs", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes", "ProtectControlGroups=yes", "ProtectClock=yes", "ProtectHostname=yes", "ProtectProc=invisible",
    "NoNewPrivileges=yes", "RestrictSUIDSGID=yes", "LockPersonality=yes", "RestrictRealtime=yes", "RestrictNamespaces=yes",
    "CapabilityBoundingSet=", "AmbientCapabilities=", "KeyringMode=private", "UMask=0077",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    "SystemCallFilter=~@mount @reboot @swap @module @raw-io @clock @obsolete",
    "TemporaryFileSystem=/tmp:mode=1777,size=128M /var/tmp:mode=1777,size=16M /srv:ro /var/cache:ro",
    "MemoryMax=768M", "MemorySwapMax=0", "TasksMax=256", "LimitNOFILE=1024", "LimitFSIZE=64M", "LimitCORE=0",
    f"WorkingDirectory={WORK}",
)
NO_STANDARD_INPUT = "StandardInput=null"
READS_INPUT_NOTE = "it reads input or command-line arguments, and the sandbox gave it none"
GIVEN_INPUT_NOTE = "the input from the question didn't fit the way it reads input"
NEUTRAL_SHAPES = (("1", 8, 1), ("1", 20, 2), ("1,2", 20, 3))
MADE_UP_INPUT_NOTE = "Run again with made-up input ({shape}), it failed like this:\n"
RAN_OUT_OF_INPUT = re.compile(r"EOFError|EOF when reading|NoSuchElementException")
MISSING_VALUE = re.compile(r"'NoneType' object|\bundefined\b|\bnull\b|\bnil\b")
CRASH_RESULTS = frozenset({"exit-code", "signal", "core-dump"})
VOLATILE_DETAIL = re.compile(r"0x[0-9a-fA-F]+|==\d+==|\bpid \d+|\btid \d+")
ERROR_LINE = re.compile(r"Error\b|Exception\b|error:|runtime error|panic|SUMMARY:|fatal|Traceback", re.I)
NEEDS_INPUT = "needs_input"
QUERY_LANGUAGES = frozenset({"sql"})
SANDBOX_ENVIRONMENT = {
    "HOME": WORK, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1", "GOPATH": f"{WORK}/.gopath", "GOTOOLCHAIN": "local", "GOPROXY": "off",
    "GOFLAGS": "-mod=mod", "CGO_ENABLED": "0", "GOTELEMETRY": "off", "DOTNET_CLI_HOME": WORK, "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_ROOT": "/opt/dotnet",
    "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off", "NO_COLOR": "1",
}
RUN_STEPS = (languages.RUN, languages.TESTS)


@dataclass(frozen=True)
class RunInput:
    stdin: str = ""
    arguments: tuple = ()

    def __bool__(self) -> bool:
        return bool(self.stdin or self.arguments)


NO_INPUT = RunInput()


@dataclass(frozen=True)
class Trouble:
    note: str = ""
    needs_input: bool = False


FAILURE = Trouble()


@dataclass(frozen=True)
class StepResult:
    result: str
    exit_code: int
    output: str
    seconds: float
    stdout: str = ""


class BadRequest(ValueError):
    pass


def csharp_tools() -> dict:
    compilers = sorted(glob.glob(languages.DOTNET_SDK_GLOB))
    references = sorted(glob.glob(languages.DOTNET_REFS_GLOB))
    if not compilers or not references:
        return {}
    newest_pack = Path(references[-1]).parent
    return {"compiler": compilers[-1], "references": tuple(sorted(str(path) for path in newest_pack.glob("*.dll")))}


def request_fields(payload) -> tuple[str, str, str, bool]:
    if not isinstance(payload, dict):
        raise BadRequest("send a JSON object")
    language, code, variant = payload.get("language"), payload.get("code"), payload.get("version", "")
    input_excuse = payload.get("input_excuse", True)
    if language not in languages.PLANS:
        raise BadRequest(f"unknown language {str(language)[:40]!r}; known: {', '.join(sorted(languages.PLANS))}")
    if not isinstance(code, str) or not code.strip():
        raise BadRequest("code must be a non-empty string")
    if len(code) > MAX_CODE_CHARS:
        raise BadRequest(f"code is longer than {MAX_CODE_CHARS} characters")
    try:
        code.encode()
    except UnicodeEncodeError as error:
        raise BadRequest(f"code must be plain text: {error.reason} at character {error.start}") from error
    if not isinstance(variant, str) or not VARIANT.match(variant):
        raise BadRequest("version must be a short plain string")
    if not isinstance(input_excuse, bool):
        raise BadRequest("input_excuse must be true or false")
    return language, code, variant, input_excuse


def is_writable_text(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


def given_input(payload: dict) -> RunInput:
    stdin, arguments = payload.get("stdin", ""), payload.get("args", [])
    if not isinstance(stdin, str) or len(stdin) > MAX_INPUT_CHARS or not is_writable_text(stdin):
        raise BadRequest(f"stdin must be text of at most {MAX_INPUT_CHARS} characters")
    if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS:
        raise BadRequest(f"args must be a list of at most {MAX_ARGUMENTS} strings")
    for argument in arguments:
        if not isinstance(argument, str) or len(argument) > MAX_ARGUMENT_CHARS or not is_writable_text(argument):
            raise BadRequest(f"each of args must be text of at most {MAX_ARGUMENT_CHARS} characters")
    return RunInput(stdin, tuple(arguments))


def cleaned(output: str) -> str:
    return output.replace(f"{WORK}/", "").replace("\x00", "")


def excerpt(output: str, from_end: bool) -> str:
    output = output.strip()
    if len(output) <= EXCERPT_CHARS:
        return output
    sanitizer_report = SANITIZER_REPORT.search(output)
    if sanitizer_report:
        return output[sanitizer_report.start():][:EXCERPT_CHARS] + "\n..."
    return "...\n" + output[-EXCERPT_CHARS:] if from_end else output[:EXCERPT_CHARS] + "\n..."


def kept_lines(step: languages.Step, output: str) -> str:
    lines = output.splitlines()
    if step.drops is not None:
        lines = [line for line in lines if not step.drops.search(line)]
    return "\n".join(lines)


def step_failed(step: languages.Step, result: StepResult, output: str) -> bool:
    if result.result in UNFINISHED_RESULTS:
        return True
    if step.counts is not None:
        return any(step.counts.search(line) for line in output.splitlines())
    return result.result != "success"


def environment_note(language: str) -> str:
    if language == "sql":
        return "it queries tables it doesn't create"
    return "it needs files, network or input the sandbox doesn't have"


def crashed(rules: languages.Rules, step: languages.Step, result: StepResult, output: str) -> bool:
    if step.only_syntax_errors_fail:
        return bool(rules.crash.search(output))
    signalled = rules.signals_crash and result.result in ("signal", "core-dump")
    return bool(SANITIZER_REPORT.search(output) or rules.crash.search(output)) or signalled


def error_signature(language: str, output: str) -> tuple[str, int | None]:
    lines = [line.strip() for line in VOLATILE_DETAIL.sub("", output).splitlines() if line.strip()]
    errors = [line for line in lines if ERROR_LINE.search(line)]
    return (errors[-1] if errors else (lines[-1] if lines else "")), languages.crash_line_number(language, output)


def no_input_causes(language: str, output: str) -> bool:
    return languages.is_an_error_no_input_causes(language, error_signature(language, output)[0], output)


def wanted_only_input(language: str, code: str, step: languages.Step, result: StepResult, output: str) -> bool:
    if result.result in UNFINISHED_RESULTS:
        return False
    if result.result == "success" or not crashed(languages.rules_for(language), step, result, output):
        return True
    if RAN_OUT_OF_INPUT.search(output):
        return True
    return languages.crashed_at_a_read(language, code, output) and not no_input_causes(language, output)


def rerun_wanted_only_input(language: str, code: str, step: languages.Step, reruns: tuple) -> bool:
    if not reruns:
        return False
    _, last = reruns[-1]
    return wanted_only_input(language, code, step, last, kept_lines(step, cleaned(last.output)))


def input_reached_the_program(language: str, code: str, given: RunInput) -> bool:
    by_stdin = bool(given.stdin) and languages.reads_standard_input(language, code)
    by_arguments = bool(given.arguments) and languages.reads_arguments(language, code)
    return by_stdin or by_arguments


def input_trouble(language: str, code: str, step: languages.Step, result: StepResult, output: str,
                  given: RunInput, reruns: tuple) -> Trouble | None:
    rules = languages.rules_for(language)
    if not crashed(rules, step, result, output):
        return Trouble(READS_INPUT_NOTE if not given else GIVEN_INPUT_NOTE, needs_input=True)
    if no_input_causes(language, output):
        return None
    if given:
        return None if input_reached_the_program(language, code, given) else Trouble(GIVEN_INPUT_NOTE, needs_input=True)
    if wanted_only_input(language, code, step, result, output):
        return Trouble(READS_INPUT_NOTE, needs_input=True)
    return made_up_input_trouble(language, code, step, output, reruns)


def same_crash(language: str, step: languages.Step, signature: tuple, result: StepResult) -> bool:
    return error_signature(language, kept_lines(step, cleaned(result.output))) == signature


def crash_is_not_excused(language: str, code: str, output: str, signature: tuple) -> bool:
    return not languages.stops_on_purpose(language, code, output) and not MISSING_VALUE.search(signature[0])


def crash_repeats_for_every_input(language: str, code: str, step: languages.Step, output: str, reruns: tuple) -> bool:
    if len(reruns) < len(NEUTRAL_SHAPES):
        return False
    signature = error_signature(language, output)
    repeats = all(same_crash(language, step, signature, result) for _, result in reruns)
    return repeats and crash_is_not_excused(language, code, output, signature)


def last_shape_ran_to_the_end(reruns: tuple) -> bool:
    return bool(reruns) and reruns[-1][1].result == "success"


def code_error_repeats_on_made_up_input(language: str, code: str, step: languages.Step, output: str, reruns: tuple) -> bool:
    signature = error_signature(language, output)
    if not languages.names_an_error_no_input_causes(language, signature[0]):
        return False
    repeats = any(same_crash(language, step, signature, result) for _, result in reruns)
    return repeats and not last_shape_ran_to_the_end(reruns) and crash_is_not_excused(language, code, output, signature)


def made_up_input_found_a_code_error(language: str, code: str, step: languages.Step, last: StepResult) -> bool:
    output = kept_lines(step, cleaned(last.output))
    error_line = error_signature(language, output)[0]
    return no_input_causes(language, output) and not languages.made_up_input_could_cause(language, code, error_line)


def made_up_input_trouble(language: str, code: str, step: languages.Step, output: str, reruns: tuple) -> Trouble | None:
    if not reruns:
        return None
    if code_error_repeats_on_made_up_input(language, code, step, output, reruns):
        return None
    if rerun_wanted_only_input(language, code, step, reruns):
        return Trouble(READS_INPUT_NOTE, needs_input=True)
    _, last = reruns[-1]
    if made_up_input_found_a_code_error(language, code, step, last):
        return None
    if crash_repeats_for_every_input(language, code, step, output, reruns):
        return None
    return Trouble(READS_INPUT_NOTE, needs_input=True)


def run_trouble(language: str, code: str, step: languages.Step, result: StepResult, output: str,
                input_excuse: bool = True, given: RunInput = NO_INPUT, reruns: tuple = ()) -> Trouble:
    rules = languages.rules_for(language)
    if result.result == "timeout":
        return Trouble(f"{step.name} stopped after {step.seconds} s (it may wait for input, serve requests or loop forever), "
                       "so it wasn't run to the end")
    if result.result == "oom-kill":
        return Trouble(f"{step.name} stopped: it used more memory than the sandbox allows")
    missing = languages.missing_names(language, output)
    if missing:
        return Trouble(languages.missing_library_note("library", missing))
    if step.name not in RUN_STEPS:
        return FAILURE
    if input_excuse and step.name == languages.RUN and languages.reads_input_or_arguments(language, code):
        trouble = input_trouble(language, code, step, result, output, given, reruns)
        if trouble is not None:
            return trouble
    if rules.environment.search(output):
        return Trouble(environment_note(language), needs_input=language not in QUERY_LANGUAGES)
    if not crashed(rules, step, result, output):
        return Trouble(f"ran, but exited with code {result.exit_code} in the sandbox")
    return FAILURE


def printed(result: StepResult) -> dict:
    stdout = cleaned(result.stdout)
    if len(stdout) <= STDOUT_CHARS:
        return {"stdout": stdout}
    return {"stdout": stdout[:STDOUT_CHARS], "stdout_cut": True}


def shape_described(shape: RunInput) -> str:
    lines = shape.stdin.splitlines()
    described = f"{len(lines)} lines of '{lines[0]}' on stdin" if lines else "no stdin"
    if shape.arguments:
        described += ", arguments " + " ".join(shape.arguments)
    return described


def first_error_decided(language: str, code: str, step: languages.Step, output: str, reruns: tuple) -> bool:
    _, last = reruns[-1]
    return same_crash(language, step, error_signature(language, output), last) or \
        code_error_repeats_on_made_up_input(language, code, step, output, reruns)


def failed_differently(language: str, code: str, step: languages.Step, output: str, reruns: tuple) -> str:
    if not reruns:
        return ""
    shape, last = reruns[-1]
    if last.result == "success" or first_error_decided(language, code, step, output, reruns):
        return ""
    rerun_output = kept_lines(step, cleaned(last.output))
    return MADE_UP_INPUT_NOTE.format(shape=shape_described(shape)) + (excerpt(rerun_output, step.tail) or
                                                                       f"exit code {last.exit_code}")


def judged(language: str, code: str, plan: languages.Plan, results: list, input_excuse: bool = True,
           given: RunInput = NO_INPUT, reruns: tuple = ()) -> dict:
    checked, notes, shown = [], list(plan.notes), {}
    for step, result in zip(plan.steps, results):
        output = kept_lines(step, cleaned(result.output))
        if not step_failed(step, result, output):
            checked.append(step.name)
            if step.name == languages.RUN and result.result == "success":
                shown = printed(result)
            continue
        trouble = run_trouble(language, code, step, result, output, input_excuse, given, reruns)
        problem = failed_differently(language, code, step, output, reruns) or excerpt(output, step.tail) or \
            f"exit code {result.exit_code}"
        if trouble.needs_input:
            return {"status": NEEDS_INPUT, "step": step.name, "problem": problem, "checked": checked,
                    "notes": notes + [trouble.note]}
        if trouble.note:
            notes.append(trouble.note)
            break
        return {"status": "failed", "step": step.name, "problem": problem, "checked": checked, "notes": notes}
    return {"status": "passed" if checked else "unchecked", "checked": checked, "notes": notes, **shown}


def summary_of(text: str) -> tuple[str, int]:
    result = SUMMARY_RESULT.search(text)
    exit_code = SUMMARY_EXIT.search(text)
    outcome = result.group(1) if result else "unknown"
    number = int(exit_code.group(2)) if exit_code else -1
    if outcome == "signal" and number in CPU_LIMIT_SIGNALS:
        return "timeout", number
    return outcome, number


def read_capped(path: Path) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= OUTPUT_BYTES * 2:
            return handle.read().decode(errors="replace")
        head = handle.read(OUTPUT_BYTES).decode(errors="replace")
        handle.seek(size - OUTPUT_BYTES)
        tail = handle.read().decode(errors="replace")
    return f"{head}\n...[{size - 2 * OUTPUT_BYTES} bytes cut]...\n{tail}"


def standard_input(step: languages.Step, given: RunInput) -> str:
    if step.name != languages.RUN or not given.stdin:
        return NO_STANDARD_INPUT
    return "StandardInputData=" + base64.b64encode(given.stdin.encode()).decode()


def step_command(step: languages.Step, given: RunInput) -> tuple:
    return (*step.command, *given.arguments) if step.name == languages.RUN else step.command


def sandbox_command(unit: str, run: Path, step: languages.Step, plan: languages.Plan, go_cache: Path | None,
                    given: RunInput = NO_INPUT) -> list[str]:
    properties = [*SANDBOX_PROPERTIES, standard_input(step, given), f"BindPaths={run / 'work'}:{WORK}",
                  f"RuntimeMaxSec={step.seconds}", f"LimitCPU={step.seconds}", f"StandardOutput=truncate:{run / 'out'}",
                  f"StandardError=truncate:{run / 'err'}"]
    environment = {**SANDBOX_ENVIRONMENT, **plan.environment, "GOCACHE": GO_CACHE_MOUNT if go_cache else f"{WORK}/.cache/go"}
    if go_cache:
        properties.append(f"BindPaths={go_cache}:{GO_CACHE_MOUNT}")
    command = ["systemd-run", "--wait", "--collect", "--expand-environment=no", f"--unit={unit}"]
    command += [part for value in properties for part in ("-p", value)]
    command += [part for name, value in environment.items() for part in ("-E", f"{name}={value}")]
    return command + ["--", *step_command(step, given)]


def stop_unit(unit: str) -> None:
    for action in (["kill", "--signal=KILL"], ["stop"]):
        subprocess.run(["systemctl", *action, unit], capture_output=True, timeout=GRACE_SECONDS)


def run_step(run: Path, step: languages.Step, plan: languages.Plan, go_cache: Path | None,
             given: RunInput = NO_INPUT) -> StepResult:
    unit = f"code-run-{uuid.uuid4().hex[:12]}.service"
    started = time.monotonic()
    try:
        finished = subprocess.run(sandbox_command(unit, run, step, plan, go_cache, given), capture_output=True, text=True,
                                  timeout=step.seconds + GRACE_SECONDS)
        result, exit_code = summary_of(finished.stderr)
    except subprocess.TimeoutExpired:
        stop_unit(unit)
        result, exit_code = "timeout", -1
    stdout = read_capped(run / "out")
    return StepResult(result, exit_code, read_capped(run / "err") + stdout, round(time.monotonic() - started, 2), stdout)


def prepared_run(plan: languages.Plan) -> Path:
    run = RUNS / uuid.uuid4().hex
    work = run / "work"
    work.mkdir(parents=True, mode=0o700)
    for name, text in plan.files.items():
        (work / name).write_text(text)
    shutil.chown(work, SANDBOX_USER, SANDBOX_USER)
    for path in work.iterdir():
        shutil.chown(path, SANDBOX_USER, SANDBOX_USER)
    return run


def mounted_go_cache(run: Path) -> Path:
    upper, scratch, merged = run / "go-upper", run / "go-scratch", run / "go-cache"
    for folder in (upper, scratch, merged):
        folder.mkdir()
    shutil.chown(upper, SANDBOX_USER, SANDBOX_USER)
    options = f"lowerdir={GO_WARM_CACHE},upperdir={upper},workdir={scratch}"
    subprocess.run(["mount", "-t", "overlay", "overlay", "-o", options, str(merged)], check=True, capture_output=True,
                   timeout=GRACE_SECONDS)
    return merged


def removed_run(run: Path, go_cache: Path | None) -> None:
    if go_cache is not None:
        unmounted = subprocess.run(["umount", str(go_cache)], capture_output=True, text=True, timeout=GRACE_SECONDS)
        if unmounted.returncode != 0:
            log.error("couldn't unmount %s: %s; detaching it", go_cache, unmounted.stderr.strip())
            subprocess.run(["umount", "-l", str(go_cache)], capture_output=True, timeout=GRACE_SECONDS)
    shutil.rmtree(run, ignore_errors=False)


def neutral_inputs(language: str, code: str) -> tuple[RunInput, ...]:
    by_position = languages.reads_arguments_by_position(language, code)
    return tuple(RunInput("\n".join([line] * line_count), tuple(str(number) for number in range(1, argument_count + 1))
                          if by_position else ())
                 for line, line_count, argument_count in NEUTRAL_SHAPES)


def wants_a_neutral_rerun(language: str, code: str, step: languages.Step, result: StepResult, input_excuse: bool,
                          given: RunInput) -> bool:
    if given or not input_excuse or step.name != languages.RUN or result.result not in CRASH_RESULTS:
        return False
    if not languages.reads_input_or_arguments(language, code):
        return False
    output = kept_lines(step, cleaned(result.output))
    return not no_input_causes(language, output) and run_trouble(language, code, step, result, output, input_excuse,
                                                                 given) == FAILURE


def neutral_reruns(language: str, code: str, step: languages.Step, first: StepResult, run_with) -> tuple:
    first_signature = error_signature(language, kept_lines(step, cleaned(first.output)))
    reruns = []
    for shape in neutral_inputs(language, code):
        result = run_with(shape)
        reruns.append((shape, result))
        if result.result == "success" or error_signature(language, kept_lines(step, cleaned(result.output))) != first_signature:
            break
    return tuple(reruns)


def reruns_for(language: str, code: str, run: Path, plan: languages.Plan, results: list, go_cache: Path | None,
               input_excuse: bool, given: RunInput) -> tuple:
    if not results:
        return ()
    step = plan.steps[len(results) - 1]
    if not wants_a_neutral_rerun(language, code, step, results[-1], input_excuse, given):
        return ()
    return neutral_reruns(language, code, step, results[-1], lambda shape: run_step(run, step, plan, go_cache, shape))


def checked_code(language: str, code: str, variant: str, input_excuse: bool = True, given: RunInput = NO_INPUT) -> dict:
    extra = csharp_tools() if language == "csharp" else {}
    plan = languages.plan_for(language, code, variant, **extra)
    if plan.unchecked:
        return {"status": "unchecked", "tool": plan.tool, "checked": [], "notes": [plan.unchecked], "seconds": 0.0}
    started = time.monotonic()
    run = prepared_run(plan)
    go_cache = None
    try:
        if plan.go_cache and GO_WARM_CACHE.is_dir():
            go_cache = mounted_go_cache(run)
        results = []
        for step in plan.steps:
            results.append(run_step(run, step, plan, go_cache, given))
            if step_failed(step, results[-1], kept_lines(step, cleaned(results[-1].output))):
                break
        reruns = reruns_for(language, code, run, plan, results, go_cache, input_excuse, given)
    finally:
        removed_run(run, go_cache)
    verdict = judged(language, code, plan, results, input_excuse, given, reruns)
    verdict.update(tool=plan.tool, seconds=round(time.monotonic() - started, 2))
    return verdict


class Handler(BaseHTTPRequestHandler):
    server_version = "code-runner"
    slots: threading.BoundedSemaphore

    def log_message(self, format, *args):
        log.debug("%s %s", self.address_string(), format % args)

    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/health":
            self.reply(404, {"error": "not found"})
            return
        self.reply(200, {"ok": True, "languages": sorted(languages.PLANS)})

    def payload(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise BadRequest("Content-Length must be a whole number") from error
        if length <= 0 or length > MAX_BODY_BYTES:
            raise BadRequest(f"the body must be between 1 and {MAX_BODY_BYTES} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except ValueError as error:
            raise BadRequest(f"the body isn't UTF-8 JSON: {error}") from error

    def is_authorised(self) -> bool:
        given = self.headers.get("Authorization") or ""
        return given.startswith(BEARER) and hmac.compare_digest(given[len(BEARER):].encode(), TOKEN.encode())

    def do_POST(self):
        if self.path != "/check":
            self.reply(404, {"error": "not found"})
            return
        if not self.is_authorised():
            self.reply(401, {"error": "send the runner token as Authorization: Bearer <token>"})
            return
        try:
            payload = self.payload()
            language, code, variant, input_excuse = request_fields(payload)
            given = given_input(payload)
        except BadRequest as error:
            self.reply(400, {"error": str(error)})
            return
        if not self.slots.acquire(timeout=QUEUE_SECONDS):
            self.reply(503, {"error": "the runner is busy, try again shortly"})
            return
        try:
            verdict = checked_code(language, code, variant, input_excuse, given)
        except Exception as error:
            log.exception("checking %s code failed", language)
            self.reply(500, {"error": f"the runner failed: {type(error).__name__}: {error}"})
            return
        finally:
            self.slots.release()
        log.info("%s: %s in %.1f s (checked %s)", language, verdict["status"], verdict["seconds"], ",".join(verdict["checked"]))
        self.reply(200, verdict)


def cleared_leftover_runs() -> None:
    for run in RUNS.iterdir():
        go_cache = run / "go-cache"
        if go_cache.is_dir() and os.path.ismount(go_cache):
            subprocess.run(["umount", "-l", str(go_cache)], capture_output=True, timeout=GRACE_SECONDS)
        shutil.rmtree(run, ignore_errors=True)
        log.warning("removed a leftover run folder %s", run.name)


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(TOKEN) < MIN_TOKEN_CHARS:
        raise SystemExit(f"RUNNER_TOKEN must be set to at least {MIN_TOKEN_CHARS} characters (see code-runner/deploy.sh)")
    host, _, port = LISTEN.rpartition(":")
    RUNS.mkdir(parents=True, exist_ok=True)
    cleared_leftover_runs()
    Handler.slots = threading.BoundedSemaphore(PARALLEL_RUNS)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    log.info("code runner listening on %s", LISTEN)
    server.serve_forever()


if __name__ == "__main__":
    serve()
