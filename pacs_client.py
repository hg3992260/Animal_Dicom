# -*- coding: utf-8 -*-
"""PACS 客户端：C-ECHO / C-FIND (Study·Series) / C-GET（基于 pynetdicom）。

供 GUI「PACS 取片」页调用；所有函数都是**阻塞式**，必须放在后台线程执行。
依赖：pynetdicom>=2, pydicom>=2.3（pydicom 3 亦可）。
"""
from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Tuple

try:
    from pydicom.dataset import Dataset
    from pynetdicom import AE, evt, StoragePresentationContexts
    from pynetdicom.sop_class import (
        Verification,
        StudyRootQueryRetrieveInformationModelFind as FIND,
        StudyRootQueryRetrieveInformationModelGet as GET,
    )
    _HAS = True
except Exception:  # noqa: BLE001
    _HAS = False

_PENDING = (0xFF00, 0xFF01)
_INSTALL_HINT = "未安装 pynetdicom，请先执行：  pip install pynetdicom"


class PacsError(Exception):
    """PACS 操作失败。"""


def available() -> bool:
    return _HAS


def require() -> None:
    if not _HAS:
        raise PacsError(_INSTALL_HINT)


def _mk_ae(calling_ae: str):
    require()
    ae = AE(ae_title=(calling_ae or "SSDVR")[:16])
    return ae


def _assoc(ae, host: str, port: int, called_ae: str, timeout: float):
    ae.acquire_connection_timeout = float(timeout)
    assoc = ae.associate(str(host), int(port), ae_title=(called_ae or "")[:16])
    if not assoc.is_established:
        raise PacsError(
            "无法建立连接：请检查 主机/端口/被叫AE，以及网络与防火墙"
            "（节点需允许本机访问）。")
    return assoc


def _st(code):
    return None if code is None else int(code)


def _date_range(dfrom: str, dto: str) -> str:
    dfrom = (dfrom or "").replace("-", "").strip()
    dto = (dto or "").replace("-", "").strip()
    if dfrom and dto:
        return f"{dfrom}-{dto}"
    if dfrom:
        return f"{dfrom}-"
    if dto:
        return f"-{dto}"
    return ""


def c_echo(host: str, port: int, called_ae: str, calling_ae: str = "SSDVR",
           timeout: float = 8.0) -> str:
    """C-ECHO 连通性测试。成功返回 'OK'。"""
    ae = _mk_ae(calling_ae)
    ae.add_requested_context(Verification)
    assoc = _assoc(ae, host, port, called_ae, timeout)
    try:
        st = assoc.send_c_echo()
        code = _st(getattr(st, "Status", None))
        if code == 0x0000:
            return "OK"
        raise PacsError("C-ECHO 返回状态 0x%04X" % code if code is not None
                        else "C-ECHO 无响应")
    finally:
        assoc.release()


def c_find_studies(host: str, port: int, called_ae: str, calling_ae: str = "SSDVR",
                   timeout: float = 15.0, patient_name: str = "", patient_id: str = "",
                   modality: str = "", date_from: str = "", date_to: str = "",
                   on_progress: Optional[Callable[[int, str], None]] = None) -> List[Dict]:
    """按 STUDY 级 C-FIND 查询检查。"""
    ae = _mk_ae(calling_ae)
    ae.add_requested_context(FIND)
    assoc = _assoc(ae, host, port, called_ae, timeout)
    out: List[Dict] = []
    try:
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = patient_name or ""
        ds.PatientID = patient_id or ""
        ds.StudyInstanceUID = ""
        ds.StudyDate = _date_range(date_from, date_to)
        ds.StudyDescription = ""
        ds.ModalitiesInStudy = (modality or "").replace("全部", "")
        ds.NumberOfStudyRelatedInstances = ""
        for status, ident in assoc.send_c_find(ds, FIND):
            if status is None:
                raise PacsError("查询过程中连接中断。")
            code = _st(getattr(status, "Status", None))
            if code == 0x0000:
                break
            if code in _PENDING and ident is not None:
                out.append({
                    "patient_id": str(getattr(ident, "PatientID", "") or ""),
                    "patient_name": str(getattr(ident, "PatientName", "") or ""),
                    "study_uid": str(getattr(ident, "StudyInstanceUID", "") or ""),
                    "study_date": str(getattr(ident, "StudyDate", "") or ""),
                    "study_desc": str(getattr(ident, "StudyDescription", "") or ""),
                    "modality": str(getattr(ident, "ModalitiesInStudy", "") or ""),
                    "instances": str(getattr(ident, "NumberOfStudyRelatedInstances", "") or ""),
                })
                if on_progress:
                    on_progress(len(out), "已找到 %d 个检查" % len(out))
            elif code not in _PENDING:
                raise PacsError("C-FIND 失败 0x%04X" % code)
    finally:
        assoc.release()
    return out


def c_find_series(host: str, port: int, called_ae: str, study_uid: str,
                  calling_ae: str = "SSDVR", timeout: float = 15.0,
                  modality: str = "", on_progress: Optional[Callable[[int, str], None]] = None) -> List[Dict]:
    """按 SERIES 级 C-FIND 查询某检查下的序列。"""
    ae = _mk_ae(calling_ae)
    ae.add_requested_context(FIND)
    assoc = _assoc(ae, host, port, called_ae, timeout)
    out: List[Dict] = []
    try:
        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = ""
        ds.Modality = (modality or "").replace("全部", "")
        ds.SeriesDescription = ""
        ds.SeriesNumber = ""
        ds.NumberOfSeriesRelatedInstances = ""
        for status, ident in assoc.send_c_find(ds, FIND):
            if status is None:
                raise PacsError("查询过程中连接中断。")
            code = _st(getattr(status, "Status", None))
            if code == 0x0000:
                break
            if code in _PENDING and ident is not None:
                out.append({
                    "series_uid": str(getattr(ident, "SeriesInstanceUID", "") or ""),
                    "modality": str(getattr(ident, "Modality", "") or ""),
                    "series_desc": str(getattr(ident, "SeriesDescription", "") or ""),
                    "series_number": str(getattr(ident, "SeriesNumber", "") or ""),
                    "instances": str(getattr(ident, "NumberOfSeriesRelatedInstances", "") or ""),
                })
                if on_progress:
                    on_progress(len(out), "已找到 %d 个序列" % len(out))
            elif code not in _PENDING:
                raise PacsError("C-FIND 失败 0x%04X" % code)
    finally:
        assoc.release()
    return out


def c_get_series(host: str, port: int, called_ae: str, study_uid: str, series_uid: str,
                 out_dir: str, calling_ae: str = "SSDVR", timeout: float = 15.0,
                 on_progress: Optional[Callable[[int, str], None]] = None) -> Tuple[int, str]:
    """C-GET 取回一个完整序列到 out_dir/<SeriesUID>/。返回 (收到张数, 目录)。"""
    require()
    ae = _mk_ae(calling_ae)
    ae.add_requested_context(GET)
    for cx in StoragePresentationContexts:
        ae.add_requested_context(cx.abstract_syntax, cx.transfer_syntax)

    series_dir = os.path.join(out_dir, series_uid or "series")
    os.makedirs(series_dir, exist_ok=True)
    counter = {"n": 0}

    def handle_store(event):
        ds = event.dataset
        try:
            ds.file_meta = event.file_meta
        except Exception:  # noqa: BLE001
            pass
        counter["n"] += 1
        inst = getattr(ds, "InstanceNumber", None)
        try:
            name = "%05d.dcm" % int(inst)
        except Exception:  # noqa: BLE001
            name = "%05d.dcm" % counter["n"]
        path = os.path.join(series_dir, name)
        if os.path.exists(path):
            path = os.path.join(series_dir, "%05d_%d.dcm" % (counter["n"], len(os.listdir(series_dir))))
        try:
            ds.save_as(path, enforce_file_format=True)
        except Exception:  # noqa: BLE001
            try:
                ds.save_as(path, write_like_original=False)
            except Exception:  # noqa: BLE001
                ds.save_as(path)
        if on_progress:
            on_progress(counter["n"], "已接收 %d 张" % counter["n"])
        return 0x0000

    handlers = [(evt.EVT_C_STORE, handle_store)]
    ae.acquire_connection_timeout = float(timeout)
    assoc = ae.associate(str(host), int(port), ae_title=(called_ae or "")[:16],
                         evt_handlers=handlers)
    if not assoc.is_established:
        raise PacsError("无法建立连接（C-GET）。")
    try:
        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        for status, ident in assoc.send_c_get(ds, GET):
            if status is None:
                raise PacsError("取片过程中连接中断。")
            code = _st(getattr(status, "Status", None))
            if code == 0x0000:
                break
            if code in _PENDING:
                if on_progress:
                    on_progress(counter["n"], "C-GET 进行中（已收 %d）" % counter["n"])
            else:
                # 4xxx/非 0：单个实例失败等，继续但提示
                if on_progress:
                    on_progress(counter["n"], "C-GET 状态 0x%04X（已收 %d）" % (code, counter["n"]))
    finally:
        assoc.release()
    return counter["n"], series_dir
