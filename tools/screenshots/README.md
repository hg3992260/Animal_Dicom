# 截图素材生成流程

README「界面预览」里的图都由这套脚本从**真实程序**产出，可随时重跑。

```
tools/screenshots/
├── make_case.py       ① 生成合成病例（多序列，用于病人列表与 VR 校验演示）
├── capture_gui.py     ② 整窗截图（Qt 控件渲染 + VTK 渲染区合成）
├── capture_modes.py   ③ 12 种渲染模式的渲染窗口截图
└── assemble.py        ④ 整理成 docs/images/（命名、压缩、模式总览拼图）
```

## 用法

```bash
python tools/screenshots/make_case.py        # → temp/shot_case/
python tools/screenshots/capture_gui.py      # → temp/shots/out/hero-*.png
python tools/screenshots/capture_modes.py    # → temp/shots/out/render-*.png（约 20 分钟）
python tools/screenshots/assemble.py         # → docs/images/
```

## 两个关键实现细节

1. **整窗截图必须合成**：`QWidget.grab()` 抓不到原生 OpenGL 子窗口，VTK 渲染区会是全黑。
   所以 `capture_gui.py` 抓两样再拼：Qt 的整窗（界面完美）+ VTK 自己的
   `vtkWindowToImageFilter`（渲染完美），按 `vtk_widget.mapTo(win)` 的位置贴回去，
   并处理 `devicePixelRatio`（本机 1.25）。两者来自同一进程同一时刻，不存在"拼接假图"。
2. **合成病例是刻意构造的**：`make_case.py` 造出需要演示的四种情形——
   薄层 CT（VR ✓）、厚层 CT（VR ⚠）、定位像（VR ✗）、MR（模态徽章），
   体模内容包含肺 / 脊柱+椎间盘 / 肋骨 / 主动脉+分支 / 高密度结节，
   以便各种渲染模式都有可看的结构。**它是合成数据，不是真实病例，解剖形态是示意性的。**

## 已知未能取到的图

- `exposure_render`（ErCore CUDA 路径追踪）在本次采集机器上报
  `ER Exception in render_estimate: invalid configuration argument ()`，
  渲染结果无效，故未收录。
- `2dtf` / `bone_mono` 在合成体模上呈现为散斑状（该体模没有真实骨小梁结构），
  只保留在 `modes-overview.jpg` 总览里，未单独出图。
