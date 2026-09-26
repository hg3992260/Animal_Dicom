# 依赖与资源清点 · Animal_Dicom v1.0.0

> 本文档由代码静态清点得出（`ast` 解析全部 68 个 Python 源文件的 import，
> 再与 `requirements.txt` / `requirements-full.txt` / `build.yaml` / `ssd_vr_viewer_macos.spec`
> 交叉核对）。目的是回答三件事：**跑起来需要什么、缺了会怎样、仓库里该放什么。**

清点日期：2026-09-26 ｜ 实测环境：Windows 11 + `D:\python\envs\mar`（Python 3.11/3.12 均可）

---

## 1. 第三方依赖

### 1.1 GUI 主程序必需（缺任何一个直接起不来）

| 包 | 声明 | 代码中的使用位置 | 说明 |
|---|---|---|---|
| **PySide6** ≥6.5 | requirements | `ssd_vr_viewer.py`、`mcp_ssd_vr/gui_bridge.py`、`segmentation/*`、`tools/screenshots/*` | Qt6 GUI 与线程/信号 |
| **PyCt6** ≥6.1 | requirements | `ssd_vr_viewer.py`（CSlider/CComboBox/CLineEdit/CTextEdit/CFrame/CLabel…） | 可换肤控件库；主题由 `warm_orange.json` 驱动 |
| **vtk** ≥9.2（含 `vtkmodules`） | requirements | 体绘制、MPR 三视图、截图 | 渲染内核；`vtkmodules.qt.QVTKRenderWindowInteractor` 必装 |
| **numpy** ≥1.24 | requirements | 全项目 | 数组/几何运算 |
| **SimpleITK** ≥2.2 | requirements | DICOM 读取、重采样、元数据（Modality） | 加载链核心 |
| **pydicom** ≥2.3 | requirements | `pacs_client.py`、`mcp_ssd_vr/dicom.py`、`tools/screenshots/make_case.py` | DICOM 文件解析（序列一致性选择 `pick_series_files`） |
| **scikit-image** ≥0.20 | requirements | NLM 去噪 / CLAHE / Frangi 微通道 | 预处理 |
| **scipy** ≥1.10 | requirements | `segmentation/detectors/*`、后处理 | 数值处理 |
| **Pillow** ≥10 | requirements | 截图落盘、`logo.ico` 生成、桥的 `take_screenshot` | 桥截图**必需**（PNG 编码） |
| **matplotlib** ≥3.7 | requirements | `ssd_vr_viewer.py:893-895`（colormap/LUT 取色） | 渲染颜色表 |
| **openpyxl** | ⚠️ **未在 requirements 中** | `ssd_vr_viewer.py:8419-8423`（`_sam_export_roi_excel` 一键导出 Excel） | **本轮补入 requirements**；缺失时该按钮报 ImportError |

### 1.2 PACS 取片（C-ECHO / C-FIND / C-GET）

| 包 | 声明 | 使用位置 | 说明 |
|---|---|---|---|
| **pynetdicom** ≥2.0 | requirements | `pacs_client.py` | DICOM 网络；缺失时 PACS 页降级提示（`pacs_client is None`） |
| **pydicom** ≥2.3 | requirements | 同上 | 数据集构造 |

### 1.3 MCP 控制桥 / Agent 集成（可选，不装也能正常用 GUI）

| 包 | 声明 | 使用位置 | 说明 |
|---|---|---|---|
| **fastmcp** | ⚠️ **未在 requirements 中** | `mcp_ssd_vr/server.py` | 只有跑 MCP server（让 AI Agent 驱动）时才需要；**本轮补入文档说明** |
| 无（stdlib） | — | `mcp_ssd_vr/gui_bridge.py`、`bridge_client.py`、`recorder.py` | 桥本身是纯 stdlib：`socket`/`threading`/`sqlite3`/`json`/`hmac` |

### 1.4 3D SAM 交互式分割（「MPR 阅片」页的分割按钮）

| 包 | 声明 | 使用位置 | 说明 |
|---|---|---|---|
| **torch** | requirements-full（注明必须用 CUDA index-url） | `segmentation/sam_pipeline.py`、`sam_adapter.py` | 3D SAM 推理 |
| **medim** ≥0.1.2 | requirements-full | `segmentation/sam_adapter.py`（SAM-Med3D 模型结构） | 权重 ~383 MB，首次运行自动下载（`download_sam_med3d.py`） |
| **torchio** ≥0.18 | requirements-full | `sam_adapter.py` | 体数据预处理 |
| **nibabel** ≥5 | requirements | `semantic_detector.py`、导出 NIfTI | 分割结果导出（可选功能） |

### 1.5 遗留依赖（**自动 ROI 功能已移除，GUI 不再调用**）

以下包只被 `segmentation/detectors/*` 与无头 CLI `ssd_vr_cli.py` 引用；
GUI（`ssd_vr_viewer.py`）在本版本中**不再 import** 它们：

| 包 | 使用位置 | 备注 |
|---|---|---|
| TotalSegmentator ≥2.16 | `segmentation/detectors/semantic_detector.py`、`download_ts_*.py` | 旧"自动ROI检测"页已删除；权重 2.2 GB（`totalseg_weights/`，不入库） |
| nnunetv2 ≥2.8 / batchgenerators | `semantic_detector.py` | 同上 |
| dipy | `segmentation/detectors/synthseg_detector.py` | SynthSeg 脑结构分割；需 Python ≥3.11 |
| sklearn | 间接（nnU-Net 运行期） | PyInstaller 打包时需检查（见 `main-full.yml`） |

> 结论：**基础版（不含分割）只需 1.1 + 1.2 两组依赖**；
> 需要 3D SAM 再装 1.4；1.5 只有继续使用 CLI 的检测器路径才需要。

### 1.6 仅开发/工具脚本用到

| 包 | 使用位置 |
|---|---|
| requests | `download_ts_mr_weights.py`（下载权重脚本，不入库） |
| PIL（Pillow） | `tools/screenshots/*`（截图拼版） |

### 1.7 GPU 加速（可选）

| 包 | 说明 |
|---|---|
| **cupy-cuda12x** ≥12 | Frangi 血管增强 GPU 加速；缺失自动回退 CPU（代码 `except ImportError`） |
| **ErCore.dll**（非 pip） | 见 §4 外部二进制：CR 路径追踪 / Exposure Render 模式需要 |

---

## 2. 运行期资源文件（必须随程序一起分发）

| 文件 | 大小 | 用途 | 缺失后果 |
|---|---|---|---|
| `retro.qss` | 10.5 KB | **暖橙复古拟物皮肤（优先级最高）** | 回退 `light.qss` |
| `light.qss` | 5.8 KB | 浅色明快主题 | 回退 `dark.qss` |
| `dark.qss` | 5.7 KB | 深色兜底主题 | 无皮肤（Qt 默认样式） |
| `warm_orange.json` | 3.9 KB | PyCt6 暖橙配色主题 | 回退 PyCt6 内置 `orange` |
| `case_template.json` | 3.3 KB | 病例档案模板（5 段 24 字段） | 使用内置 `DEFAULT_CASE_TEMPLATE` 并自动重建 |
| `presets.xml` | 16.1 KB | Slicer 风格 VR 预设（透明度/颜色/光照） | 预设下拉为空 |
| `scientific.json` | 3.6 KB | ⚠️ **清点发现：代码中无任何引用**（打包配置仍在分发） | 无影响；可清理或补文档 |
| `render_templates.json` | 3.8 KB | ⚠️ **清点发现：代码中无任何引用** | 无影响；可清理或补文档 |
| `logo.ico` / `logo.png` | 114 / 77 KB | Windows 窗口/任务栏图标 | 无图标 |
| `logo.icns` | 1.4 MB | macOS `.app` 图标（`ssd_vr_viewer_macos.spec`） | macOS 包无图标 |
| `logo.jpg` | 30.6 KB | 文档/展示用 | — |

**运行期自动生成（不入库）**：`deepseek_config.json`（DeepSeek Key）、`pacs_nodes.json`（PACS 节点）、
`mcp_records/`（事件 JSONL + SQLite + 档案 + ROI 统计 + 截图）、`.trial_anchor.json`（运行期限锚点）。

---

## 3. 仓库内自带模块（“依赖资源代码”的主体）

| 路径 | 体积 | 角色 |
|---|---|---|
| `ssd_vr_viewer.py` | 406 KB / ~8700 行 | GUI 主程序（5 页签：爱宠列表 / MPR 阅片 / 渲染 / 档案中心 / 设置） |
| `license_guard.py` | 11.7 KB | **30 天运行期限强制校验**（多锚点 + HMAC + 回拨检测 + 硬上限） |
| `pacs_client.py` | 9.2 KB | PACS C-ECHO / C-FIND(STUDY·SERIES) / C-GET |
| `deepseek_client.py` | 2.5 KB | DeepSeek `/chat/completions`（纯 urllib，无第三方依赖） |
| `ssd_vr_cli.py` | 50.9 KB | 无头批量渲染 CLI（引用 §1.5 遗留检测器） |
| `mcp_ssd_vr/` | 0.6 MB | 控制桥 + MCP server + **7 个工具模块 / 62 个工具** |
| `segmentation/` | 0.2 MB | SAM 分割管线 + 遗留检测器（`detectors/*`） |
| `tools/screenshots/` | — | 界面截图采集脚本 |
| `tools/verify/` | — | **功能自检脚本**（桥端到端 / 工具注册 / 期限对抗测试） |
| `.github/workflows/` | 3 个 | `main.yml`（基础 EXE）、`main-full.yml`（完整版）、`macos.yml` |
| `build.yaml`、`ssd_vr_viewer_macos.spec`、`pyi_rth_*.py` | — | PyInstaller 打包配置与运行时钩子 |

---

## 4. 外部二进制 / 权重（体积原因**不入库**，按需获取）

| 资源 | 体积 | 获取方式 | 缺失后果 |
|---|---|---|---|
| `ErCore.dll` | 由 `exposure-render-master` 源码构建 | 见该目录 README（CUDA 路径追踪引擎） | CR 电影级 / Exposure Render 模式不可用，其余 10 种模式正常 |
| SAM-Med3D 权重 | ~383 MB | `python download_sam_med3d.py` | 「MPR 阅片」的分割按钮不可用 |
| TotalSegmentator 权重 | 2.2 GB | 已废弃（自动 ROI 移除）；如仍用 CLI 检测器：`download_ts_weights.py` | 仅影响 CLI 旧路径 |
| 预编译 EXE（onedir+zip） | ~5.9 GB | GitHub Actions `main-full` 产物或 `SSD_VR_Fusion_Viewer_Full_Win/` | 无（源码可直接运行） |

---

## 5. 明确**不入库**的清单及原因

| 路径 | 体积 | 不入库原因 |
|---|---|---|
| `digital_body/` | **99.2 GB** / 12401 文件 | 外部数据集 |
| `temp/` | 10.3 GB | 本机日志/临时产物（自检脚本已移到 `tools/verify/`） |
| `SSD_VR_Fusion_Viewer_Full_Win/`、`*.zip` | 5.9 GB | 冻结版产物，走 Release 附件而不是 Git |
| `totalseg_weights/` | 2.3 GB | 权重，`.gitignore` 明确排除 |
| `frame/` | 392 MB | 依赖框架副本（pip 安装即得） |
| `mcp_records/` | 70 MB | **含病例档案/ROI 统计/截图/SQLite 事件记录**，且 `.trial_anchor.json` 是**本机**期限锚点 → 入库会污染其他机器 |
| `projects/`、`documentation/`、`key-paper/`、`docs/`(部分) | ~70 MB | 非运行必需（`docs/images/` 供 README 引用，保留） |
| `.opencode/`、`.dbg/` | 52 MB | Agent 配置与调试上报环境（本机私有） |
| `crash*.txt`、`*.docx`、`*.bak_*` | ~1 MB | 崩溃转储与旧备份等诊断垃圾 |
| `deepseek_config.json`、`pacs_nodes.json`、`*.npy`、`*.nii.gz` | — | 密钥 / 节点 / 影像数据 |

---

## 6. 本轮清点发现的 6 个问题（已在 v1.0.0 一并处理/记录）

| # | 问题 | 影响 | 处理 |
|---|---|---|---|
| 1 | `openpyxl` 未写入 `requirements.txt` | 「一键导出 Excel」点击即 `ImportError` | ✅ 已补进 requirements |
| 2 | `build.yaml` 的 `datas` 缺 `warm_orange.json` / `light.qss` / `retro.qss` / `case_template.json` | 冻结版**丢皮肤与病例模板**（回退到深色/内置默认） | ✅ 已补齐 |
| 3 | `build.yaml` / macOS spec 的 `hidden_imports` 缺 `license_guard` | 冻结版启动即"完整性检查失败"（退出码 4）——本轮新增期限校验引入 | ✅ 已补 |
| 4 | `requirements.txt` 未列 `fastmcp` | 跑 MCP server 时才暴露缺失 | ✅ 已在文档标注（GUI 本身不需要） |
| 5 | `scientific.json` / `render_templates.json` 在代码中**零引用** | 分发无用文件 | ⚠️ 记录，待清理或补文档 |
| 6 | `segmentation/sam_adapter.py`、`detectors/synthseg_detector.py` 带 **UTF-8 BOM**；`test_crash.py`、`test_sitk_vtk.py` 有语法错误 | BOM 会被 `ast` 解析失败（Python 运行不受影响）；测试文件已在 `.gitignore` | ⚠️ 记录（测试文件不入库） |

---

## 7. 一句话依赖画像

> **基础版（离线阅片 + 档案 + PACS + 体检报告单）**：PySide6 + PyCt6 + VTK + numpy + scipy +
> SimpleITK + pydicom + pynetdicom + scikit-image + Pillow + matplotlib + openpyxl。
> **分割版**再加 torch + medim + torchio（3D SAM）。
> 其余（TotalSegmentator / nnunetv2 / dipy）是已移除功能的遗留依赖。

---

## 8. 冻结版（EXE）打包实录 · `animal_dicom.spec`

Release 资产 `Animal_Dicom_v1.0.0_win64_portable.zip`（**424 MB**，解压后 onedir **1.16 GB**）
由 `pyinstaller --clean --noconfirm animal_dicom.spec` 产出。踩过的坑与取舍：

| 事项 | 数据 | 处理 |
|---|---|---|
| 初版 onedir 体积 | **3.63 GB** | 超出"可上传"预期 |
| ↳ 其中 CUDA 运行时 | ~1.9 GB（`cublasLt64_12.dll` 660 MB、`cusparse64_12.dll` 367 MB…因含 cupy 被整链拉入） | **排除 `cupy/cupyx/nvidia`** → Frangi 走 CPU 回退（既有降级路径） |
| ↳ 其中 QtWebEngine | ~270 MB（`Qt6WebEngineCore.dll` 195 MB + 调试资源） | 在 Analysis 后按名过滤（注意要匹配 `qt6webengine`，`qtwebengine` 匹配不到） |
| ↳ 其中 torch | 4.5 GB | 排除（3D SAM 属完整版；`segmentation` **不能**用 `collect_submodules`，会拉进 import torch 的检测器） |
| 环境里同时装了 PyQt5 | PyInstaller 直接 `Aborting build process`（不允许混用 Qt 绑定） | excludes 加 `PyQt5/PyQt6/PySide2` 与 matplotlib 的 qt 后端 |
| **PyCt6 数据文件缺失**（首次冻结版启动即崩） | 现象：窗口标题 `Unhandled exception in script`、无桥、无锚点文件；日志里只有 `set_color_theme` 前的路径 | 根因：PyCt6 是纯 Python 包但**带 `widgets/themes/*.json`、`windows/images/logo.png`**，只写 hiddenimports 只收 .py → 必须 `collect_all('PyCt6')` |
| `--noconsole` 下看不到异常 | 用户只能看到 PyInstaller 弹窗 | 在 `__main__` 包一层 try/except，写 `startup_error.log` 到 exe 同目录 |
| 冻结版期限锚点 | `__file__` 指向一次性解包目录 | `license_guard._app_dir()` 在 `sys.frozen` 下改用 `sys.executable` 目录 |
| 构建清理失败 | `PermissionError: 拒绝访问 dist…Animal_Dicom.exe` | 上次冒烟测试的 EXE 进程未退出，文件被占用 → 先杀进程再构建 |

**冻结版冒烟结果（12/12 通过）**：桥就绪、`license_guard` 随包（trial ok / 剩余 29.94 天）、
5 页签与右区显隐、加载 176 层 CT、`stable`/`cinematic`/`dual_volume` 渲染截图
（6056 / 28567 / 1447 色）、设置与档案可读、整窗截图、正常退出 rc=0。

**基础版 vs 完整版**

| | 基础版（`..._win64_portable.zip` 424 MB） | 完整版（4 个分包，共 2.9 GB） |
|---|---|---|
| 渲染 12 模式 / MPR / 测量 / 窗宽窗位 | ✅ | ✅ |
| PACS / 档案 / AI / 期限 / ErCore.dll | ✅ | ✅ |
| **GPU 加速**（cupy + CUDA 运行时） | ❌ CPU 回退（较慢） | ✅（`_internal/torch/lib` 内约 2.8 GB CUDA 运行时） |
| **3D SAM**（torch + medim + torchio + monai） | ❌ | ✅（+ `frame/SAM-Med3D-main`，权重首次自动下载） |
| 解压后体积 | 1.16 GB | 5.19 GB |

**完整版分包规则**（GitHub 单资产上限 2 GB，压缩后约 3 GB 必须拆）：

| 包 | 内容 | 压缩后 |
|---|---|---|
| part1of4 | `Animal_Dicom.exe`、`_internal/`（除 torch/cupy*）、`frame/`、`README-FIRST.txt` | 582 MB |
| part2of4 | `_internal/torch`（不含 `lib`）+ `_internal/cupy*` | 101 MB |
| part3of4 | `_internal/torch/lib` 第 1 组（`cufft`/`cusparse`/`torch_cpu` …） | 1023 MB |
| part4of4 | `_internal/torch/lib` 第 2 组（`torch_cuda` 913 MB / `cudnn_engines_precompiled` 562 MB / `cublasLt` 451 MB） | 1271 MB |

四个包都是**内容级** zip（内部路径为 `_internal/...`，互不重叠）→ 解压到同一目录即合并。
CI（`main-full.yml`）按同样规则自动分包；`torch/lib` 按体积均分（每组原始体积 ≤1.8 GB）。

**完整版实测（`ssdvr_sam_probe`）**：torch 2.6.0+cu124 ／ CUDA 可用（NVIDIA GeForce RTX 3080）／
cupy 14.0.1（GPU=True）／ medim·torchio·monai(1.5.2) 齐备 ／ `sam_adapter` import OK ／
`frame/SAM-Med3D-main` 与 `ckpt` 路径正确。渲染 stable 6056、cinematic 28567 色。
⚠️ 尚未验证：冻结环境里 `frangi_channel` 的端到端（显存紧张时 GPU 预处理可能失败）。

**打包时容易混进去的本机产物**（必须清掉，否则会把本机期限锚点/测试记录发给用户）：
`.trial_anchor.json`（期限锚点）、`mcp_records/`（档案/ROI/事件）、`startup_error.log`、
`deepseek_config.json`、`pacs_nodes.json`。CI 的分包步骤已加自动清除。


