"""渲染控制 + 加载 + 语义分割工具。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import dicom as dicom_lib
from ._util import call

TOOL_META = {"name": "ssdvr_control", "version": "1.0"}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_load_dicom(path: str, series: Optional[int] = None) -> dict:
        """加载 DICOM。path 可为病例根目录/序列文件夹/单文件；series 为 ssdvr_scan_case 返回的下标。

        忙碌时返回 {ok:false, load_busy:true}。返回 {ok, accepted, resolution}。
        """
        resolution = dicom_lib.resolve_series_path(path, series)
        if not resolution.get("ok"):
            return {"ok": False, "error": resolution.get("error")}
        ok, data = call("load_dicom", {"path": resolution["path"]}, timeout=15.0,
                        tool_name="ssdvr_load_dicom")
        return {"ok": ok, "accepted": ok, "resolution": {**resolution, **({"response": data} if ok else {})},
                "gui": data}

    @mcp.tool()
    def ssdvr_set_mode(mode: str) -> dict:
        """切换渲染模式：stable|hd_surface|cinematic|nature_channels|spectral|exposure_render|dual_volume|figure8_channels|layer_channel|frangi_channel|bone_mono|2dtf。返回实际生效模式。"""
        ok, data = call("set_mode", {"mode": mode}, tool_name="ssdvr_set_mode")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_set_opacity(ssd: Optional[float] = None, vr: Optional[float] = None) -> dict:
        """调整 SSD/VR 不透明度（0.0-1.0），至少给一个。"""
        if ssd is None and vr is None:
            return {"ok": False, "error": "需至少给 ssd 或 vr"}
        args: Dict[str, Any] = {}
        if ssd is not None:
            args["ssd"] = ssd * 100.0
        if vr is not None:
            args["vr"] = vr * 100.0
        ok, data = call("set_opacity", args, tool_name="ssdvr_set_opacity")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_set_camera(view: Optional[str] = None, azimuth: float = 0, elevation: float = 0,
                         roll: float = 0, dolly: float = 1.0, position: Optional[List[float]] = None,
                         focal: Optional[List[float]] = None, view_up: Optional[List[float]] = None,
                         view_angle: Optional[float] = None, reset: bool = False) -> dict:
        """VR 相机/视角控制。view 预设: coronal|coronal_rear|sagittal|sagittal_rear|axial|axial_rear|three_quarter|front_top。

        azimuth/elevation/roll=增量旋转角度；dolly=推拉倍率；position/focal/view_up=绝对坐标；reset=True 回默认视角。返回相机状态。
        """
        args = {"view": view, "azimuth": azimuth, "elevation": elevation, "roll": roll,
                "dolly": dolly, "position": position, "focal": focal, "view_up": view_up,
                "view_angle": view_angle, "reset": reset}
        ok, data = call("set_camera", args, tool_name="ssdvr_set_camera")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_set_window_level(wl: Optional[int] = None, ww: Optional[float] = None) -> dict:
        """【3D 渲染页】窗宽窗位：wl=窗位偏移(HU)，ww=窗宽缩放(除以100应用)。至少传一个。

        阅片器的窗宽窗位请用 ssdvr_mpr_set_window_level（MPR 页，单位 HU）。
        """
        if wl is None and ww is None:
            return {"ok": False, "error": "需至少给 wl 或 ww"}
        args: Dict[str, Any] = {}
        if wl is not None:
            args["wl"] = wl
        if ww is not None:
            args["ww"] = ww
        ok, data = call("set_window_level", args, tool_name="ssdvr_set_window_level")
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_set_ssd_threshold(lower: int, upper: int) -> dict:
        """SSD 骨骼层阈值范围（HU）。"""
        ok, data = call("set_ssd_threshold", {"lower": lower, "upper": upper}, tool_name="ssdvr_set_ssd_threshold")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_vr_threshold(lower: int, upper: int) -> dict:
        """VR 软组织层阈值范围（HU）。"""
        ok, data = call("set_vr_threshold", {"lower": lower, "upper": upper}, tool_name="ssdvr_set_vr_threshold")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_get_thresholds() -> dict:
        """读取当前 SSD/VR 阈值。"""
        ok, data = call("get_thresholds", {}, tool_name="ssdvr_get_thresholds")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_cr_params(mc_quality: Optional[float] = None, scatter_blend: Optional[float] = None,
                            scatter_g: Optional[float] = None, er_exposure: Optional[float] = None,
                            cr_denoise: Optional[float] = None) -> dict:
        """CR 渲染参数（路径追踪采样/散射/曝光/降噪）。"""
        args = {k: v for k, v in {
            "mc_quality": mc_quality, "scatter_blend": scatter_blend, "scatter_g": scatter_g,
            "er_exposure": er_exposure, "cr_denoise": cr_denoise,
        }.items() if v is not None}
        ok, data = call("set_cr_params", args, tool_name="ssdvr_set_cr_params")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_preprocess(denoise: Optional[str] = None, use_clahe: Optional[bool] = None,
                             use_frangi: Optional[bool] = None, cpu_render: Optional[bool] = None,
                             vram_threshold_gb: Optional[float] = None) -> dict:
        """预处理选项（gaussian/nlm 去噪、CLAHE、Frangi、CPU 渲染、显存阈值）。改后需重新加载。"""
        args = {k: v for k, v in {
            "denoise": denoise, "use_clahe": use_clahe, "use_frangi": use_frangi,
            "cpu_render": cpu_render, "vram_threshold_gb": vram_threshold_gb,
        }.items() if v is not None}
        ok, data = call("set_preprocess", args, tool_name="ssdvr_set_preprocess")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_crop(enabled: bool = True) -> dict:
        """启用/禁用裁剪框。"""
        ok, data = call("set_crop", {"enabled": enabled}, tool_name="ssdvr_set_crop")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_toggle_background() -> dict:
        """切换渲染背景（暗色酒红 ↔ 亮色）。返回 {bg_is_white}。"""
        ok, data = call("toggle_background", {}, tool_name="ssdvr_toggle_background")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_trigger_roi(task: Optional[str] = None, fast: bool = True) -> dict:
        """【已废弃】自动 ROI 已从本程序移除（TotalSegmentator / SynthSeg / nnU-Net 全部删除）。

        现在的分割是「MPR 阅片」页的 **3D SAM**（交互式）：在 MPR 上点选目标后调
        ssdvr_sam_run（或本工具传 task="sam"）。传其它 task 只会返回 deprecated 说明。
        """
        ok, data = call("trigger_roi", {"task": task, "fast": fast}, timeout=15.0,
                        tool_name="ssdvr_trigger_roi")
        if not ok:
            return {"ok": False, **data}
        return {"ok": True, **data}

    @mcp.tool()
    def ssdvr_list_roi_blocks() -> dict:
        """列出「已保存 ROI（3D SAM）」。字段兼容旧名：label_id(=index)/name/volume_cm3/voxel_count/mean_hu/has_mask。

        旧的自动ROI 解剖结构库（TotalSegmentator 等）已随该功能移除。
        """
        ok, data = call("list_roi_blocks", {}, tool_name="ssdvr_list_roi_blocks")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_render_roi_label(label_id: Optional[int] = None) -> dict:
        """叠加高亮一条已保存 ROI（label_id = ssdvr_list_roi_blocks 里的 index）；0/省略 = 清除高亮。"""
        ok, data = call("render_roi_label", {"label_id": label_id}, timeout=60.0,
                        tool_name="ssdvr_render_roi_label")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_roi_cancel() -> dict:
        """取消进行中的 ROI 分割。"""
        ok, data = call("roi_cancel", {}, tool_name="ssdvr_roi_cancel")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_roi_clear() -> dict:
        """清空 ROI 分割结果并恢复原始 VR。"""
        ok, data = call("roi_clear", {}, tool_name="ssdvr_roi_clear")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_custom_roi(mask_path: str = None, mask_b64: str = None, bbox: dict = None,
                             name: str = "梗死区", label_id: int = 99001) -> dict:
        """注入自定义 ROI 掩膜并高亮渲染（用于 ASPECTS 梗死区标记）。
        二选一传掩膜：mask_path=本地 .npy 文件路径（推荐），或 mask_b64=base64。
        bbox: {"z":[z0,z1],"y":[y0,y1],"x":[x0,x1]} 为掩膜在原图中的位置。"""
        args = {"bbox": bbox or {}, "name": name, "label_id": label_id}
        if mask_path: args["mask_path"] = mask_path
        if mask_b64: args["mask_b64"] = mask_b64
        ok, data = call("set_custom_roi", args, tool_name="ssdvr_set_custom_roi")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_set_roi_weight_path(path: Optional[str] = None) -> dict:
        """【已废弃】TotalSegmentator/nnU-Net 权重目录随自动ROI 功能一并从程序移除。"""
        ok, data = call("set_roi_weight_path", {"path": path}, tool_name="ssdvr_set_roi_weight_path")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_list_presets() -> dict:
        """列出可用 Slicer 渲染模板。"""
        ok, data = call("list_presets", {}, tool_name="ssdvr_list_presets")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_apply_preset(name: Optional[str] = None) -> dict:
        """应用 Slicer 渲染模板（透明度/颜色/光照）。"""
        ok, data = call("apply_preset", {"name": name}, tool_name="ssdvr_apply_preset")
        return {"ok": ok, **data}

    @mcp.tool()
    def ssdvr_get_render_params() -> dict:
        """读取当前渲染参数快照。"""
        ok, data = call("get_render_params", {}, tool_name="ssdvr_get_render_params")
        return {"ok": ok, **data}
