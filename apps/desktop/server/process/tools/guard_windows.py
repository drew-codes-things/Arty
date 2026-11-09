import re
from pathlib import Path


def _words(*names):
    return r"(?<![\w-])(?:" + "|".join(names) + r")(?![\w-])"


_CHANGES_FILES = _words(
    "remove-item", "ri", "del", "erase", "rd", "rmdir", "move-item", "mi", "move", "ren", "rename-item", "rni",
    "clear-content", "clc", "remove-itemproperty", "rp", "set-itemproperty", "sp", "new-itemproperty")
_CHANGES_THE_SYSTEM = "|".join([
    _words("taskkill", "stop-process", "spps", "stop-service", "spsv", "restart-service", "set-service",
           "shutdown", "restart-computer", "stop-computer", "logoff", "format", "diskpart", "format-volume",
           "clear-disk", "initialize-disk", "remove-partition", "bcdedit", "bootrec", "vssadmin", "wbadmin",
           "clear-eventlog", "mountvol", "chkdsk"),
    r"(?<![\w-])sc(?:\.exe)?\s+(?:stop|start|delete|config|create)(?![\w-])",
    r"(?<![\w-])net(?:\.exe)?\s+(?:stop|start|user|localgroup|share|use|accounts)(?![\w-])",
    r"(?<![\w-])cipher\s+/w", r"(?<![\w-])wevtutil\s+cl(?![\w-])",
])
_EDITS_THE_REGISTRY = r"(?<![\w-])(?:reg(?:\.exe)?\s+(?:add|delete|import|load|unload|save|restore|copy)|regedit)(?![\w-])"
_CHANGES_PERMISSIONS_OR_SOFTWARE = "|".join([
    _words("icacls", "takeown", "attrib", "cacls", "set-acl", "set-executionpolicy", "set-mppreference",
           "add-mppreference", "add-windowscapability", "remove-windowscapability", "add-appxpackage",
           "remove-appxpackage", "msiexec"),
    r"(?<![\w-])(?:disable|enable|uninstall)-\w+",
    r"(?<![\w-])install-(?:module|package|packageprovider|windowsfeature|script)(?![\w-])",
    r"(?<![\w-])winget\s+(?:install|uninstall|upgrade|import|source)(?![\w-])",
    r"(?<![\w-])choco(?:latey)?\s+(?:install|uninstall|upgrade|source)(?![\w-])",
    r"(?<![\w-])scoop\s+(?:install|uninstall|update)(?![\w-])",
])
_RUNS_CODE_OR_REACHES_OUT = "|".join([
    _words("invoke-expression", "iex", "invoke-command", "icm", "new-pssession", "enter-pssession", "runas", "mshta",
           "rundll32", "regsvr32", "certutil", "bitsadmin", "wmic", "cscript", "wscript", "installutil", "schtasks",
           "register-scheduledtask", "new-scheduledtask", "set-scheduledtask", "unregister-scheduledtask",
           "netsh", "new-netfirewallrule", "remove-netfirewallrule", "set-dnsclientserveraddress", "start-bitstransfer"),
    r"(?<![\w-])set-netfirewall\w*",
    r"(?<![\w-])route\s+(?:add|delete|change)(?![\w-])",
    r"start-process[^|;]*-verb\s+runas",
    r"(?<!\S)-enc(?:odedcommand)?(?![\w-])",
    r"(?<!\S)-executionpolicy(?![\w-])",
])
_DOWNLOADS_A_FILE = "|".join([
    r"(?<![\w-])(?:invoke-webrequest|iwr|curl(?:\.exe)?|wget)(?![\w-])[^|;]*\s-(?:outfile|out-file|output|o)(?![\w-])",
    r"\.download(?:file|string)\s*\(",
])
_WRITES_INTO_SYSTEM_FOLDERS = (
    r"(?:>>?|(?<![\w-])(?:out-file|set-content|add-content|copy-item|move-item|new-item))"
    r"\s*['\"]?[a-z]:[\\/](?:windows|program files(?: \(x86\))?|programdata)")

RISKY = re.compile("|".join([
    _CHANGES_FILES, _CHANGES_THE_SYSTEM, _EDITS_THE_REGISTRY, _CHANGES_PERMISSIONS_OR_SOFTWARE,
    _RUNS_CODE_OR_REACHES_OUT, _DOWNLOADS_A_FILE, _WRITES_INTO_SYSTEM_FOLDERS]), re.I)

SECRET_COMMANDS = re.compile(
    r"\bcmdkey\b|\bvaultcmd\b|\bget-credential\b|\bconvertfrom-securestring\b|-asplaintext|\bmimikatz\b"
    r"|\breg(?:\.exe)?\s+save\s+hk\w+[\\/](?:sam|security|system)\b"
    r"|[\\/]microsoft[\\/](?:credentials|protect|vault)\b|[\\/]gnupg\b"
    r"|[\\/]google[\\/]chrome[\\/]user data|[\\/]microsoft[\\/]edge[\\/]user data|[\\/]mozilla[\\/]firefox"
    r"|[\\/]bravesoftware|[\\/]1password|[\\/]bitwarden|[\\/]keepass|[\\/]github cli|\.aws\b", re.I)

DRIVES_INPUT = re.compile(
    r"\b(?:SendKeys|SendInput|keybd_event|mouse_event|SetCursorPos|AutoHotkey|nircmd|UIAutomation\w*)\b", re.I)

SECRET_APPS = re.compile(
    r"credential manager|windows security|user account control|windows hello|\bconsent\b|credwiz|secpol|netplwiz"
    r"|sign-?in options", re.I)


def secret_paths(home):
    roaming = Path(home) / "AppData" / "Roaming"
    local = Path(home) / "AppData" / "Local"
    return [
        roaming / "Microsoft" / "Credentials", roaming / "Microsoft" / "Protect", roaming / "Microsoft" / "Vault",
        local / "Microsoft" / "Credentials", local / "Microsoft" / "Vault",
        roaming / "gnupg", roaming / "Mozilla", roaming / "1Password", roaming / "Bitwarden", roaming / "KeePass",
        roaming / "GitHub CLI",
        local / "Google" / "Chrome", local / "Microsoft" / "Edge", local / "BraveSoftware", local / "Chromium",
        local / "1Password",
    ]
