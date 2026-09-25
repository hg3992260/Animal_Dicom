"""截图采集工具。"""
from __future__ import annotations

from typing import Optional

from .. import config
from ._util import call

TOOL_META = {"name": "ssdvr_screenshot", "version": "1.0"}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_screenshot(out_dir: Optional[str] = None, switch_to_render: bool = False) -> dict:
        """截取 VTK 渲染窗口，返回 {ok, path, bytes, png_base64, stats, vtk_visible}。默认存 mcp_records/shots/。

        stats 含 mean/bright_pct/unique_colors 用于诊断黑屏：正常渲染=100+ 颜色、>5% 亮像素。
        vtk_visible=false 表示当前页签隐藏了右侧渲染区（爱宠列表/MPR/设置）——此时截图是空白；
        switch_to_render=true 会先切到「渲染」页再截。
        """
        target = out_dir or config.shots_dir()
        ok, data = call("screenshot", {"out_dir": target, "switch_to_render": switch_to_render},
                        timeout=30.0, tool_name="ssdvr_screenshot")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}
