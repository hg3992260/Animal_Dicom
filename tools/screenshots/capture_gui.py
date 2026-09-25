"""补拍 v2：整窗截图 + 把 VTK 渲染结果合成进去。

问题：`QWidget.grab()` 抓不到原生 OpenGL 子窗口（VTK 渲染区会是黑的）。
解法：抓两样——① Qt 的整窗（UI 部分完美）② VTK 自己的 window-to-image（渲染部分完美），
按 vtk 控件在窗口中的位置合成。两者都来自同一个进程的同一时刻，不存在"拼接假图"。

顺带处理 devicePixelRatio（本机 1.25，逻辑 1500x980 → 物理 1875x1225）。
"""
from __future__ import annotations

import os
import sys
import time

REPO = r"I:\SSD+VR_github"
sys.path.insert(0, REPO)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from PIL import Image  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402
from PyCt6 import set_appearance_mode, set_color_theme  # noqa: E402

import ssd_vr_viewer as V  # noqa: E402
from mcp_ssd_vr import state as bridge_state  # noqa: E402
from mcp_ssd_vr.gui_bridge import _Dispatcher, take_screenshot  # noqa: E402

CASE = os.path.join(REPO, "temp", "shot_case")
THIN = os.path.join(CASE, "P001_CT_Thorax_1.0mm")
OUT = os.path.join(REPO, "temp", "shots", "out")
RAW = os.path.join(REPO, "temp", "shots", "_raw")
os.makedirs(OUT, exist_ok=True)
os.makedirs(RAW, exist_ok=True)

T0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - T0:6.1f}s] {m}", flush=True)


class _Stub:
    def push_event(self, *a, **k):
        pass


def pump(ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def composite(win, name: str) -> str:
    """整窗(UI) + VTK 渲染 合成。"""
    ui_path = os.path.join(RAW, name.replace(".png", "_ui.png"))
    win.grab().save(ui_path, "PNG")
    ui = Image.open(ui_path).convert("RGB")

    shot = take_screenshot(win, RAW)
    rp = shot.get("path")
    if not rp or not os.path.isfile(rp):
        log(f"{name}: 没有渲染图，退回纯 UI")
        ui.save(os.path.join(OUT, name), "PNG")
        return os.path.join(OUT, name)
    render = Image.open(rp).convert("RGB")

    widget = getattr(win, "vtk_widget", None) or getattr(win, "vtkWidget", None)
    if widget is None:
        log(f"{name}: 找不到 vtk 控件，退回纯 UI")
        ui.save(os.path.join(OUT, name), "PNG")
        return os.path.join(OUT, name)

    tl = widget.mapTo(win, QtCore.QPoint(0, 0))
    dpr = ui.width / float(win.width()) if win.width() else 1.0
    x, y = int(tl.x() * dpr), int(tl.y() * dpr)
    w, h = int(widget.width() * dpr), int(widget.height() * dpr)
    if w <= 0 or h <= 0:
        ui.save(os.path.join(OUT, name), "PNG")
        return os.path.join(OUT, name)
    ui.paste(render.resize((w, h), Image.LANCZOS), (x, y))
    final = os.path.join(OUT, name)
    ui.save(final, "PNG")
    log(f"{name}  {ui.width}x{ui.height}  render@{x},{y} {w}x{h}  {os.path.getsize(final)/1024:.0f} KB")
    return final


def main() -> int:
    app = QtWidgets.QApplication(sys.argv[:1])
    set_appearance_mode("dark")
    set_color_theme(os.path.join(REPO, "scientific.json"))
    with open(os.path.join(REPO, "dark.qss"), encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    win = V.ViewerWindow(initial_input="")
    win.resize(1500, 980)
    win.show()
    win.raise_()
    win.activateWindow()
    pump(3000)
    disp = _Dispatcher(win, _Stub())

    win.pages.setCurrentIndex(win._tab_index_by_text("渲染"))
    win.path_edit.line_edit().setText(THIN)
    log("加载…")
    win.load_dicom()
    pump(4000)

    disp._set_camera({"reset": True})
    disp._set_camera({"view": "three_quarter", "azimuth": 16, "elevation": 12, "dolly": 1.08})
    pump(1500)

    # ① 电影级 + 渲染参数页（hero）
    win.on_mode_change(bridge_state.mode_index("cinematic"))
    pump(8000)
    composite(win, "hero-01-cinematic-params.png")

    # ② 爱宠列表页 + 同一渲染（hero）
    win.pages.setCurrentIndex(win._tab_index_by_text("爱宠列表"))
    win.pat_root_edit.line_edit().setText(CASE)
    win._patient_scan()
    pump(3500)
    win._patient_expand_all()
    pump(1000)
    composite(win, "hero-02-patient-list.png")

    # ③ 自然通道（微通道观感）
    win.pages.setCurrentIndex(win._tab_index_by_text("渲染"))
    win.on_mode_change(bridge_state.mode_index("nature_channels"))
    pump(8000)
    composite(win, "hero-03-nature-channels.png")

    # ④ 自适应阈值/双侧滑块：VR 模式（稳定）+ 骨窗
    win.on_mode_change(bridge_state.mode_index("hd_surface"))
    pump(6000)
    composite(win, "hero-04-hd-surface.png")

    win.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
