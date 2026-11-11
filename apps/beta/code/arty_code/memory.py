import json
import os
from pathlib import Path

MEMORY_FILE = "ARTY.md"
NOTE_PREFIX = "- "
TRUST_FILE = "trusted-folders.json"
PRIVATE_FILE_MODE = 0o600


def trusted_folders(personal_folder: Path) -> list[str]:
    try:
        stored = json.loads((Path(personal_folder) / TRUST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(folder) for folder in stored] if isinstance(stored, list) else []


def is_trusted(personal_folder: Path, project: Path) -> bool:
    return str(Path(project).resolve()) in trusted_folders(personal_folder)


def trust(personal_folder: Path, project: Path) -> None:
    folders = trusted_folders(personal_folder)
    folder = str(Path(project).resolve())
    if folder in folders:
        return
    target = Path(personal_folder) / TRUST_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(folders + [folder], handle, indent=2)


def memory_files(workspace, personal_folder: Path, include_project: bool = True) -> list[Path]:
    candidates = [Path(personal_folder) / MEMORY_FILE] + ([workspace.root / MEMORY_FILE] if include_project else [])
    return [path for path in candidates if path.is_file()]


def instructions(workspace, personal_folder: Path, include_project: bool = True) -> str:
    parts = []
    for path in memory_files(workspace, personal_folder, include_project):
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(f"Notes from {path} (follow them):\n{text}")
    return "\n\n".join(parts)


def add_note(workspace, note: str) -> Path:
    path = workspace.root / MEMORY_FILE
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    separator = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{separator}{NOTE_PREFIX}{note.strip()}\n", encoding="utf-8")
    return path
