from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from typing import Tuple


BONE_HU_LOW = 300
BONE_HU_HIGH = 3000
MIN_BONE_VOLUME_MM3 = 8.0


def _ball_kernel(radius: float, spacing: Tuple[float, float, float]) -> np.ndarray:
    r_vox = tuple(max(1, int(radius / s)) for s in spacing)
    grid = np.ogrid[tuple(slice(-r, r + 1) for r in r_vox)]
    dist_sq = sum(((g * s) ** 2 for g, s in zip(grid, spacing)))
    return (dist_sq <= radius ** 2).astype(np.uint8)


def detect_bones(
    nda: np.ndarray,
    spacing: Tuple[float, float, float],
    min_volume_mm3: float = MIN_BONE_VOLUME_MM3,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mask = (nda > BONE_HU_LOW) & (nda < BONE_HU_HIGH)

    strel_close = _ball_kernel(1.5, spacing)
    mask = ndi.binary_closing(mask, structure=strel_close, iterations=1)

    strel_open = _ball_kernel(0.8, spacing)
    mask = ndi.binary_opening(mask, structure=strel_open, iterations=1)

    labeled, n_features = ndi.label(mask)
    if n_features == 0:
        return mask.astype(np.uint8), labeled, np.zeros(1, dtype=np.int64)

    sp_mm3 = spacing[0] * spacing[1] * spacing[2]
    min_vox = max(1, int(min_volume_mm3 / sp_mm3))

    sizes = ndi.sum_labels(mask.astype(np.int64), labeled, range(1, n_features + 1))
    sizes = np.array([0, *sizes], dtype=np.int64)

    keep_labels = np.where(sizes >= min_vox)[0]
    clean_mask = np.isin(labeled, keep_labels)

    return clean_mask.astype(np.uint8), labeled, sizes
