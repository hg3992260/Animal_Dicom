"""下载 SAM-Med3D 权重 (sam_med3d_turbo.pth, ~383MB) 到 segmentation/config.SAM_CKPT。

来源: https://huggingface.co/blueyo0/SAM-Med3D  (medim 官方 SAM-Med3D 加载路径)
用法:
  python download_sam_med3d.py            # 下载并校验
  python download_sam_med3d.py --check    # 仅校验本地
"""
from __future__ import annotations

import os
import sys

from segmentation import config

URL = "https://huggingface.co/blueyo0/SAM-Med3D/resolve/main/sam_med3d_turbo.pth"
EXPECTED_BYTES = 402_163_626


def check() -> bool:
    ok = os.path.exists(config.SAM_CKPT)
    if ok:
        size = os.path.getsize(config.SAM_CKPT)
        print(f"[OK] {config.SAM_CKPT} ({size/1e6:.1f} MB)")
        return size >= EXPECTED_BYTES - 1_000_000
    print(f"[缺失] {config.SAM_CKPT}")
    return False


def download() -> bool:
    if check():
        return True
    os.makedirs(os.path.dirname(config.SAM_CKPT), exist_ok=True)

    tmp = config.SAM_CKPT + ".part"
    import urllib.request
    ctx = __import__("ssl").create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = __import__("ssl").CERT_NONE

    print(f"[GET] {URL}")
    print(f"[→] {config.SAM_CKPT}")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done/1e6:.1f}/{total/1e6:.1f} MB ({done/total*100:.0f}%)", end="", flush=True)
        print()
    except Exception as e:
        print(f"[ERROR] {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False

    if os.path.getsize(tmp) < EXPECTED_BYTES - 1_000_000:
        print(f"[ERROR] 大小不符: {os.path.getsize(tmp)} != ~{EXPECTED_BYTES}")
        os.remove(tmp)
        return False
    os.replace(tmp, config.SAM_CKPT)
    print(f"[OK] 完成 {config.SAM_CKPT}")
    return True


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    sys.exit(0 if download() else 1)
