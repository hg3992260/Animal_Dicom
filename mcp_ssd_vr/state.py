"""事件类型常量 + GuiState 快照辅助。

桥协议: TCP line-delimited JSON
  请求 {"id","op","args"}  响应 {"id","ok","data"}  事件 {"type","data","ts"}
"""
from __future__ import annotations

# GUI 主动推送的事件类型
EVENT_TYPES = [
    "load_start",
    "load_done",
    "progress",
    "error",
    "state",
    "user_action",
    "roi_start",
    "roi_done",
    "roi_error",
    "gui_ready",
    "gui_exit",
]

# 渲染模式 → 下拉框索引（与 ssd_vr_viewer.mode_combo 顺序一致）
MODE_ORDER = [
    "stable",
    "hd_surface",
    "cinematic",
    "nature_channels",
    "figure8_channels",
    "layer_channel",
    "frangi_channel",
    "bone_mono",
    "2dtf",
    "spectral",
    "exposure_render",
    "dual_volume",
]

# MPR 阅片页的测量工具（key → GUI 显示名），与 ViewerWindow.sam_tool_names 一致
SAM_TOOLS = {
    "none": "看",
    "line": "长",
    "angle": "角",
    "rect": "矩",
    "ellipse": "圆",
    "poly": "形",
}

# 顶层页签（按顺序）；**不要**在下游硬编码下标，用 title 子串查找：
#   ssdvr_switch_tab(title="档案") / ssdvr_switch_tab(title="设置")
TAB_TITLES = ["爱宠列表", "MPR 阅片", "渲染", "档案中心", "设置"]

# 右侧区域显隐规则（与 ViewerWindow._on_tab_changed 一致）：
#   隐藏 = 爱宠列表 / MPR 阅片 / 设置；显示 = 渲染 / 档案中心
TABS_WITHOUT_RIGHT_AREA = ["爱宠列表", "MPR", "设置"]


def mode_index(mode: str) -> int:
    if mode in MODE_ORDER:
        return MODE_ORDER.index(mode)
    raise ValueError(f"未知渲染模式: {mode}（可用: {MODE_ORDER}）")


def infer_task(modality: str) -> str:
    """【已废弃】按 Modality 推断 TotalSegmentator 任务。

    自动 ROI（TotalSegmentator / SynthSeg / nnU-Net）已从本程序移除，
    不再有「分割任务」概念：分割统一走「MPR 阅片」页的 3D SAM。
    保留此函数只为兼容旧调用方（返回值无实际用途）。
    """
    m = (modality or "").upper()
    if m.startswith("MR"):
        return "total_mr"
    return "total"


def is_valid_event(etype: str) -> bool:
    return etype in EVENT_TYPES
