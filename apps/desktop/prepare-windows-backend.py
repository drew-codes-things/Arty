import re
import shutil
import sys
from pathlib import Path

HF_AGENT_FILES = ("system", "skills", "extensions", "_skills", "spaces")
HF_SKILLS_BLOCK = re.compile(r"^# Skills directories for AI agents[^\n]*\n(?:[^\n]+\n)*(?:\n|\Z)", re.M)
SITE_PACKAGES = Path("Lib") / "site-packages"
SERVER_FROM_PYTHON = Path("..") / "server"
REMOVED_FROM_SITE = ("pip", "setuptools", "wheel", "pkg_resources", "_distutils_hack")


def enable_site(python_dir):
    pth = next(python_dir.glob("python*._pth"))
    kept = [line for line in pth.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    kept = [line for line in kept if line != "import site"]
    kept += [str(SITE_PACKAGES), str(SERVER_FROM_PYTHON), "import site"]
    pth.write_text("\n".join(kept) + "\n")


def fetch_whisper(destination, model):
    from huggingface_hub import snapshot_download

    snapshot_download(f"Systran/faster-whisper-{model}", local_dir=destination,
                      allow_patterns=["*.json", "*.txt", "*.bin"])
    shutil.rmtree(Path(destination) / ".cache", ignore_errors=True)


def strip_hf_agent_tooling(site):
    hub = site / "huggingface_hub"
    for name in HF_AGENT_FILES:
        (hub / "cli" / f"{name}.py").unlink(missing_ok=True)
    constants = hub / "constants.py"
    constants.write_text(HF_SKILLS_BLOCK.sub("", constants.read_text(encoding="utf-8")), encoding="utf-8")
    metadata = list(site.glob("huggingface_hub-*.dist-info/METADATA"))
    for path in metadata:
        path.write_text(path.read_text(encoding="utf-8").split("\n\n", 1)[0] + "\n", encoding="utf-8")
    leftovers = [name for name in HF_AGENT_FILES if (hub / "cli" / f"{name}.py").exists()]
    if leftovers or "_SKILLS_" in constants.read_text(encoding="utf-8"):
        raise SystemExit("The bundled huggingface_hub still has its agent tooling.")


def trim(python_dir):
    site = python_dir / SITE_PACKAGES
    for name in REMOVED_FROM_SITE:
        shutil.rmtree(site / name, ignore_errors=True)
        for leftover in site.glob(f"{name}-*.dist-info"):
            shutil.rmtree(leftover, ignore_errors=True)
    shutil.rmtree(python_dir / "Scripts", ignore_errors=True)
    for cache in python_dir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for compiled in python_dir.rglob("*.pyc"):
        compiled.unlink()
    strip_hf_agent_tooling(site)


COMMANDS = {
    "enable-site": lambda arguments: enable_site(Path(arguments[0])),
    "whisper": lambda arguments: fetch_whisper(arguments[0], arguments[1]),
    "trim": lambda arguments: trim(Path(arguments[0])),
}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        raise SystemExit(f"usage: prepare-windows-backend.py {{{'|'.join(COMMANDS)}}} arguments")
    COMMANDS[argv[1]](argv[2:])


if __name__ == "__main__":
    main(sys.argv)
