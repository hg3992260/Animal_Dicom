"""路径 / 端口 / 解释器配置。

环境变量（可选覆盖）:
  SSD_VR_PYTHON   GUI 解释器（必须含 vtk/PySide6/SimpleITK/totalsegmentator）
  SSD_VR_EXE      冻结版 EXE 路径（或缺省目录下的 SSD_VR_Fusion_Viewer.exe）
  SSD_VR_LAUNCH   启动目标: auto(默认) | exe | source
  SSD_VR_REPO     仓库根目录
  SSD_VR_MCP_PORT 桥端口（默认 7799，被占时自动向后探测 7799-7899）
"""
from __future__ import annotations

import os
import sys

# 仓库根目录 = 本包上一级目录
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))

EXE_NAME = "SSD_VR_Fusion_Viewer.exe"
# 按优先级排列的 EXE 位置（相对仓库根）；onedir 版优先——onefile 每次启动都要解包。
EXE_SUBDIRS = [
    "SSD_VR_Fusion_Viewer_Full_Win",
    os.path.join("dist", "SSD_VR_Fusion_Viewer"),
    "SSD_VR_Fusion_Viewer",
    "SSD_VR_Fusion_Viewer_Win",
]


def _default_python() -> str:
    """返回默认 GUI 解释器。优先使用当前解释器（MCP server 与 GUI 同环境）。"""
    if os.environ.get("SSD_VR_PYTHON"):
        return os.environ["SSD_VR_PYTHON"]
    # 已知本机 mar 环境
    candidates = [
        os.path.join("D:", os.sep, "python", "envs", "mar", "python.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return sys.executable


def repo_root() -> str:
    return os.environ.get("SSD_VR_REPO") or REPO_ROOT


def viewer_script() -> str:
    return os.path.join(repo_root(), "ssd_vr_viewer.py")


def gui_python() -> str:
    return _default_python()


def gui_exe() -> str:
    """冻结版 EXE 路径；未找到返回 ""。

    解析顺序:
      1. SSD_VR_EXE（可为 exe 文件，也可为包含 exe 的目录）
      2. 仓库根下 EXE_SUBDIRS 中的 SSD_VR_Fusion_Viewer.exe
      3. 仓库根 / 本包目录下的 SSD_VR_Fusion_Viewer.exe

    只看仓库与本包（不看 cwd）：结果稳定、可复现，也不受 MCP 客户端
    工作目录影响。找不到时返回 ""，调用方回退到源码启动（见 launch_preference()）。
    """
    env = (os.environ.get("SSD_VR_EXE") or "").strip().strip('"')
    if env:
        if os.path.isdir(env):
            cand = os.path.join(env, EXE_NAME)
            return cand if os.path.isfile(cand) else ""
        return env if os.path.isfile(env) else ""

    roots = [repo_root(), PACKAGE_DIR]
    seen = set()
    cands = []
    for root in roots:
        for sub in EXE_SUBDIRS:
            cands.append(os.path.join(root, sub, EXE_NAME))
        cands.append(os.path.join(root, EXE_NAME))
    for cand in cands:
        norm = os.path.normcase(os.path.abspath(cand))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            return os.path.abspath(cand)
    return ""


def launch_preference() -> str:
    """启动目标偏好: auto | exe | source。"""
    val = (os.environ.get("SSD_VR_LAUNCH") or "auto").strip().lower()
    return val if val in ("auto", "exe", "source") else "auto"


def resolve_launch_target() -> tuple:
    """返回 (target, path) —— target ∈ {"exe","source"}，path 为要启动的文件。

    auto: 有冻结版 EXE 就用 EXE（贴近交付形态），否则回退源码。
    exe : 强制 EXE，找不到则返回 ("exe", "") 让调用方报错。
    """
    pref = launch_preference()
    exe = gui_exe()
    if pref == "source":
        return "source", viewer_script()
    if pref == "exe":
        return "exe", exe
    if exe:
        return "exe", exe
    return "source", viewer_script()


def default_port() -> int:
    try:
        return int(os.environ.get("SSD_VR_MCP_PORT", "7799"))
    except ValueError:
        return 7799


def record_dir() -> str:
    """事件/工具调用记录目录（SQLite + JSONL）。"""
    return os.path.join(repo_root(), "mcp_records")


def shots_dir() -> str:
    return os.path.join(record_dir(), "shots")


def gui_log_path() -> str:
    return os.path.join(record_dir(), "gui.log")


def db_path() -> str:
    return os.path.join(record_dir(), "ssd_vr.db")


def ensure_dirs() -> None:
    os.makedirs(record_dir(), exist_ok=True)
    os.makedirs(shots_dir(), exist_ok=True)
