"""病例结构前置分析工具。"""
from __future__ import annotations

from typing import Optional

from .. import dicom as dicom_lib

TOOL_META = {"name": "ssdvr_scan_case", "version": "1.0"}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_scan_case(path: str, max_depth: int = 4, max_files: int = 50000) -> dict:
        """只读 DICOM 头分析病例目录，按 SeriesInstanceUID 分组。

        返回每个序列: folder/file_count/series_description/modality/rows/cols/slices/spacing_xy/spacing_z/instance_range/est_raw_gb/warnings，
        以及 recommended_index（文件数最多序列）与多序列风险提示。加载时应传 folder 或 series 下标。
        """
        return dicom_lib.scan_case(path, max_depth=max_depth, max_files=max_files)
