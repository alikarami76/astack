"""A small WebSocket (RFC 6455) implementation on the standard library, for flynn desk's live connections:
the flynn agent <-> the team server (agent_hub), and the chat panel <-> the server (panel_server).

No extensions (no compression), text and binary messages, fragmentation, ping/pong and close. Messages here are
JSON text. Both ends: `WebSocket(sock_file_r, sock_file_w, client=...)`; `send_json`, `recv_json`, `ping`, `close`.

  server side:  ws = accept(handler)          after checking the path and the caller; None when not an upgrade
  client side:  ws = connect(url, headers)    url ws://, wss://, http:// or https:// (mapped to ws/wss)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024 * 1024


class Closed(Exception):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


class WebSocket:
    def __init__(self, rfile, wfile, sock: socket.socket | None = None, client: bool = False):
        self.r, self.w, self.sock, self.client = rfile, wfile, sock, client
        self.lock = threading.Lock()
        self.closed = False

    # ---- frames ----
    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.r.read(n - len(buf))
            if not chunk:
                raise Closed("connection closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode: int, data: bytes) -> None:
        head = bytes([0x80 | opcode])
        mask = 0x80 if self.client else 0
        n = len(data)
        if n < 126:
            head += bytes([mask | n])
        elif n < 65536:
            head += bytes([mask | 126]) + struct.pack("!H", n)
        else:
            head += bytes([mask | 127]) + struct.pack("!Q", n)
        if self.client:
            key = os.urandom(4)
            data = bytes(b ^ key[i % 4] for i, b in enumerate(data)) if n < 4096 else _mask_fast(data, key)
            head += key
        with self.lock:
            if self.closed and opcode != 0x8:
                raise Closed("closed")
            try:
                self.w.write(head + data)
                self.w.flush()
            except (OSError, ValueError) as e:
                self.closed = True
                raise Closed(str(e)) from e

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._read_exact(2)
        fin, opcode, masked, n = bool(b1 & 0x80), b1 & 0x0F, bool(b2 & 0x80), b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8))[0]
        if n > MAX_MESSAGE:
            raise Closed("message too large")
        key = self._read_exact(4) if masked else b""
        data = self._read_exact(n)
        if masked:
            data = _mask_fast(data, key)
        return fin, opcode, data

    # ---- messages ----
    def recv(self) -> str | bytes:
        """The next text (str) or binary (bytes) message. Answers pings; raises Closed at the end."""
        parts, kind = [], None
        while True:
            fin, opcode, data = self._recv_frame()
            if opcode == 0x8:                       # close
                try:
                    self._send_frame(0x8, data[:2])
                except Closed:
                    pass
                self.closed = True
                raise Closed("closed by peer")
            if opcode == 0x9:                       # ping
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:                       # pong
                continue
            if opcode in (0x1, 0x2):
                kind, parts = opcode, [data]
            elif opcode == 0x0 and kind:
                parts.append(data)
            if fin and kind:
                msg = b"".join(parts)
                return msg.decode("utf-8") if kind == 0x1 else msg

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_json(self, obj) -> None:
        self.send_text(json.dumps(obj, ensure_ascii=False))

    def recv_json(self):
        while True:
            m = self.recv()
            if isinstance(m, str):
                try:
                    return json.loads(m)
                except ValueError:
                    continue

    def ping(self) -> None:
        self._send_frame(0x9, b"")

    def close(self) -> None:
        if not self.closed:
            try:
                self._send_frame(0x8, struct.pack("!H", 1000))
            except Closed:
                pass
        self.closed = True
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def settimeout(self, t: float | None) -> None:
        if self.sock:
            self.sock.settimeout(t)


def _mask_fast(data: bytes, key: bytes) -> bytes:
    n = len(data)
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big") if n else b""


def is_upgrade(headers) -> bool:
    return ("websocket" in (headers.get("Upgrade") or "").lower()
            and "upgrade" in (headers.get("Connection") or "").lower())


def accept(handler) -> WebSocket | None:
    """Finish the handshake on a BaseHTTPRequestHandler. The handler's thread then owns the connection."""
    if not is_upgrade(handler.headers):
        return None
    key = handler.headers.get("Sec-WebSocket-Key") or ""
    handler.protocol_version = "HTTP/1.1"           # browsers accept the upgrade only on an HTTP/1.1 status line
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(key))
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True                 # when the socket ends, the handler ends
    return WebSocket(handler.rfile, handler.wfile, handler.connection)


def ssl_context() -> ssl.SSLContext:
    """Verify servers the way the computer does. The python.org Python on a Mac has no certificates until its
    "Install Certificates" step runs (2026-10-07: every https call failed with CERTIFICATE_VERIFY_FAILED), so use
    the system trust store (truststore) when installed, else certifi's list, else Python's default."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def connect(url: str, headers: dict | None = None, timeout: float = 20) -> WebSocket:
    u = urlsplit(url)
    secure = u.scheme in ("wss", "https")
    host, port = u.hostname, u.port or (443 if secure else 80)
    raw = socket.create_connection((host, port), timeout=timeout)
    sock = ssl_context().wrap_socket(raw, server_hostname=host) if secure else raw
    key = base64.b64encode(os.urandom(16)).decode()
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    lines = [f"GET {path} HTTP/1.1", f"Host: {u.netloc}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    rfile = sock.makefile("rb")
    status = rfile.readline().decode("latin-1").strip()
    resp = {}
    while True:
        line = rfile.readline().decode("latin-1").strip()
        if not line:
            break
        k, _, v = line.partition(":")
        resp[k.strip().lower()] = v.strip()
    if " 101 " not in f"{status} ":
        sock.close()
        code = (status.split() + ["", "?"])[1]
        raise ConnectionRefusedError(f"websocket refused: HTTP {code}")
    if resp.get("sec-websocket-accept") != accept_key(key):
        sock.close()
        raise ConnectionError("websocket handshake failed")
    sock.settimeout(None)
    return WebSocket(rfile, sock.makefile("wb"), sock, client=True)
