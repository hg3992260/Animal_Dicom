from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from typing import Tuple, Dict, Optional


def _ball_kernel(radius: float, spacing: Tuple[float, float, float]) -> np.ndarray:
    r_vox = tuple(max(1, int(radius / s)) for s in spacing)
    grid = np.ogrid[tuple(slice(-r, r + 1) for r in r_vox)]
    dist_sq = sum(((g * s) ** 2 for g, s in zip(grid, spacing)))
    return (dist_sq <= radius ** 2).astype(np.uint8)


def detect_tissues(
    nda: np.ndarray,
    spacing: Tuple[float, float, float],
    bone_mask: Optional[np.ndarray] = None,
) -> Dict[str, np.ndarray]:

    working = nda.copy()
    if bone_mask is not None:
        working[bone_mask > 0] = -1000

    result: Dict[str, np.ndarray] = {}

    lung_mask = (working > -1000) & (working < -200)
    strel_lung = _ball_kernel(3.0, spacing)
    lung_mask = ndi.binary_opening(lung_mask, structure=strel_lung, iterations=1)
    lung_labeled, n_lung = ndi.label(lung_mask)
    if n_lung > 0:
        lung_sizes = ndi.sum_labels(lung_mask.astype(np.int64), lung_labeled, range(1, n_lung + 1))
        lung_sizes = np.array([0, *lung_sizes], dtype=np.int64)
        top_lungs = np.argsort(lung_sizes)[::-1][1:min(n_lung + 1, 3)]
        lung_mask = np.isin(lung_labeled, top_lungs)
    result["lung"] = lung_mask.astype(np.uint8)

    fat_mask = (working > -200) & (working < -30)
    fat_mask[lung_mask] = 0
    strel_fat = _ball_kernel(1.0, spacing)
    fat_mask = ndi.binary_opening(fat_mask, structure=strel_fat, iterations=1)
    result["fat"] = fat_mask.astype(np.uint8)

    soft_mask = (working > 0) & (working < 100)
    soft_mask[lung_mask] = 0
    soft_mask[fat_mask] = 0
    result["soft_tissue"] = soft_mask.astype(np.uint8)

    return result
