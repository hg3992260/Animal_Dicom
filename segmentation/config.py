import os
import sys
import tempfile


def _base_dir() -> str:
    """仓库根目录；PyInstaller 冻结时用 EXE 所在目录（可写）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _base_dir()
# 权重根目录：优先环境变量 SSD_VR_WEIGHTS_DIR，其次 BASE_DIR
_WEIGHTS_ROOT = os.environ.get("SSD_VR_WEIGHTS_DIR") or BASE_DIR
SAM_MED3D_DIR = os.path.join(_WEIGHTS_ROOT, "frame", "SAM-Med3D-main")
SAM_CKPT = os.path.join(SAM_MED3D_DIR, "ckpt", "sam_med3d_turbo.pth")
TEMP_DIR = (os.path.join(tempfile.gettempdir(), "ssd_vr_segmentation")
            if getattr(sys, "frozen", False)
            else os.path.join(BASE_DIR, "temp", "segmentation"))
os.makedirs(TEMP_DIR, exist_ok=True)

DEFAULT_HU_WINDOW = (100, 700)
DEFAULT_TARGET_SPACING = (0.6, 0.6, 0.6)
DEFAULT_MIN_COMPONENT_SIZE = 500
DEFAULT_CLOSING_KERNEL = 3
DEFAULT_NUM_CLICKS = 2
DEFAULT_THRESHOLD = 0.3
