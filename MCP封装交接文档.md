# SSD+VR Viewer MCP 封装交接文档

> 供其他 agent 直接复用本 MCP 封装。阅读本文后应能完成：启动/加载/渲染/截图/语义分割的完整闭环，
> 并在遇到"黑屏、加载被忽略、分割不继续"等已知问题时正确处置。

---

## 1. 这是什么

`mcp_ssd_vr/` 是 **SSD+VR Viewer**（PySide6 + VTK 体绘制 DICOM 查看器）的 MCP 封装，让 AI agent 能：

- 启动/重启/关闭 GUI 进程
- 加载 DICOM（含多序列病例的**前置结构分析**与**逐序列加载**）
- 读取渲染状态、截图确认画面
- 触发 TotalSegmentator 语义分割（按 Modality 自动选模型）并读取结果
- 只渲染指定解剖结构、查询事件/错误/工具调用记录

**核心价值**：把"GUI 进程 + TCP 桥 + 事件推送"封装成一组确定性工具，避免 agent 直接操作 GUI 时的竞态（加载被静默丢弃、模态框阻塞、进程残留）。

---

## 2. 架构总览

```
opencode (stdio JSON-RPC)
   └── mcp_ssd_vr/server.py (FastMCP server，常驻)
         ├── tools/            ← 工具层（可热重载）
         │     ├── lifecycle.py   ssdvr_launch / ssdvr_reload_tools
         │     ├── control.py     ssdvr_load_dicom / ssdvr_set_mode / ssdvr_set_opacity /
         │     │                  ssdvr_set_camera / ssdvr_set_window_level / ssdvr_set_ssd_threshold /
         │     │                  ssdvr_set_vr_threshold / ssdvr_get_thresholds / ssdvr_set_cr_params /
         │     │                  ssdvr_set_preprocess / ssdvr_set_crop / ssdvr_toggle_background /
         │     │                  ssdvr_trigger_roi / ssdvr_list_roi_blocks / ssdvr_render_roi_label /
         │     │                  ssdvr_roi_cancel / ssdvr_roi_clear / ssdvr_set_roi_weight_path /
         │     │                  ssdvr_list_presets / ssdvr_apply_preset / ssdvr_get_render_params /
         │     │                  ssdvr_restart_gui
         │     ├── dicom_scan.py  ssdvr_scan_case（病例结构前置分析）
         │     ├── inspect.py     ssdvr_status / ssdvr_state_snapshot / ssdvr_wait_event
         │     ├── capture.py     ssdvr_screenshot
         │     └── recording.py   ssdvr_query_events / ssdvr_query_errors / ssdvr_query_tool_calls /
         │                        ssdvr_export / ssdvr_sessions
         ├── bridge_client.py  ← TCP 客户端（连 GUI 内嵌桥，收事件/发命令）
         ├── gui_bridge.py     ← 运行在 GUI 进程内（TCP server + 主线程调度）
         ├── state.py          ← GuiState 快照模型 + 事件类型常量
         ├── config.py         ← 路径/端口/解释器配置
         └── recorder.py       ← SQLite + JSONL 双写记录
ssd_vr_viewer.py（GUI 进程，独立生命周期）
```

- **分离进程**：GUI 崩溃/重启不影响 MCP server 与 opencode 会话。
- **命令流**：MCP tool → `BridgeClient.call(op)` → TCP → `gui_bridge._execute`（Qt 主线程 QueuedConnection）→ ViewerWindow 方法 → 响应回传。
- **事件流**：GUI 主动推送 `load_start/load_done/progress/roi_done/error/state/gui_ready` → MCP server 记录 + 追加到 `waited_events` → `ssdvr_wait_event` 轮询消费。
- **协议**：TCP line-delimited JSON，`{"id","op","args"}` / `{"id","ok","data"}` / `{"type","data","ts"}`。

---

## 3. 快速开始

### 3.1 配置（`mcp_ssd_vr/config.py`）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SSD_VR_PYTHON` | `/Users/cuiqi/Desktop/python/conda/envs/dicom/bin/python` | GUI 解释器（必须含 vtk/PySide6/SimpleITK/totalsegmentator） |
| `SSD_VR_REPO` | 仓库根目录 | 覆盖项目根 |
| `SSD_VR_MCP_PORT` | `7799` | 桥端口，被占时自动向后探测（7799–7899） |
| `SSD_VR_EXE` | 自动发现 | 冻结版 EXE 路径（也可给包含 exe 的目录） |
| `SSD_VR_LAUNCH` | `auto` | 启动目标：`auto`（有 EXE 用 EXE）/ `source` / `exe` |
| `SSD_VR_BRIDGE_FILE` | `%LOCALAPPDATA%\SSD_VR_MCP\bridge.json` | 桥"发现文件"路径 |

### 3.3 接入冻结版 EXE / 被别的 agent 接管

桥已编进 EXE，**默认开启**；MCP server 端会自动发现端口（发现文件 + 协议握手 + 7799–7898 扫描），
所以"用户双击 EXE、agent 直接接管"无需任何配置。完整说明、各客户端配置片段、
安全边界与排查表见 **`MCP_EXE_BRIDGE.md`**。

### 3.2 标准工作流（多序列病例）

```
1. ssdvr_status                      # 健康检查（bridge_connected / gui_state）
2. ssdvr_launch()                    # 干净启动 GUI（不传 dicom_path，避免默认数据）
3. ssdvr_scan_case(path=病例根目录)   # 前置分析：返回序列列表（folder/文件数/描述/Modality/尺寸/估内存）
4. ssdvr_load_dicom(path=序列文件夹)  # 或直接传病例根目录（自动解析到文件数最多的序列）
5. ssdvr_wait_event(etype="load_done")  # 等"加载并渲染完成"（语义见 §6.1）
6. ssdvr_screenshot()                # 截图确认 VR 显示（load_done 后无需额外等待）
7. ssdvr_trigger_roi(task="auto")    # 自动按 Modality 选任务（MR→total_mr，CT→total）
8. ssdvr_wait_event(etype="roi_done")  # 分割完成（默认 3mm 快速模型，约 1–5 分钟）
9. ssdvr_list_roi_blocks()           # 查看分割出的结构（label_id / 体积）
10. ssdvr_render_roi_label(label_id=N)  # 只叠加渲染指定结构
11. ssdvr_state_snapshot()           # 最终状态核对
```

---

## 4. 工具参考（35 个）

### 4.1 生命周期

**`ssdvr_launch(dicom_path=None)`**
- 启动 GUI 进程并等待 TCP 桥就绪。`dicom_path` 可指定启动即加载的 DICOM；**省略 = 干净启动**（不再有默认测试数据）。
- 内部会先做 `resolve_series_path`（病例根目录 → 单一序列文件夹）。
- **启动目标自动选择**：默认 `auto` = 仓库里有冻结版 EXE 就启动 EXE，否则回退源码。
  `SSD_VR_LAUNCH=source` 强制源码（调试用），`exe` 强制 EXE（找不到直接报错），
  `SSD_VR_EXE` 可显式指定 exe 或目录。详见 **`MCP_EXE_BRIDGE.md`**。
- 返回 `{ok, pid, port, target, exe}`；若已运行（含用户手工双击的 EXE）返回 `already_connected/attached`，不重复启动。

**`ssdvr_restart_gui()`**
- 重启 GUI 进程（viewer/`gui_bridge.py` 改动后调用生效）。返回 `{ok, pid, port, old_pid, target, reconnected}`。
- **会先安全关闭旧进程**（`shutdown` → 等待退出 → terminate → kill），再启动新进程，避免进程残留占端口（旧版本曾因此连回旧代码进程）。
- 当前实例是"接管"来的手工进程时，同样先通过桥 `shutdown` 再重启。
- 桥自动重连；调用返回时 `reconnected:true` 即可用。

**`ssdvr_reload_tools()`**
- 热重载 MCP 工具层（`mcp_ssd_vr/tools/*.py` 改动后调用）。返回重载清单与已注册工具。
- 内置 FastMCP 补丁：同名工具**覆盖替换**（FastMCP 原生对同名返回旧工具，见 §6.5）。
- **注意**：只影响 MCP server 侧工具；`gui_bridge.py` / `ssd_vr_viewer.py` 的改动需 `ssdvr_restart_gui`。

### 4.2 加载与病例分析

**`ssdvr_scan_case(path, max_depth=4, max_files=50000)`**
- **只读 DICOM 头**（不读像素），把病例目录按 `SeriesInstanceUID` 分组。
- 返回每个序列：`folder`（加载时应传的文件夹）/`file_count`/`series_description`/`modality`/`rows`/`cols`/`slices`/`spacing_xy`/`spacing_z`/`instance_range`/`est_raw_gb`/`warnings`，以及 `recommended_index`（文件数最多的序列）与多序列混读风险提示。
- 1000 文件约 0.5s。

**`ssdvr_load_dicom(path, series=None)`**
- 加载 DICOM。`path` 可为病例根目录/序列文件夹/单文件；`series` 为 `ssdvr_scan_case` 返回的序列下标（不传或根目录多序列时自动选文件数最多的）。
- 返回 `{ok, accepted, resolution, gui}`；`resolution` 含实际加载的序列信息与 `message`。
- **忙碌拒绝**：若上一加载仍在进行，返回 `{ok:false, error:"load in progress: ...", load_busy:true}` 并推 `error` 事件——不再静默丢弃。

### 4.3 渲染控制

**`ssdvr_set_mode(mode)`** — 切换渲染模式：`stable | hd_surface | cinematic | nature_channels | spectral | exposure_render | dual_volume | figure8_channels | layer_channel | frangi_channel | bone_mono | 2dtf`。
- `exposure_render`/`dual_volume` 在 Mac 上不可用（Exposure Render 依赖 NVIDIA CUDA，Windows-only）：**MCP 驱动时不弹模态框**，自动回退 `stable` 并推 `error` 事件；返回的 `mode` 是**实际生效**的模式（如 `{mode: "stable", requested: "exposure_render"}`）。

**`ssdvr_set_opacity(ssd=None, vr=None)`** — 调整 SSD/VR 不透明度（0.0–1.0），至少给一个。

**`ssdvr_set_camera(view=None, azimuth=0, elevation=0, roll=0, dolly=1.0, position=None, focal=None, view_up=None, view_angle=None, reset=False)`**
- VR 相机/视角控制（**任意角度**），支持组合：
  - `view` 预设：`coronal | coronal_rear | sagittal | sagittal_rear | axial | axial_rear | three_quarter | front_top`
  - `azimuth/elevation/roll`：增量旋转角度（度，正数顺时针）
  - `dolly`：镜头推拉倍率（>1 拉近，<1 拉远）
  - `position/focal/view_up`：绝对坐标 `[x,y,z]`；`view_angle`：视场角（度）
  - `reset=True`：回到默认全景视角
- 返回设置后的相机状态 `{position, focal, view_up, distance, view_angle}`。
- 示例：`view='three_quarter', azimuth=45` = 先切预设视角再顺时针转 45°。

**`ssdvr_set_window_level(wl=None, ww=None)`**
- 调整窗宽窗位：`wl`=窗位偏移(HU, -1000..1000)，`ww`=窗宽缩放(10..300，除以100应用)。至少传一个。
- 返回当前 `{wl_offset, ww_scale}`。

**`ssdvr_list_presets()`** — 列出可用 Slicer 渲染模板（首项为滑块调节模式，共 31 个：CT-AAA/CT-Bone/MR-Default/MR-T2-Brain 等）。

**`ssdvr_apply_preset(name=None)`** — 应用 Slicer 渲染模板（透明度/颜色/光照）；`name` 省略或传滑块项则恢复滑块调节。

**`ssdvr_toggle_background()`** — 切换渲染背景（暗色酒红 ↔ 亮色），返回 `{bg_is_white}`。

**`ssdvr_roi_cancel()`** — 取消进行中的 ROI 分割（无运行时安全返回）。

**`ssdvr_roi_clear()`** — 清空 ROI 分割结果并恢复原始 VR（无结果时安全返回）。

**`ssdvr_set_roi_weight_path(path=None)`** — 设置 TotalSegmentator 权重目录（影响分割模型）；返回检测到的任务列表与当前任务。

### 4.4 语义分割（ROI）

**`ssdvr_trigger_roi(task=None, fast=True)`**
- 触发 TotalSegmentator 分割。`task`：`total | total_v3 | total_mr | brain_structures`；**不传或 `"auto"` 按 Modality 自动推断**（MR→`total_mr`，CT→`total`）。`fast=True`=3mm 快速模型（默认），`False`=1.5mm 高精度（total_mr 需 Dataset850/851 权重）。
- **`brain_structures`（脑区/脑叶 16 类：额/顶/枕/颞叶 + 小脑/脑干/丘脑等）**：权重 Dataset409 属**商业授权模型**（非商业/学术用途免费，商业收费），本地缺失时 `detect_semantic` 预检查会给出明确提示（GUI 进度区 + error 事件 + last_error），**不弹窗阻塞**。
- **权重获取**：学术免费 license 在 `https://backend.totalsegmentator.com/license-academic/`（教育邮箱）申请，然后运行项目内一键下载脚本：
  ```bash
  python download_brain_structures.py 你的license编号   # 校验+下载到 totalseg_weights/
  python download_brain_structures.py --check            # 仅检查本地是否就绪
  ```
  也可手动放置 `totalseg_weights/Dataset409_neuro_550subj` 权重目录。
- 分割在 GUI 后台线程执行；**立即返回**（不再因主线程渲染/模态框阻塞而超时），完成后推 `roi_done` 事件。

**`ssdvr_list_roi_blocks()`** — 列出分割结果所有结构：`label_id/name/category/volume_cm3/voxel_count/region/bbox_*`。

**`ssdvr_render_roi_label(label_id=None)`** — 只叠加渲染指定 `label_id` 的结构；`0`/省略恢复原始 VR。

### 4.5 状态与事件

**`ssdvr_status()`** — 健康检查：`bridge_connected/port/gui_state(实时)/last_error/record_session`。

**`ssdvr_state_snapshot()`** — 完整状态：`dicom_loaded/dicom_path/dims/spacing/load_busy/render_window_size/render_mode/ssd_scale/vr_scale/roi_task/roi_fast/roi_running/roi_progress/roi_progress_msg/roi_results_count/last_error`。
- `load_busy`=后台加载线程运行中；`render_window_size` 用于诊断截图过小/黑屏。

**`ssdvr_wait_event(etype, timeout=120, contains=None)`** — 阻塞等待 GUI 推送的指定事件；`contains` 为事件 data 的 JSON 子串过滤。
- 事件类型：`load_start | load_done | progress | error | state | user_action | roi_done | gui_ready | gui_exit`。
- **建议顺序调用**（避免并行轮询的时序竞争）。

### 4.6 采集与记录

**`ssdvr_screenshot(out_dir=None)`** — 截取 VTK 渲染窗口，返回 `{ok, path, bytes, png_base64}`。默认存 `mcp_records/shots/`。
- **时机**：`load_done` 后截图即有完整画面（`load_done` 语义=已渲染，见 §6.1）。
- **像素判断**：黑屏=均值≈9、仅 1 种颜色、0% 像素>50；正常渲染=100+ 颜色、>5% 亮像素。

**`ssdvr_query_events(etype=None, limit=200)` / `ssdvr_query_errors(limit=50)` / `ssdvr_query_tool_calls(tool=None, limit=100)` / `ssdvr_export(out_path=None)` / `ssdvr_sessions()`** — 查询/导出记录（SQLite+JSONL 双写）。

**`ssdvr_hello_test(name="world")`** — 热重载验证工具（改后调 `ssdvr_reload_tools` 即生效）。

---

## 5. 桥命令（gui_bridge 内，agent 一般无需直连）

| op | 作用 |
|---|---|
| `query_state` | 采集状态快照（含 `load_busy`/`render_window_size`，已修复未加载时 `image_data` AttributeError） |
| `screenshot` | 渲染窗口 PNG base64 |
| `load_dicom` | 设置 path_edit 并触发加载；**worker 忙时返回 `load in progress`**；挂钩完成/失败事件 |
| `set_mode` / `set_opacity` | 渲染模式/透明度 |
| `set_camera` / `set_window_level` | 相机视角（预设/旋转/绝对坐标）/ 窗宽窗位 |
| `list_presets` / `apply_preset` | Slicer 渲染模板查询/应用 |
| `trigger_roi` | 设置 task（`auto` 推断）/fast 并调用 `on_roi_start` |
| `list_roi_blocks` / `render_roi_label` / `render_unsegmented_region` | ROI 结果查询/叠加渲染 |
| `shutdown` | 触发 viewer `shutdown()`（关闭窗口 + 退出 Qt 事件循环） |

---

## 6. 关键设计决策与坑（务必知晓）

### 6.1 `load_done` 语义 = 数据已加载 **且首帧已渲染**
- 由 viewer `_complete_initial_render` 完成后经 `push_load_done` 推送，**不再是 worker 读完就发**。
- 意义：agent 收到 `load_done` 后截图/操作即可看到完整画面（之前会截到小窗口/上一帧残留黑屏）。

### 6.2 加载忙碌拒绝，不做静默丢弃
- viewer `load_dicom()` 在后台线程忙时原本静默忽略新请求（曾导致：GUI 自动加载默认测试数据时，MCP 发来的真实病例被丢弃，`load_done` 报的还是测试数据的 dims）。
- 现 `gui_bridge` 在忙碌时返回 `ok:false + load_busy:true` 并推 `error` 事件；MCP 工具透传。**agent 应等上一个 `load_done` 再发新加载**。

### 6.3 多序列病例：先 `scan_case`，再逐序列加载
- 病例根目录含多个 DICOM 序列时，从根目录直接加载会让 SimpleITK 混读/非递归扫不到序列。`ssdvr_load_dicom` 会自动解析到单一序列文件夹；`series=N` 可指定。
- viewer `build_reader` 也有防御：目录多序列时只取文件数最多的（打印 `[MultiSeries]` 提示）。

### 6.4 MCP 模式跳过模态框 + ROI 相关修复
- `on_roi_start` 的 MRI 前置判定在 MCP 模式下**不弹 `QMessageBox`**（手动 GUI 仍弹），避免主线程卡死在模态框导致 `trigger_roi` 20s 超时。
- `on_mode_change` 的 Exposure Render 不可用提示同样在 MCP 模式下**自动跳过**（不弹窗），回退 `stable` 并推 `error` 事件；`ssdvr_set_mode` 返回实际生效模式。
- 首帧渲染由同步改 `QTimer.singleShot(0)` 异步，`trigger_roi` 立即返回。
- `roi_done` 事件由 viewer 挂钩 `push_roi_done` 推送（此前从未发出）。
- `closeEvent` 现在等待/取消 `roi_pipeline` 线程（此前只等加载线程，退出时报 `QThread: Destroyed while thread is still running`）。

### 6.5 macOS 兼容：nnU-Net multiprocessing spawn → fork
- TotalSegmentator 内部 nnU-Net **硬编码** `multiprocessing.get_context("spawn")`（`nnunetv2/inference/predict_from_raw_data.py:371`）。在无 `freeze_support()` 保护的上下文（脚本直跑 / GUI 的 ROI QThread）会抛：
  ```
  RuntimeError: An attempt has been made to start a new process before
  the current process has finished its bootstrapping phase.
  ```
- 修复：`segmentation/__init__.py::apply_macos_multiprocessing_compat()` monkeypatch `multiprocessing.get_context`，把 `"spawn"` 映射为 `"fork"`（macOS 原生支持），并静默 `resource_tracker leaked semaphore` 警告。
- 入口：`ssd_vr_viewer.py main()` / `ssd_vr_cli.py main()` / `semantic_detector.py` 调用前（幂等兜底）。
- 效果：报错刷屏 175 行 → 0 行，且因 fork 省去 spawn 开销，分割更快（26s → 7s）。

### 6.6 FastMCP 热重载补丁（同名工具覆盖）
- FastMCP `ToolManager.add_tool` 对同名工具**返回旧工具不替换**，导致热重载无效（改工具后仍执行旧函数）。
- `mcp_ssd_vr/tools/__init__.py` 顶部 monkeypatch `ToolManager.add_tool` 为覆盖替换。
- `capture.py` 顶部有引导：重载包自身让补丁应用到当前进程。
- 若工具改动不生效：确认是否调用了 `ssdvr_reload_tools`；`mcp_ssd_vr/tools/` 新文件会被自动发现注册。

### 6.7 默认测试数据已移除
- `ssd_vr_viewer.py --input` 默认空；`config.default_dicom()` 返回空；GUI 不再自动加载 `03_Upper_Limbs_Thorax`。
- 启动即干净状态，避免 8GB 测试卷占用加载线程导致后续操作被丢弃。

### 6.8 渲染窗口尺寸 / 截图诊断
- 刚启动未加载时 `render_window_size` 为 `[200,60]`（QVTK 初始尺寸），加载后变为实际窗口尺寸（如 `[2332,1778]`，2x Retina）。截图前应等 `load_done`。

---

## 7. 修改代码后的生效流程

| 改了哪里 | 操作 |
|---|---|
| `mcp_ssd_vr/tools/*.py` | `ssdvr_reload_tools()`（立即生效） |
| `mcp_ssd_vr/gui_bridge.py` | `ssdvr_restart_gui()` |
| `ssd_vr_viewer.py` | `ssdvr_restart_gui()` |
| `mcp_ssd_vr/server.py` / `state.py` / `config.py` | 需重启 MCP server（重启 opencode 会话） |
| 新增工具（tools 下新文件） | `ssdvr_reload_tools()` 注册；**但当前会话的工具清单是启动时固定的，模型侧需重启会话才能直接调用新工具** |

> 重要：`ssdvr_scan_case`、`ssdvr_load_dicom` 的 `series` 参数等新能力，在**本次会话**的工具清单里可见；若新增了模型侧不可见的工具，提示用户重启 opencode 会话。

---

## 8. 运维与故障排查

### 8.1 进程残留
- `ps aux | grep ssd_vr_viewer` 应只有 1 个 GUI 进程。多个残留时：先 `ssdvr_status` 确认桥连的是哪个，多余的直接 `kill <pid>`，再用 `ssdvr_restart_gui` 重启到单一实例。
- `ssdvr_restart_gui` 已自带旧进程清理（shutdown→terminate→kill）。

### 8.2 日志
- `mcp_records/gui.log`：GUI stdout/stderr（SimpleITK 警告、Memory Protect 下采样、TotalSegmentator 输出都在这里）。
- `mcp_records/ssd_vr_events_<session>.jsonl` + `ssd_vr.db`：事件/工具调用/错误双写记录。

### 8.3 常见现象与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `load_done` 后截图全黑/1 色 | 截图早于首帧渲染（旧版语义）或窗口未布局 | 用新版（load_done=已渲染）；仍黑则查 gui.log 是否有 `_complete_initial_render` 异常 |
| 加载请求返回 `load in progress` | 上一加载未完成（大体积约 60–70s） | 等上一个 `load_done` 再发；不要并发加载 |
| `trigger_roi` 超时 | 主线程被模态框/同步渲染阻塞（旧版） | 用新版（已修复）；确认已 `ssdvr_restart_gui` |
| `roi_done` 一直等不到 | 旧版从不推送 | 用新版；分割任务重（total 117 类 / 1.5mm）时放宽 timeout |
| GUI 退出报 `QThread: Destroyed while thread is still running` | ROI 线程未等待（旧版） | 用新版（closeEvent 已等待/取消 ROI 线程） |
| `No GPU detected. Running on CPU.` | TotalSegmentator import 时 CUDA 检查的提示（MPS 可用时不触发） | 无害提示；确认 `device` 选择走 mps（`torch.backends.mps.is_available()`） |
| nnU-Net `bootstrapping` RuntimeError 刷屏 | macOS spawn Pool（旧版无补丁） | 用新版（`segmentation` fork 补丁）；或 `ssdvr_restart_gui` |
| MRI 病例分割无结果/很慢 | 用了 `total`（CT 模型）而非 `total_mr` | `task="auto"` 或显式 `total_mr` |
| `brain_structures` 报"权重未就绪" | 缺 Dataset409（商业授权模型，需 license） | 学术免费 license 注册 → `python download_brain_structures.py <license>`（详见 §4.4） |

### 8.4 大体积自动下采样
- viewer 有显存保护：估计 VRAM > `vram_threshold_gb`(10GB) 或体素 > 阈值时自动下采样（如 2048²×1000 → 1024²×500）。加载慢属正常，等待 `load_done` 即可。

---

## 9. 扩展指南（新增一个工具）

1. 在 `mcp_ssd_vr/tools/` 新建 `my_tool.py`（或加到现有模块），格式：
   ```python
   TOOL_META = {"name": "my_tool", "version": "1.0", "description": "..."}

   def register(mcp) -> None:
       @mcp.tool()
       def ssdvr_my_tool(...) -> dict:
           ...  # 需要 GUI 时用 br.call(op, args) 或直接操作 holder/state
   ```
2. 若需要新的桥命令，在 `mcp_ssd_vr/gui_bridge.py` `_execute` 加 `if op == "my_op":` 分支（在主线程执行，访问 `self.win`）。
3. `ssdvr_reload_tools()` 注册；重启 opencode 会话后模型侧可见。
4. 可在工具 docstring 里写中文说明（FastMCP 会把 docstring 作为工具描述）。

---

## 10. 已知限制

- **会话工具清单固定**：模型侧可见的工具在 opencode 会话启动时确定；新工具/新参数需重启会话。
- **macOS 专用渲染**：`exposure_render`（CUDA）不可用，自动回退；分割设备为 MPS（Apple Silicon）。
- **GUI 无头截图**：截图捕获的是 VTK 渲染窗口，需等 `load_done`；窗口未布局时尺寸为 `[200,60]`。
- **`last_error` 状态粘滞**：MCP server 侧 `last_error` 记录最近一次错误，跨会话不自动清空（新会话重置）。
- **多进程写 log**：多个 GUI 实例会并发写 `gui.log`（追加模式），排查时注意按时间/端口区分。
