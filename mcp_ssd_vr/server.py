"""SSD+VR Viewer MCP server 入口（FastMCP, stdio JSON-RPC）。

运行方式（任选其一，均可用）:
  D:\\python\\envs\\mar\\python.exe mcp_ssd_vr\\server.py
  D:\\python\\envs\\mar\\python.exe -m mcp_ssd_vr.server
"""
from __future__ import annotations

import os
import sys

# 保证直接以脚本运行（而非 -m）时也能导入 mcp_ssd_vr 包
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastmcp import FastMCP

from mcp_ssd_vr import config
from mcp_ssd_vr.context import get_ctx
from mcp_ssd_vr.recorder import Recorder


def build_server() -> FastMCP:
    mcp = FastMCP(
        "ssd_vr",
        instructions="SSD+VR Viewer DICOM 体绘制与语义分割封装。"
        "先 ssdvr_launch 启动 GUI，再 ssdvr_load_dicom 加载，"
        "ssdvr_wait_event(load_done) 等渲染完成，ssdvr_screenshot 截图确认，"
        "ssdvr_trigger_roi 触发语义分割（total|total_v3|total_mr|brain_structures|synthseg）。",
    )

    config.ensure_dirs()

    ctx = get_ctx()
    ctx.recorder = Recorder()

    from mcp_ssd_vr.tools import register_all
    register_all(mcp)

    return mcp


def main() -> int:
    mcp = build_server()
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
