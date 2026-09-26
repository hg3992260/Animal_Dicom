"""运行在 GUI 进程内：TCP server + 主线程调度。

把 MCP server 发来的 op 请求投递到 Qt 主线程执行（QueuedConnection），
并把 GUI 主动事件（load_start/load_done/progress/roi_done/error/...）推回客户端。

协议: TCP line-delimited JSON（见 bridge_client.py）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

try:  # 发现文件（跨进程定位实际端口）；缺失时降级为"不发布"
    from mcp_ssd_vr import bridge_registry as registry
except Exception:  # noqa: BLE001
    class _NoRegistry:  # type: ignore[no-redef]
        @staticmethod
        def write_bridge_file(*_a, **_k):
            return ""

        @staticmethod
        def remove_bridge_file(*_a, **_k):
            return None

    registry = _NoRegistry()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 状态采集 / 截图（在主线程内执行，直接访问 ViewerWindow）
# ---------------------------------------------------------------------------

def _num(w) -> Optional[int]:
    """从多种控件形态取整数（QSlider.value / PyCt6.slider().value / SpinBox.value）。"""
    if w is None:
        return None
    for getter in (lambda: w.value(), lambda: w.slider().value(),
                   lambda: w.spin_box().value()):
        try:
            return int(getter())
        except Exception:
            continue
    return None


def _set_num(w, v) -> bool:
    """把整数值写进上面三种控件形态之一；成功返回 True。"""
    if w is None:
        return False
    for setter in (lambda: w.setValue(int(v)), lambda: w.slider().setValue(int(v)),
                   lambda: w.spin_box().setValue(int(v))):
        try:
            setter()
            return True
        except Exception:
            continue
    return False


def _txt(w, default: str = "") -> str:
    """从 QLineEdit/QLabel/QTextEdit 或 PyCt6(C...Edit/C...Label) 取文本。"""
    if w is None:
        return default
    for getter in (lambda: w.text(), lambda: w.toPlainText(), lambda: w.currentText(),
                   lambda: w.line_edit().text(), lambda: w.combo_box().currentText()):
        try:
            return str(getter() or "")
        except Exception:
            continue
    return default


def _tab_state(win) -> Dict[str, Any]:
    """页签列表 / 当前页（替代硬编码下标）。"""
    out = {"tabs": [], "tab_count": 0, "current_tab": "", "current_tab_index": -1}
    pages = getattr(win, "pages", None)
    if pages is None:
        return out
    try:
        titles = [pages.tabText(i) for i in range(pages.count())]
        idx = int(pages.currentIndex())
        out.update({"tabs": titles, "tab_count": len(titles), "current_tab_index": idx,
                    "current_tab": titles[idx] if 0 <= idx < len(titles) else ""})
    except Exception:
        pass
    return out


def _right_area_visible(win) -> Optional[bool]:
    box = getattr(win, "right_box", None)
    if box is None:
        return None
    try:
        return not bool(box.isHidden())
    except Exception:
        return None


def _saved_roi_records(win) -> List[Dict[str, Any]]:
    """「已保存 ROI（3D SAM）」记录（roi_summary_3dsam.csv）。"""
    rows = getattr(win, "_sam_roi_cache", None)
    if rows is None:
        try:
            rows = win._sam_roi_load_records()
        except Exception:
            rows = []
    keys = ("time", "name", "volume_cm3", "voxels", "mean_hu", "std_hu",
            "min_hu", "max_hu", "source", "note", "pos_points", "neg_points")
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        rec = {k: r.get(k) for k in keys}
        npy = str(r.get("mask_npy") or "")
        rec["index"] = i
        rec["mask_npy"] = npy
        rec["has_mask"] = bool(npy and os.path.exists(npy))
        out.append(rec)
    return out


def _mpr_state(win) -> Dict[str, Any]:
    """MPR 阅片页：窗宽窗位 / 测量工具 / 提示点 / SAM 结果。"""
    mask = getattr(win, "sam_mask", None)
    voxels = None
    if mask is not None:
        try:
            voxels = int(mask.sum())
        except Exception:
            voxels = None
    tools = getattr(win, "sam_tool_names", None) or {}
    tool = getattr(win, "sam_tool", "none")
    measures = getattr(win, "sam_measures", None) or {}
    wl = _num(getattr(win, "sam_wl_slider", None))
    ww = _num(getattr(win, "sam_ww_slider", None))
    thr = None
    try:
        thr = float(win.sam_threshold_spin.value())
    except Exception:
        pass
    return {
        "wl": wl if wl is not None else getattr(win, "sam_wl", None),
        "ww": ww if ww is not None else getattr(win, "sam_ww", None),
        "threshold": thr,
        "tool": tool,
        "tool_name": tools.get(tool, tool),
        "tools_available": tools,
        "pos_points": len(getattr(win, "sam_pos_pts", None) or []),
        "neg_points": len(getattr(win, "sam_neg_pts", None) or []),
        "mask_present": mask is not None,
        "mask_voxels": voxels,
        "measures": {str(k): len(v or []) for k, v in measures.items()},
        "measures_total": sum(len(v or []) for v in measures.values()),
        "expanded_axis": getattr(win, "sam_expanded_axis", None),
        "view_names": getattr(win, "sam_view_names", None),
        "busy": bool(getattr(win, "sam_busy", False)),
        "pending": bool(getattr(win, "sam_pending", False)),
        "model_loaded": getattr(win, "sam_session", None) is not None,
        "model_status": _txt(getattr(win, "sam_model_status", None)),
        "progress": _txt(getattr(win, "sam_progress_lb", None)),
    }


def _archive_state(win) -> Dict[str, Any]:
    grid = getattr(win, "arch_grid", None)
    count = None
    selected = None
    try:
        count = int(grid.count())
        cur = grid.currentItem()
        selected = cur.text() if cur is not None else None
    except Exception:
        pass
    root = ""
    try:
        root = win._archive_root()
    except Exception:
        pass
    rs = getattr(win, "right_stack", None)
    return {
        "root": root,
        "count": count,
        "selected": selected,
        "detail_dir": str(getattr(win, "_arch_detail_dir", "") or ""),
        "right_stack_index": int(rs.currentIndex()) if rs is not None else None,
        "status": _txt(getattr(win, "arch_status", None)),
    }


def _arch_detail_summary(win) -> Dict[str, Any]:
    """当前打开的病例详情页摘要（meta.json 的关键计数）。"""
    meta = getattr(win, "_arch_detail_meta", None) or {}
    case = meta.get("case", {}) if isinstance(meta, dict) else {}
    return {
        "dir": str(getattr(win, "_arch_detail_dir", "") or ""),
        "id": meta.get("id") if isinstance(meta, dict) else None,
        "name": meta.get("name") if isinstance(meta, dict) else None,
        "is_demo": bool(meta.get("demo")) if isinstance(meta, dict) else False,
        "case_filled": (sum(1 for v in case.values() if str(v or "").strip())
                        if isinstance(case, dict) else 0),
        "revisions": len(meta.get("revisions") or []) if isinstance(meta, dict) else 0,
        "shots": len(meta.get("shots") or []) if isinstance(meta, dict) else 0,
        "ai_log": len(meta.get("ai_log") or []) if isinstance(meta, dict) else 0,
    }


def _settings_state(win, include_key: bool = False) -> Dict[str, Any]:
    """设置页 / DeepSeek 配置。默认**不回传 API Key 明文**。"""
    cfg: Dict[str, Any] = {}
    try:
        cfg = win._ai_config() or {}
    except Exception:
        cfg = {}
    key = str(cfg.get("api_key") or "")
    out: Dict[str, Any] = {
        "config_path": "",
        "key_set": bool(key.strip()),
        "key_hint": ("已配置 %s…" % key[:6]) if key.strip() else "",
        "base_url": str(cfg.get("base_url") or ""),
        "model": str(cfg.get("model") or ""),
        "temperature": cfg.get("temperature"),
        "status": _txt(getattr(win, "set_status", None)),
    }
    try:
        out["config_path"] = win._ai_config_path()
    except Exception:
        pass
    if include_key:
        out["api_key"] = key
    return out


def _case_state(win) -> Dict[str, Any]:
    fields = getattr(win, "case_fields", None) or {}
    path = ""
    try:
        path = win._case_template_path()
    except Exception:
        pass
    filled = 0
    try:
        filled = sum(1 for v in (win._case_form_values() or {}).values() if str(v or "").strip())
    except Exception:
        pass
    return {"template_path": path, "field_count": len(fields), "filled": filled,
            "fields": sorted(fields.keys())}


def _ai_state(win) -> Dict[str, Any]:
    cfg = _settings_state(win)
    out = ""
    try:
        out = win.ai_out.toPlainText() or ""
    except Exception:
        out = ""
    autosave = False
    try:
        autosave = bool(win.ai_autosave.isChecked())
    except Exception:
        pass
    return {"key_set": cfg.get("key_set"), "model": cfg.get("model"), "autosave": autosave,
            "status": _txt(getattr(win, "ai_status_lb", None)),
            "output_chars": len(out), "output_tail": out[-400:] if out else ""}


def _pacs_state(win) -> Dict[str, Any]:
    tree = getattr(win, "pacs_tree", None)
    studies = series = 0
    try:
        studies = int(tree.topLevelItemCount())
        for i in range(studies):
            series += int(tree.topLevelItem(i).childCount())
    except Exception:
        pass
    busy = False
    try:
        busy = bool(win._pacs_busy())
    except Exception:
        pass
    stack = getattr(win, "pacs_stack", None)
    return {
        "node": _txt(getattr(win, "pacs_name", None)),
        "host": _txt(getattr(win, "pacs_host", None)),
        "port": _num(getattr(win, "pacs_port", None)),
        "called_ae": _txt(getattr(win, "pacs_called", None)),
        "status": _txt(getattr(win, "pacs_status", None)),
        "studies": studies,
        "series": series,
        "busy": busy,
        "last_dir": str(getattr(win, "_pacs_last_dir", "") or ""),
        "sub_page": int(stack.currentIndex()) if stack is not None else None,
    }


def _trial_state() -> Dict[str, Any]:
    """运行期限状态（读 GUI 进程内的 license_guard 快照，无磁盘 IO）。"""
    try:
        import license_guard
        return license_guard.snapshot()
    except Exception as e:  # noqa: BLE001
        return {"ok": None, "status": "unknown", "error": str(e)}


def _pets_state(win) -> Dict[str, Any]:
    tree = getattr(win, "pat_tree", None)
    studies = series = 0
    try:
        studies = int(tree.topLevelItemCount())
        for i in range(studies):
            series += int(tree.topLevelItem(i).childCount())
    except Exception:
        pass
    root = ""
    try:
        root = win.pat_root_edit.line_edit().text().strip()
    except Exception:
        pass
    stack = getattr(win, "patient_stack", None)
    return {"root": root, "studies": studies, "series": series,
            "status": _txt(getattr(win, "pat_status", None)),
            "pacs_page": (int(stack.currentIndex()) == 1) if stack is not None else None}


def collect_state(win) -> Dict[str, Any]:
    image_data = getattr(win, "image_data", None)
    loaded = image_data is not None
    dims = None
    spacing = None
    if loaded:
        try:
            dims = list(int(v) for v in image_data.GetDimensions())
        except Exception:
            dims = None
        try:
            spacing = list(float(v) for v in image_data.GetSpacing())
        except Exception:
            spacing = None

    rw = getattr(win, "render_window", None)
    rw_size = None
    if rw is not None:
        try:
            rw_size = list(int(v) for v in rw.GetSize())
        except Exception:
            rw_size = None

    saved_rois = _saved_roi_records(win)
    tabs = _tab_state(win)
    splitter_sizes = None
    try:
        splitter_sizes = [int(v) for v in win.splitter.sizes()]
    except Exception:
        pass

    def slider_val(name: str, default: float = 0.0) -> float:
        obj = getattr(win, name, None)
        try:
            return float(obj.slider().value()) / 100.0
        except Exception:
            return default

    def raw_val(name: str, default: int = 0) -> int:
        obj = getattr(win, name, None)
        try:
            return int(obj.slider().value())
        except Exception:
            return default

    return {
        "dicom_loaded": loaded,
        "dicom_path": _path_text(win),
        "dims": dims,
        "spacing": spacing,
        "load_busy": bool(getattr(win, "load_busy", False)),
        "render_window_size": rw_size,
        "render_mode": getattr(win, "render_mode", "stable"),
        "ssd_scale": slider_val("ssd_slider"),
        "vr_scale": slider_val("vr_slider"),
        "wl_offset": raw_val("wl_slider"),
        "ww_scale": raw_val("ww_slider", 100) / 100.0,
        # ---- 页面/环境 ----
        "tabs": tabs["tabs"],
        "tab_count": tabs["tab_count"],
        "current_tab": tabs["current_tab"],
        "current_tab_index": tabs["current_tab_index"],
        "right_area_visible": _right_area_visible(win),
        "splitter_sizes": splitter_sizes,
        "modality": getattr(win, "modality", None),
        "is_2d": bool(getattr(win, "is_2d", False)),
        # ---- 各子系统 ----
        "mpr": _mpr_state(win),
        "roi_saved": {"count": len(saved_rois), "records": saved_rois},
        "archive": _archive_state(win),
        "settings": _settings_state(win),
        "pacs": _pacs_state(win),
        "case": _case_state(win),
        "ai": _ai_state(win),
        "pets": _pets_state(win),
        "trial": _trial_state(),
        # ---- 旧字段（自动ROI 已移除 → 映射到 3D SAM / 已保存 ROI）----
        "roi_task": "sam3d",
        "roi_running": bool(getattr(win, "sam_busy", False)),
        "roi_results_count": len(saved_rois),
        "legacy_auto_roi_removed": True,
        "last_error": getattr(win, "last_error", None),
    }


def _path_text(win) -> str:
    pe = getattr(win, "path_edit", None)
    if pe is None:
        return ""
    try:
        le = pe.line_edit()
        return le.text().strip()
    except Exception:
        return ""


def take_screenshot(win, out_dir: Optional[str] = None,
                    switch_to_render: bool = False) -> Dict[str, Any]:
    """截取 VTK 渲染窗口。

    switch_to_render=True 时先切到「渲染」页——右侧渲染区在爱宠列表/MPR/设置
    页是隐藏的（此时 VTK 窗口只有几十像素高，截出来是空白）。
    """
    import numpy as np
    from PIL import Image
    from vtkmodules.util import numpy_support as vtk_np

    rw = getattr(win, "render_window", None)
    if rw is None:
        return {"ok": False, "error": "no render window"}

    if switch_to_render:
        pages = getattr(win, "pages", None)
        if pages is not None:
            try:
                for i in range(pages.count()):
                    if "渲染" in pages.tabText(i):
                        pages.setCurrentIndex(i)
                        break
            except Exception:
                pass
        app = QtWidgets.QApplication.instance()
        if app is not None:
            for _ in range(3):
                app.processEvents()

    vtk_visible = None
    try:
        w = getattr(win, "vtk_widget", None)
        vtk_visible = bool(w is not None and w.isVisible())
    except Exception:
        vtk_visible = None

    rw.Render()
    w2i = _import_window2image()
    w2i.SetInput(rw)
    w2i.SetInputBufferTypeToRGBA()
    w2i.ReadFrontBufferOff()
    w2i.Update()
    vtk_img = w2i.GetOutput()
    w, h, _ = vtk_img.GetDimensions()
    scalars = vtk_img.GetPointData().GetScalars()
    arr = vtk_np.vtk_to_numpy(scalars)
    arr = arr.reshape((h, w, -1))
    rgba = arr[..., :4] if arr.shape[-1] >= 4 else np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    rgba = np.flipud(rgba)

    img = Image.fromarray(rgba, mode="RGBA").convert("RGB")
    out_dir = out_dir or ""
    if out_dir:
        import os
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"shot_{int(time.time()*1000)}.png")
    else:
        path = ""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    if path:
        with open(path, "wb") as f:
            f.write(png_bytes)

    rgb = np.asarray(img)
    mean = float(rgb.mean())
    bright = float((rgb.max(axis=2) > 50).mean() * 100.0)
    uniq = len(np.unique(rgb.reshape(-1, 3), axis=0))

    return {
        "ok": True,
        "path": path,
        "bytes": len(png_bytes),
        "png_base64": base64.b64encode(png_bytes).decode("ascii"),
        "vtk_visible": vtk_visible,
        "stats": {"mean": round(mean, 2), "bright_pct": round(bright, 2),
                  "unique_colors": int(uniq)},
        "hint": ("" if vtk_visible is not False else
                 "渲染区当前页是隐藏的（爱宠列表/MPR/设置页）：换到「渲染」页，"
                 "或用 switch_to_render=true 重试"),
    }


def take_window_screenshot(win, out_dir: Optional[str] = None,
                           tab: Optional[str] = None) -> Dict[str, Any]:
    """整窗截图（审阅非 VTK 页面：爱宠列表 / 档案中心 / 设置 / MPR 控件）。

    tab 给页签标题子串时可先切页再截图（如 tab="设置"）。
    """
    if tab:
        pages = getattr(win, "pages", None)
        if pages is not None:
            try:
                for i in range(pages.count()):
                    if str(tab) in pages.tabText(i):
                        pages.setCurrentIndex(i)
                        break
            except Exception:
                pass
    app = QtWidgets.QApplication.instance()
    if app is not None:
        for _ in range(3):
            app.processEvents()
    try:
        pm = win.grab()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"整窗截图失败: {e}"}
    path = ""
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"window_{int(time.time() * 1000)}.png")
    if path:
        try:
            if not pm.save(path, "PNG"):
                return {"ok": False, "error": f"保存 PNG 失败: {path}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"保存 PNG 异常: {e}"}
    stats: Dict[str, Any] = {}
    try:
        import numpy as np
        img = pm.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB32)
        w, h = img.width(), img.height()
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(h, w, 4)[..., :3]
        stats = {"mean": round(float(arr.mean()), 2),
                 "bright_pct": round(float((arr.max(axis=2) > 50).mean() * 100.0), 2),
                 "unique_colors": int(len(np.unique(arr.reshape(-1, 3), axis=0)))}
    except Exception:
        stats = {}
    return {"ok": True, "path": path,
            "bytes": os.path.getsize(path) if path and os.path.exists(path) else None,
            "size": [int(pm.width()), int(pm.height())],
            "tab": _tab_state(win)["current_tab"], "stats": stats}


def _import_window2image():
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
    return vtkWindowToImageFilter()


# ---------------------------------------------------------------------------
# 跨线程调度器（主线程执行 op）
# ---------------------------------------------------------------------------

class _NoDialogs:
    """在 MCP 调用期间把模态对话框变成"自动确认/无人应答"，避免阻塞主线程。

    程序里有若干 QMessageBox（删除确认、无 mask 提示、保存失败告警）会在主线程
    弹模态框：桥的 op 是投递到主线程执行的，一旦弹框就会卡住整个桥（超时）。
    GUI 自身只用 `_mcp_mode` 关掉了部分对话框；这里统一兜底
    （question → Yes，information/warning/critical → Ok）。
    """

    def __enter__(self):
        self._box = QtWidgets.QMessageBox
        self._saved: Dict[str, Any] = {}
        ret_map = {
            "information": QtWidgets.QMessageBox.StandardButton.Ok,
            "warning": QtWidgets.QMessageBox.StandardButton.Ok,
            "critical": QtWidgets.QMessageBox.StandardButton.Ok,
            "about": None,
            "question": QtWidgets.QMessageBox.StandardButton.Yes,
        }
        for name, ret in ret_map.items():
            orig = getattr(self._box, name, None)
            if orig is None:
                continue
            self._saved[name] = orig
            if ret is None:
                repl = staticmethod(lambda *a, **k: None)
            else:
                repl = staticmethod(lambda *a, _r=ret, **k: _r)
            try:
                setattr(self._box, name, repl)
            except Exception:
                self._saved.pop(name, None)
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            try:
                setattr(self._box, name, fn)
            except Exception:
                pass
        return False


class _Dispatcher(QtCore.QObject):
    _cmd = QtCore.Signal(object)

    def __init__(self, win, bridge) -> None:
        super().__init__()
        self.win = win
        self.bridge = bridge
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._plock = threading.Lock()
        self._cmd.connect(self._on_cmd, QtCore.Qt.ConnectionType.QueuedConnection)

    def submit(self, req_id: str, op: str, args: Dict[str, Any], timeout: float) -> Tuple[bool, Any]:
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        with self._plock:
            self._pending[req_id] = {"evt": evt, "holder": holder}
        self._cmd.emit((req_id, op, args))
        if not evt.wait(timeout):
            with self._plock:
                self._pending.pop(req_id, None)
            return False, {"error": f"op '{op}' 主线程执行超时 ({timeout}s)"}
        return holder.get("ok", False), holder.get("data", {})

    @QtCore.Slot(object)
    def _on_cmd(self, payload) -> None:
        req_id, op, args = payload
        ok = False
        data: Any = {}
        try:
            data = self._execute(op, args)
            ok = True
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            data = {"error": f"{type(e).__name__}: {e}"}
            ok = False
            try:
                self.win.last_error = data["error"]
                self.bridge.push_event("error", {"error": data["error"], "op": op})
            except Exception:
                pass
        with self._plock:
            item = self._pending.pop(req_id, None)
        if item is not None:
            item["holder"]["ok"] = ok
            item["holder"]["data"] = data
            item["evt"].set()

    # ---- op 实现 ----
    def _execute(self, op: str, args: Dict[str, Any]) -> Any:
        win = self.win
        if op == "query_state":
            return collect_state(win)
        if op == "screenshot":
            return take_screenshot(win, out_dir=args.get("out_dir"),
                                   switch_to_render=bool(args.get("switch_to_render")))
        if op == "screenshot_window":
            return take_window_screenshot(win, out_dir=args.get("out_dir"), tab=args.get("tab"))
        # ---- 页面 / 新 UI 子系统 ----
        if op == "list_tabs":
            return {"ok": True, **_tab_state(win)}
        if op == "get_ui_state":
            return {"ok": True, "tabs": _tab_state(win),
                    "right_area_visible": _right_area_visible(win),
                    "mpr": _mpr_state(win), "archive": _archive_state(win),
                    "settings": _settings_state(win), "pacs": _pacs_state(win),
                    "case": _case_state(win), "ai": _ai_state(win), "pets": _pets_state(win)}
        if op == "switch_tab":
            return self._switch_tab(args)
        if op == "mpr_set_window_level":
            return self._mpr_set_window_level(args)
        if op == "mpr_set_tool":
            return self._mpr_set_tool(args)
        if op == "mpr_clear_measures":
            return self._invoke("_sam_meas_clear", "mpr_clear_measures")
        if op == "mpr_clear_points":
            return self._invoke("_sam_clear_points", "mpr_clear_points")
        if op == "mpr_clear_result":
            return self._invoke("_sam_clear_result", "mpr_clear_result")
        if op == "sam_run":
            return self._sam_run(args)
        if op == "sam_list_rois":
            recs = _saved_roi_records(win)
            return {"ok": True, "count": len(recs), "records": recs}
        if op == "sam_load_roi":
            return self._sam_roi_action(args, "load")
        if op == "sam_delete_roi":
            return self._sam_roi_action(args, "delete")
        if op == "archive_refresh":
            return self._archive_refresh()
        if op == "archive_list":
            return self._archive_list()
        if op == "archive_open":
            return self._archive_open(args)
        if op == "archive_back":
            return self._invoke("_arch_detail_back", "archive_back")
        if op == "archive_save_shot":
            return self._archive_save_shot(args)
        if op == "settings_get":
            return {"ok": True, **_settings_state(win, include_key=bool(args.get("include_key")))}
        if op == "settings_set":
            return self._settings_set(args)
        if op == "case_get":
            return {"ok": True, "values": self._case_values(), **_case_state(win)}
        if op == "case_set":
            return self._case_set(args)
        if op == "pets_get_state":
            return {"ok": True, **_pets_state(win)}
        if op == "pets_scan":
            return self._pets_scan(args)
        if op == "pacs_get_state":
            return {"ok": True, **_pacs_state(win)}
        if op == "pacs_echo":
            return self._pacs_op("echo", args)
        if op == "pacs_find_studies":
            return self._pacs_op("studies", args)
        if op == "pacs_retrieve":
            return self._pacs_op("retrieve", args)
        if op == "ai_get_state":
            return {"ok": True, **_ai_state(win)}
        if op == "sam_probe":
            return self._sam_probe()
        if op == "trial_status":
            return {"ok": True, "trial": _trial_state()}
        if op == "ai_run":
            return self._ai_run(args)
        if op == "load_dicom":
            return self._load_dicom(args)
        if op == "set_mode":
            return self._set_mode(args)
        if op == "set_opacity":
            return self._set_opacity(args)
        if op == "set_camera":
            return self._set_camera(args)
        if op == "set_window_level":
            return self._set_window_level(args)
        if op == "set_ssd_threshold":
            return self._set_threshold("ssd", args)
        if op == "set_vr_threshold":
            return self._set_threshold("vr", args)
        if op == "get_thresholds":
            return self._get_thresholds()
        if op == "set_cr_params":
            return self._set_cr_params(args)
        if op == "set_preprocess":
            return self._set_preprocess(args)
        if op == "set_crop":
            return self._set_crop(args)
        if op == "toggle_background":
            win.toggle_background()
            return {"bg_is_white": bool(getattr(win, "bg_is_white", False))}
        if op == "trigger_roi":
            return self._trigger_roi(args)
        if op == "list_roi_blocks":
            return self._list_roi_blocks()
        if op == "render_roi_label":
            return self._render_roi_label(args)
        if op == "render_roi_labels":
            return self._render_roi_labels(args)
        if op == "set_custom_roi":
            return self._set_custom_roi(args)
        if op == "roi_cancel":
            # 自动ROI 已移除：cancel = 清掉当前 3D SAM 结果
            fn = getattr(win, "_sam_clear_result", None)
            if fn is None:
                return {"ok": False, "deprecated": True,
                        "error": "自动ROI 已移除；本程序无进行中的分割可取消"}
            fn()
            return {"ok": True, "action": "sam_clear_result", "deprecated": True,
                    "note": "自动ROI 已移除，此 op 现等价于清空 3D SAM 结果"}
        if op == "roi_clear":
            called = []
            for _m in ("_sam_clear_points", "_sam_clear_result"):
                _fn = getattr(win, _m, None)
                if _fn is not None:
                    _fn()
                    called.append(_m)
            if not called:
                return {"ok": False, "deprecated": True,
                        "error": "自动ROI 已移除；未找到 3D SAM 清空方法"}
            return {"ok": True, "action": "sam_clear", "called": called, "deprecated": True}
        if op == "set_roi_weight_path":
            return self._set_roi_weight_path(args)
        if op == "list_presets":
            return self._list_presets()
        if op == "apply_preset":
            return self._apply_preset(args)
        if op == "get_render_params":
            return self._get_render_params()
        if op == "shutdown":
            return self._shutdown()
        raise ValueError(f"unknown op: {op}")

    # ---- 具体实现 ----
    def _load_dicom(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        if getattr(win, "load_busy", False):
            win.last_error = "load in progress"
            self.bridge.push_event("error", {"error": "load in progress: busy"})
            return {"ok": False, "error": "load in progress", "load_busy": True}
        path = args.get("path") or ""
        if not path:
            return {"ok": False, "error": "no path"}
        pe = getattr(win, "path_edit", None)
        if pe is not None:
            try:
                pe.line_edit().setText(path)
            except Exception:
                pass
        # 异步触发加载，避免阻塞主线程/桥
        QtCore.QTimer.singleShot(0, win.load_dicom)
        return {"ok": True, "accepted": True, "path": path}

    def _set_mode(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from . import state
        mode = args.get("mode") or "stable"
        win = self.win
        try:
            idx = state.mode_index(mode)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        combo = getattr(win, "mode_combo", None)
        try:
            if combo is not None:
                combo.combo_box().setCurrentIndex(idx)
        except Exception:
            pass
        win.on_mode_change(idx)
        return {"ok": True, "mode": getattr(win, "render_mode", mode), "requested": mode}

    def _set_opacity(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        if args.get("ssd") is not None:
            self._set_slider(win, "ssd_slider", float(args["ssd"]))
        if args.get("vr") is not None:
            self._set_slider(win, "vr_slider", float(args["vr"]))
        win.on_slider_change(0)
        return {"ok": True, "ssd_scale": self._slider_scale(win, "ssd_slider"),
                "vr_scale": self._slider_scale(win, "vr_slider")}

    def _set_camera(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        renderer = getattr(win, "renderer", None)
        if renderer is None:
            return {"ok": False, "error": "no renderer"}
        cam = renderer.GetActiveCamera()
        if args.get("reset"):
            renderer.ResetCamera()
        view = args.get("view")
        if view:
            self._apply_view(renderer, cam, view)
        az = float(args.get("azimuth", 0.0))
        el = float(args.get("elevation", 0.0))
        roll = float(args.get("roll", 0.0))
        if az:
            cam.Azimuth(az)
        if el:
            cam.Elevation(el)
        if roll:
            cam.Roll(roll)
        dolly = float(args.get("dolly", 1.0))
        if dolly != 1.0:
            cam.Dolly(dolly)
        pos = args.get("position")
        focal = args.get("focal")
        up = args.get("view_up")
        if pos and len(pos) == 3:
            cam.SetPosition(*[float(v) for v in pos])
        if focal and len(focal) == 3:
            cam.SetFocalPoint(*[float(v) for v in focal])
        if up and len(up) == 3:
            cam.SetViewUp(*[float(v) for v in up])
        va = args.get("view_angle")
        if va is not None:
            cam.SetViewAngle(float(va))
        renderer.ResetCameraClippingRange()
        if getattr(win, "render_window", None) is not None:
            win.render_window.Render()
        return self._camera_state(cam)

    def _apply_view(self, renderer, cam, view: str) -> None:
        renderer.ResetCamera()
        fp = cam.GetFocalPoint()
        pos = cam.GetPosition()
        import math
        d = math.sqrt(sum((pos[i] - fp[i]) ** 2 for i in range(3))) or 1.0
        views = {
            "coronal": ((0, -d, 0), (0, 0, 1)),
            "coronal_rear": ((0, d, 0), (0, 0, 1)),
            "sagittal": ((d, 0, 0), (0, 0, 1)),
            "sagittal_rear": ((-d, 0, 0), (0, 0, 1)),
            "axial": ((0, 0, d), (0, 1, 0)),
            "axial_rear": ((0, 0, -d), (0, 1, 0)),
            "three_quarter": ((d * 0.7, -d * 0.7, d * 0.7), (0, 0, 1)),
            "front_top": ((0, -d * 0.6, d * 0.8), (0, 0, 1)),
        }
        if view in views:
            off, up = views[view]
            cam.SetPosition(fp[0] + off[0], fp[1] + off[1], fp[2] + off[2])
            cam.SetViewUp(*up)

    def _set_window_level(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        if args.get("wl") is not None:
            self._set_slider(win, "wl_slider", float(args["wl"]))
        if args.get("ww") is not None:
            self._set_slider(win, "ww_slider", float(args["ww"]) * 100.0)
        win.on_ww_wl_change()
        return {"ok": True, "wl_offset": self._raw_val(win, "wl_slider"),
                "ww_scale": self._raw_val(win, "ww_slider", 100) / 100.0}

    def _set_threshold(self, which: str, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        lower = args.get("lower")
        upper = args.get("upper")
        if lower is None or upper is None:
            return {"ok": False, "error": "need lower+upper"}
        slider = getattr(win, f"{which}_threshold_slider", None)
        if slider is None:
            return {"ok": False, "error": f"no {which} threshold slider"}
        try:
            slider.setValues(int(lower), int(upper))
        except Exception:
            try:
                slider.setValues(float(lower), float(upper))
            except Exception:
                return {"ok": False, "error": "setValues failed"}
        if which == "ssd":
            win.on_ssd_threshold_change(int(lower), int(upper))
        else:
            win.on_vr_threshold_change(int(lower), int(upper))
        return {"ok": True, "lower": int(lower), "upper": int(upper)}

    def _get_thresholds(self) -> Dict[str, Any]:
        win = self.win
        out = {}
        for which in ("ssd", "vr"):
            slider = getattr(win, f"{which}_threshold_slider", None)
            if slider is None:
                out[which] = None
                continue
            try:
                l, u = slider.value()
                out[which] = [int(l), int(u)]
            except Exception:
                try:
                    out[which] = [int(slider._lower), int(slider._upper)]
                except Exception:
                    out[which] = None
        return out

    def _set_cr_params(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        for key in ("mc_quality", "scatter_blend", "scatter_g", "er_exposure", "cr_denoise",
                    "step_factor_primary", "step_factor_shadow"):
            if key in args and hasattr(win, key):
                setattr(win, key, float(args[key]))
        win._apply_cr_runtime_params()
        return {"ok": True}

    def _set_preprocess(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        mapping = {
            "denoise": "denoise_method",
            "use_clahe": "use_clahe",
            "use_frangi": "use_frangi",
            "use_distance_field": "use_distance_field",
            "use_2d_tf": "use_2d_tf",
            "use_2d_tf_bone": "use_2d_tf_bone",
            "cpu_render": "cpu_render",
            "vram_threshold_gb": "vram_threshold_gb",
        }
        for k, attr in mapping.items():
            if k in args:
                setattr(win, attr, args[k])
        return {"ok": True}

    def _set_crop(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        enabled = bool(args.get("enabled", True))
        chk = getattr(win, "check_crop", None)
        if chk is not None:
            chk.setChecked(enabled)
        bw = getattr(win, "box_widget", None)
        if bw is not None:
            if enabled:
                bw.On()
            else:
                bw.Off()
        return {"ok": True, "crop": enabled}

    def _trigger_roi(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """兼容旧接口：task='sam' 时改为触发 3D SAM；其余一律返回已移除说明。

        自动 ROI（TotalSegmentator / SynthSeg / nnU-Net）已从本程序删除，
        现在的分割入口是「MPR 阅片」页的 3D SAM（交互式点选）。
        """
        win = self.win
        if str(args.get("task") or "").lower() in ("sam", "sam3d", "3d", "3dsam"):
            return self._sam_run(args)
        if not hasattr(win, "on_roi_start"):
            return {"ok": False, "deprecated": True,
                    "error": "自动ROI 功能已移除（纯离线阅读器）；请用 MPR 阅片 + 3D SAM 手动分割",
                    "how": "在「MPR 阅片」页左键点目标 → sam_run（或 trigger_roi(task='sam')）"}
        return {"ok": False, "deprecated": True,
                "error": "自动ROI 管线已删除：GUI 仍带 on_roi_start 但依赖已不存在",
                "how": "改用 sam_run"}

    def _detect_modality(self, win) -> str:
        try:
            img = getattr(win, "original_sitk_image", None)
            if img is not None:
                for key in ("0008|0060",):
                    if img.HasMetaDataKey(key):
                        return img.GetMetaData(key)
                for key in img.GetMetaDataKeys():
                    if key.endswith("0008|0060"):
                        return img.GetMetaData(key)
        except Exception:
            pass
        return "CT"

    def _list_roi_blocks(self) -> Dict[str, Any]:
        """已保存 ROI（3D SAM）记录 → 兼容旧 block 字段名。

        旧的「自动ROI 解剖结构库」（roi_results: bones/vessels/tissues）已随该功能删除，
        这里改为读取 roi_summary_3dsam.csv，字段名保持不变以便老调用方继续工作。
        """
        recs = _saved_roi_records(self.win)
        blocks = [{
            "index": r["index"],
            "label_id": r["index"],
            "name": r.get("name") or "",
            "category": r.get("source") or "3D SAM",
            "volume_cm3": r.get("volume_cm3"),
            "voxel_count": r.get("voxels"),
            "mean_hu": r.get("mean_hu"),
            "std_hu": r.get("std_hu"),
            "min_hu": r.get("min_hu"),
            "max_hu": r.get("max_hu"),
            "has_mask": r.get("has_mask"),
            "mask_npy": r.get("mask_npy"),
            "time": r.get("time"),
        } for r in recs]
        return {"ok": True, "count": len(blocks), "blocks": blocks,
                "source": "saved_roi_csv", "legacy_auto_roi_removed": True,
                "note": "来自「已保存 ROI（3D SAM）」；用 sam_load_roi/name 或 render_roi_label(label_id=index) 高亮"}

    def _render_roi_label(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """高亮一条「已保存 ROI」（label_id = 列表下标）；0/None = 清除高亮。"""
        label_id = args.get("label_id")
        if label_id in (None, 0, "0"):
            return self._clear_sam_highlight()
        if getattr(self.win, "sam_roi_table", None) is None:
            return {"ok": False, "deprecated": True,
                    "error": "本程序无自动ROI 结构库；已保存 ROI 列表也不可用"}
        try:
            idx = int(label_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"label_id 非法: {label_id!r}"}
        return self._sam_roi_action({"index": idx}, "load")

    def _render_roi_labels(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """叠加高亮多条「已保存 ROI」（label_ids = 列表下标数组）。"""
        raw = args.get("label_ids") or []
        try:
            ids = [int(x) for x in raw]
        except (TypeError, ValueError):
            return {"ok": False, "error": "label_ids 必须是整数数组"}
        if not ids:
            return self._clear_sam_highlight()
        return self._sam_roi_action({"indexes": ids}, "load")

    def _clear_sam_highlight(self) -> Dict[str, Any]:
        win = self.win
        called = []
        for m in ("_sam_clear_result", "_sam_clear_overlay"):
            fn = getattr(win, m, None)
            if fn is None:
                continue
            try:
                fn()
                called.append(m)
            except Exception:
                pass
        if getattr(win, "render_window", None) is not None:
            try:
                win.render_window.Render()
            except Exception:
                pass
        return {"ok": True, "label_id": 0, "action": "clear", "called": called,
                "mpr": _mpr_state(win)}

    # ==================================================================
    # 新 UI：页签 / MPR / 3D SAM / 档案中心 / 设置 / 病例 / PACS / AI
    # ==================================================================

    def _invoke(self, method: str, tag: str) -> Dict[str, Any]:
        """调用 GUI 上无对话框的简单方法。"""
        fn = getattr(self.win, method, None)
        if fn is None:
            return {"ok": False, "error": f"GUI 缺少 {method}()（版本不匹配？）"}
        try:
            with _NoDialogs():
                fn()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{method}() 失败: {e}"}
        return {"ok": True, "action": tag}

    def _switch_tab(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        pages = getattr(win, "pages", None)
        if pages is None:
            return {"ok": False, "error": "GUI 无 pages"}
        title = str(args.get("title") or "").strip()
        index = args.get("index")
        if title:
            index = -1
            for i in range(pages.count()):
                if title in pages.tabText(i):
                    index = i
                    break
            if index < 0:
                return {"ok": False, "error": f"未找到页签: {title}",
                        "tabs": _tab_state(win)["tabs"]}
        if index is None:
            return {"ok": False, "error": "需要 title 或 index"}
        try:
            index = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"index 非法: {args.get('index')!r}"}
        if not 0 <= index < pages.count():
            return {"ok": False, "error": f"index 越界: {index}（共 {pages.count()} 页）"}
        pages.setCurrentIndex(index)
        return {"ok": True, **_tab_state(win), "right_area_visible": _right_area_visible(win)}

    def _mpr_set_window_level(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        wl = args.get("wl")
        ww = args.get("ww")
        if wl is None and ww is None:
            return {"ok": False, "error": "需要 wl 或 ww"}
        if wl is not None and not _set_num(getattr(win, "sam_wl_slider", None), wl):
            return {"ok": False, "error": "GUI 无 sam_wl_slider"}
        if ww is not None and not _set_num(getattr(win, "sam_ww_slider", None), ww):
            return {"ok": False, "error": "GUI 无 sam_ww_slider"}
        fn = getattr(win, "_sam_ww_wl_changed", None)
        if fn is not None:
            try:
                fn()
            except TypeError:
                try:
                    fn(0)
                except Exception:
                    pass
            except Exception:
                pass
        return {"ok": True, **_mpr_state(win)}

    def _resolve_tool(self, raw: str) -> Optional[str]:
        """把工具名解析成 key：接受 key / sam_tool_names 值 / MPR 按钮短标签。

        key:   none|line|angle|rect|ellipse|poly
        值:    浏览|长度|角度|矩形|圆形/椭圆|不规则多边形
        标签:  看|长|角|矩|圆|形
        """
        tools = getattr(self.win, "sam_tool_names", None) or {}
        raw = str(raw or "none").strip()
        if not raw:
            return "none"
        btns = {"看": "none", "浏览": "none", "长": "line", "长度": "line",
                "角": "angle", "角度": "angle", "矩": "rect", "矩形": "rect",
                "圆": "ellipse", "圆/椭圆": "ellipse", "形": "poly"}
        low = raw.lower()
        if raw in tools:
            return raw
        if low in tools:
            return low
        if raw in btns and btns[raw] in tools:
            return btns[raw]
        for k, v in tools.items():
            vs = str(v)
            if raw == vs or raw in vs or vs.startswith(raw):
                return k
        return None

    def _mpr_set_tool(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        raw = str(args.get("tool") or "none").strip()
        tools = getattr(win, "sam_tool_names", None) or {}
        key = self._resolve_tool(raw)
        if key is None:
            return {"ok": False,
                    "error": f"未知测量工具: {raw}（可用 key、中文名或按钮标签）",
                    "available": tools}
        fn = getattr(win, "_sam_set_tool", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _sam_set_tool()"}
        try:
            fn(key)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_sam_set_tool() 失败: {e}"}
        return {"ok": True, **_mpr_state(win)}

    def _sam_run(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        if not hasattr(win, "_sam_run"):
            return {"ok": False, "error": "GUI 缺少 _sam_run()"}
        if getattr(win, "sam_mask", None) is None and not (getattr(win, "sam_pos_pts", None) or []):
            return {"ok": False,
                    "error": "没有 SAM 提示点：先在「MPR 阅片」页左键点选目标（右键=负点）",
                    "mpr": _mpr_state(win)}
        thr = args.get("threshold")
        if thr is not None:
            try:
                win.sam_threshold_spin.setValue(float(thr))
            except Exception:
                pass
        was_busy = bool(getattr(win, "sam_busy", False))
        QtCore.QTimer.singleShot(0, win._sam_run)
        return {"ok": True, "accepted": True, "was_busy": was_busy,
                "note": "分割在后台线程执行，完成后推 roi_done 事件",
                "mpr": _mpr_state(win)}

    def _roi_row_indexes(self, args: Dict[str, Any]) -> List[int]:
        idxs = args.get("indexes")
        if isinstance(idxs, (list, tuple)):
            out = []
            for v in idxs:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    continue
            if out:
                return out
        idx = args.get("index")
        if idx is not None:
            try:
                return [int(idx)]
            except (TypeError, ValueError):
                return []
        name = str(args.get("name") or "").strip()
        if name:
            return [r["index"] for r in _saved_roi_records(self.win)
                    if str(r.get("name") or "") == name]
        tbl = getattr(self.win, "sam_roi_table", None)
        if tbl is not None:
            try:
                return sorted({i.row() for i in tbl.selectionModel().selectedRows()})
            except Exception:
                return []
        return []

    def _sam_roi_action(self, args: Dict[str, Any], action: str) -> Dict[str, Any]:
        win = self.win
        tbl = getattr(win, "sam_roi_table", None)
        if tbl is None:
            return {"ok": False, "error": "GUI 无 sam_roi_table"}
        idxs = self._roi_row_indexes(args)
        available = [{"index": r["index"], "name": r.get("name")}
                     for r in _saved_roi_records(win)]
        if not idxs:
            return {"ok": False, "error": "需要 index / indexes / name 定位 ROI",
                    "available": available}
        tbl.clearSelection()
        hit = []
        for i in idxs:
            if 0 <= i < tbl.rowCount():
                tbl.selectRow(i)
                hit.append(i)
        if not hit:
            return {"ok": False, "error": f"行号越界 {idxs}（共 {tbl.rowCount()} 行）",
                    "available": available}
        method = "_sam_load_selected_roi" if action == "load" else "_sam_delete_selected_roi"
        fn = getattr(win, method, None)
        if fn is None:
            return {"ok": False, "error": f"GUI 缺少 {method}()"}
        try:
            with _NoDialogs():
                fn()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{method}() 失败: {e}"}
        recs = _saved_roi_records(win)
        return {"ok": True, "action": action, "rows": hit, "count": len(recs),
                "records": recs, "mpr": _mpr_state(win)}

    def _archive_items(self) -> List[Dict[str, Any]]:
        grid = getattr(self.win, "arch_grid", None)
        out: List[Dict[str, Any]] = []
        if grid is None:
            return out
        try:
            for i in range(grid.count()):
                it = grid.item(i)
                d = str(it.data(QtCore.Qt.ItemDataRole.UserRole) or "")
                text = it.text()
                out.append({
                    "index": i,
                    "name": text,
                    "title": text.splitlines()[0] if text else "",
                    "id": os.path.basename(d.rstrip("\\/")) if d else "",
                    "dir": d,
                    "is_demo": bool(it.data(QtCore.Qt.ItemDataRole.UserRole + 1)),
                })
        except Exception:
            pass
        return out

    def _archive_refresh(self) -> Dict[str, Any]:
        res = self._invoke("_archive_refresh", "archive_refresh")
        if not res.get("ok"):
            return res
        return {"ok": True, **_archive_state(self.win)}

    def _archive_list(self) -> Dict[str, Any]:
        return {"ok": True, "archives": self._archive_items(), **_archive_state(self.win)}

    def _archive_open(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        grid = getattr(win, "arch_grid", None)
        if grid is None:
            return {"ok": False, "error": "GUI 无 arch_grid"}
        items = self._archive_items()
        name = str(args.get("name") or "").strip()
        target = None
        if args.get("index") is not None:
            try:
                target = grid.item(int(args["index"]))
            except (TypeError, ValueError):
                target = None
        elif name:
            low = name.lower()
            for it in items:
                hay = " ".join([str(it.get("name") or ""), str(it.get("id") or ""),
                                str(it.get("dir") or ""), str(it.get("title") or "")])
                if low in hay.lower():
                    target = grid.item(it["index"])
                    break
        else:
            target = grid.currentItem()
        if target is None:
            return {"ok": False, "error": "未找到档案（给 index 或 name）",
                    "available": [it["name"] for it in items]}
        fn = getattr(win, "_archive_open_detail", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _archive_open_detail()"}
        try:
            with _NoDialogs():
                fn(target)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_archive_open_detail() 失败: {e}"}
        return {"ok": True, "opened": target.text(), "detail": _arch_detail_summary(win)}

    def _archive_save_shot(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        kind = str(args.get("kind") or "render").lower()
        if kind not in ("render", "mpr"):
            return {"ok": False, "error": "kind 只能是 render|mpr"}
        fn = getattr(win, "_save_shot_to_archive", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _save_shot_to_archive()"}
        d = ""
        before: set = set()
        try:
            d = str(win._archive_target_for_current() or "")
            if d and os.path.isdir(d):
                before = set(os.listdir(d))
        except Exception:
            d, before = "", set()
        if not d or not os.path.isdir(d):
            return {"ok": False, "error": "当前没有对应档案：先在「档案中心」页保存当前病例"}
        try:
            with _NoDialogs():
                fn(kind)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_save_shot_to_archive() 失败: {e}"}
        after: set = set()
        try:
            after = set(os.listdir(d))
        except Exception:
            pass
        return {"ok": True, "kind": kind, "dir": d, "new_files": sorted(after - before),
                "status": _txt(getattr(win, "arch_status", None))}

    def _settings_set(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        mod = sys.modules.get(type(win).__module__)
        if getattr(mod, "deepseek_client", None) is None:
            return {"ok": False, "error": "缺少 deepseek_client.py，无法保存配置"}
        saved: Dict[str, Any] = {}
        if args.get("model") is not None:
            cmb = getattr(win, "set_model", None)
            try:
                t = str(args["model"])
                i = cmb.findText(t)
                if i >= 0:
                    cmb.setCurrentIndex(i)
                else:
                    cmb.setEditText(t)
                saved["model"] = t
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"设置 model 失败: {e}"}
        if args.get("base_url") is not None:
            try:
                win.set_base.setText(str(args["base_url"]))
                saved["base_url"] = str(args["base_url"])
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"设置 base_url 失败: {e}"}
        if args.get("temperature") is not None:
            try:
                win.set_temp.setValue(float(args["temperature"]))
                saved["temperature"] = float(args["temperature"])
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"设置 temperature 失败: {e}"}
        if args.get("api_key") is not None:
            if not args.get("allow_key"):
                return {"ok": False, "error": "写入 api_key 需显式 allow_key=true（防误改密钥）"}
            try:
                win.set_key.setText(str(args["api_key"]))
                saved["api_key"] = "***"
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"设置 api_key 失败: {e}"}
        if not saved:
            return {"ok": False,
                    "error": "没有可写字段：model / base_url / temperature [/ api_key+allow_key]"}
        try:
            with _NoDialogs():
                win._settings_save()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_settings_save() 失败: {e}"}
        return {"ok": True, "saved": saved, **_settings_state(win)}

    def _case_values(self) -> Dict[str, Any]:
        try:
            return self.win._case_form_values() or {}
        except Exception:
            return {}

    def _case_set(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        vals = args.get("values") or args.get("fields") or {}
        if not isinstance(vals, dict) or not vals:
            return {"ok": False, "error": "需要 values={字段:值}"}
        fn = getattr(win, "_case_form_set", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _case_form_set()"}
        known = set((getattr(win, "case_fields", None) or {}).keys())
        unknown = sorted(k for k in vals if known and k not in known)
        try:
            fn(vals)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_case_form_set() 失败: {e}"}
        return {"ok": True, "applied": sorted(vals), "unknown_keys": unknown,
                "values": self._case_values()}

    def _pets_scan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        root = str(args.get("root") or "").strip()
        if root:
            try:
                win.pat_root_edit.line_edit().setText(root)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"写入扫描根目录失败: {e}"}
        fn = getattr(win, "_patient_scan", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _patient_scan()"}
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_patient_scan() 失败: {e}"}
        return {"ok": True, **_pets_state(win)}

    def _pacs_op(self, what: str, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        for key, attr in (("host", "pacs_host"), ("called", "pacs_called"),
                          ("calling", "pacs_calling")):
            v = args.get(key)
            if v:
                try:
                    getattr(win, attr).line_edit().setText(str(v))
                except Exception:
                    pass
        if args.get("port") is not None:
            try:
                win.pacs_port.setValue(int(args["port"]))
            except Exception:
                pass
        try:
            if bool(win._pacs_busy()):
                return {"ok": False, "error": "PACS 操作进行中，请稍后", "pacs": _pacs_state(win)}
        except Exception:
            pass
        method = {"echo": "_pacs_echo", "studies": "_pacs_find_studies"}.get(what)
        if method is None:
            method = "_pacs_retrieve"
            tree = getattr(win, "pacs_tree", None)
            sel = []
            try:
                sel = tree.selectedItems() if tree is not None else []
            except Exception:
                sel = []
            if not sel:
                it = None
                try:
                    it = tree.topLevelItem(int(args.get("study_index", 0) or 0))
                except Exception:
                    it = None
                if it is None:
                    return {"ok": False,
                            "error": "PACS 树里没有检查可获取：先 pacs_find_studies",
                            "pacs": _pacs_state(win)}
                it.setSelected(True)
        fn = getattr(win, method, None)
        if fn is None:
            return {"ok": False, "error": f"GUI 缺少 {method}()"}
        try:
            with _NoDialogs():
                fn()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{method}() 失败: {e}"}
        return {"ok": True, "action": what, "accepted": True,
                "note": "PACS 操作在后台线程执行，可轮询 query_state().pacs.status",
                "pacs": _pacs_state(win)}

    def _sam_probe(self) -> Dict[str, Any]:
        """3D SAM 栈自检：torch / torchio / medim / monai / cupy + CUDA + 权重路径。

        冻结版（便携包）里没有控制台，靠它一条 op 就能确认分割依赖是否齐全。
        """
        import importlib

        info: Dict[str, Any] = {}
        for name in ("torch", "torchio", "medim", "monai"):
            try:
                mod = importlib.import_module(name)
                info[name] = str(getattr(mod, "__version__", "ok"))
            except Exception as e:  # noqa: BLE001
                info[name] = "缺失: %s" % e
        try:
            import torch
            cuda = bool(torch.cuda.is_available())
            info["cuda_available"] = cuda
            info["cuda_device"] = torch.cuda.get_device_name(0) if cuda else ""
        except Exception as e:  # noqa: BLE001
            info["cuda_available"] = False
            info["cuda_error"] = str(e)
        try:
            import cupy
            info["cupy"] = str(cupy.__version__)
            try:
                info["cupy_gpu"] = bool(cupy.cuda.runtime.getDeviceCount() > 0)
            except Exception as e:  # noqa: BLE001
                info["cupy_gpu"] = False
                info["cupy_error"] = str(e)
        except Exception as e:  # noqa: BLE001
            info["cupy"] = "缺失: %s" % e
        try:
            from segmentation import config as segcfg
            info["sam_dir"] = segcfg.SAM_MED3D_DIR
            info["sam_dir_exists"] = os.path.isdir(segcfg.SAM_MED3D_DIR)
            info["ckpt"] = segcfg.SAM_CKPT
            info["ckpt_exists"] = os.path.isfile(segcfg.SAM_CKPT)
        except Exception as e:  # noqa: BLE001
            info["sam_dir"] = "config 读取失败: %s" % e
        try:
            from segmentation.sam_adapter import SAMMed3DAdapter  # noqa: F401
            info["sam_adapter_import"] = "ok"
        except Exception as e:  # noqa: BLE001
            info["sam_adapter_import"] = "失败: %s" % e
        info["ok"] = not str(info.get("torch", "")).startswith("缺失")
        return info

    def _ai_run(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        prompt = args.get("prompt")
        if prompt:
            try:
                win.ai_prompt.setPlainText(str(prompt))
            except Exception:
                try:
                    win.ai_prompt.setText(str(prompt))
                except Exception:
                    pass
        if not _settings_state(win).get("key_set"):
            return {"ok": False,
                    "error": "未配置 DeepSeek API Key：先在「设置」页填写并保存（settings_set）"}
        fn = getattr(win, "_ai_run", None)
        if fn is None:
            return {"ok": False, "error": "GUI 缺少 _ai_run()"}
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"_ai_run() 失败: {e}"}
        return {"ok": True, "accepted": True, "ai": _ai_state(win)}

    def _set_custom_roi(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """注入自定义 ROI 掩膜（如 ASPECTS 梗死区）并高亮渲染。
        args: {
          mask_b64: base64 的 uint8 掩膜（与原始图像同 shape 或 bbox 内 shape）,
          bbox: {"z":[z0,z1],"y":[y0,y1],"x":[x0,x1]},   # 掩膜在原图中的 bbox
          label_id, name, color_hu
        }
        """
        import base64 as _b64
        import numpy as _np
        win = self.win
        mask_b64 = args.get("mask_b64")
        mask_path = args.get("mask_path")
        bbox = args.get("bbox") or {}
        if not mask_b64 and not mask_path:
            return {"ok": False, "error": "mask_b64 or mask_path required"}
        if mask_path:
            try:
                arr = _np.load(mask_path).astype(_np.uint8)
            except Exception as e:
                return {"ok": False, "error": f"bad npy: {e}"}
        else:
            try:
                arr = _np.frombuffer(_b64.b64decode(mask_b64), dtype=_np.uint8)
            except Exception as e:
                return {"ok": False, "error": f"bad base64: {e}"}
        # 尝试恢复 shape：优先 bbox，其次原始图像
        orig = getattr(win, "original_sitk_image", None) or getattr(win, "image_data", None)
        z, y, x = None, None, None
        if bbox:
            z = bbox.get("z"); y = bbox.get("y"); x = bbox.get("x")
        if z and y and x:
            want = (z[1]-z[0]) * (y[1]-y[0]) * (x[1]-x[0])
        elif orig is not None:
            dims = orig.GetDimensions()
            z, y, x = [0, dims[2]], [0, dims[1]], [0, dims[0]]
            want = dims[2]*dims[1]*dims[0]
        else:
            return {"ok": False, "error": "no bbox nor image dims"}
        if arr.size != want:
            return {"ok": False, "error": f"mask size {arr.size} != bbox size {want}"}
        mask = arr.reshape(z[1]-z[0], y[1]-y[0], x[1]-x[0]).astype(bool)
        from segmentation.roi_types import ROIBlock
        from segmentation.roi_types import ROIRegionResult
        label_id = int(args.get("label_id") or 99001)
        name = args.get("name") or "梗死区"
        block = ROIBlock(
            region="全局",
            category="tissues",
            mask=mask,
            bbox_z=(int(z[0]), int(z[1])),
            bbox_y=(int(y[0]), int(y[1])),
            bbox_x=(int(x[0]), int(x[1])),
            z_range_mm=(0.0, 0.0),
            volume_cm3=float(mask.sum()),
            voxel_count=int(mask.sum()),
            label_id=label_id,
            anatomical_name=name,
        )
        # 注入 roi_results
        results = getattr(win, "roi_results", None)
        if not results:
            rr = ROIRegionResult(region="全局")
            results = [rr]
            win.roi_results = results
        results[0].tissues.append(block)
        win._apply_roi_pixel_replacement([block])
        if getattr(win, "render_window", None) is not None:
            win.render_window.Render()
        return {"ok": True, "label_id": label_id, "name": name, "voxels": int(mask.sum())}

    def _set_roi_weight_path(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """已废弃：TotalSegmentator / nnU-Net 权重目录随自动ROI 功能一并移除。"""
        return {"ok": False, "deprecated": True,
                "error": "TotalSegmentator/nnU-Net 权重目录已随自动ROI 功能移除（本程序无此功能）",
                "how": "分割请用「MPR 阅片」页的 3D SAM（sam_run / sam_list_rois）"}

    def _list_presets(self) -> Dict[str, Any]:
        win = self.win
        presets = list(getattr(win, "slicer_presets", {}).keys())
        return {"ok": True, "count": len(presets), "presets": presets}

    def _apply_preset(self, args: Dict[str, Any]) -> Dict[str, Any]:
        win = self.win
        name = args.get("name")
        presets = getattr(win, "slicer_presets", {})
        if not name or name not in presets:
            return {"ok": False, "error": f"unknown preset: {name}"}
        p = presets[name]
        if p.get("opacity"):
            win.vr_opacity_points = list(p["opacity"])
        if p.get("color"):
            win.vr_color_points = list(p["color"])
        vol = getattr(win, "current_vr_volume", None)
        if vol is not None:
            prop = vol.GetProperty()
            if p.get("ambient") is not None:
                prop.SetAmbient(p["ambient"])
            if p.get("diffuse") is not None:
                prop.SetDiffuse(p["diffuse"])
            if p.get("specular") is not None:
                prop.SetSpecular(p["specular"])
            if p.get("specularPower") is not None:
                prop.SetSpecularPower(p["specularPower"])
            win._update_vr_transfer_functions()
        if getattr(win, "render_window", None) is not None:
            win.render_window.Render()
        return {"ok": True, "preset": name}

    def _get_render_params(self) -> Dict[str, Any]:
        win = self.win
        return {
            "render_mode": getattr(win, "render_mode", "stable"),
            "ssd_scale": self._slider_scale(win, "ssd_slider"),
            "vr_scale": self._slider_scale(win, "vr_slider"),
            "wl_offset": self._raw_val(win, "wl_slider"),
            "ww_scale": self._raw_val(win, "ww_slider", 100) / 100.0,
            "ssd_opacity_points": getattr(win, "ssd_opacity_points", None),
            "vr_opacity_points": getattr(win, "vr_opacity_points", None),
            "denoise_method": getattr(win, "denoise_method", "gaussian"),
            "cpu_render": getattr(win, "cpu_render", False),
            "vram_threshold_gb": getattr(win, "vram_threshold_gb", 10),
        }

    def _shutdown(self) -> Dict[str, Any]:
        # 延迟一点再关窗：让 shutdown 的响应先写回客户端，否则调用方会看到
        # bridge disconnected（响应还没发出去 socket 就随进程退出了）。
        QtCore.QTimer.singleShot(200, self._do_shutdown)
        return {"ok": True, "shutting_down": True}

    def _do_shutdown(self) -> None:
        win = self.win
        try:
            win.close()
        except Exception:
            pass
        QtWidgets.QApplication.quit()

    # ---- 小工具 ----
    @staticmethod
    def _set_slider(win, name: str, val: float) -> None:
        obj = getattr(win, name, None)
        if obj is None:
            return
        slider = obj.slider()
        slider.setValue(int(round(val)))

    @staticmethod
    def _slider_scale(win, name: str) -> float:
        obj = getattr(win, name, None)
        try:
            return float(obj.slider().value()) / 100.0
        except Exception:
            return 0.0

    @staticmethod
    def _raw_val(win, name: str, default: int = 0) -> int:
        obj = getattr(win, name, None)
        try:
            return int(obj.slider().value())
        except Exception:
            return default

    @staticmethod
    def _camera_state(cam) -> Dict[str, Any]:
        pos = cam.GetPosition()
        fp = cam.GetFocalPoint()
        up = cam.GetViewUp()
        import math
        d = math.sqrt(sum((pos[i] - fp[i]) ** 2 for i in range(3)))
        return {
            "position": [round(float(v), 3) for v in pos],
            "focal": [round(float(v), 3) for v in fp],
            "view_up": [round(float(v), 3) for v in up],
            "distance": round(d, 3),
            "view_angle": round(float(cam.GetViewAngle()), 3),
        }


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

class GuiBridgeServer:
    def __init__(self, win, port: int = 7799, host: str = "127.0.0.1") -> None:
        self.win = win
        self.port = port
        self.host = host
        self.dispatcher = _Dispatcher(win, self)
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._closing = threading.Event()

    # -- 绑定 / 发布 ------------------------------------------------------

    def _bind(self) -> bool:
        """同步绑定端口；被占用则向后探测。返回是否绑定成功。"""
        while self._running.is_set():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
                sock.listen(1)
                sock.settimeout(1.0)
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
                if self.port >= 7899:
                    return False
                self.port += 1
                continue
            self._sock = sock
            return True
        return False

    def _publish(self) -> None:
        """把**实际**端口写进发现文件并广播 gui_ready。

        只有绑定成功之后才能知道真实端口（被占用时会向后递增），
        所以这里必须在 _bind() 之后调用——否则客户端拿到的是请求端口。
        """
        try:
            registry.write_bridge_file(
                self.port, host=self.host,
                mode="mcp",
                frozen=bool(getattr(sys, "frozen", False)),
                exe=os.path.abspath(sys.executable),
            )
        except Exception:
            pass  # 发现文件只是便利手段，失败不影响桥本身
        self.push_event("gui_ready", {"port": self.port, "host": self.host})

    def start(self) -> None:
        self._running.set()
        if not self._bind():
            raise RuntimeError(f"cannot bind mcp bridge on {self.host}:{self.port}")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._publish()

    def _serve(self) -> None:
        while self._running.is_set():
            if self._sock is None:
                if not self._bind():
                    break
                self._publish()
            while self._running.is_set():
                try:
                    conn, _ = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with self._clients_lock:
                    self._clients.append(conn)
                threading.Thread(target=self._client_loop, args=(conn,), daemon=True).start()
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _client_loop(self, conn: socket.socket) -> None:
        buf = b""
        while self._running.is_set() and not self._closing.is_set():
            try:
                chunk = conn.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                if "id" in msg and "op" in msg:
                    self._handle_request(conn, msg)
        with self._clients_lock:
            if conn in self._clients:
                self._clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    def _handle_request(self, conn: socket.socket, msg: Dict[str, Any]) -> None:
        req_id = msg["id"]
        op = msg.get("op", "")
        args = msg.get("args", {}) or {}
        timeout = float(msg.get("timeout", 120.0))
        ok, data = self.dispatcher.submit(req_id, op, args, timeout)
        resp = {"id": req_id, "ok": ok, "data": data}
        self._send(conn, resp)

    def _send(self, conn: socket.socket, obj: Dict[str, Any]) -> None:
        try:
            conn.sendall((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        except OSError:
            pass

    def push_event(self, etype: str, data: Dict[str, Any]) -> None:
        evt = {"type": etype, "data": data, "ts": time.time()}
        with self._clients_lock:
            clients = list(self._clients)
        for c in clients:
            self._send(c, evt)

    def stop(self) -> None:
        self._closing.set()
        self._running.clear()
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            registry.remove_bridge_file(self.port)
        except Exception:
            pass


def start_bridge(win, port: int) -> GuiBridgeServer:
    bridge = GuiBridgeServer(win, port=port)
    win._mcp_bridge = bridge
    bridge.start()
    # 退出时清掉发现文件，避免下次启动读到过期的端口
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            app.aboutToQuit.connect(bridge.stop)
        except Exception:
            pass
    return bridge
