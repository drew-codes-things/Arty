import re
import sys
from dataclasses import dataclass, field

RUN_SECONDS = 10
COMPILE_SECONDS = 30
CHECK_SECONDS = 15
PYTHON_VERSIONS = {"3.13": "/usr/bin/python3", "3.14": "/usr/local/bin/python3.14"}
DEFAULT_PYTHON = "3.13"
HELPERS = "/opt/code-runner/checks.py"
KOTLIN_STDLIB = "/opt/kotlinc/lib/kotlin-stdlib.jar"
DOTNET_SDK_GLOB = "/opt/dotnet/sdk/*/Roslyn/bincore/csc.dll"
DOTNET_REFS_GLOB = "/opt/dotnet/packs/Microsoft.NETCore.App.Ref/*/ref/net*/*.dll"
NODE_TYPES = "/opt/node/lib/node_modules/@types"
RUN_COMPILED = "/usr/bin/env"

SYNTAX, LINT, COMPILE, RUN, TESTS = "syntax", "lint", "compile", "run", "tests"

PYTHON_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([A-Za-z_]\w*)[\w.]*[ \t]+import|import[ \t]+([A-Za-z_][\w.]*(?:[ \t]*,[ \t]*[A-Za-z_][\w.]*)*))",
                           re.M)
PYTHON_MAIN_GUARD = re.compile(r"^if[ \t]+__name__[ \t]*==[ \t]*['\"]__main__['\"]", re.M)
PYTHON_TEST_FUNCTION = re.compile(r"^def[ \t]+(test_\w*)[ \t]*\([ \t]*\)", re.M)
PYFLAKES_ERROR = re.compile(r"undefined name|syntax error|invalid syntax|unexpected indent|unindent does not match|"
                            r"may be undefined|referenced before assignment|is not defined|'return' outside function|"
                            r"'yield' outside function|default 'except:' must be last|invalid print syntax|"
                            r"too many expressions in star-unpacking|duplicate argument", re.I)
JS_PACKAGE = re.compile(r"""(?:\brequire\s*\(\s*|\bimport\s*\(\s*|\bfrom\s+|^\s*import\s+)['"]([^'"]+)['"]""", re.M)
BROWSER_GLOBAL = re.compile(r"\b(?:document|window|localStorage|sessionStorage|navigator|alert|HTMLElement|customElements)\b")
TS_MISSING_PACKAGE = re.compile(r"error TS(?:2307|7016|2875):")
TS_ERROR_LINE = re.compile(r"error TS\d+:")
JSX_SYNTAX = re.compile(r"</[A-Za-z][\w.]*>|<[A-Za-z][\w.]*(?:\s[^<>]*)?/>")
NODE_BUILTINS = frozenset(
    "assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns domain events "
    "fs fs/promises http http2 https inspector module net os path path/posix path/win32 perf_hooks process punycode "
    "querystring readline readline/promises repl sqlite stream stream/promises stream/web string_decoder sys test "
    "timers timers/promises tls trace_events tty url util util/types v8 vm wasi worker_threads zlib".split())
C_MAIN = re.compile(r"\bint\s+main\s*\(")
CPP_SIGNAL = re.compile(r"\bstd::|#include\s*<(?:iostream|vector|string|map|algorithm|memory|unordered_map|sstream|fstream)>|"
                        r"\bclass\s+\w+\s*[:{]|\btemplate\s*<|\bnamespace\s+\w+|\bcout\b|\bcin\b")
RUST_MAIN = re.compile(r"\bfn\s+main\s*\(")
RUST_TEST = re.compile(r"#\[(?:test|cfg\(test\))\]")
RUST_PATH_ROOT = re.compile(r"(?<![\w:.])([a-z_][a-z0-9_]*)::(?!<)")
RUST_USE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+(?:::)?([^;]+);", re.M)
RUST_WORD = re.compile(r"[A-Za-z_]\w*")
RUST_EXTERN = re.compile(r"\bextern\s+crate\s+(\w+)")
RUST_MODULE = re.compile(r"\bmod\s+(\w+)")
RUST_LOCAL_ROOTS = frozenset("std core alloc crate self super i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 "
                             "str char bool".split())
GO_PACKAGE = re.compile(r"^\s*package\s+(\w+)", re.M)
GO_MAIN = re.compile(r"^\s*func\s+main\s*\(\s*\)", re.M)
GO_TEST = re.compile(r"^\s*func\s+(?:Test|Benchmark|Example)\w*\s*\(", re.M)
GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\(([^)]*)\)", re.M)
GO_IMPORT_LINE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.M)
GO_QUOTED = re.compile(r'"([^"]+)"')
JAVA_PUBLIC_CLASS = re.compile(r"^\s*public\s+(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\s+(\w+)", re.M)
JAVA_CLASS = re.compile(r"\b(?:class|interface|enum|record)\s+(\w+)")
JAVA_MAIN = re.compile(r"\bstatic\s+void\s+main\s*\(")
JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)
JAVA_STANDARD = ("java.", "javax.", "jdk.")
JAVA_INSTANCE_MAIN = re.compile(r"^\s*void\s+main\s*\(\s*\)", re.M)
KOTLIN_MAIN = re.compile(r"^\s*fun\s+main\s*\(", re.M)
KOTLIN_STANDARD = ("kotlin.", "java.", "javax.")
CSHARP_USING = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)
CSHARP_MAIN = re.compile(r"\bstatic\s+(?:async\s+)?(?:void|int|Task(?:<int>)?)\s+Main\s*\(")
CSHARP_DECLARATION = re.compile(r"^\s*(?:(?:public|internal|private|protected|static|sealed|abstract|partial|readonly|record)\s+)*"
                                r"(?:class|struct|interface|enum|record|namespace)\b|^\s*using\b|^\s*\[|^\s*$|^\s*//", re.M)
SQL_CREATED_TABLE = re.compile(r"\bcreate\s+(?:temp(?:orary)?\s+)?(?:table|view)\s+(?:if\s+not\s+exists\s+)?[\"`\[]?(\w+)", re.I)
SQL_OTHER_DIALECT = re.compile(r"\bauto_increment\b|\bserial\b|\bilike\b|::\w|\btop\s+\d|\bnvarchar\b|\bengine\s*=|\$\d+|"
                               r"\bdate_sub\b|\binterval\b|\bnow\s*\(\s*\)|\bidentity\s*\(|\bgetdate\s*\(|\bvarchar2\b|"
                               r"\bdeclare\s+@|\bunsigned\b|\bon\s+duplicate\s+key\b|\bconcat\s*\(|\bextract\s*\(|\bsysdate\b",
                               re.I)
PLACEHOLDER = re.compile(r"\.\.\.|\u2026|\bYOUR_\w+|<your[\w -]*>", re.I)
SHELL_IGNORED_CHECKS = "SC2034,SC1090,SC1091"
NOT_INSTALLED_STANDARD_MODULES = frozenset({"tkinter", "turtle", "turtledemo", "idlelib"})
READS_INPUT_OR_ARGUMENTS = re.compile(
    r"\binput\s*\(|sys\.argv|sys\.stdin|argparse|process\.argv|process\.stdin|\breadline\b|\bprompt\s*\(|os\.Args|"
    r"os\.Stdin|\bscanf\s*\(|\bfgets\s*\(|\bgetchar\s*\(|\bcin\s*>>|std::cin|\bgetline\s*\(|std::env::args|env::args|"
    r"io::stdin|System\.in\b|\bargs\s*\[|Console\.Read|\breadLine\s*\(|\breadln\s*\(|\bgets\b|\bSTDIN\b|\bARGV\b|"
    r"\$argv|io\.read\b", re.M)
SHELL_READS_INPUT_OR_ARGUMENTS = re.compile(r"Read-Host|\$args\b|\$\{?[1-9]\b|\$@|\bread\s+(?:-\w+\s+)*\w+", re.M)
SHELL_LANGUAGES = frozenset({"bash", "powershell"})


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple
    seconds: int = RUN_SECONDS
    counts: re.Pattern | None = None
    drops: re.Pattern | None = None
    only_syntax_errors_fail: bool = False
    tail: bool = False


@dataclass(frozen=True)
class Plan:
    tool: str
    files: dict = field(default_factory=dict)
    steps: tuple = ()
    notes: tuple = ()
    unchecked: str = ""
    go_cache: bool = False
    environment: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rules:
    crash: re.Pattern
    environment: re.Pattern
    missing: re.Pattern | None = None
    signals_crash: bool = False


def named_list(names) -> str:
    names = list(dict.fromkeys(names))
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def missing_library_note(kind: str, names) -> str:
    return f"not run: it needs the {named_list(names)} {kind}{'s' if len(set(names)) > 1 else ''}, which the sandbox doesn't have"


def python_modules(code: str) -> list[str]:
    found = []
    for single, many in PYTHON_IMPORT.findall(code):
        names = [single] if single else [part.strip().split(".")[0] for part in many.split(",")]
        found += [name for name in names if name]
    return list(dict.fromkeys(found))


def missing_python_modules(code: str) -> list[str]:
    return [name for name in python_modules(code) if name not in sys.stdlib_module_names and name != "__future__"]


def python_plan(code: str, version: str = "") -> Plan:
    version = version if version in PYTHON_VERSIONS else DEFAULT_PYTHON
    python = PYTHON_VERSIONS[version]
    files = {"main.py": code}
    steps = [Step(SYNTAX, (python, "-m", "py_compile", "main.py"), CHECK_SECONDS),
             Step(LINT, (python, "-m", "pyflakes", "main.py"), CHECK_SECONDS, counts=PYFLAKES_ERROR)]
    missing = missing_python_modules(code)
    if missing:
        return Plan(f"Python {version}", files, tuple(steps), (missing_library_note("package", missing),))
    has_tests = PYTHON_TEST_FUNCTION.search(code) and not PYTHON_MAIN_GUARD.search(code)
    if has_tests:
        steps.append(Step(TESTS, (python, "-I", HELPERS, "pytests", "main.py"), RUN_SECONDS, tail=True))
    else:
        steps.append(Step(RUN, (python, "-I", "main.py"), RUN_SECONDS, tail=True))
    return Plan(f"Python {version}", files, tuple(steps))


def javascript_packages(code: str) -> list[str]:
    found = []
    for name in JS_PACKAGE.findall(code):
        if name.startswith((".", "/", "node:")) or name in NODE_BUILTINS:
            continue
        found.append(name.split("/")[0] if not name.startswith("@") else "/".join(name.split("/")[:2]))
    return list(dict.fromkeys(found))


def javascript_plan(code: str, version: str = "") -> Plan:
    files = {"main.js": code}
    syntax = Step(SYNTAX, ("/usr/local/bin/node", "--check", "main.js"), CHECK_SECONDS)
    if JSX_SYNTAX.search(code):
        return Plan("Node.js", files, unchecked="it's JSX, which needs a build step the sandbox doesn't have")
    missing = javascript_packages(code)
    if missing:
        return Plan("Node.js", files, (syntax,), (missing_library_note("package", missing),))
    if BROWSER_GLOBAL.search(code):
        return Plan("Node.js", files, (syntax,), ("not run: it's browser code, and the sandbox only has Node",))
    return Plan("Node.js", files, (syntax, Step(RUN, ("/usr/local/bin/node", "main.js"), RUN_SECONDS, tail=True)))


def typescript_plan(code: str, version: str = "") -> Plan:
    files = {"main.ts": code}
    if JSX_SYNTAX.search(code):
        return Plan("tsc and Node.js", files, unchecked="it's TSX, which needs React's type definitions the sandbox doesn't have")
    compile_step = Step(COMPILE, ("/usr/local/bin/tsc", "--strict", "--target", "es2022", "--module", "nodenext",
                                  "--moduleResolution", "nodenext", "--esModuleInterop", "--skipLibCheck", "--pretty", "false",
                                  "--types", "node", "--typeRoots", NODE_TYPES, "--outDir", "out", "main.ts"),
                        COMPILE_SECONDS, counts=TS_ERROR_LINE, drops=TS_MISSING_PACKAGE)
    missing = javascript_packages(code)
    if missing:
        return Plan("tsc and Node.js", files, (compile_step,), (missing_library_note("package", missing),))
    if BROWSER_GLOBAL.search(code):
        return Plan("tsc and Node.js", files, (compile_step,), ("not run: it's browser code, and the sandbox only has Node",))
    return Plan("tsc and Node.js", files, (compile_step, Step(RUN, ("/usr/local/bin/node", "out/main.js"), RUN_SECONDS, tail=True)))


def bash_plan(code: str, version: str = "") -> Plan:
    files = {"script.sh": code}
    steps = (Step(SYNTAX, ("/bin/bash", "-n", "script.sh"), CHECK_SECONDS),
             Step(LINT, ("/usr/bin/shellcheck", "-s", "bash", "-S", "warning", "-e", SHELL_IGNORED_CHECKS, "-f", "gcc",
                         "script.sh"), CHECK_SECONDS),
             Step(RUN, ("/bin/bash", "script.sh"), RUN_SECONDS, only_syntax_errors_fail=True, tail=True))
    return Plan("bash and ShellCheck", files, steps)


def c_family_plan(code: str, cpp: bool) -> Plan:
    source = "main.cpp" if cpp else "main.c"
    compiler = ("/usr/bin/g++", "-std=c++23") if cpp else ("/usr/bin/gcc", "-std=c17")
    flags = ("-Wall", "-Wextra", "-g", "-O0", "-fno-omit-frame-pointer", "-fsanitize=address,undefined",
             "-fno-sanitize-recover=all", "-pthread")
    tool = "g++" if cpp else "gcc"
    if not C_MAIN.search(code):
        return Plan(tool, {source: code}, (Step(COMPILE, (*compiler, *flags, "-c", source, "-o", "main.o"), COMPILE_SECONDS),),
                    ("not run: it has no main function",))
    build = Step(COMPILE, (*compiler, *flags, source, "-o", "main", "-lm"), COMPILE_SECONDS)
    return Plan(tool, {source: code}, (build, Step(RUN, (RUN_COMPILED, "/work/main"), RUN_SECONDS, tail=True)),
                environment={"ASAN_OPTIONS": "detect_leaks=0", "UBSAN_OPTIONS": "print_stacktrace=1"})


def c_plan(code: str, version: str = "") -> Plan:
    return c_family_plan(code, cpp=False)


def cpp_plan(code: str, version: str = "") -> Plan:
    return c_family_plan(code, cpp=True)


def rust_crates(code: str) -> list[str]:
    local = set(RUST_MODULE.findall(code)) | RUST_LOCAL_ROOTS
    uses = RUST_USE.findall(code)
    use_roots = [RUST_WORD.match(path.strip()).group(0) for path in uses if RUST_WORD.match(path.strip())]
    imported = {word for path in uses for word in RUST_WORD.findall(path)}
    path_roots = RUST_PATH_ROOT.findall(RUST_USE.sub("", code))
    roots = RUST_EXTERN.findall(code) + use_roots + [name for name in path_roots if name not in imported]
    return [name for name in dict.fromkeys(roots) if name not in local]


def rust_plan(code: str, version: str = "") -> Plan:
    files = {"main.rs": code}
    crates = rust_crates(code)
    if crates:
        return Plan("rustc", files, unchecked=missing_library_note("crate", crates).replace("not run: it", "it"))
    compiler = ("/usr/local/bin/rustc", "--edition", "2024", "-C", "debuginfo=0")
    if RUST_TEST.search(code):
        return Plan("rustc", files, (Step(COMPILE, (*compiler, "--test", "main.rs", "-o", "tests"), COMPILE_SECONDS),
                                    Step(TESTS, (RUN_COMPILED, "/work/tests"), RUN_SECONDS, tail=True)))
    if not RUST_MAIN.search(code):
        return Plan("rustc", files, (Step(COMPILE, (*compiler, "--crate-type", "lib", "main.rs"), COMPILE_SECONDS),),
                    ("not run: it has no main function",))
    return Plan("rustc", files, (Step(COMPILE, (*compiler, "main.rs", "-o", "main"), COMPILE_SECONDS),
                                Step(RUN, (RUN_COMPILED, "/work/main"), RUN_SECONDS, tail=True)))


def go_imports(code: str) -> list[str]:
    found = GO_IMPORT_LINE.findall(code)
    for block in GO_IMPORT_BLOCK.findall(code):
        found += GO_QUOTED.findall(block)
    return found


def go_plan(code: str, version: str = "") -> Plan:
    go = "/usr/local/bin/go"
    package = GO_PACKAGE.search(code)
    if not package:
        code = f"package {'main' if GO_MAIN.search(code) else 'snippet'}\n\n{code}"
    name = package.group(1) if package else ("main" if GO_MAIN.search(code) else "snippet")
    is_test = bool(GO_TEST.search(code))
    source = "main_test.go" if is_test else "main.go"
    files = {source: code, "go.mod": "module snippet\n\ngo 1.21\n"}
    syntax = Step(SYNTAX, ("/usr/local/bin/gofmt", "-e", "-l", source), CHECK_SECONDS)
    external = [path for path in go_imports(code) if "." in path.split("/")[0]]
    if external:
        return Plan("Go", files, (syntax,), (missing_library_note("module", external),))
    vet = Step(COMPILE, (go, "vet", "."), COMPILE_SECONDS)
    if is_test:
        return Plan("Go", files, (vet, Step(TESTS, (go, "test", "."), COMPILE_SECONDS, tail=True)), go_cache=True)
    if name != "main" or not GO_MAIN.search(code):
        return Plan("Go", files, (vet,), ("not run: it has no main function",), go_cache=True)
    return Plan("Go", files, (vet, Step(RUN, (go, "run", "."), COMPILE_SECONDS, tail=True)), go_cache=True)


def java_main_class(code: str) -> str:
    public = JAVA_PUBLIC_CLASS.search(code)
    if public:
        return public.group(1)
    names = JAVA_CLASS.findall(code)
    return names[0] if names else "Main"


def java_plan(code: str, version: str = "") -> Plan:
    imports = [name for name in JAVA_IMPORT.findall(code) if not name.startswith(JAVA_STANDARD)]
    name = java_main_class(code)
    files = {f"{name}.java": code}
    if imports:
        return Plan("Java 21", files, unchecked=missing_library_note("library", [name.rsplit(".", 1)[0] for name in imports])
                    .replace("not run: it", "it"))
    if JAVA_INSTANCE_MAIN.search(code) and not JAVA_CLASS.search(code):
        return Plan("Java 21", files, unchecked="it uses Java 25's compact source files, and the sandbox has Java 21")
    compile_step = Step(COMPILE, ("/usr/bin/javac", "-Xlint:none", "-d", "out", f"{name}.java"), COMPILE_SECONDS)
    if not JAVA_MAIN.search(code):
        return Plan("Java 21", files, (compile_step,), ("not run: it has no main method",))
    return Plan("Java 21", files, (compile_step, Step(RUN, ("/usr/bin/java", "-cp", "out", name), RUN_SECONDS, tail=True)))


def kotlin_plan(code: str, version: str = "") -> Plan:
    imports = [name for name in JAVA_IMPORT.findall(code) if not name.startswith(KOTLIN_STANDARD)]
    files = {"main.kt": code}
    if imports:
        return Plan("kotlinc", files, unchecked=missing_library_note("library", [name.rsplit(".", 1)[0] for name in imports])
                    .replace("not run: it", "it"))
    compile_step = Step(COMPILE, ("/usr/local/bin/kotlinc", "main.kt", "-d", "out", "-nowarn"), COMPILE_SECONDS)
    if not KOTLIN_MAIN.search(code):
        return Plan("kotlinc", files, (compile_step,), ("not run: it has no main function",))
    run = Step(RUN, ("/usr/bin/java", "-cp", f"out:{KOTLIN_STDLIB}", "MainKt"), RUN_SECONDS, tail=True)
    return Plan("kotlinc", files, (compile_step, run), environment={"JAVA_OPTS": "-Xmx512m"})


def csharp_has_entry_point(code: str) -> bool:
    if CSHARP_MAIN.search(code):
        return True
    top_level = [line for line in code.splitlines() if line.strip() and not line.startswith((" ", "\t", "}", "{"))]
    return any(not CSHARP_DECLARATION.match(line) for line in top_level)


CSHARP_GLOBAL_USINGS = ("global using System;\nglobal using System.Collections.Generic;\nglobal using System.IO;\n"
                        "global using System.Linq;\nglobal using System.Net.Http;\nglobal using System.Threading;\n"
                        "global using System.Threading.Tasks;\n")
CSHARP_RUNTIME_CONFIG = ('{"runtimeOptions":{"tfm":"net10.0","framework":{"name":"Microsoft.NETCore.App",'
                         '"version":"10.0.0"}}}')


def csharp_plan(code: str, version: str = "", compiler: str = "", references: tuple = ()) -> Plan:
    outside = [name for name in CSHARP_USING.findall(code) if name.split(".")[0] != "System"]
    files = {"main.cs": code, "usings.cs": CSHARP_GLOBAL_USINGS, "app.runtimeconfig.json": CSHARP_RUNTIME_CONFIG}
    if outside:
        return Plan(".NET 10", files, unchecked=missing_library_note("library", outside).replace("not run: it", "it"))
    if not compiler:
        return Plan(".NET 10", files, unchecked="the C# compiler isn't installed in the sandbox")
    runnable = csharp_has_entry_point(code)
    target = "-target:exe" if runnable else "-target:library"
    compile_step = Step(COMPILE, ("/usr/local/bin/dotnet", compiler, "-nologo", "-langversion:latest", "-nullable:enable",
                                  "-nowarn:CS8600,CS8602,CS8604,CS8618,CS8625", target, "-out:app.dll",
                                  *(f"-r:{reference}" for reference in references), "main.cs", "usings.cs"),
                        COMPILE_SECONDS)
    if not runnable:
        return Plan(".NET 10", files, (compile_step,), ("not run: it has no entry point",))
    run = Step(RUN, ("/usr/local/bin/dotnet", "exec", "--runtimeconfig", "app.runtimeconfig.json", "app.dll"), RUN_SECONDS,
               tail=True)
    return Plan(".NET 10", files, (compile_step, run))


def php_plan(code: str, version: str = "") -> Plan:
    if "<?php" not in code and "<?=" not in code:
        code = "<?php\n" + code
    return Plan("PHP 8.4", {"main.php": code}, (Step(SYNTAX, ("/usr/bin/php", "-l", "main.php"), CHECK_SECONDS),
                                               Step(RUN, ("/usr/bin/php", "main.php"), RUN_SECONDS, tail=True)))


def ruby_plan(code: str, version: str = "") -> Plan:
    return Plan("Ruby 3.3", {"main.rb": code}, (Step(SYNTAX, ("/usr/bin/ruby", "-c", "main.rb"), CHECK_SECONDS),
                                              Step(RUN, ("/usr/bin/ruby", "main.rb"), RUN_SECONDS, tail=True)))


def lua_plan(code: str, version: str = "") -> Plan:
    return Plan("Lua 5.4", {"main.lua": code}, (Step(SYNTAX, ("/usr/bin/luac5.4", "-p", "main.lua"), CHECK_SECONDS),
                                              Step(RUN, ("/usr/bin/lua5.4", "main.lua"), RUN_SECONDS, tail=True)))


def sql_plan(code: str, version: str = "") -> Plan:
    dialect = version.lower()
    if dialect and dialect not in {"sql", "sqlite", "sqlite3"}:
        return Plan("SQLite 3", {"query.sql": code}, unchecked=f"it's written for {version}, and the sandbox only has SQLite")
    if SQL_OTHER_DIALECT.search(code):
        return Plan("SQLite 3", {"query.sql": code}, unchecked="it uses another database's syntax, and the sandbox only has SQLite")
    return Plan("SQLite 3", {"query.sql": code}, (Step(RUN, ("/usr/bin/python3", "-I", HELPERS, "sql", "query.sql"),
                                                       RUN_SECONDS),))


def powershell_plan(code: str, version: str = "") -> Plan:
    pwsh = "/usr/local/bin/pwsh"
    files = {"script.ps1": code}
    parse = Step(SYNTAX, (pwsh, "-NoProfile", "-NonInteractive", "-File", "/opt/code-runner/parse.ps1", "/work/script.ps1"),
                 CHECK_SECONDS)
    run = Step(RUN, (pwsh, "-NoProfile", "-NonInteractive", "-File", "/work/script.ps1"), RUN_SECONDS,
               only_syntax_errors_fail=True, tail=True)
    return Plan("PowerShell 7", files, (parse, run))


def format_plan(kind: str, tool: str, filename: str):
    def plan(code: str, version: str = "") -> Plan:
        if PLACEHOLDER.search(code):
            return Plan(tool, {filename: code}, unchecked="it has placeholders such as ...")
        return Plan(tool, {filename: code}, (Step(SYNTAX, ("/usr/bin/python3", "-I", HELPERS, kind, filename), CHECK_SECONDS),))
    return plan


def html_plan(code: str, version: str = "") -> Plan:
    return Plan("HTML Tidy", {"page.html": code}, (Step(SYNTAX, ("/usr/bin/python3", "-I", HELPERS, "html", "page.html"),
                                                        CHECK_SECONDS),))


PLANS = {
    "python": python_plan, "javascript": javascript_plan, "typescript": typescript_plan, "bash": bash_plan,
    "c": c_plan, "cpp": cpp_plan, "rust": rust_plan, "go": go_plan, "java": java_plan, "kotlin": kotlin_plan,
    "csharp": csharp_plan, "php": php_plan, "ruby": ruby_plan, "lua": lua_plan, "sql": sql_plan,
    "powershell": powershell_plan, "html": html_plan,
    "css": format_plan("css", "CSS parser", "style.css"), "json": format_plan("json", "JSON parser", "data.json"),
    "yaml": format_plan("yaml", "YAML parser", "data.yaml"), "toml": format_plan("toml", "TOML parser", "data.toml"),
    "xml": format_plan("xml", "XML parser", "data.xml"),
}

ENVIRONMENT_TROUBLE = (r"No such file or directory|FileNotFoundError|FileNotFoundException|ENOENT|EACCES|Permission denied|"
                       r"PermissionError|ConnectionRefused|ConnectionError|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|fetch failed|"
                       r"Network is unreachable|Temporary failure in name resolution|Name or service not known|"
                       r"gaierror|URLError|UnknownHostException|ConnectException|getaddrinfo|dial tcp|no such host|"
                       r"connection refused|EOFError|EOF when reading|NoSuchElementException|InputMismatchException|"
                       r"no display name|couldn't connect to display|TclError|Address already in use")
RULES = {
    "python": Rules(re.compile(r"Traceback \(most recent call last\)|^\w*Error:", re.M), re.compile(ENVIRONMENT_TROUBLE),
                    re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")),
    "javascript": Rules(re.compile(r"\b[A-Z]\w*Error\b|^\s+at\s|Uncaught|\bthrow\b", re.M), re.compile(ENVIRONMENT_TROUBLE),
                        re.compile(r"Cannot find (?:module|package) '([^']+)'")),
    "typescript": Rules(re.compile(r"\b[A-Z]\w*Error\b|^\s+at\s|Uncaught|\bthrow\b", re.M), re.compile(ENVIRONMENT_TROUBLE),
                        re.compile(r"Cannot find (?:module|package) '([^']+)'")),
    "c": Rules(re.compile(r"ERROR: AddressSanitizer|runtime error:|Segmentation fault|Aborted|core dumped|"
                          r"stack smashing|double free|free\(\): invalid"), re.compile(ENVIRONMENT_TROUBLE),
               re.compile(r"fatal error: ([\w./+-]+): No such file or directory"), signals_crash=True),
    "rust": Rules(re.compile(r"panicked at|thread '.*' panicked|attempt to .* with overflow|test result: FAILED"),
                  re.compile(ENVIRONMENT_TROUBLE), signals_crash=True),
    "go": Rules(re.compile(r"^panic:|^fatal error:|--- FAIL|^FAIL\b|goroutine \d+ \[", re.M), re.compile(ENVIRONMENT_TROUBLE)),
    "java": Rules(re.compile(r"Exception in thread|^\S+(?:Exception|Error)\b|^\s+at\s", re.M), re.compile(ENVIRONMENT_TROUBLE)),
    "csharp": Rules(re.compile(r"Unhandled exception|^\s+at\s", re.M), re.compile(ENVIRONMENT_TROUBLE)),
    "php": Rules(re.compile(r"PHP (?:Fatal|Parse) error|Fatal error:|Parse error:|Uncaught|Warning: Undefined"),
                 re.compile(ENVIRONMENT_TROUBLE + r"|Failed opening required|vendor/autoload\.php")),
    "ruby": Rules(re.compile(r"\(\w+(?:Error|Exception)\)|\b\w+Error\b|undefined method|undefined local variable"),
                  re.compile(ENVIRONMENT_TROUBLE), re.compile(r"cannot load such file -- (\S+)")),
    "lua": Rules(re.compile(r"stack traceback|lua5\.4: |attempt to "), re.compile(ENVIRONMENT_TROUBLE),
                 re.compile(r"module '([^']+)' not found")),
    "sql": Rules(re.compile(r"^SQL error", re.M), re.compile(r"^needs table", re.M)),
    "bash": Rules(re.compile(r"syntax error"), re.compile(ENVIRONMENT_TROUBLE)),
    "powershell": Rules(re.compile(r"ParserError|ParseException"), re.compile(ENVIRONMENT_TROUBLE)),
}
RULES["cpp"] = RULES["c"]
RULES["kotlin"] = RULES["java"]
DEFAULT_RULES = Rules(re.compile(r"error", re.I), re.compile(r"(?!)"))


def reads_input_or_arguments(language: str, code: str) -> bool:
    if READS_INPUT_OR_ARGUMENTS.search(code):
        return True
    return language in SHELL_LANGUAGES and bool(SHELL_READS_INPUT_OR_ARGUMENTS.search(code))


def missing_names(language: str, output: str) -> list[str]:
    rules = rules_for(language)
    if rules.missing is None:
        return []
    found = rules.missing.findall(output)
    if language == "python":
        found = [name for name in found if name.split(".")[0] not in sys.stdlib_module_names
                 or name.split(".")[0] in NOT_INSTALLED_STANDARD_MODULES]
    return list(dict.fromkeys(found))


def rules_for(language: str) -> Rules:
    return RULES.get(language, DEFAULT_RULES)


def plan_for(language: str, code: str, version: str = "", **extra) -> Plan:
    builder = PLANS[language]
    if language == "csharp":
        return builder(code, version, **extra)
    return builder(code, version)
