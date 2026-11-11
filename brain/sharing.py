import csv
import http.client
import importlib
import io
import ipaddress
import json
import os
import re
import shlex
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import media

sys.path.insert(0, os.environ.get("ARTY_MCP_DIR", str(Path(__file__).resolve().parent.parent / "mcp")))
remote = importlib.import_module("remote")

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
SCREENSHOT_FILE = "/tmp/arty-share-screen.png"
RECORDING_FILE = "/tmp/arty-share-recording.mp4"
LIVE_RECORDING_FILE = "/tmp/arty-live-recording.mp4"
LIVE_RECORDING_STATE = "/tmp/arty-live-recording.state"
STOP_WAIT_SECONDS = 20
RECORDING_UNLOCKS = ("browser", "screen")
DEFAULT_RECORDING_SECONDS = 10
MAX_RECORDING_SECONDS = 60
RECORDING_FRAMES_PER_SECOND = 15
RECORDING_GRACE_SECONDS = 60
SCREENSHOT_TIMEOUT_SECONDS = 30
FILE_TIMEOUT_SECONDS = 180
WEB_TIMEOUT_SECONDS = 20
MAX_WEB_BYTES = 25 * 1024 * 1024
PICTURE_CANDIDATES = 6
MAX_PICTURE_BYTES = 3 * 1024 * 1024
PICTURE_EDGE = 2048
PRIVATE_FOLDERS = (".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".config/chromium", ".config/google-chrome",
                   ".mozilla", ".pki", ".aws", ".kube", ".docker", ".config/gh", ".mcp", ".gmail-chrome", ".browser-profile",
                   ".local/browser-agent")
PRIVATE_NAMES = (".env", ".netrc", ".git-credentials", ".pem", ".key", "id_rsa", "id_ed25519", "id_ecdsa", "cookies",
                 "credentials", "token", "secret", "password")
DOCUMENTS_FOLDER = "~/Documents/Arty"
DOCUMENT_TIMEOUT_SECONDS = 120
MAX_DOCUMENT_CHARS = 400_000
MAX_TITLE_CHARS = 80
PANDOC_FORMATS = ("docx", "odt", "pptx", "html", "epub", "rtf")
PLAIN_FORMATS = ("md", "txt")
DOCUMENT_FORMATS = PANDOC_FORMATS + PLAIN_FORMATS + ("pdf", "xlsx", "csv")
FORMAT_ALIASES = {"word": "docx", "doc": "docx", "powerpoint": "pptx", "ppt": "pptx", "slides": "pptx", "excel": "xlsx",
                  "xls": "xlsx", "spreadsheet": "xlsx", "markdown": "md", "text": "txt", "web": "html", "htm": "html"}
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
NUMBER = re.compile(r"^-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
PDF_STYLE = ("<style>body{font-family:sans-serif;max-width:46em;margin:2em auto;line-height:1.5}"
             "table{border-collapse:collapse}td,th{border:1px solid #999;padding:.3em .6em}</style>")
XLSX_SCRIPT = (
    "import json,os,sys\nfrom openpyxl import Workbook\nfrom openpyxl.styles import Font\n"
    "job=json.load(sys.stdin)\nbook=Workbook()\nsheet=book.active\nsheet.title=job['sheet']\n"
    "for row in job['rows']:\n    sheet.append(row)\n"
    "for cell in sheet[1]:\n    cell.font=Font(bold=True)\n"
    "for column in sheet.columns:\n"
    "    sheet.column_dimensions[column[0].column_letter].width=min(60,max(len(str(c.value or '')) for c in column)+2)\n"
    "book.save(os.path.expanduser(job['path']))\n")
SENT_NOTE = "It shows with your reply, so don't repeat the link: just say in a sentence what it is."


class ShareError(Exception):
    pass


def _stamp() -> str:
    return time.strftime("%Y-%m-%d-%H%M%S")


def _sent(meta: dict, what: str, source: str = "") -> str:
    origin = f"\nFrom: {source}" if source else ""
    return f"Sent {what} to the user: {media.link(meta)}{origin}\n{SENT_NOTE}"


def send_screenshot() -> str:
    code, data = remote.ssh(f"{remote.GUI_ENV} scrot -o {SCREENSHOT_FILE} >/dev/null 2>&1 && cat {SCREENSHOT_FILE}",
                            timeout=SCREENSHOT_TIMEOUT_SECONDS)
    if code != 0 or not data.startswith(b"\x89PNG"):
        return "Couldn't take a screenshot of my desktop: " + data[:300].decode("utf-8", errors="replace")
    meta = media.save_bytes(data, f"arty-desktop-{_stamp()}.png", "image/png", "screenshot")
    return _sent(meta, "a screenshot of your desktop")


def recording_command(seconds: int, size: str, output: str = RECORDING_FILE) -> str:
    return f"{remote.GUI_ENV} {ffmpeg_command(seconds, size, output)}"


def ffmpeg_command(seconds: int, size: str, output: str) -> str:
    return (f"ffmpeg -nostdin -loglevel error -y -f x11grab -framerate {RECORDING_FRAMES_PER_SECOND} "
            f"-video_size {size} -i :0 -t {seconds} -c:v libx264 -preset veryfast -pix_fmt yuv420p "
            f"-vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -movflags +faststart {output}")


def screen_size() -> str:
    code, found = remote.text("command -v ffmpeg")
    if code != 0 or not found.strip():
        raise ShareError("I can't record my screen: ffmpeg isn't installed on my desktop. You can add it with: "
                         "sudo apt install ffmpeg (on Arty's desktop VM).")
    code, size = remote.text(f"{remote.GUI_ENV} xdotool getdisplaygeometry")
    width_height = size.split()
    if code != 0 or len(width_height) != 2:
        raise ShareError(f"Couldn't read my screen size: {size.strip()[:200]}")
    return "x".join(width_height)


def fetch_recording(path: str, what: str) -> str:
    code, data = remote.ssh(f"cat {path} && rm -f {path}", timeout=FILE_TIMEOUT_SECONDS)
    if code != 0 or not data:
        return "The recording was made but I couldn't fetch it from my desktop."
    meta = media.save_bytes(data, f"arty-desktop-{_stamp()}.mp4", "video/mp4", "screen recording")
    return _sent(meta, what)


def send_screen_recording(seconds=DEFAULT_RECORDING_SECONDS) -> str:
    try:
        seconds = max(1, min(int(seconds or DEFAULT_RECORDING_SECONDS), MAX_RECORDING_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_RECORDING_SECONDS
    try:
        size = screen_size()
    except ShareError as error:
        return str(error)
    code, output = remote.text(recording_command(seconds, size), timeout=seconds + RECORDING_GRACE_SECONDS)
    if code != 0:
        return f"The recording failed: {output.strip()[:300]}"
    return fetch_recording(RECORDING_FILE, f"a {seconds}-second recording of your desktop")


def live_recording() -> tuple[int, int, bool] | None:
    code, state = remote.text(f"cat {LIVE_RECORDING_STATE} 2>/dev/null && (kill -0 $(cut -d' ' -f1 {LIVE_RECORDING_STATE}) "
                              "2>/dev/null && echo running || echo finished)")
    parts = state.split()
    if code != 0 or len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]), int(parts[1]), parts[2] == "running"


def start_screen_recording() -> str:
    state = live_recording()
    if state and state[2]:
        return "I'm already recording my screen. Do what you want to show, then call stop_screen_recording."
    try:
        size = screen_size()
    except ShareError as error:
        return str(error)
    command = ffmpeg_command(MAX_RECORDING_SECONDS, size, LIVE_RECORDING_FILE)
    code, output = remote.text(f"rm -f {LIVE_RECORDING_FILE}; {remote.GUI_ENV} nohup {command} >/dev/null 2>&1 </dev/null & "
                               f"echo \"$! $(date +%s)\" > {LIVE_RECORDING_STATE}")
    if code != 0:
        return f"Couldn't start recording: {output.strip()[:300]}"
    return (f"Recording my screen now (it stops by itself after {MAX_RECORDING_SECONDS} seconds). Your browser and "
            "desktop tools are ready from your next step: do what the user wants to see now, e.g. browser_open a search. "
            "Only call stop_screen_recording once it's done; it sends the video.")


def stop_screen_recording() -> str:
    state = live_recording()
    if not state:
        return "I'm not recording my screen. Call start_screen_recording first."
    pid, started, _ = state
    code, output = remote.text(
        f"kill -INT {pid} 2>/dev/null; for i in $(seq {STOP_WAIT_SECONDS * 2}); do kill -0 {pid} 2>/dev/null || break; sleep 0.5; done; "
        f"rm -f {LIVE_RECORDING_STATE}; test -s {LIVE_RECORDING_FILE} && echo $(( $(date +%s) - {started} ))",
        timeout=STOP_WAIT_SECONDS + RECORDING_GRACE_SECONDS)
    seconds = output.strip()
    if code != 0 or not seconds.isdigit():
        return "The recording didn't save anything, so there's nothing to send."
    length = min(int(seconds), MAX_RECORDING_SECONDS)
    return fetch_recording(LIVE_RECORDING_FILE, f"a {length}-second recording of your desktop")


def is_private(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    return any(f"/{folder}/" in f"{lowered}/" for folder in PRIVATE_FOLDERS) or any(part in name for part in PRIVATE_NAMES)


def send_file(path) -> str:
    if not str(path or "").strip():
        return "Say which file to send, e.g. ~/Pictures/cat.jpg."
    code, real = remote.text(f"realpath -e {remote.quoted(remote.expand(str(path)))}")
    target = real.strip()
    if code != 0 or not target:
        return f"There's no file at {path} on my desktop."
    if is_private(target):
        return f"I won't send {target}: it's private (keys, passwords or a browser profile)."
    code, size = remote.text(f"stat -c %s {remote.quoted(target)}")
    if code != 0 or not size.strip().isdigit():
        return f"There's no file at {target} on my desktop."
    if int(size) > media.MAX_UPLOAD_BYTES:
        return f"{target} is too big to send ({int(size) // (1024 * 1024)} MB; the limit is {media.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
    code, data = remote.ssh(f"cat {remote.quoted(target)}", timeout=FILE_TIMEOUT_SECONDS)
    if code != 0:
        return f"Couldn't read {target}: {data[:300].decode('utf-8', errors='replace')}"
    name = target.rstrip("/").rsplit("/", 1)[-1] or "file"
    meta = media.save_bytes(data, name, media.guess_mime(name), "desktop file")
    return _sent(meta, name)


def public_addresses(host: str) -> list[str]:
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, None)))
    except (socket.gaierror, UnicodeError):
        return []
    if addresses and all(ipaddress.ip_address(address.split("%")[0]).is_global for address in addresses):
        return addresses
    return []


def is_public_host(host: str) -> bool:
    return bool(public_addresses(host))


def checked_address(host: str) -> str:
    addresses = public_addresses(host)
    if not addresses:
        raise ShareError(f"{host} isn't a public website")
    return addresses[0]


class PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((checked_address(self.host), self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        plain = socket.create_connection((checked_address(self.host), self.port), self.timeout)
        self.sock = self._context.wrap_socket(plain, server_hostname=self.host)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(PinnedHTTPConnection, request)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PinnedHTTPSConnection, request)


def check_public_url(url: str):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ShareError(f"not a web address: {url}")
    if not is_public_host(parts.hostname):
        raise ShareError(f"{parts.hostname} isn't a public website")


class PublicOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        check_public_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicOnlyRedirects, PinnedHTTPHandler, PinnedHTTPSHandler)


def fetch_media(url: str) -> tuple[bytes, str]:
    check_public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Arty"})
    try:
        with _opener.open(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            mime = (response.headers.get_content_type() or "").lower()
            if media.kind_of(mime) not in ("image", "video"):
                raise ShareError(f"{url} isn't a picture or video ({mime or 'unknown type'})")
            data = response.read(MAX_WEB_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ShareError(f"couldn't download {url}: {error}") from error
    if len(data) > MAX_WEB_BYTES:
        raise ShareError(f"{url} is bigger than {MAX_WEB_BYTES // (1024 * 1024)} MB")
    if not data:
        raise ShareError(f"{url} was empty")
    return data, mime


def phone_sized(data: bytes, mime: str, name: str) -> tuple[bytes, str, str]:
    if media.kind_of(mime) != "image" or len(data) <= MAX_PICTURE_BYTES or mime == "image/gif":
        return data, mime, name
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder) / "in", Path(folder) / "out.jpg"
        source.write_bytes(data)
        try:
            media.refuse_scripted_image(str(source))
        except media.MediaError as error:
            raise ShareError(str(error)) from None
        try:
            media._run(["magick", f"{source}[0]", "-auto-orient", "-resize", f"{PICTURE_EDGE}x{PICTURE_EDGE}>",
                        "-quality", "85", str(target)], 120, "Shrinking the picture")
        except media.MediaError:
            return data, mime, name
        return target.read_bytes(), "image/jpeg", f"{name.rsplit('.', 1)[0]}.jpg"


def save_web(data: bytes, mime: str, name: str) -> dict:
    data, mime, name = phone_sized(data, mime, name)
    return media.save_bytes(data, name, mime, "web")


def picture_results(query: str) -> list[dict]:
    params = urllib.parse.urlencode({"q": query, "categories": "images", "format": "json", "safesearch": 1})
    with urllib.request.urlopen(f"{SEARXNG_URL}/search?{params}", timeout=WEB_TIMEOUT_SECONDS) as response:
        results = json.loads(response.read()).get("results") or []
    return [result for result in results if str(result.get("img_src", "")).startswith(("http://", "https://"))]


def _web_name(url: str, mime: str, fallback: str) -> str:
    name = media.safe_name(urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]), fallback)
    if "." not in name:
        name = f"{name}.{mime.split('/', 1)[-1].split('+')[0] or 'jpg'}"
    return name


def send_picture(query="", url="") -> str:
    query, url = str(query or "").strip(), str(url or "").strip()
    if url:
        try:
            data, mime = fetch_media(url)
            meta = save_web(data, mime, _web_name(url, mime, "picture"))
        except ShareError as error:
            return f"Couldn't send that: {error}"
        return _sent(meta, "the picture", url)
    if not query:
        return "Say what picture to find, or give its web address."
    try:
        results = picture_results(query)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return f"The picture search isn't working right now: {error}"
    problems = []
    for result in results[:PICTURE_CANDIDATES]:
        try:
            data, mime = fetch_media(result["img_src"])
            meta = save_web(data, mime, _web_name(result["img_src"], mime, query[:40] or "picture"))
        except ShareError as error:
            problems.append(str(error))
            continue
        title = str(result.get("title") or "").strip()
        return _sent(meta, f"a picture of {query}" + (f" ({title})" if title else ""), str(result.get("url") or result["img_src"]))
    if not results:
        return f"The picture search found nothing for {query}."
    return f"Found pictures of {query} but couldn't download any: {'; '.join(problems[:3])}"


def document_format(wanted: str) -> str | None:
    key = str(wanted or "").strip().lower().lstrip(".")
    key = FORMAT_ALIASES.get(key, key)
    return key if key in DOCUMENT_FORMATS else None


def document_name(title: str, extension: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:MAX_TITLE_CHARS] or "document"
    return f"{slug}-{_stamp()}.{extension}"


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _typed(cell: str):
    if NUMBER.match(cell):
        number = float(cell.replace(",", ""))
        return int(number) if number.is_integer() and "." not in cell else number
    return cell


def table_rows(content: str) -> list[list]:
    lines = [line for line in content.splitlines() if line.strip()]
    table = [line for line in lines if line.lstrip().startswith("|")]
    if table:
        rows = [_cells(line) for line in table if not TABLE_SEPARATOR.match(line)]
    else:
        rows = [row for row in csv.reader(io.StringIO("\n".join(lines))) if any(cell.strip() for cell in row)]
    return [[_typed(cell.strip()) for cell in row] for row in rows]


def _csv_text(rows: list[list]) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _desktop(command: str, stdin: bytes | None = None) -> None:
    code, output = remote.text(command, stdin, timeout=DOCUMENT_TIMEOUT_SECONDS)
    if code != 0:
        raise ShareError(output.strip()[:400] or f"exit code {code}")


SHEET_TITLE_CHARS = 31
NOT_IN_SHEET_TITLES = re.compile(r"[\\/?*\[\]:]")
PANDOC_SAFELY = "--sandbox -f markdown-raw_html-raw_attribute"
CHROME_WITHOUT_SCRIPTS = "--blink-settings=scriptEnabled=false"


def sheet_title(title: str) -> str:
    cleaned = " ".join(NOT_IN_SHEET_TITLES.sub(" ", title).split()).strip("'")
    return cleaned[:SHEET_TITLE_CHARS].strip() or "Sheet1"


def _build_document(content: str, title: str, extension: str, target: str) -> None:
    folder = remote.quoted(DOCUMENTS_FOLDER)
    path = remote.quoted(target)
    _desktop(f"mkdir -p {folder}")
    if extension in ("xlsx", "csv"):
        rows = table_rows(content)
        if not rows:
            raise ShareError("there's no table in the content: give a Markdown table or CSV text")
        if extension == "csv":
            _desktop(f"cat > {path}", _csv_text(rows).encode())
            return
        job = {"path": target, "sheet": sheet_title(title), "rows": rows}
        _desktop(f"cd ~ && python3 -c {shlex.quote(XLSX_SCRIPT)}", json.dumps(job).encode())
        return
    source = target.rsplit(".", 1)[0] + ".src.md"
    source_path = remote.quoted(source)
    _desktop(f"cat > {source_path}", content.encode())
    if extension in PLAIN_FORMATS:
        _desktop(f"mv {source_path} {path}")
        return
    metadata = f"--metadata title={shlex.quote(title)}" if not content.lstrip().startswith("# ") else ""
    try:
        if extension == "pdf":
            html = remote.quoted(target.rsplit(".", 1)[0] + ".print.html")
            _desktop(f"style=$(mktemp) && printf %s {shlex.quote(PDF_STYLE)} > \"$style\" && "
                     f"pandoc {PANDOC_SAFELY} {source_path} -s {metadata} -H \"$style\" -o {html} && profile=$(mktemp -d) && "
                     f"google-chrome --headless=new --disable-gpu {CHROME_WITHOUT_SCRIPTS} --user-data-dir=\"$profile\" "
                     f"--no-pdf-header-footer "
                     f"--print-to-pdf={path} \"file://$(realpath {html})\" >/dev/null 2>&1; "
                     f"rm -rf \"$profile\" \"$style\" {html}; [ -s {path} ] || {{ echo 'Chrome made no PDF'; exit 1; }}")
        else:
            standalone = "-s" if extension in ("html", "rtf") else ""
            _desktop(f"pandoc {PANDOC_SAFELY} {source_path} {standalone} {metadata} -o {path}")
    finally:
        remote.text(f"rm -f {source_path}")


def make_document(title, content, file_format) -> str:
    title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS]
    content = str(content or "")
    extension = document_format(file_format)
    if extension is None:
        return f"I can't make a '{file_format}' file. I can make: {', '.join(DOCUMENT_FORMATS)}."
    if not content.strip():
        return "Write the document's content first, then call make_document with it."
    if len(content) > MAX_DOCUMENT_CHARS:
        return f"That's too long for one document ({len(content)} characters; the limit is {MAX_DOCUMENT_CHARS})."
    target = f"{DOCUMENTS_FOLDER}/{document_name(title, extension)}"
    try:
        _build_document(content, title or "Document", extension, target)
    except ShareError as error:
        return f"Couldn't make the {extension} file: {error}"
    return send_file(target)


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = {
    "send_screenshot": (lambda a: send_screenshot(), _tool(
        "send_screenshot",
        "Take a screenshot of your own desktop and send the picture to the user, so they see it themselves. "
        "Use this whenever they want to see your screen; look_at_desktop only lets you see it.",
        {}, [])),
    "send_screen_recording": (lambda a: send_screen_recording(a.get("seconds")), _tool(
        "send_screen_recording",
        "Record your desktop for a set number of seconds and send the video. You can't act while it records, so to "
        "show yourself doing something use start_screen_recording and stop_screen_recording instead.",
        {"seconds": {"type": "integer", "description": f"how long, 1-{MAX_RECORDING_SECONDS} (default {DEFAULT_RECORDING_SECONDS})"}},
        [])),
    "start_screen_recording": (lambda a: start_screen_recording(), _tool(
        "start_screen_recording",
        "Start recording a video of your own desktop, to show the user yourself doing something (\"send me a video of "
        "you searching for X\"). Then do it with your other tools (use_tools unlocks the browser or desktop ones), then "
        "call stop_screen_recording, which sends the video. Starting gives you the browser and desktop tools.",
        {}, [])),
    "stop_screen_recording": (lambda a: stop_screen_recording(), _tool(
        "stop_screen_recording",
        "Stop the recording start_screen_recording began and send the video to the user.",
        {}, [])),
    "send_file": (lambda a: send_file(a.get("path")), _tool(
        "send_file",
        "Send the user a picture, video or any other file from your own desktop computer, e.g. something you downloaded or made.",
        {"path": {"type": "string", "description": "the file's path on your computer, e.g. ~/Downloads/photo.jpg"}},
        ["path"])),
    "send_picture": (lambda a: send_picture(a.get("query"), a.get("url")), _tool(
        "send_picture",
        "Find a picture on the web and send it to the user (\"show me a picture of ...\"), or send the picture or video "
        "at a web address.",
        {"query": {"type": "string", "description": "what the picture should show, e.g. 'red panda'"},
         "url": {"type": "string", "description": "or: the web address of a picture or video to send"}},
        [])),
    "make_document": (lambda a: make_document(a.get("title"), a.get("content"), a.get("format")), _tool(
        "make_document",
        "Make a real file and send it to the user: a Word document, PDF, PowerPoint, Excel spreadsheet, CSV, web page, "
        "e-book or text file. Write the whole content yourself first. Only say you've made a file after this returns a link.",
        {"title": {"type": "string", "description": "the document's title, also used for the file name"},
         "content": {"type": "string", "description": "the full content in Markdown (# headings, lists, **bold**, tables). "
                     "For pptx, each '# ' or '## ' heading starts a slide. For xlsx or csv, a Markdown table or CSV text"},
         "format": {"type": "string", "description": "one of " + ", ".join(DOCUMENT_FORMATS)}},
        ["title", "content", "format"])),
}
