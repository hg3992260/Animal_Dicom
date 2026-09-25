from __future__ import annotations

"""
SynthSeg (DIPY PyTorch 版) 脑部分割检测器
─────────────────────────────────────────
调用 dipy.nn.torch.synthseg 对任意对比度/分辨率的脑部扫描（MRI 或 CT）做 32 结构分割。

- 权重由 DIPY 自动下载并缓存到 ~/.dipy/synthseg/synthseg_model_weights.pth
  (约 3.2GB，官方 pip install dipy 即会自动获取，无需手工 Dropbox)。
- 预测始终在内部重采样到 1mm 各向同性，再回采样到原始体素网格，
  因此返回的 label_map 与 sitk.GetArrayFromImage 的 (z,y,x) 顺序一一对应。
- 输出 label 值遵循 FreeSurfer 分类（与 FreeSurfer/SynthSeg 一致）。

依赖: pip install dipy  (另需 torch / nibabel / SimpleITK，均已存在)
注意: 本文件刻意 延迟导入 dipy/torch，非 DIPY 环境也能安全 import。
"""

import os
import shutil
import tempfile
from typing import Dict, Optional, Tuple

import numpy as np
import SimpleITK as sitk

_SYNTHSEG_INSTANCE = None


def _get_model(device: str = "cuda"):
    """懒加载 + 缓存 DIPY SynthSeg 模型（首次调用会自动下载权重）。"""
    global _SYNTHSEG_INSTANCE
    if _SYNTHSEG_INSTANCE is not None:
        return _SYNTHSEG_INSTANCE

    try:
        import torch
        from dipy.nn.torch.synthseg import SynthSeg
    except Exception as e:  # pragma: no cover - 环境缺依赖时触发
        raise RuntimeError(
            "SynthSeg(DIPY) 不可用：需要 `pip install dipy` 与 `torch`。"
            f"（错误: {type(e).__name__}: {e}）"
        ) from e

    use_cuda = bool(device == "cuda" and torch.cuda.is_available())
    seg = SynthSeg(verbose=False, use_cuda=use_cuda)
    seg.fetch_default_weights()
    _SYNTHSEG_INSTANCE = seg
    return seg


def reset_model_cache():
    """清除模型缓存（如切换 device 或需要释放显存时调用）。"""
    global _SYNTHSEG_INSTANCE
    _SYNTHSEG_INSTANCE = None


def _sitk_to_nib(sitk_image: sitk.Image) -> Tuple[np.ndarray, np.ndarray, str]:
    """SimpleITK -> nibabel (x,y,z) 数据 + RAS affine。

    通过 "SimpleITK 写临时 NIfTI -> nibabel 读回" 这一业界通道保证 affine 方向正确，
    避免手敲 ITK(RAS/LPS) 换算出错。返回 (data_xyz, affine, tmpdir)。
    """
    import nibabel as nib

    tmpdir = tempfile.mkdtemp(prefix="synthseg_")
    in_nii = os.path.join(tmpdir, "in.nii.gz")
    sitk.WriteImage(sitk_image, in_nii)

    nib_img = nib.load(in_nii)
    data = np.asarray(nib_img.get_fdata(dtype=np.float32))  # (x,y,z)
    affine = np.asarray(nib_img.affine, dtype=np.float64)   # RAS 4x4
    return data, affine, tmpdir


def detect_synthseg(
    sitk_image: sitk.Image,
    device: str = "cuda",
    progress_cb=None,
    ct: Optional[bool] = None,
) -> Tuple[np.ndarray, Dict[int, str]]:
    """对单幅脑部扫描做 SynthSeg 分割。

    Parameters
    ----------
    sitk_image : sitk.Image
        输入扫描（MRI 任意对比度，或脑部 CT）。
    device : str
        "cuda"（默认，有 GPU 时自动用）或 "cpu"。
    progress_cb : Callable[[int, str], None], optional
        进度回调 (percent, message)。
    ct : Optional[bool]
        是否按 CT(Hounsfield) 处理：为 True 时把强度 clip 到 [0,80]。
        默认 None = 自动：图像最大强度 > 800 判定为 CT(HU)，否则按 MRI 处理。

    Returns
    -------
    label_map : np.ndarray, (int16, shape == sitk.GetArrayFromImage 顺序 (z,y,x))
        FreeSurfer label id 的体素图（0=背景）。
    label_dict : Dict[int, str]
        label id -> 结构英文名（FreeSurfer 命名）。
    """
    if progress_cb:
        progress_cb(3, "SynthSeg: 几何转换 / 权重加载...")

    data, affine, tmpdir = _sitk_to_nib(sitk_image)

    if ct is None:
        ct = bool(np.asarray(data).max() > 800.0)
    if ct:
        data = np.clip(data, 0.0, 80.0)

    seg = _get_model(device=device)

    if progress_cb:
        progress_cb(20, "SynthSeg: 推理中 (GPU, 约15-30s)...")

    labels_xyz, label_dict, _masks = seg.predict(data, affine)

    # nibabel (x,y,z) -> sitk (z,y,x) 顺序，与 ROI 管线其它分割一致
    label_map = np.asarray(labels_xyz, dtype=np.int16).transpose(2, 1, 0)

    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    if progress_cb:
        nz = int(np.count_nonzero(label_map))
        progress_cb(90, f"SynthSeg 完成: {nz} 非背景体素")
    return label_map, label_dict


def synthseg_label_names() -> Dict[int, str]:
    """返回 DIPY 模块内置的 label id -> 结构名映射（不加载权重）。"""
    from dipy.nn.torch.synthseg import SynthSeg

    return dict(SynthSeg(verbose=False).label_dict)