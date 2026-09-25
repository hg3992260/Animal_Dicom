from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional

import numpy as np


@dataclass
class ROIBlock:
    region: str
    category: str
    mask: np.ndarray
    bbox_z: Tuple[int, int]
    bbox_y: Tuple[int, int]
    bbox_x: Tuple[int, int]
    z_range_mm: Tuple[float, float]
    volume_cm3: float
    voxel_count: int
    label_id: int = 0
    anatomical_name: str = ""


@dataclass
class ROIRegionResult:
    region: str
    bones: List[ROIBlock] = field(default_factory=list)
    tissues: List[ROIBlock] = field(default_factory=list)
    vessels: List[ROIBlock] = field(default_factory=list)


ANATOMICAL_REGIONS: List[Tuple[int, float, float, str]] = [
    (0,   484.0,  640.0,  "颅骨顶 (Skull Vault)"),
    (1,   640.0,  796.0,  "颅底/颈部 (Skull Base/Neck)"),
    (2,   796.0,  952.0,  "上肢/胸廓 (Upper Limbs/Thorax)"),
    (3,   952.0, 1107.0,  "上腹部 (Upper Abdomen)"),
    (4,  1108.0, 1264.0,  "腹部 (Abdomen)"),
    (5,  1264.0, 1420.0,  "骨盆 (Pelvis)"),
    (6,  1420.0, 1576.0,  "股骨上段 (Upper Femur)"),
    (7,  1576.0, 1732.0,  "股骨下段 (Lower Femur)"),
    (8,  1732.0, 1888.0,  "膝/胫骨平台 (Knee/Tibial Plateau)"),
    (9,  1888.0, 2044.0,  "胫骨中段 (Mid Tibia)"),
    (10, 2044.0, 2200.0,  "足踝近端 (Foot/Ankle Proximal)"),
    (11, 2200.0, 2356.0,  "足踝中段 (Foot/Ankle Mid)"),
    (12, 2356.0, 2418.0,  "足踝远端 (Foot/Ankle Distal)"),
]


ROI_CATEGORIES = {
    "bone":   {"label": "骨骼",   "color": (0.90, 0.88, 0.80)},
    "vessel": {"label": "血管",   "color": (0.80, 0.15, 0.15)},
    "tissue": {"label": "软组织", "color": (0.15, 0.60, 0.35)},
}
