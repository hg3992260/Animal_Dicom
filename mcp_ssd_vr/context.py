"""工具层共享上下文（热重载安全的单例 holder）。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class Context:
    def __init__(self) -> None:
        self.client: Any = None       # BridgeClient
        self.recorder: Any = None     # Recorder
        self.mcp: Any = None          # FastMCP 实例（用于 reload_tools）
        self.port: Optional[int] = None
        self.gui_pid: Optional[int] = None
        self.gui_python: str = ""
        self.gui_process: Any = None
        self.last_error: Optional[str] = None
        self.waited_events: List[Dict[str, Any]] = []
        self._started_at = time.time()


_ctx = Context()


def get_ctx() -> Context:
    return _ctx
