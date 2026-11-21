import base64
import hashlib
import itertools
import json
import os
import queue
import secrets
import socket
import struct
import threading
import time

MESSAGE_END = b"\0"
READ_BYTES = 65536
MAX_MESSAGE_BYTES = 64_000_000
CALL_SECONDS = 30.0
MAX_KEPT_EVENTS = 500
LOOPBACK = "127.0.0.1"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_CONTINUATION, OPCODE_TEXT, OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG = 0x0, 0x1, 0x8, 0x9, 0xA
FINAL_FRAME = 0x80
MASKED = 0x80
SHORT_LENGTH_LIMIT = 125
MEDIUM_LENGTH_MARK, LONG_LENGTH_MARK = 126, 127
HANDSHAKE_LIMIT_BYTES = 16384


class CdpError(RuntimeError):
    pass


class ConnectionEnded(CdpError):
    pass


class PipeTransport:
    def __init__(self, read_fd, write_fd):
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._buffer = b""
        self._write_lock = threading.Lock()

    def send(self, text):
        data = text.encode("utf-8") + MESSAGE_END
        with self._write_lock:
            while data:
                written = os.write(self._write_fd, data)
                data = data[written:]

    def receive(self):
        while MESSAGE_END not in self._buffer:
            chunk = os.read(self._read_fd, READ_BYTES)
            if not chunk:
                return None
            self._buffer += chunk
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise CdpError("the browser sent a message that is too large")
        message, _end, self._buffer = self._buffer.partition(MESSAGE_END)
        return message.decode("utf-8", errors="replace")

    def close(self):
        for fd in (self._read_fd, self._write_fd):
            try:
                os.close(fd)
            except OSError:
                continue


def websocket_accept(key):
    return base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")


def masked_frame(opcode, payload, mask=None):
    mask = secrets.token_bytes(4) if mask is None else mask
    length = len(payload)
    if length <= SHORT_LENGTH_LIMIT:
        header = struct.pack("!BB", FINAL_FRAME | opcode, MASKED | length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", FINAL_FRAME | opcode, MASKED | MEDIUM_LENGTH_MARK, length)
    else:
        header = struct.pack("!BBQ", FINAL_FRAME | opcode, MASKED | LONG_LENGTH_MARK, length)
    body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + body


class WebSocketTransport:
    def __init__(self, sock):
        self._sock = sock
        self._buffer = b""
        self._write_lock = threading.Lock()

    @classmethod
    def connect(cls, port, path, timeout=10.0):
        sock = socket.create_connection((LOOPBACK, int(port)), timeout=timeout)
        transport = cls(sock)
        try:
            transport._handshake(port, path)
        except Exception:
            sock.close()
            raise
        sock.settimeout(None)
        return transport

    def _handshake(self, port, path):
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (f"GET {path} HTTP/1.1\r\nHost: {LOOPBACK}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(request.encode("ascii"))
        while b"\r\n\r\n" not in self._buffer:
            chunk = self._sock.recv(READ_BYTES)
            if not chunk or len(self._buffer) > HANDSHAKE_LIMIT_BYTES:
                raise CdpError("the browser's debugging connection didn't answer the handshake")
            self._buffer += chunk
        head, _end, self._buffer = self._buffer.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = {name.strip().lower(): value.strip() for name, _sep, value in (line.partition(":") for line in lines[1:])}
        if " 101 " not in f"{lines[0]} " or headers.get("sec-websocket-accept") != websocket_accept(key):
            raise CdpError("the browser's debugging connection refused the handshake")

    def _read(self, count):
        while len(self._buffer) < count:
            try:
                chunk = self._sock.recv(READ_BYTES)
            except ConnectionResetError:
                chunk = b""
            if not chunk:
                raise ConnectionEnded("the browser closed the debugging connection")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _frame(self):
        first, second = self._read(2)
        length = second & 0x7F
        if length == MEDIUM_LENGTH_MARK:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == LONG_LENGTH_MARK:
            length = struct.unpack("!Q", self._read(8))[0]
        if length > MAX_MESSAGE_BYTES:
            raise CdpError("the browser sent a message that is too large")
        mask = self._read(4) if second & MASKED else None
        payload = self._read(length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bool(first & FINAL_FRAME), first & 0x0F, payload

    def _send_frame(self, opcode, payload):
        with self._write_lock:
            self._sock.sendall(masked_frame(opcode, payload))

    def send(self, text):
        self._send_frame(OPCODE_TEXT, text.encode("utf-8"))

    def receive(self):
        parts = []
        while True:
            try:
                final, opcode, payload = self._frame()
            except ConnectionEnded:
                return None
            if opcode == OPCODE_CLOSE:
                return None
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if opcode in (OPCODE_TEXT, OPCODE_CONTINUATION):
                parts.append(payload)
                if sum(len(part) for part in parts) > MAX_MESSAGE_BYTES:
                    raise CdpError("the browser sent a message that is too large")
                if final:
                    return b"".join(parts).decode("utf-8", errors="replace")

    def close(self):
        try:
            self._sock.close()
        except OSError:
            return


class Connection:
    def __init__(self, transport):
        self._transport = transport
        self._ids = itertools.count(1)
        self._answers = {}
        self._events = []
        self._ready = threading.Condition()
        self._closed = False
        self._closing = False
        self._handlers = {}
        self._kept_with_handler = set()
        self._handled = queue.Queue()
        threading.Thread(target=self._read_all, name="cdp", daemon=True).start()
        threading.Thread(target=self._run_handlers, name="cdp-events", daemon=True).start()

    def subscribe(self, method, handler, keep=False):
        self._handlers[method] = handler
        if keep:
            self._kept_with_handler.add(method)

    def _run_handlers(self):
        while (event := self._handled.get()) is not None:
            handler = self._handlers.get(event.get("method"))
            try:
                handler(event.get("params") or {}, event.get("sessionId"))
            except CdpError as error:
                print(f"[own browser] handling {event.get('method')} failed: {error}", flush=True)

    def _read_all(self):
        try:
            while True:
                text = self._transport.receive()
                if text is None:
                    break
                self._take(text)
        except (OSError, CdpError) as error:
            if not self._closing:
                print(f"[own browser] the connection to the browser ended: {error}", flush=True)
        finally:
            with self._ready:
                self._closed = True
                self._ready.notify_all()
            self._handled.put(None)

    def _take(self, text):
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        with self._ready:
            if isinstance(message.get("id"), int):
                self._answers[message["id"]] = message
            elif message.get("method") in self._handlers:
                self._handled.put(message)
                if message["method"] in self._kept_with_handler:
                    self._events = (self._events + [message])[-MAX_KEPT_EVENTS:]
            else:
                self._events = (self._events + [message])[-MAX_KEPT_EVENTS:]
            self._ready.notify_all()

    @property
    def closed(self):
        with self._ready:
            return self._closed

    def call(self, method, params=None, session=None, timeout=CALL_SECONDS):
        call_id = next(self._ids)
        message = {"id": call_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        if self.closed:
            raise CdpError("the browser is closed")
        self._transport.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        with self._ready:
            while call_id not in self._answers:
                remaining = deadline - time.monotonic()
                if self._closed or remaining <= 0:
                    raise CdpError(f"the browser didn't answer {method} in time" if not self._closed else "the browser closed")
                self._ready.wait(remaining)
            answer = self._answers.pop(call_id)
        if "error" in answer:
            raise CdpError(f"{method} failed: {str(answer['error'].get('message', answer['error']))[:200]}")
        return answer.get("result") or {}

    def forget_events(self, method, session=None):
        with self._ready:
            self._events = [event for event in self._events
                            if not (event.get("method") == method and event.get("sessionId") == session)]

    def wait_for_event(self, method, session=None, timeout=CALL_SECONDS):
        deadline = time.monotonic() + timeout
        with self._ready:
            while True:
                for event in self._events:
                    if event.get("method") == method and event.get("sessionId") == session:
                        self._events.remove(event)
                        return event.get("params") or {}
                remaining = deadline - time.monotonic()
                if self._closed or remaining <= 0:
                    return None
                self._ready.wait(remaining)

    def close(self):
        self._closing = True
        self._transport.close()
