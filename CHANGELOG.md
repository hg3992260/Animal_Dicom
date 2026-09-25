# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## v1.0.0 — 2026-09-26

**首个正式版：从「SSD+VR Fusion Viewer（人医/科研）」改造成「Animal Dicom 爱宠影像工作站」。**

### 新增功能

- **信息架构重做**：5 个顶层页签 —— 爱宠列表 / MPR 阅片 / 渲染 / 档案中心 / 设置；
  全量术语「病人/患者 → 爱宠」。
- **MPR 阅片页**：三正交视图 + 滚轮翻层 + 双击单元格放大 2×2；
  测量工具（长度/角度/矩形/椭圆/不规则多边形，右击闭合）；
  独立窗宽窗位滑块 + 5 组临床预设；DR/MR 等 2D 序列自动切到本页。
- **3D SAM 交互式分割**：MPR 上左键正点/右键负点 → 后台推理 → 绿色叠加；
  结果可保存为 ROI 记录（体积/HU/体素数）、列表加载回显、删除、一键导出 Excel。
- **PACS 取片**：节点管理 + C-ECHO 连通性 + C-FIND（检查/序列）+ C-GET 拉取 → 一键载入阅片。
- **档案中心**：病例档案（5 段 24 字段模板）、`render.png` 封面缩略图、
  **时间戳版本**（`revisions/<YYYYmmdd_HHMMSS>.json`，可查看/恢复）、默认示例病例（置顶禁删）。
- **DeepSeek AI 引导补全**：按模板逐字段补全病历，记录到 `meta.ai_log`，可自动存一版时间戳版本。
- **设置页**：API Key / Base URL / 模型 / 温度，保存 / 测试连接 / 清除 Key。
- **暖橙浅色拟物皮肤**（`retro.qss` + `warm_orange.json` + 卡通猫吉祥物），皮肤优先级 retro > light > dark。
- **30 天强制运行期限**（`license_guard.py`）：多锚点 + HMAC + 篡改/回拨检测 + 硬上限，无 GUI/环境变量旁路。
- **MCP 控制桥扩展**：状态按子系统分块（`tabs/mpr/roi_saved/archive/settings/pacs/case/ai/pets/trial`），
  新增 25 个 op 与 26 个工具（共 **62 个 `ssdvr_*`**）；新增整窗截图 `ssdvr_screenshot_window`。
- **功能自检脚本**（`tools/verify/`）：桥端到端 100 项、期限对抗 15 项、工具注册静态检查。
- **依赖与资源清点文档** `DEPENDENCIES.md`。

### 移除

- **自动 ROI（TotalSegmentator / SynthSeg / nnU-Net）整条功能线**及对应页签、权重入口；
  遗留 API 改为返回明确的 deprecated 说明而非静默成功。
- **K-edge / PCCT 光谱页**、旧「自动ROI检测」页。

### 修复

| 缺陷 | 影响 |
|---|---|
| 「③ AI 引导补全」子页永不可达（`toggled` 只刷新状态，未切 `detail_stack` 页） | 点进不去 |
| 「双容积模式」被 ErCore/CUDA 门误挡（`index in (10,11)`，实际只有 10 需要 CUDA） | 无 CUDA 机器上选了自动回退 stable |
| VR 不透明度标签与滑块不一致（初值写死 `0.90`，实际 0.20） | 显示错误 |
| `load_dicom` 每次加载向 `127.0.0.1:7777` POST 几何快照 | 离线程序里的隐藏上报 → 改为 `SSDVR_DEBUG_TELEMETRY=1` 才启用 |
| 桥在 GUI 弹模态框时整体卡死（删除确认 / 无 mask 提示等） | op 超时 → 新增 `_NoDialogs` 统一兜底 |
| 桥的 `ok` 只反映"传输成功"，op 级参数校验失败被记成成功 | 记录/上报失真 → `call()` 合并为有效 ok |
| **桥未把 op 超时发给 GUI**（客户端超时形同虚设，桥侧恒为 120s） | 切 `frangi_channel` 等重预处理模式（实测 405 s）必然报"主线程执行超时" → 已在 `bridge_client.call` 带上 `timeout` |
| **冻结版缺 PyCt6 数据文件**（`widgets/themes/*.json` 等） | `set_color_theme()` 抛 `FileNotFoundError`，EXE 启动即 "Unhandled exception in script"，且 `--noconsole` 下用户看不到原因 → 已修 spec，并新增启动崩溃日志 `startup_error.log` |
| 右侧渲染区在隐藏页截图是空白（VTK 窗仅 400×38） | 新增 `vtk_visible`/`hint` 与 `switch_to_render` |
| 爱宠列表 / 设置页右侧保留渲染区 | 按需求改为占满整窗（并修复首屏不生效：首个页签不触发 `currentChanged`） |

### 打包/依赖修正

- `requirements.txt` 补 `openpyxl`（Excel 导出此前点击即 ImportError）。
- `build.yaml` / `ssd_vr_viewer_macos.spec` 补齐 `license_guard` 等 hidden-imports，
  以及 `retro.qss`/`light.qss`/`warm_orange.json`/`case_template.json`/`render_templates.json` 数据文件
  （此前冻结版会丢皮肤与病例模板，并因缺 `license_guard` 直接拒绝启动）。
- `build.yaml` 版本号与产品名对齐 1.0.0 / Animal_Dicom。
- **新增 `animal_dicom.spec`**：Windows onedir 便携包（Release 资产
  `Animal_Dicom_v1.0.0_win64_portable.zip`，424 MB）：
  - 排除 `torch/medim/torchio/dipy/monai/totalseg`（约 4.5 GB）与 CUDA 运行时
    `cupy/nvidia`（约 1.9 GB）→ 体积 3.63 GB → 1.16 GB；
  - 过滤未使用的 `QtWebEngine/QtQuick`（约 270 MB）；
  - **PyCt6 连数据文件一起收集**（`widgets/themes/*.json` 等），否则启动即崩。
- 冻结版适配：`license_guard._app_dir()` 在 `sys.frozen` 下改用 `sys.executable` 目录，
  期限锚点不再写进一次性解包目录。

### 已知问题

- 基础版不含 3D SAM（需完整版依赖与 ~383 MB 权重）；CR 模式需 `ErCore.dll`。
- `.github/workflows/` 三个流水线文件暂未纳入仓库（提交令牌缺少 `workflow` scope），文件仍在本地工作区。
- 依赖清点发现 `scientific.json` / `render_templates.json` 在代码中零引用（保留待清理）。
- `segmentation/sam_adapter.py`、`detectors/synthseg_detector.py` 带 UTF-8 BOM（不影响运行）。

### 许可

- 仓库采用 **MIT License**；源码可自由构建，分发的构建产物带 30 天运行期限——两者关系见 README「许可与致谢」。
