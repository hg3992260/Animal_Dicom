"""状态与事件工具。"""
from __future__ import annotations

import json
from typing import Optional

from .. import bridge_registry as registry
from .. import config
from ..context import get_ctx
from ._util import call, get_client

TOOL_META = {"name": "ssdvr_inspect", "version": "1.0"}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_status() -> dict:
        """健康检查：bridge_connected/port/gui_state/last_error/record_session/启动目标。

        未连接时返回的 discovery 字段说明在哪里找过桥（发现文件路径 + 扫描范围），
        便于判断"GUI 没开"还是"GUI 用了 --no-mcp"。
        """
        ctx = get_ctx()
        client = get_client(auto_connect=True)
        connected = bool(client is not None and client.connected)
        gui_state = None
        if connected:
            ok, data = call("query_state", {}, timeout=5.0, tool_name="ssdvr_status")
            if ok:
                gui_state = data
        target, path = config.resolve_launch_target()
        return {
            "ok": True,
            "bridge_connected": connected,
            "port": ctx.port,
            "gui_pid": ctx.gui_pid,
            "gui_state": gui_state,
            "last_error": ctx.last_error,
            "record_session": getattr(ctx.recorder, "_jsonl_path", None) if ctx.recorder else None,
            "launch": {"target": target, "path": path,
                       "preference": config.launch_preference(),
                       "exe_found": bool(config.gui_exe())},
            "discovery": registry.registry_info(),
            "scan_range": [config.default_port(), config.default_port() + 99],
        }

    @mcp.tool()
    def ssdvr_state_snapshot() -> dict:
        """完整状态快照。

        顶层：dicom_loaded/dims/spacing/load_busy/render_window_size/render_mode/
        ssd_scale/vr_scale/wl_offset/ww_scale/modality/is_2d/last_error；
        页面：tabs/tab_count/current_tab/current_tab_index/right_area_visible/splitter_sizes；
        子系统：mpr（窗宽窗位/工具/提示点/SAM结果）、roi_saved、archive、settings、pacs、case、ai、pets。
        旧键 roi_task/roi_running/roi_results_count 仍在，但已映射到 3D SAM / 已保存 ROI。
        """
        ok, data = call("query_state", {}, timeout=5.0, tool_name="ssdvr_state_snapshot")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_wait_event(etype: str, timeout: float = 120.0, contains: Optional[str] = None) -> dict:
        """阻塞等待 GUI 推送的指定事件。etype: load_start|load_done|progress|error|state|user_action|roi_done|roi_error|gui_ready|gui_exit。contains 为事件 data 的 JSON 子串过滤。"""
        client = get_client(auto_connect=True)
        if client is None or not client.connected:
            return {"ok": False, "error": "bridge not connected"}
        evt = client.wait_event(etype, timeout=timeout, contains=contains)
        if evt is None:
            return {"ok": False, "error": f"等待 {etype} 超时 ({timeout}s)", "timed_out": True}
        return {"ok": True, "event": evt.get("type"), "data": evt.get("data"), "ts": evt.get("ts")}
