"""事件/错误/工具调用记录查询与导出工具。"""
from __future__ import annotations

import json
import os
from typing import Optional

from .. import config
from ..context import get_ctx

TOOL_META = {"name": "ssdvr_recording", "version": "1.0"}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_query_events(etype: Optional[str] = None, limit: int = 200) -> dict:
        """查询已记录的事件（SQLite 双写）。"""
        ctx = get_ctx()
        if ctx.recorder is None:
            return {"ok": False, "error": "recorder not initialized"}
        return {"ok": True, "events": ctx.recorder.query_events(etype=etype, limit=limit)}

    @mcp.tool()
    def ssdvr_query_errors(limit: int = 50) -> dict:
        """查询已记录的错误。"""
        ctx = get_ctx()
        if ctx.recorder is None:
            return {"ok": False, "error": "recorder not initialized"}
        return {"ok": True, "errors": ctx.recorder.query_errors(limit=limit)}

    @mcp.tool()
    def ssdvr_query_tool_calls(tool: Optional[str] = None, limit: int = 100) -> dict:
        """查询工具调用记录。"""
        ctx = get_ctx()
        if ctx.recorder is None:
            return {"ok": False, "error": "recorder not initialized"}
        return {"ok": True, "tool_calls": ctx.recorder.query_tool_calls(tool=tool, limit=limit)}

    @mcp.tool()
    def ssdvr_export(out_path: Optional[str] = None) -> dict:
        """导出记录（JSONL）。默认 mcp_records/export_<ts>.jsonl。"""
        ctx = get_ctx()
        config.ensure_dirs()
        src = getattr(ctx.recorder, "_jsonl_path", None)
        if not src or not os.path.exists(src):
            return {"ok": False, "error": "no jsonl record"}
        dst = out_path or os.path.join(config.record_dir(), f"export_{int(os.path.getmtime(src))}.jsonl")
        import shutil
        shutil.copyfile(src, dst)
        return {"ok": True, "path": dst}

    @mcp.tool()
    def ssdvr_sessions() -> dict:
        """列出记录会话文件。"""
        config.ensure_dirs()
        files = sorted(os.listdir(config.record_dir()))
        return {"ok": True, "record_dir": config.record_dir(), "files": files}

    @mcp.tool()
    def ssdvr_hello_test(name: str = "world") -> dict:
        """热重载验证工具（改后调 ssdvr_reload_tools 即生效）。"""
        return {"ok": True, "message": f"hello {name}"}
