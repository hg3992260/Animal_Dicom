import argparse
import importlib.util
import json
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))


def _external_dir() -> str:
    """外部可写目录：冻结(EXE)时=EXE 所在目录，否则=源码目录。用于权重/外部脚本。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return current_dir


# ---------------------------------------------------------------------------
# macOS 冻结包：定位 Qt 插件与 Qt 库
# ---------------------------------------------------------------------------
# 背景（2026-09-17 DMG 启动 SIGABRT）：CI 上同时存在两套 Qt —— conda-forge 的 vtk
# 链接**裸 dylib**（libQt6Gui.6.dylib），pip 的 PySide6 是 **framework**
# （QtGui.framework/.../QtGui）。旧 spec 把裸 dylib 删光，导致裸命名的平台插件
# libqcocoa.dylib 变成孤儿，QApplication 构造时 qFatal → abort()。
#
# 现在 spec 不再删任何 Qt 库、并把插件统一收到 PySide6/Qt/plugins；本函数是运行时
# 兜底：把可能的目录都找一遍，按"真的含有 platforms/libqcocoa.dylib"来判定，
# 再通过环境变量告诉 Qt（环境变量优先级高于 qt.conf，找不到时不会污染）。

def _macos_qt_candidates(executable: str = "", resources: str = "") -> dict:
    """返回候选目录（纯路径计算，不碰环境变量、不要求目录存在，便于单元测试）。

    注意 PyInstaller 的 BUNDLE 会把 **binaries 放进 Contents/Frameworks**、
    datas 放进 Contents/Resources —— 实测（2026-09-17 崩溃报告）Qt 库与插件都在
    Contents/Frameworks/PySide6/Qt/... 下，所以 Frameworks 必须排在前面。
    """
    exe = os.path.abspath(executable or sys.executable)
    exe_dir = os.path.dirname(exe)
    contents = os.path.dirname(exe_dir)                      # .../X.app/Contents
    res = os.path.abspath(resources) if resources else os.path.join(contents, "Resources")
    fw = os.path.join(contents, "Frameworks")

    plugin_dirs = [
        # PyInstaller BUNDLE 的实际落点（binaries -> Contents/Frameworks）
        os.path.join(fw, "PySide6", "Qt", "plugins"),
        os.path.join(fw, "PySide6", "plugins"),
        os.path.join(fw, "plugins"),
        os.path.join(contents, "PlugIns"),                   # 传统 bundle 位置
        # onedir / 旧布局兜底（datas -> Contents/Resources）
        os.path.join(res, "PySide6", "Qt", "plugins"),
        os.path.join(res, "PySide6", "plugins"),
        os.path.join(res, "plugins"),
        os.path.join(res, "qt6", "plugins"),
        os.path.join(exe_dir, "PySide6", "Qt", "plugins"),
    ]

    lib_dirs = [
        fw,                                                  # conda 裸 dylib 常在这
        os.path.join(fw, "PySide6", "Qt", "lib"),            # pyside6 wheel 的 Qt
        os.path.join(res, "PySide6", "Qt", "lib"),
        os.path.join(res, "lib"),
        exe_dir,
    ]
    return {"exe": exe, "resources": res, "frameworks": fw,
            "plugin_dirs": plugin_dirs, "lib_dirs": lib_dirs}


def _setup_macos_qt_paths(executable: str = "", resources: str = "",
                          environ: dict = None, log=None) -> dict:
    """在 QApplication 之前设置 Qt 插件/库搜索路径。返回找到的目录，便于日志与测试。

    - QT_PLUGIN_PATH / QT_QPA_PLATFORM_PLUGIN_PATH：指向真正含 libqcocoa 的目录
    - DYLD_FALLBACK_LIBRARY_PATH：附上候选库目录（比 DYLD_LIBRARY_PATH 更可靠，
      签名/加固运行时经常把后者剥掉）
    """
    env = os.environ if environ is None else environ
    cand = _macos_qt_candidates(executable, resources)

    def _has_cocoa(d: str) -> bool:
        return os.path.isfile(os.path.join(d, "platforms", "libqcocoa.dylib"))

    plugin_dir = ""
    for d in cand["plugin_dirs"]:
        if _has_cocoa(d):
            plugin_dir = d
            break
    if not plugin_dir:                        # 退而求其次：存在 platforms 子目录即可
        for d in cand["plugin_dirs"]:
            if os.path.isdir(os.path.join(d, "platforms")):
                plugin_dir = d
                break

    if plugin_dir:
        prev = env.get("QT_PLUGIN_PATH", "")
        env["QT_PLUGIN_PATH"] = plugin_dir + (os.pathsep + prev if prev else "")
        platforms = os.path.join(plugin_dir, "platforms")
        if os.path.isdir(platforms):
            env["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms

    found_libs = [d for d in cand["lib_dirs"] if os.path.isdir(d)]
    if found_libs:
        prev = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        env["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(
            found_libs + ([prev] if prev else []))

    if log is not None:
        log(f"[macOS] plugin_dir={plugin_dir or '(none)'} lib_dirs={found_libs}")

    return {"plugin_dir": plugin_dir, "lib_dirs": found_libs,
            "plugin_candidates": cand["plugin_dirs"], "resources": cand["resources"]}

# Inject the pv_packages directory into sys.path to allow pvpython to find PyQt5 and SimpleITK
if sys.platform == "win32":
    pv_packages_dir = os.path.join(current_dir, "pv_packages")
    sys.path.insert(0, pv_packages_dir)

    # Add the DLL directory for PyQt5 and pv_packages
    pyqt5_qt5_bin = os.path.join(pv_packages_dir, "PyQt5", "Qt5", "bin")
    pyqt5_root = os.path.join(pv_packages_dir, "PyQt5")
    if hasattr(os, "add_dll_directory"):
        if os.path.exists(pyqt5_qt5_bin):
            os.add_dll_directory(pyqt5_qt5_bin)
        if os.path.exists(pyqt5_root):
            os.add_dll_directory(pyqt5_root)
        if os.path.exists(pv_packages_dir):
            os.add_dll_directory(pv_packages_dir)
    os.environ["PATH"] = pyqt5_qt5_bin + os.pathsep + pyqt5_root + os.pathsep + pv_packages_dir + os.pathsep + os.environ.get("PATH", "")

    # Ensure PyQt5 uses its own plugins instead of ParaView's Qt plugins
    qt_plugins_dir = os.path.join(pyqt5_root, "Qt5", "plugins")
    if os.path.exists(qt_plugins_dir):
        os.environ["QT_PLUGIN_PATH"] = qt_plugins_dir
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(qt_plugins_dir, "platforms")
        # Tell PyQt5 to ignore ParaView's Qt6 environment variables
        if "QT_ROOT" in os.environ:
            del os.environ["QT_ROOT"]

# Suppress Qt6 invalid metadata warnings from ParaView's DLLs
os.environ["QT_LOGGING_RULES"] = "qt.core.plugin.loader.warning=false;qt.core.library.warning=false"
os.environ["QT_CORE_NO_EXECUTABLE_PATH"] = "1"

from typing import List, Optional, Tuple

import ctypes

if sys.platform == "darwin":
    _er_ext = ".dylib"
elif sys.platform == "win32":
    _er_ext = ".dll"
else:
    _er_ext = ".so"
ER_DLL_PATH = os.path.join(current_dir, "exposure-render-master", "exposure-render-master", "Source", "build", "Release", f"ErCore{_er_ext}")
er_core = None
if os.path.exists(ER_DLL_PATH):
    try:
        # Load the directory first to satisfy CUDA runtime dependencies if any (Windows only)
        if sys.platform == "win32":
            os.add_dll_directory(os.path.dirname(ER_DLL_PATH))
        er_core = ctypes.CDLL(ER_DLL_PATH)
        er_core.er_create_tracer.restype = ctypes.c_void_p
        er_core.er_create_volume.restype = ctypes.c_void_p
        
        # Camera bindings
        er_core.er_set_tracer_resolution.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        er_core.er_bind_tracer.argtypes = [ctypes.c_void_p]
        er_core.er_get_tracer_id.argtypes = [ctypes.c_void_p]
        er_core.er_get_tracer_id.restype = ctypes.c_int
        er_core.er_set_camera.argtypes = [
            ctypes.c_void_p,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float
        ]
        
        er_core.er_bind_volume_data.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_void_p
        ]
        er_core.er_bind_volume.argtypes = [ctypes.c_void_p]
        
        # ErCore rendering
        er_core.er_render_estimate.argtypes = [ctypes.c_int]
        er_core.er_get_estimate.argtypes = [ctypes.c_int, ctypes.c_void_p]
        er_core.er_clear_opacity_tf.argtypes = [ctypes.c_void_p]
        er_core.er_add_opacity_node.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float]
        er_core.er_clear_diffuse_tf.argtypes = [ctypes.c_void_p]
        er_core.er_add_diffuse_node.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float]
        er_core.er_reset_accumulation.argtypes = [ctypes.c_void_p]

        # Light bindings
        er_core.er_create_light.restype = ctypes.c_void_p
        er_core.er_destroy_light.argtypes = [ctypes.c_void_p]
        er_core.er_bind_light.argtypes = [ctypes.c_void_p]
        er_core.er_tracer_add_light.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        er_core.er_tracer_clear_lights.argtypes = [ctypes.c_void_p]
        er_core.er_set_light_properties.argtypes = [
            ctypes.c_void_p,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float
        ]
    except Exception as e:
        print(f"Warning: Failed to load ErCore.dll: {e}")

class ErCoreWrapper:
    def __init__(self):
        if not er_core:
            raise RuntimeError("ErCore is not loaded.")
        self.tracer = er_core.er_create_tracer()
        self.volume = er_core.er_create_volume()
        self.lights = []

    def __del__(self):
        if er_core:
            er_core.er_destroy_tracer(self.tracer)
            er_core.er_destroy_volume(self.volume)
            for light in self.lights:
                er_core.er_destroy_light(light)

    def setup_lights(self, key_pos, key_dir, key_color, key_mult, key_size,
                           fill_pos, fill_dir, fill_color, fill_mult, fill_size,
                           rim_pos, rim_dir, rim_color, rim_mult, rim_size):
        # Clear existing lights
        er_core.er_tracer_clear_lights(self.tracer)
        for light in self.lights:
            er_core.er_destroy_light(light)
        self.lights.clear()

        def add_light(pos, dir_v, color, mult, size):
            light = er_core.er_create_light()
            er_core.er_set_light_properties(
                light,
                float(pos[0]), float(pos[1]), float(pos[2]),
                float(dir_v[0]), float(dir_v[1]), float(dir_v[2]),
                float(color[0]), float(color[1]), float(color[2]),
                float(mult), float(size[0]), float(size[1])
            )
            er_core.er_bind_light(light)
            er_core.er_tracer_add_light(self.tracer, light)
            self.lights.append(light)

        # Key Light
        add_light(key_pos, key_dir, key_color, key_mult, key_size)
        # Fill Light
        add_light(fill_pos, fill_dir, fill_color, fill_mult, fill_size)
        # Rim Light
        add_light(rim_pos, rim_dir, rim_color, rim_mult, rim_size)

    def set_camera(self, pos, target, up, fov, clip_near, clip_far, exposure, gamma):
        er_core.er_set_camera(self.tracer, 
            float(pos[0]), float(pos[1]), float(pos[2]),
            float(target[0]), float(target[1]), float(target[2]),
            float(up[0]), float(up[1]), float(up[2]),
            float(fov), float(clip_near), float(clip_far),
            float(exposure), float(gamma))

    def set_resolution(self, w, h):
        er_core.er_set_tracer_resolution(self.tracer, int(w), int(h))

    def update_opacity_tf(self, points):
        er_core.er_clear_opacity_tf(self.tracer)
        for val, op in points:
            er_core.er_add_opacity_node(self.tracer, float(val), float(op))

    def update_diffuse_tf(self, points):
        er_core.er_clear_diffuse_tf(self.tracer)
        for val, r, g, b in points:
            er_core.er_add_diffuse_node(self.tracer, float(val), float(r), float(g), float(b))

    def bind_tracer(self):
        er_core.er_bind_tracer(self.tracer)
        
    def get_tracer_id(self):
        return er_core.er_get_tracer_id(self.tracer)

    def reset_accumulation(self):
        er_core.er_reset_accumulation(self.tracer)
        
    def render_estimate(self, tracer_id):
        er_core.er_render_estimate(tracer_id)

    def get_estimate(self, tracer_id, buffer_ptr):
        er_core.er_get_estimate(tracer_id, buffer_ptr)
    def bind_volume_data(self, dim, spacing, data_ptr):
        er_core.er_bind_volume_data(
            self.volume,
            int(dim[0]), int(dim[1]), int(dim[2]),
            float(spacing[0]), float(spacing[1]), float(spacing[2]),
            data_ptr
        )
        er_core.er_bind_volume(self.volume)

import vtk
from PySide6 import QtCore, QtWidgets
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

def qt_message_handler(mode, context, message):
    if "invalid metadata" in message.lower() or "unexpected metadata" in message.lower():
        return
    sys.stderr.write(f"{message}\n")

QtCore.qInstallMessageHandler(qt_message_handler)

from PyCt6 import (
    CButton, CLabel, CLineEdit, CTextEdit, CComboBox, CSlider, CFrame,
    set_appearance_mode, set_color_theme
)
from PySide6.QtGui import (QPainter, QColor, QIcon, QImage, QPixmap, QPen,
                           QPainterPath, QLinearGradient, QRadialGradient,
                           QPolygonF, QBrush)

try:
    import pacs_client          # 同目录：PACS 客户端（C-ECHO / C-FIND / C-GET）
except Exception:  # noqa: BLE001
    pacs_client = None

try:
    import deepseek_client      # 同目录：DeepSeek API 客户端
except Exception:  # noqa: BLE001
    deepseek_client = None

import SimpleITK as sitk
import tempfile
from vtkmodules.util import numpy_support
import numpy as np


class _RenderCancelled(Exception):
    """用户选择「终止渲染」时，从 load_dicom 的取消检查点抛出。"""

HAS_MC_RDENOISER = False

ROI_REPLACEMENT_HU = 4096
ROI_REPLACEMENT_COLOR = (0.15, 1.0, 0.18)

# 最近一次 build_reader 读到的模态 / 是否 2D 单幅（DR 平片），
# 供 ViewerWindow.load_dicom 分流阅片显示（CT 用 HU 窗，MR/DR 用自适应归一化）。
_LAST_LOAD_MODALITY = "CT"
_LAST_LOAD_IS_2D = False


def detect_modality(dicom_path: str) -> str:
    """从 DICOM 头（0008|0060 Modality）读取模态；失败/未知返回空串。"""
    try:
        if os.path.isdir(dicom_path):
            names = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(dicom_path)
            if not names:
                return ""
            probe = sitk.ReadImage(names[0])
        else:
            probe = sitk.ReadImage(dicom_path)
        if probe.HasMetaDataKey("0008|0060"):
            return (probe.GetMetaData("0008|0060") or "").strip().upper()
    except Exception:
        pass
    return ""


ROI_RESULT_BED_OFFSET_MM = 0.0

def _compute_frangi_from_hessian(Hxx, Hyy, Hzz, Hxy, Hxz, Hyz,
                                  alpha=1.0, beta=0.5, gamma=10.0, dark_ridges=True):
    shape = Hxx.shape
    n_total = Hxx.size
    H = np.empty((n_total, 3, 3), dtype=np.float32)
    H[:, 0, 0] = Hxx.ravel()
    H[:, 0, 1] = Hxy.ravel()
    H[:, 0, 2] = Hxz.ravel()
    H[:, 1, 0] = Hxy.ravel()
    H[:, 1, 1] = Hyy.ravel()
    H[:, 1, 2] = Hyz.ravel()
    H[:, 2, 0] = Hxz.ravel()
    H[:, 2, 1] = Hyz.ravel()
    H[:, 2, 2] = Hzz.ravel()
    eigvals = np.linalg.eigvalsh(H)
    if dark_ridges:
        eigvals = -eigvals
    lam1 = eigvals[:, 0]
    lam2 = eigvals[:, 1]
    lam3 = eigvals[:, 2]

    v = np.zeros(n_total, dtype=np.float32)
    mask = (lam2 < 0) | (lam3 < 0)
    idx = np.where(mask)[0]

    if len(idx) == 0:
        return np.zeros(shape, dtype=np.float32)

    Ra = np.abs(lam2[idx]) / np.maximum(1e-12, np.abs(lam3[idx]))
    Rb = np.abs(lam1[idx]) / np.maximum(1e-12, np.sqrt(np.abs(lam2[idx] * lam3[idx])))
    S = np.sqrt(lam1[idx]**2 + lam2[idx]**2 + lam3[idx]**2)

    vesselness = np.exp(-Ra * Ra / (2.0 * alpha * alpha)) * \
                 (1.0 - np.exp(-Rb * Rb / (2.0 * beta * beta))) * \
                 (1.0 - np.exp(-S * S / (2.0 * gamma * gamma)))

    v[idx] = vesselness
    return v.reshape(shape).astype(np.float32)

def _build_2d_tf_lut(lut_size: int = 256, gm_max: float = 200.0) -> np.ndarray:
    hu_min, hu_max = -1000.0, 3000.0
    lut = np.zeros((lut_size, lut_size), dtype=np.float32)
    hu_grid, gm_grid = np.meshgrid(
        np.linspace(hu_min, hu_max, lut_size),
        np.linspace(0, gm_max, lut_size)
    )
    def gauss(hu_c, gm_c, hu_s, gm_s):
        return np.exp(-((hu_grid - hu_c) / hu_s) ** 2 - ((gm_grid - gm_c) / gm_s) ** 2)
    gm_scale = gm_max / 200.0
    lut += gauss(800, 100 * gm_scale, 400, 80 * gm_scale) * 1.00
    lut += gauss(700, 20 * gm_scale, 500, 30 * gm_scale) * 0.60
    lut += gauss(50, 10 * gm_scale, 150, 25 * gm_scale) * 0.80
    lut += gauss(300, 50 * gm_scale, 200, 60 * gm_scale) * 0.70
    lut += gauss(100, 30 * gm_scale, 120, 40 * gm_scale) * 0.50
    return np.clip(lut, 0.0, 1.0)

def _build_2d_tf_lut_bone_mono(lut_size: int = 256, gm_max: float = 200.0) -> np.ndarray:
    hu_min, hu_max = -1000.0, 3000.0
    lut = np.zeros((lut_size, lut_size), dtype=np.float32)
    hu_grid, gm_grid = np.meshgrid(
        np.linspace(hu_min, hu_max, lut_size),
        np.linspace(0, gm_max, lut_size)
    )
    def gauss(hu_c, gm_c, hu_s, gm_s):
        return np.exp(-((hu_grid - hu_c) / hu_s) ** 2 - ((gm_grid - gm_c) / gm_s) ** 2)
    gm_scale = gm_max / 200.0
    lut += gauss(900, 120 * gm_scale, 350, 90 * gm_scale) * 1.00
    lut += gauss(750, 15 * gm_scale, 450, 25 * gm_scale) * 0.65
    lut += gauss(30, 8 * gm_scale, 140, 20 * gm_scale) * 0.85
    lut += gauss(280, 45 * gm_scale, 180, 55 * gm_scale) * 0.75
    lut += gauss(120, 28 * gm_scale, 110, 35 * gm_scale) * 0.55
    return np.clip(lut, 0.0, 1.0)

def _dicom_rc(fp: str):
    """读取 DICOM 的 (Rows, Columns)；非 DICOM/失败返回 None。"""
    try:
        import pydicom
        ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
        if hasattr(ds, "Rows") and hasattr(ds, "Columns"):
            return (int(ds.Rows), int(ds.Columns))
    except Exception:  # noqa: BLE001
        pass
    return None


def pick_series_files(root: str) -> list:
    """从目录里挑出一组**尺寸一致**的 DICOM 文件，避免 ITK "Size mismatch"。

    目录常混放多个序列或不同尺寸的实例（如定位像 + 薄层）。ITK 的
    GetGDCMSeriesFileNames(不带 UID) 会把它们混在一起，Execute 直接报
    "Size mismatch"。策略：
      1) 按 SeriesInstanceUID 取**文件数最多**的系列（ITK 已按空间位置排序）；
      2) 在该系列内按 (Rows, Columns) 取**最大的一致子集**；
      3) 都没有则回退为目录内全部文件。
    """
    best: list = []
    try:
        ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(root) or []
        for uid in ids:
            names = list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(root, uid))
            if len(names) > len(best):
                best = names
    except Exception:  # noqa: BLE001
        best = []
    if not best:
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if not f.startswith("."):
                    best.append(os.path.join(dp, f))
    if not best:
        return []
    groups: dict = {}
    for fp in best:
        groups.setdefault(_dicom_rc(fp), []).append(fp)
    groups.pop(None, None)
    if not groups:
        return best
    chosen = max(groups.values(), key=len)
    return chosen


def build_reader(dicom_path: str, denoise_method: str = "gaussian", use_clahe: bool = False, use_frangi: bool = False, use_distance_field: bool = False, use_2d_tf: bool = False, use_2d_tf_bone: bool = False, vram_threshold_gb: float = 10.0, cpu_render: bool = False) -> Tuple[vtk.vtkImageData, str]:
    if os.path.isdir(dicom_path):
        dicom_names = pick_series_files(dicom_path)
        if not dicom_names:
            raise RuntimeError("在目录下未找到 DICOM 序列。")
        print(f"[PACS/DICOM] 目录含多序列/多尺寸时自动择一：选用 {len(dicom_names)} 个文件",
              flush=True)
        reader = sitk.ImageSeriesReader()
        reader.SetFileNames(dicom_names)
        try:
            image = reader.Execute()
        except RuntimeError as _e:
            # 兜底：逐个 SeriesInstanceUID 单独读，取第一个成功的
            print(f"[PACS/DICOM] 合并读取失败（{_e}），尝试逐个系列读取…", flush=True)
            image = None
            try:
                uids = list(sitk.ImageSeriesReader.GetGDCMSeriesIDs(dicom_path) or [])
            except Exception:  # noqa: BLE001
                uids = []
            for uid in uids:
                try:
                    names = list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(dicom_path, uid))
                    if not names:
                        continue
                    r2 = sitk.ImageSeriesReader()
                    r2.SetFileNames(names)
                    image = r2.Execute()
                    print(f"[PACS/DICOM] 已选用系列 {uid}（{len(names)} 张）", flush=True)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if image is None:
                raise _e
    else:
        image = sitk.ReadImage(dicom_path)

    # 模态识别 + 2D 判定（供 load_dicom 分流：CT 走 HU 窗，MR/DR 走自适应灰度）
    global _LAST_LOAD_MODALITY, _LAST_LOAD_IS_2D
    _LAST_LOAD_MODALITY = detect_modality(dicom_path) or "CT"
    _LAST_LOAD_IS_2D = (image.GetDimension() < 3) or (
        image.GetDimension() >= 3 and image.GetSize()[2] <= 1)

    # 如果是 4D 数据，提取第一个时间阶段的 3D 数据
    if image.GetDimension() > 3:
        if image.GetDimension() == 4:
            image = image[:, :, :, 0]

    # [显存保护机制] 估算显存占用并自动下采样
    size = image.GetSize()
    num_voxels = 1
    for s in size:
        num_voxels *= s

    # 估算显存：2 bytes/voxel * 2.5倍 VTK底层占用膨胀系数
    estimated_vram_gb = (num_voxels * 2 * 2.5) / (1024**3)
    downsample_msg = ""
    need_downsample = estimated_vram_gb > vram_threshold_gb
    if cpu_render:
        print(f"[CPU Render] 跳过下采样，保留全分辨率: 体积 {num_voxels/1e6:.1f}M 体素, 预估 VRAM {estimated_vram_gb:.1f}GB (CPU 端使用系统 RAM)")
        need_downsample = False
    if (use_2d_tf or use_2d_tf_bone) and num_voxels > 200_000_000:
        print(f"[2D TF / Bone Mono] 体积 {num_voxels/1e6:.0f}M > 200M 体素, 触发自动下采样 (1024³→512³)")
        need_downsample = True
    if num_voxels > 500_000_000 and not need_downsample and not cpu_render:
        print(f"[Volume Guard] 体积 {num_voxels/1e6:.0f}M > 500M 体素, GPU 可能无法分配 3D 纹理, 触发下采样")
        need_downsample = True
    if need_downsample:
        msg1 = f"[Memory Protect] Estimated VRAM {estimated_vram_gb:.1f}GB exceeds {vram_threshold_gb:.0f}GB limit, auto-downsampling..."
        print(msg1)
        downsample_msg += msg1 + "\n"
        max_dim = max(size[0], size[1])
        target = 1024 if max_dim >= 2048 else 512
        factor = max(size[0] / target, size[1] / target)
        if factor > 1.0:
            new_size = list(size)
            new_spacing = list(image.GetSpacing())
            new_size[0] = max(target, int(size[0] / factor))
            new_size[1] = max(target, int(size[1] / factor))
            if len(size) >= 3:
                new_size[2] = max(1, int(size[2] / factor))

            for i in range(len(new_size)):
                new_spacing[i] = image.GetSpacing()[i] * (size[i] / new_size[i])

            resampler = sitk.ResampleImageFilter()
            resampler.SetSize(new_size)
            resampler.SetOutputSpacing(new_spacing)
            resampler.SetOutputOrigin(image.GetOrigin())
            resampler.SetOutputDirection(image.GetDirection())
            resampler.SetInterpolator(sitk.sitkLinear)
            resampler.SetDefaultPixelValue(-1000)
            image = resampler.Execute(image)
            msg2 = f"[Memory Protect] Downsample completed: {size} -> {image.GetSize()}"
            print(msg2)
            downsample_msg += msg2

    # 保证 2D/3D 数据提取为 3D 数组(ZYX) 传入 VTK
    nda = sitk.GetArrayFromImage(image)
    while nda.ndim > 3:
        nda = nda[0]
    if nda.ndim == 2:
        nda = nda[np.newaxis, ...]
    nda = np.ascontiguousarray(nda.astype(np.int16, copy=False))

    import scipy.ndimage as ndimage
    if denoise_method == "nlm":
        try:
            from skimage.restoration import denoise_nl_means
            print("应用 3D Non-Local Means 降噪 (patch=5, distance=7) 保留微通道边缘...")
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()
            except Exception:
                pass
            est_sigma = max(1.0, float(np.std(nda)) * 0.4)
            nda_f32 = nda.astype(np.float32)
            n_vox = int(nda_f32.size)
            if n_vox > 80_000_000:
                import time
                nz = nda_f32.shape[0]
                chunk_sz = int(max(8, min(64, vram_threshold_gb * 4)))
                if n_vox > 200_000_000:
                    chunk_sz = max(8, chunk_sz // 2)
                total_chunks = (nz + chunk_sz - 1) // chunk_sz
                nlm_patch = 3
                nlm_dist = 5
                nlm_pad = nlm_patch + nlm_dist
                print(f"  NLM 体积 {n_vox/1e6:.1f}M > 80M 体素, VRAM={vram_threshold_gb:.0f}GB→chunk={chunk_sz}, patch={nlm_patch}, dist={nlm_dist}, {total_chunks} chunks")
                t_start = time.time()
                for ci, z0 in enumerate(range(0, nz, chunk_sz)):
                    z1 = min(z0 + chunk_sz + nlm_pad, nz)
                    chunk = nda_f32[z0:z1, :, :].copy()
                    pad_trim = 0 if z0 == 0 else min(nlm_pad, z1 - z0)
                    denoised = denoise_nl_means(chunk, patch_size=nlm_patch, patch_distance=nlm_dist,
                                                h=est_sigma, fast_mode=True, preserve_range=True,
                                                channel_axis=None)
                    write_sz = min(chunk_sz, nz - z0)
                    end_trim = min(pad_trim + write_sz, denoised.shape[0])
                    nda[z0:z0 + write_sz] = denoised[pad_trim:end_trim, :, :].astype(np.int16)
                    elapsed = time.time() - t_start
                    eta = elapsed / (ci + 1) * (total_chunks - ci - 1)
                    print(f"  NLM chunk {ci+1}/{total_chunks} [{z0}:{z0+write_sz}] 耗时 {elapsed:.0f}s, ETA {eta:.0f}s")
                    try:
                        from PySide6.QtWidgets import QApplication
                        QApplication.processEvents()
                    except Exception:
                        pass
                nda = nda.astype(np.int16)
                print(f"  NLM 完成, 总耗时 {time.time()-t_start:.0f}s")
                del nda_f32
            else:
                nda_f32 = denoise_nl_means(nda_f32, patch_size=5, patch_distance=7,
                                           h=est_sigma, fast_mode=True, preserve_range=True,
                                           channel_axis=None).astype(np.int16)
                nda = nda_f32
                del nda_f32
        except Exception as e:
            print(f"NLM 降噪不可用 ({e})，回退至高斯滤波。")
            nda = ndimage.gaussian_filter(nda, sigma=0.4)
    else:
        print("应用 3D 高斯平滑滤波 (Sigma=0.4) 保留更多表面细节...")
        nda = ndimage.gaussian_filter(nda, sigma=0.4)

    if use_clahe:
        try:
            from skimage import exposure
            print("应用 3D CLAHE 局部对比度增强 (kernel=64, clip=0.02)...")
            nda_f32 = nda.astype(np.float32)
            nda_min, nda_max = nda_f32.min(), nda_f32.max()
            nda_norm = (nda_f32 - nda_min) / max(1e-6, nda_max - nda_min)
            nda_eq = np.empty_like(nda_norm)
            for z in range(nda_norm.shape[0]):
                nda_eq[z] = exposure.equalize_adapthist(nda_norm[z], kernel_size=64, clip_limit=0.02)
            nda = (nda_eq * (nda_max - nda_min) + nda_min).astype(np.int16)
            print("CLAHE 增强完成。")
        except Exception as e:
            print(f"CLAHE 增强失败 ({e})，继续使用原始数据。")

    if use_frangi:
        try:
            from skimage.filters import frangi as frangi_cpu
            use_gpu = False
            gpu_fail_reason = ""
            try:
                import cupy as cp
                from cupyx.scipy.ndimage import gaussian_filter as cp_gaussian_filter
                cp_test = cp.array([1.0])
                del cp_test
                gpu_mem = cp.cuda.runtime.memGetInfo()
                gpu_mem_gb = gpu_mem[0] / (1024**3)
                use_gpu = True
                print(f"CuPy GPU 可用: 空闲显存 {gpu_mem_gb:.1f} GB")
            except ImportError as e:
                gpu_fail_reason = f"CuPy 未安装或不可导入: {e}"
            except Exception as e:
                gpu_fail_reason = f"CuPy 初始化失败: {e}"

            if not use_gpu:
                print(f"GPU 不可用 ({gpu_fail_reason})，使用 CPU 计算 Frangi...")

            spacing = image.GetSpacing()
            min_sp = float(min(spacing))
            if min_sp <= 0.001:
                min_sp = 1.0
            sigmas_mm = [0.2, 0.3, 0.5, 0.8]
            sigmas_px = [s / min_sp for s in sigmas_mm]
            sigmas_px = [s for s in sigmas_px if s >= 0.6]
            if not sigmas_px:
                sigmas_px = [0.6]

            print(f"计算 3D Frangi Vesselness (dark tube, sigma_mm={sigmas_mm}, sigma_px={[f'{s:.1f}' for s in sigmas_px]}, GPU={use_gpu}) ...")

            if use_gpu:
                nda_f32 = nda.astype(np.float32)
                orig_shape = nda_f32.shape
                n_vox = orig_shape[0] * orig_shape[1] * orig_shape[2]
                vesselness_cpu = np.zeros(orig_shape, dtype=np.float32)

                if n_vox > 80_000_000:
                    chunk_size = max(16, orig_shape[0] // 6)
                else:
                    chunk_size = orig_shape[0]

                for sigma_px in sigmas_px:
                    print(f"  Frangi σ={sigma_px:.1f}px (GPU) ...")

                    for z_start in range(0, orig_shape[0], chunk_size):
                        z_end = min(z_start + chunk_size, orig_shape[0])
                        pad_before = int(sigma_px * 4)
                        pad_after = int(sigma_px * 4)
                        z0 = max(0, z_start - pad_before)
                        z1 = min(orig_shape[0], z_end + pad_after)

                        chunk = cp.asarray(nda_f32[z0:z1, :, :].copy(), dtype=cp.float32)
                        chunk_sz = z1 - z0
                        actual_start = z_start - z0
                        actual_end = z_end - z0

                        smoothed = cp_gaussian_filter(chunk, sigma_px, mode='reflect')
                        Dxx = cp_gaussian_filter(smoothed, sigma_px, order=(2, 0, 0), mode='reflect')
                        Dyy = cp_gaussian_filter(smoothed, sigma_px, order=(0, 2, 0), mode='reflect')
                        Dzz = cp_gaussian_filter(smoothed, sigma_px, order=(0, 0, 2), mode='reflect')
                        Dxy = cp_gaussian_filter(smoothed, sigma_px, order=(1, 1, 0), mode='reflect')
                        Dxz = cp_gaussian_filter(smoothed, sigma_px, order=(1, 0, 1), mode='reflect')
                        Dyz = cp_gaussian_filter(smoothed, sigma_px, order=(0, 1, 1), mode='reflect')

                        Hxx = cp.asnumpy(Dxx[actual_start:actual_end, :, :])
                        Hyy = cp.asnumpy(Dyy[actual_start:actual_end, :, :])
                        Hzz = cp.asnumpy(Dzz[actual_start:actual_end, :, :])
                        Hxy = cp.asnumpy(Dxy[actual_start:actual_end, :, :])
                        Hxz = cp.asnumpy(Dxz[actual_start:actual_end, :, :])
                        Hyz = cp.asnumpy(Dyz[actual_start:actual_end, :, :])

                        del smoothed, Dxx, Dyy, Dzz, Dxy, Dxz, Dyz, chunk
                        cp.get_default_memory_pool().free_all_blocks()

                        v = _compute_frangi_from_hessian(
                            Hxx, Hyy, Hzz, Hxy, Hxz, Hyz,
                            alpha=1.0, beta=0.5, gamma=10.0, dark_ridges=True
                        )
                        vesselness_cpu[z_start:z_end, :, :] = np.maximum(
                            vesselness_cpu[z_start:z_end, :, :], v
                        )
                        try:
                            from PySide6.QtWidgets import QApplication
                            QApplication.processEvents()
                        except Exception:
                            pass
                vesselness = vesselness_cpu.astype(np.float32)
            else:
                nda_f32 = nda.astype(np.float32)
                vox_count = nda_f32.size
                est_mem_gb = vox_count * 4 * 3 / (1024**3)
                print(f"  Frangi CPU 模式: {sigmas_px} 尺度, 体积 {vox_count/1e6:.1f}M 体素")
                print(f"  预估 CPU 内存占用 ~{est_mem_gb:.1f} GB (FP32)")
                if vox_count > 80_000_000:
                    print(f"  体积 > 80M 体素, 使用 Z轴分块 Frangi (chunk=64 slices, 96GB RAM safe)...")
                    import time
                    vesselness = np.zeros(nda_f32.shape, dtype=np.float32)
                    nz = nda_f32.shape[0]
                    chunk_sz = 64
                    for z0 in range(0, nz, chunk_sz):
                        z1 = min(z0 + chunk_sz + 16, nz)
                        chunk = nda_f32[z0:z1, :, :].copy()
                        pad = min(8, z1 - z0)
                        for i, sigma_px in enumerate(sigmas_px):
                            t0 = time.time()
                            v = frangi_cpu(chunk.astype(np.float64), sigmas=[sigma_px],
                                           alpha=1.0, beta=0.5, gamma=10.0,
                                           black_ridges=True, mode='reflect')
                            write_sz = min(chunk_sz, nz - z0)
                            v_slice = v[pad:pad + write_sz, :, :].astype(np.float32)
                            vesselness[z0:z0 + write_sz, :, :] = np.maximum(
                                vesselness[z0:z0 + write_sz, :, :], v_slice)
                            print(f"  Frangi σ={sigma_px:.1f}px Z[{z0}:{z0+write_sz}] 完成, {time.time()-t0:.1f}s")
                        try:
                            from PySide6.QtWidgets import QApplication
                            QApplication.processEvents()
                        except Exception:
                            pass
                else:
                    vesselness = np.zeros(nda_f32.shape, dtype=np.float32)
                    for i, sigma_px in enumerate(sigmas_px):
                        print(f"  Frangi σ={sigma_px:.1f}px ({i+1}/{len(sigmas_px)}) (CPU) ...")
                        try:
                            from PySide6.QtWidgets import QApplication
                            QApplication.processEvents()
                        except Exception:
                            pass
                        import time
                        t0 = time.time()
                        v = frangi_cpu(nda_f32.astype(np.float64), sigmas=[sigma_px],
                                       alpha=1.0, beta=0.5, gamma=10.0,
                                       black_ridges=True, mode='reflect')
                        vesselness = np.maximum(vesselness, v.astype(np.float32))
                        print(f"    完成, 耗时 {time.time()-t0:.1f}s")

            print(f"Frangi 完成: vesselness range=[{vesselness.min():.4f}, {vesselness.max():.4f}]")
            boost_offset = np.int16(1200)
            vessel_mask = vesselness > 0.12
            nda_mod = nda.astype(np.int32)
            nda_mod[vessel_mask] += boost_offset
            nda = np.clip(nda_mod, -1000, 3000).astype(np.int16)
            n_voxels = int(vessel_mask.sum())
            print(f"Frangi HU 偏移完成: {n_voxels} 体素标记为微通道候选 (boost +{boost_offset} HU)")
        except Exception as e:
            import traceback
            print(f"Frangi 计算失败 ({e}):")
            traceback.print_exc()

    if use_distance_field:
        try:
            from scipy.ndimage import distance_transform_edt
            import time
            spacing = image.GetSpacing()
            sx, sy, sz = float(spacing[0]), float(spacing[1]), float(spacing[2])
            dist_decay_start_mm = 1.0
            dist_decay_end_mm = 4.0
            bone_threshold = 300
            print(f"计算距离场骨膜聚合 (骨阈值>{bone_threshold}HU, 衰减 {dist_decay_start_mm}-{dist_decay_end_mm}mm)...")
            t0 = time.time()
            bone_mask = (nda > bone_threshold).astype(np.uint8)
            dist_vx = distance_transform_edt(1 - bone_mask, sampling=(sx, sy, sz))
            decay = np.clip((dist_vx - dist_decay_start_mm) / (dist_decay_end_mm - dist_decay_start_mm), 0.0, 1.0)
            nda_f32 = nda.astype(np.float32)
            air_val = np.float32(-1000)
            soft_mask = nda_f32 < bone_threshold
            nda_f32[soft_mask] = nda_f32[soft_mask] * (1.0 - decay[soft_mask]) + air_val * decay[soft_mask]
            nda = np.clip(nda_f32, -1000, 3000).astype(np.int16)
            kept_vox = int((decay < 0.99).sum())
            print(f"  距离场完成, {time.time()-t0:.1f}s. 骨膜保留体素 {kept_vox/1e6:.1f}M (距离<{dist_decay_end_mm}mm)")
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()
            except Exception:
                pass
        except Exception as e:
            import traceback
            print(f"距离场计算失败 ({e}):")
            traceback.print_exc()

    if use_2d_tf:
        try:
            from scipy.ndimage import sobel
            import time, sys
            lut_mode = "Bone Monochrome" if use_2d_tf_bone else "Kniss 2001"
            n_vox = nda.size
            if n_vox > 200_000_000:
                print(f"  2D TF 预处理: 体积 {n_vox/1e6:.0f}M > 200M 体素, 预计内存消耗 ~{n_vox*4*4/1e9:.1f}GB, 耗时数分钟...")
                sys.stdout.flush()
            print(f"计算 2D TF (HU × Gradient Magnitude) {lut_mode} 风格预处理...")
            sys.stdout.flush()
            t0 = time.time()
            nda_f32 = nda.astype(np.float32)
            gx = sobel(nda_f32, axis=0); sys.stdout.flush()
            gy = sobel(nda_f32, axis=1); sys.stdout.flush()
            gz = sobel(nda_f32, axis=2); sys.stdout.flush()
            gm = np.sqrt(gx**2 + gy**2 + gz**2)
            del gx, gy, gz
            print(f"  梯度幅值完成 ({time.time()-t0:.1f}s), GM range=[{gm.min():.1f}, {gm.max():.1f}]")
            sys.stdout.flush()
            if gm.size > 50_000_000:
                sample = np.random.choice(gm.ravel(), min(500_000, gm.size), replace=False)
                gm_max_lut = max(200.0, float(np.percentile(sample, 95)))
            else:
                gm_max_lut = max(200.0, float(np.percentile(gm, 95)))
            print(f"  gm_max_lut adjusted to P95 = {gm_max_lut:.0f}")
            sys.stdout.flush()
            lut_2d = _build_2d_tf_lut_bone_mono(256, gm_max_lut) if use_2d_tf_bone else _build_2d_tf_lut(256, gm_max_lut)
            hu_min, hu_max = -1000.0, 3000.0
            hu_idx = np.clip(((nda_f32 - hu_min) / (hu_max - hu_min) * 255).astype(np.int32), 0, 255)
            gm_idx = np.clip(((gm / gm_max_lut) * 255).astype(np.int32), 0, 255)
            alpha_2d = lut_2d[gm_idx, hu_idx]
            print(f"  2D TF 查询完成 ({time.time()-t0:.1f}s), 可见体素>0.05={(alpha_2d>0.05).sum()/1e6:.1f}M")
            sys.stdout.flush()
            nda_mod = nda.astype(np.float32)
            fade_mask = alpha_2d < 0.05
            nda_mod[fade_mask] = -1000
            fade_smooth = np.clip((0.10 - alpha_2d) / 0.10, 0, 1)
            mid_mask = (alpha_2d >= 0.05) & (alpha_2d < 0.15)
            nda_mod[mid_mask] = nda_mod[mid_mask] * (1 - fade_smooth[mid_mask]) - 1000 * fade_smooth[mid_mask]
            nda = np.clip(nda_mod, -1000, 3000).astype(np.int16)
            surv = (nda > -900).sum()
            del nda_f32, alpha_2d, nda_mod, gm, fade_mask, mid_mask, fade_smooth
            print(f"  2D TF 体积重映射完成, 总耗时 {time.time()-t0:.1f}s")
            print(f"  Surviving voxels > -900 HU: {surv/1e6:.1f}M / {nda.size/1e6:.1f}M ({surv/nda.size*100:.1f}%)")
            try:
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                fig, ax = plt.subplots(figsize=(6, 5))
                im = ax.imshow(lut_2d, origin='lower', extent=[hu_min, hu_max, 0, gm_max_lut],
                               aspect='auto', cmap='gray_r')
                ax.set_xlabel('HU'); ax.set_ylabel('Gradient Magnitude')
                ax.set_title('2D TF LUT (Kniss 2001)')
                plt.colorbar(im, label='Opacity')
                lut_path = os.path.join(tempfile.gettempdir(), 'ssd_vr_2dtf_lut.png')
                fig.savefig(lut_path, dpi=100, bbox_inches='tight')
                plt.close(fig)
                print(f"  2D TF LUT 可视化保存至 {lut_path}")
            except Exception:
                pass
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()
            except Exception:
                pass
        except Exception as e:
            import traceback
            print(f"2D TF 计算失败 ({e}):")
            traceback.print_exc()

    vtk_data = numpy_support.numpy_to_vtk(
        num_array=nda.ravel(order="C"), deep=True, array_type=vtk.VTK_SHORT     
    )

    final_size = list(image.GetSize())
    spacing = list(image.GetSpacing())
    origin = list(image.GetOrigin())
    if len(final_size) > 3:
        final_size = final_size[:3]
        spacing = spacing[:3]
        origin = origin[:3]
    elif len(final_size) == 2:
        final_size = [final_size[0], final_size[1], 1]
        spacing = [spacing[0], spacing[1], 1.0]
        origin = [origin[0], origin[1], 0.0]

    img_vtk = vtk.vtkImageData()
    img_vtk.SetDimensions(int(final_size[0]), int(final_size[1]), int(final_size[2]))
    img_vtk.SetSpacing(float(spacing[0]), float(spacing[1]), float(spacing[2])) 
    img_vtk.SetOrigin(float(origin[0]), float(origin[1]), float(origin[2]))     
    if hasattr(img_vtk, "SetDirectionMatrix") and image.GetDimension() >= 3:
        direction = image.GetDirection()
        direction_matrix = vtk.vtkMatrix3x3()
        for row in range(3):
            for col in range(3):
                direction_matrix.SetElement(row, col, float(direction[row * 3 + col]))
        img_vtk.SetDirectionMatrix(direction_matrix)
    img_vtk.GetPointData().SetScalars(vtk_data)

    return img_vtk, downsample_msg


def make_opacity(points: List[Tuple[float, float]], scale: float = 1.0) -> vtk.vtkPiecewiseFunction:
    otf = vtk.vtkPiecewiseFunction()
    for x, y in points:
        otf.AddPoint(x, max(0.0, min(1.0, y * scale)))
    return otf


def make_color(points: List[Tuple[float, float, float, float]]) -> vtk.vtkColorTransferFunction:
    ctf = vtk.vtkColorTransferFunction()
    for x, r, g, b in points:
        ctf.AddRGBPoint(x, r, g, b)
    return ctf


def interp_piecewise(points: List[Tuple[float, float]], x: float) -> float:
    if not points:
        return 0.0
    if x <= points[0][0]:
        return points[0][1]
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return y0 * (1.0 - t) + y1 * t
    return points[-1][1]


def interp_color(points: List[Tuple[float, float, float, float]], x: float) -> Tuple[float, float, float]:
    if not points:
        return 0.0, 0.0, 0.0
    if x <= points[0][0]:
        return points[0][1], points[0][2], points[0][3]
    for i in range(1, len(points)):
        x0, r0, g0, b0 = points[i - 1]
        x1, r1, g1, b1 = points[i]
        if x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return (
                r0 * (1.0 - t) + r1 * t,
                g0 * (1.0 - t) + g1 * t,
                b0 * (1.0 - t) + b1 * t,
            )
    return points[-1][1], points[-1][2], points[-1][3]


class FusionController:
    def __init__(
        self,
        ssd_volume: Optional[vtk.vtkVolume],
        vr_volume: Optional[vtk.vtkVolume],
        ssd_points: List[Tuple[float, float]],
        vr_points: List[Tuple[float, float]],
        ssd_color_points: List[Tuple[float, float, float, float]],
        vr_color_points: List[Tuple[float, float, float, float]],
        fused_volume: Optional[vtk.vtkVolume] = None,
    ) -> None:
        self.ssd_volume = ssd_volume
        self.vr_volume = vr_volume
        self.fused_volume = fused_volume
        self.ssd_points = ssd_points
        self.vr_points = vr_points
        self.ssd_color_points = ssd_color_points
        self.vr_color_points = vr_color_points

    def update(self, ssd_scale: float, vr_scale: float, exposure: float = 1.0) -> None:
        if self.fused_volume is not None:
            op, col = self._build_fused_transfer(ssd_scale, vr_scale, exposure)
            prop = self.fused_volume.GetProperty()
            prop.SetScalarOpacity(op)
            prop.SetColor(col)
            return
        if self.ssd_volume is not None:
            self.ssd_volume.GetProperty().SetScalarOpacity(make_opacity(self.ssd_points, ssd_scale))
        if self.vr_volume is not None:
            self.vr_volume.GetProperty().SetScalarOpacity(make_opacity(self.vr_points, vr_scale))

    def _build_fused_transfer(
        self, ssd_scale: float, vr_scale: float, exposure: float
    ) -> Tuple[vtk.vtkPiecewiseFunction, vtk.vtkColorTransferFunction]:
        # 参考 Exposure Render：两类体材质在同一体积内融合，再做指数色调映射近似
        hu_samples = list(range(-1000, 3001, 20))
        otf = vtk.vtkPiecewiseFunction()
        ctf = vtk.vtkColorTransferFunction()
        # tonemap.cuh: c_out = 1 - exp(-c_in * Exposure)
        for hu in hu_samples:
            a_ssd = max(0.0, min(1.0, interp_piecewise(self.ssd_points, hu) * ssd_scale))
            a_vr = max(0.0, min(1.0, interp_piecewise(self.vr_points, hu) * vr_scale))
            a_mix = 1.0 - (1.0 - a_ssd) * (1.0 - a_vr)
            r1, g1, b1 = interp_color(self.ssd_color_points, hu)
            r2, g2, b2 = interp_color(self.vr_color_points, hu)
            w = a_ssd + a_vr
            if w > 1e-6:
                r = (r1 * a_ssd + r2 * a_vr) / w
                g = (g1 * a_ssd + g2 * a_vr) / w
                b = (b1 * a_ssd + b2 * a_vr) / w
            else:
                r, g, b = r2, g2, b2
            
            # Apply exposure multiplier
            r = 1.0 - np.exp(-r * exposure)
            g = 1.0 - np.exp(-g * exposure)
            b = 1.0 - np.exp(-b * exposure)
            otf.AddPoint(float(hu), float(a_mix))
            ctf.AddRGBPoint(float(hu), float(r), float(g), float(b))
        return otf, ctf


class RangeSlider(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minimum = -1000
        self.maximum = 3000
        self._lower = 300
        self._upper = 1500
        self._handle_radius = 8
        self._active_handle = None
        self._drag_offset = 0
        self.setFixedHeight(30)
        
    def setRange(self, min_val, max_val):
        self.minimum = min_val
        self.maximum = max_val
        self.update()
        
    def values(self):
        """当前区间 (lower, upper)。"""
        return (int(self._lower), int(self._upper))

    def setValues(self, lower, upper):
        self._lower = max(self.minimum, min(lower, self.maximum))
        self._upper = max(self.minimum, min(upper, self.maximum))
        self.update()
        self.valueChanged.emit(int(self._lower), int(self._upper))
        
    def blockSignals(self, b):
        return super().blockSignals(b)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        r = self._handle_radius
        
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QColor(236, 222, 202))     # 浅色主题：轨道暖灰
        painter.drawRoundedRect(r, h//2 - 2, w - 2*r, 4, 2, 2)
        
        lx = self._val_to_pos(self._lower)
        ux = self._val_to_pos(self._upper)
        
        painter.setBrush(QColor(240, 140, 30))       # 选中区间：明快橙
        painter.drawRoundedRect(QtCore.QRectF(lx, h//2 - 2, ux - lx, 4), 2, 2)
        
        painter.setPen(QColor(212, 118, 26))         # 手柄描边
        painter.setBrush(QColor(255, 168, 66))       # 手柄：亮橙
        painter.drawEllipse(QtCore.QPointF(lx, h//2), r, r)
        painter.drawEllipse(QtCore.QPointF(ux, h//2), r, r)
        
    def _val_to_pos(self, val):
        w = self.width() - 2 * self._handle_radius
        return self._handle_radius + w * (val - self.minimum) / (self.maximum - self.minimum)
        
    def _pos_to_val(self, x):
        w = self.width() - 2 * self._handle_radius
        val = self.minimum + (x - self._handle_radius) / w * (self.maximum - self.minimum)
        return max(self.minimum, min(self.maximum, val))
        
    def mousePressEvent(self, event):
        x = event.position().toPoint().x()
        lx = self._val_to_pos(self._lower)
        ux = self._val_to_pos(self._upper)
        
        if abs(x - lx) < self._handle_radius * 2:
            self._active_handle = 'lower'
        elif abs(x - ux) < self._handle_radius * 2:
            self._active_handle = 'upper'
        elif lx < x < ux:
            self._active_handle = 'center'
            self._drag_offset = x
            self._drag_lower_start = self._lower
            self._drag_upper_start = self._upper
        else:
            self._active_handle = None

    def mouseMoveEvent(self, event):
        if not self._active_handle:
            return
        x = event.position().toPoint().x()
        val = self._pos_to_val(x)
        
        if self._active_handle == 'lower':
            self._lower = min(val, self._upper - 1)
        elif self._active_handle == 'upper':
            self._upper = max(val, self._lower + 1)
        elif self._active_handle == 'center':
            delta_val = self._pos_to_val(x) - self._pos_to_val(self._drag_offset)
            new_lower = self._drag_lower_start + delta_val
            new_upper = self._drag_upper_start + delta_val
            
            if new_lower < self.minimum:
                diff = self.minimum - new_lower
                new_lower += diff
                new_upper += diff
            elif new_upper > self.maximum:
                diff = new_upper - self.maximum
                new_lower -= diff
                new_upper -= diff
                
            self._lower = new_lower
            self._upper = new_upper
            
        self.update()
        self.valueChanged.emit(int(self._lower), int(self._upper))
        
    def mouseReleaseEvent(self, event):
        self._active_handle = None


class SamSliceLabel(QtWidgets.QLabel):
    """带点击/右键信号的切片视图标签（轴号随事件带出）。

    保持原始像素纵横比显示：图片按 label 大小等比缩放并居中（不拉伸），
    记录实际绘制矩形供点击坐标换算。
    """
    clicked = QtCore.Signal(int, object)  # (axis, {x,y,button}) 单击（延时判定）
    scrolled = QtCore.Signal(int, int)    # (axis, slice_delta) 滚轮翻层
    double_clicked = QtCore.Signal(int)   # (axis) 双击 → 放大/还原
    # 测量工具用的原始鼠标事件（测量激活时不再派发 clicked）
    m_press = QtCore.Signal(int, object)
    m_move = QtCore.Signal(int, object)
    m_release = QtCore.Signal(int, object)

    def __init__(self, axis: int, parent=None):
        super().__init__(parent)
        self.axis = axis
        self._src = None          # 原始像素图
        self._disp = QtCore.QRect()  # 实际绘制区域(相对 label)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setMouseTracking(True)
        self._measure = False     # True = 测量模式
        # 单击延时判定：先等双击窗口，避免双击时误加提示点
        self._click_timer = QtCore.QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._pending_click = None

    def set_measure_mode(self, on: bool) -> None:
        """开启/关闭测量模式（开启后不派发 clicked，改发 m_press/m_move/m_release）。"""
        self._measure = bool(on)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor if on
                       else QtCore.Qt.CursorShape.ArrowCursor)

    @staticmethod
    def _ev_pos(ev) -> dict:
        return {"x": ev.position().x(), "y": ev.position().y(), "button": ev.button()}

    def set_source(self, pix) -> None:
        """保存源图并按当前尺寸等比显示。"""
        self._src = pix
        self._apply_scale()

    def source_pixmap(self):
        return self._src

    def display_rect(self) -> QtCore.QRect:
        """当前实际绘制矩形（未画图时为空）。"""
        return QtCore.QRect(self._disp)

    def _apply_scale(self) -> None:
        if self._src is None:
            return
        w, h = self.width(), self.height()
        self._disp = QtCore.QRect()
        if w <= 2 or h <= 2 or self._src.isNull():
            self.setPixmap(QPixmap() if self._src is None else self._src)
            return
        scaled = self._src.scaled(w, h, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                  QtCore.Qt.TransformationMode.SmoothTransformation)
        sw, sh = scaled.width(), scaled.height()
        self._disp = QtCore.QRect((w - sw) // 2, (h - sh) // 2, sw, sh)
        self.setPixmap(scaled)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._apply_scale()

    def _emit_single_click(self):
        info = self._pending_click
        self._pending_click = None
        if info is not None:
            self.clicked.emit(self.axis, info)

    def mousePressEvent(self, ev):
        if self._measure:
            self.m_press.emit(self.axis, self._ev_pos(ev))
            ev.accept()
            return
        self._pending_click = self._ev_pos(ev)
        self._click_timer.start()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._measure:
            self.m_move.emit(self.axis, self._ev_pos(ev))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._measure:
            self.m_release.emit(self.axis, self._ev_pos(ev))
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # 测量模式下不触发放大/还原
        if self._measure:
            ev.accept()
            return
        # 双击：取消挂起的单击（避免误加提示点），改为放大/还原该视图
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._pending_click = None
            self.double_clicked.emit(self.axis)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta:
            # 上滚 = 上一层，下滚 = 下一层
            self.scrolled.emit(self.axis, -1 if delta > 0 else 1)
            ev.accept()
        else:
            super().wheelEvent(ev)


def _icon_path() -> str:
    """定位程序图标：优先 .ico/.png，其次 .jpg；兼容 PyInstaller 冻结目录。"""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(getattr(sys, "_MEIPASS", ""))
        candidates.append(os.path.dirname(sys.executable))
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    for d in candidates:
        if not d:
            continue
        for name in ("logo.ico", "logo.png", "logo.jpg"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return ""


class CatMascot(QtWidgets.QWidget):
    """可爱卡通猫吉祥物：呼吸起伏 / 尾巴摇摆 / 周期眨眼；忙碌时更活泼并冒爱心。

    纯 QPainter 手绘，无外部资源依赖，给复古拟物界面增添一点轻松的卡通感。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(140, 52)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setToolTip("喵～ 爱宠影像助手")
        self._t = 0.0
        self._frame = 0
        self._blink = 0
        self._next_blink = 80
        self._excited = False
        self._hearts = []          # [[x, y, scale], ...]
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_busy(self, on: bool) -> None:
        self._excited = bool(on)

    def _tick(self) -> None:
        self._frame += 1
        self._t += 0.30 if self._excited else 0.13
        if self._blink > 0:
            self._blink -= 1
        else:
            self._next_blink -= 1
            if self._next_blink <= 0:
                self._blink = 5
                self._next_blink = 110 + (self._frame % 70)
        if self._excited and self._frame % 14 == 0:
            self._hearts.append([self.width() * 0.42, self.height() * 0.45, 0.0])
        for hrt in self._hearts:
            hrt[1] -= 0.7
            hrt[2] += 0.05
        self._hearts = [x for x in self._hearts if x[1] > -8]
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        cx = self.width() * 0.42
        base = h - 5
        bob = math.sin(self._t) * 1.7

        # 地面阴影
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QColor(120, 90, 50, 55))
        p.drawEllipse(QtCore.QRectF(cx - 20, base - 4, 40, 8))

        # 尾巴（摇摆）
        wag = math.sin(self._t * 1.9) * (1.0 if self._excited else 0.6)
        tail = QPainterPath()
        tail.moveTo(cx + 16, base - 10 + bob)
        tail.cubicTo(cx + 30, base - 14 + wag * 5,
                     cx + 34, base - 30 + wag * 12,
                     cx + 22, base - 36 + wag * 14)
        pen = QPen(QColor(198, 118, 38), 5)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawPath(tail)

        # 身体
        body = QLinearGradient(0, base - 18 + bob, 0, base + 2)
        body.setColorAt(0.0, QColor(255, 196, 108))
        body.setColorAt(1.0, QColor(226, 140, 42))
        p.setPen(QPen(QColor(150, 88, 26), 2))
        p.setBrush(body)
        p.drawRoundedRect(QtCore.QRectF(cx - 17, base - 16 + bob, 34, 17), 8, 8)
        # 前爪
        p.setBrush(QColor(255, 226, 176))
        p.setPen(QPen(QColor(150, 88, 26), 1.5))
        p.drawEllipse(QtCore.QRectF(cx - 13, base - 5 + bob, 9, 7))
        p.drawEllipse(QtCore.QRectF(cx + 4, base - 5 + bob, 9, 7))

        # 头
        hy = base - 30 + bob
        head = QLinearGradient(0, hy, 0, hy + 26)
        head.setColorAt(0.0, QColor(255, 206, 128))
        head.setColorAt(1.0, QColor(238, 156, 54))
        # 耳朵（轻微抖动）
        ear_tw = math.sin(self._t * 1.5) * (2.0 if self._excited else 0.7)
        p.setPen(QPen(QColor(150, 88, 26), 2))
        p.setBrush(QColor(236, 150, 52))
        p.drawPolygon(QPolygonF([QtCore.QPointF(cx - 16, hy + 6),
                                 QtCore.QPointF(cx - 20 - ear_tw, hy - 8),
                                 QtCore.QPointF(cx - 5, hy - 1)]))
        p.drawPolygon(QPolygonF([QtCore.QPointF(cx + 16, hy + 6),
                                 QtCore.QPointF(cx + 20 - ear_tw, hy - 8),
                                 QtCore.QPointF(cx + 5, hy - 1)]))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QColor(246, 170, 170))
        p.drawPolygon(QPolygonF([QtCore.QPointF(cx - 14, hy + 5),
                                 QtCore.QPointF(cx - 17 - ear_tw, hy - 5),
                                 QtCore.QPointF(cx - 7, hy + 0)]))
        p.drawPolygon(QPolygonF([QtCore.QPointF(cx + 14, hy + 5),
                                 QtCore.QPointF(cx + 17 - ear_tw, hy - 5),
                                 QtCore.QPointF(cx + 7, hy + 0)]))
        # 脸
        p.setPen(QPen(QColor(150, 88, 26), 2))
        p.setBrush(head)
        p.drawEllipse(QtCore.QRectF(cx - 18, hy, 36, 28))

        # 额头斑纹
        p.setPen(QPen(QColor(180, 104, 30), 2))
        for ox in (-6, 0, 6):
            p.drawLine(QtCore.QPointF(cx + ox, hy + 3), QtCore.QPointF(cx + ox, hy + 7))

        # 眼睛（周期眨眼）
        ey = hy + 13
        if self._blink > 0:
            p.setPen(QPen(QColor(70, 44, 20), 2))
            for ex in (cx - 7, cx + 7):
                p.drawLine(QtCore.QPointF(ex - 3, ey), QtCore.QPointF(ex + 3, ey))
        else:
            p.setPen(QPen(QColor(70, 44, 20), 1.5))
            p.setBrush(QColor(120, 200, 160))
            p.drawEllipse(QtCore.QRectF(cx - 10, ey - 4, 8, 9))
            p.drawEllipse(QtCore.QRectF(cx + 2, ey - 4, 8, 9))
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QColor(40, 28, 16))
            p.drawEllipse(QtCore.QRectF(cx - 7.5, ey - 2, 4, 6))
            p.drawEllipse(QtCore.QRectF(cx + 4.5, ey - 2, 4, 6))
            p.setBrush(QColor(255, 255, 255, 210))
            p.drawEllipse(QtCore.QRectF(cx - 7.0, ey - 2, 1.7, 1.7))
            p.drawEllipse(QtCore.QRectF(cx + 5.0, ey - 2, 1.7, 1.7))

        # 鼻子 + 嘴
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QColor(232, 120, 120))
        p.drawPolygon(QPolygonF([QtCore.QPointF(cx - 2.5, ey + 7),
                                 QtCore.QPointF(cx + 2.5, ey + 7),
                                 QtCore.QPointF(cx, ey + 10)]))
        p.setPen(QPen(QColor(120, 70, 30), 1.4))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawArc(QtCore.QRectF(cx - 6, ey + 8, 6, 5), 200 * 16, 140 * 16)
        p.drawArc(QtCore.QRectF(cx, ey + 8, 6, 5), 200 * 16, 140 * 16)
        # 胡须
        p.setPen(QPen(QColor(120, 70, 30, 170), 1))
        for dy in (-2, 1, 4):
            p.drawLine(QtCore.QPointF(cx - 11, ey + 8 + dy),
                       QtCore.QPointF(cx - 22, ey + 6 + dy * 1.4))
            p.drawLine(QtCore.QPointF(cx + 11, ey + 8 + dy),
                       QtCore.QPointF(cx + 22, ey + 6 + dy * 1.4))

        # 忙碌时冒出的爱心
        for hx, hyy, sc in self._hearts:
            p.save()
            p.translate(hx, hyy)
            p.scale(1 + sc, 1 + sc)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QColor(244, 120, 150, 220))
            hp = QPainterPath()
            hp.moveTo(0, 3)
            hp.cubicTo(-5, -2, -3, -7, 0, -4)
            hp.cubicTo(3, -7, 5, -2, 0, 3)
            p.drawPath(hp)
            p.restore()
        p.end()


# ======================================================================
# 宠物病例档案「默认模板」
# 依据兽医病历通行要素：Signalment（动物特征）→ Subjective（病史）→
# Objective（体格检查）→ 影像检查 → Assessment/Plan（评估与计划）；
# 影像部分参照小动物 CT/MRI 报告结构（技术参数 / 所见 / 结论 / 建议）。
# 可在项目根目录用 case_template.json 覆盖/扩展。
# ======================================================================
DEFAULT_CASE_TEMPLATE = {
    "version": 1,
    "title": "宠物影像病例档案模板",
    "sections": [
        {"name": "① 动物与主人 (Signalment)",
         "fields": [
             {"key": "case_no", "label": "病例号", "type": "text"},
             {"key": "owner", "label": "主人 / 电话", "type": "text"},
             {"key": "species", "label": "物种（犬/猫/…）", "type": "text"},
             {"key": "breed", "label": "品种", "type": "text"},
             {"key": "sex", "label": "性别 / 绝育", "type": "text"},
             {"key": "age", "label": "年龄", "type": "text"},
             {"key": "weight", "label": "体重 (kg)", "type": "text"},
             {"key": "date", "label": "就诊日期", "type": "text"},
         ]},
        {"name": "② 病史 (Subjective)",
         "fields": [
             {"key": "cc", "label": "主诉", "type": "multi"},
             {"key": "history", "label": "现病史 / 既往史", "type": "multi"},
             {"key": "meds", "label": "用药 / 疫苗 / 驱虫", "type": "multi"},
         ]},
        {"name": "③ 体格检查 (Objective)",
         "fields": [
             {"key": "pe_vitals", "label": "T / P / R / 黏膜 / BCS", "type": "text"},
             {"key": "pe_exam", "label": "系统检查所见", "type": "multi"},
         ]},
        {"name": "④ 影像检查 (Imaging)",
         "fields": [
             {"key": "modality", "label": "检查类型（CT/DR/MRI）", "type": "text"},
             {"key": "region", "label": "检查部位", "type": "text"},
             {"key": "contrast", "label": "对比剂（种类/剂量/途径）", "type": "text"},
             {"key": "anesthesia", "label": "麻醉 / 镇静方案", "type": "text"},
             {"key": "technique", "label": "扫描与重建参数", "type": "text"},
             {"key": "findings", "label": "影像所见", "type": "multi"},
         ]},
        {"name": "⑤ 评估与计划 (Assessment / Plan)",
         "fields": [
             {"key": "assessment", "label": "评估 / 鉴别诊断", "type": "multi"},
             {"key": "diagnosis", "label": "诊断", "type": "multi"},
             {"key": "incidental", "label": "偶然发现", "type": "multi"},
             {"key": "recommend", "label": "建议 / 随访", "type": "multi"},
             {"key": "vet", "label": "医师 / 签名", "type": "text"},
         ]},
    ],
}

# 「默认案例」里预填的示例病例（仅演示）
DEMO_CASE_CLINICAL = {
    "case_no": "DEMO-2026-001",
    "owner": "王女士 / 138****0000",
    "species": "犬（Canis familiaris）",
    "breed": "金毛寻回犬",
    "sex": "雄性 / 已绝育",
    "age": "6 岁",
    "weight": "31.5",
    "date": "2026-09-25",
    "cc": "间歇性跛行 2 周，运动后加重。",
    "history": "既往无外伤史；1 年前髌骨脱位 II 级，保守治疗。",
    "meds": "已接种疫苗；每月体内外驱虫；近期口服非甾体止痛药 5 天。",
    "pe_vitals": "T 38.6℃ / P 96 bpm / R 24 / 黏膜粉红 CRT<2s / BCS 6/9",
    "pe_exam": "左后肢触诊疼痛，髋关节活动度下降；心肺听诊无明显异常。",
    "modality": "CT",
    "region": "骨盆及双后肢",
    "contrast": "碘海醇 350 mgI/mL，2 mL/kg，静脉团注",
    "anesthesia": "右美托咪定 + 丙泊酚诱导，异氟烷维持",
    "technique": "层厚 0.625 mm，120 kVp，自动管电流；骨算法 + 软组织算法重建",
    "findings": "左侧股骨头颈区骨皮质不规则、关节间隙变窄、关节囊增厚；髋臼缘骨赘形成。双侧髌骨位置正常。",
    "assessment": "左髋关节退行性关节病（DJD）Ⅲ期；鉴别：股骨头缺血性坏死、感染性关节炎。",
    "diagnosis": "左侧髋关节发育不良继发退行性关节病。",
    "incidental": "胸腰段椎体轻度骨质增生（未见压迫）。",
    "recommend": "骨科专科评估；体重管理；必要时行全髋置换（THR）；3 个月后复查。",
    "vet": "Dr. Chen / 影像科",
}


class PacsWorker(QtCore.QThread):
    """PACS 网络操作后台线程（C-ECHO / C-FIND / C-GET），避免阻塞 GUI。"""

    echo_done = QtCore.Signal(bool, str)
    studies_ready = QtCore.Signal(object)
    series_ready = QtCore.Signal(str, object)     # study_uid, list
    retrieve_done = QtCore.Signal(str, int)       # series_dir, count
    progress = QtCore.Signal(int, str)
    failed = QtCore.Signal(str)

    def __init__(self, job: str, params: dict, parent=None):
        super().__init__(parent)
        self.job = job
        self.params = dict(params or {})

    def run(self) -> None:
        if pacs_client is None:
            self.failed.emit("PACS 模块不可用（缺少 pacs_client.py 或 pynetdicom）。"
                             "请执行： pip install pynetdicom")
            return
        p = self.params
        try:
            if self.job == "echo":
                try:
                    pacs_client.c_echo(p["host"], p["port"], p["called"],
                                       p["calling"], p["timeout"])
                    self.echo_done.emit(True, "C-ECHO 成功：节点可达")
                except Exception as exc:  # noqa: BLE001
                    self.echo_done.emit(False, str(exc))
            elif self.job == "studies":
                res = pacs_client.c_find_studies(
                    p["host"], p["port"], p["called"], p["calling"], p["timeout"],
                    patient_name=p.get("patient_name", ""),
                    patient_id=p.get("patient_id", ""),
                    modality=p.get("modality", ""),
                    date_from=p.get("date_from", ""), date_to=p.get("date_to", ""),
                    on_progress=lambda n, m: self.progress.emit(0, m))
                self.studies_ready.emit(res)
            elif self.job == "series":
                res = pacs_client.c_find_series(
                    p["host"], p["port"], p["called"], p["study_uid"],
                    p["calling"], p["timeout"], modality=p.get("modality", ""),
                    on_progress=lambda n, m: self.progress.emit(0, m))
                self.series_ready.emit(p["study_uid"], res)
            elif self.job == "get":
                total = 0
                last_dir = ""
                items = list(p.get("series", []))
                for i, (st_uid, se_uid) in enumerate(items, start=1):
                    n, d = pacs_client.c_get_series(
                        p["host"], p["port"], p["called"], st_uid, se_uid,
                        p["out_dir"], p["calling"], p["timeout"],
                        on_progress=lambda n_, m, i=i: self.progress.emit(
                            int((i - 1) / max(1, len(items)) * 100),
                            "[%d/%d] %s" % (i, len(items), m)))
                    total += n
                    last_dir = d
                self.retrieve_done.emit(last_dir, total)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AiWorker(QtCore.QThread):
    """DeepSeek 推理后台线程（避免阻塞 GUI）。"""

    done = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, cfg: dict, messages: list, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.messages = messages

    def run(self) -> None:
        if deepseek_client is None:
            self.failed.emit("缺少 deepseek_client.py")
            return
        try:
            self.failed.emit("") if False else None
            self.done.emit(deepseek_client.chat(self.cfg, self.messages))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, initial_input: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("Animal Dicom · 爱宠影像工作站 v1.0.0")
        _ic = _icon_path()
        if _ic:
            self.setWindowIcon(QIcon(_ic))
        self.resize(1520, 920)

        self.initial_input = initial_input or ""
        self.load_busy = False
        self.load_cancel = False        # True = 用户要求终止当前渲染
        self._pending_load = None       # 排队等待的下一个序列 (path, label)
        self._loading_label = ""        # 当前正在渲染的对象，供冲突提示显示
        self._pat_pending_request = None  # 点击后待执行的加载请求 (folder, label)
        # 点击去抖：连点多个序列时只加载最后一个，避免排队一串过期加载
        self._pat_debounce = QtCore.QTimer(self)
        self._pat_debounce.setSingleShot(True)
        self._pat_debounce.setInterval(250)
        self._pat_debounce.timeout.connect(self._pat_flush_request)
        self.last_error = None
        self._mcp_mode = False
        self._mcp_bridge = None
        # ---- 3D SAM 交互分割状态 ----
        self.sam_session = None
        self.sam_pos_pts = []   # [(z,y,x), ...] 正点（VR 网格坐标）
        self.sam_neg_pts = []   # [(z,y,x), ...] 负点
        self.sam_mask = None    # np.bool (D,H,W) 最近一次 SAM 结果
        self.sam_busy = False
        self.sam_pending = False
        self._sam_refreshing = False
        self.controller = None  # type: Optional[FusionController]
        self.current_ssd_volume = None  # type: Optional[vtk.vtkVolume]
        self.current_vr_volume = None  # type: Optional[vtk.vtkVolume]
        self.current_roi_volume = None
        self.current_ssd_mapper = None
        self.current_vr_mapper = None
        self.current_roi_mapper = None
        self.vr_producer = None
        self.roi_producer = None
        self.render_mode = "stable"  # stable | hd_surface | cinematic | nature_channels | spectral | exposure_render
        self.mc_quality = 0.60  # 0-1, 越高路径追踪采样越密
        self.scatter_blend = 0.65  # 0-1, 混合散射强度
        self.scatter_g = 0.80  # HG 各向异性参数
        self.er_exposure = 1.50  # Exposure Render 风格曝光参数
        self.cr_denoise = 0.00   # CR 降噪控制 (0-1)
        # 预处理选项
        self.denoise_method = "gaussian"  # gaussian | nlm
        self.use_clahe = False
        self.use_frangi = False
        self.use_distance_field = False
        self.use_2d_tf = False
        self.use_2d_tf_bone = False
        self.cpu_render = False
        self.vram_threshold_gb = 10  # 用户可调显存阈值
        # 对齐 Exposure Render 的 Traversal 参数
        self.step_factor_primary = 0.10
        self.step_factor_shadow = 0.10
        # 更保守的默认显存预算，降低 nvoglv64.dll 驱动崩溃概率
        self.total_gpu_budget_bytes = 3 * 1024 * 1024 * 1024
        self.er_wrapper = None
        self.er_image_actor = None
        self.er_image_data = None
        # We don't use repeating timer anymore to avoid freezing the UI
        self.er_buffer = None
        self.last_cam_params = None
        self.last_tf_params = None

        self.seg_pipeline = None
        self.seg_visualizer = None
        self.seg_result = None
        self.original_sitk_image = None
        self.seg_renderer = None
        self.vr_image_data = None
        self.roi_image_data = None
        self.vr_base_array = None
        self.vr_work_array = None
        self.roi_array = None
        self.original_image_shape_zyx = None
        self.right_volume = None
        self.right_producer = None
        self.right_mapper = None
        # 模态信息（CT / MR / DR ...），用于阅片灰度分流
        self.modality = "CT"
        self.is_2d = False

        self.slicer_presets = {}
        self._load_slicer_presets()

    def _mcp_push(self, etype, data):
        bridge = getattr(self, "_mcp_bridge", None)
        if bridge is not None:
            try:
                bridge.push_event(etype, data)
            except Exception:
                pass

    def _load_slicer_presets(self):
        import xml.etree.ElementTree as ET
        preset_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.xml")
        if not os.path.exists(preset_file):
            print(f"Warning: Slicer preset file {preset_file} not found.")
            return
            
        try:
            tree = ET.parse(preset_file)
            root = tree.getroot()
            for vp in root.findall('VolumeProperty'):
                name = vp.get('name')
                if not name:
                    continue
                
                # Parse scalarOpacity
                so_str = vp.get('scalarOpacity', '')
                so_parts = so_str.split()
                if len(so_parts) > 0:
                    num_pts = int(so_parts[0])
                    opacity_pts = []
                    idx = 1
                    for _ in range(num_pts):
                        if idx + 1 < len(so_parts):
                            val = float(so_parts[idx])
                            op = float(so_parts[idx+1])
                            opacity_pts.append((val, op))
                            idx += 2
                else:
                    opacity_pts = []
                
                # Parse colorTransfer
                ct_str = vp.get('colorTransfer', '')
                ct_parts = ct_str.split()
                if len(ct_parts) > 0:
                    num_pts = int(ct_parts[0])
                    color_pts = []
                    idx = 1
                    for _ in range(num_pts):
                        if idx + 3 < len(ct_parts):
                            val = float(ct_parts[idx])
                            r = float(ct_parts[idx+1])
                            g = float(ct_parts[idx+2])
                            b = float(ct_parts[idx+3])
                            color_pts.append((val, r, g, b))
                            idx += 4
                else:
                    color_pts = []
                    
                ambient = float(vp.get('ambient', 0.1))
                diffuse = float(vp.get('diffuse', 0.9))
                specular = float(vp.get('specular', 0.2))
                specularPower = float(vp.get('specularPower', 10.0))
                
                self.slicer_presets[name] = {
                    'opacity': opacity_pts,
                    'color': color_pts,
                    'ambient': ambient,
                    'diffuse': diffuse,
                    'specular': specular,
                    'specularPower': specularPower
                }
        except Exception as e:
            print(f"Error parsing presets.xml: {e}")

        # Class A (SSD Layer): 胸骨外壳（稳定模式）
        self.ssd_opacity_points_stable = [
            (-1000, 0.00),
            (320, 0.00),
            (420, 0.01),
            (560, 0.10),
            (760, 0.52),
            (980, 0.86),
            (1300, 1.00),
            (3000, 1.00),
        ]
        # Class A (SSD Layer): 胸骨外壳（高清骨表模式：更高阈值、更陡峭）
        self.ssd_opacity_points_hd = [
            (-1000, 0.00),
            (380, 0.00),
            (520, 0.01),
            (700, 0.18),
            (900, 0.62),
            (1150, 0.92),
            (1450, 1.00),
            (3000, 1.00),
        ]
        # Nature Paper mode points
        self.ssd_opacity_points_nature = [
            (-1000, 0.00),
            (500, 0.00),
            (700, 0.15),   # Semi-transparent cortical bone shell
            (1200, 0.25),
            (3000, 0.30),
        ]
        self.vr_opacity_points_nature = [
            (-1000, 0.00),
            (50, 0.00),
            (100, 0.40),   # Emphasize vascular channels / marrow spaces inside bone
            (250, 0.85),
            (400, 0.95),
            (600, 0.00),   # Drop off before dense bone
        ]
        self.vr_color_points_nature = [
            (-1000, 0.00, 0.00, 0.00),
            (50, 0.80, 0.10, 0.10),   # Deep red for marrow
            (150, 0.95, 0.30, 0.10),  # Bright orange/red for channels
            (300, 1.00, 0.60, 0.20),
            (600, 1.00, 0.80, 0.50),
        ]
        
        self.ssd_opacity_points = list(self.ssd_opacity_points_stable)
        self.ssd_color_points_stable = [
            (-1000, 0.00, 0.00, 0.00),
            (420, 0.80, 0.78, 0.75),
            (760, 0.95, 0.93, 0.90),
            (1300, 1.00, 1.00, 1.00),
            (3000, 1.00, 1.00, 1.00),
        ]
        self.ssd_color_points = list(self.ssd_color_points_stable)
        # Class B (VR Layer): 骨髓腔，低 HU（20-150）
        self.vr_opacity_points = [
            (-1000, 0.00),
            (10, 0.00),
            (20, 0.05),
            (80, 0.16),
            (120, 0.24),
            (150, 0.28),
            (180, 0.10),
            (260, 0.00),
        ]
        self.vr_color_points = [
            (-1000, 0.00, 0.00, 0.00),
            (20, 0.55, 0.18, 0.18),
            (80, 0.72, 0.30, 0.28),
            (120, 0.82, 0.42, 0.36),
            (150, 0.92, 0.56, 0.46),
            (260, 0.95, 0.80, 0.70),
        ]
        # Cinematic 模式下的 VR 传递函数（根据 2026 Thorax/Vascular Spectral Optimized 方案优化）
        self.vr_opacity_points_cinematic = [
            (-1000, 0.00),
            (-950, 0.00),
            (-900, 0.12),  # 肺实质 (紫/靛蓝) 起点
            (-400, 0.20),  # 肺实质 终点
            (-350, 0.00),  # 截断空气与脂肪
            (100, 0.00),   # 增强血管前
            (150, 0.60),   # 增强血管 (高亮黄/金橙) 起点
            (250, 0.90),   # 血管核心
            (550, 0.95),   # 血管终点
            (600, 0.00),   # 交接给骨骼 (SSD)
        ]
        self.vr_color_points_cinematic = [
            (-1000, 0.00, 0.00, 0.00),
            (-950, 0.29, 0.00, 0.51),  # Deep Violet
            (-400, 0.15, 0.00, 0.35),  # Indigo
            (150, 1.00, 0.55, 0.00),   # Golden Orange
            (550, 1.00, 0.96, 0.00),   # Cadmium Yellow
        ]
        
        self.ssd_opacity_points_cinematic = [
            (-1000, 0.00),
            (150, 0.00),
            (200, 0.20),   # 骨骼系统起点
            (500, 0.70),   # 骨皮质
            (1200, 1.00),  # 密质骨
            (3000, 1.00),
        ]
        self.ssd_color_points_cinematic = [
            (-1000, 0.00, 0.00, 0.00),
            (200, 1.00, 0.99, 0.82),   # Pale Yellow Cream
            (1200, 1.00, 1.00, 0.94),  # Ivory
            (3000, 1.00, 1.00, 1.00),  # White
        ]
        
        self.vr_opacity_points_spectral = [
            (-1000, 0.00),
            (-950, 0.00),
            (-900, 0.10),  # Lungs (cyan mist)
            (-500, 0.20),
            (-400, 0.00),
            (-120, 0.00),
            (-100, 0.02),  # Soft tissue (dark cool grey, lower opacity to reduce fatigue)
            (80, 0.02),
            (100, 0.00),
            (150, 0.60),   # Vessels (crimson)
            (250, 0.90),
            (500, 0.95),
            (700, 0.00),
        ]
        self.vr_color_points_spectral = [
            (-1000, 0.00, 0.00, 0.00),
            (-900, 0.20, 0.80, 0.80),  # Cyan
            (-500, 0.40, 0.90, 0.90),
            (-100, 0.18, 0.21, 0.25),  # Cool Dark Grey
            (80, 0.21, 0.23, 0.28),
            (150, 0.50, 0.05, 0.05),   # Deep Crimson
            (250, 0.70, 0.10, 0.10),
            (500, 0.86, 0.87, 0.88),   # Cool Light Grey / bone
        ]

        # Figure 8 mode: 皮质SSD开口 + 骨髓骨性结构拼接
        # SSD: semi-transparent ivory shell with channel openings visible as dark perforations
        self.ssd_opacity_points_figure8 = [
            (-1000, 0.00),
            (300, 0.00),
            (420, 0.06),
            (550, 0.15),
            (700, 0.22),
            (900, 0.28),
            (1100, 0.25),
            (1500, 0.18),
            (3000, 0.12),
        ]
        self.ssd_color_points_figure8 = [
            (-1000, 0.00, 0.00, 0.00),
            (420, 0.85, 0.83, 0.78),
            (900, 0.95, 0.93, 0.90),
            (3000, 1.00, 1.00, 1.00),
        ]
        self.vr_opacity_points_figure8 = [
            (-1000, 0.00),
            (-50, 0.00),
            (0, 0.04),
            (50, 0.38),
            (100, 0.70),
            (200, 0.85),
            (350, 0.90),
            (450, 0.55),
            (550, 0.00),
            (3000, 0.00),
        ]
        self.vr_color_points_figure8 = [
            (-1000, 0.00, 0.00, 0.00),
            (0, 0.35, 0.10, 0.05),
            (50, 0.60, 0.25, 0.12),
            (100, 0.80, 0.45, 0.22),
            (200, 0.95, 0.65, 0.40),
            (350, 1.00, 0.80, 0.55),
            (550, 1.00, 0.90, 0.75),
        ]

        # Layered Channel 模式: 4层虚拟体渲染 (外板/板障/内板/微通道增强)
        # Layer 1+3 (SSD): outer + inner cortical shell, 非常透明
        self.ssd_opacity_points_layered = [
            (-1000, 0.000),
            (400, 0.000),
            (600, 0.025),
            (800, 0.065),
            (1100, 0.100),
            (1500, 0.070),
            (2200, 0.025),
            (3000, 0.012),
        ]
        self.ssd_color_points_layered = [
            (-1000, 0.00, 0.00, 0.00),
            (600, 0.78, 0.80, 0.84),
            (1100, 0.88, 0.90, 0.93),
            (3000, 0.96, 0.97, 0.99),
        ]
        # Layer 2 (VR): 12点非线性骨髓TF — 覆盖黄骨髓→红骨髓→骨小梁全动态范围
        self.vr_opacity_points_layered = [
            (-1000, 0.000),
            (-120, 0.000),           # 黄骨髓起点
            (-100, 0.015),           # 淡黄可见
            (-60,  0.040),           # 黄骨髓峰
            (-20,  0.100),           # 红骨髓爬升
            (0,    0.080),           # 纯细胞区微降
            (30,   0.120),           # 红骨髓峰
            (60,   0.160),           # 骨髓-骨小梁过渡
            (100,  0.220),           # 骨小梁起点
            (180,  0.300),           # 骨小梁峰
            (280,  0.260),           # 致密骨小梁
            (400,  0.120),           # 皮质过渡
            (500,  0.030),           # 皮质渐隐
            (600,  0.000),
        ]
        self.vr_color_points_layered = [
            (-1000, 0.00, 0.00, 0.00),
            (-100,  0.55, 0.48, 0.25),   # 淡黄
            (-60,   0.60, 0.52, 0.30),   # 暖黄
            (-20,   0.72, 0.30, 0.20),   # 红过渡
            (0,     0.75, 0.22, 0.18),   # 深红 (造血)
            (30,    0.80, 0.25, 0.20),   # 亮红
            (60,    0.85, 0.38, 0.28),   # 红琥珀
            (100,   0.90, 0.50, 0.35),   # 琥珀
            (180,   0.95, 0.65, 0.45),   # 金琥珀
            (280,   0.98, 0.78, 0.58),   # 浅琥珀
            (400,   1.00, 0.88, 0.75),   # 骨白
            (500,   1.00, 0.92, 0.82),   # 亮骨白
        ]
        # Layer 4 gradient opacity: 微通道边缘增强 (aggressive edge detection)
        self.gradient_opacity_points_layered = [
            (0.0, 0.00),
            (2.0, 0.00),
            (5.0, 0.18),
            (12.0, 0.48),
            (30.0, 0.78),
            (80.0, 0.95),
            (200.0, 1.00),
        ]

        # Frangi Channel 模式: 4层虚拟模型 + Frangi Dark Tube 微通道增强
        # SSD Layer: 超薄骨皮质壳，透明以显示微通道开口
        self.ssd_opacity_points_frangi = [
            (-1000, 0.000),
            (400, 0.000),
            (600, 0.020),
            (800, 0.050),
            (1100, 0.080),
            (1600, 0.050),
            (2200, 0.018),
            (3000, 0.008),
        ]
        self.ssd_color_points_frangi = [
            (-1000, 0.00, 0.00, 0.00),
            (600, 0.72, 0.75, 0.80),
            (1100, 0.85, 0.88, 0.92),
            (3000, 0.95, 0.96, 0.98),
        ]
        # VR Layer: 12点骨髓TF + Frangi-boosted微通道 (青色高亮)
        self.vr_opacity_points_frangi = [
            (-1000, 0.000),
            (-120, 0.000),  (-100, 0.015),  (-60,  0.040),
            (-20,  0.100),  (0,    0.080),  (30,   0.120),
            (60,   0.160),  (100,  0.220),  (180,  0.300),
            (280,  0.260),  (400,  0.120),  (500,  0.030),
            (600,  0.000),
            (1150, 0.000),
            (1250, 0.600),  (1400, 0.880),  (1600, 0.920),
            (1850, 0.350),  (2000, 0.000),  (3000, 0.000),
        ]
        self.vr_color_points_frangi = [
            (-1000, 0.00, 0.00, 0.00),
            (-100,  0.55, 0.48, 0.25),  (-60,   0.60, 0.52, 0.30),
            (-20,   0.72, 0.30, 0.20),  (0,     0.75, 0.22, 0.18),
            (30,    0.80, 0.25, 0.20),  (60,    0.85, 0.38, 0.28),
            (100,   0.90, 0.50, 0.35),  (180,   0.95, 0.65, 0.45),
            (280,   0.98, 0.78, 0.58),  (400,   1.00, 0.88, 0.75),
            (500,   1.00, 0.92, 0.82),
            (1150, 0.00, 0.00, 0.00),
            (1250, 0.00, 0.85, 0.90),  (1400, 0.05, 0.95, 0.88),
            (1600, 0.00, 1.00, 0.70),  (1850, 0.10, 0.70, 0.60),
        ]
        # Gradient opacity: 微通道边缘增强
        self.gradient_opacity_points_frangi = [
            (0.0, 0.00),
            (2.0, 0.00),
            (5.0, 0.15),
            (15.0, 0.45),
            (35.0, 0.75),
            (80.0, 0.95),
            (200.0, 1.00),
        ]

        # Bone Monochrome 模式: 纯VR — 皮质结构突出，髓质半透明衬托
        self.ssd_opacity_points_bone_mono = [(-1000, 0.0), (3000, 0.0)]
        self.ssd_color_points_bone_mono = [(-1000, 0.0, 0.0, 0.0), (3000, 0.0, 0.0, 0.0)]
        self.vr_opacity_points_bone_mono = [
            (-1000, 0.000),
            (-120, 0.000),
            (-100, 0.180),   # 黄骨髓淡入
            (-50,  0.350),   # 黄髓半透明
            (-20,  0.550),   # 红髓中透
            (30,   0.720),   # 红髓密致
            (100,  0.850),   # 骨小梁
            (250,  0.920),   # 骨小梁峰
            (400,  1.000),   # 皮质骨 ▸ 完全不透明
            (800,  1.000),   # 密质骨
            (1500, 1.000),   # 超密骨
            (3000, 1.000),
        ]
        self.vr_color_points_bone_mono = [
            (-1000, 0.00, 0.00, 0.00),
            (-100, 0.55, 0.52, 0.48),   # 冷骨色
            (-20,  0.65, 0.63, 0.60),   # 暖灰骨
            (30,   0.72, 0.70, 0.68),   # 骨本色
            (100,  0.82, 0.81, 0.79),   # 浅骨色
            (400,  0.94, 0.94, 0.93),   # 瓷白 ▸ 皮质
            (1000, 1.00, 1.00, 1.00),   # 纯白
        ]
        self.gradient_opacity_points_bone_mono = [
            (0.0, 1.00),
            (50.0, 1.00),
        ]
        self.vr_opacity_points_bone_mono_frangi = [
            (-1000, 0.000), (-120, 0.000),
            (-100, 0.180),  (-50, 0.350),  (-20, 0.550),
            (30, 0.720),    (100, 0.850),  (250, 0.920),
            (400, 1.000),   (800, 1.000),
            (1150, 0.000),  (1250, 0.800), (1500, 1.000),
            (1850, 1.000),  (2000, 0.000),  (3000, 1.000),
        ]
        self.vr_color_points_bone_mono_frangi = [
            (-1000, 0.00, 0.00, 0.00),
            (-100, 0.55, 0.52, 0.48),
            (-20,  0.65, 0.63, 0.60),
            (30,   0.72, 0.70, 0.68),
            (100,  0.82, 0.81, 0.79),
            (400,  0.94, 0.94, 0.93),
            (1000, 1.00, 1.00, 1.00),
            (1150, 0.00, 0.00, 0.00), (1250, 0.92, 0.90, 0.88),
            (1850, 1.00, 1.00, 1.00),
        ]

        # 2D TF (Kniss 2001) 模式: 2D TF预处理后VR — 骨+界面仅存, 骨髓/软组织远场已衰减
        self.vr_opacity_points_2dtf = [
            (-1000, 0.000),
            (-120, 0.000), (-100, 0.300),
            (-30,  0.600),  (100,  0.900),
            (400,  1.000),  (3000, 1.000),
        ]
        self.vr_color_points_2dtf = [
            (-1000, 0.00, 0.00, 0.00),
            (-100, 0.45, 0.42, 0.37),
            (0, 0.65, 0.62, 0.58),
            (300, 0.92, 0.90, 0.87),
            (800, 1.00, 1.00, 1.00),
        ]

        self._build_ui()
        if self.initial_input and os.path.exists(self.initial_input):
            self.path_edit.line_edit().setText(self.initial_input)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        root_layout = QtWidgets.QVBoxLayout(central)

        # --- Tab widget for pages ---
        self.pages = QtWidgets.QTabWidget()

        # === Tab 0: 爱宠列表（病例浏览；内含「PACS 节点」二级子页）===
        # 顶层页签：爱宠列表 / MPR 阅片 / 渲染。
        # PACS 不占顶层页签，作为本页的二级子页，由「PACS节点」按钮触发显示。
        self.patient_stack = QtWidgets.QStackedWidget()
        patient_page = QtWidgets.QWidget()
        patient_layout = QtWidgets.QVBoxLayout(patient_page)
        patient_layout.setContentsMargins(4, 2, 4, 2)
        patient_layout.setSpacing(4)
        self._build_patient_section(patient_page, patient_layout)
        self.patient_stack.addWidget(patient_page)          # 0 = 爱宠列表
        self.pages.addTab(self.patient_stack, "爱宠列表")

        # === Tab 1: 渲染（参数）===
        # render_layout 绑定到本页，后续几十个 render_layout.addWidget(...)
        # 无需改动即自动归入「渲染」页。
        render_page = QtWidgets.QWidget()
        render_layout = QtWidgets.QVBoxLayout(render_page)
        render_layout.setContentsMargins(4, 2, 4, 2)
        render_layout.setSpacing(1)

        # --- 路径输入（「选文件夹 / 选文件」已由上面的爱宠列表取代）---
        self.path_edit = CLineEdit(
            master=render_page,
            placeholder_text="DICOM 目录路径（从「爱宠列表」选择，或在此直接填写）...")
        render_layout.addWidget(self.path_edit)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_load = CButton(master=render_page, width=66, text="加载渲染", command=self.load_dicom,
                                 background_color=("#c46a18", "#e0842a"),
                                 hover_color=("#b85e12", "#cf7a26"),
                                 text_color=("#ffffff", "#ffffff"))
        btn_row.addWidget(self.btn_load)
        btn_row.addStretch()
        render_layout.addLayout(btn_row)

        # --- 窗宽窗位 ---
        render_layout.addWidget(QtWidgets.QLabel("窗位 (Window Level) 偏移"))
        self.wl_slider = CSlider(master=render_page, minimum=-1000, maximum=1000, value=0)
        self.wl_label = QtWidgets.QLabel("当前: 0 HU")
        self.wl_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.wl_slider)
        render_layout.addWidget(self.wl_label)

        render_layout.addWidget(QtWidgets.QLabel("窗宽 (Window Width) 缩放"))
        self.ww_slider = CSlider(master=render_page, minimum=10, maximum=300, value=100)
        self.ww_label = QtWidgets.QLabel("当前: 1.00x")
        self.ww_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.ww_slider)
        render_layout.addWidget(self.ww_label)

        # --- SSD ---
        render_layout.addWidget(QtWidgets.QLabel("SSD 骨骼层不透明度"))
        self.ssd_slider = CSlider(master=render_page, minimum=0, maximum=200, value=90)
        self.ssd_slider_label = QtWidgets.QLabel("当前: 0.90")
        self.ssd_slider_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.ssd_slider)
        render_layout.addWidget(self.ssd_slider_label)

        render_layout.addWidget(QtWidgets.QLabel("SSD 阈值范围"))
        self.ssd_threshold_slider = RangeSlider()
        self.ssd_threshold_slider.setRange(-1000, 3000)
        self.ssd_threshold_slider.setValues(320, 1300)
        self.ssd_threshold_slider.valueChanged.connect(self.on_ssd_threshold_change)
        self.ssd_threshold_label = QtWidgets.QLabel("当前: [320, 1300]")
        self.ssd_threshold_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.ssd_threshold_slider)
        render_layout.addWidget(self.ssd_threshold_label)

        # --- VR ---
        render_layout.addWidget(QtWidgets.QLabel("VR 软组织层不透明度"))
        self.vr_slider = CSlider(master=render_page, minimum=0, maximum=200, value=20)
        self.vr_slider_label = QtWidgets.QLabel("当前: 0.90")
        self.vr_slider_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.vr_slider)
        render_layout.addWidget(self.vr_slider_label)
        # 初始标签跟实际滑块值对齐（CSlider 构造值 20 → 0.20），
        # 否则界面上会一直显示"当前: 0.90"直到用户拖一下滑块。
        try:
            self.vr_slider_label.setText(
                "\u5f53\u524d: %.2f" % (self.vr_slider.slider().value() / 100.0))
        except Exception:  # noqa: BLE001
            pass

        # --- 渲染模式 ---
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("渲染模式"))
        self.mode_combo = CComboBox(master=render_page, values=[
            "稳定渲染模式 (CPU/基础)",
            "高清骨表面模式 (GPU)",
            "CR 电影级渲染模式",
            "Nature 颅骨微通道模式 (PDF)",
            "Figure8 皮质开口/骨髓拼接模式",
            "Layered 分层体渲染 (微通道增强)",
            "Frangi 微通道增强 (Dark Tube)",
            "骨组织固有色渲染 (Bone Monochrome)",
            "2D TF 界面分离 (Kniss 2001)",
            "CR 光谱模式 (Spectral Look)",
            "Exposure Render (CUDA)",
            "双容积模式 (Dual Volume)",
        ])
        mode_row.addWidget(self.mode_combo, 1)
        render_layout.addLayout(mode_row)

        # --- 裁剪与背景 ---
        util_row = QtWidgets.QHBoxLayout()
        self.check_crop = QtWidgets.QCheckBox("启用裁剪框")
        self.btn_bg_toggle = CButton(master=render_page, width=90, text="切换背景", command=self.toggle_background)
        self.bg_is_white = True    # 浅色主题：默认亮背景
        util_row.addWidget(self.check_crop)
        util_row.addWidget(self.btn_bg_toggle)
        util_row.addStretch()
        render_layout.addLayout(util_row)

        # --- Slicer 预设 ---
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Slicer 预设模板"))
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItem("None (使用滑块调节)")
        for preset_name in sorted(self.slicer_presets.keys()):
            self.preset_combo.addItem(preset_name)
        # 不让预设名撑宽下拉框（列表会很长）
        self.preset_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setMinimumContentsLength(10)
        self.preset_combo.setMinimumWidth(140)
        self.preset_combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                        QtWidgets.QSizePolicy.Policy.Fixed)
        self.preset_combo.currentIndexChanged.connect(self.on_preset_change)
        preset_row.addWidget(self.preset_combo, 1)
        render_layout.addLayout(preset_row)

        # --- 预处理 ---
        self.check_nlm = QtWidgets.QCheckBox("NLM 降噪 (保留微通道边缘)")
        self.check_nlm.stateChanged.connect(self.on_preproc_change)
        render_layout.addWidget(self.check_nlm)

        self.check_clahe = QtWidgets.QCheckBox("CLAHE 局部对比度增强")
        self.check_clahe.stateChanged.connect(self.on_preproc_change)
        render_layout.addWidget(self.check_clahe)

        self.check_frangi = QtWidgets.QCheckBox("Frangi 微通道检测 (Dark Tube)")
        self.check_frangi.stateChanged.connect(self.on_preproc_change)
        render_layout.addWidget(self.check_frangi)

        self.check_dist = QtWidgets.QCheckBox("距离场骨膜聚合 (Distance-Based)")
        self.check_dist.stateChanged.connect(self.on_preproc_change)
        render_layout.addWidget(self.check_dist)

        self.check_2dtf = QtWidgets.QCheckBox("2D TF 骨界面分离 (Kniss 2001)")
        self.check_2dtf.stateChanged.connect(self.on_preproc_change)
        render_layout.addWidget(self.check_2dtf)

        vram_row = QtWidgets.QHBoxLayout()
        vram_row.addWidget(QtWidgets.QLabel("VRAM 限额"))
        self.vram_spin = QtWidgets.QSpinBox()
        self.vram_spin.setRange(1, 48)
        self.vram_spin.setValue(10)
        self.vram_spin.setSuffix(" GB")
        self.vram_spin.valueChanged.connect(self.on_vram_change)
        vram_row.addWidget(self.vram_spin)
        vram_row.addStretch()
        render_layout.addLayout(vram_row)

        self.check_cpu = QtWidgets.QCheckBox("CPU 全分辨率渲染 (跳过下采样)")
        self.check_cpu.setChecked(False)
        self.check_cpu.stateChanged.connect(self.on_cpu_change)
        render_layout.addWidget(self.check_cpu)

        # --- VR 阈值 ---
        render_layout.addWidget(QtWidgets.QLabel("VR 阈值范围 1"))
        self.vr_threshold_slider = RangeSlider()
        self.vr_threshold_slider.setRange(-1000, 3000)
        self.vr_threshold_slider.setValues(20, 150)
        self.vr_threshold_slider.valueChanged.connect(self.on_vr_threshold_change)
        self.vr_threshold_label = QtWidgets.QLabel("当前: [20, 150]")
        self.vr_threshold_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.vr_threshold_slider)
        render_layout.addWidget(self.vr_threshold_label)

        render_layout.addWidget(QtWidgets.QLabel("VR 阈值范围 2"))
        self.vr_threshold_slider2 = RangeSlider()
        self.vr_threshold_slider2.setRange(-1000, 3000)
        self.vr_threshold_slider2.setValues(-200, -50)
        self.vr_threshold_slider2.valueChanged.connect(self.on_vr_threshold_change)
        self.vr_threshold_label2 = QtWidgets.QLabel("当前: [-200, -50]")
        self.vr_threshold_label2.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.vr_threshold_slider2)
        render_layout.addWidget(self.vr_threshold_label2)

        # --- 渲染参数 ---
        render_layout.addWidget(QtWidgets.QLabel("Monte Carlo 质量"))
        self.mc_slider = CSlider(master=render_page, minimum=10, maximum=100, value=60)
        self.mc_label = QtWidgets.QLabel("当前: 0.60")
        self.mc_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.mc_slider)
        render_layout.addWidget(self.mc_label)

        render_layout.addWidget(QtWidgets.QLabel("散射混合强度"))
        self.scatter_slider = CSlider(master=render_page, minimum=0, maximum=100, value=65)
        self.scatter_label = QtWidgets.QLabel("当前: 0.65")
        self.scatter_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.scatter_slider)
        render_layout.addWidget(self.scatter_label)

        render_layout.addWidget(QtWidgets.QLabel("各向异性参数 (g)"))
        self.g_slider = CSlider(master=render_page, minimum=0, maximum=95, value=80)
        self.g_label = QtWidgets.QLabel("当前: 0.80")
        self.g_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.g_slider)
        render_layout.addWidget(self.g_label)

        render_layout.addWidget(QtWidgets.QLabel("曝光系数 (Exposure)"))
        self.exp_slider = CSlider(master=render_page, minimum=20, maximum=300, value=150)
        self.exp_label = QtWidgets.QLabel("当前: 1.50")
        self.exp_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.exp_slider)
        render_layout.addWidget(self.exp_label)

        render_layout.addWidget(QtWidgets.QLabel("CR 降噪强度"))
        self.denoise_slider = CSlider(master=render_page, minimum=0, maximum=100, value=0)
        self.denoise_label = QtWidgets.QLabel("当前: 0.00")
        self.denoise_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.denoise_slider)
        render_layout.addWidget(self.denoise_label)

        render_layout.addWidget(QtWidgets.QLabel("主光线采样步长 (Primary)"))
        self.primary_slider = CSlider(master=render_page, minimum=1, maximum=100, value=10)
        self.primary_label = QtWidgets.QLabel("当前: 0.10")
        self.primary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.primary_slider)
        render_layout.addWidget(self.primary_label)

        render_layout.addWidget(QtWidgets.QLabel("阴影光线采样步长 (Shadow)"))
        self.shadow_slider = CSlider(master=render_page, minimum=1, maximum=100, value=10)
        self.shadow_label = QtWidgets.QLabel("当前: 0.10")
        self.shadow_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        render_layout.addWidget(self.shadow_slider)
        render_layout.addWidget(self.shadow_label)

        # --- DoF & BG Light ---
        dof_row = QtWidgets.QHBoxLayout()
        self.cr_dof_checkbox = QtWidgets.QCheckBox("景深 (DoF)")
        self.cr_dof_checkbox.stateChanged.connect(self.on_cr_params_change)
        dof_row.addWidget(self.cr_dof_checkbox)
        # 限宽：否则 CSlider 默认 280+10 会把整行撑到 545，导致参数栏右侧被裁
        self.cr_dof_radius_slider = CSlider(master=render_page, width=140,
                                            minimum=0, maximum=50, value=10)
        self.cr_dof_radius_slider.slider().valueChanged.connect(self.on_cr_params_change)
        dof_row.addWidget(self.cr_dof_radius_slider, 1)
        render_layout.addLayout(dof_row)

        self.cr_bg_checkbox = QtWidgets.QCheckBox("使用背景光源")
        self.cr_bg_checkbox.stateChanged.connect(self.on_cr_params_change)
        render_layout.addWidget(self.cr_bg_checkbox)

        # --- 连接滑块信号 ---
        self.denoise_slider.slider().valueChanged.connect(self.on_cr_params_change)

        # --- 加载进度 ---
        progress_group = QtWidgets.QGroupBox("加载进度")
        progress_group.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        progress_layout = QtWidgets.QVBoxLayout(progress_group)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_text = QtWidgets.QTextEdit()
        self.progress_text.setReadOnly(True)
        self.progress_text.setMinimumHeight(50)
        self.progress_text.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_text, 1)
        render_layout.addWidget(progress_group)

        # render_page goes in its own scroll area for the tab
        render_scroll = QtWidgets.QScrollArea()
        render_scroll.setWidgetResizable(True)
        # 兜底：万一仍有控件超宽，允许横向滚动而不是被硬裁掉
        render_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        render_scroll.setWidget(render_page)
        self.pages.addTab(render_scroll, "渲染")

        # === MPR 阅片 / 3D SAM 页（主阅片界面）===
        self._build_sam_tab()

        # === PACS 取片页（插到「爱宠列表」之后）===
        self._build_pacs_tab()

        # Split layout: left=tabs+controls, right=VTK render
        #
        # 关于"分隔栏能不能拖"：QSplitter 的把手默认只有 1-4 像素、且没有任何
        # 配色，在深色主题下几乎看不见也抓不住 —— 用起来就等同于"分隔栏不能拖"。
        # 这里显式给把手宽度 + 两侧最小宽度，并配合 dark.qss 里的
        # QSplitter::handle 配色 / hover / pressed 反馈，
        # 让它成为一个明显、好抓、可左右拖动的把手。
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # 两侧各留一个可用下限：参数栏不至于被压到看不见控件，
        # 渲染视图也不至于被压成一条缝（配合 setChildrenCollapsible(False)）。
        self.pages.setMinimumWidth(300)
        self.splitter.addWidget(self.pages)

        # 右侧区域：QStackedWidget（0 = 3D 渲染视图，1 = 档案册网格）
        # 这样「档案册」能占参数栏右侧的整块大区域，而不是挤在参数栏里。
        self.right_stack = QtWidgets.QStackedWidget()
        self.right_stack.setMinimumWidth(320)
        self.vtk_widget = QVTKRenderWindowInteractor(central)
        self.vtk_widget.setMinimumWidth(320)
        self.right_stack.addWidget(self.vtk_widget)

        # 右区容器：右上角「保存到档案（截图）」按钮 + 内容堆叠
        self.right_box = QtWidgets.QWidget()
        _rb = QtWidgets.QVBoxLayout(self.right_box)
        _rb.setContentsMargins(0, 0, 0, 0)
        _rb.setSpacing(2)
        _tb = QtWidgets.QHBoxLayout()
        _tb.setContentsMargins(2, 2, 6, 0)
        _tb.addStretch(1)
        self.btn_shot_render = CButton(
            master=self.right_box, width=150, text="保存到档案（截图）",
            command=lambda: self._save_shot_to_archive("render"))
        _tb.addWidget(self.btn_shot_render)
        _rb.addLayout(_tb)
        _rb.addWidget(self.right_stack, 1)
        self.splitter.addWidget(self.right_box)

        self.splitter.setHandleWidth(8)        # 可抓取宽度（默认太窄）
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setOpaqueResize(True)    # 拖动过程中实时重排，VTK 视图跟着变
        self.splitter.setSizes([480, 1040])    # 初始比例
        # 窗口整体变大时，把多出来的宽度给渲染视图（参数栏保持拖动后的宽度）
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        root_layout.addWidget(self.splitter, 1)

        self.setCentralWidget(central)

        self.renderer = vtk.vtkRenderer()
        self.renderer.SetBackground(0.97, 0.95, 0.92)  # Bright cream (浅色主题)
        self.render_window = self.vtk_widget.GetRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        
        # --- VTK 原生性能优化：设置期望的交互帧率 ---
        self.iren = self.render_window.GetInteractor()
        self.iren.SetDesiredUpdateRate(30.0)  # 交互时期望保持 30 帧
        self.iren.SetStillUpdateRate(0.0001)  # 静止时允许更多时间渲染高画质
        
        self.iren.SetInteractorStyle(vtk.vtkInteractorStyleTrackballCamera())

        def on_camera_modified(caller, event):
            if self.render_mode == "exposure_render":
                self._sync_ercore_state()
                
        self.renderer.GetActiveCamera().AddObserver("ModifiedEvent", on_camera_modified)

        self.ssd_slider.slider().valueChanged.connect(self.on_slider_change)
        self.vr_slider.slider().valueChanged.connect(self.on_slider_change)
        self.wl_slider.slider().valueChanged.connect(self.on_ww_wl_change)
        self.ww_slider.slider().valueChanged.connect(self.on_ww_wl_change)
        self.check_crop.toggled.connect(self.on_crop_toggle)
        self.mode_combo.combo_box().currentIndexChanged.connect(self.on_mode_change)
        self.mc_slider.slider().valueChanged.connect(self.on_cr_params_change)
        self.scatter_slider.slider().valueChanged.connect(self.on_cr_params_change)
        self.g_slider.slider().valueChanged.connect(self.on_cr_params_change)
        self.exp_slider.slider().valueChanged.connect(self.on_cr_params_change)
        self.primary_slider.slider().valueChanged.connect(self.on_cr_params_change)
        self.shadow_slider.slider().valueChanged.connect(self.on_cr_params_change)


        self.box_widget = None

        self.iren.Initialize()

        self.seg_renderer = vtk.vtkRenderer()
        self.seg_renderer.SetBackground(0.94, 0.91, 0.86)
        self.seg_renderer.SetViewport(0.0, 0.0, 0.0, 1.0)
        self.render_window.AddRenderer(self.seg_renderer)
        self.render_window.SetNumberOfLayers(2)
        self.pages.currentChanged.connect(self._on_tab_changed)

        # === 档案中心页（操作栏在左页签，档案册网格在右侧大区域）===
        self._build_archive_tab()

        # === 设置页（DeepSeek API 配置）===
        self._build_settings_tab()

        # --- 署名 / 版权：常驻状态栏右侧（强调色徽章，要求"显眼"）---------
        # 用 PyCt6 的 CLabel（遵循 master= 与 (浅色, 深色) 双色元组约定）；
        # 圆角 + 描边 + 半透明底由 CLabel 自身的 border_width/corner_radius/
        # background_color 参数生成，不手写样式表。
        # 放状态栏的理由：
        #   1) 主界面任何时候都可见，又不占用参数区/渲染区；
        #   2) addPermanentWidget 不会被 showMessage() 的临时提示顶掉；
        #   3) 截图（含 MCP 的 ssdvr_screenshot）会把它一起带出去。
        #
        # 字号/字重必须单独覆盖：dark.qss 里有全局
        #   QLabel { font-weight: 600; font-size: 9pt; }
        # 会把所有 QLabel 统一成 9pt 半粗，署名要更显眼就得加点力度。
        # 覆盖方式用「追加规则」而不是 setStyleSheet 整体替换 ——
        # 后者会丢掉 CLabel 按主题算出来的颜色。
        _CREDIT_TEXT = "designed by christ.paul90@gmail.com, all rights reserved"
        self.credit_label = CLabel(
            master=self,
            width=420,
            height=16,
            text=_CREDIT_TEXT,
            font_size=11,                                  # 渲染时会被下面的规则覆盖为 11pt
            text_color=("#b86a20", "#ffcfa0"),             # (浅色模式, 深色模式) 高对比
            background_color=("rgba(232,132,42,0.12)", "rgba(245,154,63,0.20)"),
            border_color=("rgba(232,132,42,0.55)", "rgba(245,154,63,0.65)"),
            border_width=1,
            corner_radius=9,                               # 胶囊形
            tooltip=_CREDIT_TEXT,
        )
        _credit_lbl = self.credit_label.label()
        # 字号/字重提权：dark.qss 里全局 QLabel{font-weight:600;font-size:9pt}
        # 会把署名压成普通小字。字体规则写在 dark.qss 的 QLabel#creditLabel 里，
        # **不能**在这里 setStyleSheet 追加 —— CLabel._change_theme() 在
        # 调色板/主题变化时会重写它自己的整个样式表，追加的规则会被抹掉
        # （实测确实被抹掉了）。这里只负责挂对象名 + 立刻重新 polish。
        _credit_lbl.setObjectName("creditLabel")
        _credit_lbl.style().unpolish(_credit_lbl)
        _credit_lbl.style().polish(_credit_lbl)
        self.statusBar().addPermanentWidget(self.credit_label)

        # 复古拟物皮肤：PyCt6 组件会用自身样式表覆盖应用级 qss，必须直接下发
        self._apply_retro_skin()

        # 档案中心：初始化列表
        try:
            self._archive_refresh()
        except Exception:  # noqa: BLE001
            pass

        # 应用「初始页签」对应的右区状态（首个页签不会触发 currentChanged）
        try:
            self._on_tab_changed(self.pages.currentIndex())
        except Exception:  # noqa: BLE001
            pass

        # 可爱卡通猫吉祥物（常驻状态栏左侧）
        self.cat_mascot = CatMascot(self)
        self.statusBar().addWidget(self.cat_mascot)

    def _apply_retro_skin(self) -> None:
        """给 PyCt6 组件内层控件下发复古拟物皮肤（凸起渐变 + 高光斜面 + 按压缩进）。"""
        try:
            btn_qss = (
                "QPushButton {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #ffd9a0, stop:0.42 #f8a24a, stop:0.5 #ef8f26, stop:1 #d97a18);"
                " color:#5a3208; font-weight:700;"
                " border:2px solid; border-top-color:#ffe9c6; border-left-color:#ffdfb0;"
                " border-right-color:#a85410; border-bottom-color:#8f4410;"
                " border-radius:9px; padding:4px 12px; }"
                "QPushButton:hover {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #ffe4b8, stop:0.5 #ffb057, stop:1 #e8862a); }"
                "QPushButton:pressed {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #c46810, stop:0.5 #e8862a, stop:1 #f8a24a);"
                " border-top-color:#8f4410; border-left-color:#a85410;"
                " border-right-color:#ffe9c6; border-bottom-color:#ffdfb0;"
                " padding-top:6px; padding-bottom:2px; }"
                "QPushButton:disabled { background:#e6d8c2; color:#a8907a;"
                " border-color:#d4c2a8; }"
            )
            for b in self.findChildren(CButton):
                try:
                    b.button().setStyleSheet(btn_qss)
                except Exception:
                    pass

            edit_qss = (
                "QLineEdit {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #e8d6b8, stop:1 #fffdf6);"
                " border:2px solid; border-top-color:#b8976c; border-left-color:#c8aa82;"
                " border-right-color:#fff4e0; border-bottom-color:#fff4e0;"
                " border-radius:7px; padding:3px 8px; color:#3a2e22;"
                " selection-background-color:#f0a95a; selection-color:#4a2a08; }"
            )
            for e in self.findChildren(CLineEdit):
                try:
                    e.line_edit().setStyleSheet(edit_qss)
                except Exception:
                    pass

            combo_qss = (
                "QComboBox {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #ffe6c2, stop:1 #efd3a6);"
                " border:2px solid; border-top-color:#fff2dc; border-left-color:#ffeccf;"
                " border-right-color:#a8783c; border-bottom-color:#9a6c34;"
                " border-radius:7px; padding:3px 8px; color:#3a2e22; }"
                "QComboBox::drop-down { border:none; width:22px; }"
                "QComboBox QAbstractItemView { background:#fffaf0; color:#3a2e22;"
                " border:1px solid #c8aa82; selection-background-color:#ffd9a8;"
                " selection-color:#5a3a10; }"
            )
            for cb in self.findChildren(CComboBox):
                try:
                    cb.combo_box().setStyleSheet(combo_qss)
                    # 关键：不要让下拉框按最长选项撑宽（否则会把面板撑到 600+，右侧被裁掉）
                    cb.combo_box().setSizeAdjustPolicy(
                        QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                    cb.combo_box().setMinimumContentsLength(8)
                    cb.combo_box().setMinimumWidth(110)
                    cb.combo_box().setSizePolicy(
                        QtWidgets.QSizePolicy.Policy.Expanding,
                        QtWidgets.QSizePolicy.Policy.Fixed)
                except Exception:
                    pass

            slider_qss = (
                "QSlider { min-height:22px; }"
                "QSlider::groove:horizontal { height:9px; border-radius:5px;"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #b8976c, stop:0.5 #e6d4b6, stop:1 #fff6e6);"
                " border:1px solid #a8834f; }"
                "QSlider::sub-page:horizontal {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #f6b45e, stop:1 #df7a12); border-radius:5px; }"
                "QSlider::handle:horizontal { width:18px; margin:-6px 0; border-radius:9px;"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #fff2d6, stop:0.5 #f9a83e, stop:1 #c96a14);"
                " border:2px solid #a85410; }"
                "QSlider::handle:horizontal:hover {"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #fff8ea, stop:1 #e8862a); }"
            )
            for s in self.findChildren(CSlider):
                try:
                    s.slider().setStyleSheet(slider_qss)
                except Exception:
                    pass
        except Exception:
            pass

    # ==================================================================
    # PACS 取片（C-ECHO / C-FIND / C-GET）
    # ==================================================================

    def _pacs_nodes_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "pacs_nodes.json")

    def _build_pacs_tab(self) -> None:
        """构建「PACS 节点」页：节点设置为父页，「取片」为其子页。"""
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(6)

        back_row = QtWidgets.QHBoxLayout()
        self.btn_pacs_back = CButton(master=page, width=140, text="← 返回爱宠列表",
                                     command=self._pacs_back_to_list)
        back_row.addWidget(self.btn_pacs_back)
        back_row.addStretch(1)
        outer.addLayout(back_row)

        tip = QtWidgets.QLabel(
            "连接 CT / DR / MRI 主机（PACS）：先在「节点设置」配置并测试，"
            "再到「取片」查询并取回完整序列，最后载入阅片。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a8641a; font-size:11px;")
        outer.addWidget(tip)

        # --- 子页切换条（① 节点设置 / ② 取片）---
        _seg_qss = (
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #f7e6c8, stop:1 #e8cfa2); color:#8a6a3c;"
            " border:2px solid; border-top-color:#fff6e6; border-left-color:#fbeed6;"
            " border-right-color:#b08c50; border-bottom-color:#a8834f;"
            " border-radius:7px; padding:4px 14px; font-weight:700; }"
            "QPushButton:hover:!checked { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #fff6e6, stop:1 #eddbb8); color:#a8641a; }"
            "QPushButton:checked { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #ffd89a, stop:1 #f8a63e); color:#5a3208;"
            " border-bottom-color:#c46a12; }")
        _bar = QtWidgets.QWidget()
        _bar_lay = QtWidgets.QHBoxLayout(_bar)
        _bar_lay.setContentsMargins(0, 0, 0, 0)
        _bar_lay.setSpacing(4)
        self.pacs_sub_node = QtWidgets.QPushButton("\u2460 节点设置")
        self.pacs_sub_get = QtWidgets.QPushButton("\u2461 取片 (C-GET)")
        for _b in (self.pacs_sub_node, self.pacs_sub_get):
            _b.setCheckable(True)
            _b.setAutoExclusive(True)
            _b.setMinimumHeight(26)
            _b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            _b.setStyleSheet(_seg_qss)
            _bar_lay.addWidget(_b)
        _bar_lay.addStretch(1)
        outer.addWidget(_bar)

        self.pacs_stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.pacs_stack, 1)

        # ---- 子页 0：节点设置 ----
        node_page = QtWidgets.QWidget()
        np_lay = QtWidgets.QVBoxLayout(node_page)
        np_lay.setContentsMargins(0, 0, 0, 0)
        ng = QtWidgets.QGroupBox("PACS / 影像主机节点")
        nf = QtWidgets.QGridLayout(ng)
        self.pacs_node_combo = CComboBox(master=ng, width=150, values=["（新建节点）"],
                                         current_value="（新建节点）")
        self.pacs_node_combo.combo_box().currentIndexChanged.connect(self._pacs_node_selected)
        self.pacs_name = CLineEdit(master=ng, width=150, placeholder_text="节点名（保存用）")
        self.pacs_host = CLineEdit(master=ng, width=170, placeholder_text="主机/IP")
        self.pacs_port = QtWidgets.QSpinBox()
        self.pacs_port.setRange(1, 65535)
        self.pacs_port.setValue(104)
        self.pacs_called = CLineEdit(master=ng, width=130, placeholder_text="被叫 AE")
        self.pacs_calling = CLineEdit(master=ng, width=130, placeholder_text="本机 AE")
        self.pacs_calling.line_edit().setText("SSDVR")
        self.pacs_timeout = QtWidgets.QDoubleSpinBox()
        self.pacs_timeout.setRange(1, 120)
        self.pacs_timeout.setValue(15)
        nf.addWidget(QtWidgets.QLabel("已存节点"), 0, 0)
        nf.addWidget(self.pacs_node_combo, 0, 1)
        nf.addWidget(QtWidgets.QLabel("节点名"), 0, 2)
        nf.addWidget(self.pacs_name, 0, 3)
        nf.addWidget(QtWidgets.QLabel("主机/IP"), 1, 0)
        nf.addWidget(self.pacs_host, 1, 1)
        nf.addWidget(QtWidgets.QLabel("端口"), 1, 2)
        nf.addWidget(self.pacs_port, 1, 3)
        nf.addWidget(QtWidgets.QLabel("被叫 AE"), 2, 0)
        nf.addWidget(self.pacs_called, 2, 1)
        nf.addWidget(QtWidgets.QLabel("本机 AE"), 2, 2)
        nf.addWidget(self.pacs_calling, 2, 3)
        nf.addWidget(QtWidgets.QLabel("超时(s)"), 3, 0)
        nf.addWidget(self.pacs_timeout, 3, 1)
        nrow = QtWidgets.QHBoxLayout()
        self.btn_pacs_save = CButton(master=ng, width=90, text="保存节点",
                                     command=self._pacs_save_node)
        self.btn_pacs_echo = CButton(master=ng, width=110, text="测试连接",
                                     command=self._pacs_echo)
        self.btn_pacs_goto_get = CButton(master=ng, width=120, text="前往取片 →",
                                         command=lambda: self.pacs_sub_get.setChecked(True))
        nrow.addWidget(self.btn_pacs_save)
        nrow.addWidget(self.btn_pacs_echo)
        nrow.addWidget(self.btn_pacs_goto_get)
        nrow.addStretch(1)
        nf.addLayout(nrow, 3, 2, 1, 2)
        np_lay.addWidget(ng)
        np_lay.addStretch(1)
        self.pacs_stack.addWidget(node_page)

        # ---- 子页 1：取片 ----
        get_page = QtWidgets.QWidget()
        gp_lay = QtWidgets.QVBoxLayout(get_page)
        gp_lay.setContentsMargins(0, 0, 0, 0)
        gp_lay.setSpacing(6)

        qg = QtWidgets.QGroupBox("查询 (C-FIND)")
        qf = QtWidgets.QGridLayout(qg)
        self.pacs_q_name = CLineEdit(master=qg, width=150, placeholder_text="姓名/名称（可空）")
        self.pacs_q_id = CLineEdit(master=qg, width=130, placeholder_text="编号（可空）")
        self.pacs_q_mod = CComboBox(master=qg, width=90,
                                    values=["全部", "CT", "MR", "DR", "CR", "US"],
                                    current_value="全部")
        self.pacs_q_from = CLineEdit(master=qg, width=120, placeholder_text="起始 YYYYMMDD")
        self.pacs_q_to = CLineEdit(master=qg, width=120, placeholder_text="结束 YYYYMMDD")
        qf.addWidget(QtWidgets.QLabel("姓名"), 0, 0)
        qf.addWidget(self.pacs_q_name, 0, 1)
        qf.addWidget(QtWidgets.QLabel("编号"), 0, 2)
        qf.addWidget(self.pacs_q_id, 0, 3)
        qf.addWidget(QtWidgets.QLabel("模态"), 1, 0)
        qf.addWidget(self.pacs_q_mod, 1, 1)
        qf.addWidget(QtWidgets.QLabel("日期"), 1, 2)
        _dt = QtWidgets.QHBoxLayout()
        _dt.addWidget(self.pacs_q_from)
        _dt.addWidget(QtWidgets.QLabel("~"))
        _dt.addWidget(self.pacs_q_to)
        qf.addLayout(_dt, 1, 3)
        qrow = QtWidgets.QHBoxLayout()
        self.btn_pacs_find = CButton(master=qg, width=110, text="查询检查",
                                     command=self._pacs_find_studies)
        self.btn_pacs_find_series = CButton(master=qg, width=170, text="查询选中检查的序列",
                                            command=self._pacs_find_series)
        self.btn_pacs_clear = CButton(master=qg, width=70, text="清空",
                                      command=lambda: self.pacs_tree.clear())
        qrow.addWidget(self.btn_pacs_find)
        qrow.addWidget(self.btn_pacs_find_series)
        qrow.addWidget(self.btn_pacs_clear)
        qrow.addStretch(1)
        qf.addLayout(qrow, 2, 0, 1, 4)
        gp_lay.addWidget(qg)

        self.pacs_tree = QtWidgets.QTreeWidget()
        self.pacs_tree.setHeaderLabels(["名称 / 序列", "编号", "模态", "日期", "实例数"])
        self.pacs_tree.setColumnWidth(0, 240)
        self.pacs_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        gp_lay.addWidget(self.pacs_tree, 1)

        gg = QtWidgets.QGroupBox("取片 (C-GET)")
        gf = QtWidgets.QGridLayout(gg)
        self.pacs_out = CLineEdit(master=gg, width=300,
                                  text=os.path.join(_external_dir(), "pacs_downloads"))
        self.btn_pacs_browse = CButton(master=gg, width=70, text="浏览",
                                       command=self._pacs_pick_out)
        self.btn_pacs_get = CButton(master=gg, width=170, text="获取选中序列 (C-GET)",
                                    command=self._pacs_retrieve)
        self.btn_pacs_use = CButton(master=gg, width=130, text="载入到阅片",
                                    command=self._pacs_load_result)
        gf.addWidget(QtWidgets.QLabel("保存目录"), 0, 0)
        gf.addWidget(self.pacs_out, 0, 1)
        gf.addWidget(self.btn_pacs_browse, 0, 2)
        _gr = QtWidgets.QHBoxLayout()
        _gr.addWidget(self.btn_pacs_get)
        _gr.addWidget(self.btn_pacs_use)
        _gr.addStretch(1)
        gf.addLayout(_gr, 1, 0, 1, 3)
        gp_lay.addWidget(gg)
        self.pacs_stack.addWidget(get_page)

        # --- 顶层进度 / 状态（两个子页都能看到）---
        self.pacs_prog = QtWidgets.QProgressBar()
        self.pacs_prog.setRange(0, 100)
        self.pacs_prog.setValue(0)
        self.pacs_status = QtWidgets.QLabel("")
        self.pacs_status.setWordWrap(True)
        self.pacs_status.setStyleSheet("color:#a8641a; font-size:11px;")
        outer.addWidget(self.pacs_prog)
        outer.addWidget(self.pacs_status)

        self.pacs_sub_node.toggled.connect(
            lambda on: self.pacs_stack.setCurrentIndex(0) if on else None)
        self.pacs_sub_get.toggled.connect(
            lambda on: self.pacs_stack.setCurrentIndex(1) if on else None)
        self.pacs_sub_node.setChecked(True)

        self._pacs_worker = None
        self._pacs_last_dir = ""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        # 作为「爱宠列表」页的**二级子页**（不占顶层页签），由「PACS节点」按钮触发显示
        self.patient_stack.addWidget(scroll)                # 1 = PACS 节点
        self._pacs_refresh_nodes()
        if pacs_client is None or not pacs_client.available():
            self.pacs_status.setText(
                "提示：未安装 pynetdicom，PACS 功能不可用。请执行  pip install pynetdicom")

    def _open_pacs_page(self) -> None:
        """「爱宠列表」页的 PACS节点 按钮 → 显示二级子页「PACS 节点」（默认节点设置）。"""
        try:
            self.pages.setCurrentIndex(0)          # 爱宠列表（PACS 子页的宿主）
            self.patient_stack.setCurrentIndex(1)  # 切到 PACS 节点二级子页
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "pacs_sub_node"):
            self.pacs_sub_node.setChecked(True)

    def _pacs_back_to_list(self) -> None:
        """从 PACS 二级子页返回爱宠列表。"""
        try:
            self.patient_stack.setCurrentIndex(0)
        except Exception:  # noqa: BLE001
            pass

    # ---- 节点保存 / 加载 ----
    def _pacs_read_nodes(self) -> dict:
        try:
            with open(self._pacs_nodes_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def _pacs_refresh_nodes(self) -> None:
        names = sorted(self._pacs_read_nodes().keys())
        combo = self.pacs_node_combo.combo_box()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("（新建节点）")
        for nm in names:
            combo.addItem(nm)
        combo.blockSignals(False)

    def _pacs_node_selected(self, idx: int) -> None:
        nodes = self._pacs_read_nodes()
        names = ["（新建节点）"] + sorted(nodes.keys())
        if idx <= 0 or idx >= len(names):
            return
        nd = nodes.get(names[idx], {})
        self.pacs_name.line_edit().setText(nd.get("name", names[idx]))
        self.pacs_host.line_edit().setText(nd.get("host", ""))
        self.pacs_port.setValue(int(nd.get("port", 104)))
        self.pacs_called.line_edit().setText(nd.get("called", ""))
        self.pacs_calling.line_edit().setText(nd.get("calling", "SSDVR"))
        self.pacs_timeout.setValue(float(nd.get("timeout", 15)))

    def _pacs_save_node(self) -> None:
        name = self.pacs_name.line_edit().text().strip()
        host = self.pacs_host.line_edit().text().strip()
        if not name or not host:
            QtWidgets.QMessageBox.warning(self, "节点不完整", "请填写 节点名 与 主机/IP。")
            return
        nodes = self._pacs_read_nodes()
        nodes[name] = {
            "name": name, "host": host, "port": int(self.pacs_port.value()),
            "called": self.pacs_called.line_edit().text().strip(),
            "calling": self.pacs_calling.line_edit().text().strip() or "SSDVR",
            "timeout": float(self.pacs_timeout.value()),
        }
        try:
            with open(self._pacs_nodes_path(), "w", encoding="utf-8") as f:
                json.dump(nodes, f, ensure_ascii=False, indent=2)
            self._pacs_refresh_nodes()
            self.pacs_status.setText("节点已保存：%s → %s" % (name, self._pacs_nodes_path()))
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))

    def _pacs_pick_out(self) -> None:
        cur = self.pacs_out.line_edit().text().strip() or _external_dir()
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择取片保存目录", cur)
        if d:
            self.pacs_out.line_edit().setText(d)

    def _pacs_params(self) -> dict:
        return {
            "host": self.pacs_host.line_edit().text().strip(),
            "port": int(self.pacs_port.value()),
            "called": self.pacs_called.line_edit().text().strip(),
            "calling": self.pacs_calling.line_edit().text().strip() or "SSDVR",
            "timeout": float(self.pacs_timeout.value()),
        }

    def _pacs_busy(self) -> bool:
        return self._pacs_worker is not None and self._pacs_worker.isRunning()

    def _pacs_start(self, job: str, params: dict) -> bool:
        if self._pacs_busy():
            self.pacs_status.setText("已有 PACS 任务在执行，请稍候…")
            return False
        p = self._pacs_params()
        if not p["host"]:
            QtWidgets.QMessageBox.warning(self, "缺少节点", "请先填写 主机/IP。")
            return False
        p.update(params or {})
        w = PacsWorker(job, p, self)
        w.progress.connect(self._pacs_on_progress)
        w.studies_ready.connect(self._pacs_on_studies)
        w.series_ready.connect(self._pacs_on_series)
        w.retrieve_done.connect(self._pacs_on_retrieve)
        w.echo_done.connect(self._pacs_on_echo)
        w.failed.connect(self._pacs_on_failed)
        self._pacs_worker = w
        w.start()
        return True

    def _pacs_on_progress(self, pct: int, msg: str) -> None:
        if pct:
            self.pacs_prog.setValue(max(0, min(100, int(pct))))
        self.pacs_status.setText(msg)

    def _pacs_on_echo(self, ok: bool, msg: str) -> None:
        self.pacs_status.setText(("✅ " if ok else "❌ ") + msg)
        if ok and hasattr(self, "pacs_sub_get"):
            self.pacs_sub_get.setChecked(True)   # 连接成功 → 直接进入取片

    def _pacs_on_failed(self, msg: str) -> None:
        self.pacs_prog.setValue(0)
        self.pacs_status.setText("❌ " + msg)
        if not self._mcp_mode:
            QtWidgets.QMessageBox.critical(self, "PACS 操作失败", msg)

    def _pacs_echo(self) -> None:
        self.pacs_prog.setValue(0)
        self.pacs_status.setText("正在测试连接 (C-ECHO)…")
        self._pacs_start("echo", {})

    def _pacs_find_studies(self) -> None:
        mod = self.pacs_q_mod.combo_box().currentText()
        self.pacs_prog.setValue(0)
        self.pacs_status.setText("正在查询检查 (C-FIND STUDY)…")
        self.pacs_tree.clear()
        self._pacs_start("studies", {
            "patient_name": self.pacs_q_name.line_edit().text().strip(),
            "patient_id": self.pacs_q_id.line_edit().text().strip(),
            "modality": "" if mod == "全部" else mod,
            "date_from": self.pacs_q_from.line_edit().text().strip(),
            "date_to": self.pacs_q_to.line_edit().text().strip(),
        })

    def _pacs_on_studies(self, studies) -> None:
        self.pacs_tree.clear()
        for s in studies:
            it = QtWidgets.QTreeWidgetItem([
                s.get("study_desc") or s.get("patient_name") or "(检查)",
                s.get("patient_id", ""), s.get("modality", ""),
                s.get("study_date", ""), s.get("instances", ""),
            ])
            it.setToolTip(0, "%s  %s" % (s.get("patient_name", ""), s.get("study_desc", "")))
            it.setData(0, QtCore.Qt.ItemDataRole.UserRole, dict({"kind": "study"}, **s))
            self.pacs_tree.addTopLevelItem(it)
        self.pacs_prog.setValue(100)
        self.pacs_status.setText(
            "查询完成：%d 个检查。选中一个检查后点「查询选中检查的序列」。" % len(studies))

    def _pacs_find_series(self) -> None:
        sel = self.pacs_tree.selectedItems()
        if not sel:
            QtWidgets.QMessageBox.information(self, "未选中", "请先选中一个检查。")
            return
        it = sel[0]
        data = it.data(0, QtCore.Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") != "study":
            par = it.parent()
            data = (par.data(0, QtCore.Qt.ItemDataRole.UserRole) if par else {}) or {}
        uid = data.get("study_uid", "")
        if not uid:
            QtWidgets.QMessageBox.information(self, "无效检查", "该检查缺少 StudyInstanceUID。")
            return
        self.pacs_prog.setValue(0)
        self.pacs_status.setText("正在查询序列 (C-FIND SERIES)…")
        self._pacs_start("series", {"study_uid": uid})

    def _pacs_on_series(self, study_uid: str, series) -> None:
        target = None
        for i in range(self.pacs_tree.topLevelItemCount()):
            it = self.pacs_tree.topLevelItem(i)
            d = it.data(0, QtCore.Qt.ItemDataRole.UserRole) or {}
            if d.get("study_uid") == study_uid:
                target = it
                break
        if target is None:
            return
        target.takeChildren()
        for s in series:
            c = QtWidgets.QTreeWidgetItem([
                s.get("series_desc") or "(序列)",
                s.get("series_number", ""), s.get("modality", ""), "", s.get("instances", ""),
            ])
            c.setData(0, QtCore.Qt.ItemDataRole.UserRole,
                      dict({"kind": "series", "study_uid": study_uid}, **s))
            target.addChild(c)
        target.setExpanded(True)
        self.pacs_prog.setValue(100)
        self.pacs_status.setText(
            "该检查下 %d 个序列。可多选序列后点「获取选中序列」。" % len(series))

    def _pacs_retrieve(self) -> None:
        pairs = []
        for it in self.pacs_tree.selectedItems():
            d = it.data(0, QtCore.Qt.ItemDataRole.UserRole) or {}
            if d.get("kind") == "series" and d.get("series_uid"):
                pairs.append((d.get("study_uid", ""), d["series_uid"]))
        if not pairs:
            QtWidgets.QMessageBox.information(
                self, "未选中序列", "请在树上选中要取回的「序列」行（可多选）。")
            return
        out = self.pacs_out.line_edit().text().strip() or os.path.join(
            _external_dir(), "pacs_downloads")
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "目录不可用", str(exc))
            return
        self.pacs_prog.setValue(0)
        self.pacs_status.setText("开始 C-GET：%d 个序列…" % len(pairs))
        self._pacs_start("get", {"series": pairs, "out_dir": out})

    def _pacs_on_retrieve(self, series_dir: str, count: int) -> None:
        self._pacs_last_dir = series_dir
        self.pacs_prog.setValue(100)
        if count > 0:
            self.pacs_status.setText(
                "取片完成：共 %d 张 → %s\n点「载入到阅片」把它加载到查看器。"
                % (count, series_dir))
        else:
            self.pacs_status.setText(
                "未接收到影像（该 PACS 可能不允许 C-GET，或序列为空）。")

    def _pacs_load_result(self) -> None:
        d = self._pacs_last_dir
        if not d or not os.path.isdir(d):
            QtWidgets.QMessageBox.information(self, "无可载入", "请先取片，或取片目录为空。")
            return
        self.path_edit.line_edit().setText(d)
        try:
            self.patient_stack.setCurrentIndex(0)   # 回到爱宠列表子页
        except Exception:  # noqa: BLE001
            pass
        self.pages.setCurrentIndex(self._tab_index_by_text("渲染"))
        self.load_dicom()

    def _on_tab_changed(self, index: int) -> None:
        """页签切换时决定右侧大区域显示什么：
        - MPR 阅片：隐藏整个右区，阅片页占满窗口
        - 档案中心：右区显示「档案册」网格（参数栏在左侧页签）
        - 其它：右区显示 3D 渲染视图
        """
        if not hasattr(self, "vtk_widget") or not hasattr(self, "splitter"):
            return
        try:
            title = self.pages.tabText(index)
        except Exception:
            title = ""
        # 爱宠列表 / 设置 / MPR 页都不需要右侧渲染区 → 占满整窗
        is_reader = (title.startswith("MPR") or title.startswith("设置")
                     or title.startswith("爱宠"))
        is_arch = title.startswith("\u6863\u6848")
        # 右区内容
        if hasattr(self, "right_stack"):
            try:
                if is_arch and hasattr(self, "arch_grid_host"):
                    self.right_stack.setCurrentWidget(self.arch_grid_host)
                    self.arch_grid_host.setVisible(True)
                else:
                    self.right_stack.setCurrentWidget(self.vtk_widget)
            except Exception:
                pass
            _container = getattr(self, "right_box", self.right_stack)
            _container.setVisible(not is_reader)
            if hasattr(self, "btn_shot_render"):
                self.btn_shot_render.setVisible(not (is_reader or is_arch))
        else:
            self.vtk_widget.setVisible(not is_reader)
        try:
            if is_reader:
                self.splitter.setSizes([1, 0])
            elif is_arch:
                self.splitter.setSizes([360, 1400])   # 操作栏收窄，档案册放大
            else:
                self.splitter.setSizes([480, 1040])
        except Exception:
            pass
        try:
            self.iren.Render()
        except Exception:
            pass

    def pick_dir(self) -> None:
        current = self.path_edit.line_edit().text().strip() or os.getcwd()
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "选择 DICOM 序列目录", current)
        if selected:
            self.path_edit.line_edit().setText(selected)

    def pick_file(self) -> None:
        current = self.path_edit.line_edit().text().strip() or os.getcwd()
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 DICOM 文件", current, "DICOM (*.dcm);;All files (*.*)"
        )
        if selected:
            self.path_edit.line_edit().setText(selected)

    # ==================================================================
    # 「爱宠列表」页（左侧参数栏的第一个页签，位于「渲染」之前）
    #
    # 扫描根目录默认取「渲染」页已填的路径（也可手动改/另选）。
    # 根目录下每个子文件夹视为一个爱宠/病例；双击一行 = 把该病例路径
    # 填回渲染页并复用 load_dicom() 直接加载。
    # 扫描逻辑在 mcp_ssd_vr/dicom.py::scan_patients()，只读 DICOM 头，很快。
    # ==================================================================
    # ==================================================================
    # 设置页：DeepSeek API Key 配置
    # ==================================================================
    def _build_settings_tab(self) -> None:
        """「设置」页：配置 DeepSeek API Key（也可用环境变量 DEEPSEEK_API_KEY）。"""
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(6)

        tip = QtWidgets.QLabel(
            "DeepSeek API 配置：AI 引导补全使用此密钥。\n"
            "配置保存在项目根 deepseek_config.json（已加入 .gitignore）；"
            "环境变量 DEEPSEEK_API_KEY 作为回退。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a8641a; font-size:11px;")
        outer.addWidget(tip)

        _qss = ("QLineEdit { background:#fffdf6; color:#3a2e22; border:2px solid;"
                " border-top-color:#a8834f; border-left-color:#bb9760;"
                " border-right-color:#fff6e6; border-bottom-color:#fff6e6;"
                " border-radius:6px; padding:3px 8px; font-size:9pt; }")

        grp = QtWidgets.QGroupBox("DeepSeek API")
        gl = QtWidgets.QVBoxLayout(grp)
        gl.setSpacing(5)

        gl.addWidget(QtWidgets.QLabel("API Key"))
        _krow = QtWidgets.QHBoxLayout()
        self.set_key = QtWidgets.QLineEdit()
        self.set_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.set_key.setPlaceholderText("sk-...（粘贴 DeepSeek API Key）")
        self.set_key.setStyleSheet(_qss)
        self.set_show_key = QtWidgets.QCheckBox("显示")
        self.set_show_key.toggled.connect(
            lambda on: self.set_key.setEchoMode(
                QtWidgets.QLineEdit.EchoMode.Normal if on
                else QtWidgets.QLineEdit.EchoMode.Password))
        _krow.addWidget(self.set_key, 1)
        _krow.addWidget(self.set_show_key)
        gl.addLayout(_krow)

        gl.addWidget(QtWidgets.QLabel("Base URL"))
        self.set_base = QtWidgets.QLineEdit("https://api.deepseek.com")
        self.set_base.setStyleSheet(_qss)
        gl.addWidget(self.set_base)

        gl.addWidget(QtWidgets.QLabel("模型"))
        self.set_model = QtWidgets.QComboBox()
        self.set_model.setEditable(True)
        self.set_model.addItems(["deepseek-chat", "deepseek-reasoner"])
        gl.addWidget(self.set_model)

        gl.addWidget(QtWidgets.QLabel("温度 (0 ~ 2)"))
        self.set_temp = QtWidgets.QDoubleSpinBox()
        self.set_temp.setRange(0.0, 2.0)
        self.set_temp.setSingleStep(0.1)
        self.set_temp.setDecimals(2)
        self.set_temp.setValue(0.30)
        gl.addWidget(self.set_temp)

        _btn = QtWidgets.QHBoxLayout()
        self.set_save = CButton(master=grp, width=120, text="保存配置",
                                command=self._settings_save)
        self.set_test = CButton(master=grp, width=120, text="测试连接",
                                command=self._settings_test)
        self.set_clear = CButton(master=grp, width=110, text="清除 Key",
                                 command=self._settings_clear_key)
        _btn.addWidget(self.set_save)
        _btn.addWidget(self.set_test)
        _btn.addWidget(self.set_clear)
        _btn.addStretch(1)
        gl.addLayout(_btn)

        self.set_status = QtWidgets.QLabel("")
        self.set_status.setWordWrap(True)
        self.set_status.setStyleSheet("color:#a8641a; font-size:11px;")
        gl.addWidget(self.set_status)
        outer.addWidget(grp)
        outer.addStretch(1)
        self._ai_test_worker = None
        self.pages.addTab(page, "设置")
        self._settings_load()

    def _settings_load(self) -> None:
        try:
            cfg = self._ai_config() or {}
            self.set_key.setText(cfg.get("api_key", "") or "")
            self.set_base.setText(cfg.get("base_url") or "https://api.deepseek.com")
            _m = cfg.get("model") or "deepseek-chat"
            _i = self.set_model.findText(_m)
            if _i >= 0:
                self.set_model.setCurrentIndex(_i)
            else:
                self.set_model.setEditText(_m)
            self.set_temp.setValue(float(cfg.get("temperature", 0.3) or 0.3))
            self.set_status.setText(
                "配置文件：%s%s" % (self._ai_config_path(),
                                 "" if cfg.get("api_key") else "　（尚未配置 Key）"))
        except Exception as exc:  # noqa: BLE001
            self.set_status.setText("读取配置失败：%s" % exc)

    def _settings_save(self) -> None:
        if deepseek_client is None:
            QtWidgets.QMessageBox.warning(self, "不可用", "缺少 deepseek_client.py")
            return
        cfg = self._ai_config() or {}
        cfg.update({
            "api_key": self.set_key.text().strip(),
            "base_url": self.set_base.text().strip() or "https://api.deepseek.com",
            "model": (self.set_model.currentText() or "deepseek-chat").strip(),
            "temperature": float(self.set_temp.value()),
        })
        try:
            deepseek_client.save_config(self._ai_config_path(), cfg)
            self.set_status.setText("已保存配置：%s" % self._ai_config_path())
            self._ai_refresh_status()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))

    def _settings_clear_key(self) -> None:
        if deepseek_client is None:
            return
        cfg = self._ai_config() or {}
        cfg["api_key"] = ""
        try:
            deepseek_client.save_config(self._ai_config_path(), cfg)
            self.set_key.setText("")
            self.set_status.setText("已清除 Key（仍可用环境变量 DEEPSEEK_API_KEY）")
            self._ai_refresh_status()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "清除失败", str(exc))

    def _settings_test(self) -> None:
        if deepseek_client is None:
            QtWidgets.QMessageBox.warning(self, "不可用", "缺少 deepseek_client.py")
            return
        self._settings_save()
        cfg = self._ai_config() or {}
        if not (cfg.get("api_key") or "").strip():
            self.set_status.setText("请先填写 API Key。")
            return
        if getattr(self, "_ai_test_worker", None) is not None and self._ai_test_worker.isRunning():
            self.set_status.setText("上一次测试仍在进行…")
            return
        self.set_status.setText("正在测试连接…")
        w = AiWorker(cfg, [{"role": "user", "content": "ping"}], self)
        w.done.connect(lambda t: self.set_status.setText(
            "连接成功：%s" % (t or "").replace("\n", " ")[:60]))
        w.failed.connect(lambda m: self.set_status.setText("连接失败：%s" % m))
        self._ai_test_worker = w
        w.start()

    def _build_archive_tab(self) -> None:
        """「档案中心」：左侧页签放操作栏；右侧大区域放「档案册」网格（render.png 封面）。"""
        # ---------------- 左：操作页（顶层页签）----------------
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(6)

        tip = QtWidgets.QLabel(
            "从「爱宠列表」选中序列后，MPR 阅片与渲染都会指向该序列。\n"
            "单击右侧档案 → 在右侧查看/编辑它的病例信息；双击 → 直接载入其渲染与 MPR 参数。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a8641a; font-size:11px;")
        outer.addWidget(tip)

        grp = QtWidgets.QGroupBox("爱宠档案")
        gl = QtWidgets.QVBoxLayout(grp)
        self.arch_case_lb = QtWidgets.QLabel("当前爱宠：—（未加载序列）")
        self.arch_case_lb.setWordWrap(True)
        self.arch_case_lb.setStyleSheet("color:#a8641a; font-size:11px;")
        gl.addWidget(self.arch_case_lb)
        self.arch_name = CLineEdit(master=grp, placeholder_text="档案名称 / 备注（可空）")
        gl.addWidget(self.arch_name)
        _ar1 = QtWidgets.QHBoxLayout()
        self.arch_btn_save = CButton(master=grp, width=110, text="保存当前档案",
                                     command=self._archive_save)
        self.arch_btn_refresh = CButton(master=grp, width=66, text="刷新",
                                        command=self._archive_refresh)
        self.arch_btn_dir = CButton(master=grp, width=86, text="打开目录",
                                    command=self._archive_open_dir)
        _ar1.addWidget(self.arch_btn_save)
        _ar1.addWidget(self.arch_btn_refresh)
        _ar1.addWidget(self.arch_btn_dir)
        _ar1.addStretch(1)
        gl.addLayout(_ar1)
        self.arch_only_series = QtWidgets.QCheckBox("仅显示当前序列的档案")
        self.arch_only_series.setChecked(True)
        self.arch_only_series.toggled.connect(self._archive_refresh)
        gl.addWidget(self.arch_only_series)
        outer.addWidget(grp)

        grp2 = QtWidgets.QGroupBox("选中档案")
        gl2 = QtWidgets.QVBoxLayout(grp2)
        self.arch_sel_lb = QtWidgets.QLabel("未选中（在右侧档案册里点选）")
        self.arch_sel_lb.setWordWrap(True)
        self.arch_sel_lb.setStyleSheet("color:#7a6650; font-size:11px;")
        gl2.addWidget(self.arch_sel_lb)
        self.arch_btn_load = CButton(master=grp2, width=150, text="载入选中档案",
                                     command=self._archive_load_selected)
        self.arch_btn_del = CButton(master=grp2, width=120, text="删除选中",
                                    command=self._archive_delete_selected)
        gl2.addWidget(self.arch_btn_load)
        gl2.addWidget(self.arch_btn_del)
        outer.addWidget(grp2)

        self.arch_status = QtWidgets.QLabel("")
        self.arch_status.setWordWrap(True)
        self.arch_status.setStyleSheet("color:#a8641a; font-size:11px;")
        outer.addWidget(self.arch_status)
        self.pages.addTab(page, "档案中心")

        # ---------------- 右：档案册（放入右区大区域）----------------
        host = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(host)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(6)
        _cap = QtWidgets.QLabel("档案册（render.png 封面 · 双击载入）")
        _cap.setStyleSheet("color:#a8641a; font-weight:700;")
        hl.addWidget(_cap)
        self.arch_grid = QtWidgets.QListWidget()
        self.arch_grid.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.arch_grid.setIconSize(QtCore.QSize(136, 136))
        self.arch_grid.setGridSize(QtCore.QSize(198, 200))
        self.arch_grid.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.arch_grid.setMovement(QtWidgets.QListView.Movement.Static)
        self.arch_grid.setWordWrap(True)
        self.arch_grid.setUniformItemSizes(True)
        self.arch_grid.setSpacing(10)
        self.arch_grid.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.arch_grid.itemClicked.connect(self._archive_open_detail)
        self.arch_grid.itemDoubleClicked.connect(
            lambda _it: self._archive_load_selected())
        self.arch_grid.itemSelectionChanged.connect(self._archive_selection_changed)
        hl.addWidget(self.arch_grid, 1)
        self.arch_grid_host = host
        if hasattr(self, "right_stack"):
            self.right_stack.addWidget(host)

        # ---------------- 右：档案详情（病例信息）----------------
        detail = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(detail)
        dl.setContentsMargins(10, 8, 10, 8)
        dl.setSpacing(6)

        _top = QtWidgets.QHBoxLayout()
        self.detail_back = CButton(master=detail, width=120, text="← 返回档案册",
                                   command=self._arch_detail_back)
        self.detail_apply = CButton(master=detail, width=170, text="载入该档案的参数",
                                    command=self._arch_detail_apply_params)
        _top.addWidget(self.detail_back)
        _top.addWidget(self.detail_apply)
        _top.addStretch(1)
        dl.addLayout(_top)

        self.detail_title = QtWidgets.QLabel("—")
        self.detail_title.setStyleSheet("color:#a8641a; font-weight:700; font-size:13pt;")
        dl.addWidget(self.detail_title)
        self.detail_case = QtWidgets.QLabel("")
        self.detail_case.setWordWrap(True)
        self.detail_case.setStyleSheet("color:#7a6650; font-size:11px;")
        dl.addWidget(self.detail_case)

        _thumb = QtWidgets.QHBoxLayout()
        _tqss = ("QLabel { background:#fffdf6; border:2px solid #c9a86e;"
                 " border-radius:6px; color:#a08b70; }")
        self.detail_thumb1 = QtWidgets.QLabel("无 render.png")
        self.detail_thumb1.setFixedSize(320, 210)
        self.detail_thumb1.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.detail_thumb1.setStyleSheet(_tqss)
        self.detail_thumb2 = QtWidgets.QLabel("无 mpr.png")
        self.detail_thumb2.setFixedSize(320, 210)
        self.detail_thumb2.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.detail_thumb2.setStyleSheet(_tqss)
        _thumb.addWidget(self.detail_thumb1)
        _thumb.addWidget(self.detail_thumb2)
        _thumb.addStretch(1)
        dl.addLayout(_thumb)

        self.detail_shots = QtWidgets.QLabel("")
        self.detail_shots.setWordWrap(True)
        self.detail_shots.setStyleSheet("color:#7a6650; font-size:11px;")
        dl.addWidget(self.detail_shots)

        # ---- 子页面：① 当前病例信息  ② 历史版本（时间戳）----
        _seg = (
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #f7e6c8, stop:1 #e8cfa2); color:#8a6a3c;"
            " border:2px solid; border-top-color:#fff6e6; border-left-color:#fbeed6;"
            " border-right-color:#b08c50; border-bottom-color:#a8834f;"
            " border-radius:7px; padding:4px 14px; font-weight:700; }"
            "QPushButton:hover:!checked { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #fff6e6, stop:1 #eddbb8); color:#a8641a; }"
            "QPushButton:checked { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #ffd89a, stop:1 #f8a63e); color:#5a3208;"
            " border-bottom-color:#c46a12; }")
        _sbar = QtWidgets.QHBoxLayout()
        self.detail_sub_cur = QtWidgets.QPushButton("\u2460 当前病例信息")
        self.detail_sub_hist = QtWidgets.QPushButton("\u2461 历史版本（时间戳）")
        self.detail_sub_ai = QtWidgets.QPushButton("\u2462 AI 引导补全")
        for _b in (self.detail_sub_cur, self.detail_sub_hist, self.detail_sub_ai):
            _b.setCheckable(True)
            _b.setAutoExclusive(True)
            _b.setMinimumHeight(26)
            _b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            _b.setStyleSheet(_seg)
            _sbar.addWidget(_b)
        _sbar.addStretch(1)
        dl.addLayout(_sbar)

        self.detail_stack = QtWidgets.QStackedWidget()
        dl.addWidget(self.detail_stack, 1)

        # --- 子页 ①：当前病例信息（模板表单）---
        _pa = QtWidgets.QWidget()
        _al = QtWidgets.QVBoxLayout(_pa)
        _al.setContentsMargins(0, 0, 0, 0)
        _al.setSpacing(6)
        self._build_case_form(_al)
        _save = QtWidgets.QHBoxLayout()
        self.detail_save = CButton(master=_pa, width=150, text="保存病例信息",
                                   command=self._arch_detail_save_clinical)
        _save.addWidget(self.detail_save)
        _save.addStretch(1)
        _al.addLayout(_save)
        self.detail_stack.addWidget(_pa)

        # --- 子页 ②：历史版本（时间戳列表 + 预览 + 恢复）---
        _pb = QtWidgets.QWidget()
        _bl = QtWidgets.QVBoxLayout(_pb)
        _bl.setContentsMargins(0, 0, 0, 0)
        _bl.setSpacing(6)
        _hrow = QtWidgets.QHBoxLayout()
        self.detail_hist_refresh = CButton(master=_pb, width=110, text="刷新版本",
                                          command=self._arch_detail_load_history)
        _hrow.addWidget(self.detail_hist_refresh)
        self.detail_hist_restore = CButton(master=_pb, width=150, text="恢复此版本",
                                          command=self._arch_detail_restore)
        _hrow.addWidget(self.detail_hist_restore)
        _hrow.addStretch(1)
        _bl.addLayout(_hrow)
        self.detail_hist_list = QtWidgets.QListWidget()
        self.detail_hist_list.setMaximumHeight(150)
        self.detail_hist_list.currentRowChanged.connect(
            lambda _i: self._arch_detail_hist_selected())
        _bl.addWidget(self.detail_hist_list)
        self.detail_hist_view = QtWidgets.QTextEdit()
        self.detail_hist_view.setReadOnly(True)
        self.detail_hist_view.setStyleSheet(
            "QTextEdit { background:#fffdf6; color:#3a2e22; border:2px solid #c9a86e;"
            " border-radius:6px; padding:4px 8px; font-size:9pt; }")
        _bl.addWidget(self.detail_hist_view, 1)
        self.detail_stack.addWidget(_pb)

        # --- 子页 ③：AI 引导补全（DeepSeek）---
        _pc = QtWidgets.QWidget()
        _cl = QtWidgets.QVBoxLayout(_pc)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(6)
        self.ai_status_lb = QtWidgets.QLabel("")
        self.ai_status_lb.setWordWrap(True)
        self.ai_status_lb.setStyleSheet("color:#a8641a; font-size:11px;")
        _cl.addWidget(self.ai_status_lb)
        _r1 = QtWidgets.QHBoxLayout()
        self.ai_btn_key = CButton(master=_pc, width=130, text="配置 API Key",
                                  command=self._ai_set_key)
        self.ai_btn_run = CButton(master=_pc, width=150, text="AI 引导补全",
                                  command=self._ai_run)
        self.ai_btn_apply = CButton(master=_pc, width=160, text="应用建议到表单",
                                    command=self._ai_apply)
        self.ai_btn_apply.setEnabled(False)
        _r1.addWidget(self.ai_btn_key)
        _r1.addWidget(self.ai_btn_run)
        _r1.addWidget(self.ai_btn_apply)
        _r1.addStretch(1)
        _cl.addLayout(_r1)
        _r2 = QtWidgets.QHBoxLayout()
        self.ai_prompt = CLineEdit(master=_pc, width=420,
                                   text="根据影像所见与既往版本，补全并完善评估、诊断与建议；事实缺失处写“待确认”。")
        _r2.addWidget(self.ai_prompt, 1)
        self.ai_autosave = QtWidgets.QCheckBox("应用后自动保存（生成时间戳版本）")
        self.ai_autosave.setChecked(True)
        _r2.addWidget(self.ai_autosave)
        _cl.addLayout(_r2)
        self.ai_out = QtWidgets.QTextEdit()
        self.ai_out.setReadOnly(True)
        self.ai_out.setStyleSheet(
            "QTextEdit { background:#fffdf6; color:#3a2e22; border:2px solid #c9a86e;"
            " border-radius:6px; padding:4px 8px; font-size:9pt; }")
        _cl.addWidget(self.ai_out, 1)
        self.detail_stack.addWidget(_pc)
        self._ai_worker = None
        self._ai_suggest = None

        self.detail_sub_ai.toggled.connect(
            # 切到「③ AI 引导补全」子页（detail_stack 第 2 页）并刷新 Key 状态。
            # 以前只刷新状态、没切页，导致该子页永远看不到。
            lambda on: (self.detail_stack.setCurrentIndex(2), self._ai_refresh_status())
            if on else None)

        self.detail_sub_cur.toggled.connect(
            lambda on: self.detail_stack.setCurrentIndex(0) if on else None)
        self.detail_sub_hist.toggled.connect(
            lambda on: (self.detail_stack.setCurrentIndex(1),
                        self._arch_detail_load_history()) if on else None)
        self.detail_sub_cur.setChecked(True)

        self.detail_status = QtWidgets.QLabel("")
        self.detail_status.setWordWrap(True)
        self.detail_status.setStyleSheet("color:#a8641a; font-size:11px;")
        dl.addWidget(self.detail_status)

        self.arch_detail_host = detail
        if hasattr(self, "right_stack"):
            self.right_stack.addWidget(detail)

    # ---------------- 截图保存到病例档案 ----------------
    def _newest_archive_dir(self) -> str:
        root = self._archive_root()
        try:
            names = sorted(os.listdir(root), reverse=True)
        except Exception:  # noqa: BLE001
            return ""
        for nm in names:
            d = os.path.join(root, nm)
            mp = os.path.join(d, "meta.json")
            if not os.path.isfile(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            if not meta.get("demo"):
                return d
        return ""

    def _archive_target_for_current(self) -> str:
        """当前序列对应的档案目录：详情页打开的 → 同序列最新 → 自动新建 → 空。"""
        d = getattr(self, "_arch_detail_dir", "")
        if d and os.path.isfile(os.path.join(d, "meta.json")):
            return d
        cur = self._current_case_info()
        uid = cur.get("series_uid", "")
        root = self._archive_root()
        try:
            names = sorted(os.listdir(root), reverse=True)
        except Exception:  # noqa: BLE001
            names = []
        for nm in names:
            dd = os.path.join(root, nm)
            mp = os.path.join(dd, "meta.json")
            if not os.path.isfile(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            if meta.get("demo"):
                continue
            if uid and (meta.get("case", {}) or {}).get("series_uid") == uid:
                return dd
        # 没有对应档案：若已加载序列则自动建一条
        if cur.get("path"):
            try:
                self._archive_save()
                return self._newest_archive_dir()
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def _save_shot_to_archive(self, kind: str = "render") -> None:
        """截图当前显示状态，保存进对应病例档案（meta.shots）。"""
        import time as _t
        import shutil as _sh
        d = self._archive_target_for_current()
        if not d:
            QtWidgets.QMessageBox.information(
                self, "无对应档案",
                "当前序列还没有档案。请先在「档案中心」点「保存当前档案」。")
            return
        ts = _t.strftime("%Y%m%d_%H%M%S")
        fn = "shot_%s_%s.png" % (kind, ts)
        path = os.path.join(d, fn)
        p = (self._archive_grab_mpr(path) if kind == "mpr"
             else self._archive_grab_render(path))
        if not p:
            self.statusBar().showMessage("截图失败。", 5000)
            return
        mp = os.path.join(d, "meta.json")
        try:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:  # noqa: BLE001
            meta = {}
        shots = meta.get("shots") or []
        shots.append({"file": fn, "time": _t.strftime("%Y-%m-%d %H:%M:%S"),
                      "kind": kind})
        meta["shots"] = shots
        # 渲染截图可兼作封面（若无 render.png）
        if kind == "render" and not os.path.isfile(os.path.join(d, "render.png")):
            try:
                _sh.copyfile(p, os.path.join(d, "render.png"))
            except Exception:  # noqa: BLE001
                pass
        try:
            with open(mp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage("写入档案失败：%s" % exc, 6000)
            return
        self.statusBar().showMessage(
            "已保存%s截图到档案：%s（共 %d 张）"
            % ("MPR" if kind == "mpr" else "渲染", fn, len(shots)), 8000)
        try:
            self._archive_refresh()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "_arch_detail_dir", "") == d and hasattr(self, "detail_shots"):
                self.detail_shots.setText(self._detail_shots_text(meta))
                self.detail_status.setText("已追加截图：%s" % fn)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _detail_shots_text(meta: dict) -> str:
        shots = meta.get("shots") or []
        if not shots:
            return "截图：暂无（可在「渲染」或「MPR 阅片」视图右上角点「保存到档案（截图）」）"
        last = shots[-1]
        return "截图：共 %d 张 ｜ 最近 %s（%s）" % (
            len(shots), last.get("file", ""), last.get("kind", ""))

    # ---------------- 档案详情（病例信息）----------------
    def _archive_open_detail(self, item) -> None:
        """单击档案 → 右侧显示区进入该档案的病例信息页。"""
        if item is None:
            return
        d = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not d or not os.path.isdir(d):
            return
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as exc:  # noqa: BLE001
            self.arch_status.setText("读取档案失败：%s" % exc)
            return
        self._arch_detail_meta = meta
        self._arch_detail_dir = d
        case = meta.get("case", {}) or {}
        self.detail_title.setText(meta.get("name") or "(未命名)")
        self.detail_case.setText(
            "爱宠：%s（%s）｜ 序列：%s ｜ 模态：%s ｜ 时间：%s"
            % (case.get("patient_name") or case.get("patient_id") or "—",
               case.get("patient_id") or "—",
               case.get("series_desc") or os.path.basename(case.get("path", "")) or "—",
               case.get("modality") or "—",
               meta.get("time", "")))
        for w, fn in ((self.detail_thumb1, "render.png"),
                      (self.detail_thumb2, "mpr.png")):
            p = os.path.join(d, fn)
            pm = QPixmap(p) if os.path.isfile(p) else QPixmap()
            if not pm.isNull():
                w.setPixmap(pm.scaled(w.width(), w.height(),
                                      QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                      QtCore.Qt.TransformationMode.SmoothTransformation))
                w.setToolTip(fn)
            else:
                w.setPixmap(QPixmap())
                w.setText("无 %s" % fn)
        self._case_form_set(meta.get("clinical") or {})
        try:
            if hasattr(self, "detail_shots"):
                self.detail_shots.setText(self._detail_shots_text(meta))
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "detail_sub_cur"):
                self.detail_sub_cur.setChecked(True)
            self._arch_detail_load_history()
            self._ai_refresh_status()
            self._ai_suggest = None
            if hasattr(self, "ai_btn_apply"):
                self.ai_btn_apply.setEnabled(False)
            if hasattr(self, "ai_out"):
                self.ai_out.setPlainText("")
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "right_stack") and hasattr(self, "arch_detail_host"):
            self.right_stack.setCurrentWidget(self.arch_detail_host)
        self.detail_status.setText("档案目录：%s" % d)

    # ---------------- DeepSeek AI 引导补全 ----------------
    def _ai_config_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "deepseek_config.json")

    def _ai_config(self) -> dict:
        if deepseek_client is None:
            return {}
        try:
            return deepseek_client.load_config(self._ai_config_path())
        except Exception:  # noqa: BLE001
            return {}

    def _ai_refresh_status(self) -> None:
        if not hasattr(self, "ai_status_lb"):
            return
        if deepseek_client is None:
            self.ai_status_lb.setText("缺少 deepseek_client.py，AI 功能不可用。")
            if hasattr(self, "ai_btn_run"):
                self.ai_btn_run.setEnabled(False)
            return
        cfg = self._ai_config()
        has = bool((cfg or {}).get("api_key"))
        if has:
            self.ai_status_lb.setText(
                "已配置 DeepSeek（model=%s）。AI 会读取模板字段、当前填写内容与**全部时间戳版本**，"
                "给出引导式补全建议。" % cfg.get("model", ""))
        else:
            self.ai_status_lb.setText(
                "未配置 API Key：点「配置 API Key」，或设环境变量 DEEPSEEK_API_KEY。")
        if hasattr(self, "ai_btn_run"):
            self.ai_btn_run.setEnabled(bool(has))

    def _ai_set_key(self) -> None:
        """跳到「设置」页配置 API Key。"""
        try:
            i = self._tab_index_by_text("设置")
            if i >= 0:
                self.pages.setCurrentIndex(i)
            if hasattr(self, "_settings_load"):
                self._settings_load()
            if hasattr(self, "set_status"):
                self.set_status.setText("在此填写 API Key，然后点「保存配置」。")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.information(self, "设置", str(exc))

    def _ai_context(self) -> dict:
        """AI 上下文：模板字段 + 当前填写 + 全部时间戳版本 + 档案基本信息。"""
        d = getattr(self, "_arch_detail_dir", "")
        meta = getattr(self, "_arch_detail_meta", {}) or {}
        tpl = self._case_template()
        fields = []
        for sec in tpl.get("sections", []):
            for f in sec.get("fields", []):
                fields.append({"key": f.get("key"), "label": f.get("label"),
                               "type": f.get("type"), "section": sec.get("name")})
        hist = []
        rev_dir = os.path.join(d, "revisions") if d else ""
        try:
            names = sorted([n for n in os.listdir(rev_dir) if n.endswith(".json")],
                           reverse=True)
        except Exception:  # noqa: BLE001
            names = []
        for nm in names:
            try:
                with open(os.path.join(rev_dir, nm), encoding="utf-8") as f:
                    rev = json.load(f)
                hist.append({"file": nm, "time": rev.get("time", ""),
                             "clinical": rev.get("clinical") or {}})
            except Exception:  # noqa: BLE001
                pass
        return {"template_fields": fields, "current": self._case_form_values(),
                "history_versions": hist,
                "case_basic": meta.get("case", {}) if isinstance(meta, dict) else {}}

    def _ai_run(self) -> None:
        if deepseek_client is None:
            QtWidgets.QMessageBox.warning(self, "不可用", "缺少 deepseek_client.py")
            return
        cfg = self._ai_config()
        if not (cfg or {}).get("api_key"):
            QtWidgets.QMessageBox.information(
                self, "未配置", "请先点「配置 API Key」，或设环境变量 DEEPSEEK_API_KEY。")
            return
        if getattr(self, "_ai_worker", None) is not None and self._ai_worker.isRunning():
            self.ai_status_lb.setText("上一次推理仍在进行…")
            return
        ctx = self._ai_context()
        instr = self.ai_prompt.line_edit().text().strip()
        sys_msg = ("你是兽医影像科医师助手，负责补全宠物病例档案。"
                   "只输出一个 JSON 对象，键为给定字段 key，值为中文字符串；"
                   "不要编造未提供的事实，缺失处写“待确认”；不要输出 JSON 以外的任何文字。")
        user_msg = json.dumps({
            "instruction": instr,
            "template_fields": ctx["template_fields"],
            "current": ctx["current"],
            "history_versions": ctx["history_versions"],
            "case_basic": ctx["case_basic"],
        }, ensure_ascii=False)
        self.ai_out.setPlainText(
            "推理中…（已读取 %d 个时间戳版本作为上下文）" % len(ctx["history_versions"]))
        self.ai_status_lb.setText("正在请求 DeepSeek …")
        w = AiWorker(cfg, [{"role": "system", "content": sys_msg},
                           {"role": "user", "content": user_msg}], self)
        w.done.connect(self._ai_on_done)
        w.failed.connect(self._ai_on_failed)
        self._ai_worker = w
        w.start()

    def _ai_on_done(self, text: str) -> None:
        self.ai_out.setPlainText(text or "")
        sug = self._ai_parse_json(text or "")
        self._ai_suggest = sug
        if sug:
            try:
                self.ai_btn_apply.setEnabled(True)
            except Exception:  # noqa: BLE001
                pass
            self.ai_status_lb.setText(
                "已获得建议（%d 个字段）→ 点「应用建议到表单」。" % len(sug))
        else:
            self.ai_status_lb.setText("模型回复不是 JSON，已原样显示（可参照手动填写）。")

    def _ai_on_failed(self, msg: str) -> None:
        self.ai_out.setPlainText("请求失败：" + (msg or ""))
        self.ai_status_lb.setText("AI 推理失败。")

    @staticmethod
    def _ai_parse_json(text: str):
        try:
            i = text.find("{")
            j = text.rfind("}")
            if i < 0 or j <= i:
                return None
            obj = json.loads(text[i:j + 1])
            if isinstance(obj, dict):
                return {str(k): v for k, v in obj.items()}
        except Exception:  # noqa: BLE001
            pass
        return None

    def _ai_apply(self) -> None:
        sug = getattr(self, "_ai_suggest", None)
        if not sug:
            return
        n = 0
        for k, w in getattr(self, "case_fields", {}).items():
            v = sug.get(k)
            if v is None:
                continue
            v = str(v).strip()
            if not v:
                continue
            try:
                if isinstance(w, QtWidgets.QTextEdit):
                    w.setPlainText(v)
                else:
                    w.setText(v)
                n += 1
            except Exception:  # noqa: BLE001
                pass
        d = getattr(self, "_arch_detail_dir", "")
        meta = getattr(self, "_arch_detail_meta", None)
        if meta is not None and d:
            try:
                import time as _t
                meta.setdefault("ai_log", []).append({
                    "time": _t.strftime("%Y-%m-%d %H:%M:%S"),
                    "instruction": self.ai_prompt.line_edit().text().strip(),
                    "applied_keys": n})
                with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception:  # noqa: BLE001
                pass
        self.ai_status_lb.setText("已应用 %d 个字段。" % n)
        try:
            if self.ai_autosave.isChecked():
                self._arch_detail_save_clinical()
        except Exception:  # noqa: BLE001
            pass

    def _case_text_from_values(self, vals: dict) -> str:
        """把病例字段按模板顺序排版成可读文本（历史版本预览用）。"""
        lines = []
        try:
            tpl = self._case_template()
        except Exception:  # noqa: BLE001
            tpl = {"sections": []}
        for sec in tpl.get("sections", []):
            lines.append("\u3010%s\u3011" % sec.get("name", ""))
            for fld in sec.get("fields", []):
                k = fld.get("key", "")
                v = str((vals or {}).get(k, "") or "")
                lines.append("  %s: %s" % (fld.get("label", k), v))
        return "\n".join(lines)

    def _arch_detail_load_history(self, *_):
        """扫描该档案 revisions/ 下的时间戳版本，填入列表。"""
        lst = getattr(self, "detail_hist_list", None)
        d = getattr(self, "_arch_detail_dir", "")
        if lst is None:
            return
        lst.blockSignals(True)
        lst.clear()
        self._detail_hist_files = []
        rev_dir = os.path.join(d, "revisions") if d else ""
        try:
            names = sorted([n for n in os.listdir(rev_dir) if n.endswith(".json")],
                           reverse=True)
        except Exception:  # noqa: BLE001
            names = []
        for nm in names:
            t = nm
            try:
                with open(os.path.join(rev_dir, nm), encoding="utf-8") as f:
                    t = json.load(f).get("time", nm)
            except Exception:  # noqa: BLE001
                pass
            lst.addItem("%s    (%s)" % (t, nm))
            self._detail_hist_files.append(nm)
        if not self._detail_hist_files:
            lst.addItem("（暂无历史版本：每次「保存病例信息」都会生成一个时间戳版本）")
            if hasattr(self, "detail_hist_view"):
                self.detail_hist_view.setPlainText("")
        lst.blockSignals(False)
        if self._detail_hist_files:
            lst.setCurrentRow(0)

    def _arch_detail_hist_selected(self):
        lst = getattr(self, "detail_hist_list", None)
        d = getattr(self, "_arch_detail_dir", "")
        files = getattr(self, "_detail_hist_files", [])
        if lst is None or not d:
            return
        i = lst.currentRow()
        if i < 0 or i >= len(files):
            return
        try:
            with open(os.path.join(d, "revisions", files[i]), encoding="utf-8") as f:
                rev = json.load(f)
        except Exception as exc:  # noqa: BLE001
            self.detail_hist_view.setPlainText("读取失败：%s" % exc)
            return
        self.detail_hist_view.setPlainText(
            "%s\n\n%s" % (rev.get("time", files[i]),
                          self._case_text_from_values(rev.get("clinical") or {})))

    def _arch_detail_restore(self):
        """把选中历史版本回填到表单（需再点「保存病例信息」才生效）。"""
        lst = getattr(self, "detail_hist_list", None)
        d = getattr(self, "_arch_detail_dir", "")
        files = getattr(self, "_detail_hist_files", [])
        if lst is None or not d:
            return
        i = lst.currentRow()
        if i < 0 or i >= len(files):
            self.detail_status.setText("请先在列表中选择一个历史版本。")
            return
        try:
            with open(os.path.join(d, "revisions", files[i]), encoding="utf-8") as f:
                rev = json.load(f)
        except Exception as exc:  # noqa: BLE001
            self.detail_status.setText("读取失败：%s" % exc)
            return
        self._case_form_set(rev.get("clinical") or {})
        if hasattr(self, "detail_sub_cur"):
            self.detail_sub_cur.setChecked(True)
        self.detail_status.setText(
            "已恢复到版本 %s —— 请点「保存病例信息」使其生效。"
            % rev.get("time", files[i]))

    def _arch_detail_back(self) -> None:
        if hasattr(self, "right_stack") and hasattr(self, "arch_grid_host"):
            self.right_stack.setCurrentWidget(self.arch_grid_host)

    def _arch_detail_save_clinical(self) -> None:
        meta = getattr(self, "_arch_detail_meta", None)
        d = getattr(self, "_arch_detail_dir", "")
        if not meta or not d:
            self.detail_status.setText("没有打开的档案。")
            return
        import time as _t
        meta["clinical"] = self._case_form_values()
        # 每次保存生成一个时间戳版本（历史版本子页可查/可恢复）
        rev_name = ""
        try:
            rev_dir = os.path.join(d, "revisions")
            os.makedirs(rev_dir, exist_ok=True)
            stamp = _t.strftime("%Y%m%d_%H%M%S")
            rev_name = stamp + ".json"
            _n = 1
            while os.path.exists(os.path.join(rev_dir, rev_name)):
                rev_name = "%s_%d.json" % (stamp, _n)
                _n += 1
            with open(os.path.join(rev_dir, rev_name), "w", encoding="utf-8") as f:
                json.dump({"time": _t.strftime("%Y-%m-%d %H:%M:%S"),
                           "clinical": meta["clinical"]},
                          f, ensure_ascii=False, indent=2)
            revs = meta.get("revisions") or []
            revs.append({"file": rev_name, "time": _t.strftime("%Y-%m-%d %H:%M:%S")})
            meta["revisions"] = revs
        except Exception:  # noqa: BLE001
            pass
        try:
            with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            self.detail_status.setText("病例信息已保存：%s%s"
                                       % (d, ("　（已存版本 %s）" % rev_name) if rev_name else ""))
            try:
                self._arch_detail_load_history()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            self.detail_status.setText("保存失败：%s" % exc)

    def _arch_detail_apply_params(self) -> None:
        meta = getattr(self, "_arch_detail_meta", None)
        if not meta:
            self.detail_status.setText("没有打开的档案。")
            return
        if meta.get("demo"):
            self.detail_status.setText("「默认案例」仅用于演示，无实际参数可载入。")
            return
        self._archive_apply(meta)
        self.detail_status.setText("已载入该档案的渲染与 MPR 参数。")

    def _archive_selection_changed(self, *_):
        if not hasattr(self, "arch_grid") or not hasattr(self, "arch_sel_lb"):
            return
        items = self.arch_grid.selectedItems()
        if not items:
            self.arch_sel_lb.setText("未选中（在右侧档案册里点选）")
            return
        it = items[0]
        self.arch_sel_lb.setText("已选中 %d 个：\n%s"
                                 % (len(items), it.text().replace("\n", " · ")))

    # ---------------- 病例档案模板 / 表单 ----------------
    def _case_template_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "case_template.json")

    def _case_template(self) -> dict:
        """读取 case_template.json；不存在则写入默认模板；解析失败回退默认。"""
        p = self._case_template_path()
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    tpl = json.load(f)
                if isinstance(tpl, dict) and tpl.get("sections"):
                    return tpl
            with open(p, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CASE_TEMPLATE, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_CASE_TEMPLATE

    def _build_case_form(self, parent_layout) -> None:
        """按模板生成「病例信息」表单（可滚动）。"""
        self.case_fields = {}
        grp = QtWidgets.QGroupBox("病例信息（模板）")
        gl = QtWidgets.QVBoxLayout(grp)
        gl.setSpacing(3)
        _tpl = self._case_template()
        inner = QtWidgets.QWidget()
        il = QtWidgets.QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(5)
        for sec in _tpl.get("sections", []):
            _t = QtWidgets.QLabel(sec.get("name", ""))
            _t.setStyleSheet("color:#a8641a; font-weight:700; padding-top:4px;")
            _t.setWordWrap(True)
            il.addWidget(_t)
            for fld in sec.get("fields", []):
                key = fld.get("key", "")
                lab = QtWidgets.QLabel(fld.get("label", key))
                lab.setStyleSheet("color:#7a6650; font-size:10pt;")
                il.addWidget(lab)
                if fld.get("type") == "multi":
                    w = QtWidgets.QTextEdit()
                    w.setFixedHeight(54)
                    w.setTabChangesFocus(True)
                    w.setStyleSheet(
                        "QTextEdit { background:#fffdf6; color:#3a2e22;"
                        " border:2px solid; border-top-color:#a8834f;"
                        " border-left-color:#bb9760; border-right-color:#fff6e6;"
                        " border-bottom-color:#fff6e6; border-radius:6px;"
                        " padding:2px 6px; font-size:9pt; }")
                else:
                    w = QtWidgets.QLineEdit()
                    w.setStyleSheet(
                        "QLineEdit { background:#fffdf6; color:#3a2e22;"
                        " border:2px solid; border-top-color:#a8834f;"
                        " border-left-color:#bb9760; border-right-color:#fff6e6;"
                        " border-bottom-color:#fff6e6; border-radius:6px;"
                        " padding:2px 6px; font-size:9pt; }")
                il.addWidget(w)
                self.case_fields[key] = w
        il.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(260)
        scroll.setWidget(inner)
        gl.addWidget(scroll, 1)
        _row = QtWidgets.QHBoxLayout()
        self.case_btn_clear = CButton(master=grp, width=110, text="清空表单",
                                      command=self._case_form_clear)
        self.case_btn_demo = CButton(master=grp, width=130, text="填入示例病例",
                                     command=lambda: self._case_form_set(DEMO_CASE_CLINICAL))
        _row.addWidget(self.case_btn_clear)
        _row.addWidget(self.case_btn_demo)
        _row.addStretch(1)
        gl.addLayout(_row)
        parent_layout.addWidget(grp, 1)

    def _case_form_values(self) -> dict:
        out = {}
        for k, w in getattr(self, "case_fields", {}).items():
            try:
                if isinstance(w, QtWidgets.QTextEdit):
                    out[k] = w.toPlainText()
                else:
                    out[k] = w.text()
            except Exception:  # noqa: BLE001
                pass
        return out

    def _case_form_set(self, vals: dict) -> None:
        vals = vals or {}
        for k, w in getattr(self, "case_fields", {}).items():
            try:
                v = str(vals.get(k, "") or "")
                if isinstance(w, QtWidgets.QTextEdit):
                    w.setPlainText(v)
                else:
                    w.setText(v)
            except Exception:  # noqa: BLE001
                pass

    def _case_form_clear(self) -> None:
        self._case_form_set({})

    # ==================================================================
    # 档案中心：保存 / 列表 / 载入 / 删除「本爱宠的 MPR + 渲染」档案
    # ==================================================================
    def _archive_root(self) -> str:
        try:
            from mcp_ssd_vr import config as _cfg
            _cfg.ensure_dirs()
            base = _cfg.record_dir()
        except Exception:  # noqa: BLE001
            base = os.path.join(_external_dir(), "mcp_records")
        d = os.path.join(base, "archives")
        os.makedirs(d, exist_ok=True)
        return d

    def _current_case_info(self) -> dict:
        """当前加载序列的爱宠/序列身份（读第一张 DICOM 头，快）。"""
        info = {"path": "", "label": "", "patient_id": "", "patient_name": "",
                "study_uid": "", "series_uid": "", "series_desc": "",
                "modality": "", "study_date": "", "slices": 0}
        try:
            info["path"] = self.path_edit.line_edit().text().strip()
        except Exception:  # noqa: BLE001
            pass
        info["label"] = (getattr(self, "_loading_label", "")
                         or os.path.basename(os.path.normpath(info["path"] or "")))
        try:
            if self.image_data is not None:
                info["slices"] = int(self.image_data.GetDimensions()[2])
        except Exception:  # noqa: BLE001
            pass
        p = info["path"]
        if not p or not os.path.exists(p):
            return info
        try:
            import pydicom
            first = p
            if os.path.isdir(p):
                first = None
                for _dp, _dn, _fn in os.walk(p):
                    for _f in _fn:
                        if not _f.startswith("."):
                            first = os.path.join(_dp, _f)
                            break
                    if first:
                        break
            if first:
                ds = pydicom.dcmread(first, stop_before_pixels=True, force=True)
                info["patient_id"] = str(getattr(ds, "PatientID", "") or "")
                info["patient_name"] = str(getattr(ds, "PatientName", "") or "")
                info["study_uid"] = str(getattr(ds, "StudyInstanceUID", "") or "")
                info["series_uid"] = str(getattr(ds, "SeriesInstanceUID", "") or "")
                info["series_desc"] = str(getattr(ds, "SeriesDescription", "") or "")
                info["modality"] = str(getattr(ds, "Modality", "") or "")
                info["study_date"] = str(getattr(ds, "StudyDate", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return info

    def _archive_capture_render(self) -> dict:
        d = {}
        try:
            d["mode"] = self.mode_combo.combo_box().currentText()
            d["mode_index"] = int(self.mode_combo.combo_box().currentIndex())
        except Exception:  # noqa: BLE001
            pass
        for k, name in (("ssd", "ssd_slider"), ("vr", "vr_slider"), ("wl", "wl_slider"),
                        ("ww", "ww_slider"), ("mc", "mc_slider"), ("scatter", "scatter_slider"),
                        ("g", "g_slider"), ("exposure", "exp_slider"),
                        ("denoise", "denoise_slider"), ("primary", "primary_slider"),
                        ("shadow", "shadow_slider")):
            try:
                d[k] = int(getattr(self, name).slider().value())
            except Exception:  # noqa: BLE001
                pass
        for k, name in (("ssd_range", "ssd_threshold_slider"),
                        ("vr_range1", "vr_threshold_slider"),
                        ("vr_range2", "vr_threshold_slider2")):
            try:
                d[k] = list(getattr(self, name).values())
            except Exception:  # noqa: BLE001
                pass
        for k, name in (("nlm", "check_nlm"), ("clahe", "check_clahe"),
                        ("frangi", "check_frangi"), ("dist", "check_dist"),
                        ("tf2d", "check_2dtf"), ("cpu", "check_cpu")):
            try:
                d[k] = bool(getattr(self, name).isChecked())
            except Exception:  # noqa: BLE001
                pass
        try:
            cam = self.renderer.GetActiveCamera()
            d["camera"] = {"pos": list(cam.GetPosition()),
                           "focal": list(cam.GetFocalPoint()),
                           "up": list(cam.GetViewUp())}
        except Exception:  # noqa: BLE001
            pass
        d["bg_white"] = bool(getattr(self, "bg_is_white", True))
        return d

    def _archive_capture_mpr(self) -> dict:
        d = {"ww": int(getattr(self, "sam_ww", 400)),
             "wl": int(getattr(self, "sam_wl", 40)),
             "tool": getattr(self, "sam_tool", "none"),
             "slices": {}, "measures": {}}
        for a in (0, 1, 2):
            try:
                d["slices"][str(a)] = int(self.sam_view_sliders[a].value())
            except Exception:  # noqa: BLE001
                pass
            d["measures"][str(a)] = self.sam_measures.get(a, [])
        return d

    def _archive_grab_render(self, path: str) -> str:
        """抓 VTK 渲染窗口；失败则回退抓控件。返回实际写入路径，失败返回空串。"""
        try:
            w2i = vtk.vtkWindowToImageFilter()
            w2i.SetInput(self.render_window)
            w2i.SetScale(1)
            w2i.ReadFrontBufferOff()
            w2i.Update()
            pw = vtk.vtkPNGWriter()
            pw.SetFileName(path)
            pw.SetInputConnection(w2i.GetOutputPort())
            pw.Write()
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return path
        except Exception:  # noqa: BLE001
            pass
        try:
            self.vtk_widget.grab().save(path, "PNG")
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return path
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _archive_grab_mpr(self, path: str) -> str:
        """抓 MPR 网格区域。返回实际写入路径，失败返回空串。"""
        w = None
        try:
            w = self.sam_mpr_grid.parentWidget()
        except Exception:  # noqa: BLE001
            w = None
        if w is None:
            return ""
        try:
            w.grab().save(path, "PNG")
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return path
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _archive_save(self) -> None:
        info = self._current_case_info()
        if not info.get("path"):
            QtWidgets.QMessageBox.information(self, "无数据", "请先加载一个序列。")
            return
        import time as _t
        root = self._archive_root()
        base = _t.strftime("%Y%m%d_%H%M%S")
        n = 1
        while os.path.exists(os.path.join(root, base)):
            base = "%s_%d" % (_t.strftime("%Y%m%d_%H%M%S"), n)
            n += 1
        d = os.path.join(root, base)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        meta = {"id": base, "time": _t.strftime("%Y-%m-%d %H:%M:%S"),
                "name": self.arch_name.line_edit().text().strip(),
                "case": info,
                "clinical": self._case_form_values(),
                "render": self._archive_capture_render(),
                "mpr": self._archive_capture_mpr()}
        try:
            _rp = self._archive_grab_render(os.path.join(d, "render.png"))
            if _rp:
                meta["render_png"] = _rp
        except Exception:  # noqa: BLE001
            pass
        try:
            _mp = self._archive_grab_mpr(os.path.join(d, "mpr.png"))
            if _mp:
                meta["mpr_png"] = _mp
        except Exception:  # noqa: BLE001
            pass
        try:
            with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.arch_status.setText("已保存档案：%s → %s" % (base, d))
        self._archive_refresh()

    _DEMO_ID = "0000_DEFAULT_DEMO"

    def _archive_ensure_demo(self) -> None:
        """确保存在一个常驻「默认案例」档案（演示文件夹+封面效果）。"""
        try:
            root = self._archive_root()
            d = os.path.join(root, self._DEMO_ID)
            mp = os.path.join(d, "meta.json")
            if os.path.isfile(mp):
                # 老版本默认案例可能没有示例病例 → 补上
                try:
                    with open(mp, encoding="utf-8") as f:
                        _m = json.load(f)
                    if not _m.get("clinical"):
                        _m["clinical"] = dict(DEMO_CASE_CLINICAL)
                        with open(mp, "w", encoding="utf-8") as f:
                            json.dump(_m, f, ensure_ascii=False, indent=2)
                except Exception:  # noqa: BLE001
                    pass
                return
            os.makedirs(d, exist_ok=True)
            meta = {"id": self._DEMO_ID, "time": "默认", "name": "默认案例（示例）",
                    "demo": True,
                    "case": {"patient_name": "示例爱宠", "patient_id": "DEMO-001",
                             "modality": "CT", "series_desc": "示例序列",
                             "path": "", "series_uid": "", "study_date": "", "slices": 0},
                    "clinical": dict(DEMO_CASE_CLINICAL),
                    "render": {}, "mpr": {}}
            with open(mp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            pm = QPixmap(480, 320)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QLinearGradient(0, 0, 0, 320)
            g.setColorAt(0.0, QColor(120, 90, 60))
            g.setColorAt(1.0, QColor(40, 30, 20))
            p.fillRect(0, 0, 480, 320, QBrush(g))
            p.setPen(QPen(QColor(255, 214, 120), 4))
            p.setBrush(QColor(255, 214, 120, 55))
            p.drawEllipse(QtCore.QRectF(150, 60, 180, 180))
            p.setPen(QPen(QColor(255, 240, 210, 180), 2))
            p.drawLine(40, 40, 440, 40)
            p.drawLine(40, 280, 440, 280)
            p.setPen(QPen(QColor(255, 240, 210)))
            _f = p.font()
            _f.setPointSize(26)
            _f.setBold(True)
            p.setFont(_f)
            p.drawText(QtCore.QRectF(0, 250, 480, 50),
                       int(QtCore.Qt.AlignmentFlag.AlignCenter), "示例档案")
            p.end()
            pm.save(os.path.join(d, "render.png"), "PNG")
        except Exception:  # noqa: BLE001
            pass

    def _archive_icon(self, path: str):
        """文件夹图标 + render.png 封面（把缩略图贴在文件夹正面）。"""
        folder = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirIcon)
        png = os.path.join(path, "render.png")
        if not os.path.isfile(png):
            return folder
        try:
            base = folder.pixmap(128, 128)
            photo = QPixmap(png)
            if base.isNull() or photo.isNull():
                return folder
            canvas = QPixmap(128, 128)
            canvas.fill(QtCore.Qt.GlobalColor.transparent)
            pt = QPainter(canvas)
            pt.setRenderHint(QPainter.RenderHint.Antialiasing)
            pt.drawPixmap(0, 0, base)
            w, h = 92, 62
            x, y = (128 - w) // 2, 32
            scaled = photo.scaled(w, h,
                                  QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  QtCore.Qt.TransformationMode.SmoothTransformation)
            scaled = scaled.copy((scaled.width() - w) // 2, (scaled.height() - h) // 2,
                                 w, h)
            pt.setPen(QPen(QColor(40, 30, 20), 2))
            pt.drawRect(x - 1, y - 1, w + 2, h + 2)
            pt.drawPixmap(x, y, scaled)
            pt.end()
            return QIcon(canvas)
        except Exception:  # noqa: BLE001
            return folder

    def _archive_refresh(self, *_):
        if not hasattr(self, "arch_grid"):
            return
        root = self._archive_root()
        cur = self._current_case_info()
        only = bool(self.arch_only_series.isChecked())
        self._archive_ensure_demo()
        rows = []
        try:
            for nm in sorted(os.listdir(root), reverse=True):
                d = os.path.join(root, nm)
                mp = os.path.join(d, "meta.json")
                if not os.path.isfile(mp):
                    continue
                try:
                    with open(mp, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:  # noqa: BLE001
                    continue
                case = meta.get("case", {}) or {}
                is_demo = bool(meta.get("demo"))
                if (only and not is_demo and cur.get("series_uid")
                        and case.get("series_uid")
                        and case["series_uid"] != cur["series_uid"]):
                    continue
                rows.append((meta.get("time", ""),
                             meta.get("name", "") or "(未命名)",
                             case.get("patient_name") or case.get("patient_id") or "—",
                             case.get("series_desc") or os.path.basename(case.get("path", "")),
                             d, is_demo))
        except Exception:  # noqa: BLE001
            pass
        rows.sort(key=lambda r: (not r[5],))    # 「默认案例」置顶（稳定排序）
        self._arch_rows = rows
        self.arch_grid.clear()
        for (tm, nm, pt, se, path, is_demo) in rows:
            it = QtWidgets.QListWidgetItem(self._archive_icon(path),
                                           "%s\n%s" % (nm, tm))
            it.setData(QtCore.Qt.ItemDataRole.UserRole + 1, is_demo)
            it.setToolTip("名称：%s\n时间：%s\n爱宠：%s\n序列：%s\n目录：%s"
                          % (nm, tm, pt, se, path))
            it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            it.setData(QtCore.Qt.ItemDataRole.UserRole, path)
            self.arch_grid.addItem(it)
        if cur.get("path"):
            self.arch_case_lb.setText(
                "当前爱宠：%s（%s）｜ 序列：%s" % (
                    cur.get("patient_name") or cur.get("patient_id") or "—",
                    cur.get("modality") or "—",
                    cur.get("series_desc") or os.path.basename(cur.get("path", ""))))
        else:
            self.arch_case_lb.setText("当前爱宠：—（未加载序列）")
        self.arch_status.setText("共 %d 条档案 ｜ %s" % (len(rows), root))

    def _archive_load_selected(self) -> None:
        if not hasattr(self, "arch_grid"):
            return
        items = self.arch_grid.selectedItems()
        if not items:
            QtWidgets.QMessageBox.information(self, "未选中", "请先在右侧网格里点选一个档案。")
            return
        d = items[0].data(QtCore.Qt.ItemDataRole.UserRole)
        if not d or not os.path.isdir(d):
            return
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "载入失败", str(exc))
            return
        self._archive_apply(meta)
        if meta.get("demo"):
            self.arch_status.setText("「默认案例」仅用于演示档案册外观，无实际参数可载入。")
        else:
            self.arch_status.setText("已载入档案：%s（%s）" % (meta.get("id", ""), d))

    def _archive_apply(self, meta: dict) -> None:
        r = meta.get("render", {}) or {}
        m = meta.get("mpr", {}) or {}
        # 病例信息（模板表单）
        try:
            self._case_form_set(meta.get("clinical") or {})
        except Exception:  # noqa: BLE001
            pass
        # --- 渲染参数 ---
        try:
            cb = self.mode_combo.combo_box()
            idx = r.get("mode_index")
            if (idx is None or int(idx) < 0) and r.get("mode"):
                idx = cb.findText(r["mode"])
            if idx is not None and int(idx) >= 0:
                cb.setCurrentIndex(int(idx))
        except Exception:  # noqa: BLE001
            pass
        for k, name in (("ssd", "ssd_slider"), ("vr", "vr_slider"), ("wl", "wl_slider"),
                        ("ww", "ww_slider"), ("mc", "mc_slider"), ("scatter", "scatter_slider"),
                        ("g", "g_slider"), ("exposure", "exp_slider"),
                        ("denoise", "denoise_slider"), ("primary", "primary_slider"),
                        ("shadow", "shadow_slider")):
            if k in r:
                try:
                    getattr(self, name).slider().setValue(int(r[k]))
                except Exception:  # noqa: BLE001
                    pass
        for k, name in (("ssd_range", "ssd_threshold_slider"),
                        ("vr_range1", "vr_threshold_slider"),
                        ("vr_range2", "vr_threshold_slider2")):
            if r.get(k):
                try:
                    lo, hi = r[k][0], r[k][1]
                    getattr(self, name).setValues(int(lo), int(hi))
                except Exception:  # noqa: BLE001
                    pass
        for k, name in (("nlm", "check_nlm"), ("clahe", "check_clahe"),
                        ("frangi", "check_frangi"), ("dist", "check_dist"),
                        ("tf2d", "check_2dtf"), ("cpu", "check_cpu")):
            if k in r:
                try:
                    getattr(self, name).setChecked(bool(r[k]))
                except Exception:  # noqa: BLE001
                    pass
        cam = r.get("camera")
        if cam:
            try:
                c = self.renderer.GetActiveCamera()
                c.SetPosition(*[float(x) for x in cam.get("pos", c.GetPosition())])
                c.SetFocalPoint(*[float(x) for x in cam.get("focal", c.GetFocalPoint())])
                c.SetViewUp(*[float(x) for x in cam.get("up", c.GetViewUp())])
                self.renderer.ResetCameraClippingRange()
                self.render_window.Render()
            except Exception:  # noqa: BLE001
                pass
        # --- MPR 参数 ---
        try:
            if "ww" in m:
                self.sam_ww_slider.setValue(int(m["ww"]))
            if "wl" in m:
                self.sam_wl_slider.setValue(int(m["wl"]))
            self._sam_ww_wl_changed()
        except Exception:  # noqa: BLE001
            pass
        try:
            sl = m.get("slices", {}) or {}
            for a in (0, 1, 2):
                if str(a) in sl:
                    self.sam_view_sliders[a].setValue(int(sl[str(a)]))
        except Exception:  # noqa: BLE001
            pass
        try:
            ms = m.get("measures", {}) or {}
            self.sam_measures = {0: [], 1: [], 2: []}
            for a in (0, 1, 2):
                for item in (ms.get(str(a)) or []):
                    pts = [tuple(p) for p in (item.get("pts") or [])]
                    if pts:
                        self.sam_measures[a].append({
                            "type": item.get("type"), "pts": pts,
                            "text": item.get("text", "")})
            if hasattr(self, "sam_tool_buttons") and m.get("tool") in self.sam_tool_buttons:
                self.sam_tool_buttons[m["tool"]].setChecked(True)
            self._sam_meas_draft = None
            self._sam_refresh_views()
        except Exception:  # noqa: BLE001
            pass

    def _archive_delete_selected(self) -> None:
        if not hasattr(self, "arch_grid"):
            return
        items = self.arch_grid.selectedItems()
        if not items:
            QtWidgets.QMessageBox.information(self, "未选中", "请先选中要删除的档案。")
            return
        if not self._mcp_mode:
            if QtWidgets.QMessageBox.question(
                    self, "删除档案", "确定删除选中的 %d 条档案（含截图）？" % len(items)
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        import shutil
        ok = 0
        skipped = 0
        for it in items:
            if it.data(QtCore.Qt.ItemDataRole.UserRole + 1):
                skipped += 1        # 「默认案例」保护，不删除
                continue
            d = it.data(QtCore.Qt.ItemDataRole.UserRole)
            if d and os.path.isdir(d):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    ok += 1
                except Exception:  # noqa: BLE001
                    pass
        self.arch_status.setText("已删除 %d 条档案%s"
                                 % (ok, "（默认案例已跳过）" if skipped else ""))
        self._archive_refresh()

    def _archive_open_dir(self) -> None:
        d = self._archive_root()
        try:
            if sys.platform == "win32":
                os.startfile(d)  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.information(self, "档案目录", "%s\n(%s)" % (d, exc))

    def _build_patient_section(self, page, parent_layout) -> None:
        """渲染页的**子页面 0**：爱宠列表（取代原来的「选文件夹 / 选文件」）。

        两级树：**爱宠 → 序列**。扫描结果由 DICOM 标签决定，与目录怎么嵌套无关。
        单击任一**序列**即加载，并自动切到默认「稳定渲染模式 (CPU/基础)」。
        """
        tip = QtWidgets.QLabel(
            "智能递归扫描：按 DICOM 标签归成「爱宠 → 序列」两级，与目录嵌套方式无关。\n"
            "单击一个序列 → 自动填入路径、切到「稳定渲染模式 (CPU/基础)」并加载。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a8641a; font-size:11px;")
        parent_layout.addWidget(tip)

        parent_layout.addWidget(QtWidgets.QLabel("扫描根目录"))
        self.pat_root_edit = CLineEdit(master=page,
                                       placeholder_text="放爱宠数据的根目录...")
        parent_layout.addWidget(self.pat_root_edit)

        row = QtWidgets.QHBoxLayout()
        self.btn_pat_pick_root = CButton(master=page, width=80, text="选根目录",
                                         command=self._patient_pick_root)
        self.btn_pat_pacs = CButton(master=page, width=90, text="PACS节点",
                                    command=self._open_pacs_page)
        self.btn_pat_scan = CButton(master=page, width=60, text="扫描",
                                    command=self._patient_scan)
        self.btn_pat_expand = CButton(master=page, width=50, text="展开",
                                      command=self._patient_expand_all)
        self.btn_pat_collapse = CButton(master=page, width=50, text="折叠",
                                        command=self._patient_collapse_all)
        for _b in (self.btn_pat_pick_root, self.btn_pat_pacs, self.btn_pat_scan,
                   self.btn_pat_expand, self.btn_pat_collapse):
            row.addWidget(_b)
        row.addStretch(1)
        parent_layout.addLayout(row)

        # 两级树。三处图标元素：类型图标(第0列) + 状态图标(第1列) + 模态色块(第2列)。
        # 用 QStyle 标准图标与 QPainter 自绘色块，不依赖 emoji 字体，任何机器都能显示。
        self.pat_tree = QtWidgets.QTreeWidget()
        self.pat_tree.setColumnCount(6)
        self.pat_tree.setHeaderLabels(
            ["爱宠 / 序列", "VR", "ID / 模态", "检查日期", "层数", "大小MB"])
        self.pat_tree.setRootIsDecorated(True)
        self.pat_tree.setUniformRowHeights(True)
        self.pat_tree.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.pat_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.pat_tree.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pat_tree.setStyleSheet(
            "QTreeWidget::item:selected { background:#ffd9a8; color:#5a3a10; }"
            "QTreeWidget::item:selected:!active { background:#f4c98a; color:#5a3a10; }"
            "QTreeWidget::item:hover { background:#fdf0dc; }")
        # 不再限制高度：让爱宠/序列树铺满下方空白区域
        self.pat_tree.setMinimumHeight(220)
        _hdr = self.pat_tree.header()
        _hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        _hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.pat_tree.setColumnWidth(1, 46)
        self.pat_tree.itemClicked.connect(self._patient_tree_clicked)
        parent_layout.addWidget(self.pat_tree, 1)

        self.pat_status = QtWidgets.QLabel(
            "尚未扫描。点「扫描」开始（根目录留空则自动用当前渲染路径）。")
        self.pat_status.setWordWrap(True)
        self.pat_status.setStyleSheet("color:#a8641a; font-size:11px;")
        parent_layout.addWidget(self.pat_status)

        self._pat_patients = []
        self._pat_last_loaded = None

    def _patient_sync_from_render_path(self) -> None:
        p = self.path_edit.line_edit().text().strip()
        if p:
            self.pat_root_edit.line_edit().setText(p)
            self.pat_status.setText(f"已同步渲染页路径：{p}")
        else:
            self.pat_status.setText("渲染页路径为空 —— 请先在「渲染」页填好路径，或点「选文件夹」。")

    def _patient_pick_root(self) -> None:
        cur = self.pat_root_edit.line_edit().text().strip() or os.getcwd()
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "选择病例根目录", cur)
        if selected:
            self.pat_root_edit.line_edit().setText(selected)

    def _patient_scan(self) -> None:
        root = self.pat_root_edit.line_edit().text().strip()
        if not root:
            # 根目录留空 -> 直接取渲染页已填路径（这就是选定的默认行为）
            root = self.path_edit.line_edit().text().strip()
            if root:
                self.pat_root_edit.line_edit().setText(root)
        if not root:
            self.pat_status.setText("请先指定扫描根目录（或先在「渲染」页填好路径）。")
            return
        if not os.path.exists(root):
            self.pat_status.setText(f"路径不存在：{root}")
            return

        self.pat_status.setText(f"扫描中… {root}")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        QtWidgets.QApplication.processEvents()
        try:
            from mcp_ssd_vr.dicom import scan_patients
            res = scan_patients(root)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if not res.get("ok"):
            self._pat_patients = []
            self.pat_tree.clear()
            self.pat_status.setText(f"扫描失败：{res.get('error')}")
            return

        self._pat_patients = res["patients"]
        tree = self.pat_tree
        tree.clear()
        for p in self._pat_patients:
            p_it = QtWidgets.QTreeWidgetItem(tree)
            p_it.setIcon(0, self._pat_icon("patient"))
            p_it.setIcon(1, self._pat_icon("ok" if p["series"] else "warn"))
            p_it.setText(0, p["display_name"])
            p_it.setText(2, p["patient_id"] or "—")
            p_it.setText(3, p["study_date"] or "—")
            p_it.setText(4, f"{p['series_count']} 序列")
            p_it.setText(5, f"{p['size_mb']:g}")
            p_it.setToolTip(
                0,
                f"爱宠ID: {p['patient_id'] or '—'}\n"
                f"名字: {p['patient_name'] or '—'}\n"
                f"检查日期: {p['study_date'] or '—'}\n"
                f"模态: {', '.join(p['modalities']) or '—'}\n"
                f"序列数: {p['series_count']}    总层数: {p['file_count']}")
            _f = p_it.font(0)
            _f.setBold(True)
            p_it.setFont(0, _f)
            p_it.setForeground(0, QColor("#8a5a12"))          # 爱宠行高亮
            p_it.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "patient"})

            for s in p["series"]:
                vr = s.get("vr") or {}
                vr_level = vr.get("level", "warn")
                s_it = QtWidgets.QTreeWidgetItem(p_it)
                s_it.setIcon(0, self._pat_icon("series"))
                # 第 1 列 = VR 校验结论（可 / 慎 / 否）
                s_it.setIcon(1, self._pat_icon(f"vr_{vr_level}"))
                s_it.setText(1, {"ok": "可", "warn": "慎", "block": "否"}.get(vr_level, "?"))
                s_it.setIcon(2, self._modality_badge(s["modality"]))
                s_it.setText(0, s["description"])
                s_it.setText(2, s["modality"] or "—")
                s_it.setText(3, s["study_date"] or "")
                s_it.setText(4, str(s["file_count"]))
                s_it.setText(5, f"{s['size_mb']:g}")

                tip = (f"目录: {s['folder']}\n"
                       f"序列UID: {s['series_uid'] or '—'}\n"
                       f"层数: {s['file_count']}    大小: {s['size_mb']:g} MB\n"
                       f"涉及文件夹数: {len(s['folders'])}")
                # VR 校验详情
                vr_head = {"ok": "VR 校验：可以体渲染",
                           "warn": "VR 校验：可以渲染，但有折扣",
                           "block": "VR 校验：无法体渲染"}.get(vr_level, "VR 校验：未知")
                tip += f"\n\n{vr_head}"
                if vr.get("rows") and vr.get("cols"):
                    tip += (f"\n  尺寸 {vr['rows']}x{vr['cols']}  "
                            f"层数 {vr.get('slices')}  "
                            f"体素 {vr.get('voxels', 0) / 1e6:.1f}M")
                for _r in (vr.get("reasons") or []):
                    tip += f"\n  ✗ {_r}"
                for _w in (vr.get("warnings") or []):
                    tip += f"\n  ⚠ {_w}"
                if s.get("warnings"):
                    tip += "\n其他提示: " + "、".join(s["warnings"])
                if s.get("split_multi_folder"):
                    tip += (f"\n注意: 该序列分散在 {len(s['folders'])} 个文件夹，"
                            f"加载时使用层数最多的：\n  {s.get('load_folder', s['folder'])}")
                s_it.setToolTip(0, tip)
                s_it.setData(0, QtCore.Qt.ItemDataRole.UserRole,
                             {"kind": "series",
                              "folder": s.get("load_folder") or s["folder"],
                              "label": f"{p['display_name']} / {s['description']}",
                              "split": bool(s.get("split_multi_folder")),
                              "vr": vr})

        if len(self._pat_patients) <= 40:
            tree.expandAll()
        else:
            tree.collapseAll()
        self._pat_last_loaded = None
        self.pat_status.setText(
            f"扫描完成：{res['patient_count']} 个爱宠 / {res['series_count']} 个序列"
            f"（{res['folder_count']} 个序列目录）。单击一个序列即加载。")

    # ------------------------------------------------------------------
    # 列表图标
    # ------------------------------------------------------------------
    _MODALITY_COLORS = {
        "CT": "#3f6fa8", "MR": "#7a4fa0", "PT": "#a06a3f", "NM": "#5f8f3f",
        "US": "#3f8f7a", "CR": "#8f6a3f", "DX": "#8f6a3f", "MG": "#a04f6f",
        "XA": "#6f3f8f", "RF": "#8f3f4f", "SC": "#5f5f6f", "OT": "#5f5f6f",
    }

    def _pat_icon(self, kind: str):
        """类型/状态图标。用 QStyle 标准图标 —— 不依赖 emoji 字体，任何机器都能显示。"""
        sp = QtWidgets.QStyle.StandardPixmap
        table = {
            "patient": sp.SP_DirHomeIcon,
            "series": sp.SP_FileIcon,
            "warn": sp.SP_MessageBoxWarning,
            "ok": sp.SP_DialogApplyButton,
            # VR 校验三档
            "vr_ok": sp.SP_DialogApplyButton,
            "vr_warn": sp.SP_MessageBoxWarning,
            "vr_block": sp.SP_MessageBoxCritical,
        }
        try:
            return self.style().standardIcon(table.get(kind, sp.SP_FileIcon))
        except Exception:  # noqa: BLE001
            return QIcon()

    def _modality_badge(self, modality: str):
        """模态小色块（QPainter 自绘，同样不依赖字体）。"""
        text = (modality or "?")[:3].upper()
        pm = QPixmap(26, 14)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._MODALITY_COLORS.get(text, "#5f5f6f")))
            p.drawRoundedRect(0, 0, 25, 13, 3, 3)
            p.setPen(QColor("#ffffff"))
            _f = p.font()
            _f.setPointSize(7)
            _f.setBold(True)
            p.setFont(_f)
            p.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, text)
        finally:
            p.end()
        return QIcon(pm)

    def _patient_expand_all(self) -> None:
        if hasattr(self, "pat_tree"):
            self.pat_tree.expandAll()

    def _patient_collapse_all(self) -> None:
        if hasattr(self, "pat_tree"):
            self.pat_tree.collapseAll()

    # ------------------------------------------------------------------
    # 单击一个序列 -> 加载
    # ------------------------------------------------------------------
    def _patient_tree_clicked(self, item, column) -> None:
        """单击**序列**行 -> 登记一个加载请求（不当场加载）。

        单击爱宠行不触发加载（只展开/折叠），避免误触。
        """
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") != "series":
            return
        folder = (data.get("folder") or "").strip()
        if not folder or not os.path.exists(folder):
            self.pat_status.setText(f"路径不存在：{folder}")
            return
        if self._pat_last_loaded == folder and not self.load_busy:
            self.pat_status.setText("该序列已经加载。")
            return
        label = data.get("label") or folder
        if data.get("split"):
            label += "（序列分散在多个文件夹，已取层数最多的那个）"
        # VR 校验：判定"无法体渲染"的先拦一道，别让用户白等一次加载
        vr = data.get("vr") or {}
        if vr.get("level") == "block" and not self._confirm_vr_block(label, vr):
            return
        if vr.get("level") == "warn":
            label += "（VR 校验有提示，悬停列表可看详情）"
        self._queue_series_load(folder, label)

    def _confirm_vr_block(self, label: str, vr: dict) -> bool:
        """VR 校验判定"不可体渲染"时先确认一次。返回 True 表示用户仍要尝试。"""
        reasons = vr.get("reasons") or []
        self._mcp_push("error", {"error": "vr_check_blocked",
                                 "label": label, "reasons": reasons})
        if self._mcp_mode:                      # 自动化模式不弹模态框
            self.pat_status.setText(
                f"VR 校验未通过，未加载：{label} —— " + "；".join(reasons))
            return False

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("VR 校验未通过")
        box.setText(f"「{label}」可能无法进行 VR 体渲染。")
        box.setInformativeText("· " + "\n· ".join(reasons) + "\n\n仍然尝试加载？")
        btn_try = box.addButton("仍然尝试",
                                QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = box.addButton("取消",
                                   QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_cancel)        # 默认不加载
        box.exec()

        if box.clickedButton() is btn_try:
            self.pat_status.setText(f"已忽略 VR 校验结论，继续加载：{label}")
            return True
        self.pat_status.setText(f"已取消（VR 校验未通过）：{label}")
        return False

    def _queue_series_load(self, folder: str, label: str) -> None:
        """登记加载请求并去抖——**关键：不在这里直接调用 load_dicom**。

        点击回调有可能是在 build_reader()/set_progress() 的 processEvents()
        栈深处被派发的。在那里同步发起第二次 load_dicom 会造成嵌套重入：
        进度条互相覆盖、对话框嵌在别人的渲染栈里，极端情况下直接卡死。
        这里只登记请求，交给去抖定时器在事件循环里执行；真正加载前还会再查
        一次 load_busy，忙则走「终止 / 等待」对话框，绝不并发启动第二次加载。
        """
        self._pat_pending_request = (folder, label)
        self.pat_status.setText(f"准备加载：{label} …（连点只会加载最后一个）")
        self._pat_debounce.start()          # 重启计时：连点只保留最后一次

    def _pat_flush_request(self) -> None:
        """去抖到期后执行登记好的请求（此时已回到事件循环，栈是干净的）。"""
        req, self._pat_pending_request = self._pat_pending_request, None
        if not req:
            return
        folder, label = req
        if not os.path.exists(folder):
            self.pat_status.setText(f"路径已不存在：{folder}")
            return
        if self.load_busy:
            # 渲染未完成 -> 在顶层弹提示，绝不在这里直接发起第二次加载
            if self._prompt_render_conflict(folder, label) == "busy_queued":
                self._pat_last_loaded = folder
            return
        self._load_series_stable_cpu(folder, label)

    def _load_series_stable_cpu(self, folder: str, label: str) -> str:
        """加载一个序列，并强制使用默认的「稳定渲染模式 (CPU/基础)」。

        返回 load_dicom 的状态串：done / invalid / cancelled / error /
        busy_queued / busy_cancelled。

        交互要点：**渲染期间不切页**——用户仍留在「爱宠列表」，
        进度通过 set_progress 镜像到列表底部的提示行。
        只有渲染成功后才切到「渲染参数」子页去看结果，避免操作过程被反复打断。
        """
        self.path_edit.line_edit().setText(folder)
        # 渲染模式下拉第 0 项 = 「稳定渲染模式 (CPU/基础)」 -> render_mode = "stable"
        try:
            self.mode_combo.combo_box().setCurrentIndex(0)
        except Exception:  # noqa: BLE001
            pass
        self._loading_label = label
        self._pat_last_loaded = folder
        self.pat_status.setText(f"正在渲染：{label}（稳定渲染模式 CPU/基础）…")
        QtWidgets.QApplication.processEvents()

        try:
            status = self.load_dicom()
        except Exception as exc:  # noqa: BLE001
            self.pat_status.setText(f"加载失败：{type(exc).__name__}: {exc}")
            return "error"

        if status == "done":
            # 渲染完成，这时才把用户带到「渲染」页
            _ri = self._tab_index_by_text("渲染")
            if _ri >= 0:
                self.pages.setCurrentIndex(_ri)
            self.pat_status.setText(f"已加载：{label}　｜　稳定渲染模式 (CPU/基础)")
        elif status == "cancelled":
            self._pat_last_loaded = None       # 没渲染成，允许再点
            self.pat_status.setText("当前渲染已终止。")
        elif status == "error":
            self._pat_last_loaded = None
            self.pat_status.setText(f"渲染失败：{label}")
        elif status == "invalid":
            self._pat_last_loaded = None
            self.pat_status.setText(f"路径无效：{folder}")
        # busy_* 的状态文本已由 _prompt_render_conflict 写好，这里不覆盖
        return status

    def _tab_index_by_text(self, needle: str) -> int:
        """按页签名找下标。

        不要用硬编码下标：往 self.pages 前面插页签会让所有下标整体位移。
        """
        for i in range(self.pages.count()):
            if needle in self.pages.tabText(i):
                return i
        return -1

    def get_adjusted_points(self, points, wl_offset, ww_scale):
        return [(p[0] * ww_scale + wl_offset,) + p[1:] for p in points]

    def on_ww_wl_change(self, value=None) -> None:
        wl_offset = self.wl_slider.slider().value()
        ww_scale = self.ww_slider.slider().value() / 100.0
        self.wl_label.setText(f"当前: {wl_offset} HU")
        self.ww_label.setText(f"当前: {ww_scale:.2f}x")
        self.on_slider_change(0)

    def _sync_ercore_state(self):
        if not self.er_wrapper:
            return
            
        # Update resolution if needed
        w, h = self.render_window.GetSize()
        if self.er_buffer is None or self.er_buffer.shape[0] != h or self.er_buffer.shape[1] != w:
            self.er_buffer = np.zeros((h, w, 4), dtype=np.uint8)
            import vtkmodules.util.numpy_support as vtk_np
            self.er_image_data.SetDimensions(w, h, 1)
            vtk_array = vtk_np.numpy_to_vtk(num_array=self.er_buffer.ravel(), deep=False, array_type=vtk.VTK_UNSIGNED_CHAR)
            self.er_image_data.GetPointData().SetScalars(vtk_array)
            self.er_wrapper.set_resolution(w, h)
            
        # Sync Camera
        cam = self.renderer.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        target = np.array(cam.GetFocalPoint())
        up = np.array(cam.GetViewUp())
        fov = cam.GetViewAngle()
        clip_near, clip_far = cam.GetClippingRange()
        
        # Transform VTK world coordinates to Exposure Render normalized coordinates
        # ER centers the volume at (0,0,0) and scales the max dimension to 1.0
        dims = self.er_image_data.GetDimensions() # wait, this is screen res!
        # Need volume dimensions!
        if self.current_ssd_volume:
            bounds = self.current_ssd_volume.GetBounds()
            center = np.array([(bounds[0]+bounds[1])/2, (bounds[2]+bounds[3])/2, (bounds[4]+bounds[5])/2])
            phys_size = np.array([bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4]])
        else:
            center = np.array([0,0,0])
            phys_size = np.array([1,1,1])
            
        max_size = max(phys_size) if max(phys_size) > 0 else 1.0
        
        er_pos = (pos - center) / max_size
        er_target = (target - center) / max_size
        er_clip_near = clip_near / max_size
        er_clip_far = clip_far / max_size
        
        cam_params = (tuple(er_pos), tuple(er_target), tuple(up), fov, er_clip_near, er_clip_far, self.er_exposure)
        if self.last_cam_params != cam_params:
            self.er_wrapper.set_camera(
                er_pos, er_target, up, fov, er_clip_near, er_clip_far,
                self.er_exposure, 1.0  # gamma
            )
            
            # Setup Cinematic 3-Point Lighting relative to Camera
            import math
            # Calculate view direction (Z) and right vector (X)
            view_dir = er_target - er_pos
            dist = np.linalg.norm(view_dir)
            if dist > 0: view_dir /= dist
            up_vec = up / np.linalg.norm(up) if np.linalg.norm(up) > 0 else up
            right_vec = np.cross(view_dir, up_vec)
            if np.linalg.norm(right_vec) > 0: right_vec /= np.linalg.norm(right_vec)
            
            # Re-orthogonalize up vector
            up_vec = np.cross(right_vec, view_dir)
            
            # Light distance multiplier
            light_dist = dist * 1.5
            
            # Key Light (Top-Right-Front)
            key_pos = er_pos + right_vec * (light_dist * 0.8) + up_vec * (light_dist * 0.8) - view_dir * (light_dist * 0.5)
            key_dir = er_target - key_pos
            key_color = (1.00, 0.98, 0.95)
            key_mult = 300.0  # Path tracing needs high multiplier for area lights
            key_size = (dist * 0.5, dist * 0.5)
            
            # Fill Light (Left-Front, lower intensity, larger area)
            fill_pos = er_pos - right_vec * (light_dist * 1.0) - up_vec * (light_dist * 0.2) - view_dir * (light_dist * 0.2)
            fill_dir = er_target - fill_pos
            fill_color = (0.78, 0.82, 0.95)
            fill_mult = 100.0
            fill_size = (dist * 1.0, dist * 1.0)
            
            # Rim Light (Top-Back, to highlight silhouettes)
            rim_pos = er_target + view_dir * (light_dist * 1.2) + up_vec * (light_dist * 0.8)
            rim_dir = er_target - rim_pos
            rim_color = (1.00, 0.95, 0.88)
            rim_mult = 400.0
            rim_size = (dist * 0.6, dist * 0.6)
            
            self.er_wrapper.setup_lights(
                key_pos, key_dir, key_color, key_mult, key_size,
                fill_pos, fill_dir, fill_color, fill_mult, fill_size,
                rim_pos, rim_dir, rim_color, rim_mult, rim_size
            )
            
            self.er_wrapper.reset_accumulation()
            self.last_cam_params = cam_params

        # Sync Transfer Functions (Fused SSD + VR for Exposure Render)
        wl_offset = self.wl_slider.slider().value()
        ww_scale = self.ww_slider.slider().value() / 100.0
        
        adj_ssd_op = self.get_adjusted_points(self.ssd_opacity_points, wl_offset, ww_scale)
        adj_ssd_co = self.get_adjusted_points(self.ssd_color_points, wl_offset, ww_scale)
        adj_vr_op = self.get_adjusted_points(self.vr_opacity_points, wl_offset, ww_scale)
        adj_vr_co = self.get_adjusted_points(self.vr_color_points, wl_offset, ww_scale)

        ssd_scale = self.ssd_slider.slider().value() / 100.0
        vr_scale = self.vr_slider.slider().value() / 100.0
        eff_ssd_scale = self._effective_ssd_scale(ssd_scale)
        eff_vr_scale = self._effective_vr_scale(vr_scale)

        tf_params = (tuple(adj_ssd_op), tuple(adj_vr_op), tuple(adj_ssd_co), tuple(adj_vr_co), eff_ssd_scale, eff_vr_scale)
        if self.last_tf_params != tf_params:
            er_op_points = []
            er_co_points = []
            
            # Fuse the TF for Exposure Render across the HU range
            for hu in range(-1000, 3001, 20):
                a_ssd = max(0.0, min(1.0, interp_piecewise(adj_ssd_op, hu) * eff_ssd_scale))
                a_vr = max(0.0, min(1.0, interp_piecewise(adj_vr_op, hu) * eff_vr_scale))
                a_mix = 1.0 - (1.0 - a_ssd) * (1.0 - a_vr)
                
                r1, g1, b1 = interp_color(adj_ssd_co, hu)
                r2, g2, b2 = interp_color(adj_vr_co, hu)
                w = a_ssd + a_vr
                if w > 1e-6:
                    r = (r1 * a_ssd + r2 * a_vr) / w
                    g = (g1 * a_ssd + g2 * a_vr) / w
                    b = (b1 * a_ssd + b2 * a_vr) / w
                else:
                    r, g, b = r2, g2, b2
                
                # We do NOT apply exposure pre-multiplication here because ER handles exposure natively in its camera
                er_op_points.append((hu + 32768, a_mix))
                er_co_points.append((hu + 32768, r, g, b))

            self.er_wrapper.update_opacity_tf(er_op_points)
            self.er_wrapper.update_diffuse_tf(er_co_points)
            self.er_wrapper.reset_accumulation()
            self.last_tf_params = tf_params
        
        self.er_wrapper.bind_tracer()

    def on_slider_change(self, value: int) -> None:
        ssd_scale = self.ssd_slider.slider().value() / 100.0
        vr_scale = self.vr_slider.slider().value() / 100.0
        self.ssd_slider_label.setText(f"当前: {ssd_scale:.2f}")
        self.vr_slider_label.setText(f"当前: {vr_scale:.2f}")
        
        wl_offset = self.wl_slider.slider().value()
        ww_scale = self.ww_slider.slider().value() / 100.0
        
        adj_ssd_op = self.get_adjusted_points(self.ssd_opacity_points, wl_offset, ww_scale)
        adj_ssd_co = self.get_adjusted_points(self.ssd_color_points, wl_offset, ww_scale)
        adj_vr_op = self.get_adjusted_points(self.vr_opacity_points, wl_offset, ww_scale)
        adj_vr_co = self.get_adjusted_points(self.vr_color_points, wl_offset, ww_scale)

        if self.current_ssd_volume is not None:
            self.current_ssd_volume.GetProperty().SetScalarOpacity(make_opacity(adj_ssd_op, self._effective_ssd_scale(ssd_scale)))
            self.current_ssd_volume.GetProperty().SetColor(make_color(adj_ssd_co))
        if self.current_vr_volume is not None:
            self.current_vr_volume.GetProperty().SetScalarOpacity(
                make_opacity(
                    self.get_adjusted_points(self._roi_enhanced_vr_opacity_points(self.vr_opacity_points), wl_offset, ww_scale),
                    self._effective_vr_scale(vr_scale),
                )
            )
            self.current_vr_volume.GetProperty().SetColor(
                make_color(
                    self.get_adjusted_points(self._roi_enhanced_vr_color_points(self.vr_color_points), wl_offset, ww_scale)
                )
            )

        if self.controller is not None:
            self.controller.ssd_points = adj_ssd_op
            self.controller.vr_points = adj_vr_op
            self.controller.ssd_color_points = adj_ssd_co
            self.controller.vr_color_points = adj_vr_co
            self.controller.update(
                self._effective_ssd_scale(ssd_scale),
                self._effective_vr_scale(vr_scale),
                self.er_exposure,
            )
            self._apply_cr_runtime_params()
            self._sync_ercore_state()
            self.render_window.Render()

    def on_cr_params_change(self, value=None):
        self.mc_quality = self.mc_slider.slider().value() / 100.0
        self.scatter_blend = self.scatter_slider.slider().value() / 100.0
        self.scatter_g = self.g_slider.slider().value() / 100.0
        self.er_exposure = self.exp_slider.slider().value() / 100.0
        self.cr_denoise = self.denoise_slider.slider().value() / 100.0
        self.step_factor_primary = self.primary_slider.slider().value() / 100.0
        self.step_factor_shadow = self.shadow_slider.slider().value() / 100.0

        self.mc_label.setText(f"当前: {self.mc_quality:.2f}")
        self.scatter_label.setText(f"当前: {self.scatter_blend:.2f}")
        self.g_label.setText(f"当前: {self.scatter_g:.2f}")
        self.exp_label.setText(f"当前: {self.er_exposure:.2f}")
        self.denoise_label.setText(f"当前: {self.cr_denoise:.2f}")
        self.primary_label.setText(f"当前: {self.step_factor_primary:.2f}")
        self.shadow_label.setText(f"当前: {self.step_factor_shadow:.2f}")
        
        # Update render parameters
        if self.render_mode in ("cinematic", "nature_channels", "spectral", "figure8_channels", "layer_channel", "frangi_channel"):
            dof_enabled = self.cr_dof_checkbox.isChecked()
            dof_radius = self.cr_dof_radius_slider.slider().value()
            bg_light_enabled = self.cr_bg_checkbox.isChecked()

            # Depth of Field (DoF) Settings
            camera = self.renderer.GetActiveCamera()
            if hasattr(camera, 'SetFocalDisk'):
                camera.SetFocalDisk(dof_radius if dof_enabled else 0.0)
                if dof_enabled:
                    bounds = self.renderer.ComputeVisiblePropBounds()
                    if bounds:
                        focal_dist = ((bounds[1]-bounds[0])**2 + (bounds[3]-bounds[2])**2 + (bounds[5]-bounds[4])**2)**0.5 / 2.0
                        camera.SetFocalDistance(focal_dist)


        if self.controller is not None:
            self.controller.update(
                self._effective_ssd_scale(self.ssd_slider.slider().value() / 100.0),
                self._effective_vr_scale(self.vr_slider.slider().value() / 100.0),
                self.er_exposure,
            )
        self._apply_light_rig_for_mode()
        self._apply_cr_runtime_params()
        self._sync_ercore_state()
        self.render_window.Render()

    def _path_tracing_sample_distance(self) -> float:
        # 限制最小步长以防止 GPU 驱动因单帧计算超时 (TDR) 而崩溃
        # 强制缩小采样距离，固定为最小体素间距的 0.5 倍
        min_spacing = 1.0
        if getattr(self, "image_data", None):
            spacing = self.image_data.GetSpacing()
            min_spacing = min(spacing)
        
        sf = max(0.10, self.step_factor_primary)
        q = max(0.20, self.mc_quality)
        base_dist = max(0.5, min(2.5, 1.5 * sf / q))
        
        # 采用较小值以确保精细度
        return min(base_dist, min_spacing * 0.5)

    def _effective_vr_scale(self, vr_scale: float) -> float:
        if self.render_mode in ("cinematic", "spectral"):
            return max(1.10, min(4.0, vr_scale * 2.6))
        if self.render_mode == "figure8_channels":
            return max(1.00, min(3.5, vr_scale * 2.2))
        if self.render_mode == "layer_channel":
            return max(0.90, min(3.2, vr_scale * 2.8))
        if self.render_mode == "frangi_channel":
            return max(0.85, min(3.0, vr_scale * 3.0))
        if self.render_mode == "bone_mono":
            return max(0.90, min(3.2, vr_scale * 2.5))
        if self.render_mode == "2dtf":
            return max(0.85, min(3.0, vr_scale * 2.2))
        return vr_scale

    def _effective_ssd_scale(self, ssd_scale: float) -> float:
        if self.render_mode in ("cinematic", "spectral"):
            return max(0.20, min(1.20, ssd_scale * 0.45))
        if self.render_mode == "figure8_channels":
            return max(0.25, min(1.30, ssd_scale * 0.55))
        if self.render_mode == "layer_channel":
            return max(0.18, min(0.90, ssd_scale * 0.40))
        if self.render_mode == "frangi_channel":
            return max(0.15, min(0.80, ssd_scale * 0.35))
        if self.render_mode == "bone_mono":
            return max(0.20, min(0.85, ssd_scale * 0.45))
        if self.render_mode == "2dtf":
            return max(0.15, min(0.80, ssd_scale * 0.35))
        return ssd_scale

    def _apply_cr_runtime_params(self) -> None:
        # Default sampling distance is tied to image spacing (typically 0.5 * spacing)
        # to ensure high quality rendering without stepping artifacts.
        sample_distance = 0.5
        if getattr(self, "image_data", None):
            spacing = self.image_data.GetSpacing()
            sample_distance = min(spacing) * 0.5
            
        for mapper in (getattr(self, "current_vr_mapper", None), getattr(self, "current_ssd_mapper", None)):
            if mapper is None:
                continue
            if self.cpu_render:
                if hasattr(mapper, "SetFixedPointBlockSize"):
                    mapper.SetFixedPointBlockSize(128)
                continue
            if hasattr(mapper, "SetSampleDistance"):
                if self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel"):
                    mapper.SetSampleDistance(self._path_tracing_sample_distance())
                else:
                    mapper.SetSampleDistance(sample_distance)
            
            if self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel"):
                if hasattr(mapper, "SetVolumetricScatteringBlending"):
                    blend = self.scatter_blend * (1.0 - self.cr_denoise * 0.4)
                    mapper.SetVolumetricScatteringBlending(blend)

        if self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel"):
                if hasattr(mapper, "SetVolumetricScatteringBlending"):
                    # Denoise optimization: Reduce scattering blend as denoise increases
                    blend = self.scatter_blend * (1.0 - self.cr_denoise * 0.4)
                    mapper.SetVolumetricScatteringBlending(blend)

        if self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel"):
            if self.current_vr_volume is not None:
                self.current_vr_volume.GetProperty().SetScatteringAnisotropy(self.scatter_g)
            if self.current_ssd_volume is not None:
                self.current_ssd_volume.GetProperty().SetScatteringAnisotropy(min(0.95, self.scatter_g * 0.8))

    def _configure_ssd_property_by_mode(self, prop: vtk.vtkVolumeProperty) -> None:
        if self.render_mode in ("hd_surface", "cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel"):
            prop.ShadeOn()
            if self.render_mode in ("cinematic", "exposure_render", "dual_volume"):
                prop.SetAmbient(0.08)
                prop.SetDiffuse(0.72)
                prop.SetSpecular(0.50)
                prop.SetSpecularPower(50.0)
            elif self.render_mode == "nature_channels":
                prop.SetAmbient(0.15)
                prop.SetDiffuse(0.60)
                prop.SetSpecular(0.60)  # High specular to show glass-like bone contour
                prop.SetSpecularPower(40.0)
            elif self.render_mode == "figure8_channels":
                prop.SetAmbient(0.12)
                prop.SetDiffuse(0.65)
                prop.SetSpecular(0.55)  # Strong specular to highlight surface channel openings
                prop.SetSpecularPower(50.0)
            elif self.render_mode == "layer_channel":
                prop.SetAmbient(0.08)
                prop.SetDiffuse(0.55)
                prop.SetSpecular(0.20)
                prop.SetSpecularPower(16.0)
            elif self.render_mode == "frangi_channel":
                prop.SetAmbient(0.06)
                prop.SetDiffuse(0.50)
                prop.SetSpecular(0.18)
                prop.SetSpecularPower(12.0)
            elif self.render_mode == "bone_mono":
                prop.SetAmbient(0.18)
                prop.SetDiffuse(0.72)
                prop.SetSpecular(0.30)
                prop.SetSpecularPower(25.0)
            else:
                prop.SetAmbient(0.10)
                prop.SetDiffuse(0.75)
                prop.SetSpecular(0.38)
                prop.SetSpecularPower(24.0)
        else:
            prop.ShadeOff()

    def _configure_vr_property_by_mode(self, prop: vtk.vtkVolumeProperty) -> None:
        if self.render_mode in ("cinematic", "spectral", "dual_volume", "exposure_render", "figure8_channels"):
            prop.ShadeOn()
            prop.SetAmbient(0.35)
            prop.SetDiffuse(0.85)
            prop.SetSpecular(0.35)
            prop.SetSpecularPower(40.0)
            base_dist = max(0.4, min(1.6, 0.5 + 2.0 * self.step_factor_shadow))
            dist = base_dist + getattr(self, 'cr_denoise', 0.0) * 0.8
            prop.SetScalarOpacityUnitDistance(dist)
            grad = vtk.vtkPiecewiseFunction()
            grad.AddPoint(0.0, 0.00)
            grad.AddPoint(5.0, 0.00)
            grad.AddPoint(25.0, 0.20)
            grad.AddPoint(60.0, 0.60)
            grad.AddPoint(120.0, 0.90)
            grad.AddPoint(200.0, 1.00)
            prop.SetGradientOpacity(grad)
        elif self.render_mode == "layer_channel":
            prop.ShadeOn()
            prop.SetAmbient(0.30)
            prop.SetDiffuse(0.88)
            prop.SetSpecular(0.12)
            prop.SetSpecularPower(8.0)
            base_dist = max(0.3, min(1.4, 0.4 + 2.0 * self.step_factor_shadow))
            dist = base_dist + getattr(self, 'cr_denoise', 0.0) * 0.6
            prop.SetScalarOpacityUnitDistance(dist)
            grad = vtk.vtkPiecewiseFunction()
            for x, y in self.gradient_opacity_points_layered:
                grad.AddPoint(float(x), float(y))
            prop.SetGradientOpacity(grad)
        elif self.render_mode == "frangi_channel":
            prop.ShadeOn()
            prop.SetAmbient(0.28)
            prop.SetDiffuse(0.90)
            prop.SetSpecular(0.10)
            prop.SetSpecularPower(8.0)
            base_dist = max(0.3, min(1.4, 0.4 + 2.0 * self.step_factor_shadow))
            dist = base_dist + getattr(self, 'cr_denoise', 0.0) * 0.5
            prop.SetScalarOpacityUnitDistance(dist)
            grad = vtk.vtkPiecewiseFunction()
            for x, y in self.gradient_opacity_points_frangi:
                grad.AddPoint(float(x), float(y))
            prop.SetGradientOpacity(grad)
        elif self.render_mode == "bone_mono":
            prop.ShadeOn()
            if self.cpu_render:
                prop.SetAmbient(0.15)
                prop.SetDiffuse(0.60)
                prop.SetSpecular(0.35)
                prop.SetSpecularPower(40.0)
            else:
                prop.SetAmbient(0.12)
                prop.SetDiffuse(0.55)
                prop.SetSpecular(0.85)
                prop.SetSpecularPower(120.0)
            base_dist = max(0.3, min(1.4, 0.4 + 2.0 * self.step_factor_shadow))
            dist = base_dist + getattr(self, 'cr_denoise', 0.0) * 0.5
            prop.SetScalarOpacityUnitDistance(dist)
            grad = vtk.vtkPiecewiseFunction()
            for x, y in self.gradient_opacity_points_bone_mono:
                grad.AddPoint(float(x), float(y))
            prop.SetGradientOpacity(grad)
        elif self.render_mode == "2dtf":
            prop.ShadeOn()
            prop.SetAmbient(0.15)
            prop.SetDiffuse(0.78)
            prop.SetSpecular(0.42)
            prop.SetSpecularPower(45.0)
            base_dist = max(0.3, min(1.4, 0.4 + 2.0 * self.step_factor_shadow))
            dist = base_dist + getattr(self, 'cr_denoise', 0.0) * 0.5
            prop.SetScalarOpacityUnitDistance(dist)
            grad = vtk.vtkPiecewiseFunction()
            grad.AddPoint(0.0, 1.0)
            grad.AddPoint(50.0, 1.0)
            prop.SetGradientOpacity(grad)
        elif self.render_mode == "nature_channels":
            prop.ShadeOn()
            prop.SetAmbient(0.20)
            prop.SetDiffuse(0.80)
            prop.SetSpecular(0.10)
            prop.SetSpecularPower(5.0)
        else:
            prop.ShadeOff()
            prop.SetScalarOpacityUnitDistance(1.0)

    def _apply_light_rig_for_mode(self) -> None:
        self.renderer.AutomaticLightCreationOff()
        self.renderer.RemoveAllLights()
        is_cr = self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "exposure_render", "figure8_channels", "layer_channel", "frangi_channel")
        if is_cr:
            # 5-point light rig for even volume penetration + scattering
            # Key: front-right-above, primary illumination
            key = vtk.vtkLight()
            key.SetLightTypeToSceneLight()
            key.SetPositional(False)
            key.SetPosition(-0.7, -0.4, 1.0)
            key.SetFocalPoint(0.0, 0.0, 0.0)
            key.SetColor(1.00, 0.97, 0.93)
            key.SetIntensity(2.8)
            self.renderer.AddLight(key)

            # Fill: front-left, balances key shadows
            fill = vtk.vtkLight()
            fill.SetLightTypeToSceneLight()
            fill.SetPositional(False)
            fill.SetPosition(0.8, 0.2, 0.5)
            fill.SetFocalPoint(0.0, 0.0, 0.0)
            fill.SetColor(0.92, 0.94, 1.00)
            fill.SetIntensity(1.8)
            self.renderer.AddLight(fill)

            # Back: rear penetration / subsurface scattering simulation
            back = vtk.vtkLight()
            back.SetLightTypeToSceneLight()
            back.SetPositional(False)
            back.SetPosition(0.0, 0.0, -1.2)
            back.SetFocalPoint(0.0, 0.0, 0.0)
            back.SetColor(0.85, 0.90, 1.00)
            back.SetIntensity(1.6)
            self.renderer.AddLight(back)

            # Top rim: subtle highlight from above
            rim = vtk.vtkLight()
            rim.SetLightTypeToSceneLight()
            rim.SetPositional(False)
            rim.SetPosition(0.0, 1.1, 0.4)
            rim.SetFocalPoint(0.0, 0.0, 0.0)
            rim.SetColor(1.00, 0.96, 0.90)
            rim.SetIntensity(1.2)
            self.renderer.AddLight(rim)

            # Bottom fill: reduces harsh downward shadows
            bot = vtk.vtkLight()
            bot.SetLightTypeToSceneLight()
            bot.SetPositional(False)
            bot.SetPosition(0.0, -0.7, 0.0)
            bot.SetFocalPoint(0.0, 0.0, 0.0)
            bot.SetColor(0.88, 0.90, 0.95)
            bot.SetIntensity(0.8)
            self.renderer.AddLight(bot)
        else:
            # Non-CR: use same 5-point rig but at slightly reduced intensity for non-path-traced modes
            key = vtk.vtkLight()
            key.SetLightTypeToSceneLight()
            key.SetPositional(False)
            key.SetPosition(-0.7, -0.4, 1.0)
            key.SetFocalPoint(0.0, 0.0, 0.0)
            key.SetColor(1.00, 0.97, 0.93)
            key.SetIntensity(2.0 if self.render_mode in ("bone_mono", "2dtf") else 0.8)
            self.renderer.AddLight(key)
            fill = vtk.vtkLight()
            fill.SetLightTypeToSceneLight()
            fill.SetPositional(False)
            fill.SetPosition(0.8, 0.2, 0.5)
            fill.SetFocalPoint(0.0, 0.0, 0.0)
            fill.SetColor(0.92, 0.94, 1.00)
            fill.SetIntensity(1.2 if self.render_mode in ("bone_mono", "2dtf") else 0.30)
            self.renderer.AddLight(fill)
            back = vtk.vtkLight()
            back.SetLightTypeToSceneLight()
            back.SetPositional(False)
            back.SetPosition(0.0, 0.0, -1.2)
            back.SetFocalPoint(0.0, 0.0, 0.0)
            back.SetColor(0.85, 0.90, 1.00)
            back.SetIntensity(1.0 if self.render_mode in ("bone_mono", "2dtf") else 0.0)
            if self.render_mode in ("bone_mono", "2dtf"):
                self.renderer.AddLight(back)

    def get_original_ssd_points(self, mode):
        if mode == "stable":
            return self.ssd_opacity_points_stable
        elif mode == "hd_surface":
            return self.ssd_opacity_points_hd
        elif mode == "nature_channels":
            return self.ssd_opacity_points_nature
        elif mode in ("cinematic", "exposure_render"):
            return self.ssd_opacity_points_cinematic
        elif mode == "figure8_channels":
            return self.ssd_opacity_points_figure8
        elif mode == "layer_channel":
            return self.ssd_opacity_points_layered
        elif mode == "frangi_channel":
            return self.ssd_opacity_points_frangi
        elif mode == "bone_mono":
            return self.ssd_opacity_points_bone_mono
        elif mode == "2dtf":
            return [(-1000, 0.0), (3000, 0.0)]
        elif mode == "spectral":
            return self.ssd_opacity_points_hd
        return self.ssd_opacity_points_stable

    def on_ssd_threshold_change(self, lower, upper):
        self.ssd_threshold_label.setText(f"当前: [{lower},{upper}]")
        
        if self.render_mode == "dual_volume":
            self.ssd_opacity_points = [
                (-1000, 0.0),
                (lower - 1, 0.0),
                (lower, 0.8),
                (upper, 0.8),
                (upper + 1, 0.0),
                (3000, 0.0)
            ]
            color = self.ssd_color_points[1][1:] # get RGB
            self.ssd_color_points = [
                (-1000, 0.0, 0.0, 0.0),
                (lower, *color),
                (upper, *color),
                (3000, *color)
            ]
            self.on_slider_change(0)
            return
        
        orig_points = self.get_original_ssd_points(self.render_mode)
        if len(orig_points) < 3:
            return
            
        orig_lower = orig_points[1][0]
        orig_upper = orig_points[-2][0]
        
        new_points = []
        new_points.append(orig_points[0]) # (-1000, 0)
        
        for x, op in orig_points[1:-1]:
            if orig_upper == orig_lower:
                new_x = lower
            else:
                new_x = lower + (x - orig_lower) / (orig_upper - orig_lower) * (upper - lower)
            new_points.append((new_x, op))
            
        new_points.append((3000, orig_points[-1][1])) # (3000, max_op)
        
        self.ssd_opacity_points = new_points
        self.on_slider_change(0)

    def get_original_vr_points(self, mode):
        if mode == "nature_channels":
            return self.vr_opacity_points_nature
        elif mode == "figure8_channels":
            return self.vr_opacity_points_figure8
        elif mode == "layer_channel":
            return self.vr_opacity_points_layered
        elif mode == "frangi_channel":
            return self.vr_opacity_points_frangi
        elif mode == "bone_mono":
            if getattr(self, 'use_frangi', False):
                return self.vr_opacity_points_bone_mono_frangi
            return self.vr_opacity_points_bone_mono
        elif mode == "2dtf":
            return self.vr_opacity_points_2dtf
        elif mode in ("cinematic", "exposure_render"):
            return self.vr_opacity_points_cinematic
        elif mode == "spectral":
            return self.vr_opacity_points_spectral
        else:
            return [
                (-1000, 0.00),
                (10, 0.00),
                (20, 0.05),
                (80, 0.16),
                (120, 0.24),
                (150, 0.28),
                (180, 0.10),
                (260, 0.00),
            ]

    def toggle_background(self):
        self.bg_is_white = not self.bg_is_white
        if self.bg_is_white:
            self.renderer.SetBackground(0.97, 0.95, 0.92)  # Bright cream
            self.seg_renderer.SetBackground(0.94, 0.91, 0.86)
        else:
            self.renderer.SetBackground(0.10, 0.07, 0.05)  # Warm dark brown
            self.seg_renderer.SetBackground(0.08, 0.055, 0.04)

        if self.render_mode == "exposure_render":
            if self.er_wrapper:
                self.er_wrapper.reset_accumulation()

        self.render_window.Render()

    def on_preproc_change(self, value=None) -> None:
        self.denoise_method = "nlm" if self.check_nlm.isChecked() else "gaussian"
        self.use_clahe = self.check_clahe.isChecked()
        self.use_frangi = self.check_frangi.isChecked()
        self.use_distance_field = self.check_dist.isChecked() if hasattr(self, 'check_dist') else False
        self.use_2d_tf = self.check_2dtf.isChecked() if hasattr(self, 'check_2dtf') else False

    def on_vram_change(self, value: int) -> None:
        self.vram_threshold_gb = value

    def on_cpu_change(self, value=None) -> None:
        self.cpu_render = self.check_cpu.isChecked() if hasattr(self, 'check_cpu') else False

    def on_vr_threshold_change(self, lower=None, upper=None):
        if self.render_mode == "dual_volume":
            l1, u1 = self.vr_threshold_slider.value() if hasattr(self.vr_threshold_slider, 'value') else (self.vr_threshold_slider._lower, self.vr_threshold_slider._upper)
            l2, u2 = self.vr_threshold_slider2.value() if hasattr(self.vr_threshold_slider2, 'value') else (self.vr_threshold_slider2._lower, self.vr_threshold_slider2._upper)
            
            self.vr_threshold_label.setText(f"当前: [{int(l1)},{int(u1)}]")
            self.vr_threshold_label2.setText(f"当前: [{int(l2)},{int(u2)}]")
            
            # Rebuild VR TF with strictly two active regions
            self.vr_opacity_points = [
                (-1000, 0.0),
                (l2 - 1, 0.0), (l2, 0.6), (u2, 0.6), (u2 + 1, 0.0),
                (l1 - 1, 0.0), (l1, 0.6), (u1, 0.6), (u1 + 1, 0.0),
                (3000, 0.0)
            ]
            
            # Ensure points are sorted by HU
            self.vr_opacity_points.sort(key=lambda x: x[0])
            
            c1 = (0.10, 0.80, 0.80) # Cyan/Blue for VR Region 1
            c2 = (0.80, 0.20, 0.20) # Red for VR Region 2
            
            self.vr_color_points = [
                (-1000, 0.0, 0.0, 0.0),
                (l2, *c2), (u2, *c2),
                (l1, *c1), (u1, *c1),
                (3000, *c1)
            ]
            self.vr_color_points.sort(key=lambda x: x[0])
            
            self.on_slider_change(0)
            return

        if lower is None or upper is None:
            return
            
        self.vr_threshold_label.setText(f"当前: [{lower},{upper}]")
        
        orig_points = self.get_original_vr_points(self.render_mode)
        if len(orig_points) < 3:
            return
            
        orig_lower = orig_points[1][0]
        orig_upper = orig_points[-2][0]
        
        new_points = []
        new_points.append(orig_points[0]) # (-1000, 0)
        
        for x, op in orig_points[1:-1]:
            if orig_upper == orig_lower:
                new_x = lower
            else:
                new_x = lower + (x - orig_lower) / (orig_upper - orig_lower) * (upper - lower)
            new_points.append((new_x, op))
            
        new_points.append((3000, orig_points[-1][1])) # (3000, max_op)
        
        self.vr_opacity_points = new_points
        self.on_slider_change(0)

    def _er_render_step(self):
        if not self.er_wrapper or self.render_mode != "exposure_render":
            return
            
        tracer_id = self.er_wrapper.get_tracer_id()
        self.er_wrapper.render_estimate(tracer_id)
        
        # Get frame estimate back
        if self.er_buffer is not None:
            self.er_wrapper.get_estimate(tracer_id, self.er_buffer.ctypes.data_as(ctypes.c_void_p))
            
            # RGBA format update
            import vtkmodules.util.numpy_support as vtk_np
            vtk_array = vtk_np.numpy_to_vtk(num_array=self.er_buffer.ravel(), deep=False, array_type=vtk.VTK_UNSIGNED_CHAR)
            self.er_image_data.GetPointData().SetScalars(vtk_array)
            self.er_image_data.Modified()
            self.render_window.Render()
            
        # Instead of continuous repeating timer, we schedule the next frame to allow Qt event loop to process events
        if self.render_mode == "exposure_render":
            QtCore.QTimer.singleShot(1, self._er_render_step)

    def on_preset_change(self, index: int) -> None:
        preset_name = self.preset_combo.currentText()
        if preset_name == "None (使用滑块调节)":
            self.on_mode_change(self.mode_combo.combo_box().currentIndex())
            return
            
        if preset_name in self.slicer_presets:
            preset = self.slicer_presets[preset_name]
            
            # Apply Opacity
            if preset['opacity']:
                self.vr_opacity_points = preset['opacity']
            
            # Apply Color
            if preset['color']:
                self.vr_color_points = preset['color']
                
            # Apply Lighting Properties
            if self.current_vr_volume is not None:
                prop = self.current_vr_volume.GetProperty()
                prop.SetAmbient(preset['ambient'])
                prop.SetDiffuse(preset['diffuse'])
                prop.SetSpecular(preset['specular'])
                prop.SetSpecularPower(preset['specularPower'])
                
            if self.render_mode == "exposure_render" and self.er_wrapper:
                self.er_wrapper.update_opacity_tf(self.vr_opacity_points)
                self.er_wrapper.update_diffuse_tf(self.vr_color_points)
                self.er_wrapper.reset_accumulation()
            else:
                if self.current_vr_volume is not None:
                    self.current_vr_volume.GetProperty().SetScalarOpacity(
                        make_opacity(self._roi_enhanced_vr_opacity_points(self.vr_opacity_points))
                    )
                    self.current_vr_volume.GetProperty().SetColor(
                        make_color(self._roi_enhanced_vr_color_points(self.vr_color_points))
                    )
            
            self.render_window.Render()

    def on_mode_change(self, index: int) -> None:
        # 只有 Exposure Render（index 10）依赖 ErCore/CUDA。
        # 双容积模式（index 11）走 vtkSmartVolumeMapper，不需要 CUDA——
        # 原先写成 `index in (10, 11)`，在没有 ErCore 的机器上会把双容积模式也顶回 stable。
        if index == 10 and er_core is None:
            self.last_error = "Exposure Render (CUDA) 在此平台不可用，回退 stable"
            self._mcp_push("error", {"error": self.last_error})
            if not self._mcp_mode:
                QtWidgets.QMessageBox.information(self, "不可用", "Exposure Render (CUDA) 在此平台不可用。\n需要 NVIDIA GPU + CUDA + ErCore 编译库。")
            self.mode_combo.combo_box().setCurrentIndex(0)
            return
        if index == 11:
            self.render_mode = "dual_volume"
        elif index == 10:
            self.render_mode = "exposure_render"
        elif index == 9:
            self.render_mode = "spectral"
        elif index == 8:
            self.render_mode = "2dtf"
        elif index == 7:
            self.render_mode = "bone_mono"
        elif index == 6:
            self.render_mode = "frangi_channel"
        elif index == 5:
            self.render_mode = "layer_channel"
        elif index == 4:
            self.render_mode = "figure8_channels"
        elif index == 3:
            self.render_mode = "nature_channels"
        elif index == 2:
            self.render_mode = "cinematic"
        elif index == 1:
            self.render_mode = "hd_surface"
        else:
            self.render_mode = "stable"

        # Ensure UI updates match mode requirements
        if hasattr(self, "cr_widget"):
            if self.render_mode in ("cinematic", "nature_channels", "spectral", "exposure_render", "dual_volume", "figure8_channels", "layer_channel", "frangi_channel"):
                self.cr_widget.show()
            else:
                self.cr_widget.hide()

        if self.render_mode == "exposure_render":
            if self.er_image_actor:
                self.er_image_actor.SetVisibility(True)
            if self.current_vr_volume:
                self.current_vr_volume.SetVisibility(False)
            if self.current_ssd_volume:
                self.current_ssd_volume.SetVisibility(False)
            # Start the render loop if not already running
            QtCore.QTimer.singleShot(1, self._er_render_step)
        else:
            if self.er_image_actor:
                self.er_image_actor.SetVisibility(False)
            if self.current_vr_volume:
                self.current_vr_volume.SetVisibility(True)
            if self.current_ssd_volume:
                self.current_ssd_volume.SetVisibility(True)

        if self.render_mode == "dual_volume":
            self.vr_threshold_slider2.show()
            self.vr_threshold_label2.show()
            self.ssd_threshold_slider.hide()
            self.ssd_threshold_label.hide()
            
            self.ssd_opacity_points = [
                (-1000, 0.0), (3000, 0.0) # Hide SSD entirely
            ]
            self.ssd_color_points = [
                (-1000, 0.0, 0.0, 0.0), (3000, 0.0, 0.0, 0.0)
            ]
            self.vr_opacity_points = [
                (-1000, 0.0), (-201, 0.0), (-200, 0.6), (-50, 0.6), (-49, 0.0),
                (49, 0.0), (50, 0.6), (200, 0.6), (201, 0.0), (3000, 0.0)
            ]
            self.vr_color_points = [
                (-1000, 0.0, 0.0, 0.0),
                (-200, 0.80, 0.20, 0.20), # Red
                (-50, 0.80, 0.20, 0.20),
                (50, 0.10, 0.80, 0.80),   # Cyan / Blue
                (200, 0.10, 0.80, 0.80),
                (3000, 0.10, 0.80, 0.80),
            ]
        elif self.render_mode == "hd_surface":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_hd)
            self.ssd_color_points = list(self.ssd_color_points_stable)
        elif self.render_mode == "nature_channels":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_nature)
            self.ssd_color_points = list(self.ssd_color_points_stable)
            self.vr_opacity_points = list(self.vr_opacity_points_nature)        
            self.vr_color_points = list(self.vr_color_points_nature)
        elif self.render_mode == "figure8_channels":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_figure8)
            self.ssd_color_points = list(self.ssd_color_points_figure8)
            self.vr_opacity_points = list(self.vr_opacity_points_figure8)
            self.vr_color_points = list(self.vr_color_points_figure8)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(70)
        elif self.render_mode == "layer_channel":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_layered)
            self.ssd_color_points = list(self.ssd_color_points_layered)
            self.vr_opacity_points = list(self.vr_opacity_points_layered)
            self.vr_color_points = list(self.vr_color_points_layered)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(70)
        elif self.render_mode == "frangi_channel":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_frangi)
            self.ssd_color_points = list(self.ssd_color_points_frangi)
            self.vr_opacity_points = list(self.vr_opacity_points_frangi)
            self.vr_color_points = list(self.vr_color_points_frangi)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(70)
            if hasattr(self, 'check_frangi'):
                self.check_frangi.blockSignals(True)
                self.check_frangi.setChecked(True)
                self.check_frangi.blockSignals(False)
            if hasattr(self, 'check_nlm'):
                self.check_nlm.blockSignals(True)
                self.check_nlm.setChecked(True)
                self.check_nlm.blockSignals(False)
            self.on_preproc_change()
        elif self.render_mode == "bone_mono":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.hide()
            self.ssd_threshold_label.hide()
            self.ssd_opacity_points = [(-1000, 0.0), (3000, 0.0)]
            self.ssd_color_points = [(-1000, 0.0, 0.0, 0.0), (3000, 0.0, 0.0, 0.0)]
            self.on_preproc_change()
            self.use_2d_tf = False
            self.use_2d_tf_bone = False
            if self.use_frangi:
                self.vr_opacity_points = sorted(self.vr_opacity_points_bone_mono_frangi)
                self.vr_color_points = sorted(self.vr_color_points_bone_mono_frangi)
            else:
                self.vr_opacity_points = list(self.vr_opacity_points_bone_mono)
                self.vr_color_points = list(self.vr_color_points_bone_mono)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(70)
        elif self.render_mode == "2dtf":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.hide()
            self.ssd_threshold_label.hide()
            self.ssd_opacity_points = [(-1000, 0.0), (3000, 0.0)]
            self.ssd_color_points = [(-1000, 0.0, 0.0, 0.0), (3000, 0.0, 0.0, 0.0)]
            if hasattr(self, 'check_2dtf'):
                self.check_2dtf.blockSignals(True)
                self.check_2dtf.setChecked(True)
                self.check_2dtf.blockSignals(False)
            self.on_preproc_change()
            import sys
            print(f"[2dtf] 2D TF 预处理将运行, 预计 3-5 分钟...")
            sys.stdout.flush()
            self.vr_opacity_points = list(self.vr_opacity_points_2dtf)
            self.vr_color_points = list(self.vr_color_points_2dtf)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(70)
        elif self.render_mode in ("cinematic", "exposure_render"):
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_cinematic)
            self.ssd_color_points = list(self.ssd_color_points_cinematic)
            self.vr_opacity_points = list(self.vr_opacity_points_cinematic)     
            self.vr_color_points = list(self.vr_color_points_cinematic)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(75)
        elif self.render_mode == "spectral":
            self.vr_threshold_slider2.hide()
            self.vr_threshold_label2.hide()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_hd)
            self.ssd_color_points = list(self.ssd_color_points_stable)
            self.vr_opacity_points = list(self.vr_opacity_points_spectral)     
            self.vr_color_points = list(self.vr_color_points_spectral)
            if self.vr_slider.slider().value() < 55:
                self.vr_slider.slider().setValue(75)
        else:
            self.vr_threshold_slider2.show()
            self.vr_threshold_label2.show()
            self.ssd_threshold_slider.show()
            self.ssd_threshold_label.show()
            self.ssd_opacity_points = list(self.ssd_opacity_points_stable)
            self.ssd_color_points = list(self.ssd_color_points_stable)
            self.vr_opacity_points = [
                (-1000, 0.00),
                (10, 0.00),
                (20, 0.05),
                (80, 0.16),
                (120, 0.24),
                (150, 0.28),
                (180, 0.10),
                (260, 0.00),
            ]
            self.vr_color_points = [
                (-1000, 0.00, 0.00, 0.00),
                (20, 0.55, 0.18, 0.18),
                (80, 0.72, 0.30, 0.28),
                (120, 0.82, 0.42, 0.36),
                (150, 0.92, 0.56, 0.46),
                (260, 0.95, 0.80, 0.70),
            ]

        # Initialize the range slider values dynamically based on mode
        orig_points = self.get_original_ssd_points(self.render_mode)
        if len(orig_points) >= 3:
            orig_lower = orig_points[1][0] if self.render_mode != "dual_volume" else 300
            orig_upper = orig_points[-2][0] if self.render_mode != "dual_volume" else 1500
            self.ssd_threshold_slider.blockSignals(True)
            self.ssd_threshold_slider.setValues(orig_lower, orig_upper)
            self.ssd_threshold_label.setText(f"当前: [{orig_lower}, {orig_upper}]")
            self.ssd_threshold_slider.blockSignals(False)

        orig_vr_points = self.get_original_vr_points(self.render_mode)
        if len(orig_vr_points) >= 3:
            orig_lower_vr = orig_vr_points[1][0] if self.render_mode != "dual_volume" else 50
            orig_upper_vr = orig_vr_points[-2][0] if self.render_mode != "dual_volume" else 200
            self.vr_threshold_slider.blockSignals(True)
            self.vr_threshold_slider.setValues(orig_lower_vr, orig_upper_vr)
            self.vr_threshold_label.setText(f"当前: [{orig_lower_vr}, {orig_upper_vr}]")
            self.vr_threshold_slider.blockSignals(False)

        if self.controller is not None:
            # Different modes correspond to different mapper pipelines, rebuild is most reliable
            self.load_dicom()
            return

        if self.current_ssd_volume is not None:
            ssd_prop = self.current_ssd_volume.GetProperty()
            self._configure_ssd_property_by_mode(ssd_prop)
            ssd_prop.SetScalarOpacity(
                make_opacity(self.ssd_opacity_points, self.ssd_slider.slider().value() / 100.0)
            )

        if self.current_vr_volume is not None:
            vr_prop = self.current_vr_volume.GetProperty()
            vr_prop.SetColor(make_color(self._roi_enhanced_vr_color_points(self.vr_color_points)))
            vr_prop.SetScalarOpacity(
                make_opacity(
                    self._roi_enhanced_vr_opacity_points(self.vr_opacity_points),
                    self.vr_slider.slider().value() / 100.0,
                )
            )
            self._configure_vr_property_by_mode(vr_prop)

        if self.controller is not None:
            self.controller.ssd_points = self.ssd_opacity_points
            self.controller.vr_points = self.vr_opacity_points
            self.controller.ssd_color_points = self.ssd_color_points
            self.controller.vr_color_points = self.vr_color_points
            self.controller.update(
                self._effective_ssd_scale(self.ssd_slider.slider().value() / 100.0),
                self._effective_vr_scale(self.vr_slider.slider().value() / 100.0),
                self.er_exposure,
            )

        self._apply_light_rig_for_mode()
        self._apply_cr_runtime_params()
        self.render_window.Render()

    def on_crop_toggle(self, checked: bool) -> None:
        if self.box_widget is not None:
            if checked:
                # 仅控制裁剪框可见性，不改变当前裁剪结果
                self.box_widget.On()
            else:
                # 仅控制裁剪框可见性，不改变当前裁剪结果
                self.box_widget.Off()
            self.render_window.Render()

    def _box_bounds(self, box_widget: vtk.vtkBoxWidget) -> Tuple[float, float, float, float, float, float]:
        pd = vtk.vtkPolyData()
        box_widget.GetPolyData(pd)
        return pd.GetBounds()

    def _apply_crop_to_active_mappers(
        self, bounds: Tuple[float, float, float, float, float, float]
    ) -> None:
        if hasattr(self, "current_vr_mapper") and self.current_vr_mapper is not None:
            self.current_vr_mapper.SetCroppingRegionFlagsToSubVolume()
            self.current_vr_mapper.SetCropping(True)
            self.current_vr_mapper.SetCroppingRegionPlanes(bounds)
        if hasattr(self, "current_ssd_mapper") and self.current_ssd_mapper is not None:
            self.current_ssd_mapper.SetCroppingRegionFlagsToSubVolume()
            self.current_ssd_mapper.SetCropping(True)
            self.current_ssd_mapper.SetCroppingRegionPlanes(bounds)

    def set_progress(self, percent: int, message: str) -> None:
        value = max(0, min(100, int(percent)))
        self.progress_bar.setValue(value)
        self.progress_text.append(message)
        self._mcp_push("progress", {"percent": value, "message": message})
        # 渲染期间把进度同时镜像到「爱宠列表」页的提示行：
        # 用户点完序列后仍留在列表里，也能看到进度，不必被强行切页。
        if getattr(self, "load_busy", False) and hasattr(self, "pat_status"):
            self.pat_status.setText(f"渲染中 {value}% · {message}")
        QtWidgets.QApplication.processEvents()

    def _clear_old_volumes(self) -> None:
        if self.box_widget is not None:
            self.box_widget.Off()
            self.box_widget = None
        self.current_vr_mapper = None
        self.current_ssd_mapper = None
        self.current_roi_mapper = None
        self.vr_producer = None
        self.roi_producer = None
        self.vr_image_data = None
        self.roi_image_data = None
        self.vr_base_array = None
        self.vr_work_array = None
        self.roi_array = None
        self.original_image_shape_zyx = None

        if hasattr(self, 'current_multi_volume') and self.current_multi_volume is not None:
            self.renderer.RemoveVolume(self.current_multi_volume)
            self.current_multi_volume = None
        if self.current_vr_volume is not None:
            self.renderer.RemoveVolume(self.current_vr_volume)
            self.current_vr_volume = None
        if self.current_roi_volume is not None:
            self.renderer.RemoveVolume(self.current_roi_volume)
            self.current_roi_volume = None
        if self.current_ssd_volume is not None:
            self.renderer.RemoveVolume(self.current_ssd_volume)
            self.current_ssd_volume = None
        self.controller = None

    def _clone_vtk_image(self, image_data: vtk.vtkImageData) -> vtk.vtkImageData:
        cloned = vtk.vtkImageData()
        cloned.DeepCopy(image_data)
        return cloned

    def _refresh_vr_scalar_binding(self) -> None:
        if self.vr_image_data is None or self.vr_work_array is None:
            return
        vtk_array = numpy_support.numpy_to_vtk(
            num_array=self.vr_work_array.ravel(order="C"),
            deep=True,
            array_type=vtk.VTK_SHORT,
        )
        self.vr_image_data.GetPointData().SetScalars(vtk_array)
        self.vr_image_data.Modified()
        vr_producer = getattr(self, "vr_producer", None)
        if vr_producer is not None:
            vr_producer.Modified()
        vr_mapper = getattr(self, "current_vr_mapper", None)
        if vr_mapper is not None:
            vr_mapper.Modified()

    def _ensure_roi_volume(self) -> bool:
        if self.current_vr_volume is None or self.vr_image_data is None:
            return False
        if self.current_roi_volume is not None:
            return True

        roi_image = self._clone_vtk_image(self.vr_image_data)
        dims = roi_image.GetDimensions()
        roi_array = np.full((dims[2], dims[1], dims[0]), -1024, dtype=np.int16)
        vtk_array = numpy_support.numpy_to_vtk(
            num_array=roi_array.ravel(order="C"),
            deep=True,
            array_type=vtk.VTK_SHORT,
        )
        roi_image.GetPointData().SetScalars(vtk_array)
        roi_image.Modified()

        self.roi_image_data = roi_image
        self.roi_array = roi_array
        self.roi_producer = vtk.vtkTrivialProducer()
        self.roi_producer.SetOutput(roi_image)

        roi_mapper = vtk.vtkGPUVolumeRayCastMapper()
        roi_mapper.SetInputConnection(self.roi_producer.GetOutputPort())
        roi_mapper.SetBlendModeToComposite()
        roi_mapper.SetSampleDistance(1.2)

        if self.current_vr_mapper is not None and hasattr(self.current_vr_mapper, "GetCropping") and self.current_vr_mapper.GetCropping():
            roi_mapper.SetCropping(True)
            roi_mapper.SetCroppingRegionPlanes(self.current_vr_mapper.GetCroppingRegionPlanes())

        roi_prop = vtk.vtkVolumeProperty()
        roi_prop.SetColor(make_color([
            (-1024.0, 0.0, 0.0, 0.0),
            (ROI_REPLACEMENT_HU - 8.0, 0.0, 0.0, 0.0),
            (ROI_REPLACEMENT_HU, ROI_REPLACEMENT_COLOR[0], ROI_REPLACEMENT_COLOR[1], ROI_REPLACEMENT_COLOR[2]),
            (ROI_REPLACEMENT_HU + 8.0, 0.95, 1.0, 0.95),
        ]))
        roi_prop.SetScalarOpacity(make_opacity([
            (-1024.0, 0.0),
            (ROI_REPLACEMENT_HU - 8.0, 0.0),
            (ROI_REPLACEMENT_HU, 0.90),
            (ROI_REPLACEMENT_HU + 8.0, 0.98),
        ], 1.0))
        roi_prop.SetInterpolationTypeToLinear()
        self._configure_vr_property_by_mode(roi_prop)

        roi_volume = vtk.vtkVolume()
        roi_volume.SetMapper(roi_mapper)
        roi_volume.SetProperty(roi_prop)
        roi_volume.SetPosition(0.0, float(ROI_RESULT_BED_OFFSET_MM), 0.0)
        roi_volume.SetVisibility(False)

        self.current_roi_mapper = roi_mapper
        self.current_roi_volume = roi_volume
        self.renderer.AddVolume(roi_volume)
        return True

    def _clear_roi_volume(self) -> None:
        if getattr(self, "roi_array", None) is not None:
            self.roi_array.fill(-1024)
            vtk_array = numpy_support.numpy_to_vtk(
                num_array=self.roi_array.ravel(order="C"),
                deep=True,
                array_type=vtk.VTK_SHORT,
            )
            self.roi_image_data.GetPointData().SetScalars(vtk_array)
            self.roi_image_data.Modified()
            if getattr(self, "roi_producer", None) is not None:
                self.roi_producer.Modified()
            if self.current_roi_mapper is not None:
                self.current_roi_mapper.Modified()
        if self.current_roi_volume is not None:
            self.current_roi_volume.SetVisibility(False)

    def _restore_vr_pixels(self) -> None:
        if self.vr_base_array is None or self.vr_work_array is None:
            return
        np.copyto(self.vr_work_array, self.vr_base_array)
        self._refresh_vr_scalar_binding()
        self._clear_roi_volume()

    def _apply_roi_pixel_replacement(self, blocks: List) -> None:
        if not self._ensure_roi_volume():
            return
        self._clear_roi_volume()
        replaced_voxels = 0
        mapped_blocks = 0
        for block in blocks:
            roi_view, mask = self._map_block_to_vr_view(block)
            if roi_view is None or mask is None:
                continue
            if roi_view.shape != mask.shape:
                continue
            mapped_bbox = getattr(block, "_mapped_bbox_zyx", None)
            if mapped_bbox is None:
                continue
            (z0, y0, x0), (z1, y1, x1) = mapped_bbox
            target = self.roi_array[z0:z1, y0:y1, x0:x1]
            if target.shape != mask.shape:
                continue
            mapped_blocks += 1
            replaced_voxels += int(mask.sum())
            target[mask] = ROI_REPLACEMENT_HU
        vtk_array = numpy_support.numpy_to_vtk(
            num_array=self.roi_array.ravel(order="C"),
            deep=True,
            array_type=vtk.VTK_SHORT,
        )
        self.roi_image_data.GetPointData().SetScalars(vtk_array)
        self.roi_image_data.Modified()
        if self.roi_producer is not None:
            self.roi_producer.Modified()
        if self.current_roi_mapper is not None:
            self.current_roi_mapper.Modified()
        if self.current_roi_volume is not None:
            self.current_roi_volume.SetVisibility(mapped_blocks > 0)
        # #region debug-point C:vr-replacement
        import json, urllib.request, time
        _p = '.dbg/roi-result-drift.env'
        _u, _s = 'http://127.0.0.1:7777/event', 'roi-result-drift'
        try:
            with open(_p, encoding='utf-8') as f:
                _c = f.read()
            _u = next((l.split('=', 1)[1].strip() for l in _c.split('\n') if l.startswith('DEBUG_SERVER_URL=')), _u)
            _s = next((l.split('=', 1)[1].strip() for l in _c.split('\n') if l.startswith('DEBUG_SESSION_ID=')), _s)
        except Exception:
            pass
        _payload = {
            'sessionId': _s,
            'runId': 'pre-fix',
            'hypothesisId': 'C',
            'location': 'ssd_vr_viewer.py:_apply_roi_pixel_replacement',
            'msg': '[DEBUG] vr pixel replacement summary',
            'data': {
                'selected_blocks': int(len(blocks)),
                'mapped_blocks': int(mapped_blocks),
                'replaced_voxels': int(replaced_voxels),
                'vr_shape': tuple(int(v) for v in self.roi_array.shape) if getattr(self, "roi_array", None) is not None else None,
                'orig_shape': tuple(int(v) for v in self.original_image_shape_zyx) if self.original_image_shape_zyx is not None else None,
            },
            'ts': int(time.time() * 1000),
        }
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    _u,
                    data=json.dumps(_payload).encode(),
                    headers={'Content-Type': 'application/json'},
                ),
                timeout=0.8,
            ).read()
        except Exception:
            pass
        # #endregion

    def _map_block_to_vr_view(self, block) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if self.vr_work_array is None:
            return None, None
        vr_shape = self.vr_work_array.shape
        if self.original_sitk_image is None or self.vr_image_data is None:
            z0, z1 = block.bbox_z
            y0, y1 = block.bbox_y
            x0, x1 = block.bbox_x
            roi_view = self.vr_work_array[z0:z1, y0:y1, x0:x1]
            return roi_view, block.mask.astype(bool, copy=False)

        orig_spacing = np.array(self.original_sitk_image.GetSpacing()[:3], dtype=np.float64)
        orig_origin = np.array(self.original_sitk_image.GetOrigin()[:3], dtype=np.float64)
        vr_spacing = np.array(self.vr_image_data.GetSpacing(), dtype=np.float64)
        vr_origin = np.array(self.vr_image_data.GetOrigin(), dtype=np.float64)

        world_start = orig_origin + np.array([
            block.bbox_x[0] * orig_spacing[0],
            block.bbox_y[0] * orig_spacing[1],
            block.bbox_z[0] * orig_spacing[2],
        ], dtype=np.float64)
        world_end = orig_origin + np.array([
            block.bbox_x[1] * orig_spacing[0],
            block.bbox_y[1] * orig_spacing[1],
            block.bbox_z[1] * orig_spacing[2],
        ], dtype=np.float64)

        x0 = int(np.floor((world_start[0] - vr_origin[0]) / vr_spacing[0]))
        y0 = int(np.floor((world_start[1] - vr_origin[1]) / vr_spacing[1]))
        z0 = int(np.floor((world_start[2] - vr_origin[2]) / vr_spacing[2]))
        x1 = int(np.ceil((world_end[0] - vr_origin[0]) / vr_spacing[0]))
        y1 = int(np.ceil((world_end[1] - vr_origin[1]) / vr_spacing[1]))
        z1 = int(np.ceil((world_end[2] - vr_origin[2]) / vr_spacing[2]))

        z0 = max(0, min(z0, vr_shape[0] - 1))
        y0 = max(0, min(y0, vr_shape[1] - 1))
        x0 = max(0, min(x0, vr_shape[2] - 1))
        z1 = max(z0 + 1, min(z1, vr_shape[0]))
        y1 = max(y0 + 1, min(y1, vr_shape[1]))
        x1 = max(x0 + 1, min(x1, vr_shape[2]))

        roi_view = self.vr_work_array[z0:z1, y0:y1, x0:x1]
        if roi_view.size == 0:
            return None, None

        mask = self._resize_mask_nearest(block.mask.astype(bool, copy=False), roi_view.shape)
        block._mapped_bbox_zyx = ((z0, y0, x0), (z1, y1, x1))
        return roi_view, mask

    def _resize_mask_nearest(self, mask: np.ndarray, target_shape: Tuple[int, int, int]) -> np.ndarray:
        if mask.shape == target_shape:
            return mask
        z_idx = np.clip(np.round(np.linspace(0, mask.shape[0] - 1, target_shape[0])).astype(np.int32), 0, mask.shape[0] - 1)
        y_idx = np.clip(np.round(np.linspace(0, mask.shape[1] - 1, target_shape[1])).astype(np.int32), 0, mask.shape[1] - 1)
        x_idx = np.clip(np.round(np.linspace(0, mask.shape[2] - 1, target_shape[2])).astype(np.int32), 0, mask.shape[2] - 1)
        return mask[np.ix_(z_idx, y_idx, x_idx)]

    def _roi_enhanced_vr_color_points(self, points: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
        base = [tuple(p) for p in points if p[0] < ROI_REPLACEMENT_HU - 1]
        base.extend([
            (ROI_REPLACEMENT_HU - 8.0, 0.10, 0.85, 0.12),
            (ROI_REPLACEMENT_HU, ROI_REPLACEMENT_COLOR[0], ROI_REPLACEMENT_COLOR[1], ROI_REPLACEMENT_COLOR[2]),
            (ROI_REPLACEMENT_HU + 8.0, 0.95, 1.0, 0.95),
        ])
        return base

    def _roi_enhanced_vr_opacity_points(self, points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        base = [tuple(p) for p in points if p[0] < ROI_REPLACEMENT_HU - 1]
        base.extend([
            (ROI_REPLACEMENT_HU - 8.0, 0.22),
            (ROI_REPLACEMENT_HU, 0.72),
            (ROI_REPLACEMENT_HU + 8.0, 0.92),
        ])
        return base

    def _update_vr_transfer_functions(self) -> None:
        if self.current_vr_volume is None:
            return
        wl_offset = self.wl_slider.slider().value()
        ww_scale = self.ww_slider.slider().value() / 100.0
        vr_scale = self.vr_slider.slider().value() / 100.0
        adj_vr_op = self.get_adjusted_points(self._roi_enhanced_vr_opacity_points(self.vr_opacity_points), wl_offset, ww_scale)
        adj_vr_co = self.get_adjusted_points(self._roi_enhanced_vr_color_points(self.vr_color_points), wl_offset, ww_scale)
        vr_prop = self.current_vr_volume.GetProperty()
        vr_prop.SetScalarOpacity(make_opacity(adj_vr_op, self._effective_vr_scale(vr_scale)))
        vr_prop.SetColor(make_color(adj_vr_co))
        self._configure_vr_property_by_mode(vr_prop)

    # ==================================================================
    # 渲染进程保护：上一个序列没渲染完又点了新序列
    # ==================================================================
    def _check_load_cancel(self) -> None:
        """取消检查点。用户选了「终止渲染」就在这里中断 load_dicom。

        检查点放在每个耗时步骤**之后**——那些地方正好是 processEvents 刚跑过、
        嵌套点击最可能已经进来的位置。
        """
        if self.load_cancel:
            raise _RenderCancelled()

    def _prompt_render_conflict(self, dicom_path: str,
                                label: str | None = None) -> str:
        """渲染进行中又来新请求 -> 明确提示，让用户选「终止」还是「等待」。

        返回 "busy_queued"（已排队，稍后自动加载）或 "busy_cancelled"（用户取消）。
        """
        pending = label or os.path.basename(os.path.normpath(dicom_path)) or dicom_path
        # MCP / 自动化模式不弹模态框，保持原来的静默拒绝行为
        if self._mcp_mode:
            return "busy_cancelled"
        if self.load_cancel:
            return "busy_queued"        # 已经决定终止了，直接排队即可

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("正在渲染")
        box.setText("上一个病例/序列还没有渲染完成。")
        box.setInformativeText(
            f"正在渲染：{self._loading_label or '（未知）'}\n"
            f"新的请求：{pending}\n\n"
            "· 终止渲染 —— 立刻中断当前渲染，直接加载新序列\n"
            "· 等待渲染 —— 当前渲染完成后，自动接着加载新序列")
        btn_abort = box.addButton(
            "终止渲染", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        btn_wait = box.addButton(
            "等待渲染", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_wait)          # 默认「等待」，避免误终止
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_abort:
            self.load_cancel = True
            self._pending_load = (dicom_path, pending)
            self.pat_status.setText(f"已请求终止当前渲染，随后加载：{pending}")
            return "busy_queued"
        if clicked is btn_wait:
            self._pending_load = (dicom_path, pending)
            self.pat_status.setText(f"已排队等待渲染完成，随后加载：{pending}")
            return "busy_queued"
        self.pat_status.setText("已取消新请求，继续当前渲染。")
        return "busy_cancelled"

    def _start_pending_load(self, path: str, label: str) -> None:
        """执行排队中的下一个序列（由 load_dicom 的 finally 用 singleShot 触发）。"""
        if not os.path.exists(path):
            self.pat_status.setText(f"排队的序列路径已不存在：{path}")
            return
        self._pat_last_loaded = path
        self._load_series_stable_cpu(path, label)

    def load_dicom(self) -> str:
        """加载并渲染 path_edit 里的路径。

        返回状态字符串之一："done" / "invalid" / "busy_queued" / "busy_cancelled"
        （调用方据此判断是否真的开始加载了）。
        """
        dicom_path = self.path_edit.line_edit().text().strip()
        if not dicom_path or not os.path.exists(dicom_path):
            self.last_error = "路径无效: " + dicom_path
            self._mcp_push("error", {"error": self.last_error})
            if not self._mcp_mode:
                QtWidgets.QMessageBox.warning(self, "路径无效", "请输入有效的 DICOM 路径。")
            return "invalid"

        if self.load_busy:
            # 上一个病例/序列还没渲染完 -> 明确提示，让用户决定「终止」还是「等待」
            self.last_error = "load in progress: busy"
            self._mcp_push("error", {"error": self.last_error, "load_busy": True})
            return self._prompt_render_conflict(dicom_path)

        self.load_busy = True
        if hasattr(self, "cat_mascot"):
            self.cat_mascot.set_busy(True)     # 猫咪进入活泼状态
        self.load_cancel = False
        self._loading_label = self._loading_label or os.path.basename(
            os.path.normpath(dicom_path))
        self._mcp_push("load_start", {"path": dicom_path})
        self._clear_old_volumes()
        self.btn_load.setEnabled(False)
        self.progress_text.clear()
        self.set_progress(0, "开始加载 DICOM (SimpleITK) ...")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        QtWidgets.QApplication.processEvents()

        try:
            if self.use_2d_tf or self.use_2d_tf_bone:
                print(f"[2D TF] build_reader flags: use_2d_tf={self.use_2d_tf}, use_2d_tf_bone={self.use_2d_tf_bone}, denoise={self.denoise_method}")
                import sys; sys.stdout.flush()
            image_data, downsample_msg = build_reader(dicom_path, self.denoise_method, self.use_clahe, self.use_frangi, self.use_distance_field, self.use_2d_tf, self.use_2d_tf_bone, self.vram_threshold_gb, self.cpu_render)
            self.modality = _LAST_LOAD_MODALITY
            self.is_2d = _LAST_LOAD_IS_2D
            self.set_progress(35, "DICOM 数据读取完成。")
            self._check_load_cancel()
            if downsample_msg:
                self.set_progress(40, downsample_msg)

            if image_data is None or image_data.GetDimensions() == (0, 0, 0):
                raise RuntimeError("无法提取有效的体数据。")
            dims = image_data.GetDimensions()
            self.image_data = image_data
            self.vr_image_data = self._clone_vtk_image(image_data)
            base_scalars = numpy_support.vtk_to_numpy(self.vr_image_data.GetPointData().GetScalars())
            self.vr_base_array = base_scalars.reshape((dims[2], dims[1], dims[0])).copy()
            self.vr_work_array = self.vr_base_array.copy()
            self._refresh_vr_scalar_binding()
            self.set_progress(45, f"体数据尺寸: {dims[0]} x {dims[1]} x {dims[2]}")
            self._check_load_cancel()

            try:
                if os.path.isdir(dicom_path):
                    reader = sitk.ImageSeriesReader()
                    reader.SetFileNames(reader.GetGDCMSeriesFileNames(dicom_path))
                    self.original_sitk_image = reader.Execute()
                else:
                    self.original_sitk_image = sitk.ReadImage(dicom_path)
                if self.original_sitk_image is not None and self.original_sitk_image.GetDimension() >= 3:
                    orig_size = self.original_sitk_image.GetSize()
                    self.original_image_shape_zyx = (
                        int(orig_size[2]),
                        int(orig_size[1]),
                        int(orig_size[0]),
                    )
            except Exception as e:
                print(f"Warning: Failed to store original sitk image: {e}")
                self.original_sitk_image = None
                self.original_image_shape_zyx = None

            # #region debug-point B:load-dicom-geometry
            # 纯离线程序：调试上报默认**关闭**（此前每次加载都会往 127.0.0.1:7777
            # POST 一次几何快照）。仅显式设置 SSDVR_DEBUG_TELEMETRY=1 时才启用。
            if os.environ.get("SSDVR_DEBUG_TELEMETRY") == "1":
                import json, urllib.request, time
                _p = '.dbg/roi-surface-misalignment.env'
                _u, _s = 'http://127.0.0.1:7777/event', 'roi-surface-misalignment'
                try:
                    with open(_p, encoding='utf-8') as f:
                        _c = f.read()
                    _u = next((l.split('=', 1)[1].strip() for l in _c.split('\n') if l.startswith('DEBUG_SERVER_URL=')), _u)
                    _s = next((l.split('=', 1)[1].strip() for l in _c.split('\n') if l.startswith('DEBUG_SESSION_ID=')), _s)
                except Exception:
                    pass
                _payload = {
                    'sessionId': _s,
                    'runId': 'pre-fix',
                    'hypothesisId': 'B',
                    'location': 'ssd_vr_viewer.py:load_dicom',
                    'msg': '[DEBUG] load_dicom geometry snapshot',
                    'data': {
                        'dicom_path': dicom_path,
                        'vtk_dims': tuple(int(v) for v in image_data.GetDimensions()),
                        'vtk_spacing': tuple(float(v) for v in image_data.GetSpacing()),
                        'vtk_origin': tuple(float(v) for v in image_data.GetOrigin()),
                        'sitk_size': tuple(int(v) for v in self.original_sitk_image.GetSize()) if self.original_sitk_image is not None else None,
                        'sitk_spacing': tuple(float(v) for v in self.original_sitk_image.GetSpacing()) if self.original_sitk_image is not None else None,
                        'sitk_origin': tuple(float(v) for v in self.original_sitk_image.GetOrigin()) if self.original_sitk_image is not None else None,
                        'sitk_direction': tuple(float(v) for v in self.original_sitk_image.GetDirection()) if self.original_sitk_image is not None else None,
                    },
                    'ts': int(time.time() * 1000),
                }
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            _u,
                            data=json.dumps(_payload).encode(),
                            headers={'Content-Type': 'application/json'},
                        ),
                        timeout=0.8,
                    ).read()
                except Exception:
                    pass
            # #endregion

            if er_core is not None:
                self.set_progress(50, "初始化 Exposure Render (CUDA) 核心...")
                try:
                    if self.er_wrapper is None:
                        self.er_wrapper = ErCoreWrapper()
                    
                    # Convert VTK image data pointer to ctypes void pointer
                    import vtkmodules.util.numpy_support as vtk_np
                    vtk_array = image_data.GetPointData().GetScalars()
                    np_array = vtk_np.vtk_to_numpy(vtk_array)
                    
                    dims = image_data.GetDimensions()
                    spacing = image_data.GetSpacing()
                    
                    # ErCore assumes unsigned short. VTK DICOM is usually int16.
                    # We shift by 32768 to map int16 range to uint16 safely.
                    np_array = (np_array.astype(np.int32) + 32768).astype(np.uint16)
                    
                    # Keep reference to prevent GC
                    self._er_data_ref = np_array
                    
                    self.er_wrapper.bind_volume_data(dims, spacing, self._er_data_ref.ctypes.data_as(ctypes.c_void_p))
                except Exception as e:
                    print(f"Failed to initialize Exposure Render: {e}")

            self.set_progress(55, "初始化稳定渲染 mapper ...")
            
            # 使用 vtkTrivialProducer 将 vtkImageData 接入到 Pipeline
            self.producer = vtk.vtkTrivialProducer()
            self.producer.SetOutput(image_data)
            self.vr_producer = vtk.vtkTrivialProducer()
            self.vr_producer.SetOutput(self.vr_image_data)
            
            # Use vtkGPUVolumeRayCastMapper as the primary engine for standard modes
            use_path_tracing = self.render_mode in ("cinematic", "nature_channels", "spectral", "dual_volume", "figure8_channels", "layer_channel", "frangi_channel")
            
            if use_path_tracing:
                mapper_cls = vtk.vtkSmartVolumeMapper
            elif self.render_mode == "hd_surface":
                mapper_cls = vtk.vtkGPUVolumeRayCastMapper
            else:
                mapper_cls = vtk.vtkGPUVolumeRayCastMapper

            if self.cpu_render:
                mapper_cls = vtk.vtkFixedPointVolumeRayCastMapper
                print("[CPU Render] 使用 vtkFixedPointVolumeRayCastMapper (系统 RAM)，无下采样全分辨率")

            # CR uses a single combined volume. Others use dual mappers.
            ssd_mapper = mapper_cls()
            ssd_mapper.SetInputConnection(self.producer.GetOutputPort())        
            ssd_mapper.SetBlendModeToComposite()
            
            # Apply GPU memory constraints (70% of user-set VRAM threshold) to avoid overflow
            gpu_budget = int(self.vram_threshold_gb * 0.7 * 1024 * 1024 * 1024)
            if not use_path_tracing and not self.cpu_render and hasattr(ssd_mapper, "SetMaxMemoryInBytes"):
                ssd_mapper.SetMaxMemoryInBytes(gpu_budget)
            if not use_path_tracing and hasattr(ssd_mapper, "SetMaxMemoryFraction"):
                ssd_mapper.SetMaxMemoryFraction(0.65)
                
            if use_path_tracing:
                ssd_mapper.SetSampleDistance(self._path_tracing_sample_distance())
                if hasattr(ssd_mapper, "SetVolumetricScatteringBlending"):
                    ssd_mapper.SetVolumetricScatteringBlending(self.scatter_blend)
                if hasattr(ssd_mapper, "SetMaxMemoryInBytes"):
                    ssd_mapper.SetMaxMemoryInBytes(gpu_budget)
                if hasattr(ssd_mapper, "SetMaxMemoryFraction"):
                    ssd_mapper.SetMaxMemoryFraction(0.65)
            else:
                ssd_mapper.SetSampleDistance(1.2)

            vr_mapper = ssd_mapper if use_path_tracing else mapper_cls()    
            if not use_path_tracing:
                vr_mapper.SetInputConnection(self.vr_producer.GetOutputPort())
                vr_mapper.SetBlendModeToComposite()
                vr_mapper.SetSampleDistance(1.2)
                if not self.cpu_render:
                    if hasattr(vr_mapper, "SetMaxMemoryInBytes"):
                        vr_mapper.SetMaxMemoryInBytes(gpu_budget)
                    if hasattr(vr_mapper, "SetMaxMemoryFraction"):
                        vr_mapper.SetMaxMemoryFraction(0.65)
            if self.cpu_render and hasattr(ssd_mapper, "SetFixedPointBlockSize"):
                ssd_mapper.SetFixedPointBlockSize(128)
                if vr_mapper is not ssd_mapper:
                    vr_mapper.SetFixedPointBlockSize(128)
            
            # --- 确保初始裁剪状态开启 ---
            is_crop_checked = self.check_crop.isChecked()
            ssd_mapper.SetCropping(True)
            if vr_mapper is not ssd_mapper:
                vr_mapper.SetCropping(True)
            
            if self.cpu_render:
                self.set_progress(65, f"CPU 全分辨率渲染 (vtkFixedPointVolumeRayCastMapper, {self.vram_threshold_gb}GB VRAM)。")
            elif use_path_tracing:
                self.set_progress(65, "CR 路径追踪渲染器已就绪（混合散射）。")
            else:
                self.set_progress(65, f"采用 GPU 混合模式：已启用 vtkGPUVolumeRayCastMapper (显存限额 {self.vram_threshold_gb}GB)。")
            self._check_load_cancel()

            ssd_prop = vtk.vtkVolumeProperty()
            ssd_prop.SetColor(make_color(self.ssd_color_points))
            ssd_prop.SetScalarOpacity(make_opacity(self.ssd_opacity_points, 0.9))
            ssd_prop.SetInterpolationTypeToLinear()
            self._configure_ssd_property_by_mode(ssd_prop)

            vr_prop = vtk.vtkVolumeProperty()
            vr_prop.SetColor(make_color(self._roi_enhanced_vr_color_points(self.vr_color_points)))
            vr_prop.SetScalarOpacity(make_opacity(self._roi_enhanced_vr_opacity_points(self.vr_opacity_points), 0.9))
            vr_prop.SetInterpolationTypeToLinear()
            self._configure_vr_property_by_mode(vr_prop)
            vr_prop.SetScatteringAnisotropy(self.scatter_g)
            ssd_prop.SetScatteringAnisotropy(min(0.95, self.scatter_g * 0.8))

            if use_path_tracing:
                fused_prop = vtk.vtkVolumeProperty()
                fused_prop.SetInterpolationTypeToLinear()
                self._configure_vr_property_by_mode(fused_prop)
                fused_prop.SetScatteringAnisotropy(self.scatter_g)
                fused_volume = vtk.vtkVolume()
                fused_volume.SetMapper(ssd_mapper)
                fused_volume.SetProperty(fused_prop)
                ssd_volume = None
                vr_volume = fused_volume
            else:
                ssd_volume = vtk.vtkVolume()
                ssd_volume.SetMapper(ssd_mapper)
                ssd_volume.SetProperty(ssd_prop)

                vr_volume = vtk.vtkVolume()
                vr_volume.SetMapper(vr_mapper)
                vr_volume.SetProperty(vr_prop)

            # Setup VTK Image Actor for Exposure Render
            if self.er_image_actor is None:
                w, h = self.render_window.GetSize()
                if w == 0 or h == 0:
                    w, h = 800, 600
                    
                self.er_buffer = np.zeros((h, w, 4), dtype=np.uint8)
                
                # Create VTK Image Data mapped to numpy array
                import vtkmodules.util.numpy_support as vtk_np
                self.er_image_data = vtk.vtkImageData()
                self.er_image_data.SetDimensions(w, h, 1)
                self.er_image_data.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, 4)
                vtk_array = vtk_np.numpy_to_vtk(num_array=self.er_buffer.ravel(), deep=False, array_type=vtk.VTK_UNSIGNED_CHAR)
                self.er_image_data.GetPointData().SetScalars(vtk_array)
                
                self.er_image_actor = vtk.vtkImageActor()
                self.er_image_actor.SetInputData(self.er_image_data)
                self.renderer.AddActor(self.er_image_actor)
                
                # Make sure actor covers the background
                self.er_image_actor.SetVisibility(False)
                
            self.current_vr_mapper = vr_mapper
            self.current_ssd_mapper = ssd_mapper
            self.current_vr_volume = vr_volume
            self.current_ssd_volume = ssd_volume
            
            if vr_volume is not None:
                self.renderer.AddVolume(vr_volume)
            if ssd_volume is not None:
                self.renderer.AddVolume(ssd_volume)
            self._apply_light_rig_for_mode()
            
            # --- 增加 vtkBoxWidget 裁剪功能 ---
            self.box_widget = vtk.vtkBoxWidget()
            self.box_widget.SetInteractor(self.iren)
            self.box_widget.SetPlaceFactor(1.0)
            self.box_widget.SetInputData(image_data)
            self.box_widget.PlaceWidget()
            self.box_widget.InsideOutOn()
            self.box_widget.SetRotationEnabled(0) # 保持轴对齐，以匹配 CroppingRegionPlanes
            
            # --- 设置初始裁剪范围 ---
            init_bounds = self._box_bounds(self.box_widget)
            if is_crop_checked:
                self._apply_crop_to_active_mappers(init_bounds)

            # 优化线框外观：使其仅作为一个交互控制器，避免遮挡渲染内容
            outline_prop = self.box_widget.GetOutlineProperty()
            outline_prop.SetColor(1.0, 1.0, 0.0) # 黄色边框
            
            face_prop = self.box_widget.GetFaceProperty()
            face_prop.SetOpacity(0.0) # 面完全透明
            
            selected_face_prop = self.box_widget.GetSelectedFaceProperty()
            selected_face_prop.SetOpacity(0.3) # 选中面半透明
            selected_face_prop.SetColor(1.0, 1.0, 0.0)

            def on_box_interaction(obj, event):
                self._apply_crop_to_active_mappers(self._box_bounds(obj))
                # 必须显式触发重新渲染，否则拖动时不会实时更新
                self.render_window.Render()
                
            self.box_widget.AddObserver("InteractionEvent", on_box_interaction)
            # 初始化即生效裁剪；checkbox 只控制框体显示/隐藏
            self._apply_crop_to_active_mappers(init_bounds)
            if self.check_crop.isChecked():
                self.box_widget.On()
            else:
                self.box_widget.Off()
            
            if use_path_tracing:
                self.set_progress(80, "已完成 CR 单体积 Monte Carlo 挂载（SSD/VR 分类融合）。")
            else:
                self.set_progress(80, "已完成 SSD+VR 独立容积裁剪挂载。")

            self.controller = FusionController(
                ssd_volume,
                vr_volume,
                self.ssd_opacity_points,
                self.vr_opacity_points,
                self.ssd_color_points,
                self.vr_color_points,
                fused_volume=vr_volume if use_path_tracing else None,
            )
            
            # Apply sliders and window level offsets to ensure correct initialization
            self.on_slider_change(0)

            self.renderer.ResetCamera()
            cam = self.renderer.GetActiveCamera()
            fp = cam.GetFocalPoint()
            pos = cam.GetPosition()
            distance = ((pos[0] - fp[0]) ** 2 + (pos[1] - fp[1]) ** 2 + (pos[2] - fp[2]) ** 2) ** 0.5
            # Start from a coronal-like viewpoint to mirror Figure 8 style.
            cam.SetPosition(fp[0], fp[1] - max(distance, 1.0), fp[2])
            cam.SetViewUp(0, 0, 1)
            self.renderer.ResetCameraClippingRange()
            self._apply_cr_runtime_params()
            # 最后一次重渲染前再确认一次：这一步最耗时，用户很可能就是在这里点了新序列
            self._check_load_cancel()
            self.render_window.Render()
            self.set_progress(100, "渲染完成。可交互浏览。")

            self.statusBar().showMessage(
                "左键：旋转 | 中键/Shift+左键：平移 | 右键：缩放 | 拖动裁剪框：实时剪裁。 SSD/VR 参数可独立调节。"
            )
            dims = self.image_data.GetDimensions()
            self._mcp_push("load_done", {
                "path": dicom_path,
                "dims": [int(v) for v in dims],
                "spacing": [float(v) for v in self.image_data.GetSpacing()],
            })
            # 3D SAM：新数据到来时重置点/结果并同步切片范围
            self._sam_reset()
            # 档案中心：刷新「当前爱宠」提示与档案列表
            try:
                self._archive_refresh()
            except Exception:  # noqa: BLE001
                pass
            # DR 平片（2D）：无体绘制意义，自动切到 MPR 阅片页
            if self.is_2d:
                _rd = self._tab_index_by_text("MPR")
                if _rd >= 0:
                    self.pages.setCurrentIndex(_rd)
                self.statusBar().showMessage(
                    "检测到 2D 影像（DR/单幅）：已切换到 MPR 阅片页面。")
        except _RenderCancelled:
            # 用户在"正在渲染"对话框里选择了「终止渲染」
            self.set_progress(0, "当前渲染已终止。")
            self._mcp_push("error", {"error": "render_cancelled", "path": dicom_path})
            status = "cancelled"
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.last_error = f"DICOM 加载或渲染失败：{exc}"
            self._mcp_push("error", {"error": self.last_error})
            if not self._mcp_mode:
                QtWidgets.QMessageBox.critical(self, "加载失败", f"DICOM 加载或渲染失败：\n{exc}")
            self.set_progress(0, "加载失败。")
            status = "error"
        else:
            status = "done"
        finally:
            self.load_busy = False
            if hasattr(self, "cat_mascot"):
                self.cat_mascot.set_busy(False)
            self.load_cancel = False
            self._loading_label = ""
            self.btn_load.setEnabled(True)
            QtWidgets.QApplication.restoreOverrideCursor()
            # 有排队的请求就接着加载。
            # 用 singleShot(0) 让当前调用先完整退栈，避免 load_dicom 递归。
            _pend, self._pending_load = self._pending_load, None
            if _pend is not None:
                QtCore.QTimer.singleShot(
                    0, lambda p=_pend[0], l=_pend[1]: self._start_pending_load(p, l))
        return status

    def closeEvent(self, event) -> None:
        try:
            if self.sam_session is not None:
                self.sam_session.close()
        except Exception:
            pass
        super().closeEvent(event)

    # ==================================================================
    # 3D SAM 交互分割 (MPR 点击 → SAM-Med3D → 绿色高亮)
    # ==================================================================

    def _build_sam_tab(self) -> None:
        """构建「MPR 阅片」页：主 MPR 切片视图 + 模型/点/结果控件。"""
        sam_page = QtWidgets.QWidget()
        sam_layout = QtWidgets.QVBoxLayout(sam_page)
        sam_layout.setContentsMargins(4, 2, 4, 2)
        sam_layout.setSpacing(6)
        self.sam_expanded_axis = None      # 当前被放大到整页的视图轴（None=2×2）

        # MPR 视图右上角：保存到档案（截图）
        _mpr_top = QtWidgets.QHBoxLayout()
        _mpr_top.addStretch(1)
        self.btn_shot_mpr = CButton(master=sam_page, width=150,
                                    text="保存到档案（截图）",
                                    command=lambda: self._save_shot_to_archive("mpr"))
        _mpr_top.addWidget(self.btn_shot_mpr)
        sam_layout.addLayout(_mpr_top)

        # --- 模型 ---
        mdl_group = QtWidgets.QGroupBox("SAM-Med3D 模型 (3D 交互分割)")
        mdl_layout = QtWidgets.QHBoxLayout(mdl_group)
        self.sam_btn_load = QtWidgets.QPushButton("加载模型")
        self.sam_btn_load.clicked.connect(self._sam_load_model)
        self.sam_model_status = QtWidgets.QLabel("未加载")
        self.sam_model_status.setWordWrap(True)
        mdl_layout.addWidget(self.sam_btn_load)
        mdl_layout.addWidget(self.sam_model_status, 1)
        self.sam_mdl_group = mdl_group
        sam_layout.addWidget(mdl_group)

        # --- MPR 视图 (2×2 布局: 轴向|冠状 / 矢状|信息) ---
        self.sam_view_labels = {}   # axis -> QLabel
        self.sam_view_sliders = {}  # axis -> QSlider
        self.sam_view_names = {0: "轴向 (z)", 1: "冠状 (y)", 2: "矢状 (x)"}
        mpr_group = QtWidgets.QGroupBox(
            "MPR 阅片（点击=加提示点；右击=加负点；双击=整页放大）")
        mpr_outer = QtWidgets.QVBoxLayout(mpr_group)
        mpr_outer.setSpacing(5)

        # --- 窗宽 / 窗位（MPR 专用，独立于「渲染」页）---
        self.sam_wl = 40        # 窗位 (HU)
        self.sam_ww = 400       # 窗宽 (HU)
        wl_row = QtWidgets.QHBoxLayout()
        self.sam_wl_name = QtWidgets.QLabel("窗位")
        self.sam_wl_name.setMinimumWidth(30)
        self.sam_wl_value = QtWidgets.QLabel("40 HU")
        self.sam_wl_value.setMinimumWidth(66)
        self.sam_wl_value.setStyleSheet("color:#a8641a; font-weight:700;")
        self.sam_wl_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sam_wl_slider.setRange(-1000, 3000)
        self.sam_wl_slider.setValue(self.sam_wl)
        self.sam_wl_slider.valueChanged.connect(self._sam_ww_wl_changed)
        wl_row.addWidget(self.sam_wl_name)
        wl_row.addWidget(self.sam_wl_slider, 1)
        wl_row.addWidget(self.sam_wl_value)
        mpr_outer.addLayout(wl_row)

        ww_row = QtWidgets.QHBoxLayout()
        self.sam_ww_name = QtWidgets.QLabel("窗宽")
        self.sam_ww_name.setMinimumWidth(30)
        self.sam_ww_value = QtWidgets.QLabel("400 HU")
        self.sam_ww_value.setMinimumWidth(66)
        self.sam_ww_value.setStyleSheet("color:#a8641a; font-weight:700;")
        self.sam_ww_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sam_ww_slider.setRange(1, 4000)
        self.sam_ww_slider.setValue(self.sam_ww)
        self.sam_ww_slider.valueChanged.connect(self._sam_ww_wl_changed)
        ww_row.addWidget(self.sam_ww_name)
        ww_row.addWidget(self.sam_ww_slider, 1)
        ww_row.addWidget(self.sam_ww_value)
        self.sam_ww_preset = QtWidgets.QComboBox()
        self.sam_ww_preset.addItems(["预设…", "软组织 400/40", "肺窗 1500/-600",
                                     "骨窗 2000/400", "脑窗 80/40", "腹部 350/50"])
        self.sam_ww_preset.setMinimumWidth(120)
        self.sam_ww_preset.currentIndexChanged.connect(self._sam_ww_preset_changed)
        ww_row.addWidget(self.sam_ww_preset)
        mpr_outer.addLayout(ww_row)

        # ---- 测量状态 ----
        self.sam_tool = "none"
        self.sam_measures = {0: [], 1: [], 2: []}
        self._sam_meas_draft = None
        self.sam_tool_names = {
            "none": "浏览", "line": "长度", "angle": "角度",
            "rect": "矩形", "ellipse": "圆形/椭圆", "poly": "不规则形",
        }

        # ---- 左侧测量小工具条 + 右侧 2×2 视图网格 ----
        _tool_qss = (
            "QToolButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #f7e6c8, stop:1 #e8cfa2); color:#8a5a12;"
            " border:2px solid; border-top-color:#fff6e6; border-left-color:#fbeed6;"
            " border-right-color:#b08c50; border-bottom-color:#a8834f;"
            " border-radius:7px; font-size:12pt; font-weight:700; }"
            "QToolButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #fff2dc, stop:1 #f0c184); }"
            "QToolButton:checked { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #ffd89a, stop:1 #f09a2e); color:#5a3208;"
            " border-bottom-color:#b8620f; }")
        content = QtWidgets.QWidget()
        content_row = QtWidgets.QHBoxLayout(content)
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(6)
        tools = QtWidgets.QWidget()
        tools.setFixedWidth(46)
        tl = QtWidgets.QVBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        self.sam_tool_group = QtWidgets.QButtonGroup(self)
        self.sam_tool_group.setExclusive(True)
        self.sam_tool_buttons = {}
        _tdefs = [
            ("none", "看", "浏览（不测量；左键加提示点）"),
            ("line", "长", "长度：按住拖出一条线"),
            ("angle", "角", "角度：先点顶点，再点两臂（共 3 下）"),
            ("rect", "矩", "矩形：按住拖出矩形"),
            ("ellipse", "圆", "圆形/椭圆：按住拖出外接框"),
            ("poly", "形", "不规则形：按住左键沿轮廓描边，松开即自动闭合（也可右击闭合）"),
        ]
        for _key, _sym, _tip in _tdefs:
            _b = QtWidgets.QToolButton()
            _b.setText(_sym)
            _b.setToolTip(_tip)
            _b.setCheckable(True)
            _b.setFixedSize(38, 34)
            _b.setStyleSheet(_tool_qss)
            _b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.sam_tool_group.addButton(_b)
            tl.addWidget(_b)
            self.sam_tool_buttons[_key] = _b
            _b.clicked.connect(lambda _=False, k=_key: self._sam_set_tool(k))
        self.sam_tool_buttons["none"].setChecked(True)
        tl.addStretch(1)
        self.sam_btn_meas_clear = QtWidgets.QToolButton()
        self.sam_btn_meas_clear.setText("清")
        self.sam_btn_meas_clear.setToolTip("清除全部测量")
        self.sam_btn_meas_clear.setFixedSize(38, 34)
        self.sam_btn_meas_clear.setStyleSheet(_tool_qss)
        self.sam_btn_meas_clear.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.sam_btn_meas_clear.clicked.connect(self._sam_meas_clear)
        tl.addWidget(self.sam_btn_meas_clear)
        content_row.addWidget(tools)

        grid_host = QtWidgets.QWidget()
        mpr_grid = QtWidgets.QGridLayout(grid_host)
        mpr_grid.setContentsMargins(0, 0, 0, 0)
        mpr_grid.setSpacing(6)
        content_row.addWidget(grid_host, 1)
        mpr_outer.addWidget(content, 1)
        self.sam_mpr_grid = mpr_grid        # 供双击放大重排用
        self.sam_cells = {}                 # axis -> cell widget
        # 记录每轴标签当前几何/像素自然尺寸（点击换算用）
        self.sam_view_nat = {0: (1, 1), 1: (1, 1), 2: (1, 1)}
        self.sam_view_idxlabels = {0: None, 1: None, 2: None}
        _cell_pos = {0: (0, 0), 1: (0, 1), 2: (1, 0)}   # 轴向左上 冠状右上 矢状左下
        self.sam_cell_pos = dict(_cell_pos)
        for axis in (0, 1, 2):
            cell = QtWidgets.QWidget()
            col = QtWidgets.QVBoxLayout(cell)
            col.setContentsMargins(0, 0, 0, 0)
            hdr = QtWidgets.QHBoxLayout()
            name_lb = QtWidgets.QLabel(self.sam_view_names[axis])
            name_lb.setStyleSheet("font-weight: bold; color: #a8641a;")
            idx_lb = QtWidgets.QLabel("")
            idx_lb.setStyleSheet("color: #7a6650;")
            hdr.addWidget(name_lb)
            hdr.addStretch()
            hdr.addWidget(idx_lb)
            col.addLayout(hdr)
            img_lb = SamSliceLabel(axis)
            img_lb.setMinimumSize(280, 240)
            img_lb.setStyleSheet("background-color: #fffdf9; border: 1px solid #e2d3bf;")
            col.addWidget(img_lb, 1)
            sld = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            sld.setRange(0, 0)
            sld.valueChanged.connect(lambda v, a=axis, il=idx_lb: self._sam_slider_changed(a, v, il))
            col.addWidget(sld)
            r, c = _cell_pos[axis]
            mpr_grid.addWidget(cell, r, c)
            self.sam_cells[axis] = cell
            self.sam_view_labels[axis] = img_lb
            self.sam_view_sliders[axis] = sld
            self.sam_view_idxlabels[axis] = idx_lb
        # 第 4 格：信息/图例占位
        info_cell = QtWidgets.QWidget()
        info_col = QtWidgets.QVBoxLayout(info_cell)
        leg = QtWidgets.QLabel(
            "交互提示\n"
            "· 左键 = 加正点(绿+)　右击 = 加负点(红−)\n"
            "· 点图后其余两平面自动定位\n"
            "· 双击某视图 = 放大/还原（整页）\n\n"
            "左侧测量工具（先选工具，再在图上操作）\n"
            "· 看 = 浏览（不测量，左键加提示点）\n"
            "· 长 = 长度：按住拖一条线 → 显示 mm\n"
            "· 角 = 角度：先点顶点，再点两臂（共 3 下）→ 显示 °\n"
            "· 矩 = 矩形：按住拖出矩形 → 宽×高 与面积 cm²\n"
            "· 圆 = 圆形/椭圆：按住拖出外接框 → 直径 与面积 cm²\n"
            "· 形 = 不规则形：按住左键描边，松开即自动闭合（右击也可闭合）→ 面积/周长\n"
            "· 清 = 清除全部测量\n\n"
            "十字线图例\n"
            "· 青 = 轴向面(z)　橙 = 冠状面(y)　品红 = 矢状面(x)\n\n"
            "3D VR 在右侧窗口，分割结果以绿色高亮显示。"
        )
        leg.setWordWrap(True)
        leg.setStyleSheet("color: #7a6650; background:#fbf3e8; border:1px solid #e2d3bf; padding:6px;")
        # 内容较长：放进滚动区，避免在 2×2 小格里被裁掉
        info_scroll = QtWidgets.QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        info_scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        info_scroll.setWidget(leg)
        info_col.addWidget(info_scroll, 1)
        mpr_grid.addWidget(info_cell, 1, 1)
        self.sam_info_cell = info_cell
        mpr_grid.setColumnStretch(0, 1)
        mpr_grid.setColumnStretch(1, 1)
        mpr_grid.setRowStretch(0, 1)
        mpr_grid.setRowStretch(1, 1)
        sam_layout.addWidget(mpr_group, 1)   # MPR 占满剩余高度（主阅片区）

        # --- 点/结果操作 ---
        pts_group = QtWidgets.QGroupBox("提示点与分割")
        pts_layout = QtWidgets.QVBoxLayout(pts_group)
        mode_row = QtWidgets.QHBoxLayout()
        self.sam_btn_mode_pos = QtWidgets.QRadioButton("正点 +")
        self.sam_btn_mode_pos.setChecked(True)
        self.sam_btn_mode_neg = QtWidgets.QRadioButton("负点 -")
        mode_row.addWidget(self.sam_btn_mode_pos)
        mode_row.addWidget(self.sam_btn_mode_neg)
        mode_row.addStretch()
        pts_layout.addLayout(mode_row)
        self.sam_summary = QtWidgets.QLabel("无点")
        pts_layout.addWidget(self.sam_summary)
        op_row = QtWidgets.QHBoxLayout()
        self.sam_btn_run = QtWidgets.QPushButton("分割 ▶")
        self.sam_btn_run.setEnabled(False)
        self.sam_btn_run.clicked.connect(self._sam_run)
        self.sam_btn_undo = QtWidgets.QPushButton("撤销点")
        self.sam_btn_undo.clicked.connect(self._sam_undo)
        self.sam_btn_clearpts = QtWidgets.QPushButton("清空点")
        self.sam_btn_clearpts.clicked.connect(self._sam_clear_points)
        self.sam_btn_clearmask = QtWidgets.QPushButton("清结果")
        self.sam_btn_clearmask.clicked.connect(self._sam_clear_result)
        op_row.addWidget(self.sam_btn_run)
        op_row.addWidget(self.sam_btn_undo)
        op_row.addWidget(self.sam_btn_clearpts)
        op_row.addWidget(self.sam_btn_clearmask)
        pts_layout.addLayout(op_row)
        save_row = QtWidgets.QHBoxLayout()
        self.sam_btn_save = QtWidgets.QPushButton("保存 ROI")
        self.sam_btn_save.setEnabled(False)
        self.sam_btn_save.clicked.connect(self._sam_save_roi)
        save_row.addWidget(self.sam_btn_save)
        self.sam_save_status = QtWidgets.QLabel("")
        self.sam_save_status.setStyleSheet("color:#4e7a2e; font-size:11px;")
        save_row.addWidget(self.sam_save_status, 1)
        pts_layout.addLayout(save_row)
        thr_row = QtWidgets.QHBoxLayout()
        thr_row.addWidget(QtWidgets.QLabel("阈值"))
        self.sam_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.sam_threshold_spin.setRange(0.05, 0.95)
        self.sam_threshold_spin.setSingleStep(0.05)
        self.sam_threshold_spin.setValue(0.30)
        thr_row.addWidget(self.sam_threshold_spin)
        thr_row.addStretch()
        pts_layout.addLayout(thr_row)
        self.sam_progress_lb = QtWidgets.QLabel("")
        self.sam_progress_lb.setWordWrap(True)
        self.sam_progress_lb.setStyleSheet("color: #a8641a; font-size: 11px;")
        pts_layout.addWidget(self.sam_progress_lb)
        self.sam_pts_group = pts_group
        sam_layout.addWidget(pts_group)

        # --- 已保存 ROI 列表/加载 ---
        self._build_saved_roi_panel(sam_layout)

        # 点击事件（左键=当前模式点，右键=负点）
        for axis, lb in self.sam_view_labels.items():
            lb.clicked.connect(self._sam_label_mouse)
            lb.scrolled.connect(self._sam_scroll_slice)
            lb.double_clicked.connect(self._sam_toggle_expand)
            lb.m_press.connect(self._sam_meas_press)
            lb.m_move.connect(self._sam_meas_move)
            lb.m_release.connect(self._sam_meas_release)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sam_page)
        # 放在「爱宠列表」之后、「渲染」之前（即渲染页左侧）
        self.pages.insertTab(1, scroll, "MPR 阅片")

    # ---------------- 会话/模型 ----------------
    def _sam_ensure_worker(self):
        """惰性创建常驻后台线程与 worker。

        注意：必须在主线程先初始化 torch/CUDA 上下文，否则后台线程第 2 次
        CUDA 调用会死锁（GUI 表现为第二次点击分割卡死）。
        基础版 EXE（未打包 torch/medim）会在此给出友好提示。
        """
        try:
            from segmentation.sam_pipeline import SamSession, ensure_torch_cuda_on_main
        except Exception as e:  # noqa: BLE001
            self._sam_status(f"3D SAM 依赖未打包：{type(e).__name__}: {e}\n请用 mar 环境运行源码，或使用 build_full 完整版", "#c0392b")
            return None
        ensure_torch_cuda_on_main()   # 主线程预建 CUDA 上下文（幂等、轻量）
        if self.sam_session is not None and self.sam_session.worker is not None:
            return self.sam_session.worker
        self.sam_session = SamSession(self)
        worker = self.sam_session.start()
        worker.progress_signal.connect(self._sam_on_progress)
        worker.finished_signal.connect(self._sam_on_finished)
        worker.error_signal.connect(self._sam_on_error)
        return worker

    def _sam_load_model(self):
        """启动常驻后台线程；SAM 权重在首次分割时自动加载（可点此预热）。"""
        worker = self._sam_ensure_worker()
        if worker is None:
            return
        self._sam_status("后台线程就绪；首次分割将自动加载权重 (~383MB)", "#e0c060")
        try:
            QtCore.QMetaObject.invokeMethod(
                worker, "ensure", QtCore.Qt.ConnectionType.QueuedConnection,
            )
        except Exception:  # noqa: BLE001
            pass

    def _sam_status(self, text, color="#7a6650"):
        self.sam_model_status.setText(text)
        self.sam_model_status.setStyleSheet(f"color: {color};")

    # ---------------- 视图 ----------------
    def _sam_vol(self):
        vol = getattr(self, "vr_work_array", None)
        if vol is None:
            vol = getattr(self, "vr_base_array", None)
        return vol

    def _sam_shape(self):
        vol = self._sam_vol()
        if vol is None:
            return None
        return tuple(int(v) for v in vol.shape)  # (D,H,W)

    def _sam_scroll_slice(self, axis, step):
        """滚轮翻层：调整对应视图的切片索引。"""
        sld = getattr(self, "sam_view_sliders", {}).get(axis)
        if sld is None:
            return
        sld.setValue(int(sld.value()) + int(step))

    def _sam_slider_changed(self, axis, value, idx_lb):
        # 任一切片变化都会影响三视图十字线 → 整体刷新
        if hasattr(self, "sam_view_sliders") and not self._sam_refreshing:
            self._sam_refresh_views()
        else:
            if idx_lb is not None:
                idx_lb.setText(f"{self.sam_view_names[axis]} = {value}")

    def _sam_cur_idx(self, axis):
        try:
            return int(self.sam_view_sliders[axis].value())
        except Exception:
            return 0

    def _sam_axis_slice(self, vol, axis, idx):
        """返回 (显示用 2D 数组, 行数, 列数)。

        轴 0=轴向(z 固定, 行=y 列=x)；轴 1=冠状(y 固定, 行=z 列=x)；
        轴 2=矢状(x 固定, 行=z 列=y)。
        冠状/矢状行序按「顶部=最大 z(头/上)」翻转，与 VR 冠状/矢状视角一致。
        """
        D = vol.shape[0]
        if axis == 0:
            return vol[idx, :, :], vol.shape[1], vol.shape[2]
        if axis == 1:
            return vol[::-1, idx, :], D, vol.shape[2]   # 行=z 翻转
        return vol[::-1, :, idx], D, vol.shape[1]       # 行=z 翻转

    def _sam_voxel(self, axis, idx, row, col, D):
        if axis == 0:
            return int(idx), int(row), int(col)
        if axis == 1:
            return int(D - 1 - row), int(idx), int(col)
        return int(D - 1 - row), int(col), int(idx)

    def _sam_view_pos(self, axis, D, z, y, x):
        """体素 (z,y,x) → 该视图像素 (col,row)。"""
        if axis == 0:
            return x, y
        if axis == 1:
            return x, D - 1 - z
        return y, D - 1 - z

    @staticmethod
    def _sam_plane_pos(axis, plane_axis, idx, D, H, W):
        """某平面(plane_axis, 索引=idx)在当前视图(axis)上的线位置。

        返回 (col,row,vertical)：vertical=True 为竖线(固定col, 沿行延展)，
        False 为横线(固定row, 沿列延展)。
        """
        if axis == 0:            # 轴向: 行=y(idx 即 plane1), 列=x
            if plane_axis == 1:      # 冠状面(y=idx) → 横线 row=idx
                return 0, idx, False
            if plane_axis == 2:      # 矢状面(x=idx) → 竖线 col=idx
                return idx, 0, True
        elif axis == 1:          # 冠状: 行=z(翻转为 D-1-idx), 列=x
            if plane_axis == 0:      # 轴向(z=idx) → 横线 row=D-1-idx
                return 0, D - 1 - idx, False
            if plane_axis == 2:      # 矢状面(x=idx) → 竖线 col=idx
                return idx, 0, True
        elif axis == 2:          # 矢状: 行=z(翻转), 列=y
            if plane_axis == 0:      # 轴向(z=idx) → 横线 row=D-1-idx
                return 0, D - 1 - idx, False
            if plane_axis == 1:      # 冠状面(y=idx) → 竖线 col=idx
                return idx, 0, True
        return None, None, None

    # 平面颜色：青=轴向面(z)、橙=冠状面(y)、品红=矢状面(x)
    _SAM_PLANE_COLORS = {0: (0, 224, 255), 1: (255, 176, 0), 2: (255, 64, 224)}

    def _sam_draw_crosshair(self, pix, axis, D, H, W):
        """在已生成的基础图上叠加互动十字线（其余两平面的位置）。"""
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rows, cols = pix.height(), pix.width()
        for plane_axis in (0, 1, 2):
            if plane_axis == axis:
                continue
            idx = self._sam_cur_idx(plane_axis)
            col, row, vertical = self._sam_plane_pos(axis, plane_axis, idx, D, H, W)
            if col is None:
                continue
            cr, cg, cb = self._SAM_PLANE_COLORS[plane_axis]
            pen = QPen(QColor(cr, cg, cb, 190))
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            if vertical:
                p.drawLine(int(col), 0, int(col), rows - 1)
            else:
                p.drawLine(0, int(row), cols - 1, int(row))
        p.end()

    # ================= 测量工具（长度/角度/矩形/圆形/不规则） =================
    def _sam_set_tool(self, key: str) -> None:
        """切换测量工具；'none' = 浏览（左键加提示点）。"""
        self.sam_tool = key or "none"
        measuring = self.sam_tool != "none"
        for lb in getattr(self, "sam_view_labels", {}).values():
            lb.set_measure_mode(measuring)
        self._sam_meas_draft = None
        name = self.sam_tool_names.get(self.sam_tool, self.sam_tool)
        self._sam_status(
            ("测量工具：%s" % name) if measuring else "浏览模式（不测量）", "#a8641a")
        self._sam_refresh_views()

    def _sam_meas_clear(self) -> None:
        """清除三个视图上的全部测量。"""
        self.sam_measures = {0: [], 1: [], 2: []}
        self._sam_meas_draft = None
        self._sam_status("已清除全部测量", "#a8641a")
        self._sam_refresh_views()

    def _sam_spacing_rc(self, axis: int):
        """该视图的行/列方向物理间距 (mm/px)：返回 (row_mm, col_mm)。"""
        sx = sy = sz = 1.0
        try:
            sp = self.vr_image_data.GetSpacing()
            sx, sy, sz = float(sp[0]), float(sp[1]), float(sp[2])
        except Exception:  # noqa: BLE001
            pass
        if axis == 0:          # 轴向：行=y, 列=x
            return (sy, sx)
        if axis == 1:          # 冠状：行=z, 列=x
            return (sz, sx)
        return (sz, sy)        # 矢状：行=z, 列=y

    def _sam_pix_from_info(self, axis: int, info):
        """屏幕坐标 → 该视图的 (col,row) 浮点像素；越界返回 None。"""
        lb = getattr(self, "sam_view_labels", {}).get(axis)
        if lb is None:
            return None
        rows, cols = self.sam_view_nat.get(axis, (1, 1))
        disp = lb.display_rect()
        px, py = float(info.get("x", 0.0)), float(info.get("y", 0.0))
        if disp.isNull() or disp.width() < 2 or disp.height() < 2:
            return None
        if not disp.contains(int(px), int(py)):
            return None
        col = min(cols - 1, max(0.0, (px - disp.x()) / disp.width() * cols))
        row = min(rows - 1, max(0.0, (py - disp.y()) / disp.height() * rows))
        return (float(col), float(row))

    @staticmethod
    def _sam_is_right_button(btn) -> bool:
        """稳健判断右键：兼容枚举与 int（新版 PySide 枚举不再与 int 相等）。"""
        try:
            if btn == QtCore.Qt.MouseButton.RightButton:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            _rv = getattr(QtCore.Qt.MouseButton.RightButton, "value",
                          QtCore.Qt.MouseButton.RightButton)
            _bv = getattr(btn, "value", btn)
            return int(_bv) == int(_rv)
        except Exception:  # noqa: BLE001
            return False

    def _sam_meas_press(self, axis, info):
        if getattr(self, "sam_tool", "none") == "none":
            return
        # 不规则形：右击 = 提前闭合当前描边
        if (self.sam_tool == "poly"
                and self._sam_is_right_button(info.get("button"))
                and self._sam_meas_draft is not None
                and self._sam_meas_draft.get("axis") == axis):
            self._sam_meas_commit()
            return
        pt = self._sam_pix_from_info(axis, info)
        if pt is None:
            return
        if self.sam_tool == "angle":
            d = self._sam_meas_draft
            if d is None or d.get("axis") != axis:
                d = {"axis": axis, "type": "angle", "pts": []}
                self._sam_meas_draft = d
            d["pts"].append(pt)
            if len(d["pts"]) >= 3:
                self._sam_meas_commit()
            else:
                self._sam_render_view(axis)
            return
        if self.sam_tool == "poly":
            # 自由描边：从按下开始记录轨迹，松开自动闭合
            self._sam_meas_draft = {"axis": axis, "type": "poly", "pts": [pt]}
        else:
            self._sam_meas_draft = {"axis": axis, "type": self.sam_tool, "pts": [pt, pt]}
        self._sam_render_view(axis)

    def _sam_meas_move(self, axis, info):
        d = self._sam_meas_draft
        if d is None or d.get("axis") != axis:
            return
        pt = self._sam_pix_from_info(axis, info)
        if pt is None:
            return
        if d["type"] == "poly":
            last = d["pts"][-1]
            # 抽稀：移动超过 1.2px 才记点，避免点数爆炸
            if (pt[0] - last[0]) ** 2 + (pt[1] - last[1]) ** 2 >= 1.44:
                d["pts"].append(pt)
        else:
            d["pts"][-1] = pt
        self._sam_render_view(axis)     # 只重绘当前轴，保持流畅

    def _sam_meas_release(self, axis, info):
        d = self._sam_meas_draft
        if d is None or d.get("axis") != axis:
            return
        pt = self._sam_pix_from_info(axis, info)
        if pt is not None and d["type"] != "poly":
            d["pts"][-1] = pt
        self._sam_meas_commit()

    def _sam_meas_commit(self) -> None:
        d = self._sam_meas_draft
        self._sam_meas_draft = None
        if not d:
            return
        axis, t, pts = d["axis"], d["type"], d["pts"]
        if t == "poly":
            if len(pts) < 3:
                self._sam_render_view(axis)
                return
        else:
            need = 3 if t == "angle" else 2
            if len(pts) < need:
                self._sam_render_view(axis)
                return
            # 太小（误触）则丢弃
            if t != "angle":
                (c1, r1), (c2, r2) = pts[0], pts[-1]
                if abs(c2 - c1) < 1.5 and abs(r2 - r1) < 1.5:
                    self._sam_render_view(axis)
                    return
        m = {"type": t, "pts": [tuple(p) for p in pts]}
        m["text"] = self._sam_meas_text(axis, m)
        self.sam_measures.setdefault(axis, []).append(m)
        self._sam_render_view(axis)

    def _sam_meas_text(self, axis: int, m: dict) -> str:
        import math
        rsp, csp = self._sam_spacing_rc(axis)
        t, pts = m["type"], m["pts"]

        def _d(a, b):
            dc = (b[0] - a[0]) * csp
            dr = (b[1] - a[1]) * rsp
            return math.hypot(dc, dr)

        if t == "line":
            return "%.1f mm" % _d(pts[0], pts[1])
        if t == "angle":
            v, a, b = pts[0], pts[1], pts[2]
            v1 = ((a[0] - v[0]) * csp, (a[1] - v[1]) * rsp)
            v2 = ((b[0] - v[0]) * csp, (b[1] - v[1]) * rsp)
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            if n1 < 1e-6 or n2 < 1e-6:
                return "0.0\u00b0"
            cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
            return "%.1f\u00b0" % math.degrees(math.acos(cos))
        if t == "rect":
            w = abs(pts[1][0] - pts[0][0]) * csp
            h = abs(pts[1][1] - pts[0][1]) * rsp
            return "%.1f\u00d7%.1f mm  %.2f cm\u00b2" % (w, h, w * h / 100.0)
        if t == "ellipse":
            a = abs(pts[1][0] - pts[0][0]) * csp / 2.0
            b = abs(pts[1][1] - pts[0][1]) * rsp / 2.0
            return "%.1f/%.1f mm  %.2f cm\u00b2" % (2 * a, 2 * b, math.pi * a * b / 100.0)
        if t == "poly":
            area = 0.0
            per = 0.0
            n = len(pts)
            for i in range(n):
                c1, r1 = pts[i]
                c2, r2 = pts[(i + 1) % n]
                x1, y1 = c1 * csp, r1 * rsp
                x2, y2 = c2 * csp, r2 * rsp
                area += x1 * y2 - x2 * y1
                per += math.hypot(x2 - x1, y2 - y1)
            return "%.2f cm\u00b2  \u5468\u957f%.0fmm" % (abs(area) / 2.0 / 100.0, per)
        return ""

    def _sam_draw_measures(self, p: QPainter, axis: int) -> None:
        """在当前切片 pixmap 上绘制测量（含正在拖拽的草稿）。"""
        items = list(self.sam_measures.get(axis, []))
        d = self._sam_meas_draft
        if d is not None and d.get("axis") == axis:
            items = items + [{"type": d["type"], "draft": True,
                              "pts": [tuple(x) for x in d["pts"]], "text": ""}]
        if not items:
            return
        for m in items:
            pts = m.get("pts") or []
            if not pts:
                continue
            t = m["type"]
            p.setPen(QPen(QColor(255, 214, 0), 2))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            if t == "line" and len(pts) >= 2:
                p.drawLine(QtCore.QPointF(*pts[0]), QtCore.QPointF(*pts[-1]))
                p.setBrush(QColor(255, 214, 0))
                for q in (pts[0], pts[-1]):
                    p.drawEllipse(QtCore.QPointF(*q), 3.5, 3.5)
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            elif t == "angle" and len(pts) >= 2:
                for q in pts[1:]:
                    p.drawLine(QtCore.QPointF(*pts[0]), QtCore.QPointF(*q))
                p.setBrush(QColor(255, 214, 0))
                for q in pts:
                    p.drawEllipse(QtCore.QPointF(*q), 3.5, 3.5)
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            elif t == "rect" and len(pts) >= 2:
                x1, y1 = pts[0]
                x2, y2 = pts[-1]
                p.drawRect(QtCore.QRectF(min(x1, x2), min(y1, y2),
                                         abs(x2 - x1), abs(y2 - y1)))
            elif t == "ellipse" and len(pts) >= 2:
                x1, y1 = pts[0]
                x2, y2 = pts[-1]
                p.drawEllipse(QtCore.QRectF(min(x1, x2), min(y1, y2),
                                            abs(x2 - x1), abs(y2 - y1)))
            elif t == "poly" and len(pts) >= 2:
                if m.get("draft"):
                    # 描边中：画折线 + 虚线提示"将首尾闭合成多边形"
                    p.drawPolyline(QPolygonF([QtCore.QPointF(c, r) for c, r in pts]))
                    p.setPen(QPen(QColor(255, 214, 0, 170), 2,
                                  QtCore.Qt.PenStyle.DashLine))
                    p.drawLine(QtCore.QPointF(*pts[-1]), QtCore.QPointF(*pts[0]))
                    p.setPen(QPen(QColor(255, 214, 0), 2))
                else:
                    p.drawPolygon(QPolygonF([QtCore.QPointF(c, r) for c, r in pts]))
            # 数值标签
            txt = m.get("text") or ""
            if txt:
                ax, ay = pts[0]
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(txt) + 10
                hh = fm.height() + 2
                rect = QtCore.QRectF(ax + 8, ay - hh - 6, w, hh)
                p.setPen(QPen(QColor(120, 70, 10), 1))
                p.setBrush(QColor(255, 232, 150, 235))
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QPen(QColor(60, 40, 0)))
                p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), txt)

    def _sam_ww_wl_changed(self, *_):
        """MPR 窗宽/窗位变化：更新数值标签并重绘三视图。"""
        try:
            self.sam_wl = int(self.sam_wl_slider.value())
            self.sam_ww = int(self.sam_ww_slider.value())
        except Exception:
            return
        if hasattr(self, "sam_wl_value"):
            self.sam_wl_value.setText(f"{self.sam_wl} HU")
        if hasattr(self, "sam_ww_value"):
            self.sam_ww_value.setText(f"{self.sam_ww} HU")
        self._sam_refresh_views()

    def _sam_ww_preset_changed(self, index: int):
        """窗宽/窗位预设（WW, WL）。"""
        presets = {1: (400, 40), 2: (1500, -600), 3: (2000, 400),
                   4: (80, 40), 5: (350, 50)}
        if index in presets:
            ww, wl = presets[index]
            with QtCore.QSignalBlocker(self.sam_ww_slider), \
                    QtCore.QSignalBlocker(self.sam_wl_slider):
                self.sam_ww_slider.setValue(ww)
                self.sam_wl_slider.setValue(wl)
            self._sam_ww_wl_changed()
        # 复位到占位项，方便再次选择
        self.sam_ww_preset.blockSignals(True)
        self.sam_ww_preset.setCurrentIndex(0)
        self.sam_ww_preset.blockSignals(False)

    def _sam_render_view(self, axis):
        vol = self._sam_vol()
        if vol is None:
            self.sam_view_labels[axis].setPixmap(QPixmap())
            return
        D, H, W = vol.shape
        idx = int(max(0, min(self._sam_cur_idx(axis), [D, H, W][axis] - 1)))
        arr, rows, cols = self._sam_axis_slice(vol, axis, idx)
        ww = float(getattr(self, "sam_ww", 400) or 400)
        wl = float(getattr(self, "sam_wl", 40))
        arrf = arr.astype(np.float32)
        if getattr(self, "modality", "CT") == "CT":
            # CT：真正的窗宽/窗位映射（HU）：[WL-WW/2, WL+WW/2] → 0..255
            lo = wl - ww / 2.0
            hi = lo + ww
            gray = np.clip((arrf - lo) / max(1e-6, (hi - lo)) * 255.0, 0, 255)
        else:
            # MR/DR：无 HU，按 1–99 百分位归一化；窗宽→对比度，窗位→亮度
            _lo = float(np.percentile(arrf, 1.0))
            _hi = float(np.percentile(arrf, 99.0))
            if _hi <= _lo:
                _hi = _lo + 1.0
            norm = (arrf - _lo) / (_hi - _lo) * 255.0
            contrast = max(0.2, min(4.0, 400.0 / max(1.0, ww)))
            bright = (wl - 40.0) * 0.25
            gray = np.clip((norm - 128.0) * contrast + 128.0 + bright, 0, 255)
        gray = gray.astype(np.uint8)
        rgb = np.repeat(gray[:, :, None], 3, axis=2)

        mask = self.sam_mask
        if mask is not None and mask.shape == (D, H, W):
            m = mask[idx, :, :] if axis == 0 else (mask[:, idx, :] if axis == 1 else mask[:, :, idx])
            if axis in (1, 2):
                m = m[::-1, :]          # 与行翻转后的视图对齐
            if m.shape == rgb.shape[:2]:
                over = rgb[m]
                rgb[m] = (0.20 * over + 0.80 * np.array([90, 255, 110], dtype=np.float32)).astype(np.uint8)

        self.sam_view_nat[axis] = (rows, cols)
        pix = self._np_rgb_to_pixmap(rgb)
        # 互动十字线
        self._sam_draw_crosshair(pix, axis, D, H, W)
        # 提示点标记（仅在点所在平面显示；行映射需考虑 z 翻转）
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 255, 0), 2))
        for pt in self.sam_pos_pts:
            z, y, x = pt
            if (axis == 0 and z == idx) or (axis == 1 and y == idx) or (axis == 2 and x == idx):
                col, row = self._sam_view_pos(axis, D, z, y, x)
                self._paint_cross(p, col, row)
        p.setPen(QPen(QColor(255, 40, 40), 2))
        for pt in self.sam_neg_pts:
            z, y, x = pt
            if (axis == 0 and z == idx) or (axis == 1 and y == idx) or (axis == 2 and x == idx):
                col, row = self._sam_view_pos(axis, D, z, y, x)
                self._paint_cross(p, col, row)
        # 测量标注（长度/角度/矩形/圆/不规则）
        try:
            self._sam_draw_measures(p, axis)
        except Exception:  # noqa: BLE001
            pass
        p.end()
        self.sam_view_labels[axis].set_source(pix)
        ilb = self.sam_view_idxlabels.get(axis)
        if ilb is not None:
            ilb.setText(f"{self.sam_view_names[axis]} = {idx}")

    @staticmethod
    def _paint_cross(p: QPainter, cx, cy):
        r = 6
        p.drawLine(int(cx) - r, int(cy), int(cx) + r, int(cy))
        p.drawLine(int(cx), int(cy) - r, int(cx), int(cy) + r)

    @staticmethod
    def _np_rgb_to_pixmap(rgb):
        rows, cols, _ = rgb.shape
        img = QImage(rgb.data, cols, rows, 3 * cols, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def _sam_refresh_views(self):
        if self._sam_refreshing:
            return
        self._sam_refreshing = True
        try:
            if self._sam_vol() is None:
                for a in (0, 1, 2):
                    self.sam_view_labels[a].setText("无数据")
                    self.sam_view_sliders[a].setRange(0, 0)
                return
            D, H, W = self._sam_shape()
            for a, dim in ((0, D), (1, H), (2, W)):
                sld = self.sam_view_sliders[a]
                old = sld.value()
                sld.blockSignals(True)
                sld.setRange(0, max(0, dim - 1))
                if old == 0 and dim > 0:
                    sld.setValue(dim // 2)
                sld.blockSignals(False)
            for a in (0, 1, 2):
                self._sam_render_view(a)
        finally:
            self._sam_refreshing = False

    def _sam_reset(self):
        """新数据加载后重置 SAM 状态。"""
        self.sam_pos_pts = []
        self.sam_neg_pts = []
        self.sam_mask = None
        self.sam_busy = False
        self.sam_pending = False
        # 新数据到来时退出放大态，回到 2×2
        if getattr(self, "sam_expanded_axis", None) is not None:
            self._sam_set_expanded(None)
        # 清空测量并回到浏览模式
        self.sam_measures = {0: [], 1: [], 2: []}
        self._sam_meas_draft = None
        if hasattr(self, "sam_tool_buttons"):
            self.sam_tool = "none"
            self.sam_tool_buttons["none"].setChecked(True)
            for _lb in getattr(self, "sam_view_labels", {}).values():
                _lb.set_measure_mode(False)
        # 窗宽/窗位回到默认（软组织 400/40）
        if hasattr(self, "sam_ww_slider"):
            with QtCore.QSignalBlocker(self.sam_ww_slider), \
                    QtCore.QSignalBlocker(self.sam_wl_slider):
                self.sam_ww_slider.setValue(400)
                self.sam_wl_slider.setValue(40)
            self._sam_ww_wl_changed()
        if hasattr(self, "sam_summary"):
            self.sam_summary.setText("无点")
            self.sam_progress_lb.setText("")
            self.sam_btn_run.setEnabled(False)
        if hasattr(self, "sam_view_sliders"):
            self._sam_refresh_views()

    # ---------------- 点击 ----------------
    def _sam_toggle_expand(self, axis: int) -> None:
        """双击某视图：放大占满整个阅片区；再次双击（或双击其它视图）还原/切换。"""
        if getattr(self, "sam_expanded_axis", None) == axis:
            self._sam_set_expanded(None)
        else:
            self._sam_set_expanded(axis)

    def _sam_set_expanded(self, axis):
        """axis=None 还原 2×2；否则把该视图跨满整格并隐藏其它视图与附属面板。"""
        grid = getattr(self, "sam_mpr_grid", None)
        if grid is None or not getattr(self, "sam_cells", None):
            return
        # 先把当前放大项放回原格，避免残留跨格
        cur = getattr(self, "sam_expanded_axis", None)
        if cur is not None and cur in self.sam_cells:
            _c = self.sam_cells[cur]
            grid.removeWidget(_c)
            _r, _col = self.sam_cell_pos[cur]
            grid.addWidget(_c, _r, _col)

        panels = [getattr(self, n, None) for n in
                  ("sam_mdl_group", "sam_pts_group", "sam_roi_group")]

        if axis is None:
            for _a, _cell in self.sam_cells.items():
                _cell.setVisible(True)
            _info = getattr(self, "sam_info_cell", None)
            if _info is not None:
                _info.setVisible(True)
            for _w in panels:
                if _w is not None:
                    _w.setVisible(True)
            self.sam_expanded_axis = None
            self._sam_status("已还原 2×2 视图", "#a8641a")
        else:
            for _a, _cell in self.sam_cells.items():
                _cell.setVisible(_a == axis)
            _info = getattr(self, "sam_info_cell", None)
            if _info is not None:
                _info.setVisible(False)
            for _w in panels:
                if _w is not None:
                    _w.setVisible(False)
            _cell = self.sam_cells[axis]
            grid.removeWidget(_cell)
            grid.addWidget(_cell, 0, 0, max(1, grid.rowCount()), max(1, grid.columnCount()))
            _cell.setVisible(True)
            self.sam_expanded_axis = axis
            self._sam_status(
                "已放大「%s」；双击返回 2×2" % self.sam_view_names.get(axis, axis),
                "#a8641a")
        QtCore.QTimer.singleShot(0, self._sam_refresh_views)

    def _sam_label_mouse(self, axis, info):
        vol = self._sam_vol()
        if vol is None:
            return
        D, H, W = vol.shape
        sld = self.sam_view_sliders[axis]
        idx = sld.value()
        rows, cols = self.sam_view_nat[axis]
        lb = self.sam_view_labels[axis]
        # 用实际等比绘制矩形换算坐标（点在图外则忽略）
        disp = lb.display_rect()
        px, py = float(info.get("x", 0.0)), float(info.get("y", 0.0))
        if disp.isNull() or disp.width() < 2 or disp.height() < 2:
            return
        if not disp.contains(int(px), int(py)):
            return
        col = int(min(cols - 1, max(0, (px - disp.x()) / disp.width() * cols)))
        row = int(min(rows - 1, max(0, (py - disp.y()) / disp.height() * rows)))
        vox = self._sam_voxel(axis, idx, row, col, D)
        positive = self.sam_btn_mode_pos.isChecked()
        if self._sam_is_right_button(info.get("button")):
            positive = False
        # 互动：把另外两平面切到点击体素处（三视图十字线交汇于该点）
        z, y, x = vox
        if axis != 0:
            s0 = self.sam_view_sliders[0]
            s0.setValue(int(max(0, min(z, D - 1))))
        if axis != 1:
            s1 = self.sam_view_sliders[1]
            s1.setValue(int(max(0, min(y, H - 1))))
        if axis != 2:
            s2 = self.sam_view_sliders[2]
            s2.setValue(int(max(0, min(x, W - 1))))
        self._sam_add_point(vox, positive)

    def _sam_add_point(self, vox, positive):
        # 不再因推理中而丢弃点击：先记录点，若正在推理则排队，完成后再统一重算
        if positive:
            self.sam_pos_pts.append(tuple(int(v) for v in vox))
        else:
            self.sam_neg_pts.append(tuple(int(v) for v in vox))
        self._sam_refresh_views()
        self.sam_summary.setText(
            f"正 {len(self.sam_pos_pts)} 负 {len(self.sam_neg_pts)}  最近点 (z={vox[0]},y={vox[1]},x={vox[2]})"
        )
        self.sam_btn_run.setEnabled(True)
        if self.sam_busy:
            self.sam_pending = True
        else:
            self._sam_run()

    def _sam_undo(self):
        if self.sam_pos_pts:
            self.sam_pos_pts.pop()
        elif self.sam_neg_pts:
            self.sam_neg_pts.pop()
        self._sam_refresh_views()
        self.sam_summary.setText(f"正 {len(self.sam_pos_pts)} 负 {len(self.sam_neg_pts)}")
        if self.sam_pos_pts or self.sam_neg_pts:
            self.sam_btn_run.setEnabled(True)
            self._sam_run()
        else:
            self.sam_btn_run.setEnabled(False)
            self.sam_mask = None
            self._sam_clear_overlay()

    def _sam_clear_points(self):
        self.sam_pos_pts = []
        self.sam_neg_pts = []
        self.sam_summary.setText("无点")
        self.sam_btn_run.setEnabled(False)
        self.sam_mask = None
        self._sam_clear_overlay()
        self._sam_refresh_views()

    def _sam_clear_result(self):
        self.sam_mask = None
        self._sam_clear_overlay()
        self._sam_refresh_views()
        if hasattr(self, "sam_btn_save"):
            self.sam_btn_save.setEnabled(False)

    # ---------------- ROI 保存 ----------------
    def _sam_roi_stats(self):
        """从当前 SAM mask + VR 网格计算统计，供保存对话框预填。"""
        mask = self.sam_mask
        vol = self._sam_vol()
        if mask is None or vol is None or mask.shape != vol.shape or not mask.any():
            return None
        vals = vol[mask].astype(np.float64)
        spacing = (1.0, 1.0, 1.0)
        try:
            if self.vr_image_data is not None:
                spacing = tuple(float(v) for v in self.vr_image_data.GetSpacing())
        except Exception:
            pass
        vol_mm3 = vals.size * spacing[0] * spacing[1] * spacing[2]
        return {
            "voxels": int(vals.size),
            "volume_cm3": vol_mm3 / 1000.0,
            "mean_hu": float(vals.mean()),
            "std_hu": float(vals.std()),
            "min_hu": float(vals.min()),
            "max_hu": float(vals.max()),
        }

    def _sam_save_roi(self):
        """弹窗输入 ROI 名称/体积/统计值并保存记录。"""
        stats = self._sam_roi_stats()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("保存 ROI（3D SAM）")
        dlg.setMinimumWidth(420)
        form = QtWidgets.QFormLayout(dlg)
        ed_name = QtWidgets.QLineEdit("")
        ed_name.setPlaceholderText("例如：左上肺叶 / 病灶A")
        form.addRow("ROI 名称 *", ed_name)
        sp_vol = QtWidgets.QDoubleSpinBox()
        sp_vol.setRange(0.0, 1e7); sp_vol.setDecimals(3); sp_vol.setSuffix(" cm³")
        sp_vox = QtWidgets.QSpinBox(); sp_vox.setRange(0, 10 ** 9)
        sp_mean = QtWidgets.QDoubleSpinBox(); sp_mean.setRange(-4000, 8000); sp_mean.setDecimals(2); sp_mean.setSuffix(" HU")
        sp_std = QtWidgets.QDoubleSpinBox(); sp_std.setRange(0, 8000); sp_std.setDecimals(2)
        sp_min = QtWidgets.QDoubleSpinBox(); sp_min.setRange(-4000, 8000); sp_min.setDecimals(2)
        sp_max = QtWidgets.QDoubleSpinBox(); sp_max.setRange(-4000, 8000); sp_max.setDecimals(2)
        if stats:
            sp_vol.setValue(round(stats["volume_cm3"], 3))
            sp_vox.setValue(stats["voxels"])
            sp_mean.setValue(round(stats["mean_hu"], 2))
            sp_std.setValue(round(stats["std_hu"], 2))
            sp_min.setValue(round(stats["min_hu"], 2))
            sp_max.setValue(round(stats["max_hu"], 2))
            auto = QtWidgets.QLabel("已从当前绿色 SAM 结果自动计算，可修改。")
        else:
            auto = QtWidgets.QLabel("当前无 SAM 结果：请手动填写体积与统计值。")
        auto.setStyleSheet("color:#a8641a;")
        form.addRow(auto)
        form.addRow("体积", sp_vol)
        form.addRow("体素数", sp_vox)
        form.addRow("像素均值", sp_mean)
        form.addRow("标准差", sp_std)
        form.addRow("最小值", sp_min)
        form.addRow("最大值", sp_max)
        ed_src = QtWidgets.QLineEdit(os.path.basename(self.path_edit.line_edit().text().strip()) or "3D SAM")
        form.addRow("来源/病例", ed_src)
        ed_note = QtWidgets.QLineEdit("")
        ed_note.setPlaceholderText("备注（可选）")
        form.addRow("备注", ed_note)
        hint = QtWidgets.QLabel("")
        hint.setStyleSheet("color:#c0392b;")
        form.addRow(hint)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("保存")
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        form.addRow(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)

        while True:
            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            name = ed_name.text().strip()
            if name:
                break
            hint.setText("请填写 ROI 名称")
        import csv, time
        from mcp_ssd_vr import config as _cfg
        _cfg.ensure_dirs()
        # 先落盘 mask (npy)，再写 CSV（含 mask_npy 路径列，供列表加载回显）
        npy_path = ""
        if stats is not None:
            npy_dir = os.path.join(_cfg.record_dir(), "roi_sam_masks")
            os.makedirs(npy_dir, exist_ok=True)
            npy_path = os.path.join(npy_dir, f"sam_{time.strftime('%Y%m%d_%H%M%S')}_{name}.npy")
            np.save(npy_path, self.sam_mask)
        rec = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "name": name,
            "volume_cm3": sp_vol.value(),
            "voxels": sp_vox.value(),
            "mean_hu": sp_mean.value(),
            "std_hu": sp_std.value(),
            "min_hu": sp_min.value(),
            "max_hu": sp_max.value(),
            "source": ed_src.text().strip() or "3D SAM",
            "note": ed_note.text().strip(),
            "pos_points": len(self.sam_pos_pts),
            "neg_points": len(self.sam_neg_pts),
            "mask_npy": npy_path,
        }
        csv_path = self._sam_roi_csv_path()
        header = ["time", "name", "volume_cm3", "voxels", "mean_hu", "std_hu",
                  "min_hu", "max_hu", "source", "note", "pos_points", "neg_points", "mask_npy"]
        new_file = not os.path.exists(csv_path)
        try:
            with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
                if new_file:
                    w.writeheader()
                w.writerow(rec)
            msg = f"已保存 ROI: {name}（体积 {sp_vol.value():.3f} cm³） → {csv_path}"
            if npy_path:
                msg += f"\nmask: {npy_path}"
            self.sam_save_status.setText(msg)
            self._sam_status(f"ROI 已保存: {name}", "#4e7a2e")
            self._sam_refresh_roi_list()
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(e))

    @staticmethod
    def _sam_roi_csv_path():
        from mcp_ssd_vr import config as _cfg
        _cfg.ensure_dirs()
        return os.path.join(_cfg.record_dir(), "roi_summary_3dsam.csv")

    def _sam_roi_load_records(self):
        """读取已保存 ROI 记录；旧文件无 mask_npy 列时自动补空列迁移。

        同时**清除历史遗留的「自动ROI (TotalSegmentator / SynthSeg / nnU-Net)」条目**——
        它们是已移除的「自动ROI检测」功能写下的默认列表，不再显示、并从 CSV 中删除。
        """
        import csv as _csv
        path = self._sam_roi_csv_path()
        if not os.path.exists(path):
            return []
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            rows = list(_csv.DictReader(f))
        full_header = ["time", "name", "volume_cm3", "voxels", "mean_hu", "std_hu",
                       "min_hu", "max_hu", "source", "note", "pos_points", "neg_points", "mask_npy"]
        if not rows:
            return rows

        def _is_legacy(r):
            src = (r.get("source") or "").strip().lower()
            return (src.startswith("totalsegmentator") or src.startswith("nnunet")
                    or "synthseg" in src)

        kept = [r for r in rows if not _is_legacy(r)]
        removed = len(rows) - len(kept)
        for r in kept:
            r.setdefault("mask_npy", "")
        # 需要补列，或需要清除遗留条目时，重写一次 CSV
        if "mask_npy" not in rows[0] or removed > 0:
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    w = _csv.DictWriter(f, fieldnames=full_header, extrasaction="ignore")
                    w.writeheader()
                    for r in kept:
                        w.writerow(r)
            except Exception:  # noqa: BLE001
                pass
        return kept

    def _build_saved_roi_panel(self, sam_layout) -> None:
        """「已保存 ROI」列表 + 加载/删除。"""
        grp = QtWidgets.QGroupBox("已保存 ROI（列表/加载）")
        lay = QtWidgets.QVBoxLayout(grp)
        self.sam_roi_table = QtWidgets.QTableWidget(0, 6)
        self.sam_roi_table.setHorizontalHeaderLabels(
            ["时间", "名称", "体积 cm³", "均值 HU", "来源", "mask"])
        self.sam_roi_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.sam_roi_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        # 选中行高亮样式（深色主题下也要明显）
        self.sam_roi_table.setStyleSheet(
            "QTableWidget::item:selected { background:#ffd9a8; color:#5a3a10; }"
            "QTableWidget::item:selected:!active { background:#f4c98a; color:#5a3a10; }"
            "QTableWidget::item:hover { background:#fdf0dc; }"
            "QTableWidget { selection-background-color:#ffd9a8; }")
        self.sam_roi_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sam_roi_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.sam_roi_table.verticalHeader().setVisible(False)
        self.sam_roi_table.setMaximumHeight(160)
        lay.addWidget(self.sam_roi_table)
        btns = QtWidgets.QHBoxLayout()
        self.sam_btn_reload_roi = QtWidgets.QPushButton("刷新列表")
        self.sam_btn_reload_roi.clicked.connect(self._sam_refresh_roi_list)
        self.sam_btn_load_roi = QtWidgets.QPushButton("加载选中 → 高亮")
        self.sam_btn_load_roi.clicked.connect(self._sam_load_selected_roi)
        self.sam_btn_del_roi = QtWidgets.QPushButton("删除选中")
        self.sam_btn_del_roi.clicked.connect(self._sam_delete_selected_roi)
        self.sam_btn_export_xlsx = QtWidgets.QPushButton("一键导出 Excel")
        self.sam_btn_export_xlsx.clicked.connect(self._sam_export_roi_excel)
        btns.addWidget(self.sam_btn_reload_roi)
        btns.addWidget(self.sam_btn_load_roi)
        btns.addWidget(self.sam_btn_del_roi)
        btns.addWidget(self.sam_btn_export_xlsx)
        lay.addLayout(btns)
        # 第二行：全选 / 选中即高亮(叠加)
        row2 = QtWidgets.QHBoxLayout()
        self.sam_btn_sel_all = QtWidgets.QPushButton("全选")
        self.sam_btn_sel_all.clicked.connect(self._sam_roi_select_all)
        self.sam_chk_auto_hl = QtWidgets.QCheckBox("选中行同步到 VR 绿色高亮（叠加）")
        self.sam_chk_auto_hl.setChecked(False)
        self.sam_chk_auto_hl.toggled.connect(self._sam_roi_selection_changed)
        row2.addWidget(self.sam_btn_sel_all)
        row2.addWidget(self.sam_chk_auto_hl)
        row2.addStretch()
        lay.addLayout(row2)
        # 行选择变化 → 若开启自动高亮则重算叠加
        self.sam_roi_table.itemSelectionChanged.connect(self._sam_roi_selection_changed)
        self.sam_roi_panel_status = QtWidgets.QLabel("")
        self.sam_roi_panel_status.setWordWrap(True)
        self.sam_roi_panel_status.setStyleSheet("color:#a8641a; font-size:11px;")
        lay.addWidget(self.sam_roi_panel_status)
        self.sam_roi_group = grp
        sam_layout.addWidget(grp)
        self._sam_refresh_roi_list()

    def _sam_refresh_roi_list(self):
        if not hasattr(self, "sam_roi_table"):
            return
        rows = self._sam_roi_load_records()
        tbl = self.sam_roi_table
        tbl.setRowCount(0)
        self._sam_roi_cache = rows
        for i, r in enumerate(rows):
            tbl.insertRow(i)
            mask_ok = bool(r.get("mask_npy")) and os.path.exists(r.get("mask_npy") or "")
            vals = [r.get("time", ""), r.get("name", ""), r.get("volume_cm3", ""),
                    r.get("mean_hu", ""), r.get("source", ""), "✓" if mask_ok else "—"]
            for c, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(str(v))
                if c == 5:
                    item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(i, c, item)
        path = self._sam_roi_csv_path()
        self.sam_roi_panel_status.setText(
            f"共 {len(rows)} 条 ｜ {path}" + ("（无 mask 的记录仅能查看统计，无法高亮）" if any(
                not (r.get("mask_npy") and os.path.exists(r.get("mask_npy") or "")) for r in rows) else ""))

    # ---- 已保存ROI 列表：多选/全选/选中高亮 ----
    def _sam_roi_selected_rows(self):
        """返回当前选中行的记录列表（按行序）。"""
        cache = getattr(self, "_sam_roi_cache", [])
        sel = sorted({i.row() for i in self.sam_roi_table.selectionModel().selectedRows()})
        return [cache[i] for i in sel if 0 <= i < len(cache)]

    def _sam_roi_select_all(self):
        self.sam_roi_table.selectAll()

    def _sam_roi_selection_changed(self, *args):
        if not getattr(self, "sam_chk_auto_hl", None) or not self.sam_chk_auto_hl.isChecked():
            return
        QtCore.QTimer.singleShot(30, self._sam_roi_apply_selection_highlight)

    def _sam_roi_apply_selection_highlight(self, quiet=True):
        """把当前选中行(带 mask)合并叠加为绿色高亮；无选中则清空。"""
        rows = self._sam_roi_selected_rows()
        if not rows:
            self.sam_mask = None
            self._sam_clear_overlay()
            self._sam_refresh_views()
            return
        withmask = [r for r in rows if r.get("mask_npy") and os.path.exists(r.get("mask_npy") or "")]
        if not withmask:
            return
        cur = self._sam_vol()
        if cur is None:
            return
        shape = tuple(cur.shape)
        acc = None
        names = []
        for r in withmask:
            try:
                m = np.load(r["mask_npy"])
                if m.dtype != bool:
                    m = m > 0.5
                if m.shape != shape:
                    m = self._resize_mask_nearest(m, shape)
                acc = m if acc is None else (acc | m)
                names.append(r.get("name", "?"))
            except Exception:
                continue
        if acc is None:
            return
        self.sam_mask = acc
        self._sam_write_overlay()
        self._sam_refresh_views()
        if not quiet:
            self.sam_progress_lb.setText(f"叠加高亮 {len(withmask)} 条 ROI: {'、'.join(names[:6])}{'…' if len(names) > 6 else ''}（{int(acc.sum())} 体素）")
        else:
            self.sam_progress_lb.setText(f"已叠加高亮 {len(withmask)} 条 ROI（{int(acc.sum())} 体素）")
        self._sam_status(f"高亮 {len(withmask)} 条", "#4e7a2e")

    def _sam_load_selected_roi(self):
        cache = getattr(self, "_sam_roi_cache", [])
        rows = self._sam_roi_selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "提示", "请先在列表选中（可多选/全选）已保存 ROI")
            return
        no_mask = [r.get("name") for r in rows if not (r.get("mask_npy") and os.path.exists(r.get("mask_npy") or ""))]
        cur = self._sam_vol()
        shape = tuple(cur.shape) if cur is not None else None
        acc = None
        cnt = 0
        for r in rows:
            npy = r.get("mask_npy") or ""
            if not os.path.exists(npy):
                continue
            try:
                m = np.load(npy)
                if m.dtype != bool:
                    m = m > 0.5
                if shape is not None and m.shape != shape:
                    m = self._resize_mask_nearest(m, shape)
                acc = m if acc is None else (acc | m)
                cnt += 1
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "加载失败", f"{r.get('name')}: {e}")
        if acc is None:
            names = "、".join(r.get("name", "?") for r in rows[:8])
            QtWidgets.QMessageBox.warning(self, "无 mask",
                                          f"选中记录都没有可加载的 mask：\n{names}"
                                          + ("…" if len(rows) > 8 else "")
                                          + "\n（列表「—」行只有统计，无法高亮）")
            return
        self.sam_mask = acc
        self._sam_write_overlay()
        self._sam_refresh_views()
        msg = f"已加载并叠加 {cnt} 条 ROI（{int(acc.sum())} 体素，绿色高亮）"
        if no_mask:
            msg += "\n无 mask 仅统计：" + "、".join(no_mask[:8]) + ("…" if len(no_mask) > 8 else "")
        self.sam_progress_lb.setText(msg)
        self._sam_status(f"已加载 {cnt} 条 ROI", "#4e7a2e")

    def _sam_delete_selected_roi(self):
        rows = self._sam_roi_selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要删除的记录（可多选/全选）")
            return
        names = "、".join(r.get("name", "?") for r in rows[:6]) + ("…" if len(rows) > 6 else "")
        if QtWidgets.QMessageBox.question(
                self, "删除确认", f"删除选中的 {len(rows)} 条 ROI？\n{names}\n(关联的 npy mask 也会删除)",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        import csv as _csv
        path = self._sam_roi_csv_path()
        all_rows = self._sam_roi_load_records()
        idxs = {self.sam_roi_table.row(i) for i in self.sam_roi_table.selectionModel().selectedRows()}
        to_del = [r for i, r in enumerate(all_rows) if i in idxs]
        keep = [r for i, r in enumerate(all_rows) if i not in idxs]
        header = list(all_rows[0].keys()) if all_rows else \
            ["time", "name", "volume_cm3", "voxels", "mean_hu", "std_hu",
             "min_hu", "max_hu", "source", "note", "pos_points", "neg_points", "mask_npy"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for row in keep:
                w.writerow(row)
        removed_npy = 0
        for r in to_del:
            npy = r.get("mask_npy") or ""
            if npy and os.path.exists(npy):
                try:
                    os.remove(npy)
                    removed_npy += 1
                except Exception:
                    pass
        self.sam_save_status.setText(f"已删除 {len(to_del)} 条 ROI（含 {removed_npy} 个 mask）")
        self._sam_refresh_roi_list()
        self._sam_roi_selection_changed()

    def _sam_export_roi_excel(self):
        """把已保存 ROI 全部记录一键导出为 Excel(.xlsx)。"""
        import time as _t
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            from openpyxl.utils import get_column_letter
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "缺少依赖", f"需要 openpyxl：{e}")
            return
        rows = self._sam_roi_load_records()
        if not rows:
            QtWidgets.QMessageBox.information(self, "无数据", "当前没有已保存的 ROI 记录。")
            return
        headers = [
            "保存时间", "ROI 名称", "体积 cm³", "体素数", "均值 HU", "标准差",
            "最小 HU", "最大 HU", "来源", "备注", "正点数", "负点数", "mask 文件",
        ]
        keys = ["time", "name", "volume_cm3", "voxels", "mean_hu", "std_hu",
                "min_hu", "max_hu", "source", "note", "pos_points", "neg_points", "mask_npy"]

        wb = Workbook()
        ws = wb.active
        ws.title = "ROI"
        head_font = Font(bold=True, color="FFFFFF")
        head_fill = PatternFill("solid", fgColor="4472C4")
        for c, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        num_keys = {"volume_cm3", "voxels", "mean_hu", "std_hu", "min_hu", "max_hu",
                    "pos_points", "neg_points"}

        for i, r in enumerate(rows, start=2):
            for c, k in enumerate(keys, start=1):
                val = r.get(k, "")
                if k in num_keys and isinstance(val, str) and val != "":
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                if k == "voxels" and isinstance(val, float):
                    val = int(val)
                ws.cell(row=i, column=c, value=val)
        for c in range(1, len(headers) + 1):
            letter = get_column_letter(c)
            ws.column_dimensions[letter].width = 15
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["J"].width = 22
        ws.column_dimensions["M"].width = 30
        ws.freeze_panes = "A2"
        try:
            ws.auto_filter.ref = ws.dimensions
        except Exception:
            pass

        from mcp_ssd_vr import config as _cfg
        _cfg.ensure_dirs()
        out = os.path.join(_cfg.record_dir(), f"roi_export_{_t.strftime('%Y%m%d_%H%M%S')}.xlsx")
        try:
            wb.save(out)
        except PermissionError:
            QtWidgets.QMessageBox.warning(self, "导出失败", "目标文件被占用，请关闭后重试。")
            return
        msg = f"已导出 {len(rows)} 条 ROI → {out}"
        self.sam_save_status.setText(msg)
        self._sam_status(f"Excel 导出完成：{len(rows)} 条", "#4e7a2e")
        res = QtWidgets.QMessageBox.question(
            self, "导出完成", msg + "\n是否打开所在文件夹？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes)
        if res == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                import subprocess
                subprocess.Popen(["explorer", "/select,", out])
            except Exception:
                pass

    # ---------------- 推理 ----------------
    def _sam_run(self):
        if self.sam_busy:
            self.sam_pending = True
            return
        vol = self._sam_vol()
        if vol is None:
            return
        if not self.sam_pos_pts:
            return
        worker = self._sam_ensure_worker()
        if worker is None:
            self.sam_busy = False
            self.sam_btn_run.setEnabled(True)
            return
        self.sam_busy = True
        self.sam_btn_run.setEnabled(False)
        self.sam_progress_lb.setText("SAM-Med3D 推理中...")
        QtWidgets.QApplication.processEvents()
        thr = float(self.sam_threshold_spin.value())
        # 负点去重
        neg = list(dict.fromkeys(self.sam_neg_pts))
        pos = list(dict.fromkeys(self.sam_pos_pts))
        self.sam_session.request.emit(np.ascontiguousarray(vol), pos, neg, thr)

    def _sam_on_progress(self, percent, message):
        if hasattr(self, "sam_progress_lb"):
            self.sam_progress_lb.setText(f"[{percent}%] {message}")
        if "加载" in message or "就绪" in message:
            self._sam_status(message, "#4e7a2e")

    def _sam_on_finished(self, mask):
        self.sam_busy = False
        try:  # MCP 桥：通知等待中的客户端（无桥时是 no-op）
            self._mcp_push("roi_done", {
                "voxels": int(mask.sum()) if mask is not None else 0,
                "source": "3d_sam",
                "path": self.path_edit.line_edit().text().strip(),
            })
        except Exception:  # noqa: BLE001
            pass
        self.sam_mask = mask
        self._sam_write_overlay()
        self._sam_refresh_views()
        n = int(mask.sum()) if mask is not None else 0
        self.sam_progress_lb.setText(f"完成：绿色高亮 {n} 体素 (VR 中显示)")
        self.sam_btn_run.setEnabled(bool(self.sam_pos_pts))
        if hasattr(self, "sam_btn_save"):
            self.sam_btn_save.setEnabled(bool(self.sam_mask is not None and n > 0))
        if self.sam_pending:
            self.sam_pending = False
            QtCore.QTimer.singleShot(0, self._sam_run)

    def _sam_on_error(self, message):
        self.sam_busy = False
        self.sam_pending = False
        try:  # MCP 桥：通知等待中的客户端
            self._mcp_push("roi_error", {"error": str(message), "source": "3d_sam"})
        except Exception:  # noqa: BLE001
            pass
        self.sam_progress_lb.setText(f"错误: {message}")
        self.sam_btn_run.setEnabled(bool(self.sam_pos_pts))
        if not self._mcp_mode:
            QtWidgets.QMessageBox.warning(self, "SAM 分割失败", message)

    def _sam_write_overlay(self):
        """把 SAM mask 写入 roi overlay（绿色），在 VR 上高亮。"""
        mask = self.sam_mask
        if mask is None:
            return
        if not self._ensure_roi_volume():
            return
        self._clear_roi_volume()
        if mask.shape != self.roi_array.shape:
            # 尺寸不一致则最近邻重采样到 roi_array 网格
            mask = self._resize_mask_nearest(mask, self.roi_array.shape)
        target = self.roi_array
        target[:] = -1024
        target[mask] = ROI_REPLACEMENT_HU
        vtk_array = numpy_support.numpy_to_vtk(
            num_array=target.ravel(order="C"),
            deep=True,
            array_type=vtk.VTK_SHORT,
        )
        self.roi_image_data.GetPointData().SetScalars(vtk_array)
        self.roi_image_data.Modified()
        if self.roi_producer is not None:
            self.roi_producer.Modified()
        if self.current_roi_mapper is not None:
            self.current_roi_mapper.Modified()
        if self.current_roi_volume is not None:
            self.current_roi_volume.SetVisibility(True)
        self.render_window.Render()

    def _sam_clear_overlay(self):
        if self.current_roi_volume is not None:
            self._clear_roi_volume()
            if getattr(self, "render_window", None) is not None:
                self.render_window.Render()


def main() -> int:
    # --- 标准流编码兜底（Windows Server 等英文系统上必须）----------------
    # --noconsole 打包时 PyInstaller 会把 sys.stdout/stderr 置为 None，
    # 原来的兜底是 open(os.devnull, "w")——**没指定编码**，于是用系统 ANSI
    # 代码页：中文 Windows 是 cp936（能编中文，本机看不出问题），
    # 但英文版 Windows Server 是 cp1252，一 print 中文就抛
    #   UnicodeEncodeError: 'charmap' codec can't encode characters ...
    # 而 build_reader 里有几十条中文 print，且异常会被 load_dicom 的
    # except 抓成 "DICOM 加载或渲染失败"——表现为"渲染直接失败"，
    # 实际只是日志写不出去。
    # 兜底策略：devnull 明确用 utf-8 + errors="replace"（反正输出是被丢掉的）；
    # 真实流只放宽 errors、**不改编码**，避免中文控制台出现乱码。
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(errors="replace")
            except Exception:  # noqa: BLE001
                pass

    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        _qt = _setup_macos_qt_paths()
        print(f"[macOS] QT_PLUGIN_PATH={_qt['plugin_dir']} "
              f"(lib dirs: {len(_qt['lib_dirs'])})", flush=True)
        if not _qt["plugin_dir"]:
            print("[macOS] WARNING: no Qt platform plugin dir found in the bundle; "
                  "the app will likely fail to start (see ssd_vr_viewer_macos.spec).",
                  flush=True)

    parser = argparse.ArgumentParser(
        description="Animal Dicom — 爱宠（犬猫）CT/DR/MRI 离线影像工作站 v1.0.0")
    parser.add_argument("--input", default="", help="DICOM folder path or a single DICOM file")
    # 纯离线阅读器：MCP 桥默认**关闭**。需要被 AI Agent 接管时显式加 --mcp，
    # 或设环境变量 SSD_VR_MCP=1。桥只绑 127.0.0.1，不联网。
    parser.add_argument("--mcp", dest="mcp", action="store_true", default=None,
                        help="启动 MCP 桥（TCP，默认关闭）")
    parser.add_argument("--no-mcp", dest="mcp", action="store_false",
                        help="不启动 MCP 桥（默认行为）")
    parser.add_argument("--mcp-port", type=int,
                        default=int(os.environ.get("SSD_VR_MCP_PORT") or 7799),
                        help="MCP 桥端口（被占时自动向后探测；默认取 SSD_VR_MCP_PORT，否则 7799）")
    args = parser.parse_args()

    if args.mcp is None:
        _env_mcp = (os.environ.get("SSD_VR_MCP") or "").strip().lower()
        args.mcp = _env_mcp in ("1", "true", "on", "yes")

    initial = args.input.strip()

    # Windows 任务栏图标：显式 AppUserModelID，否则任务栏可能显示 python/默认图标
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ssdvr.fusion.viewer")
        except Exception:
            pass

    set_appearance_mode("light")
    # 宠物影像：浅色明快暖橙主题（文件缺失时回退到 PyCt6 内置 orange）
    theme_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "warm_orange.json")
    set_color_theme(theme_path if os.path.exists(theme_path) else "orange")

    app = QtWidgets.QApplication(sys.argv)

    _app_icon = _icon_path()
    if _app_icon:
        app.setWindowIcon(QIcon(_app_icon))

    # ------------------------------------------------------------------
    # 30 天运行期限（强制，无 GUI 开关 / 无环境变量旁路）
    # 校验逻辑见 license_guard.py。这里做两件事：
    #   1) 启动前判定：到期/篡改/改时钟 → 弹窗说明并直接退出；
    #   2) 缺模块也拒绝启动（删掉 license_guard.py 不能绕过）。
    # ------------------------------------------------------------------
    try:
        import license_guard as _trial_guard
    except Exception as _e:  # noqa: BLE001
        if os.environ.get("SSDVR_NO_DIALOG") == "1":
            print("[trial] 完整性检查失败: 缺少 license_guard.py (%s)" % _e, flush=True)
        else:
            QtWidgets.QMessageBox.critical(
                None, "完整性检查失败",
                "缺少运行期限校验模块 license_guard.py，无法启动。\n%s" % _e)
        return 4

    def _trial_notify(title: str, msg: str, parent=None) -> None:
        """期限提示：交互环境弹窗；无人值守（SSDVR_NO_DIALOG=1 / offscreen）只打日志。

        注意：这里只影响"怎么告知"，不影响"拒不拒绝启动"——拒绝逻辑在下面。
        """
        if os.environ.get("SSDVR_NO_DIALOG") == "1":
            print("[trial] %s: %s" % (title, msg.replace("\n", " ")), flush=True)
            return
        QtWidgets.QMessageBox.critical(parent, title, msg)

    _trial_status = _trial_guard.startup()
    if not _trial_status.get("ok"):
        _trial_notify(
            "运行期限已到",
            "%s\n\n首次运行：%s\n到期时间：%s（期限 %s 天）\n\n"
            "请联系提供方获取新的授权版本。" % (
                _trial_status.get("message", ""),
                _trial_status.get("first_run", "-"),
                _trial_status.get("expires_at", "-"),
                _trial_status.get("trial_days", 30)))
        return 3

    # 皮肤优先级：复古拟物 retro.qss > 浅色 light.qss > 深色 dark.qss
    _base = os.path.dirname(os.path.abspath(__file__))
    qss_path = os.path.join(_base, "retro.qss")
    if not os.path.exists(qss_path):
        qss_path = os.path.join(_base, "light.qss")
    if not os.path.exists(qss_path):
        qss_path = os.path.join(_base, "dark.qss")
    with open(qss_path, "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    win = ViewerWindow(initial_input=initial)
    win.show()

    # 状态栏显示剩余期限；后台每 5 分钟复核一次，程序跨过到期时刻也会立即停用。
    _left = _trial_status.get("days_left")
    if _left is not None:
        win.statusBar().showMessage(
            "运行期限剩余 %.1f 天（到期 %s）" % (_left, _trial_status.get("expires_at", "-")))
        if _left <= 3:
            _trial_notify(
                "运行期限即将结束",
                "本程序为期限版本，剩余 %.1f 天（到期 %s）。"
                % (_left, _trial_status.get("expires_at", "-")), parent=win)

    _trial_timer = QtCore.QTimer(win)
    _trial_timer.setInterval(5 * 60 * 1000)

    def _trial_recheck() -> None:
        st = _trial_guard.note_seen()
        if not st.get("ok"):
            _trial_timer.stop()
            win.statusBar().showMessage(st.get("message", ""))
            _trial_notify("运行期限已到",
                          "%s\n\n程序即将关闭。" % st.get("message", ""), parent=win)
            try:
                win.close()
            except Exception:  # noqa: BLE001
                pass
            QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)
            return
        win.statusBar().showMessage(
            "运行期限剩余 %.1f 天（到期 %s）" % (st.get("days_left") or 0.0,
                                          st.get("expires_at", "-")), 4000)

    _trial_timer.timeout.connect(_trial_recheck)
    _trial_timer.start()
    win._trial_timer = _trial_timer
    win._trial_status = _trial_status

    if args.mcp:
        win._mcp_mode = True
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from mcp_ssd_vr.gui_bridge import start_bridge
            _bridge = start_bridge(win, args.mcp_port)
            # 打印**实际**端口：请求端口被占用时桥会自动向后探测，
            # 客户端可用发现文件（%LOCALAPPDATA%\SSD_VR_MCP\bridge.json）拿到它。
            print(f"[MCP bridge] listening on 127.0.0.1:{_bridge.port} "
                  f"(pid={os.getpid()}, frozen={getattr(sys, 'frozen', False)})", flush=True)
        except Exception as _e:
            print(f"[MCP bridge] failed to start: {_e}", flush=True)

    if os.path.isdir(initial) or os.path.isfile(initial):
        QtCore.QTimer.singleShot(400, win.load_dicom)

    return app.exec()


if __name__ == "__main__":
    # PyInstaller 冻结版必需，且必须是 __main__ 里的第一条语句。
    #
    # nnU-Net / TotalSegmentator 会用 multiprocessing.get_context("spawn").Pool
    # 导出分割结果（见 totalsegmentator/nnunet_runtime_patches.py:316）。
    # 不调用 freeze_support() 时，spawn 的 worker 会重新执行本 EXE 的入口，
    # 也就是把整个 Qt GUI 再启动一遍，而不是去跑 worker 目标函数，
    # 于是 Pool 永远拿不到结果 → 卡在 10%、GPU 全程 0%。
    #
    # 源码运行时这里是无害的 no-op（无 sys.frozen 时 freeze_support 直接返回），
    # 所以"源码正常、EXE 卡死"正是这个差异造成的。
    import multiprocessing
    multiprocessing.freeze_support()

    sys.exit(main())
