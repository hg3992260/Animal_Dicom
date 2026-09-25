"""下载 TotalSegmentator MRI (total_mr) 与 brain_structures 权重到 totalseg_weights/。

用法:
  python download_ts_mr_weights.py            # 下载 total_mr (850/851/852)
  python download_ts_mr_weights.py --brain <license>   # 下载 brain_structures (Dataset409, 需学术license)

total_mr 权重（公开）:
  Dataset852_TotalSegMRI_total_3mm_1088subj  -> total_mr fast (3mm, detect_semantic 默认 fast=True)
  Dataset850_TotalSegMRI_part1_organs_1088subj / Dataset851_..._part2_muscles_1088subj -> total_mr default (1.5mm)
brain_structures（商业授权模型）:
  Dataset409_neuro_550subj  -> 需 license（学术免费: https://backend.totalsegmentator.com/license-academic/）
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import urllib.request
import ssl
import zipfile

DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "totalseg_weights")

GITHUB = "https://github.com/wasserth/TotalSegmentator/releases/download"
GITHUB_URLS = {
    "Dataset852_TotalSegMRI_total_3mm_1088subj": f"{GITHUB}/v2.5.0-weights/Dataset852_TotalSegMRI_total_3mm_1088subj.zip",
    "Dataset850_TotalSegMRI_part1_organs_1088subj": f"{GITHUB}/v2.5.0-weights/Dataset850_TotalSegMRI_part1_organs_1088subj.zip",
    "Dataset851_TotalSegMRI_part2_muscles_1088subj": f"{GITHUB}/v2.5.0-weights/Dataset851_TotalSegMRI_part2_muscles_1088subj.zip",
}

# 若本机 ~/.totalsegmentator/nnunet/results 已有同款权重，直接复制避免重复下载
HOME_RESULTS = os.path.join(os.path.expanduser("~"), ".totalsegmentator", "nnunet", "results")


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def download_github(folder: str, url: str) -> None:
    dest_dir = os.path.join(DEST, folder)
    if os.path.isdir(dest_dir):
        print(f"[SKIP] {folder} 已存在")
        return
    # 优先从本机默认权重目录复制
    home_src = os.path.join(HOME_RESULTS, folder)
    if os.path.isdir(home_src):
        print(f"[COPY] {folder} 从 {home_src} 复制")
        shutil.copytree(home_src, dest_dir)
        return
    print(f"[GET] {folder} <- {url}")
    os.makedirs(DEST, exist_ok=True)
    zip_path = os.path.join(DEST, folder + ".zip")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ctx(), timeout=120) as r:
            data = r.read()
        print(f"  下载 {len(data)/1e6:.1f} MB, 解压中...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(DEST)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        print(f"  [OK] {folder}")
    except Exception as e:
        print(f"  [ERROR] {folder}: {e}")


def download_brain(license_number: str) -> None:
    import json
    folder = "Dataset409_neuro_550subj"
    dest_dir = os.path.join(DEST, folder)
    if os.path.isdir(dest_dir):
        print(f"[SKIP] {folder} 已存在")
        return
    import requests  # noqa: F401  # 复用 totalsegmentator 依赖
    from totalsegmentator import config as ts_config

    # 写入 license 到 totalseg 配置（backend 校验用）
    ts_config.setup_totalseg()
    cfg_file = ts_config.get_totalseg_dir() / "config.json"
    cfg = {}
    if cfg_file.exists():
        with open(cfg_file, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["license_number"] = license_number
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    from totalsegmentator.libs import download_model_with_license_and_unpack
    print(f"[LICENSE] 使用 license {license_number} 下载 {folder} ...")
    os.makedirs(dest_dir, exist_ok=True)
    ok = download_model_with_license_and_unpack("brain_structures", os.path.dirname(dest_dir))
    if ok:
        print(f"  [OK] {folder}")
    else:
        print(f"  [ERROR] 下载失败，请检查 license 是否有效")


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 total_mr / brain_structures 权重")
    ap.add_argument("--brain", nargs="?", const="__ask__", default=None,
                    help="下载 brain_structures (Dataset409)；传 license 编号")
    ap.add_argument("--check", action="store_true", help="仅检查本地是否就绪")
    args = ap.parse_args()

    os.makedirs(DEST, exist_ok=True)
    if args.check:
        for folder in list(GITHUB_URLS) + ["Dataset409_neuro_550subj"]:
            print(f"{folder}: {'OK' if os.path.isdir(os.path.join(DEST, folder)) else '缺失'}")
        return 0

    # total_mr
    for folder, url in GITHUB_URLS.items():
        download_github(folder, url)

    # brain_structures
    if args.brain is not None:
        if args.brain == "__ask__":
            print("用法: python download_ts_mr_weights.py --brain <license编号>")
            print("学术 license 申请: https://backend.totalsegmentator.com/license-academic/（教育邮箱）")
        else:
            download_brain(args.brain)

    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
