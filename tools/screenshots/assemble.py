"""把截图整理成仓库用的 docs/images/ 素材（命名、压缩、拼图）。"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

SRC = r"I:\SSD+VR_github\temp\shots\out"
DST = r"I:\SSD+VR_github\docs\images"
os.makedirs(DST, exist_ok=True)

# 界面图：PNG（文字锐利）
UI = {
    "hero-02-patient-list.png": "ui-patient-list.png",
    "hero-01-cinematic-params.png": "ui-render-params.png",
    "hero-03-nature-channels.png": "ui-nature-channels.png",
    "hero-04-hd-surface.png": "ui-hd-surface.png",
    "ui-03-roi-tab.png": "ui-roi-tab.png",
    "ui-04-kedge-tab.png": "ui-kedge-tab.png",
    "ui-05-sam-tab.png": "ui-sam-tab.png",
}

# 渲染模式图：JPEG（照片类内容，体积小）
MODES = [
    ("render-stable.png", "stable", "稳定"),
    ("render-hd_surface.png", "hd_surface", "HD 表面"),
    ("render-cinematic.png", "cinematic", "电影级"),
    ("render-nature_channels.png", "nature_channels", "Nature 通道"),
    ("render-figure8_channels.png", "figure8_channels", "Figure8"),
    ("render-layer_channel.png", "layer_channel", "分层"),
    ("render-frangi_channel.png", "frangi_channel", "Frangi 通道"),
    ("render-bone_mono.png", "bone_mono", "骨单色"),
    ("render-2dtf.png", "2dtf", "2D TF"),
    ("render-spectral.png", "spectral", "光谱"),
    ("render-dual_volume.png", "dual_volume", "双体数据"),
    ("render-cinematic-rear.png", "cinematic_rear", "电影级（背面）"),
]


def load(name: str) -> Image.Image:
    return Image.open(os.path.join(SRC, name)).convert("RGB")


def font(size: int):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    total = 0
    # 1) 界面图（PNG，宽度限制 1600 以控体积）
    for src, dst in UI.items():
        p = os.path.join(SRC, src)
        if not os.path.isfile(p):
            print(f"  跳过（缺）{src}")
            continue
        img = load(src)
        if img.width > 1600:
            h = int(img.height * 1600 / img.width)
            img = img.resize((1600, h), Image.LANCZOS)
        out = os.path.join(DST, dst)
        img.save(out, "PNG", optimize=True)
        total += os.path.getsize(out)
        print(f"  {dst:<28} {img.width}x{img.height}  {os.path.getsize(out)/1024:6.0f} KB")

    # 2) 单张模式图（JPEG q90）
    for src, key, _cn in MODES:
        p = os.path.join(SRC, src)
        if not os.path.isfile(p):
            print(f"  跳过（缺）{src}")
            continue
        img = load(src)
        out = os.path.join(DST, f"mode-{key.replace('_', '-')}.jpg")
        img.save(out, "JPEG", quality=90, optimize=True, progressive=True)
        total += os.path.getsize(out)
        print(f"  mode-{key:<24} {img.width}x{img.height}  {os.path.getsize(out)/1024:6.0f} KB")

    # 3) 模式总览拼图（带中英文标签）
    cols = 4
    cw, ch, pad, bar = 340, 316, 8, 30
    rows = (len(MODES) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + bar + pad) + pad), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    f = font(16)
    for i, (src, key, cn) in enumerate(MODES):
        p = os.path.join(SRC, src)
        if not os.path.isfile(p):
            continue
        img = load(src)
        img.thumbnail((cw, ch))
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + bar + pad)
        sheet.paste(img, (x + (cw - img.width) // 2, y + (ch - img.height) // 2))
        draw.text((x + 6, y + ch + 6), f"{cn}  ({key})", fill=(210, 214, 222), font=f)
    out = os.path.join(DST, "modes-overview.jpg")
    sheet.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    total += os.path.getsize(out)
    print(f"  modes-overview.jpg           {sheet.width}x{sheet.height}  {os.path.getsize(out)/1024:6.0f} KB")

    print(f"\n合计 {total/1048576:.2f} MB → {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
