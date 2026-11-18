import fnmatch
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath

SHELL_PUNCTUATION = "();<>|&"
LOOSE_TOKEN = re.compile(r"[();<>|&]+|[^\s();<>|&]+")
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VARIABLE_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
REDIRECT = re.compile(r"^[<>]+[&|]?$|^&>>?$")
ATTACHED_DESCRIPTOR = re.compile(r"(?:(?<=\s)|^)\d+(?=[<>])")
CLEAN_HERE_DOCUMENT_MARKER = re.compile(r"^[A-Za-z0-9_.!-][A-Za-z0-9_.! -]*$")
HERE_DOCUMENT_OPERATOR = re.compile(r"(?<!<)<<(?!<)")
HERE_DOCUMENT_OPENER = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|([^\s;&|<>()]*))")
SPACED_TAB_STRIPPING_OPERATOR = re.compile(r"(?<!<)<<-[ \t]+")
MARKER_QUOTING = "'\"\\"
ARITHMETIC_OPENER = "(("
OLD_ARITHMETIC_OPENER = "$["
OLD_ARITHMETIC_CLOSER = "]"
ARITHMETIC_CANDIDATE = re.compile(r"(?=\(\(|\$\[)")
ARITHMETIC_SIGNS = "<>"
QUOTES = "'\""
ANSI_QUOTE = "$'"
PARAMETER_OPENER = "${"
PARAMETER_CLOSER = "}"
QUOTED, CODE, COMMENT = "quoted", "code", "comment"
WORD_BREAKS = ";&|()"
COMMAND_BREAKS = "\n;&|("
LINE_JOIN = "\\"
SHIFTING_COMMAND = "let"
CODE_FLAGS = frozenset("er")
COMMENT_START = "#"
SUBSTITUTION_OPENER = "$"
COMMAND_SUBSTITUTION = "$("
SUBSTITUTION_CLOSER = ")"
ARITHMETIC_EXPANSION = "$(("
BACKTICK = "`"
PROCESS_SUBSTITUTION = "<("
HERE_DOCUMENT = "<<"
HERE_STRING = "<<<"
TAB_STRIPPING_MARKER = "-"
MAX_NESTING = 50
WORK_PER_CHARACTER = 10
WORK_FLOOR = 10000
KEYWORDS_BEFORE_A_COMMAND = frozenset({"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time"})
HEADER_KEYWORDS = frozenset({"for", "select", "case"})
KEYWORDS_BEFORE_ARITHMETIC = KEYWORDS_BEFORE_A_COMMAND | {"for"}
FUNCTION_KEYWORD = "function"
GROUP_CLOSE = ["}"]
WRAPPER_VALUE_OPTIONS = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-U", "-r", "-t", "-T", "--user", "--group", "--host", "--prompt",
                       "--chdir", "--other-user", "--role", "--type", "--command-timeout", "--close-from"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "xargs": frozenset({"-a", "-d", "-E", "-e", "-I", "-i", "-L", "-l", "-n", "-P", "-s", "--arg-file", "--delimiter",
                        "--max-args", "--max-procs", "--max-chars", "--max-lines", "--replace", "--eof"}),
    "strace": frozenset({"-o", "-e", "-p", "-s", "-u", "-a", "-b", "-E", "-I", "-O", "-P", "-S", "-X"}),
    "ltrace": frozenset({"-o", "-e", "-p", "-s", "-u", "-a", "-n"}),
    "flock": frozenset({"-w", "-E", "--timeout", "--wait", "--conflict-exit-code"}),
    "chroot": frozenset({"--userspec", "--groups"}),
    "setsid": frozenset(), "nohup": frozenset(), "time": frozenset(), "command": frozenset(), "exec": frozenset(),
    "builtin": frozenset(), "chronic": frozenset(), "unbuffer": frozenset(), "taskset": frozenset(),
}
WRAPPERS_WITH_ONE_ARGUMENT = frozenset({"timeout", "flock", "chroot", "taskset"})
ARGUMENT_FEEDER = "xargs"
NESTED_COMMAND_OPTIONS = {"su": frozenset({"-c", "--command"}), "runuser": frozenset({"-c", "--command"}),
                          "flock": frozenset({"-c", "--command"})}
TEXT_ONLY_PROGRAMS = frozenset({"echo", "printf", "git", "grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk", "cat", "less",
                                "more", "head", "tail", "jq", "logger", "notify-send", "wc", "sort", "uniq", "cut", "tr", "diff",
                                "man", "which", "type", "test", "[", "[[", "tee", "touch", "mkdir", "cd", "ls", "true", "false"})
COMMAND_STRING_PROGRAMS = frozenset({"tmux", "screen", "watch", "ssh", "parallel"})
COMMAND_OPTIONS = frozenset({"-c", "--command", "-e", "--exec", "-x", "--run"})
COMMAND_STRING_FLAG = "c"
EVAL = "eval"
DEACTIVATE = "deactivate"
USER_INSTALL_FLAG = "--user"
COMMAND_RUNNERS = frozenset({"docker", "podman", "kubectl", "lxc", "incus", "distrobox", "toolbox", "nix-shell", "flatpak-spawn",
                             "systemd-run", "firejail", "xvfb-run", "script", "gnome-terminal", "konsole", "xterm", "su", "runuser"})
CONDA_FAMILY = frozenset({"conda", "mamba", "micromamba"})
CONDA_VALUE_OPTIONS = frozenset({"-n", "--name", "-p", "--prefix"})
VENV_RUNNERS = frozenset({"poetry", "pipenv", "pdm", "hatch"})
VENV_RUN = "run"
EXPORTING = frozenset({"export", "declare", "typeset"})
PIP = re.compile(r"^pip[\d.]*$")
PYTHON = re.compile(r"^(?:python[\d.]*|py)$")
PYTHON_FLAGS_WITH_VALUES = "WX"
PIP_MODULES = frozenset({"pip", "pip3", "pip._internal"})
PYTHON_INSTALL_MODULES = frozenset({"ensurepip", "easy_install"})
PIP_VALUE_OPTIONS = frozenset({"--log", "--proxy", "--retries", "--timeout", "--exists-action", "--trusted-host", "--cert",
                               "--client-cert", "--cache-dir", "--python", "--keyring-provider", "--use-feature",
                               "--use-deprecated", "--resume-retries"})
HELP_FLAGS = frozenset({"-h", "--help"})
LONG_HELP_FLAGS = frozenset({"--help"})
OTHER_QUERY_FLAGS = frozenset({"--version", "--list"})
QUERY_FLAGS = HELP_FLAGS | OTHER_QUERY_FLAGS
END_OF_OPTIONS = "--"
SHORT_HELP_PROGRAMS = frozenset({"apt", "apt-get", "aptitude", "pacman", "dnf", "yum", "zypper", "apk", "snap", "flatpak", "cargo",
                                 "go", "gem", "deno", "conda", "mamba", "micromamba", "brew", "npm", "pnpm", "yarn", "bun",
                                 "npx", "pipx", "uv", "uvx", "rustup", "dotnet", "composer"})
RPM = "rpm"
RPM_INSTALL_MODES = frozenset("iUF")
RPM_OTHER_MODES = frozenset("qVe")
RPM_MODES = RPM_INSTALL_MODES | RPM_OTHER_MODES
RPM_INSTALL_LONG = frozenset({"--install", "--upgrade", "--freshen", "--reinstall"})
SHELLS = re.compile(r"^(?:(?:ba|z|da|k|mk|a|fi)?sh|fish)$")
SHELL_VALUE_FLAGS = "oO"
SHELL_LONG_VALUE_OPTIONS = frozenset({"--rcfile", "--init-file"})
CODE_FLAG_INTERPRETERS = re.compile(r"^(?:perl[\d.]*|ruby[\d.]*|node(?:js)?|php[\d.]*|lua[\d.]*|deno|bun)$")
SHELLS_AND_INTERPRETERS = re.compile(r"^(?:(?:ba|z|da|k|mk|a|fi)?sh|fish|python[\d.]*|perl[\d.]*|ruby[\d.]*|node(?:js)?|php[\d.]*|"
                                     r"lua[\d.]*|tclsh|deno|bun|source|\.)$")
SOURCING = frozenset({"source", "."})
COMMAND_STDIN_READERS = frozenset({"at", "batch"})
STDIN_NAMES = frozenset({"-", "/dev/stdin", "/proc/self/fd/0", "/dev/fd/0"})
DESCRIPTOR_PATH = re.compile(r"^/(?:dev/fd|proc/(?:self|thread-self|\d+|\$\$|\$BASHPID)/fd)/\d+$")
REPEATED_SLASHES = re.compile(r"/+")
EXEC = "exec"
DESCRIPTOR_INPUT = "<&"
DEVICE_FOLDER = "/dev/"
DOWNLOADERS = frozenset({"curl", "wget", "aria2c", "http", "https", "fetch"})
OUTPUT_SHORT_FLAGS = {"curl": "o", "wget": "O", "aria2c": "o", "fetch": "o", "http": "o", "https": "o"}
SAVES_UNDER_THE_REMOTE_NAME = DOWNLOADERS - {"curl"}
SAVING_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>"})
DIRECTORY_CHANGERS = frozenset({"cd", "pushd"})
TAR_PROGRAMS = frozenset({"tar", "bsdtar"})
ZIP_UNPACKERS = frozenset({"unzip"})
SEVEN_ZIP_UNPACKERS = frozenset({"7z", "7za", "7zr", "unrar"})
SEVEN_ZIP_EXTRACTS = frozenset({"x", "e"})
DECOMPRESSORS = frozenset({"gunzip", "bunzip2", "unxz", "unzstd", "uncompress", "unlzma"})
COMPRESSORS = frozenset({"gzip", "bzip2", "xz", "zstd", "lzma"})
DECOMPRESS_FLAGS = frozenset({"-d", "--decompress"})
COMPRESSED_SUFFIXES = (".gz", ".bz2", ".xz", ".zst", ".Z", ".lzma")
COPIERS = frozenset({"mv", "cp", "ln", "install"})
DD = "dd"
DD_INPUT = "if"
DD_OUTPUT = "of"
TAR_VALUE_FLAGS = "bCfFgHIKLNTVX"
TAR_FOLDER_FLAG = "C"
TAR_COMMAND_OPTIONS = frozenset({"--to-command", "--use-compress-program", "-I", "--info-script", "--new-volume-script", "-F",
                                 "--rsh-command"})
TAR_CHECKPOINT_ACTION = "--checkpoint-action"
TAR_EXEC_ACTION = "exec="
GIT = "git"
GIT_CONFIG_OPTION = "-c"
GIT_SHELL_ALIAS = "!"
GIT_COMMAND_SETTINGS = frozenset({"core.sshcommand", "core.fsmonitor", "core.pager", "core.editor"})
GIT_COMMAND_SETTING_ENDINGS = ("command", "editor", "pager", ".cmd", ".clean", ".smudge", "external", "helper")
GIT_HOOKS_SETTING_ENDING = "hookspath"
COMMAND_SETTING = re.compile(r"^(?:GIT_\w*(?:COMMAND|EDITOR|PAGER|DIFF|ASKPASS|SSH)|PAGER|EDITOR|VISUAL|MANPAGER)$")
GIT_CONFIG = "config"
GIT_CONFIG_SET = "set"
GIT_CONFIG_VALUE_OPTIONS = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"})
TRAP = "trap"
SWITCH_GROUP = "sg"
SWITCH_USER = "su"
POWERSHELLS = frozenset({"pwsh", "powershell", "pwsh-preview"})
POWERSHELL_COMMAND_OPTIONS = ("-command", "-commandwithargs")
POWERSHELL_COMMAND_ALIASES = frozenset({"-cwa"})
POWERSHELL_ENCODED_OPTION = "-encodedcommand"
POWERSHELL_ENCODED_ALIASES = frozenset({"-ec"})
POWERSHELL_STDIN = "-"
POWERSHELL_RUNNERS = frozenset({"iex", "invoke-expression", "icm", "invoke-command", "ii", "invoke-item", "start-process", "saps",
                                "start", "start-job", "msiexec", "install-module", "install-package", "install-script",
                                "install-psresource", "add-appxpackage", "add-appxprovisionedpackage", "invoke-webrequest",
                                "iwr", "invoke-restmethod", "irm", "curl", "wget", "start-bitstransfer", "new-object",
                                "set-executionpolicy", "add-type"})
POWERSHELL_DEFINERS = frozenset({"set-alias", "new-alias", "sal", "nal", "function"})
POWERSHELL_FETCHING_VERBS = ("update-", "save-")
POWERSHELL_UNREAD_MARKS = (BACKTICK, "@(", COMMAND_SUBSTITUTION)
POWERSHELL_JOINED_STRINGS = re.compile(r"[\"']\s*\+|\+\s*[\"']")
POWERSHELL_FILE_OPTION = "-file"
POWERSHELL_VALUE_OPTIONS = frozenset({"-executionpolicy", "-ex", "-ep", "-workingdirectory", "-wd", "-outputformat", "-o", "-of",
                                      "-inputformat", "-in", "-if", "-windowstyle", "-w", "-configurationname", "-config",
                                      "-custompipename", "-settingsfile", "-settings", "-configurationfile"})
POWERSHELL_COMMAND_WORD = re.compile(r"[A-Za-z][\w-]*")
POWERSHELL_CALL = re.compile(r"(?<!&)&(?!&)|(?:^|[;|{(\n])\s*\.\s")
POWERSHELL_DOWNLOAD_MARKS = ("downloadstring", "downloadfile", "webclient", "://")
START_UP_SETTINGS = frozenset({"BASH_ENV", "ZDOTDIR"})
INTERACTIVE_START_UP_SETTINGS = frozenset({"ENV", "PROMPT_COMMAND"})
INTERACTIVE_FLAG = "i"
ENV_SPLIT_OPTIONS = frozenset({"-S", "--split-string"})
ENV_SPLIT_PREFIXES = ("-S", "--split-string=")
PATH_VARIABLE = re.compile(r"^\$\{?(?:HOME|PWD|TMPDIR|VIRTUAL_ENV|JAVA_HOME|GOPATH)\}?/")
UNPACK_FOLDER_OPTIONS = frozenset({"-C", "--directory", "-d"})
JAVA = "java"
JAR_OPTION = "-jar"
TAR_EXTRACT_FLAG = "x"
TAR_EXTRACT_LONG = frozenset({"--extract", "--get"})
MAKE = "make"
MAKE_INSTALL_TARGET = "install"
MAKE_DIRECTORY_OPTIONS = frozenset({"-C", "--directory"})
GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"})
CLONE_VALUE_OPTIONS = frozenset({"--depth", "-b", "--branch", "-j", "--jobs", "-o", "--origin", "-c", "--config", "--reference",
                                 "--reference-if-able", "--separate-git-dir", "-u", "--upload-pack", "--template", "--filter",
                                 "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri"})
GH_CLONE = ["repo", "clone"]
GIT_SUFFIX = ".git"
APT_FAMILY = frozenset({"apt", "apt-get", "aptitude"})
APT_VALUE_OPTIONS = frozenset({"-o", "-c", "-t", "--option", "--config-file", "--target-release", "--default-release"})
APT_INSTALLS = frozenset({"install", "reinstall", "upgrade", "full-upgrade", "dist-upgrade", "build-dep", "satisfy"})
DPKG_INSTALL_LONG = frozenset({"--install", "--unpack"})
PACMAN_FAMILY = frozenset({"pacman", "yay", "paru"})
PACMAN_SYNC_LONG = frozenset({"--sync", "--upgrade"})
PACMAN_SYNC = "S"
PACMAN_UPGRADE = "U"
PACMAN_SYNC_QUERIES = frozenset("silgpc")
NODE_INSTALLS = frozenset({"install", "i", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal", "isntall", "add",
                           "update", "up", "upgrade", "link", "ln"})
NODE_GLOBAL_FLAGS = frozenset({"-g", "--global", "--global=true", "--location=global"})
NODE_GLOBAL_SHORT_FLAG = "g"
NODE_GLOBAL_SETTING = "npm_config_global"
NODE_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "bun", "yarn"})
NPM_RUNS_A_PACKAGE = frozenset({"exec", "x"})
FETCHING_SUBCOMMANDS = {"pnpm": frozenset({"dlx"}), "bun": frozenset({"x"}), "yarn": frozenset({"dlx"})}
NODE_CREATORS = {"npm": frozenset({"create", "init", "innit"}), "pnpm": frozenset({"create"}), "yarn": frozenset({"create"}),
                 "bun": frozenset({"create"})}
NPM_INIT = frozenset({"init", "innit"})
NPX_LOCAL_ONLY = frozenset({"--no", "--no-install", "--offline"})
FETCHING_RUNNERS = frozenset({"pnpx", "bunx", "uvx"})
PIPX_INSTALLS = frozenset({"install", "install-all", "run", "inject", "upgrade", "upgrade-all", "reinstall", "reinstall-all"})
UV_TOOL_INSTALLS = frozenset({"install", "run", "upgrade"})
UV_TOOL = "tool"
PROGRAMS_THAT_RUN_A_PACKAGE = frozenset({"pipx", "uv", "go", "deno", "bun"})
UV_RUN_FETCH_OPTIONS = ("--with", "--with-requirements", "--with-editable")
GO_RUN = "run"
GO_MODULE_VERSION = "@"
DENO = "deno"
SCRIPT_RUNNERS_WITH_RUN = frozenset({DENO, "bun"})
URL_MARK = "://"
UV_SYSTEM_SETTING = "UV_SYSTEM_PYTHON"
PACKAGE_MANAGER_INSTALLS = frozenset({"install", "reinstall", "upgrade"})
INSTALL_SUBCOMMANDS = {"snap": frozenset({"install"}), "flatpak": frozenset({"install"}), "cargo": frozenset({"install", "binstall"}),
                       "go": frozenset({"install"}), "gem": frozenset({"install"}), "deno": frozenset({"install"}),
                       "nvm": frozenset({"install"}), "conda": frozenset({"install", "update", "upgrade"}),
                       "mamba": frozenset({"install", "update", "upgrade"}),
                       "micromamba": frozenset({"install", "update", "upgrade"}),
                       "brew": PACKAGE_MANAGER_INSTALLS, "dnf": PACKAGE_MANAGER_INSTALLS | {"groupinstall", "localinstall"},
                       "yum": PACKAGE_MANAGER_INSTALLS | {"groupinstall", "localinstall"}, "zypper": frozenset({"install", "in"}),
                       "apk": frozenset({"add"}), "pyenv": frozenset({"install"}), "asdf": frozenset({"install"}),
                       "luarocks": frozenset({"install"}), "volta": frozenset({"install"}), "port": frozenset({"install"}),
                       "choco": frozenset({"install"}), "winget": frozenset({"install"}), "scoop": frozenset({"install"})}
TWO_WORD_INSTALLS = {"dotnet": frozenset({("tool", "install"), ("tool", "update")}),
                     "composer": frozenset({("global", "require"), ("global", "update")})}
GO_FETCH = "get"
LOCAL_PATH_START = "."
SUBCOMMAND_VALUE_OPTIONS = {"flatpak": frozenset({"--installation"})}
ALWAYS_INSTALLERS = frozenset({"rustup-init", "easy_install", "cpanm"})
RUSTUP = "rustup"
RUSTUP_QUERIES = frozenset({"", "show", "which", "help", "doc", "completions", "check"})
PACMAN_REFRESH = frozenset("y")
SETUP_SCRIPT = "setup.py"
SETUP_INSTALLS = frozenset({"install", "develop"})
VENV_MAKERS = frozenset({"virtualenv"})
DEFAULT_UV_VENV = ".venv"
TRUTHY = frozenset({"1", "true", "yes", "on"})
VENV_FOLDER = re.compile(r"^(?:\.?venv|\.?env|virtualenv|.*[._-]venv|venv[._-].*)$")
ACTIVATE_SCRIPT = re.compile(r"(?:^|/)(?:bin|Scripts)/activate$")
PATH_PARTS_NEVER_NAMED = frozenset({".", "..", "/", ""})
CURRENT_FOLDER = "."
SUBMODULE_ADD = ["submodule", "add"]
GLOB_CHARACTERS = frozenset("*?[")
RELATIVE_PATH_STARTS = ("./", "../")
OPTION_STARTS = ("-", "+")
DOWNLOAD_READERS = frozenset({"sha1sum", "sha224sum", "sha256sum", "sha384sum", "sha512sum", "shasum", "md5sum", "b2sum", "cksum",
                              "sum", "file", "stat", "du", "cmp", "gpg", "gpgv", "od", "xxd", "hexdump", "strings", "base64",
                              "readlink", "realpath", "basename", "dirname", "nl", "zcat", "rm", "rmdir", "chmod", "chown",
                              "chgrp"})
READ_ONLY_TOOLS = frozenset({"bat", "batcat", "shellcheck", "nano", "code", "ffprobe", "ffmpeg", "pdftotext", "tree", "cloc"})
DATA_FILE_VIEWERS = frozenset({"xdg-open"})
DOWNLOAD_HANDLERS = (TEXT_ONLY_PROGRAMS | DOWNLOAD_READERS | COPIERS | {DD} | DOWNLOADERS | TAR_PROGRAMS | ZIP_UNPACKERS
                     | SEVEN_ZIP_UNPACKERS | DECOMPRESSORS | COMPRESSORS | DIRECTORY_CHANGERS | READ_ONLY_TOOLS)
AWK_PROGRAM_OPTIONS = ("fEil", frozenset({"--file", "--exec", "--include", "--load"}))
BAT_PROGRAM_OPTIONS = ("", frozenset({"--pager"}))
PROGRAM_FILE_OPTIONS = {"sed": ("f", frozenset({"--file"})), "awk": AWK_PROGRAM_OPTIONS, "gawk": AWK_PROGRAM_OPTIONS,
                        "sort": ("", frozenset({"--compress-program"})), "install": ("", frozenset({"--strip-program"})),
                        "man": ("PH", frozenset({"--pager", "--html"})), "bat": BAT_PROGRAM_OPTIONS, "batcat": BAT_PROGRAM_OPTIONS,
                        "code": ("", frozenset({"--install-extension"}))}
NO_PROGRAM_OPTIONS = ("", frozenset())
UNRESOLVED_SCRIPT_MARKS = frozenset("${}`")
DATA_READING_MODULES = frozenset({"json.tool"})
DATA_SUFFIXES = frozenset({".json", ".csv", ".tsv", ".txt", ".xml", ".yaml", ".yml", ".toml", ".md", ".html", ".htm", ".log",
                           ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".wav", ".mp3", ".mp4", ".parquet", ".xlsx"})


class WorkBudgetSpent(Exception):
    pass


@dataclass
class WorkBudget:
    left: int

    @classmethod
    def for_command(cls, command: str) -> "WorkBudget":
        return cls(WORK_FLOOR + WORK_PER_CHARACTER * len(command))

    def spend(self, amount: int):
        self.left -= amount
        if self.left < 0:
            raise WorkBudgetSpent(f"reading the command took more than {WORK_PER_CHARACTER} steps per character")


@dataclass
class Segment:
    words: list[str] = field(default_factory=list)
    joined_by: str = ""
    redirects: list[tuple[str, str]] = field(default_factory=list)
    last_token: str = ""


@dataclass
class Reading:
    budget: WorkBudget
    downloads: set[str] = field(default_factory=set)
    unpacked_folders: set[str] = field(default_factory=set)
    venvs: set[str] = field(default_factory=set)
    variables: dict[str, str] = field(default_factory=dict)
    exported: dict[str, str] = field(default_factory=dict)
    inside_download: bool = False
    depth: int = 0
    opened_descriptors: bool = False

    def nested(self, command: str, environment: dict | None = None) -> bool:
        exported = dict(self.exported if environment is None else environment)
        inner = Reading(self.budget, self.downloads, self.unpacked_folders, self.venvs, dict(self.variables),
                        exported, self.inside_download, self.depth + 1, self.opened_descriptors)
        return reads_an_install(command, inner)

    def download_state(self) -> tuple[int, int, bool]:
        return len(self.downloads), len(self.unpacked_folders), self.inside_download


@dataclass
class LineState:
    activated: bool = False
    carries_download: bool = False
    piped_text: str | None = None
    previous_program: str = ""
    previous_ends_in_substitution: bool = False
    previous_awaits_a_script: bool = False


@dataclass
class HereDocument:
    words: list[str]
    body: str
    expands: bool


@dataclass
class HereDocumentOpener:
    marker: str
    strips_tabs: bool
    quoted: bool

    def as_a_token(self) -> str:
        return (TAB_STRIPPING_MARKER if self.strips_tabs else "") + self.marker

    def ends_the_body(self, line: str) -> bool:
        return (line.lstrip("\t") if self.strips_tabs else line) == self.marker


def with_backticks_as_substitutions(line: str) -> str:
    parts = line.split(BACKTICK)
    closers = [COMMAND_SUBSTITUTION if position % 2 == 0 else SUBSTITUTION_CLOSER for position in range(len(parts) - 1)]
    return "".join(part + closer for part, closer in zip(parts, closers)) + parts[-1]


def tokens(line: str) -> list[str]:
    line = ATTACHED_DESCRIPTOR.sub("", with_backticks_as_substitutions(line))
    line = SPACED_TAB_STRIPPING_OPERATOR.sub(HERE_DOCUMENT + TAB_STRIPPING_MARKER, line)
    lexer = shlex.shlex(line, posix=True, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return [token.strip("\"'") for token in LOOSE_TOKEN.findall(line)]


def is_operator(token: str) -> bool:
    return bool(token) and all(character in SHELL_PUNCTUATION for character in token)


def segments(line: str) -> list[Segment]:
    found, current, line_tokens = [], Segment(), tokens(line)
    index = 0
    while index < len(line_tokens):
        token = line_tokens[index]
        following = line_tokens[index + 1] if index + 1 < len(line_tokens) else ""
        if REDIRECT.match(token):
            target = "" if is_operator(following) else following
            current.redirects.append((token, target))
            current.last_token = target or current.last_token
            index += 2 if target else 1
            continue
        if is_operator(token):
            found.append(current)
            current = Segment(joined_by=token)
        else:
            current.words.append(token)
            current.last_token = token
        index += 1
    return [segment for segment in found + [current] if segment.words or segment.redirects]


def end_of_line(text: str, index: int) -> int:
    position = text.find("\n", index)
    return len(text) if position < 0 else position


def starts_a_word(text: str, index: int) -> bool:
    return index == 0 or text[index - 1].isspace() or text[index - 1] in WORD_BREAKS


@dataclass
class ShellScan:
    quote: str = ""
    braces: int = 0


def scanned_character(text: str, index: int, scan: ShellScan) -> tuple[int, str]:
    character = text[index]
    if scan.quote == "'":
        scan.quote = "" if character == "'" else scan.quote
        return index + 1, QUOTED
    if character == "\\":
        return index + 2, QUOTED
    if scan.quote:
        closer = "'" if scan.quote == ANSI_QUOTE else '"'
        scan.quote = "" if character == closer else scan.quote
        return index + 1, QUOTED
    if text.startswith(ANSI_QUOTE, index):
        scan.quote = ANSI_QUOTE
        return index + len(ANSI_QUOTE), QUOTED
    if character in QUOTES:
        scan.quote = character
        return index + 1, QUOTED
    if text.startswith(PARAMETER_OPENER, index):
        scan.braces += 1
    elif character == PARAMETER_CLOSER and scan.braces:
        scan.braces -= 1
    elif character == COMMENT_START and not scan.braces and starts_a_word(text, index):
        return index, COMMENT
    return index + 1, CODE


def scanned_code(text: str, quote: str = "") -> tuple[list[bool], bool]:
    outside, index, has_a_comment, scan = [False] * len(text), 0, False, ShellScan(quote)
    while index < len(text):
        if text[index] == "\n":
            scan = ShellScan()
            index += 1
            continue
        next_index, kind = scanned_character(text, index, scan)
        if kind == COMMENT:
            has_a_comment = True
            index = end_of_line(text, index)
            continue
        outside[index] = kind == CODE
        index = next_index
    return outside, has_a_comment


def ends_with_a_line_join(line: str) -> bool:
    return (len(line) - len(line.rstrip(LINE_JOIN))) % 2 == 1


def continues_as_code(line: str) -> bool:
    return ends_with_a_line_join(line) and not scanned_code(line)[1]


def logical_line(lines: list[str], index: int, continues) -> tuple[str, int]:
    parts = []
    while index < len(lines) and continues(lines[index]):
        parts.append(lines[index][:-len(LINE_JOIN)] + " ")
        index += 1
    if index < len(lines):
        parts.append(lines[index])
        index += 1
    return "".join(parts), index


def matching_brackets(text: str, outside: list[bool]) -> dict[int, int]:
    pairs, open_positions = {}, []
    for index, character in enumerate(text):
        if not outside[index]:
            continue
        if character == "(":
            open_positions.append(index)
        elif character == ")" and open_positions:
            pairs[open_positions.pop()] = index
    return pairs


def begins_a_command(text: str, index: int) -> bool:
    before = index - 1
    while before >= 0 and text[before] in " \t":
        before -= 1
    if before < 0 or text[before] in COMMAND_BREAKS:
        return True
    start = before
    while start >= 0 and not text[start].isspace() and text[start] not in WORD_BREAKS:
        start -= 1
    return text[start + 1:before + 1] in KEYWORDS_BEFORE_ARITHMETIC


def arithmetic_end(text: str, index: int, outside: list[bool], pairs: dict[int, int]) -> int | None:
    if not (outside[index] and outside[index + 1]):
        return None
    if not (index > 0 and text[index - 1] == SUBSTITUTION_OPENER) and not begins_a_command(text, index):
        return None
    inner_end = pairs.get(index + 1)
    if inner_end is None or pairs.get(index) != inner_end + 1:
        return None
    return inner_end + 2


def ends_a_process_id(text: str, index: int) -> bool:
    start = index
    while start > 0 and text[start - 1] == SUBSTITUTION_OPENER:
        start -= 1
    return (index - start) % 2 == 1


def arithmetic_spans(text: str, outside: list[bool], pairs: dict[int, int]) -> list[tuple[int, int]]:
    spans, covered_until, line_end, unclosed_until = [], 0, -1, -1
    for candidate in ARITHMETIC_CANDIDATE.finditer(text):
        index, end = candidate.start(), None
        if index < covered_until:
            continue
        if text.startswith(ARITHMETIC_OPENER, index):
            end = arithmetic_end(text, index, outside, pairs)
        elif outside[index] and index >= unclosed_until and not ends_a_process_id(text, index):
            line_end = end_of_line(text, index) if index > line_end else line_end
            close = text.find(OLD_ARITHMETIC_CLOSER, index + len(OLD_ARITHMETIC_OPENER), line_end)
            unclosed_until = line_end if close < 0 else unclosed_until
            end = close + 1 if close >= 0 else None
        if end is not None:
            spans.append((index, end))
            covered_until = end
    return spans


def without_arithmetic(text: str) -> str:
    if ARITHMETIC_OPENER not in text and OLD_ARITHMETIC_OPENER not in text:
        return text
    outside, _ = scanned_code(text)
    characters = list(text)
    for start, end in arithmetic_spans(text, outside, matching_brackets(text, outside)):
        for index in range(start, end):
            if characters[index] in ARITHMETIC_SIGNS:
                characters[index] = " "
    return "".join(characters)


def is_comment(line: str) -> bool:
    return line.lstrip().startswith(COMMENT_START)


def quote_left_open(line: str, quote: str) -> str:
    index, scan = 0, ShellScan(quote)
    while index < len(line):
        index, kind = scanned_character(line, index, scan)
        if kind == COMMENT:
            break
    return scan.quote


def closing_bracket(text: str, start: int) -> int:
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def substitutions(text: str) -> list[str]:
    found, index, quote = [], 0, ""
    while index < len(text):
        character = text[index]
        if quote == "'":
            quote = "" if character == "'" else quote
            index += 1
            continue
        if character == "\\":
            index += 2
            continue
        if character == "'" and not quote:
            quote = "'"
        elif character == '"':
            quote = "" if quote == '"' else '"'
        elif text.startswith(COMMAND_SUBSTITUTION, index) and not text.startswith(ARITHMETIC_EXPANSION, index):
            end = closing_bracket(text, index + len(COMMAND_SUBSTITUTION))
            found.append(text[index + len(COMMAND_SUBSTITUTION):end])
            if end >= len(text):
                return found
            index = end + 1
            continue
        elif character == BACKTICK:
            end = text.find(BACKTICK, index + 1)
            end = len(text) if end < 0 else end
            found.append(text[index + 1:end])
            index = end + 1
            continue
        index += 1
    return found


def name_of(program: str) -> str:
    return PurePosixPath(program).name or program


def is_true(value: str) -> bool:
    return value.strip().lower() in TRUTHY


def remember_setting(settings: dict, word: str):
    name, value = word.split("=", 1)
    settings[name] = value


def expanded(word: str, variables: dict[str, str]) -> str:
    return VARIABLE_REFERENCE.sub(lambda found: variables.get(found.group(1) or found.group(2), found.group(0)), word)


def remember_variables(settings: dict, reading: Reading):
    reading.variables.update({name: value for name, value in settings.items() if SUBSTITUTION_OPENER not in value})


def splits_a_string(wrapper: str, option: str) -> bool:
    return wrapper == "env" and (option in ENV_SPLIT_OPTIONS or option.startswith(ENV_SPLIT_PREFIXES))


def split_string_words(words: list[str]) -> list[str]:
    option = words[0]
    if option in ENV_SPLIT_OPTIONS:
        return tokens(words[1]) + words[2:] if len(words) > 1 else []
    prefix = next(prefix for prefix in ENV_SPLIT_PREFIXES if option.startswith(prefix))
    return tokens(option[len(prefix):]) + words[1:]


def without_one_wrapper(words: list[str], settings: dict) -> list[str]:
    wrapper, words = name_of(words[0]), words[1:]
    while words and (words[0].startswith("-") or ENV_ASSIGNMENT.match(words[0])):
        if ENV_ASSIGNMENT.match(words[0]):
            remember_setting(settings, words[0])
        if splits_a_string(wrapper, words[0]):
            words = split_string_words(words)
            continue
        words = words[2:] if words[0] in WRAPPER_VALUE_OPTIONS[wrapper] else words[1:]
    if wrapper in WRAPPERS_WITH_ONE_ARGUMENT and words:
        words = words[1:]
    return words


def after_keywords_and_settings(words: list[str], settings: dict) -> list[str] | None:
    start = 0
    while start < len(words) and words[start] in KEYWORDS_BEFORE_A_COMMAND:
        start += 1
    if start < len(words) and words[start] == FUNCTION_KEYWORD:
        start += 2
    if start < len(words) and words[start] in HEADER_KEYWORDS:
        return None
    while start < len(words) and ENV_ASSIGNMENT.match(words[start]):
        remember_setting(settings, words[start])
        start += 1
    return words[start:] if start else words


def command_words(words: list[str]) -> tuple[list[str], dict]:
    settings: dict = {}
    while True:
        before = words
        words = after_keywords_and_settings(words, settings)
        if words is None:
            return [], settings
        if words and name_of(words[0]) in WRAPPER_VALUE_OPTIONS:
            words = without_one_wrapper(words, settings)
        if words is before or words == before:
            return words, settings


def arguments(words: list[str]) -> list[str]:
    return [word for word in words[1:] if not word.startswith("-")]


def positional_words(words: list[str], value_options: frozenset) -> list[str]:
    found, index = [], 0
    while index < len(words):
        word = words[index]
        if word in value_options:
            index += 2
            continue
        if not word.startswith("-"):
            found.append(word)
        index += 1
    return found


def subcommand(words: list[str], value_options: frozenset = frozenset()) -> str:
    index = 1
    while index < len(words):
        word = words[index]
        if word in value_options:
            index += 2
            continue
        if not word.startswith(("-", "+")):
            return word
        index += 1
    return ""


def is_short_cluster_with(word: str, flag: str) -> bool:
    return word.startswith("-") and not word.startswith("--") and flag in word[1:]


def python_flags(words: list[str]) -> tuple[str, list[str], bool, int]:
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return "", [], False, index + 1
        if not word.startswith("-") or word == "-":
            return "", [], False, index
        if word.startswith("--"):
            index += 1
            continue
        cluster = word[1:]
        for position, flag in enumerate(cluster):
            attached = cluster[position + 1:]
            if flag == "m":
                if attached:
                    return attached, [attached] + words[index + 1:], False, index
                module = words[index + 1] if index + 1 < len(words) else ""
                return module, words[index + 1:], False, index
            if flag == "c":
                return "", [], True, index
            if flag in PYTHON_FLAGS_WITH_VALUES:
                index += 0 if attached else 1
                break
        index += 1
    return "", [], False, index


def python_module(words: list[str]) -> tuple[str, list[str]]:
    module, rest, _, _ = python_flags(words)
    return module, rest


def python_script(words: list[str]) -> str:
    module, _, runs_code, index = python_flags(words)
    if module or runs_code or index >= len(words):
        return ""
    return words[index]


def shell_script(words: list[str]) -> str:
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return words[index + 1] if index + 1 < len(words) else ""
        if is_a_shell_option(word):
            index += 2 if takes_a_shell_value(word) else 1
            continue
        return word
    return ""


def is_a_shell_option(word: str) -> bool:
    return len(word) > 1 and word[0] in "-+"


def takes_a_shell_value(option: str) -> bool:
    return option in SHELL_LONG_VALUE_OPTIONS or (not option.startswith("--") and option[-1] in SHELL_VALUE_FLAGS)


def shell_words_from(words: list[str], start: int) -> list[str]:
    end = start + 1
    while end < len(words) and is_a_shell_option(words[end]):
        end += 2 if takes_a_shell_value(words[end]) else 1
    return words[start:end + 1]


def later_shells(program: str, words: list[str]) -> list[list[str]]:
    if program in TEXT_ONLY_PROGRAMS or SHELLS.match(program) or program in SOURCING:
        return []
    return [shell_words_from(words, index) for index, word in enumerate(words[1:], start=1)
            if not word.startswith("-") and "=" not in word and SHELLS.match(name_of(word))]


def lives_in_a_venv(program: str, reading: Reading) -> bool:
    folder = PurePosixPath(program).parent
    if folder.name not in ("bin", "Scripts"):
        return False
    return bool(VENV_FOLDER.match(folder.parent.name)) or str(folder.parent) in reading.venvs


def made_venv(words: list[str]) -> str:
    program = name_of(words[0])
    if PYTHON.match(program):
        module, rest = python_module(words)
        positional = arguments(rest)
        return positional[0] if module == "venv" and positional else ""
    if program in VENV_MAKERS:
        positional = arguments(words)
        return positional[0] if positional else ""
    if program == "uv" and subcommand(words) == "venv":
        positional = arguments(words)
        return positional[1] if len(positional) > 1 else DEFAULT_UV_VENV
    return ""


def pip_words(words: list[str]) -> list[str] | None:
    program = name_of(words[0])
    if PIP.match(program):
        return words
    if PYTHON.match(program):
        module, rest = python_module(words)
        return rest if module in PIP_MODULES else None
    return None


def is_pip_install(words: list[str], activated: bool, reading: Reading) -> bool:
    pip = pip_words(words)
    if pip is None or subcommand(pip, PIP_VALUE_OPTIONS) != "install" or HELP_FLAGS & set(before_the_end_of_options(pip)):
        return False
    is_bare = "/" not in words[0]
    stays_in_the_venv = activated and is_bare and USER_INSTALL_FLAG not in pip
    return not (lives_in_a_venv(words[0], reading) or stays_in_the_venv)


def is_setup_install(words: list[str]) -> bool:
    if not PYTHON.match(name_of(words[0])):
        return False
    script = python_script(words)
    if name_of(script) != SETUP_SCRIPT:
        return False
    return bool(SETUP_INSTALLS & set(words[words.index(script) + 1:]))


def has_global_flag(words: list[str], settings: dict) -> bool:
    if any(name.lower() == NODE_GLOBAL_SETTING and is_true(value) for name, value in settings.items()):
        return True
    if any(word in NODE_GLOBAL_FLAGS or is_short_cluster_with(word, NODE_GLOBAL_SHORT_FLAG) for word in words):
        return True
    return any(word == "--location" and following == "global" for word, following in zip(words, words[1:]))


def package_runner_fetches(words: list[str]) -> bool:
    for word in words[1:]:
        if word in NPX_LOCAL_ONLY:
            return False
        if not word.startswith("-"):
            return True
    return True


def creates_from_a_package(program: str, words: list[str], action: str) -> bool:
    if action not in NODE_CREATORS.get(program, frozenset()):
        return False
    if program == "npm" and action in NPM_INIT:
        return bool(arguments(words[words.index(action):]))
    return True


def is_node_install(program: str, words: list[str], settings: dict) -> bool:
    if program == "npx" or program in FETCHING_RUNNERS:
        return package_runner_fetches(words)
    if program not in NODE_PACKAGE_MANAGERS:
        return False
    action = subcommand(words)
    if program == "npm" and action in NPM_RUNS_A_PACKAGE:
        return package_runner_fetches(words[words.index(action):])
    if action in FETCHING_SUBCOMMANDS.get(program, frozenset()):
        return package_runner_fetches(words[words.index(action):])
    if creates_from_a_package(program, words, action):
        return True
    if program == "yarn":
        given = arguments(words)
        return given[:1] == ["global"] and len(given) > 1
    return action in NODE_INSTALLS and has_global_flag(words, settings)


def is_python_tool_install(program: str, words: list[str], settings: dict) -> bool:
    given = arguments(words)
    if program == "pipx":
        return bool(given) and given[0] in PIPX_INSTALLS
    if program != "uv":
        return False
    if given[:1] == ["tool"]:
        return len(given) > 1 and given[1] in UV_TOOL_INSTALLS
    if given[:2] == ["python", "install"]:
        return True
    if given[:2] == ["pip", "install"]:
        return "--system" in words or is_true(settings.get(UV_SYSTEM_SETTING, ""))
    if given[:1] == ["run"]:
        return any(word.startswith(UV_RUN_FETCH_OPTIONS) for word in words)
    return False


def is_dpkg_install(words: list[str]) -> bool:
    for word in words[1:]:
        if word in DPKG_INSTALL_LONG:
            return True
        if word.startswith("-") and not word.startswith("--") and "i" in word[1:]:
            return True
    return False


def first_rpm_mode(words: list[str]) -> str:
    for word in words[1:]:
        if not word.startswith("-") or word.startswith("--"):
            continue
        mode = next((letter for letter in word[1:] if letter in RPM_MODES), "")
        if mode:
            return mode
    return ""


def is_rpm_install(words: list[str]) -> bool:
    if any(word in RPM_INSTALL_LONG for word in words[1:]):
        return True
    return first_rpm_mode(words) in RPM_INSTALL_MODES


def is_pacman_install(words: list[str]) -> bool:
    for word in words[1:]:
        if word in PACMAN_SYNC_LONG:
            return True
        if not word.startswith("-") or word.startswith("--"):
            continue
        flags = word[1:]
        if flags.startswith(PACMAN_UPGRADE):
            return True
        only_refreshes = set(flags[1:]) <= PACMAN_REFRESH and not arguments(words)
        if flags.startswith(PACMAN_SYNC) and not PACMAN_SYNC_QUERIES & set(flags[1:]) and not only_refreshes:
            return True
    return False


def fetches_a_go_module(words: list[str]) -> bool:
    return any(not module.startswith(LOCAL_PATH_START) for module in arguments(words)[1:])


def is_python_module_install(words: list[str], settings: dict, reading: Reading) -> bool:
    if not PYTHON.match(name_of(words[0])) or pip_words(words) is not None:
        return False
    module, rest = python_module(words)
    if module in PYTHON_INSTALL_MODULES:
        return not lives_in_a_venv(words[0], reading)
    return module == "pipx" and is_python_tool_install("pipx", rest, settings)


def runs_a_fetched_module(program: str, words: list[str]) -> bool:
    if program == "go" and subcommand(words) == GO_RUN:
        return any(GO_MODULE_VERSION in word for word in arguments(words)[1:])
    return program == DENO and any(URL_MARK in word for word in words[1:])


def before_the_end_of_options(words: list[str]) -> list[str]:
    return words[:words.index(END_OF_OPTIONS)] if END_OF_OPTIONS in words else words


def help_flags_of(program: str) -> frozenset:
    return HELP_FLAGS if program in SHORT_HELP_PROGRAMS else LONG_HELP_FLAGS


def is_a_lone_query(program: str, words: list[str]) -> bool:
    action = subcommand(words)
    rest = words[words.index(action, 1) + 1:] if action else words[1:]
    return len(rest) == 1 and rest[0] in help_flags_of(program) | OTHER_QUERY_FLAGS


def package_running_actions(program: str) -> frozenset:
    actions = FETCHING_SUBCOMMANDS.get(program, frozenset()) | NODE_CREATORS.get(program, frozenset())
    if program == "npm":
        actions |= NPM_RUNS_A_PACKAGE
    if program in PROGRAMS_THAT_RUN_A_PACKAGE:
        actions |= {VENV_RUN}
    return actions


def package_runner_words(program: str, words: list[str]) -> list[str] | None:
    if program == "npx" or program in FETCHING_RUNNERS:
        return words[1:]
    action = subcommand(words)
    if not action:
        return None
    index = words.index(action, 1)
    if program == "uv" and action == UV_TOOL:
        rest = words[index:]
        return rest[rest.index(VENV_RUN, 1) + 1:] if subcommand(rest) == VENV_RUN else None
    return words[index + 1:] if action in package_running_actions(program) else None


def queries_before_the_package(words: list[str]) -> bool:
    for word in words:
        if word in QUERY_FLAGS:
            return True
        if not word.startswith("-"):
            return False
    return False


def only_asks_for_help_or_version(program: str, words: list[str]) -> bool:
    runner_words = package_runner_words(program, words)
    if runner_words is not None:
        return queries_before_the_package(runner_words)
    if is_a_lone_query(program, words):
        return True
    asks_for_help = help_flags_of(program) & set(before_the_end_of_options(words[1:]))
    return bool(asks_for_help) and program not in ALWAYS_INSTALLERS


def installs_directly(words: list[str], activated: bool, settings: dict, reading: Reading) -> bool:
    program = name_of(words[0])
    if only_asks_for_help_or_version(program, words):
        return False
    if program in ALWAYS_INSTALLERS:
        return True
    if program == RUSTUP:
        return subcommand(words) not in RUSTUP_QUERIES
    if runs_a_fetched_module(program, words):
        return True
    if program in APT_FAMILY:
        return subcommand(words, APT_VALUE_OPTIONS) in APT_INSTALLS
    if program == "dpkg":
        return is_dpkg_install(words)
    if program == RPM:
        return is_rpm_install(words)
    if program in PACMAN_FAMILY:
        return is_pacman_install(words)
    if program in TWO_WORD_INSTALLS:
        return tuple(arguments(words)[:2]) in TWO_WORD_INSTALLS[program]
    if program == "go" and subcommand(words) == GO_FETCH:
        return fetches_a_go_module(words)
    if program in INSTALL_SUBCOMMANDS:
        return subcommand(words, SUBCOMMAND_VALUE_OPTIONS.get(program, frozenset())) in INSTALL_SUBCOMMANDS[program]
    return (is_python_module_install(words, settings, reading) or is_pip_install(words, activated, reading)
            or is_setup_install(words) or is_node_install(program, words, settings)
            or is_python_tool_install(program, words, settings))


def is_known_program(program: str) -> bool:
    return bool(program in ALWAYS_INSTALLERS or program == RUSTUP or program in APT_FAMILY or program in ("dpkg", RPM)
                or program in INSTALL_SUBCOMMANDS
                or program in PACMAN_FAMILY or program in TWO_WORD_INSTALLS
                or PIP.match(program) or PYTHON.match(program) or program in NODE_PACKAGE_MANAGERS
                or program in ("npx", "pipx", "uv") or program in FETCHING_RUNNERS)


def is_command_name(word: str) -> bool:
    program = name_of(word)
    return bool("/" in word or is_known_program(program) or is_interpreter(program) or program in TEXT_ONLY_PROGRAMS
                or program in DOWNLOADERS or program in WRAPPER_VALUE_OPTIONS or program in NESTED_COMMAND_OPTIONS
                or program in COMMAND_STRING_PROGRAMS or program in VENV_RUNNERS or program in EXPORTING
                or program in COMMAND_RUNNERS or program == EVAL
                or program in KEYWORDS_BEFORE_A_COMMAND or program in HEADER_KEYWORDS or program == FUNCTION_KEYWORD)


def names_a_known_program(line: str) -> bool:
    for segment in segments(line):
        words, _ = command_words(segment.words)
        if any(is_command_name(word) for word in segment.words[:1] + words[:1]):
            return True
    return False


def is_device(path: str) -> bool:
    return path.startswith(DEVICE_FOLDER)


def downloaded_names(words: list[str]) -> set[str]:
    program = name_of(words[0])
    names, urls = set(), [word for word in words[1:] if "://" in word]
    remote_names = {PurePosixPath(url.split("?", 1)[0].rstrip("/")).name for url in urls}
    index, saved_somewhere, output_flag = 1, False, OUTPUT_SHORT_FLAGS.get(program, "")
    while index < len(words):
        word = words[index]
        value = words[index + 1] if index + 1 < len(words) else ""
        short = word.startswith("-") and not word.startswith("--")
        if word in ("--output", "--output-document"):
            names.add(value)
            saved_somewhere = True
        elif word.startswith(("--output=", "--output-document=")):
            names.add(word.split("=", 1)[1])
            saved_somewhere = True
        elif short and output_flag and output_flag in word[1:]:
            names.add(word[word.index(output_flag, 1) + 1:] or value)
            saved_somewhere = True
        elif word == "--remote-name" or (short and program == "curl" and "O" in word[1:]):
            names |= remote_names
            saved_somewhere = True
        index += 1
    if program in SAVES_UNDER_THE_REMOTE_NAME and not saved_somewhere:
        names |= remote_names
    return {name_of(name) for name in names if name and name != "-" and not is_device(name)}


def saved_by_redirect(segment: Segment) -> set[str]:
    return {name_of(target) for operator, target in segment.redirects
            if operator in SAVING_REDIRECTS and target and not is_device(target)}


def folder_of_clone(given: list[str]) -> set[str]:
    if len(given) > 1:
        return {name_of(given[1].rstrip("/"))}
    if given:
        return {name_of(given[0].rstrip("/")).removesuffix(GIT_SUFFIX)}
    return set()


def cloned_folder(words: list[str]) -> set[str]:
    rest = words[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in GIT_VALUE_OPTIONS else rest[1:]
    if rest[:len(SUBMODULE_ADD)] == SUBMODULE_ADD:
        return folder_of_clone(positional_words(rest[len(SUBMODULE_ADD):], CLONE_VALUE_OPTIONS))
    if rest[:1] != ["clone"]:
        return set()
    return folder_of_clone(positional_words(rest[1:], CLONE_VALUE_OPTIONS))


def gh_cloned_folder(words: list[str]) -> set[str]:
    given = arguments(words)
    if given[:len(GH_CLONE)] != GH_CLONE:
        return set()
    return folder_of_clone(given[len(GH_CLONE):])


def touches_download(word: str, downloads: set[str]) -> bool:
    return any(part in downloads for part in PurePosixPath(word).parts if part not in PATH_PARTS_NEVER_NAMED)


def lies_in_an_unpacked_folder(word: str, reading: Reading) -> bool:
    return any(word.startswith(folder + "/") for folder in reading.unpacked_folders)


def comes_from_a_download(word: str, reading: Reading) -> bool:
    return bool(word) and (touches_download(word, reading.downloads) or lies_in_an_unpacked_folder(word, reading))


def is_interpreter(program: str) -> bool:
    return bool(SHELLS_AND_INTERPRETERS.match(program))


def short_flags(words: list[str]) -> str:
    flags = []
    for word in words[1:]:
        if not word.startswith("-") or word == "-":
            break
        if not word.startswith("--"):
            flags.append(word[1:])
    return "".join(flags)


def script_of(words: list[str]) -> str:
    program = name_of(words[0])
    if PYTHON.match(program):
        return python_script(words)
    if SHELLS.match(program):
        return shell_script(words)
    given = arguments(words)
    if program == JAVA:
        return next((following for word, following in zip(words, words[1:]) if word == JAR_OPTION), "")
    if given[:1] == [VENV_RUN] and program in SCRIPT_RUNNERS_WITH_RUN:
        given = given[1:]
    return (given[:1] or [""])[0]


def normalised_path(word: str) -> str:
    if not word.startswith("/"):
        return word
    return posixpath.normpath(REPEATED_SLASHES.sub("/", word))


def is_descriptor_path(word: str) -> bool:
    return bool(DESCRIPTOR_PATH.match(normalised_path(word)))


def is_stdin_name(word: str) -> bool:
    return word in STDIN_NAMES or is_descriptor_path(word)


def reads_program_from_stdin(words: list[str]) -> bool:
    program = name_of(words[0])
    if PYTHON.match(program):
        module, _, runs_code, index = python_flags(words)
        return not module and not runs_code and (index >= len(words) or is_stdin_name(words[index]))
    if program in SOURCING:
        return is_stdin_name((arguments(words)[:1] or [""])[0])
    flags = short_flags(words)
    if SHELLS.match(program):
        if "c" in flags:
            return False
        if "s" in flags:
            return True
    elif CODE_FLAG_INTERPRETERS.match(program) and ("e" in flags or "r" in flags):
        return False
    script = script_of(words)
    return not script or is_stdin_name(script)


def reads_commands_from_stdin(words: list[str]) -> bool:
    program = name_of(words[0])
    if program in COMMAND_STDIN_READERS:
        return True
    if program == SWITCH_USER:
        return not any(hands_over_a_command_string(word) for word in words[1:])
    if SHELLS.match(program) or program in SOURCING:
        return reads_program_from_stdin(words)
    return any(reads_program_from_stdin(shell) for shell in later_shells(program, words))


def is_make_install(words: list[str]) -> bool:
    return name_of(words[0]) == MAKE and MAKE_INSTALL_TARGET in arguments(words)


def makes_install_in_a_download(words: list[str], downloads: set[str]) -> bool:
    if not is_make_install(words):
        return False
    folders = [following for word, following in zip(words, words[1:]) if word in MAKE_DIRECTORY_OPTIONS]
    folders += [word.split("=", 1)[1] for word in words if word.startswith("--directory=")]
    return any(touches_download(folder, downloads) for folder in folders)


def runs_local_code(words: list[str]) -> bool:
    program = name_of(words[0])
    if "/" in words[0] and not words[0].startswith("/"):
        return True
    if SHELLS.match(program) or program in SOURCING:
        script = script_of(words) if SHELLS.match(program) else (arguments(words)[:1] or [""])[0]
        return bool(script) and not is_stdin_name(script)
    return is_make_install(words)


def names_a_download(word: str, reading: Reading) -> bool:
    if comes_from_a_download(word, reading):
        return True
    if not GLOB_CHARACTERS & set(word):
        return False
    parts = PurePosixPath(word).parts
    reading.budget.spend(len(parts) * len(reading.downloads))
    return any(fnmatch.fnmatchcase(download, part) for part in parts for download in reading.downloads)


def option_values(words: list[str], letters: str, long_options: frozenset) -> list[str]:
    found = []
    for word, following in zip(words[1:], words[2:] + [""]):
        name, has_value, value = word.partition("=")
        if name in long_options:
            found.append(value if has_value else following)
        elif letters and word.startswith("-") and not word.startswith("--") and word[-1] in letters:
            found.append(following)
    return found


def hands_a_download_to_an_option(program: str, words: list[str], reading: Reading) -> bool:
    letters, long_options = PROGRAM_FILE_OPTIONS.get(program, NO_PROGRAM_OPTIONS)
    checked = option_values(words, letters, long_options)
    if program not in TAR_PROGRAMS:
        checked += [word for word in words[1:] if word.startswith(OPTION_STARTS)]
    return any(names_a_download(word, reading) for word in checked)


def launches_local_code(words: list[str]) -> bool:
    for index, word in enumerate(words[1:], start=1):
        if word.startswith(RELATIVE_PATH_STARTS):
            return True
        program = name_of(word)
        if is_interpreter(program) and program not in SOURCING and runs_local_code(words[index:]):
            return True
    return False


def is_data_file(word: str) -> bool:
    return PurePosixPath(word).suffix.lower() in DATA_SUFFIXES


def feeds_downloaded_data_to_a_local_script(words: list[str], reading: Reading) -> bool:
    program = name_of(words[0])
    if not is_interpreter(program) or program in SOURCING or takes_code_from_an_argument(words):
        return False
    module = python_module(words)[0] if PYTHON.match(program) else ""
    if module and module not in DATA_READING_MODULES:
        return False
    code = module or script_of(words)
    if not code or names_a_download(code, reading):
        return False
    return all(is_data_file(word) for word in words[1:] if names_a_download(word, reading))


def script_word(words: list[str]) -> str:
    program = name_of(words[0])
    if PYTHON.match(program) or SHELLS.match(program):
        return script_of(words)
    following = (word for word in words[1:] if not word.startswith("-"))
    first = next(following, "")
    return next(following, "") if first == VENV_RUN and program in SCRIPT_RUNNERS_WITH_RUN else first


def is_unresolved(word: str) -> bool:
    return bool(UNRESOLVED_SCRIPT_MARKS & set(word))


def gives_an_interpreter_an_unresolved_script(words: list[str], reading: Reading) -> bool:
    for index, word in enumerate(words):
        if not is_interpreter(name_of(word)):
            continue
        rest = words[index:]
        reading.budget.spend(len(rest))
        if not takes_code_from_an_argument(rest) and is_unresolved(script_word(rest)):
            return True
    return False


def names_a_download_to_run(words: list[str], reading: Reading) -> bool:
    if not (reading.downloads or reading.unpacked_folders or reading.inside_download):
        return False
    program = name_of(words[0])
    if program in DOWNLOAD_HANDLERS:
        return hands_a_download_to_an_option(program, words, reading)
    if program in DATA_FILE_VIEWERS:
        return any(names_a_download(word, reading) and not is_data_file(word) for word in words[1:])
    if gives_an_interpreter_an_unresolved_script(words, reading):
        return True
    if feeds_downloaded_data_to_a_local_script(words, reading):
        return False
    if any(names_a_download(word, reading) for word in words[1:]):
        return True
    return reading.inside_download and launches_local_code(words)


def runs_a_download(words: list[str], segment: Segment, reading: Reading) -> bool:
    if reading.inside_download and runs_local_code(words):
        return True
    downloads = reading.downloads
    if not downloads and not reading.unpacked_folders:
        return False
    if comes_from_a_download(words[0], reading) or makes_install_in_a_download(words, downloads):
        return True
    if not is_interpreter(name_of(words[0])) and name_of(words[0]) != JAVA:
        return False
    if comes_from_a_download(script_of(words), reading):
        return True
    fed = any(operator.startswith("<") and not operator.startswith(HERE_DOCUMENT) and touches_download(target, downloads)
              for operator, target in segment.redirects)
    return fed and reads_program_from_stdin(words)


def is_pipe(operator: str) -> bool:
    return "|" in operator and "||" not in operator


def shell_command_strings(words: list[str]) -> list[str]:
    found = []
    for index, word in enumerate(words[:-1]):
        if word.startswith("-") and not word.startswith("--") and word.endswith("c"):
            following = words[index + 1]
            if following == "--" and index + 2 < len(words):
                following = words[index + 2]
            found.append(following)
    return found


def setting_command(name: str, value: str) -> str | None:
    if value.startswith(GIT_SHELL_ALIAS):
        return value[len(GIT_SHELL_ALIAS):]
    key = name.lower()
    return value if key in GIT_COMMAND_SETTINGS or key.endswith(GIT_COMMAND_SETTING_ENDINGS) else None


def git_config_settings(words: list[str]) -> list[tuple[str, str]]:
    if subcommand(words, GIT_VALUE_OPTIONS) != GIT_CONFIG:
        return []
    positional = positional_words(words[words.index(GIT_CONFIG, 1) + 1:], GIT_CONFIG_VALUE_OPTIONS)
    if positional[:1] == [GIT_CONFIG_SET]:
        positional = positional[1:]
    return [(positional[0], positional[1])] if len(positional) > 1 else []


def git_settings(words: list[str]) -> list[tuple[str, str]]:
    given = [tuple(following.partition("=")[::2]) for word, following in zip(words[1:], words[2:]) if word == GIT_CONFIG_OPTION]
    return given + git_config_settings(words)


def git_setting_commands(words: list[str]) -> list[str]:
    commands = [setting_command(name, value) for name, value in git_settings(words)]
    return [command for command in commands if command is not None]


def sets_a_git_hooks_folder(words: list[str]) -> bool:
    return name_of(words[0]) == GIT and any(name.lower().endswith(GIT_HOOKS_SETTING_ENDING) for name, _ in git_settings(words))


def settings_that_name_a_command(words: list[str], settings: dict) -> list[str]:
    assigned = dict(settings)
    if name_of(words[0]) in EXPORTING:
        for word in words[1:]:
            if ENV_ASSIGNMENT.match(word):
                remember_setting(assigned, word)
    return [value for name, value in assigned.items() if COMMAND_SETTING.match(name)]


def tar_command_strings(words: list[str]) -> list[str]:
    found = []
    for word, following in zip(words[1:], words[2:] + [""]):
        name, has_value, value = word.partition("=")
        command = value if has_value else following
        if name in TAR_COMMAND_OPTIONS:
            found.append(command)
        elif name == TAR_CHECKPOINT_ACTION and command.startswith(TAR_EXEC_ACTION):
            found.append(command[len(TAR_EXEC_ACTION):])
    return found


def nested_commands(words: list[str]) -> list[str]:
    program = name_of(words[0])
    if program == "eval":
        return [" ".join(words[1:])]
    if program == TRAP:
        return arguments(words)[:1]
    if program == SWITCH_GROUP:
        return [" ".join(arguments(words)[1:])]
    if program == GIT:
        return git_setting_commands(words)
    if program in TAR_PROGRAMS:
        return tar_command_strings(words)
    if program in POWERSHELLS:
        return [command for command in [powershell_command(words)] if command]
    if program in NESTED_COMMAND_OPTIONS:
        options = NESTED_COMMAND_OPTIONS[program]
        return [following for word, following in zip(words, words[1:]) if word in options]
    if is_interpreter(program) and not PYTHON.match(program):
        return shell_command_strings(words)
    return []


def has_whitespace(word: str) -> bool:
    return any(character.isspace() for character in word)


def runs_in_a_project_venv(words: list[str], reading: Reading) -> bool:
    rest = words[words.index(VENV_RUN) + 1:]
    return bool(rest) and installs_directly(rest, True, {}, reading)


def hands_over_a_command_string(option: str) -> bool:
    return option in COMMAND_OPTIONS or is_short_cluster_with(option, COMMAND_STRING_FLAG)


def runs_a_command_in_a_conda_env(program: str, words: list[str]) -> bool:
    return program in CONDA_FAMILY and subcommand(words, CONDA_VALUE_OPTIONS) == VENV_RUN


def mentions_an_install_elsewhere(words: list[str], settings: dict, reading: Reading) -> bool:
    program = name_of(words[0])
    if program in TEXT_ONLY_PROGRAMS:
        return False
    if program in VENV_RUNNERS and subcommand(words) == VENV_RUN:
        return runs_in_a_project_venv(words, reading)
    reads_quoted_commands = program in COMMAND_STRING_PROGRAMS
    looks_for_a_program = not is_known_program(program) or runs_a_command_in_a_conda_env(program, words)
    for index, word in enumerate(words[1:], start=1):
        if has_whitespace(word):
            if (reads_quoted_commands or hands_over_a_command_string(words[index - 1])) and reading.nested(word, settings):
                return True
        elif looks_for_a_program and is_known_program(name_of(word)):
            if installs_directly(words[index:], False, {}, reading):
                return True
            looks_for_a_program = False
    return False


def piped_payload(words: list[str]) -> str | None:
    if name_of(words[0]) not in ("echo", "printf"):
        return None
    return " ".join(word.replace("\\n", "\n") for word in words[1:] if not word.startswith("-"))


def substitution_runs_a_download(segment: Segment, program: str, state: LineState) -> bool:
    if program not in DOWNLOADERS:
        return False
    opened_by_process = PROCESS_SUBSTITUTION in segment.joined_by
    opened_by_dollar = "(" in segment.joined_by and state.previous_ends_in_substitution
    runner = state.previous_program
    return (opened_by_process or opened_by_dollar) and (runner == SUBSTITUTION_OPENER or is_interpreter(runner))


def unpacks_an_archive(words: list[str]) -> bool:
    program = name_of(words[0])
    if program in ZIP_UNPACKERS:
        return True
    if program in SEVEN_ZIP_UNPACKERS:
        return subcommand(words) in SEVEN_ZIP_EXTRACTS
    if program not in TAR_PROGRAMS or len(words) < 2:
        return False
    if not words[1].startswith("-") and TAR_EXTRACT_FLAG in words[1]:
        return True
    return any(word in TAR_EXTRACT_LONG or is_short_cluster_with(word, TAR_EXTRACT_FLAG) for word in words[1:])


def decompresses(program: str, words: list[str]) -> bool:
    return program in DECOMPRESSORS or (program in COMPRESSORS and any(word in DECOMPRESS_FLAGS for word in words))


def decompressed_names(words: list[str], downloads: set[str]) -> set[str]:
    names = set()
    for word in arguments(words):
        if touches_download(word, downloads):
            name = name_of(word)
            stem = next((name[:-len(suffix)] for suffix in COMPRESSED_SUFFIXES if name.endswith(suffix)), name)
            names.add(stem + ".tar" if name.endswith(".tgz") else stem)
    return names


def tar_cluster_folders(words: list[str]) -> set[str]:
    folders = set()
    for index, word in enumerate(words[1:], start=1):
        old_style = index == 1 and not word.startswith("-")
        if TAR_FOLDER_FLAG not in word or not (old_style or is_short_cluster_with(word, TAR_FOLDER_FLAG)):
            continue
        cluster, values = (word if old_style else word[1:]), iter(words[index + 1:])
        for position, flag in enumerate(cluster):
            if flag not in TAR_VALUE_FLAGS:
                continue
            attached = "" if old_style else cluster[position + 1:]
            value = attached or next(values, "")
            if flag == TAR_FOLDER_FLAG:
                folders.add(value.rstrip("/"))
            if not old_style:
                break
    return folders


def unpacked_folders(words: list[str]) -> set[str]:
    folders = {following.rstrip("/") for word, following in zip(words, words[1:]) if word in UNPACK_FOLDER_OPTIONS}
    folders |= {word.split("=", 1)[1].rstrip("/") for word in words if word.startswith("--directory=")}
    if name_of(words[0]) in TAR_PROGRAMS:
        folders |= tar_cluster_folders(words)
    return {folder for folder in folders if folder}


def remember_unpacking(words: list[str], reading: Reading):
    reading.inside_download = True
    reading.unpacked_folders |= unpacked_folders(words)


def remember_clone(folders: set[str], reading: Reading):
    if CURRENT_FOLDER in folders:
        reading.inside_download = True
    reading.downloads |= folders - {CURRENT_FOLDER}


def dd_operands(words: list[str], name: str) -> list[str]:
    return [word.split("=", 1)[1] for word in words[1:] if word.startswith(name + "=")]


def remember_downloads(words: list[str], segment: Segment, state: LineState, reading: Reading):
    program = name_of(words[0])
    names_a_download = any(touches_download(word, reading.downloads) for word in words[1:])
    if state.carries_download or names_a_download:
        reading.downloads |= saved_by_redirect(segment)
    if program in DOWNLOADERS:
        state.carries_download = True
        reading.downloads |= downloaded_names(words) | saved_by_redirect(segment)
    elif program == "git":
        remember_clone(cloned_folder(words), reading)
    elif program == "gh":
        remember_clone(gh_cloned_folder(words), reading)
    elif state.carries_download and program == "tee":
        reading.downloads |= {name_of(name) for name in arguments(words)}
    elif program in DIRECTORY_CHANGERS and names_a_download:
        reading.inside_download = True
    elif unpacks_an_archive(words) and (state.carries_download or names_a_download):
        remember_unpacking(words, reading)
    elif decompresses(program, words) and names_a_download:
        reading.downloads |= decompressed_names(words, reading.downloads)
        state.carries_download = True
    elif program in COPIERS and names_a_download:
        reading.downloads |= {name_of(arguments(words)[-1])}
    elif program == DD and (state.carries_download or any(touches_download(path, reading.downloads)
                                                          for path in dd_operands(words, DD_INPUT))):
        reading.downloads |= {name_of(path) for path in dd_operands(words, DD_OUTPUT)}
    elif names_a_download:
        state.carries_download = True


def remember_environment(program: str, words: list[str], reading: Reading):
    if program in EXPORTING:
        for word in words[1:]:
            if ENV_ASSIGNMENT.match(word):
                remember_setting(reading.exported, word)


def awaits_a_script(program: str, words: list[str]) -> bool:
    if program in SOURCING:
        return not arguments(words)
    if not SHELLS.match(program):
        return False
    return not takes_code_from_an_argument(words) and not shell_script(words)


def note_previous(segment: Segment, program: str, words: list[str], state: LineState):
    state.previous_program = program
    state.previous_ends_in_substitution = segment.last_token.endswith(SUBSTITUTION_OPENER)
    state.previous_awaits_a_script = bool(words) and awaits_a_script(program, words)


def runs_a_process_substitution(segment: Segment, words: list[str], state: LineState, reading: Reading) -> bool:
    if PROCESS_SUBSTITUTION not in segment.joined_by or not state.previous_awaits_a_script:
        return False
    payload = piped_payload(words)
    return payload is None or reading.nested(payload)


def is_unreadable_command(word: str) -> bool:
    return SUBSTITUTION_OPENER in word and SUBSTITUTION_OPENER in PATH_VARIABLE.sub("", word, count=1)


def continues_a_word(segment: Segment) -> bool:
    return bool(segment.joined_by) and not segment.joined_by.strip(SUBSTITUTION_CLOSER)


def runs_unread_or_split_text(segment: Segment, words: list[str], reading: Reading) -> bool:
    if continues_a_word(segment):
        return False
    if is_unreadable_command(words[0]):
        return True
    return has_whitespace(words[0]) and reading.nested(" ".join(words))


def shell_option_letters(words: list[str]) -> str:
    letters, index = [], 1
    while index < len(words) and words[index] != END_OF_OPTIONS and is_a_shell_option(words[index]):
        word = words[index]
        if not word.startswith("--"):
            letters.append(word[1:])
        index += 2 if takes_a_shell_value(word) else 1
    return "".join(letters)


def may_be_interactive(words: list[str]) -> bool:
    if INTERACTIVE_FLAG in shell_option_letters(words):
        return True
    return not (takes_code_from_an_argument(words) or shell_script(words))


def reads_a_start_up_file(program: str, words: list[str], settings: dict) -> bool:
    shells = [words] if SHELLS.match(program) else later_shells(program, words)
    return any(shell_reads_a_start_up_file(shell, settings) for shell in shells)


def shell_reads_a_start_up_file(words: list[str], settings: dict) -> bool:
    if START_UP_SETTINGS & settings.keys():
        return True
    if INTERACTIVE_START_UP_SETTINGS & settings.keys() and may_be_interactive(words):
        return True
    return any(word in SHELL_LONG_VALUE_OPTIONS and not is_device(following) for word, following in zip(words, words[1:] + [""]))


def powershell_option(word: str) -> str:
    option = word.lower()
    return option[1:] if option.startswith("--") else option


def is_powershell_encoded_option(word: str) -> bool:
    option = powershell_option(word)
    if len(option) < 2 or not option.startswith("-"):
        return False
    return option in POWERSHELL_ENCODED_ALIASES or POWERSHELL_ENCODED_OPTION.startswith(option)


def is_powershell_command_option(word: str) -> bool:
    option = powershell_option(word)
    if len(option) < 2 or not option.startswith("-"):
        return False
    return option in POWERSHELL_COMMAND_ALIASES or any(full.startswith(option) for full in POWERSHELL_COMMAND_OPTIONS)


def powershell_command(words: list[str]) -> str | None:
    for index, word in enumerate(words[1:], start=1):
        if is_powershell_command_option(word):
            return " ".join(words[index + 1:])
    return None


def is_powershell_word_that_runs_or_fetches(word: str) -> bool:
    lowered = word.lower()
    return lowered in POWERSHELL_RUNNERS or lowered in POWERSHELL_DEFINERS or lowered.startswith(POWERSHELL_FETCHING_VERBS)


def powershell_runs_or_fetches(text: str) -> bool:
    lowered = text.lower()
    if any(mark in lowered for mark in POWERSHELL_DOWNLOAD_MARKS + POWERSHELL_UNREAD_MARKS):
        return True
    if POWERSHELL_CALL.search(text) or POWERSHELL_JOINED_STRINGS.search(text):
        return True
    return any(is_powershell_word_that_runs_or_fetches(word) for word in POWERSHELL_COMMAND_WORD.findall(text))


def has_a_powershell_encoded_option(words: list[str]) -> bool:
    return any(is_powershell_encoded_option(word) for word in words[1:])


def powershell_text_is_unread(words: list[str]) -> bool:
    if has_a_powershell_encoded_option(words):
        return True
    command = powershell_command(words)
    return command is not None and (command in ("", POWERSHELL_STDIN) or powershell_runs_or_fetches(command))


def is_powershell_file_option(word: str) -> bool:
    option = powershell_option(word)
    return len(option) > 1 and POWERSHELL_FILE_OPTION.startswith(option)


def powershell_script(words: list[str]) -> str:
    index = 1
    while index < len(words):
        word = words[index]
        if is_powershell_file_option(word):
            return words[index + 1] if index + 1 < len(words) else ""
        if word == POWERSHELL_STDIN or not word.startswith("-"):
            return word
        index += 2 if powershell_option(word) in POWERSHELL_VALUE_OPTIONS else 1
    return ""


def powershell_reads_commands_from_stdin(words: list[str]) -> bool:
    if powershell_command(words) is not None or has_a_powershell_encoded_option(words):
        return False
    return powershell_script(words) in ("", POWERSHELL_STDIN)


def has_an_input_redirect(segment: Segment) -> bool:
    return any(operator.startswith("<") for operator, _ in segment.redirects)


def feeds_standard_input(segment: Segment) -> bool:
    return is_pipe(segment.joined_by) or has_an_input_redirect(segment)


def opens_a_descriptor(segment: Segment, segment_words: list[str]) -> bool:
    return segment_words[:1] == [EXEC] and has_an_input_redirect(segment)


def reads_an_opened_descriptor(segment: Segment, words: list[str]) -> bool:
    if not is_interpreter(name_of(words[0])):
        return False
    if is_descriptor_path(script_word(words)):
        return True
    return any(operator == DESCRIPTOR_INPUT for operator, _ in segment.redirects) and reads_program_from_stdin(words)


def is_fed_as_arguments(segment_words: list[str], words: list[str]) -> bool:
    wrappers = segment_words[:len(segment_words) - len(words)]
    return any(name_of(word) == ARGUMENT_FEEDER for word in wrappers)


def here_string_installs(segment: Segment, words: list[str], reading: Reading) -> bool:
    texts = [target for operator, target in segment.redirects if operator == HERE_STRING]
    return bool(texts) and reads_commands_from_stdin(words) and any(reading.nested(text) for text in texts)


def has_a_command_string_cluster(words: list[str]) -> bool:
    for word in words[1:]:
        if word.startswith("--"):
            continue
        if not word.startswith("-"):
            return False
        if COMMAND_STRING_FLAG in word[1:-1]:
            return True
    return False


def hands_code_to_a_later_shell(program: str, words: list[str]) -> bool:
    return any(takes_code_from_an_argument(shell) for shell in later_shells(program, words))


def hands_text_to_an_unread_shell(segment: Segment, segment_words: list[str], words: list[str], piped_text: str | None) -> bool:
    program = name_of(words[0])
    if hands_code_to_a_later_shell(program, words):
        return True
    if program in POWERSHELLS and powershell_text_is_unread(words):
        return True
    if program in POWERSHELLS and powershell_reads_commands_from_stdin(words) and feeds_standard_input(segment):
        return True
    if SHELLS.match(program) and (has_a_command_string_cluster(words) or is_fed_as_arguments(segment_words, words)):
        return True
    return is_pipe(segment.joined_by) and piped_text is None and reads_commands_from_stdin(words)


def fed_a_download_as_code(segment_words: list[str], words: list[str], state: LineState) -> bool:
    program = name_of(words[0])
    if not state.carries_download or not is_interpreter(program):
        return False
    return reads_program_from_stdin(words) or (is_fed_as_arguments(segment_words, words) and not script_of(words))


def takes_code_from_an_argument(words: list[str]) -> bool:
    program = name_of(words[0])
    if PYTHON.match(program):
        return python_flags(words)[2]
    flags = set(short_flags(words))
    if SHELLS.match(program):
        return COMMAND_STRING_FLAG in flags
    return bool(CODE_FLAG_INTERPRETERS.match(program)) and bool(flags & CODE_FLAGS)


def takes_downloaded_code_as_an_argument(words: list[str]) -> bool:
    if not takes_code_from_an_argument(words):
        return False
    return any(name_of((inner.split() or [""])[0]) in DOWNLOADERS for word in words[1:] for inner in substitutions(word))


def judge_segment(segment: Segment, state: LineState, reading: Reading) -> bool:
    if segment.words == GROUP_CLOSE and not segment.redirects:
        return False
    segment_words = [expanded(word, reading.variables) for word in segment.words]
    words, settings = command_words(segment_words)
    piped = is_pipe(segment.joined_by)
    state.carries_download = state.carries_download and piped
    piped_text = state.piped_text if piped else None
    state.piped_text = None
    if segment.joined_by != "&&":
        state.activated = False
    if not words:
        remember_variables(settings, reading)
        reading.opened_descriptors = reading.opened_descriptors or opens_a_descriptor(segment, segment_words)
        note_previous(segment, "", [], state)
        return False
    named_commands = settings_that_name_a_command(words, settings)
    settings = {**reading.exported, **settings}
    program = name_of(words[0])
    if substitution_runs_a_download(segment, program, state) or runs_a_process_substitution(segment, words, state, reading):
        return True
    note_previous(segment, program, words, state)
    if reading.opened_descriptors and reads_an_opened_descriptor(segment, words):
        return True
    if runs_unread_or_split_text(segment, words, reading) or reads_a_start_up_file(program, words, settings):
        return True
    if hands_text_to_an_unread_shell(segment, segment_words, words, piped_text if piped else None):
        return True
    if here_string_installs(segment, words, reading):
        return True
    if fed_a_download_as_code(segment_words, words, state) or takes_downloaded_code_as_an_argument(words):
        return True
    if piped_text is not None and reads_commands_from_stdin(words) and reading.nested(piped_text):
        return True
    if program == DEACTIVATE:
        state.activated = False
        return False
    if program in SOURCING and len(words) == 2 and ACTIVATE_SCRIPT.search(words[1]):
        state.activated = True
        return False
    venv = made_venv(words)
    if venv:
        reading.venvs.add(venv.rstrip("/"))
    if installs_directly(words, state.activated, settings, reading) or runs_a_download(words, segment, reading):
        return True
    if names_a_download_to_run(words, reading):
        return True
    if any(reading.nested(nested, settings) for nested in nested_commands(words) + named_commands) or sets_a_git_hooks_folder(words):
        return True
    remember_downloads(words, segment, state, reading)
    remember_environment(program, words, reading)
    state.piped_text = piped_text if program == "tee" else piped_payload(words)
    return mentions_an_install_elsewhere(words, settings, reading)


def is_unclean_here_document(segment: Segment, operator: str, target: str) -> bool:
    if operator != HERE_DOCUMENT:
        return False
    return not CLEAN_HERE_DOCUMENT_MARKER.match(target) or segment.words[:1] == [SHIFTING_COMMAND]


def has_an_unclean_here_document(line_segments: list[Segment]) -> bool:
    return any(is_unclean_here_document(segment, operator, target)
               for segment in line_segments for operator, target in segment.redirects)


def here_document_opener(found: re.Match) -> HereDocumentOpener:
    dash, single_quoted, double_quoted, bare = found.groups()
    if single_quoted is not None or double_quoted is not None:
        return HereDocumentOpener(single_quoted if single_quoted is not None else double_quoted, bool(dash), True)
    quoted = any(character in MARKER_QUOTING for character in bare)
    marker = "".join(character for character in bare if character not in MARKER_QUOTING)
    return HereDocumentOpener(marker, bool(dash), quoted)


def here_document_openers(line: str, quote: str = "") -> list[HereDocumentOpener]:
    outside, _ = scanned_code(line, quote)
    positions = [found.start() for found in HERE_DOCUMENT_OPERATOR.finditer(line)]
    return [here_document_opener(HERE_DOCUMENT_OPENER.match(line, position)) for position in positions
            if outside[position] and outside[position + 1]]


def here_documents(line: str, line_segments: list[Segment], lines: list[str], next_index: int,
                   open_quote: str) -> tuple[list[HereDocument], int] | None:
    if open_quote:
        return None if here_document_openers(line, open_quote) else ([], next_index)
    targets = [(segment, target) for segment in line_segments for operator, target in segment.redirects if operator == HERE_DOCUMENT]
    openers = here_document_openers(line) if targets else []
    if len(openers) != len(targets) or any(opener.as_a_token() != target for opener, (_, target) in zip(openers, targets)):
        return None
    found = []
    for opener, (segment, _) in zip(openers, targets):
        body, next_index = here_document_body(lines, next_index, opener)
        found.append(HereDocument(command_words(segment.words)[0], body, not opener.quoted))
    return found, next_index


def here_document_body(lines: list[str], next_index: int, opener: HereDocumentOpener) -> tuple[str, int]:
    body, continues = [], never_continues if opener.quoted else ends_with_a_line_join
    while next_index < len(lines):
        line, next_index = logical_line(lines, next_index, continues)
        if opener.ends_the_body(line):
            break
        body.append(line)
    return "\n".join(body), next_index


def never_continues(line: str) -> bool:
    return False


def here_document_installs(document: HereDocument, reading: Reading) -> bool:
    if document.words and reads_commands_from_stdin(document.words):
        return reading.nested(document.body)
    return document.expands and any(reading.nested(inner) for inner in substitutions(document.body))


def line_installs(line: str, line_segments: list[Segment], reading: Reading) -> bool:
    inner_commands = substitutions(line)
    if any(reading.nested(inner) for inner in inner_commands):
        return True
    known_before = reading.download_state()
    state = LineState()
    if any(judge_segment(segment, state, reading) for segment in line_segments):
        return True
    learned_a_download = reading.download_state() != known_before
    return learned_a_download and any(reading.nested(inner) for inner in inner_commands)


def reads_an_install(command: str, reading: Reading) -> bool:
    if reading.depth > MAX_NESTING:
        return True
    reading.budget.spend(len(command) + 1)
    lines = without_arithmetic(command).splitlines()
    index, open_quote = 0, ""
    while index < len(lines):
        line, index = logical_line(lines, index, continues_as_code)
        if is_comment(line) and not open_quote:
            continue
        quote_at_start, open_quote = open_quote, quote_left_open(line, open_quote)
        line_segments = segments(line)
        if has_an_unclean_here_document(line_segments):
            return True
        read = here_documents(line, line_segments, lines, index, quote_at_start)
        if read is None:
            return True
        documents, index = read
        if line_installs(line, line_segments, reading):
            return True
        if any(here_document_installs(document, reading) for document in documents):
            return True
    return False


def installs_software(command: str) -> bool:
    command = command or ""
    try:
        return reads_an_install(command, Reading(WorkBudget.for_command(command)))
    except WorkBudgetSpent:
        return True
