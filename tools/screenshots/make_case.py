"""生成用于 README 截图的合成病例（多序列 + 有解剖感的体模）。

产出 temp/shot_case/ 下 4 个序列：
  P001_CT_Thorax_1.0mm  薄层 CT（VR ok）
  P001_CT_Thorax_5.0mm  厚层 CT（VR warn：层厚过大）
  P001_CT_Scout         定位像（VR block：非体数据）
  P001_MR_T1            3D MR（VR ok，模态徽章 MR）

体模内容（让渲染图有东西可看，而不是一个白圆柱）：
  体轮廓 / 双肺 / 脊柱 + 椎间盘 / 肋骨 / 主动脉 + 分支 / 几个高密度结节 / 噪声

用法: python temp/shots/make_case.py
"""
from __future__ import annotations

import os
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shot_case")
ROOT = os.path.abspath(ROOT)

STUDY_UID = generate_uid()


def _ellipsoid(shape, center, radii, grid=None):
    zz, yy, xx = grid
    cz, cy, cx = center
    rz, ry, rx = radii
    return ((zz - cz) / rz) ** 2 + ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0


def _cylinder(shape, axis, center, radius, grid=None, half_len=None):
    zz, yy, xx = grid
    cz, cy, cx = center
    if axis == "z":
        d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        m = d <= radius
        if half_len is not None:
            m &= np.abs(zz - cz) <= half_len
        return m
    if axis == "x":
        d = np.sqrt((yy - cy) ** 2 + (zz - cz) ** 2)
        m = d <= radius
        if half_len is not None:
            m &= np.abs(xx - cx) <= half_len
        return m
    d = np.sqrt((xx - cx) ** 2 + (zz - cz) ** 2)
    m = d <= radius
    if half_len is not None:
        m &= np.abs(yy - cy) <= half_len
    return m


def build_volume(nx=256, ny=256, nz=176, seed=7):
    """返回 HU 体数据 float32，形状 (nz, ny, nx)。"""
    rng = np.random.default_rng(seed)
    zz, yy, xx = np.mgrid[0:nz, 0:ny, 0:nx].astype(np.float32)
    grid = (zz, yy, xx)
    vol = np.full((nz, ny, nx), -1000.0, dtype=np.float32)   # 空气

    cy, cx = ny / 2.0, nx / 2.0

    # 体轮廓：椭圆 + 随 z 收窄（肩到腹）
    body = _ellipsoid((nz, ny, nx), (nz / 2.0, cy, cx), (nz * 0.52, ny * 0.40, nx * 0.33), grid)
    vol[body] = 35.0                                          # 软组织

    # 皮下脂肪环
    fat = body & ~_ellipsoid((nz, ny, nx), (nz / 2.0, cy, cx),
                             (nz * 0.50, ny * 0.375, nx * 0.305), grid)
    vol[fat] = -95.0

    # 双肺
    for sx in (+1, -1):
        lung = _ellipsoid((nz, ny, nx), (nz * 0.62, cy - 6, cx + sx * nx * 0.16),
                          (nz * 0.30, ny * 0.23, nx * 0.135), grid)
        vol[lung] = -830.0

    # 脊柱（椎体 + 椎间盘交替）
    spine = _cylinder((nz, ny, nx), "z", (0, cy + ny * 0.28, cx), radius=nx * 0.055, grid=grid)
    vol[spine] = 720.0
    disc_band = ((np.arange(nz)[:, None, None] // 6) % 2 == 0)
    vol[spine & np.broadcast_to(disc_band, vol.shape)] = 190.0

    # 椎管（低密度）
    canal = _cylinder((nz, ny, nx), "z", (0, cy + ny * 0.21, cx), radius=nx * 0.022, grid=grid)
    vol[canal & body] = 25.0

    # 肋骨：沿 z 斜置的管（左右各 6 根）
    for k in range(6):
        z0 = nz * (0.18 + 0.13 * k)
        for sx in (+1, -1):
            ys = cy - ny * 0.02 + 0.0
            rib = _cylinder((nz, ny, nx), "x",
                            (z0, ys, cx + sx * nx * 0.20), radius=nx * 0.014,
                            grid=grid, half_len=nx * 0.20)
            # 斜置：把 z 随 x 偏移
            shift = ((xx - cx) * sx * 0.22).astype(np.int32)
            idx = np.clip(zz.astype(np.int32) + shift, 0, nz - 1)
            rib = np.take_along_axis(rib, idx, axis=0)
            vol[rib & body] = 620.0

    # 主动脉 + 分支（对比剂，血管渲染模式能看出来）
    aorta = _cylinder((nz, ny, nx), "z", (0, cy + ny * 0.20, cx - nx * 0.045),
                      radius=nx * 0.035, grid=grid)
    vol[aorta & body] = 320.0
    for k, (dy, dx, zc) in enumerate(((-0.10, -0.10, 0.62), (0.02, -0.16, 0.48), (-0.14, 0.02, 0.40))):
        br = _cylinder((nz, ny, nx), "x",
                       (nz * zc, cy + ny * dy, cx + nx * dx), radius=nx * 0.018,
                       grid=grid, half_len=nx * 0.16)
        vol[br & body] = 300.0

    # 高密度结节（SSD / 骨窗下的亮点）
    for (zc, dy, dx, r) in ((0.55, -0.10, -0.14, 0.020), (0.42, -0.06, 0.13, 0.016),
                            (0.70, 0.04, -0.10, 0.013)):
        nod = _ellipsoid((nz, ny, nx), (nz * zc, cy + ny * dy, cx + nx * dx),
                         (nz * r * 1.6, ny * r, nx * r), grid)
        vol[nod] = 1150.0

    # 噪声（让 CR / 降噪模式有真实质感）
    vol += rng.normal(0.0, 18.0, vol.shape).astype(np.float32)
    return vol


def write_series(folder, vol, spacing, series_desc, modality, series_uid,
                 instance_extra=None, rows_cols=None):
    os.makedirs(folder, exist_ok=True)
    nz, ny, nx = vol.shape
    for k in range(nz):
        sop = generate_uid()
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage if modality == "CT" else pydicom.uid.MRImageStorage
        meta.MediaStorageSOPInstanceUID = sop
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(os.path.join(folder, f"IM{k + 1:04d}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.PatientName = "SSDVR^Demo"
        ds.PatientID = "DEMO001"
        ds.PatientBirthDate = "19800101"
        ds.PatientSex = "O"
        ds.StudyInstanceUID = STUDY_UID
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop
        ds.SOPClassUID = meta.MediaStorageSOPClassUID
        ds.Modality = modality
        ds.SeriesDescription = series_desc
        ds.StudyDescription = "SSD+VR Demo Study"
        ds.SeriesNumber = instance_extra.get("series_number", 1) if instance_extra else 1
        ds.InstanceNumber = k + 1
        ds.ImagePositionPatient = [0.0, 0.0, float(k) * float(spacing[2])]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceLocation = float(k) * float(spacing[2])
        ds.PixelSpacing = [float(spacing[0]), float(spacing[1])]
        ds.SliceThickness = float(spacing[2])
        ds.SpacingBetweenSlices = float(spacing[2])
        ds.Rows, ds.Columns = ny, nx
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleIntercept = 0.0 if modality == "CT" else 0.0
        ds.RescaleSlope = 1.0
        ds.WindowCenter = 300 if modality == "CT" else 400
        ds.WindowWidth = 1500 if modality == "CT" else 800
        if instance_extra and instance_extra.get("localizer"):
            ds.ImageType = ["ORIGINAL", "PRIMARY", "LOCALIZER"]
        else:
            ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        ds.PixelData = np.clip(vol[k], -1024, 3071).astype(np.int16).tobytes()
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        ds.save_as(ds.filename, write_like_original=False)
    return nz


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    print("生成体模…")
    vol = build_volume()
    print("  体积:", vol.shape, "HU 范围", float(vol.min()), float(vol.max()))

    # 1) 薄层 CT（1.0 mm）
    n1 = write_series(os.path.join(ROOT, "P001_CT_Thorax_1.0mm"), vol,
                      (0.8, 0.8, 1.2), "CT Thorax 1.0mm", "CT", generate_uid(),
                      {"series_number": 1})
    print(f"  CT 薄层   : {n1} 层")

    # 2) 厚层 CT（隔 4 层取样 → 层厚 4.8 mm，VR 预检应给 warn）
    thick = vol[::4]
    n2 = write_series(os.path.join(ROOT, "P001_CT_Thorax_5.0mm"), thick,
                      (0.8, 0.8, 4.8), "CT Thorax 5.0mm", "CT", generate_uid(),
                      {"series_number": 2})
    print(f"  CT 厚层   : {n2} 层")

    # 3) 定位像（单层 + LOCALIZER，VR 预检应给 block）
    scout = vol[np.newaxis, vol.shape[1] // 2, :, :]
    n3 = write_series(os.path.join(ROOT, "P001_CT_Scout"), scout,
                      (0.8, 0.8, 1.0), "CT Scout Topogram", "CT", generate_uid(),
                      {"series_number": 3, "localizer": True}, )
    print(f"  Scout     : {n3} 层")

    # 4) MR（降采样，模态徽章 MR）
    mr = vol[::2, ::2, ::2].astype(np.float32) * 0.35 + 300.0
    n4 = write_series(os.path.join(ROOT, "P001_MR_T1"), mr,
                      (1.6, 1.6, 2.4), "MR T1 3D", "MR", generate_uid(),
                      {"series_number": 4})
    print(f"  MR        : {n4} 层")

    # 单独一个"仅一层"的序列文件夹也给渲染用（薄层）
    print("完成 →", ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
