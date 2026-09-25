from __future__ import annotations

import os
import sys
import traceback

import numpy as np
import SimpleITK as sitk
from PySide6 import QtCore

from .config import (
    DEFAULT_CLOSING_KERNEL,
    DEFAULT_HU_WINDOW,
    DEFAULT_MIN_COMPONENT_SIZE,
    DEFAULT_NUM_CLICKS,
    DEFAULT_TARGET_SPACING,
    DEFAULT_THRESHOLD,
    SAM_MED3D_DIR,
    TEMP_DIR,
)
from .postprocessor import Postprocessor
from .preprocessor import Preprocessor
from .sam_adapter import SAMMed3DAdapter


class SegmentationPipeline(QtCore.QThread):
    progress_signal = QtCore.Signal(int, str)
    finished_signal = QtCore.Signal(dict)
    error_signal = QtCore.Signal(str)

    def __init__(
        self,
        sitk_image: sitk.Image,
        hu_window=DEFAULT_HU_WINDOW,
        target_spacing=DEFAULT_TARGET_SPACING,
        use_sam: bool = True,
        use_nnunet: bool = False,
        num_clicks: int = DEFAULT_NUM_CLICKS,
        threshold: float = DEFAULT_THRESHOLD,
        min_component_size: int = DEFAULT_MIN_COMPONENT_SIZE,
        closing_kernel: int = DEFAULT_CLOSING_KERNEL,
    ):
        super().__init__()
        self.sitk_image = sitk_image
        self.hu_window = hu_window
        self.target_spacing = target_spacing
        self.use_sam = use_sam
        self.use_nnunet = use_nnunet
        self.num_clicks = num_clicks
        self.threshold = threshold
        self.min_component_size = min_component_size
        self.closing_kernel = closing_kernel
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress_signal.emit(0, "初始化预处理管线...")
            if self._cancelled:
                return
            preprocessor = Preprocessor(
                hu_window=self.hu_window,
                target_spacing=self.target_spacing,
            )

            self.progress_signal.emit(10, "预处理: HU窗口 + 重采样 + 归一化...")
            processed = preprocessor.run(self.sitk_image)
            nifti_path = preprocessor.to_nifti_temp(processed, prefix="cta_")

            if self._cancelled:
                preprocessor.cleanup_temp(nifti_path)
                return

            if self.use_sam:
                self.progress_signal.emit(20, "SAM-Med3D: 加载模型...")

                if SAM_MED3D_DIR not in sys.path:
                    sys.path.insert(0, SAM_MED3D_DIR)
                os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

                sam = SAMMed3DAdapter()
                sam.load_model()

                self.progress_signal.emit(30, f"SAM-Med3D: 推理中 ({self.num_clicks} click(s), 约2-5分钟)...")
                if self._cancelled:
                    preprocessor.cleanup_temp(nifti_path)
                    return

                vessel_prob = sam.predict_vessel_prior(
                    nifti_path,
                    num_clicks=self.num_clicks,
                )
                binary_mask = (vessel_prob > self.threshold).astype(np.uint8)

                import torch
                torch.cuda.empty_cache()
            else:
                vessel_prob = None
                arr = sitk.GetArrayFromImage(processed)
                binary_mask = (arr > 0.3).astype(np.uint8)

            self.progress_signal.emit(75, "后处理: 连通域过滤 + 形态学闭运算 + 填充孔洞...")
            if self._cancelled:
                preprocessor.cleanup_temp(nifti_path)
                return

            postproc = Postprocessor(
                min_component_size=self.min_component_size,
                closing_kernel=self.closing_kernel,
            )
            cleaned_mask = postproc.run(binary_mask)

            spacing = processed.GetSpacing()
            origin = processed.GetOrigin()
            stats = postproc.compute_stats(cleaned_mask, spacing)

            preprocessor.cleanup_temp(nifti_path)

            self.progress_signal.emit(100, "分割完成")
            self.finished_signal.emit({
                "mask": cleaned_mask,
                "probability": vessel_prob,
                "spacing": spacing,
                "origin": origin,
                "stats": stats,
            })

        except Exception as e:
            traceback.print_exc()
            self.error_signal.emit(f"{type(e).__name__}: {e}")
