import glob
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
RUNS = Path(os.environ.get("RUNNER_RUNS", "/srv/runs"))
GO_WARM_CACHE = Path(os.environ.get("RUNNER_GO_CACHE", "/var/cache/code-runner/go"))
SANDBOX_USER = os.environ.get("RUNNER_USER", "sandbox")
PARALLEL_RUNS = int(os.environ.get("RUNNER_PARALLEL", "2"))
QUEUE_SECONDS = 30
MAX_BODY_BYTES = 256_000
MAX_CODE_CHARS = 100_000
OUTPUT_BYTES = 6_000
EXCERPT_CHARS = 1_800
STDOUT_CHARS = 4_000
SANITIZER_REPORT = re.compile(r"^==\d+==ERROR: \w+Sanitizer", re.M)
GRACE_SECONDS = 10
VARIANT = re.compile(r"^[\w.+# -]{0,24}$")
SUMMARY_RESULT = re.compile(r"Finished with result: ([\w-]+)")
SUMMARY_EXIT = re.compile(r"Main processes terminated with: code=(\w+), status=(\d+)")
CPU_LIMIT_SIGNALS = (9, 24)
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
    f"WorkingDirectory={WORK}", "StandardInput=null",
)
SANDBOX_ENVIRONMENT = {
    "HOME": WORK, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1", "GOPATH": f"{WORK}/.gopath", "GOTOOLCHAIN": "local", "GOPROXY": "off",
    "GOFLAGS": "-mod=mod", "CGO_ENABLED": "0", "GOTELEMETRY": "off", "DOTNET_CLI_HOME": WORK, "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_ROOT": "/opt/dotnet",
    "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off", "NO_COLOR": "1",
}
RUN_STEPS = (languages.RUN, languages.TESTS)


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


def request_fields(payload) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise BadRequest("send a JSON object")
    language, code, variant = payload.get("language"), payload.get("code"), payload.get("version", "")
    if language not in languages.PLANS:
        raise BadRequest(f"unknown language {str(language)[:40]!r}; known: {', '.join(sorted(languages.PLANS))}")
    if not isinstance(code, str) or not code.strip():
        raise BadRequest("code must be a non-empty string")
    if len(code) > MAX_CODE_CHARS:
        raise BadRequest(f"code is longer than {MAX_CODE_CHARS} characters")
    if not isinstance(variant, str) or not VARIANT.match(variant):
        raise BadRequest("version must be a short plain string")
    return language, code, variant


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
    if step.counts is not None:
        return any(step.counts.search(line) for line in output.splitlines())
    return result.result != "success"


def environment_note(language: str) -> str:
    if language == "sql":
        return "not run to the end: it queries tables it doesn't create"
    return "not run to the end: it needs files, network or input the sandbox doesn't have"


def run_trouble_note(language: str, code: str, step: languages.Step, result: StepResult, output: str) -> str:
    rules = languages.rules_for(language)
    if result.result == "timeout":
        return (f"{step.name} stopped after {step.seconds} s (it may wait for input, serve requests or loop forever), "
                "so it wasn't run to the end")
    if result.result == "oom-kill":
        return f"{step.name} stopped: it used more memory than the sandbox allows"
    missing = languages.missing_names(language, output)
    if missing:
        return languages.missing_library_note("library", missing)
    if step.name not in RUN_STEPS:
        return ""
    if rules.environment.search(output):
        return environment_note(language)
    if languages.reads_input_or_arguments(language, code):
        return "not run to the end: it reads input or command-line arguments, which the sandbox doesn't give it"
    crashed = bool(rules.crash.search(output)) or (rules.signals_crash and result.result in ("signal", "core-dump"))
    if step.only_syntax_errors_fail:
        crashed = bool(rules.crash.search(output))
    if not crashed:
        return f"ran, but exited with code {result.exit_code} in the sandbox"
    return ""


def printed(result: StepResult) -> dict:
    stdout = cleaned(result.stdout)
    if len(stdout) <= STDOUT_CHARS:
        return {"stdout": stdout}
    return {"stdout": stdout[:STDOUT_CHARS], "stdout_cut": True}


def judged(language: str, code: str, plan: languages.Plan, results: list) -> dict:
    checked, notes, shown = [], list(plan.notes), {}
    for step, result in zip(plan.steps, results):
        output = kept_lines(step, cleaned(result.output))
        if not step_failed(step, result, output):
            checked.append(step.name)
            if step.name == languages.RUN and result.result == "success":
                shown = printed(result)
            continue
        note = run_trouble_note(language, code, step, result, output)
        if note:
            notes.append(note)
            break
        return {"status": "failed", "step": step.name, "problem": excerpt(output, step.tail) or f"exit code {result.exit_code}",
                "checked": checked, "notes": notes}
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


def sandbox_command(unit: str, run: Path, step: languages.Step, plan: languages.Plan, go_cache: Path | None) -> list[str]:
    properties = [*SANDBOX_PROPERTIES, f"BindPaths={run / 'work'}:{WORK}", f"RuntimeMaxSec={step.seconds}",
                  f"LimitCPU={step.seconds}", f"StandardOutput=truncate:{run / 'out'}", f"StandardError=truncate:{run / 'err'}"]
    environment = {**SANDBOX_ENVIRONMENT, **plan.environment, "GOCACHE": GO_CACHE_MOUNT if go_cache else f"{WORK}/.cache/go"}
    if go_cache:
        properties.append(f"BindPaths={go_cache}:{GO_CACHE_MOUNT}")
    command = ["systemd-run", "--wait", "--collect", f"--unit={unit}"]
    command += [part for value in properties for part in ("-p", value)]
    command += [part for name, value in environment.items() for part in ("-E", f"{name}={value}")]
    return command + ["--", *step.command]


def stop_unit(unit: str) -> None:
    for action in (["kill", "--signal=KILL"], ["stop"]):
        subprocess.run(["systemctl", *action, unit], capture_output=True, timeout=GRACE_SECONDS)


def run_step(run: Path, step: languages.Step, plan: languages.Plan, go_cache: Path | None) -> StepResult:
    unit = f"code-run-{uuid.uuid4().hex[:12]}.service"
    started = time.monotonic()
    try:
        finished = subprocess.run(sandbox_command(unit, run, step, plan, go_cache), capture_output=True, text=True,
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


def checked_code(language: str, code: str, variant: str) -> dict:
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
            results.append(run_step(run, step, plan, go_cache))
            if step_failed(step, results[-1], kept_lines(step, cleaned(results[-1].output))):
                break
    finally:
        removed_run(run, go_cache)
    verdict = judged(language, code, plan, results)
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
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            raise BadRequest(f"the body must be between 1 and {MAX_BODY_BYTES} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise BadRequest(f"the body isn't JSON: {error}") from error

    def do_POST(self):
        if self.path != "/check":
            self.reply(404, {"error": "not found"})
            return
        try:
            language, code, variant = request_fields(self.payload())
        except BadRequest as error:
            self.reply(400, {"error": str(error)})
            return
        if not self.slots.acquire(timeout=QUEUE_SECONDS):
            self.reply(503, {"error": "the runner is busy, try again shortly"})
            return
        try:
            verdict = checked_code(language, code, variant)
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
    host, _, port = LISTEN.rpartition(":")
    RUNS.mkdir(parents=True, exist_ok=True)
    cleared_leftover_runs()
    Handler.slots = threading.BoundedSemaphore(PARALLEL_RUNS)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    log.info("code runner listening on %s", LISTEN)
    server.serve_forever()


if __name__ == "__main__":
    serve()
