"""TCP 客户端：连 GUI 内嵌桥，收事件 / 发命令。

协议: TCP line-delimited JSON。
  请求  {"id","op","args"}   响应 {"id","ok","data"}   事件 {"type","data","ts"}
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple


class BridgeClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7799) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self._on_event = None  # Optional[Callable[[dict], None]] 事件回调（不消费队列）

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, timeout: float = 5.0) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.settimeout(None)
            self._sock = sock
            self._closed.clear()
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            return True
        except OSError:
            return False

    def _read_loop(self) -> None:
        buf = b""
        sock = self._sock
        while sock is not None and not self._closed.is_set():
            try:
                chunk = sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                self._handle_line(line)
        self._mark_disconnected()

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            return
        if "id" in msg:
            with self._lock:
                p = self._pending.get(msg["id"])
            if p is not None:
                p["result"] = msg
                p["evt"].set()
        elif "type" in msg:
            self._events.put(msg)
            if self._on_event is not None:
                try:
                    self._on_event(msg)
                except Exception:
                    pass

    def set_event_handler(self, fn) -> None:
        self._on_event = fn

    def _mark_disconnected(self) -> None:
        self._sock = None
        with self._lock:
            for p in self._pending.values():
                p["result"] = {"id": p["id"], "ok": False, "data": {"error": "bridge disconnected"}}
                p["evt"].set()

    def call(self, op: str, args: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Tuple[bool, Any]:
        if self._sock is None:
            return False, {"error": "bridge not connected"}
        req_id = uuid.uuid4().hex[:12]
        payload = json.dumps({"id": req_id, "op": op, "args": args or {}}, ensure_ascii=False)
        evt = threading.Event()
        with self._lock:
            self._pending[req_id] = {"id": req_id, "evt": evt, "result": None}
        try:
            self._sock.sendall(payload.encode("utf-8") + b"\n")
        except OSError as e:
            with self._lock:
                self._pending.pop(req_id, None)
            return False, {"error": f"send failed: {e}"}
        if not evt.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            return False, {"error": f"timeout waiting for op '{op}' ({timeout}s)"}
        with self._lock:
            result = self._pending.pop(req_id, {}).get("result")
        if result is None:
            return False, {"error": "no result"}
        ok = bool(result.get("ok", False))
        data = result.get("data", {})
        return ok, data

    def drain_events(self) -> list:
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def wait_event(self, etype: str, timeout: float = 120.0, contains: Optional[str] = None) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                evt = self._events.get(timeout=remaining)
            except queue.Empty:
                return None
            if evt.get("type") == etype:
                if contains is None:
                    return evt
                data_str = json.dumps(evt.get("data", {}), ensure_ascii=False)
                if contains in data_str:
                    return evt
        return None

    def close(self) -> None:
        self._closed.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
