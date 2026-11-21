import base64
import json
import re
import socketserver
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOOPBACK = "127.0.0.1"
APPEND_LITERAL = re.compile(rb"\{(\d+)\}\r\n$")


class _Running:
    def __init__(self, server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSmtp(_Running):
    def __init__(self, user="drew@example.org", password="app-password"):
        self.user, self.password = user, password
        self.messages = []
        self.logins = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, line: str):
                self.wfile.write(line.encode() + b"\r\n")

            def handle(self):
                self.reply("220 fake ESMTP ready")
                sender, recipients = "", []
                while line := self.rfile.readline():
                    command = line.decode().strip()
                    verb = command.split(" ", 1)[0].upper()
                    if verb in ("EHLO", "HELO"):
                        self.wfile.write(b"250-fake\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
                    elif verb == "AUTH":
                        given = base64.b64decode(command.split()[-1]).split(b"\0")
                        outer.logins.append((given[1].decode(), given[2].decode()))
                        good = given[1].decode() == outer.user and given[2].decode() == outer.password
                        self.reply("235 ok" if good else "535 no")
                    elif verb == "MAIL":
                        sender, recipients = command.split(":", 1)[1].split()[0].strip("<>"), []
                        self.reply("250 ok")
                    elif verb == "RCPT":
                        recipients.append(command.split(":", 1)[1].strip().strip("<>"))
                        self.reply("250 ok")
                    elif verb == "DATA":
                        self.reply("354 go on")
                        body = []
                        while (data := self.rfile.readline()) not in (b".\r\n", b""):
                            body.append(data[1:] if data.startswith(b"..") else data)
                        outer.messages.append({"from": sender, "to": recipients, "data": b"".join(body)})
                        self.reply("250 queued")
                    elif verb == "QUIT":
                        self.reply("221 bye")
                        return
                    else:
                        self.reply("250 ok")

        super().__init__(_TCPServer((LOOPBACK, 0), Handler))


class FakeImap(_Running):
    def __init__(self, messages: dict[int, tuple[bytes, str]], user="drew@example.org", password="app-password",
                 capabilities: bytes = b"IMAP4rev1 AUTH=PLAIN", uid_validity: int = 1):
        self.messages = dict(messages)
        self.user, self.password = user, password
        self.capabilities, self.uid_validity = capabilities, uid_validity
        self.commands = []
        self.appended = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def send(self, data: bytes):
                self.wfile.write(data)

            def handle(self):
                self.send(b"* OK IMAP4rev1 fake ready\r\n")
                while line := self.rfile.readline():
                    tag, _, rest = line.rstrip(b"\r\n").partition(b" ")
                    outer.commands.append(rest.decode(errors="replace"))
                    if not self.answer(tag, rest, line):
                        return

            def answer(self, tag: bytes, rest: bytes, line: bytes) -> bool:
                words = rest.split(b" ")
                verb = words[0].upper()
                if verb == b"CAPABILITY":
                    self.send(b"* CAPABILITY " + outer.capabilities + b"\r\n" + tag + b" OK done\r\n")
                elif verb == b"LOGIN":
                    good = words[1].strip(b'"').decode() == outer.user and words[2].strip(b'"').decode() == outer.password
                    self.send(tag + (b" OK signed in\r\n" if good else b" NO wrong password\r\n"))
                elif verb in (b"SELECT", b"EXAMINE"):
                    self.send(f"* {len(outer.messages)} EXISTS\r\n* OK [UIDVALIDITY {outer.uid_validity}] ok\r\n".encode() + tag + b" OK done\r\n")
                elif verb == b"UID" and words[1].upper() == b"SEARCH":
                    uids = " ".join(str(uid) for uid in sorted(outer.messages))
                    self.send(f"* SEARCH {uids}\r\n".encode() + tag + b" OK done\r\n")
                elif verb == b"UID" and words[1].upper() == b"FETCH":
                    self.fetch(tag, int(words[2]), b" ".join(words[3:]).upper())
                elif verb == b"APPEND":
                    size = int(APPEND_LITERAL.search(line).group(1))
                    self.send(b"+ go on\r\n")
                    data = self.rfile.read(size)
                    self.rfile.readline()
                    outer.appended.append({"command": rest.decode(errors="replace"), "data": data})
                    self.send(tag + b" OK appended\r\n")
                elif verb == b"LOGOUT":
                    self.send(b"* BYE bye\r\n" + tag + b" OK done\r\n")
                    return False
                else:
                    self.send(tag + b" OK whatever\r\n")
                return True

            def fetch(self, tag: bytes, uid: int, items: bytes):
                if uid not in outer.messages:
                    self.send(tag + b" OK nothing\r\n")
                    return
                raw, flags = outer.messages[uid]
                if b"HEADER" in items:
                    raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                    section = b"BODY[HEADER]"
                else:
                    section = b"BODY[]"
                head = f"* 1 FETCH (UID {uid} FLAGS ({flags}) ".encode() + section + f" {{{len(raw)}}}\r\n".encode()
                self.send(head + raw + b")\r\n" + tag + b" OK done\r\n")

        super().__init__(_TCPServer((LOOPBACK, 0), Handler))


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def answer(self, status: int, data: bytes = b"", content_type: str = "application/json", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


class FakeCalDav(_Running):
    COLLECTION = "/dav/drew/personal/"

    def __init__(self, user="drew", password="calendar-app-password"):
        self.user, self.password = user, password
        self.items: dict[str, tuple[str, str]] = {}
        self.requests = []
        self.version = 0
        outer = self

        class Handler(_QuietHandler):
            def allowed(self) -> bool:
                expected = "Basic " + base64.b64encode(f"{outer.user}:{outer.password}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    self.answer(401, b"sign in")
                    return False
                return True

            def note(self):
                outer.requests.append((self.command, urllib.parse.unquote(self.path), dict(self.headers)))

            def multistatus(self, parts: list[str]):
                xml = ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                       + "".join(parts) + "</D:multistatus>")
                self.answer(207, xml.encode(), "application/xml")

            def do_REPORT(self):
                self.note()
                self.body()
                if self.allowed():
                    self.multistatus([f"<D:response><D:href>{href}</D:href><D:propstat><D:prop><D:getetag>{etag}</D:getetag>"
                                      f"<C:calendar-data>{_xml_text(ics)}</C:calendar-data></D:prop></D:propstat></D:response>"
                                      for href, (ics, etag) in outer.items.items()])

            def do_PROPFIND(self):
                self.note()
                self.body()
                if self.allowed():
                    self.multistatus([f"<D:response><D:href>{outer.COLLECTION}</D:href></D:response>"]
                                     + [f"<D:response><D:href>{href}</D:href><D:propstat><D:prop><D:getetag>{etag}"
                                        "</D:getetag></D:prop></D:propstat></D:response>"
                                        for href, (_, etag) in outer.items.items()])

            def do_GET(self):
                self.note()
                if not self.allowed():
                    return
                found = outer.items.get(urllib.parse.unquote(self.path))
                if found is None:
                    self.answer(404)
                    return
                self.answer(200, found[0].encode(), "text/calendar", {"ETag": found[1]})

            def do_PUT(self):
                self.note()
                data = self.body()
                if not self.allowed():
                    return
                path = urllib.parse.unquote(self.path)
                current = outer.items.get(path)
                if self.headers.get("If-None-Match") == "*" and current is not None:
                    self.answer(412)
                    return
                if self.headers.get("If-Match") and (current is None or current[1] != self.headers["If-Match"]):
                    self.answer(412)
                    return
                outer.version += 1
                etag = f'"v{outer.version}"'
                outer.items[path] = (data.decode(), etag)
                self.answer(201 if current is None else 204, headers={"ETag": etag})

            def do_DELETE(self):
                self.note()
                if not self.allowed():
                    return
                path = urllib.parse.unquote(self.path)
                current = outer.items.get(path)
                if current is None:
                    self.answer(404)
                    return
                if self.headers.get("If-Match") and current[1] != self.headers["If-Match"]:
                    self.answer(412)
                    return
                del outer.items[path]
                self.answer(204)

        super().__init__(ThreadingHTTPServer((LOOPBACK, 0), Handler))

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}{self.COLLECTION}"

    def put_event(self, name: str, ics: str) -> str:
        self.version += 1
        href = self.COLLECTION + name
        self.items[href] = (ics, f'"v{self.version}"')
        return href


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class FakeDrive(_Running):
    CSRF = "csrf-token-1"

    def __init__(self, user="drew", proxy_secret="proxy-secret", chunk_bytes=4):
        self.user, self.proxy_secret, self.chunk_bytes = user, proxy_secret, chunk_bytes
        self.files: dict[int, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.shares: dict[int, dict] = {}
        self.trashed: list[int] = []
        self.revoked: list[int] = []
        self.chunks = 0
        self.next_id = 100
        outer = self

        class Handler(_QuietHandler):
            def allowed(self) -> bool:
                if self.headers.get("X-Forwarded-User") != outer.user or self.headers.get("X-Drive-Proxy-Secret") != outer.proxy_secret:
                    self.answer(401, b'{"error": "sign in first"}')
                    return False
                if self.command != "GET" and self.headers.get("X-CSRF-Token") != outer.CSRF:
                    self.answer(403, b'{"error": "missing or wrong CSRF token"}')
                    return False
                return True

            def json(self, status: int, value):
                self.answer(status, json.dumps(value).encode())

            def do_GET(self):
                if not self.allowed():
                    return
                parts = urllib.parse.urlsplit(self.path)
                if parts.path == "/api/session":
                    self.json(200, {"user": outer.user, "csrfToken": outer.CSRF})
                elif parts.path == "/api/folders/0":
                    self.json(200, {"items": [{"id": 5, "kind": "folder", "name": "Notes"}]})
                elif parts.path == "/api/search":
                    wanted = urllib.parse.parse_qs(parts.query).get("q", [""])[0].lower()
                    self.json(200, {"items": [node for node in outer.files.values() if wanted in node["name"].lower()]})
                else:
                    self.json(404, {"error": "no such thing"})

            def do_POST(self):
                data = self.body()
                if not self.allowed():
                    return
                path = urllib.parse.urlsplit(self.path).path
                payload = json.loads(data) if data else {}
                if path == "/api/uploads":
                    upload_id = f"up{len(outer.uploads) + 1}"
                    outer.uploads[upload_id] = {**payload, "data": b""}
                    self.json(201, {"id": upload_id, "chunkBytes": outer.chunk_bytes})
                elif path.startswith("/api/uploads/") and path.endswith("/finish"):
                    upload = outer.uploads.pop(path.split("/")[3])
                    outer.next_id += 1
                    node = {"id": outer.next_id, "name": upload["name"], "kind": "file", "parentId": upload["parentId"],
                            "data": upload["data"].decode("latin-1")}
                    outer.files[node["id"]] = node
                    self.json(200, {key: value for key, value in node.items() if key != "data"})
                elif path == "/api/shares":
                    share_id = len(outer.shares) + 7
                    outer.shares[share_id] = payload
                    self.json(201, {"id": share_id, "token": f"token{share_id}", "nodeId": payload["nodeId"]})
                elif path == "/api/nodes/trash":
                    outer.trashed.extend(payload["ids"])
                    self.json(200, {"trashed": payload["ids"]})
                else:
                    self.json(404, {"error": "no such thing"})

            def do_PUT(self):
                data = self.body()
                if not self.allowed():
                    return
                parts = urllib.parse.urlsplit(self.path)
                upload = outer.uploads[parts.path.split("/")[3]]
                offset = int(urllib.parse.parse_qs(parts.query)["offset"][0])
                if offset != len(upload["data"]):
                    self.json(409, {"error": "wrong offset"})
                    return
                upload["data"] += data
                outer.chunks += 1
                self.json(200, {"received": len(upload["data"])})

            def do_DELETE(self):
                if not self.allowed():
                    return
                share_id = int(self.path.rsplit("/", 1)[-1])
                outer.revoked.append(share_id)
                self.answer(204)

        super().__init__(ThreadingHTTPServer((LOOPBACK, 0), Handler))

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"
