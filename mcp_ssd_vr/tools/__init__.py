"""工具层注册器（支持热重载）。

FastMCP 3.x 对同名工具 add_tool 会覆盖旧工具（仅打印 WARNING），
因此 ssdvr_reload_tools 直接 importlib.reload 各模块后重新 register 即可生效。
"""
from __future__ import annotations

import importlib
from typing import List

TOOL_MODULES = [
    "lifecycle",
    "control",
    "cases",
    "dicom_scan",
    "inspect",
    "capture",
    "recording",
]


def register_all(mcp, reloading: bool = False) -> List[str]:
    from ..context import get_ctx
    get_ctx().mcp = mcp
    registered: List[str] = []
    for mod_name in TOOL_MODULES:
        mod = importlib.import_module(f"{__name__}.{mod_name}")
        if reloading:
            mod = importlib.reload(mod)
        if hasattr(mod, "register"):
            mod.register(mcp)
        registered.append(mod_name)
    return registered


def reload_tools(mcp) -> List[str]:
    return register_all(mcp, reloading=True)
