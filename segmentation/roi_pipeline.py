from __future__ import annotations

import traceback
from typing import List, Optional

import numpy as np
import SimpleITK as sitk
import vtk
from PySide6 import QtCore
from vtkmodules.util import numpy_support

from .roi_types import (
    ROIBlock,
    ROIRegionResult,
)
from .roi_label_map import TOTALSEG_TOTAL, TOTALSEG_TOTAL_MR, SYNTHSEG_LABELS, TS_BRAIN_STRUCTURES
from .detectors.semantic_detector import detect_semantic
from .detectors.synthseg_detector import detect_synthseg


class ROIPipeline(QtCore.QThread):
    progress_signal = QtCore.Signal(int, str)
    finished_signal = QtCore.Signal(list, np.ndarray)
    error_signal = QtCore.Signal(str)

    def __init__(
        self,
        image_data: vtk.vtkImageData,
        original_sitk_image: sitk.Image,
        task: str = "total",
    ):
        super().__init__()
        self.image_data = image_data
        self.sitk_image = original_sitk_image
        self.task = task
        self._cancelled = False

        self.spacing = tuple(float(v) for v in original_sitk_image.GetSpacing()[:3])
        self.origin_z = float(original_sitk_image.GetOrigin()[2])

    def cancel(self):
        self._cancelled = True

    def _emit_progress(self, pct: int, msg: str):
        if not self._cancelled:
            self.progress_signal.emit(pct, msg)

    def _bbox_from_mask(self, mask: np.ndarray, z_offset: int = 0) -> tuple:
        coords = np.argwhere(mask)
        if coords.size == 0:
            return (0, 0), (0, 0), (0, 0)
        z_min, y_min, x_min = coords.min(axis=0)
        z_max, y_max, x_max = coords.max(axis=0)
        return (
            (int(z_min) + z_offset, int(z_max) + z_offset + 1),
            (int(y_min), int(y_max) + 1),
            (int(x_min), int(x_max) + 1),
        )

    def _build_block(
        self, region: str, category: str, mask: np.ndarray,
        z_offset: int, spacing: tuple,
        label_id: int = 0, anat_name: str = "",
    ) -> Optional[ROIBlock]:
        vox_count = int(mask.sum())
        if vox_count == 0:
            return None
        sp_mm3 = spacing[0] * spacing[1] * spacing[2]
        volume_cm3 = vox_count * sp_mm3 / 1000.0
        bbox_z, bbox_y, bbox_x = self._bbox_from_mask(mask, z_offset)
        local_z0 = max(0, bbox_z[0] - z_offset)
        local_z1 = max(local_z0 + 1, bbox_z[1] - z_offset)
        cropped_mask = mask[
            local_z0:local_z1,
            bbox_y[0]:bbox_y[1],
            bbox_x[0]:bbox_x[1],
        ].copy()
        z_range_mm = (
            self.origin_z + bbox_z[0] * spacing[2],
            self.origin_z + bbox_z[1] * spacing[2],
        )
        # #region debug-point B:block-summary
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
            'hypothesisId': 'B',
            'location': 'roi_pipeline.py:_build_block',
            'msg': '[DEBUG] roi block extracted',
            'data': {
                'label_id': int(label_id),
                'name': anat_name,
                'category': category,
                'bbox_z': tuple(int(v) for v in bbox_z),
                'bbox_y': tuple(int(v) for v in bbox_y),
                'bbox_x': tuple(int(v) for v in bbox_x),
                'mask_shape': tuple(int(v) for v in cropped_mask.shape),
                'voxel_count': int(vox_count),
                'volume_cm3': float(volume_cm3),
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
        return ROIBlock(
            region=region,
            category=category,
            mask=cropped_mask,
            bbox_z=bbox_z,
            bbox_y=bbox_y,
            bbox_x=bbox_x,
            z_range_mm=z_range_mm,
            volume_cm3=volume_cm3,
            voxel_count=vox_count,
            label_id=label_id,
            anatomical_name=anat_name,
        )

    def run(self):
        try:
            if self.task == "temporal_lobe":
                label_map, label_def = self._run_temporal_lobe()
            elif self.task == "synthseg":
                label_map, label_def = self._run_synthseg()
            elif self.task == "brain_structures":
                label_map, label_def = self._run_brain_structures_ts()
            else:
                label_map, label_def = self._run_totalseg()
            if self._cancelled:
                return

            self._emit_progress(85, "从全局 label_map 提取结构...")
            region_results = self._build_region_results(label_map, label_def)
            if self._cancelled:
                return

            n_bone = sum(len(r.bones) for r in region_results)
            n_vessel = sum(len(r.vessels) for r in region_results)
            n_tissue = sum(len(r.tissues) for r in region_results)
            self._emit_progress(100, f"完成: {n_bone}骨/{n_vessel}血管/{n_tissue}组织")
            self.finished_signal.emit(region_results, label_map)

        except Exception as e:
            traceback.print_exc()
            self.error_signal.emit(f"{type(e).__name__}: {e}")

    def _run_synthseg(self):
        """SynthSeg(DIPY) 脑部结构分割。

        把 detect_synthseg 内部的 0~90% 进度缩放到 0~72%，
        让后续 85~100% 的结构提取/收尾进度保持单调递增。

        修复: 直接对整头扫描(含颈部/颅底外组织)运行 SynthSeg 时,
        因 SynthSeg 内部把输入重采样到 1mm 后缩放至 192³,
        脑区被颈部大范围挤压, 导致仅覆盖部分脑组织 (约 z 底部 1/4)。
        这里先用 HU 阈值 + 形态学定位脑实质范围, 裁剪出脑部包围盒
        后再送入 SynthSeg, 并将标签回填到原体积。
        """
        self._emit_progress(0, "SynthSeg (DIPY): 脑部结构语义分割...")

        def _scaled(pct, msg):
            self._emit_progress(min(72, int(pct * 0.8)), msg)

        sitk_img = self.sitk_image
        arr = sitk.GetArrayFromImage(sitk_img).astype(np.float32)  # (z,y,x)

        # 1) 脑部粗定位 (1024³ 全分辨率):
        #    CT 脑实质 ≈ HU 20~120, 且位于扫描 z 方向上部 ~70%,
        #    形态学开闭后取最大连通域 = 脑实质+部分软组织
        from scipy import ndimage
        nz = arr.shape[0]
        brain_guess = (arr > 20) & (arr < 120)
        brain_guess = brain_guess & (np.arange(nz)[:, None, None] > nz * 0.30)
        brain_guess = ndimage.binary_closing(brain_guess, iterations=3)
        brain_guess = ndimage.binary_opening(brain_guess, iterations=2)
        lab, n = ndimage.label(brain_guess)
        if n > 0:
            sizes = ndimage.sum(brain_guess, lab, range(1, n + 1))
            top = np.argmax(sizes) + 1
            brain_guess = lab == top
        else:
            brain_guess = np.zeros_like(brain_guess)

        coords = np.argwhere(brain_guess)
        if coords.size == 0:
            # 阈值定位失败 -> 退化为全图(不裁剪)
            self._emit_progress(5, "SynthSeg: 脑部定位失败, 使用全图")
            label_map, _ = detect_synthseg(sitk_img, progress_cb=_scaled)
            return label_map, SYNTHSEG_LABELS

        pad = 20  # voxel (~11mm) 给 SynthSeg 上下文
        z0, z1 = max(0, coords[:, 0].min() - pad), min(arr.shape[0], coords[:, 0].max() + pad + 1)
        y0, y1 = max(0, coords[:, 1].min() - pad), min(arr.shape[1], coords[:, 1].max() + pad + 1)
        x0, x1 = max(0, coords[:, 2].min() - pad), min(arr.shape[2], coords[:, 2].max() + pad + 1)

        self._emit_progress(5, f"SynthSeg: 脑部包围盒 z[{z0},{z1}) y[{y0},{y1}) x[{x0},{x1})")

        crop_arr = arr[z0:z1, y0:y1, x0:x1]
        crop_img = sitk.GetImageFromArray(crop_arr.astype(np.int16))
        sp = sitk_img.GetSpacing()
        crop_img.SetSpacing(sp)
        crop_img.SetDirection(sitk_img.GetDirection())
        orig = sitk_img.GetOrigin()
        crop_img.SetOrigin((orig[0] + x0 * sp[0], orig[1] + y0 * sp[1], orig[2] + z0 * sp[2]))

        crop_labels, _ = detect_synthseg(crop_img, progress_cb=_scaled)

        # 回填到原体积
        label_map = np.zeros(arr.shape, dtype=np.int16)
        label_map[z0:z1, y0:y1, x0:x1] = crop_labels
        return label_map, SYNTHSEG_LABELS

    def _run_temporal_lobe(self):
        """颞叶分割 (非官方): 先跑 SynthSeg 拿左右皮质, 再用解剖约束提取颞叶。

        解剖约束 (与官方 SynthSeg 输出的皮质一致):
          - z 方向: 侧裂下方 (脑 z 下 50%)
          - x 方向: 各侧皮质的外侧半
          - y 方向: 前后 12%~75% (排除枕极/额极)
        返回 label_map (含 201=左颞叶, 202=右颞叶 临时标签) 与 label_def。
        """
        self._emit_progress(0, "颞叶 (非官方): 先运行 SynthSeg 脑部分割...")

        def _scaled(pct, msg):
            self._emit_progress(min(60, int(pct * 0.6)), msg)

        # 复用 SynthSeg 的裁剪+推理逻辑
        sitk_img = self.sitk_image
        arr = sitk.GetArrayFromImage(sitk_img).astype(np.float32)
        from scipy import ndimage

        nz = arr.shape[0]
        brain_guess = (arr > 20) & (arr < 120)
        brain_guess = brain_guess & (np.arange(nz)[:, None, None] > nz * 0.30)
        brain_guess = ndimage.binary_closing(brain_guess, iterations=3)
        brain_guess = ndimage.binary_opening(brain_guess, iterations=2)
        lab, n = ndimage.label(brain_guess)
        if n > 0:
            sizes = ndimage.sum(brain_guess, lab, range(1, n + 1))
            brain_guess = lab == (np.argmax(sizes) + 1)
        coords = np.argwhere(brain_guess)
        if coords.size == 0:
            raise RuntimeError("脑部定位失败, 无法提取颞叶")

        pad = 20
        z0, z1 = max(0, coords[:, 0].min() - pad), min(arr.shape[0], coords[:, 0].max() + pad + 1)
        y0, y1 = max(0, coords[:, 1].min() - pad), min(arr.shape[1], coords[:, 1].max() + pad + 1)
        x0, x1 = max(0, coords[:, 2].min() - pad), min(arr.shape[2], coords[:, 2].max() + pad + 1)
        self._emit_progress(5, f"SynthSeg: 脑部包围盒 z[{z0},{z1}) y[{y0},{y1}) x[{x0},{x1})")

        crop_arr = arr[z0:z1, y0:y1, x0:x1]
        crop_img = sitk.GetImageFromArray(crop_arr.astype(np.int16))
        sp = sitk_img.GetSpacing()
        crop_img.SetSpacing(sp)
        crop_img.SetDirection(sitk_img.GetDirection())
        orig = sitk_img.GetOrigin()
        crop_img.SetOrigin((orig[0] + x0 * sp[0], orig[1] + y0 * sp[1], orig[2] + z0 * sp[2]))

        crop_labels, _ = detect_synthseg(crop_img, progress_cb=_scaled)

        label_map = np.zeros(arr.shape, dtype=np.int16)
        label_map[z0:z1, y0:y1, x0:x1] = crop_labels

        # ── 解剖提取颞叶 ──
        self._emit_progress(70, "颞叶: 解剖约束提取 (非官方)...")
        Lc = label_map == 3
        Rc = label_map == 42
        brain_mask = np.isin(label_map, [2, 3, 7, 8, 41, 42, 16])
        bb = np.argwhere(brain_mask)
        bz0, bz1 = bb[:, 0].min(), bb[:, 0].max()
        by0, by1 = bb[:, 1].min(), bb[:, 1].max()

        z_hi = bz0 + int((bz1 - bz0) * 0.50)
        y_lo = by0 + int((by1 - by0) * 0.12)
        y_hi = by0 + int((by1 - by0) * 0.75)

        zz = np.arange(arr.shape[0])[:, None, None] < z_hi
        yy = (np.arange(arr.shape[1])[None, :, None] > y_lo) & (np.arange(arr.shape[1])[None, :, None] < y_hi)

        Lc_coords = np.argwhere(Lc)
        Rc_coords = np.argwhere(Rc)
        L_xkeep = np.arange(arr.shape[2])[None, None, :] > Lc_coords[:, 2].mean()
        R_xkeep = np.arange(arr.shape[2])[None, None, :] < Rc_coords[:, 2].mean()

        Lt = Lc & zz & yy & L_xkeep
        Rt = Rc & zz & yy & R_xkeep

        # 写入临时标签 (201=左颞叶, 202=右颞叶), 与原始 SynthSeg 标签无冲突
        out = label_map.copy()
        out[Lt] = 201
        out[Rt] = 202

        label_def = SYNTHSEG_LABELS.copy()
        label_def[201] = ("左颞叶 (非官方)", "tissue")
        label_def[202] = ("右颞叶 (非官方)", "tissue")

        v_lt = int(Lt.sum()) * sp[0] * sp[1] * sp[2] / 1000.0
        v_rt = int(Rt.sum()) * sp[0] * sp[1] * sp[2] / 1000.0
        self._emit_progress(95, f"颞叶完成 (非官方): 左 {v_lt:.1f}cm³ / 右 {v_rt:.1f}cm³")
        return out, label_def

    def _run_totalseg(self):
        """TotalSegmentator 全身/脑区语义分割（total/total_v3/total_mr/brain_structures）。"""
        self._emit_progress(0, "TotalSegmentator: 全身117类语义分割...")

        def ts_progress(pct, msg):
            self._emit_progress(pct, msg)

        self._emit_progress(3, "开始语义检测...")
        label_map = detect_semantic(
            self.sitk_image, self.spacing, progress_cb=ts_progress, task=self.task,
        )
        if self.task == "total_mr":
            return label_map, TOTALSEG_TOTAL_MR
        return label_map, TOTALSEG_TOTAL

    def _run_brain_structures_ts(self):
        """TotalSegmentator 官方 brain_structures (Dataset409, 学术授权)。

        输出 16 类跨左右整体脑区(含颞叶=15 全叶含白质)。0.5mm 高分辨率模型,
        fast 不被支持; 需 TOTALSEG license (config.json license_number)。
        直接经 detect_semantic 跑 (task=brain_structures), 标签映射 TS_BRAIN_STRUCTURES。
        """
        self._emit_progress(0, "TS brain_structures (官方, Dataset409, 学术授权)...")

        def ts_progress(pct, msg):
            self._emit_progress(pct, msg)

        self._emit_progress(3, "脑部裁剪 + 0.5mm 推理中 (约 1-3 分钟)...")
        label_map = detect_semantic(
            self.sitk_image, self.spacing, progress_cb=ts_progress, task="brain_structures",
        )
        return label_map, TS_BRAIN_STRUCTURES

    def _build_region_results(self, label_map: np.ndarray, label_def: dict) -> List[ROIRegionResult]:
        """从全局 label_map 按 label_def 提取各结构 ROIBlock。"""
        result = ROIRegionResult(region="全局")
        for idx, (label_id, (anat_name, category)) in enumerate(label_def.items(), start=1):
            if self._cancelled:
                return []

            mask = (label_map == label_id)
            if mask.sum() == 0:
                continue

            block = self._build_block(
                "全局",
                category,
                mask,
                0,
                self.spacing,
                label_id=label_id,
                anat_name=anat_name,
            )
            if block is None:
                continue

            if category == "bone":
                result.bones.append(block)
            elif category == "vessel":
                result.vessels.append(block)
            else:
                result.tissues.append(block)

            pct = 85 + int(idx / len(label_def) * 13)
            self._emit_progress(pct, f"结构提取: {anat_name}")

        region_results: List[ROIRegionResult] = []
        if result.bones or result.vessels or result.tissues:
            region_results.append(result)
        return region_results
