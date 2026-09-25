from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from typing import Tuple, Optional


VESSEL_HU_LOW = 80
VESSEL_HU_HIGH = 500
MIN_VESSEL_VOLUME_MM3 = 5.0


def _ball_kernel(mmx: float, vx: float) -> np.ndarray:
    r = max(1, int(mmx / vx))
    z = np.arange(-r, r + 1)
    g = np.meshgrid(z, z, z, indexing="ij")
    d = np.sqrt(g[0]**2 + g[1]**2 + g[2]**2) * vx
    return (d <= mmx).astype(np.uint8)


def detect_vessels(
    nda: np.ndarray,
    spacing: Tuple[float, float, float],
    bone_mask: Optional[np.ndarray] = None,
    vesselness_threshold: float = 0.0,
    min_volume_mm3: float = MIN_VESSEL_VOLUME_MM3,
    progress_cb=None,
) -> np.ndarray:

    if progress_cb:
        progress_cb(10, f"血管HU阈值筛选: {VESSEL_HU_LOW}-{VESSEL_HU_HIGH} HU...")

    mask = (nda > VESSEL_HU_LOW) & (nda < VESSEL_HU_HIGH)

    if bone_mask is not None:
        mask[bone_mask > 0] = 0

    if progress_cb:
        n_vox = int(mask.sum())
        progress_cb(30, f"阈值初筛: {n_vox:,} 体素候选")

    sp_vox = float(np.mean(spacing))
    strel = _ball_kernel(1.0, sp_vox)
    mask = ndi.binary_closing(mask, structure=strel, iterations=2)
    mask = ndi.binary_fill_holes(mask)
    mask = ndi.binary_opening(mask, structure=strel, iterations=1)

    if progress_cb:
        progress_cb(50, "形态学处理完成，连通域过滤中...")

    labeled, n_features = ndi.label(mask)
    if n_features == 0:
        return mask.astype(np.uint8)

    sp_mm3 = spacing[0] * spacing[1] * spacing[2]
    min_vox = max(1, int(min_volume_mm3 / sp_mm3))

    sizes = ndi.sum_labels(mask.astype(np.int64), labeled, range(1, n_features + 1))
    sizes = np.array([0, *sizes], dtype=np.int64)
    keep_labels = np.where(sizes >= min_vox)[0]
    clean = np.isin(labeled, keep_labels)

    if progress_cb:
        progress_cb(70, f"连通域过滤完成: {len(keep_labels)} 个血管区域")

    return clean.astype(np.uint8)
