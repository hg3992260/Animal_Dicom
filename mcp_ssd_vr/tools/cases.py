"""新 UI（爱宠影像工作站）工具：页签 / MPR / 3D SAM / 档案中心 / 设置 / 病例 / PACS / AI。

这些工具对应 ssd_vr_viewer.py 的顶层页签：
  爱宠列表(0) │ MPR 阅片(1) │ 渲染(2) │ 档案中心(3) │ 设置(4)

注意：不要硬编码页签下标，用 ssdvr_switch_tab(title="档案") 之类的标题子串。
「自动ROI（TotalSegmentator/SynthSeg/nnU-Net）」已从程序移除，分割统一走 MPR 页的 3D SAM。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import config
from ._util import call

TOOL_META = {"name": "ssdvr_ui", "version": "1.0"}


def _shot_dir() -> str:
    try:
        config.ensure_dirs()
    except Exception:
        pass
    return config.shots_dir()


def register(mcp) -> None:
    # ---------------- 页签 / 总览 ----------------
    @mcp.tool()
    def ssdvr_list_tabs() -> dict:
        """列出顶层页签与当前页（爱宠列表 / MPR 阅片 / 渲染 / 档案中心 / 设置）。"""
        ok, data = call("list_tabs", {}, tool_name="ssdvr_list_tabs")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_switch_tab(title: Optional[str] = None, index: Optional[int] = None) -> dict:
        """切换顶层页签：title 为标题子串（"爱宠"/"MPR"/"渲染"/"档案"/"设置"），或给 index。

        切换会同步右侧区域的显隐：爱宠列表/MPR/设置 = 隐藏右区，渲染/档案中心 = 显示。
        """
        args: Dict[str, Any] = {}
        if title is not None:
            args["title"] = title
        if index is not None:
            args["index"] = index
        ok, data = call("switch_tab", args, tool_name="ssdvr_switch_tab")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_ui_state() -> dict:
        """各子系统状态：tabs / mpr / archive / settings / pacs / case / ai / pets。"""
        ok, data = call("get_ui_state", {}, tool_name="ssdvr_ui_state")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_screenshot_window(tab: Optional[str] = None) -> dict:
        """整窗截图（含非 VTK 页面）。tab 给标题子串可先切页（如 "设置"、"档案"）。"""
        args = {"out_dir": _shot_dir()}
        if tab:
            args["tab"] = tab
        ok, data = call("screenshot_window", args, timeout=30.0,
                        tool_name="ssdvr_screenshot_window")
        return {"ok": ok, **data}

    # ---------------- MPR 阅片 ----------------
    @mcp.tool()
    def ssdvr_mpr_set_window_level(wl: Optional[int] = None, ww: Optional[int] = None) -> dict:
        """MPR 阅片窗宽窗位（HU）：wl=窗位，ww=窗宽。与 3D 渲染页的窗宽窗位不是同一套控件。"""
        if wl is None and ww is None:
            return {"ok": False, "error": "需至少给 wl 或 ww"}
        args: Dict[str, Any] = {}
        if wl is not None:
            args["wl"] = wl
        if ww is not None:
            args["ww"] = ww
        ok, data = call("mpr_set_window_level", args, tool_name="ssdvr_mpr_set_window_level")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_mpr_set_tool(tool: str) -> dict:
        """MPR 测量工具。可用写法：key（none/line/angle/rect/ellipse/poly）、
        中文名（浏览/长度/角度/矩形/圆形椭圆/不规则多边形）或按钮标签（看/长/角/矩/圆/形）。
        """
        ok, data = call("mpr_set_tool", {"tool": tool}, tool_name="ssdvr_mpr_set_tool")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_mpr_clear(what: str = "all") -> dict:
        """清空 MPR 内容：measures(测量) | points(SAM提示点) | result(SAM结果) | all。"""
        what = (what or "all").lower()
        mapping = {"measures": "mpr_clear_measures", "points": "mpr_clear_points",
                   "result": "mpr_clear_result", "mask": "mpr_clear_result"}
        ops = [mapping[what]] if what in mapping else \
              ["mpr_clear_measures", "mpr_clear_points", "mpr_clear_result"]
        results = []
        for op in ops:
            ok, data = call(op, {}, tool_name=f"ssdvr_mpr_clear:{op}")
            results.append({"op": op, "ok": ok, **data})
        return {"ok": all(r.get("ok") for r in results), "cleared": results}

    # ---------------- 3D SAM 分割 / 已保存 ROI ----------------
    @mcp.tool()
    def ssdvr_sam_run(threshold: Optional[float] = None) -> dict:
        """触发 3D SAM 分割（需先在 MPR 页有提示点）。异步：完成后推 roi_done 事件。"""
        args: Dict[str, Any] = {}
        if threshold is not None:
            args["threshold"] = threshold
        ok, data = call("sam_run", args, timeout=15.0, tool_name="ssdvr_sam_run")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_sam_list_rois() -> dict:
        """列出「已保存 ROI（3D SAM）」：index/name/volume_cm3/mean_hu/std_hu/has_mask。"""
        ok, data = call("sam_list_rois", {}, tool_name="ssdvr_sam_list_rois")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_sam_load_roi(index: Optional[int] = None, name: Optional[str] = None) -> dict:
        """按 index 或 name 加载已保存 ROI → 叠加绿色高亮（可多选时用 indexes，本工具单条）。"""
        args: Dict[str, Any] = {}
        if index is not None:
            args["index"] = index
        if name is not None:
            args["name"] = name
        ok, data = call("sam_load_roi", args, timeout=60.0, tool_name="ssdvr_sam_load_roi")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_sam_delete_roi(index: Optional[int] = None, name: Optional[str] = None) -> dict:
        """删除已保存 ROI（连带 npy 掩膜）。MCP 模式下不弹确认框。"""
        args: Dict[str, Any] = {}
        if index is not None:
            args["index"] = index
        if name is not None:
            args["name"] = name
        ok, data = call("sam_delete_roi", args, timeout=30.0, tool_name="ssdvr_sam_delete_roi")
        return {"ok": ok, **data}

    # ---------------- 档案中心 ----------------
    @mcp.tool()
    def ssdvr_archive_list() -> dict:
        """列出档案册：index/name/dir/is_demo + 根目录与当前选中项。"""
        ok, data = call("archive_list", {}, tool_name="ssdvr_archive_list")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_archive_refresh() -> dict:
        """刷新档案册列表。"""
        ok, data = call("archive_refresh", {}, tool_name="ssdvr_archive_refresh")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_archive_open(index: Optional[int] = None, name: Optional[str] = None) -> dict:
        """打开某份档案的病例详情页（index，或 name / 目录 id 子串，如 "0000_DEFAULT_DEMO"）。"""
        args: Dict[str, Any] = {}
        if index is not None:
            args["index"] = index
        if name is not None:
            args["name"] = name
        ok, data = call("archive_open", args, tool_name="ssdvr_archive_open")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_archive_save_shot(kind: str = "render") -> dict:
        """把当前渲染/阅片截图存入对应档案（kind: render | mpr）。需先有对应档案。"""
        ok, data = call("archive_save_shot", {"kind": kind}, timeout=60.0,
                        tool_name="ssdvr_archive_save_shot")
        return {"ok": ok, **data}

    # ---------------- 设置（DeepSeek）----------------
    @mcp.tool()
    def ssdvr_settings_get(include_key: bool = False) -> dict:
        """读取设置：key_set/base_url/model/temperature/config_path。默认不返回 Key 明文。"""
        ok, data = call("settings_get", {"include_key": bool(include_key)},
                        tool_name="ssdvr_settings_get")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_settings_set(model: Optional[str] = None, base_url: Optional[str] = None,
                           temperature: Optional[float] = None, api_key: Optional[str] = None,
                           allow_key: bool = False) -> dict:
        """写设置并保存到 deepseek_config.json。

        api_key 必须同时 allow_key=True 才会写入（避免误改密钥）。
        """
        args: Dict[str, Any] = {}
        if model is not None:
            args["model"] = model
        if base_url is not None:
            args["base_url"] = base_url
        if temperature is not None:
            args["temperature"] = temperature
        if api_key is not None:
            args["api_key"] = api_key
            args["allow_key"] = bool(allow_key)
        ok, data = call("settings_set", args, timeout=30.0, tool_name="ssdvr_settings_set")
        return {"ok": ok, **data}

    # ---------------- 病例档案表单 ----------------
    @mcp.tool()
    def ssdvr_case_get() -> dict:
        """读取病例档案表单当前值（5 段 24 字段）与模板路径/字段清单。"""
        ok, data = call("case_get", {}, tool_name="ssdvr_case_get")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_case_set(values: Dict[str, Any]) -> dict:
        """写入病例档案表单字段（{字段: 值}，键见 ssdvr_case_get().fields）。"""
        ok, data = call("case_set", {"values": values or {}}, tool_name="ssdvr_case_set")
        return {"ok": ok, **data}

    # ---------------- 爱宠列表（病例扫描）----------------
    @mcp.tool()
    def ssdvr_pets_scan(root: Optional[str] = None) -> dict:
        """扫描病例目录并填充「爱宠列表」树（root 省略则用界面上已填的目录）。"""
        args: Dict[str, Any] = {}
        if root:
            args["root"] = root
        ok, data = call("pets_scan", args, timeout=180.0, tool_name="ssdvr_pets_scan")
        return {"ok": ok, **data}

    # ---------------- PACS 取片 ----------------
    @mcp.tool()
    def ssdvr_pacs_status() -> dict:
        """PACS 节点状态：node/host/port/called_ae/status/studies/series/busy。"""
        ok, data = call("pacs_get_state", {}, tool_name="ssdvr_pacs_status")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_pacs_echo(host: Optional[str] = None, port: Optional[int] = None,
                        called: Optional[str] = None, calling: Optional[str] = None) -> dict:
        """C-ECHO 连通性测试（可顺带写入节点参数）。异步：可轮询 ssdvr_pacs_status。"""
        args: Dict[str, Any] = {}
        for k, v in (("host", host), ("port", port), ("called", called), ("calling", calling)):
            if v is not None:
                args[k] = v
        ok, data = call("pacs_echo", args, timeout=15.0, tool_name="ssdvr_pacs_echo")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_pacs_find_studies(host: Optional[str] = None, port: Optional[int] = None,
                                called: Optional[str] = None) -> dict:
        """C-FIND 查询检查列表（结果填进 PACS 树）。异步：完成后用 ssdvr_pacs_status 看数量。"""
        args: Dict[str, Any] = {}
        for k, v in (("host", host), ("port", port), ("called", called)):
            if v is not None:
                args[k] = v
        ok, data = call("pacs_find_studies", args, timeout=15.0,
                        tool_name="ssdvr_pacs_find_studies")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_pacs_retrieve(study_index: int = 0) -> dict:
        """C-GET 获取检查（默认第 0 个）；完成后可 ssdvr_load_dicom 载入到阅片。"""
        ok, data = call("pacs_retrieve", {"study_index": study_index}, timeout=15.0,
                        tool_name="ssdvr_pacs_retrieve")
        return {"ok": ok, **data}

    # ---------------- 3D SAM 栈自检（冻结版诊断）----------------
    @mcp.tool()
    def ssdvr_sam_probe() -> dict:
        """3D SAM 依赖自检：torch/torchio/medim/monai/cupy 版本、CUDA 可用性、
        SAM-Med3D 目录与权重是否存在、sam_adapter 能否 import。

        便携包没有控制台，靠它确认分割依赖是否齐全（尤其"基础版"缺 torch 时）。
        """
        ok, data = call("sam_probe", {}, timeout=180.0, tool_name="ssdvr_sam_probe")
        return {"ok": ok, **data}

    # ---------------- 运行期限（30 天强制）----------------
    @mcp.tool()
    def ssdvr_trial_status() -> dict:
        """运行期限状态：status(ok/warning/expired/tampered)/days_left/expires_at/hard_deadline。

        期限为代码常量强制（30 天，且有绝对上限 HARD_DEADLINE），无 GUI 开关、无环境变量旁路。
        """
        ok, data = call("trial_status", {}, tool_name="ssdvr_trial_status")
        return {"ok": ok, **data}

    # ---------------- DeepSeek AI 辅助 ----------------
    @mcp.tool()
    def ssdvr_ai_status() -> dict:
        """AI 辅助状态：key_set/model/autosave/status/输出长度与结尾片段。"""
        ok, data = call("ai_get_state", {}, tool_name="ssdvr_ai_status")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_ai_run(prompt: Optional[str] = None) -> dict:
        """运行 DeepSeek AI 引导补全（可先写入 prompt）。需已配置 API Key。异步执行。"""
        args: Dict[str, Any] = {}
        if prompt:
            args["prompt"] = prompt
        ok, data = call("ai_run", args, timeout=15.0, tool_name="ssdvr_ai_run")
        return {"ok": ok, **data}
