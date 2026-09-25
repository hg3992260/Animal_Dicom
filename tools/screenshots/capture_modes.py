"""生成 README 截图素材：启动真实 GUI（in-process），驱动并抓图。

为什么在进程内抓图：`QWidget.grab()` 拿到的是控件自身渲染结果，不受窗口遮挡/分辨率/DPI 影响，
比屏幕截图干净得多。渲染窗口另用 `mcp_ssd_vr.gui_bridge.take_screenshot()`（与 MCP 同一路径）。

用法:
    python temp/shots/capture.py            # 全部
    python temp/shots/capture.py ui         # 只出界面图
    python temp/shots/capture.py render     # 只出渲染模式图
"""
from __future__ import annotations

import os
import sys
import time

REPO = r"I:\SSD+VR_github"
sys.path.insert(0, REPO)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PyCt6 import set_appearance_mode, set_color_theme  # noqa: E402

import ssd_vr_viewer as V  # noqa: E402
from mcp_ssd_vr import state as bridge_state  # noqa: E402
from mcp_ssd_vr.gui_bridge import _Dispatcher, take_screenshot  # noqa: E402

CASE = os.path.join(REPO, "temp", "shot_case")
THIN = os.path.join(CASE, "P001_CT_Thorax_1.0mm")
OUT = os.path.join(REPO, "temp", "shots", "out")
os.makedirs(OUT, exist_ok=True)

MODES = ["stable", "hd_surface", "cinematic", "nature_channels", "figure8_channels",
         "layer_channel", "frangi_channel", "bone_mono", "2dtf", "spectral",
         "dual_volume", "exposure_render"]

LOG = []


def log(msg: str) -> None:
    line = f"[{time.time() - T0:6.1f}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


T0 = time.time()


class _StubBridge:
    def push_event(self, *a, **k):
        pass


def pump(ms: int) -> None:
    """跑事件循环 ms 毫秒（渲染需要真实时间，不能只 processEvents 一次）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def save_window(win, name: str) -> str:
    path = os.path.join(OUT, name)
    pix = win.grab()
    pix.save(path, "PNG")
    log(f"窗口截图 {name}  {pix.width()}x{pix.height()}  {os.path.getsize(path)/1024:.0f} KB")
    return path


def save_render(win, name: str) -> str:
    data = take_screenshot(win, OUT)
    src = data.get("path")
    if not src:
        log(f"渲染截图失败 {name}: {data.get('error')}")
        return ""
    dst = os.path.join(OUT, name)
    try:
        os.replace(src, dst)
    except OSError:
        dst = src
    log(f"渲染截图 {name}  {data.get('stats')}")
    return dst


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"

    app = QtWidgets.QApplication(sys.argv[:1])
    set_appearance_mode("dark")
    set_color_theme(os.path.join(REPO, "scientific.json"))
    with open(os.path.join(REPO, "dark.qss"), encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    log("创建 ViewerWindow")
    win = V.ViewerWindow(initial_input="")
    win.resize(1500, 980)
    win.show()
    win.raise_()
    win.activateWindow()
    pump(3500)

    disp = _Dispatcher(win, _StubBridge())

    # ─────────────────────────── 界面截图 ───────────────────────────
    if stage in ("all", "ui"):
        try:
            win.pages.setCurrentIndex(win._tab_index_by_text("爱宠列表"))
            win.pat_root_edit.line_edit().setText(CASE)
            log("扫描爱宠列表…")
            win._patient_scan()
            pump(5000)
            win._patient_expand_all()
            pump(1500)
            rows = win.pat_tree.topLevelItemCount()
            log(f"爱宠列表顶级节点 {rows} 个")
            save_window(win, "ui-01-patient-list.png")
        except Exception as e:  # noqa: BLE001
            log(f"爱宠列表截图失败: {e}")

        for label, name in (("渲染参数", "ui-02-render-params.png"),
                            ("MPR 阅片", "ui-05-sam-tab.png")):
            try:
                if label == "渲染参数":
                    win.pages.setCurrentIndex(win._tab_index_by_text("渲染"))
                else:
                    idx = win._tab_index_by_text(label)
                    win.pages.setCurrentIndex(idx)
                pump(1200)
                save_window(win, name)
            except Exception as e:  # noqa: BLE001
                log(f"{label} 截图失败: {e}")

    # ─────────────────────────── 渲染截图 ───────────────────────────
    if stage in ("all", "render"):
        try:
            win.pages.setCurrentIndex(win._tab_index_by_text("渲染"))
            win.path_edit.line_edit().setText(THIN)
            log("加载薄层序列（阻塞渲染）…")
            win.load_dicom()
            pump(4000)
            st = win.image_data.GetDimensions() if getattr(win, "image_data", None) is not None else None
            log(f"已加载 dims={st}")

            disp._set_camera({"reset": True})
            disp._set_camera({"view": "three_quarter", "azimuth": 18, "elevation": 10, "dolly": 1.06})
            pump(2000)
            save_window(win, "ui-06-hero-cinematic.png")   # 先存一张带界面的
        except Exception as e:  # noqa: BLE001
            log(f"加载失败: {e}")

        for mode in MODES:
            try:
                idx = bridge_state.mode_index(mode)
                win.on_mode_change(idx)
                # CR / 路径追踪类多给点时间积累样本
                pump(4500 if mode in ("cinematic", "spectral", "exposure_render") else 2200)
                save_render(win, f"render-{mode}.png")
            except Exception as e:  # noqa: BLE001
                log(f"模式 {mode} 截图失败: {e}")

        # 自定义 ROI 叠加渲染（演示 label 高亮，不依赖分割权重）
        try:
            import numpy as np
            mask = np.zeros((40, 128, 128), dtype=np.uint8)
            zz, yy, xx = np.mgrid[0:40, 0:128, 0:128]
            sphere = ((zz - 20) / 14.0) ** 2 + ((yy - 58) / 26.0) ** 2 + ((xx - 70) / 26.0) ** 2 <= 1
            mask[sphere] = 1
            mpath = os.path.join(OUT, "_demo_infarct.npy")
            np.save(mpath, mask)
            # 掩膜覆盖在体数据左前上区域（归一化 bbox）
            dims = [int(v) for v in win.image_data.GetDimensions()] if getattr(win, "image_data", None) else [256, 256, 176]
            bbox = {"z": [0.35, 0.65], "y": [0.30, 0.70], "x": [0.35, 0.75]}
            res = disp._set_custom_roi({"mask_path": mpath, "bbox": bbox, "name": "示例梗死区", "label_id": 99001})
            log(f"自定义 ROI: {res}")
            pump(2500)
            save_render(win, "render-custom-roi.png")
            save_window(win, "ui-07-custom-roi.png")
        except Exception as e:  # noqa: BLE001
            log(f"自定义 ROI 截图失败: {e}")

        try:
            disp._set_camera({"azimuth": 120})
            win.on_mode_change(bridge_state.mode_index("cinematic"))
            pump(5000)
            save_render(win, "render-cinematic-rear.png")
        except Exception as e:  # noqa: BLE001
            log(f"背面视角截图失败: {e}")

    win.close()
    app.quit()

    with open(os.path.join(OUT, "_capture.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG))
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".png"))
    log(f"共生成 {len(files)} 张: {files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
