"""3D SAM 交互分割后台任务（QThread + QObject Worker）。

复用 ROIPipeline 的信号模式：后台线程持有 SAMMed3DAdapter（懒加载，常驻），
收到「体数据 + 点集」请求即推理并回发 mask。线程只在首次使用时创建一次，
后续点击复用同一模型实例，避免重复加载权重。
"""
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
from PySide6 import QtCore

from .sam_adapter import SAMMed3DAdapter


def ensure_torch_cuda_on_main():
    """必须在主(GUI)线程调用：预置 torch + 建立 CUDA 上下文。

    实测：若 torch/CUDA 上下文首次在 QThread 工作线程内创建，
    该线程第 2 次 CUDA 调用会永久死锁（torch.rand(cuda)/tensor.to(cuda)）。
    先在主线程触发一次轻量 CUDA 初始化即可让工作线程安全复用同一上下文。
    """
    import torch
    if torch.cuda.is_available():
        _t = torch.zeros(1, device="cuda")
        del _t
        torch.cuda.synchronize()


class SamWorker(QtCore.QObject):
    progress_signal = QtCore.Signal(int, str)
    finished_signal = QtCore.Signal(object)   # np.ndarray bool (D,H,W)
    error_signal = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._adapter: Optional[SAMMed3DAdapter] = None

    def _ensure_adapter(self) -> SAMMed3DAdapter:
        if self._adapter is None:
            self.progress_signal.emit(1, "加载 SAM-Med3D 模型 (首次约 10-30s)...")
            self._adapter = SAMMed3DAdapter()
            self._adapter.load_model()
        return self._adapter

    @QtCore.Slot()
    def ensure(self) -> None:
        """仅加载模型（供 GUI 预热按钮调用）。"""
        try:
            self._ensure_adapter()
            self.progress_signal.emit(100, "SAM-Med3D 模型已就绪")
        except Exception as e:  # noqa: BLE001
            self.error_signal.emit(f"模型加载失败: {e}")

    @QtCore.Slot(object, list, list, float)
    def predict(self, vol_zyx: np.ndarray, pts: list, neg_pts: Optional[list] = None,
                threshold: float = 0.3) -> None:
        import time
        try:
            adapter = self._ensure_adapter()
            t0 = time.time()
            self.progress_signal.emit(20, f"SAM-Med3D 推理 ({len(pts)} 正点/{len(neg_pts or [])} 负点)...")
            mask = adapter.predict_from_volume(
                np.asarray(vol_zyx),
                pts_zyx=pts,
                neg_pts_zyx=neg_pts or [],
                threshold=threshold,
            )
            dt = time.time() - t0
            self.progress_signal.emit(100, f"分割完成 {int(mask.sum())} 体素 (GPU {dt*1000:.0f} ms)")
            self.finished_signal.emit(np.asarray(mask, dtype=bool))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.error_signal.emit(f"{type(e).__name__}: {e}")


class SamSession(QtCore.QObject):
    """管理 SamWorker 所在的后台线程；同一会话内模型常驻。"""

    # GUI 线程侧信号（emit 后自动排队到 worker 线程）
    request = QtCore.Signal(object, list, list, float)  # (vol, pts, neg_pts, threshold)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.worker: Optional[SamWorker] = None
        self.thread: Optional[QtCore.QThread] = None
        self.busy = False

    def start(self) -> SamWorker:
        if self.worker is not None:
            return self.worker
        self.thread = QtCore.QThread(self)
        self.thread.setObjectName("sam_seg_thread")
        self.worker = SamWorker()
        self.worker.moveToThread(self.thread)
        self.request.connect(self.worker.predict)
        self.thread.start()
        return self.worker

    def close(self) -> None:
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
            self.thread = None
            self.worker = None
