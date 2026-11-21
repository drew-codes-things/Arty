import contextlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from xml_stream import FileReadError

PDF_TIMEOUT_SECONDS = 120
MEMORY_LIMIT_BYTES = 3 * 1024 ** 3
CPU_LIMIT_SECONDS = 110
RENDER_DPI = 100
PAGE_IMAGE_EDGE = 1024
FORM_FEED = "\f"
PAGES_LINE = re.compile(r"^Pages:\s+(\d+)", re.MULTILINE)
PAGE_COUNT_OUTPUT = re.compile(r"^\d+$", re.MULTILINE)
GHOSTSCRIPT_BASE = ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER"]
PDF_START = b"%PDF-"
PDF_SNIFF_BYTES = 1024


def have(program: str) -> bool:
    return shutil.which(program) is not None


def limited(command: list[str]) -> list[str]:
    if not have("prlimit"):
        return command
    return ["prlimit", f"--as={MEMORY_LIMIT_BYTES}", f"--cpu={CPU_LIMIT_SECONDS}", "--", *command]


def run(command: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(limited(command), capture_output=True, timeout=PDF_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise FileReadError(f"{command[0]} isn't installed on the AI server, so {what} isn't possible") from None
    except subprocess.TimeoutExpired:
        raise FileReadError(f"{what} took too long and was stopped") from None
    return result


def failure(result: subprocess.CompletedProcess, what: str) -> FileReadError:
    message = result.stderr.decode("utf-8", errors="replace").strip()
    if "password" in message.lower() or "encrypted" in message.lower():
        return FileReadError("that PDF is password protected, so I can't open it")
    last = message.splitlines()[-1][:200] if message else "unknown error"
    return FileReadError(f"{what} failed: {last}")


def check_is_pdf(path: str):
    with open(path, "rb") as handle:
        start = handle.read(PDF_SNIFF_BYTES)
    if PDF_START not in start:
        raise FileReadError("that file doesn't start like a PDF, so it may be damaged or a different kind of file")


def postscript_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def page_count(path: str) -> int:
    check_is_pdf(path)
    if have("pdfinfo"):
        result = run(["pdfinfo", path], "reading the PDF")
        found = PAGES_LINE.search(result.stdout.decode("utf-8", errors="replace"))
        if result.returncode != 0 or not found:
            raise failure(result, "reading the PDF")
        return int(found.group(1))
    result = run([*GHOSTSCRIPT_BASE, "-dNODISPLAY", f"--permit-file-read={path}", "-c",
                  f"{postscript_string(path)} (r) file runpdfbegin pdfpagecount = quit"], "reading the PDF")
    found = PAGE_COUNT_OUTPUT.search(result.stdout.decode("utf-8", errors="replace"))
    if result.returncode != 0 or not found:
        raise failure(result, "reading the PDF")
    return int(found.group(0))


def page_texts(path: str, first: int, last: int) -> list[str]:
    wanted = last - first + 1
    if have("pdftotext"):
        result = run(["pdftotext", "-layout", "-enc", "UTF-8", "-f", str(first), "-l", str(last), path, "-"], "reading the PDF text")
        if result.returncode != 0:
            raise failure(result, "reading the PDF text")
        pages = result.stdout.decode("utf-8", errors="replace").split(FORM_FEED)[:wanted]
    else:
        with tempfile.TemporaryDirectory(prefix="arty-pdf-") as folder:
            result = run([*GHOSTSCRIPT_BASE, "-sDEVICE=txtwrite", f"-dFirstPage={first}", f"-dLastPage={last}",
                          f"-sOutputFile={folder}/page%04d.txt", path], "reading the PDF text")
            if result.returncode != 0:
                raise failure(result, "reading the PDF text")
            pages = [file.read_text(encoding="utf-8", errors="replace") for file in sorted(Path(folder).glob("page*.txt"))][:wanted]
    return pages + [""] * (wanted - len(pages))


@contextlib.contextmanager
def rendered_page(path: str, number: int):
    with tempfile.TemporaryDirectory(prefix="arty-page-") as folder:
        target = Path(folder) / "page.jpg"
        if have("pdftoppm"):
            result = run(["pdftoppm", "-jpeg", "-r", str(RENDER_DPI), "-f", str(number), "-l", str(number), "-singlefile",
                          path, str(target.with_suffix(""))], "drawing the page")
        else:
            result = run([*GHOSTSCRIPT_BASE, "-sDEVICE=jpeg", f"-r{RENDER_DPI}", f"-dFirstPage={number}", f"-dLastPage={number}",
                          f"-sOutputFile={target}", path], "drawing the page")
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            raise failure(result, "drawing the page")
        if have("magick"):
            run(["magick", str(target), "-resize", f"{PAGE_IMAGE_EDGE}x{PAGE_IMAGE_EDGE}>", str(target)], "shrinking the page picture")
        yield str(target)
