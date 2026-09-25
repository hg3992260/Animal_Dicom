"""记录器：SQLite + JSONL 双写。

- 事件:   {"t": "...", "type": "load_done", "data": {...}}
- 工具调用: {"t": "...", "tool": "ssdvr_load_dicom", "args": {...}, "ok": true, "result": {...}}
- 错误:   {"t": "...", "level": "error", "source": "bridge", "msg": "..."}
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from . import config


class Recorder:
    def __init__(self) -> None:
        config.ensure_dirs()
        self._lock = threading.Lock()
        self._db_path = config.db_path()
        self._jsonl_path = os.path.join(
            config.record_dir(), f"ssd_vr_events_{self._session_id()}.jsonl"
        )
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_schema()

    @staticmethod
    def _session_id() -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, type TEXT, data TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, tool TEXT, args TEXT, ok INTEGER, result TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, level TEXT, source TEXT, msg TEXT
            )"""
        )
        self._conn.commit()

    def _append_jsonl(self, obj: Dict[str, Any]) -> None:
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def record_event(self, etype: str, data: Dict[str, Any]) -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, type, data) VALUES (?, ?, ?)",
                (ts, etype, json.dumps(data, ensure_ascii=False)),
            )
            self._conn.commit()
            self._append_jsonl({"t": ts, "type": etype, "data": data})

    def record_tool_call(self, tool: str, args: Dict[str, Any], ok: bool, result: Any) -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_calls (ts, tool, args, ok, result) VALUES (?, ?, ?, ?, ?)",
                (ts, tool, json.dumps(args, ensure_ascii=False), int(ok),
                 json.dumps(result, ensure_ascii=False, default=str)),
            )
            self._conn.commit()
            self._append_jsonl({"t": ts, "tool": tool, "args": args, "ok": ok, "result": result})

    def record_error(self, msg: str, source: str = "mcp", level: str = "error") -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO errors (ts, level, source, msg) VALUES (?, ?, ?, ?)",
                (ts, level, source, msg),
            )
            self._conn.commit()
            self._append_jsonl({"t": ts, "level": level, "source": source, "msg": msg})

    def query_events(self, etype: Optional[str] = None, limit: int = 200) -> list:
        sql = "SELECT ts, type, data FROM events"
        params: list = []
        if etype:
            sql += " WHERE type = ?"
            params.append(etype)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for ts, t, data in rows:
            try:
                d = json.loads(data)
            except Exception:
                d = data
            out.append({"t": ts, "type": t, "data": d})
        return out

    def query_errors(self, limit: int = 50) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, level, source, msg FROM errors ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"t": r[0], "level": r[1], "source": r[2], "msg": r[3]} for r in rows]

    def query_tool_calls(self, tool: Optional[str] = None, limit: int = 100) -> list:
        sql = "SELECT ts, tool, args, ok, result FROM tool_calls"
        params: list = []
        if tool:
            sql += " WHERE tool = ?"
            params.append(tool)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for ts, t, args, ok, result in rows:
            out.append({
                "t": ts, "tool": t,
                "args": json.loads(args) if args else {},
                "ok": bool(ok),
                "result": json.loads(result) if result else None,
            })
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
