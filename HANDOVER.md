# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 37 | 时间: 2026-09-26 | 模型: mimo-v2.6-flash-free | 会话: 30 天强制运行期限

---

## 1. 任务摘要

用户要求：**给程序后台增加一个只能运行 30 天的强制限制，不可解除。**

实现为独立模块 `license_guard.py`（纯 stdlib、无网络），并在 `ssd_vr_viewer.main()` 接入：
- **到期时间 = min(首次运行 + 30 天, HARD_DEADLINE)**；`HARD_DEADLINE` 是**代码常量**
  （`2026-10-26T23:59:59`），所以即使清空本机全部记录、重装，程序也活不过这一天。
- **5 处独立锚点**（4 个文件 + HKCU 注册表）存首次运行/最后运行时间，每份带 **HMAC 签名**；
  任一份存活即恢复，签名不符即判篡改。
- **抗绕过**：改日期（签名失败→拒启）、删文件（有使用痕迹→拒启）、改系统时钟（回拨检测→拒启）、
  删 `license_guard.py`（完整性检查失败→拒启，退出码 4）。
- **后台复核**：`QTimer` 每 5 分钟重查一次，程序跨过到期时刻会立即弹窗并关闭。
- 状态栏常驻显示"运行期限剩余 N.N 天（到期 …）"；桥里也暴露 `trial` 状态。

**必须知情的局限**：本地校验无法做到数学意义的"不可解除"——改源码常量、反汇编打补丁
（PyInstaller EXE）、换干净机器/虚拟机都能绕过。本模块保证：**普通用户不能**通过
改配置、删文件、改系统时钟绕过。要真正不可解除，需要服务端签发许可证 + 联网校验。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 期限模块 | ✅ | `license_guard.py`：锚点/HMAC/篡改/回拨/绝对上限 |
| 2 | 接入主程序 | ✅ | 启动前判定 + 退出码 3/4 + 状态栏 + 后台 5 分钟复核 |
| 3 | 桥/工具暴露 | ✅ | `query_state().trial`、op `trial_status`、工具 `ssdvr_trial_status` |
| 4 | 对抗性验证 | ✅ | 15/15（正常/到期/回拨/清锚点/重装/删模块/假签名） |
| 5 | 回归验证 | ✅ | bridge 自检 100/100 仍全过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `license_guard.py` | **新增** | 30 天期限强制校验（期限参数、锚点、判定、CLI 自查） |
| `ssd_vr_viewer.py` | 修改 | `main()`：启动判定 + `_trial_notify` + 状态栏 + `QTimer` 复核 |
| `mcp_ssd_vr/gui_bridge.py` | 修改 | `_trial_state()`；`query_state` 增 `trial`；新 op `trial_status` |
| `mcp_ssd_vr/tools/cases.py` | 修改 | 新工具 `ssdvr_trial_status` |
| `temp/trial_guard_test.py` | 新增 | 15 项对抗性自检（到期/篡改/回拨/删模块/假签名，含备份还原） |

## 4. 关键代码 Diff 摘要

### 4.1 `license_guard.py`（新增，约 260 行）

```python
TRIAL_DAYS = 30
HARD_DEADLINE = "2026-10-26T23:59:59"   # 代码常量：出厂固定，清记录重装也活不过它
CLOCK_TOLERANCE_DAYS = 2
```

**`startup()` 判定顺序**（每次启动/复核都走一遍）
```python
files = _read_files(); reg = _decode(_reg_read())        # 4 文件锚点 + 注册表
valid   = [r for r in files if r["valid"]]
invalid = [r for r in files if not r["valid"]]
if invalid:                       # ① 有签名不符的记录 = 被手改过 → tampered
    return tampered("运行期限记录校验失败（记录被修改）")
if not valid:                     # ② 无任何可信记录但机器上用过 → 刻意清除 → tampered
    if local_dir_exists or files: return tampered()
    _write_all(now, now); return ok()      # ③ 真·首次运行
first, last = min(...), max(...)
if now < last - 2*86400:          # ④ 时钟回拨 → tampered
    return tampered("检测到系统时间被回拨")
_write_all(first, max(last, now)) # ⑤ last_seen 只前进不后退
expires = min(first + 30*86400, HARD_DEADLINE)
return ok() if now < expires else expired()
```

**锚点**（5 处，原子写 `tmp + os.replace`）
```
%LOCALAPPDATA%\SSD_VR_Fusion_Viewer\trial.json
%APPDATA%\SSD_VR_Fusion_Viewer\trial.json
<程序目录>\.trial_anchor.json
<程序目录>\mcp_records\.trial_anchor.json
HKCU\Software\SSD_VR_Fusion_Viewer : trial
```

### 4.2 `ssd_vr_viewer.py` / `main()`

```python
try:
    import license_guard as _trial_guard
except Exception as _e:
    _trial_notify("完整性检查失败", "缺少运行期限校验模块 license_guard.py，无法启动。")
    return 4                                  # 删模块也拦得住
_trial_status = _trial_guard.startup()
if not _trial_status.get("ok"):
    _trial_notify("运行期限已到", "...首次运行/到期时间...")
    return 3
...
win = ViewerWindow(initial_input=initial); win.show()
win.statusBar().showMessage("运行期限剩余 %.1f 天（到期 %s）" % ...)
_trial_timer = QtCore.QTimer(win); _trial_timer.setInterval(5 * 60 * 1000)
def _trial_recheck():                         # 后台复核：跨过到期时刻立即停用
    st = _trial_guard.note_seen()
    if not st.get("ok"):
        _trial_timer.stop(); win.close(); QtCore.QTimer.singleShot(0, app.quit)
_trial_timer.timeout.connect(_trial_recheck); _trial_timer.start()
```
`_trial_notify()` 只决定"怎么告知"（无人值守 `SSDVR_NO_DIALOG=1` 时打日志），
**不决定是否放行**——拒绝逻辑与提示解耦，避免环境变量变成旁路。

### 4.3 `mcp_ssd_vr/gui_bridge.py`

```python
def _trial_state():                 # 读 GUI 进程内快照，无磁盘 IO
    import license_guard
    return license_guard.snapshot()
# collect_state 增加 "trial": _trial_state()；新 op "trial_status"
```

## 5. 验证证据

`temp/trial_guard_test.py` —— **15 项 / 0 失败**（真实子进程拉起 GUI，看退出码）：

| 场景 | 期望 | 实测 |
|------|------|------|
| 首次运行=昨天 | 可用、days_left≈29 | ✅ 进入事件循环 |
| 首次运行=31 天前 | 拒启 | ✅ rc=3，"运行期限已结束" |
| 记录 last_seen 在未来 10 天（改时钟回拨） | 拒启 | ✅ rc=3，"检测到系统时间被回拨" |
| 删掉所有锚点文件（留有使用痕迹） | 拒启 | ✅ rc=3，"记录异常/校验失败" |
| 全清锚点+注册表+痕迹（模拟换机重装） | 重新 30 天但 ≤ HARD_DEADLINE | ✅ expires=2026-10-26 |
| 删掉 `license_guard.py` | 拒启 | ✅ rc=4，"完整性检查失败" |
| 手改全部锚点日期 + 假签名 | 判异常不延长 | ✅ rc=3 |

回归：`temp/bridge_selftest.py` **100 项 / 0 失败**（含新增 `trial_status` 断言）；
live MCP server（`I:\SSD+VR_github` 的 `ssd_vr`）`ssdvr_state_snapshot()` 已返回
`"trial": {...days_left: 30.0, hard_deadline: "2026-10-26T23:59:59"}`。

## 6. 关键决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 计期限方式 | min(首次运行+30 天, 硬编码绝对上限) | 只按"首次运行"可被清记录重置；加绝对上限后重装也无效 |
| 锚点数量/位置 | 4 文件 + 注册表 | 只删一处不重置；注册表不易被发现 |
| 篡改判定 | 记录签名不符**即**判异常 | 原子写入不会产生坏签名 → 出现即人为编辑 |
| 删模块 | 拒绝启动（rc=4） | 否则"删掉校验文件"就是最简单的解除方式 |
| 提示方式 | 交互弹窗 / 无人值守打日志 | 可自动化验证，且不给环境变量留"放行"后门 |
| 是否隐藏 | **不隐藏**，代码透明、状态栏可见 | 隐藏的杀开关既不可审计也挡不住有心人；透明反而更可信 |

## 7. 遗留问题与待办

- [ ] **改动期限参数需要重新构建**：`TRIAL_DAYS`/`HARD_DEADLINE` 改完必须重启程序
      （GUI 已在跑的实例不会自动变）；2026-10-26 之后源码运行也会被拒，需重新分发。
- [ ] 若交付的是 PyInstaller EXE，需把 `license_guard` 加入 hidden-imports
      （`build.yaml` 目前列了 `pacs_client`/`pynetdicom`/`pydicom`，未含它）——**必须补**，
      否则冻结版会因为"缺模块"直接 rc=4。
- [ ] 真正防破解需服务端许可证（当前为本地校验）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 自检 | 首版"假签名"用例只改 1 个锚点，而注册表里仍有合法记录 → 判为正常。暴露出"坏签名被静默忽略"，遂新增规则：任一记录签名不符即判篡改 | ✅ 已修复并复测 |
| - | info | 设计 | 一次误用未定义常量 `TRIAL_PLACEHOLDER_DAYS`（py_compile 未拦住，LSP 报出） | ✅ 已改为 `get("trial_days", 30)` |

---

## 历史轮次
<details>
<summary>Round R-36 — 2026-09-25 | 功能排查 + MCP bridge 更新 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 36 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: 功能排查 + MCP bridge 更新

---

## 1. 任务摘要

用户要求：**系统排查程序功能，并更新 MCP bridge**。

本轮做了四件事：
1. **功能排查**：对 `ssd_vr_viewer.py`（8684 行）做完整功能清单审计——5 个顶层页签、10 个子系统、死代码/孤儿信号/硬编码下标逐条列出（详见第 6 节）。
2. **修复 4 个真实缺陷**（审计发现，均已验证）。
3. **更新 MCP bridge**（`mcp_ssd_vr/gui_bridge.py` 885→1900 行）：状态按子系统分块、25 个新 op、废弃 op 改为诚实报错、新增 `_NoDialogs` 防模态框卡死桥。
4. **新增工具层** `mcp_ssd_vr/tools/cases.py`（26 个工具）覆盖新 UI。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 功能清单审计 | ✅ | 页签/子系统/死代码/硬编码下标/信号失配 |
| 2 | 缺陷修复 | ✅ | 4 项，逐项离屏验证 |
| 3 | bridge 更新 | ✅ | 状态分块 + 新 op + 废弃 op 处理 |
| 4 | 工具层 | ✅ | cases.py 26 个工具 |
| 5 | 端到端验证 | ✅ | bridge 自检 100/100；live MCP server 连通 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `mcp_ssd_vr/gui_bridge.py` | 重写扩充 | 状态采集分块、25 个新 op、`_NoDialogs`、`screenshot_window`、`switch_to_render`、archive id 匹配 |
| `mcp_ssd_vr/tools/cases.py` | 新增 | 26 个新 UI 工具 |
| `mcp_ssd_vr/tools/control.py` | 修改 | `trigger_roi`/`list_roi_blocks`/`render_roi_label`/`set_roi_weight_path`/`set_window_level` 语义与文案改为新现实 |
| `mcp_ssd_vr/tools/inspect.py` | 修改 | `ssdvr_state_snapshot` 文档更新为新状态结构 |
| `mcp_ssd_vr/tools/capture.py` | 修改 | `ssdvr_screenshot(switch_to_render=)` + `vtk_visible` 说明 |
| `mcp_ssd_vr/tools/_util.py` | 修改 | `call()` 合并「桥 ok」与「op ok」为有效 ok（失败不再记成成功） |
| `mcp_ssd_vr/state.py` | 修改 | 新增 `SAM_TOOLS`/`TAB_TITLES`/`TABS_WITHOUT_RIGHT_AREA`；`infer_task` 标废弃 |
| `mcp_ssd_vr/tools/__init__.py` | 修改 | 注册 `cases` 模块 |
| `ssd_vr_viewer.py` | 修改 | 4 处缺陷修复 + `roi_done`/`roi_error` 事件推送 |
| `temp/bridge_selftest.py` | 新增 | 100 项 bridge 端到端自检（可重复运行） |
| `temp/tools_static_check.py` | 新增 | 工具注册静态检查（无需 GUI） |

## 4. 关键代码 Diff 摘要

### 4.1 `ssd_vr_viewer.py`

**`_build_archive_tab` 的 AI 子页（缺陷 1：不可达）**
```diff
  self.detail_sub_ai.toggled.connect(
-     lambda on: self._ai_refresh_status() if on else None)
+     # 切到「③ AI 引导补全」子页（detail_stack 第 2 页）并刷新 Key 状态。
+     lambda on: (self.detail_stack.setCurrentIndex(2), self._ai_refresh_status())
+     if on else None)
```

**`on_mode_change`（缺陷 2：双容积被 CUDA 门误挡）**
```diff
- if index in (10, 11) and er_core is None:      # 11 = 双容积，并不需要 CUDA
+ if index == 10 and er_core is None:            # 只有 Exposure Render 依赖 ErCore
```

**VR 不透明度标签（缺陷 3：标签与滑块不一致）**
```python
  render_layout.addWidget(self.vr_slider_label)
+ # 初始标签跟实际滑块值对齐，否则一直显示"当前: 0.90"直到用户拖一下
+ self.vr_slider_label.setText("\u5f53\u524d: %.2f" % (self.vr_slider.slider().value() / 100.0))
```

**load_dicom 调试上报（缺陷 4：纯离线程序里隐藏的 HTTP 上报）**
```diff
- import json, urllib.request, time
- _p = '.dbg/roi-surface-misalignment.env'
+ # 调试上报默认关闭；仅 SSDVR_DEBUG_TELEMETRY=1 时启用
+ if os.environ.get("SSDVR_DEBUG_TELEMETRY") == "1":
+     import json, urllib.request, time
+     ...
```

**3D SAM 完成/失败事件（补桥所需事件）**
```python
  def _sam_on_finished(self, mask):
      self.sam_busy = False
+     self._mcp_push("roi_done", {"voxels": int(mask.sum()) if mask is not None else 0,
+                                 "source": "3d_sam", "path": ...})
```
（`_sam_on_error` 同理推 `roi_error`。）

### 4.2 `mcp_ssd_vr/gui_bridge.py`

**`collect_state()`：从"自动ROI 时代"改成新 UI 分块**
```python
  "tabs"/"tab_count"/"current_tab"/"current_tab_index"/"right_area_visible"/"splitter_sizes",
  "modality"/"is_2d",
  "mpr"       -> 窗宽窗位/工具/提示点/SAM 结果/测量条数/模型状态,
  "roi_saved" -> 已保存 ROI 记录（CSV 缓存）,
  "archive"   -> 档案册数量/选中/详情目录/右栈页码,
  "settings"  -> config_path/key_set/base_url/model/temperature（**不返回 Key 明文**）,
  "pacs"      -> 节点/状态/检查数/序列数/busy,
  "case"      -> 模板路径/字段数/已填数,
  "ai"        -> key_set/model/autosave/输出片段,
  "pets"      -> 扫描根/爱宠数/序列数,
  # 旧键保留但改为诚实映射：
  "roi_task" = "sam3d", "roi_running" = sam_busy, "roi_results_count" = 已保存 ROI 数,
  "legacy_auto_roi_removed" = True,
```

**`_NoDialogs`（新增）**：桥的 op 在主线程执行，GUI 里任何 `QMessageBox`（删除确认/无 mask 提示）
都会把整个桥卡到超时。现在在危险 op（SAM 载入/删除、档案打开、设置保存、PACS）外层统一把
`question→Yes`、`information/warning/critical→Ok`，退出时还原。

**`_util.call()`：有效 ok 传播**
```python
  effective = bool(ok)
  if effective and isinstance(data, dict) and data.get("ok") is False:
      effective = False     # op 级参数校验失败不再被记成"成功"
```

**`take_screenshot()`**：新增 `switch_to_render` 与 `vtk_visible`/`hint`——
右侧渲染区在爱宠列表/MPR/设置页是隐藏的，此时 VTK 窗口只有几十像素高（实测 400×38），
截出来是空白；现在会明确告知并可按需先切到「渲染」页。

## 5. 验证证据

| 验证 | 结果 |
|------|------|
| `py_compile`（viewer + bridge + tools + state） | 全部 OK |
| bridge 端到端自检 `temp/bridge_selftest.py` | **100 项 / 0 失败**（真实启动带 `--mcp` 的 GUI，走 TCP 桥） |
| 工具注册静态检查 `temp/tools_static_check.py` | 7 个模块、**62 个工具、无重名**、26 个新工具齐备 |
| 缺陷修复验证（离屏） | AI 子页 index=2；`on_mode_change(11)`→`dual_volume`（`er_core=None` 时）；VR 标签=滑块值；`urlopen` 仅剩 1 处且被 env 门控 |
| **live MCP server**（opencode 的 `ssd_vr`，跑在 `I:\SSD+VR_github`） | 已连上本程序 GUI(7799)，`ssdvr_state_snapshot()` 返回新状态分块；`ssdvr_list_roi_blocks()`/`ssdvr_trigger_roi()`/`ssdvr_screenshot()` 均按新语义返回 |

## 6. 排查结论：死代码 / 已知不一致（**未改**，仅记录）

| 项 | 位置 | 说明 |
|----|------|------|
| `pick_dir` / `pick_file` | 3201/3207 | 无任何调用者（旧"选文件"按钮遗留） |
| `_patient_sync_from_render_path` | 4819 | 未被调用 |
| `_restore_vr_pixels` / `_apply_roi_pixel_replacement` | 6366/6373 | 自动ROI 遗留，已无调用者（后者内部还有一处 127.0.0.1:7777 上报） |
| `_update_vr_transfer_functions` | 6522 | 未被调用（bridge 的 `apply_preset` 仍会调它，因此保留） |
| `self.seg_pipeline/seg_visualizer/seg_result`、`right_volume/producer/mapper` | 1699+ | 只赋 None、从不读取 |
| `use_2d_tf_bone` | 1699 | 永远 False → `_build_2d_tf_lut_bone_mono` 分支不可达 |
| 过期注释 | 2176「3 个页签」、2451「PACS 取片页是顶层页」 | 与实际（5 页签、PACS 是爱宠列表子页）不符 |
| `insertTab(1, ...)` | 7309 | MPR 页用固定下标 1 插入，脆弱（其余处已用 `_tab_index_by_text`） |
| ROI CSV 里的 `mask_npy` | `mcp_records/roi_summary_3dsam.csv` | 5 条记录里的 mask 路径仍指向 `I:\SSD+VR_github\...`（历史迁移残留，本机可用，换机即失效） |

## 7. 重要发现：MCP 配置指向的是另一个仓库

opencode 的 `ssd_vr` MCP server 启动命令为
`D:\python\envs\mar\python.exe I:\SSD+VR_github\mcp_ssd_vr\server.py`
——即 **`I:\animal_dicom` 是本仓库的完整副本，但 agent 用的 MCP 服务来自原仓库**。
本轮正因为桥的发现机制（`%LOCALAPPDATA%\SSD_VR_MCP\bridge.json` + 7799-7898 扫描）
才能自动连上本程序。要让 agent 用到本仓库新增的 26 个工具，需要把配置指到
`I:\animal_dicom\mcp_ssd_vr\server.py`（改完需重启 opencode 才会重新加载 MCP）。

## 8. 下一轮建议

1. 决定 MCP 配置指向哪个仓库（见第 7 节）。
2. 清理死代码与过期注释（第 6 节），或明确标注为"历史保留"。
3. PACS 联调：`pacs_client.py` 已就绪但未对真实节点做过 C-ECHO/C-FIND/C-GET。
4. ROI CSV 的 `mask_npy` 改为相对路径（跟随 `record_dir()` 解析），避免换机失效。

---

## 历史轮次
<details>
<summary>Round R-35 — 2026-09-25 | 爱宠列表页取消右侧显示区 + 首屏状态修正 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 35 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：**「爱宠列表」页右侧的显示框也删除**。本轮把「爱宠列表」并入**占满整窗**分支（与 MPR / 设置同处理）：切到爱宠列表页时隐藏右侧区域，让病例/序列树独占整窗宽度。同时修复一个隐藏问题：**首个页签在启动时不会触发 `currentChanged`**，导致初始页（爱宠列表）右侧区域仍然显示 —— 在 `_build_ui` 末尾主动调用一次 `_on_tab_changed(pages.currentIndex())` 应用初始状态。现右区显隐规则：爱宠列表 / MPR / 设置 = 隐藏；渲染 / 档案中心 = 显示。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 合并分支 | ✅ | 爱宠列表 → 隐藏右区 |
| 2 | 首屏修复 | ✅ | 启动时主动应用初始页签状态 |
| 3 | 验证 | ✅ | 五页签显隐全部正确 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_on_tab_changed`：`is_reader` 增加「爱宠」；`_build_ui` 末尾主动调用一次 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_on_tab_changed()`**
```diff
- is_reader = title.startswith("MPR") or title.startswith("设置")
+ is_reader = (title.startswith("MPR") or title.startswith("设置")
+              or title.startswith("爱宠"))
```

**`_build_ui()` 末尾** — 首屏应用初始状态
```python
+ try:
+     self._on_tab_changed(self.pages.currentIndex())
+ except Exception:
+     pass
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 爱宠列表布局 | 隐藏右区、独占整窗 | 用户要求；病例树越宽越好 |
| 首屏不生效 | 启动时主动调一次 `_on_tab_changed` | Qt 首个页签不触发 currentChanged |
| 右区显隐规则 | 列表/MPR/设置=隐藏；渲染/档案中心=显示 | 与各页内容匹配 |

## 6. 遗留问题与待办

- [ ] 无。

## 7. 下一轮建议

1. 切换五个页签确认右区显隐符合预期。
2. 可选：给 PACS 取片页也确认右区策略（当前显示渲染区）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 验证 | 首屏（爱宠列表）右区未隐藏：首个页签不触发 currentChanged | ✅ 启动时主动应用后修复 |

---

---

## 历史轮次
<details>
<summary>Round R-34 — 2026-09-25 | 「设置」页取消右侧显示区 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 34 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：**「设置」页右侧的显示区删除掉**。本轮在 `_on_tab_changed` 中把「设置」页并入**占满整窗**分支（与 MPR 阅片同处理）：切到设置页时**隐藏整个右侧区域**（`right_box` 不可见）并把 `splitter` 设为 `[1, 0]`，让设置表单独占整窗宽度。渲染页 / 档案中心页仍照旧显示右侧区域。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 合并分支 | ✅ | 设置页 → 与 MPR 同「隐藏右区」 |
| 2 | 验证 | ✅ | settings 右区隐藏、宽度全给左栏 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_on_tab_changed`：`is_reader` 增加「设置」判断 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_on_tab_changed()`**
```diff
- is_reader = title.startswith("MPR")
+ # 设置页与 MPR 页都不需要右侧渲染区 → 占满整窗
+ is_reader = title.startswith("MPR") or title.startswith("设置")
  is_arch = title.startswith("档案")
```
（`is_reader` 分支已负责：`right_box.setVisible(False)` + `splitter.setSizes([1, 0])`。）

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 设置页布局 | 与 MPR 同：隐藏右区、独占整窗 | 配置表单不需要 3D 渲染区，宽屏更易读 |
| 复用方式 | 仅扩展 `is_reader` 条件 | 一处改动，行为一致（含截图按钮自动隐藏） |

## 6. 遗留问题与待办

- [ ] 无。

## 7. 下一轮建议

1. 切到「设置」页确认右侧不再有渲染区、表单占满宽度。
2. 可选：把设置页做成两栏（左键配置 / 右说明文档）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 编辑 | 首次 Edit 未命中（文件中该行用 `\uXXXX` 转义存放），改为只替换纯 ASCII 的 `is_reader` 行 | ✅ 已修复 |

---

---

## 历史轮次
<details>
<summary>Round R-33 — 2026-09-25 | 新增顶层「设置」页配置 DeepSeek API Key (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 33 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：**增加 API KEY 配置页面**。本轮新增顶层页签 **「设置」**（位于「档案中心」右侧）：DeepSeek API 配置表单——**API Key**（密码模式，可勾选"显示"）、**Base URL**、**模型**（可编辑下拉：`deepseek-chat` / `deepseek-reasoner`）、**温度**（0~2），以及 **保存配置 / 测试连接 / 清除 Key** 三个按钮与状态行（显示配置文件路径）。配置写入项目根 `deepseek_config.json`（已 gitignore）。病例详情页「③ AI 引导补全」里的「配置 API Key」按钮改为**跳转到「设置」页**（不再弹输入框）。「测试连接」用 `AiWorker` 后台发送 `ping`，显示成功/失败。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 设置页 | ✅ | Key/Base URL/模型/温度 |
| 2 | 保存/清除 | ✅ | 写 deepseek_config.json |
| 3 | 测试连接 | ✅ | AiWorker 后台 ping |
| 4 | 入口联动 | ✅ | AI 页按钮 → 跳设置页 |
| 5 | 验证 | ✅ | 页签/保存/重载/状态/跳转/清除 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 新增 `_build_settings_tab` / `_settings_load/save/clear_key/test`；`_ai_set_key` 改为跳转设置页 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()`** — 新增页面
```python
+ self._build_settings_tab()      # 在 _build_archive_tab() 之后
```

**新增 `_build_settings_tab()`**
```python
+ 分组「DeepSeek API」：set_key(密码/显示切换) / set_base / set_model(可编辑) / set_temp
+ [保存配置][测试连接][清除 Key] + set_status
+ self.pages.addTab(page, "设置"); self._settings_load()
```

**`_settings_save()` / `_settings_clear_key()` / `_settings_test()`**
```python
+ cfg.update({api_key, base_url, model, temperature}) → deepseek_client.save_config(...)
+ clear: cfg["api_key"]="" → 保存
+ test: AiWorker(cfg, [{"role":"user","content":"ping"}]) → 成功/失败状态
```

**`_ai_set_key()`** — 由输入框改为跳页
```diff
- key, ok = QInputDialog.getText(...); save_config(...)
+ i = self._tab_index_by_text("设置"); self.pages.setCurrentIndex(i)
+ self._settings_load(); self.set_status.setText("在此填写 API Key，然后点「保存配置」。")
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 页面形态 | 顶层页签「设置」 | "配置页面"最直观、随时可达 |
| Key 呈现 | 密码模式 + 「显示」开关 | 默认不裸显，需要时可核对 |
| 模型 | 可编辑下拉（chat/reasoner） | 兼顾预设与自定义 |
| 测试连接 | 后台线程 ping | 不阻塞界面；直接验证 Key 有效性 |
| AI 页入口 | 跳转到设置页 | 单一配置入口，避免两处输入 |

## 6. 遗留问题与待办

- [ ] 未做 Key 加密存储（明文 JSON，但已 gitignore；如需可接系统凭据管理器）。
- [ ] 联网推理/测试仍需用户自备 Key（本机未配，路径已覆盖失败分支）。

## 7. 下一轮建议

1. 打开「设置」页粘贴 DeepSeek API Key → 保存 → 测试连接（应显示"连接成功"）。
2. 之后到「档案中心 → 病例详情 → ③ AI 引导补全」点「AI 引导补全」实测。
3. 可选：Key 加密存储、模型列表从 `/models` 动态拉取。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-32 — 2026-09-25 | 嵌入 DeepSeek 引导式补全（读取全部时间戳版本）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 32 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：把 **DeepSeek API** 服务嵌入病例信息；在"病历档案基本信息"之外的字段采用**引导式补全**逻辑，推理由 LLM API 提供；且 **LLM 自动读取全部时间戳版本**作为上下文。本轮：① 新建 `deepseek_client.py`（标准库 urllib 调 `/chat/completions`，零第三方依赖）；② 病例详情页新增**子页 ③「AI 引导补全」**：配置 API Key（存 `deepseek_config.json`，或读环境变量 `DEEPSEEK_API_KEY`）、指令输入、`AI 引导补全`、`应用建议到表单`（可选**应用后自动保存**生成时间戳版本）、只读输出框；③ `_ai_context()` 组装上下文：**24 个模板字段 + 当前填写值 + 全部 revisions 时间戳版本 + 档案基本信息**；④ 模型被要求**只输出 JSON**，解析后一键写回表单；⑤ AI 应用记录写入 `meta.ai_log`。API Key 文件已加入 `.gitignore`。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | API 客户端 | ✅ | deepseek_client.py（urllib，无依赖） |
| 2 | AI 子页 | ✅ | Key 配置 / 指令 / 运行 / 应用 / 自动保存 |
| 3 | 上下文 | ✅ | 模板字段 + 当前值 + **全部时间戳版本** + 基本信息 |
| 4 | JSON 回填 | ✅ | 只输出 JSON → 解析 → 写回表单 |
| 5 | 留痕 | ✅ | meta.ai_log |
| 6 | 验证 | ✅ | 客户端/配置/上下文/解析/应用/自动存版本 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `deepseek_client.py` | 新建 | DeepSeek /chat/completions 客户端 + 配置读写 |
| `ssd_vr_viewer.py` | 修改 | 导入 deepseek_client；`AiWorker`；子页③ UI；`_ai_*` 方法 |
| `.gitignore` | 修改 | 忽略 `deepseek_config.json`（含密钥） |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `deepseek_client.py`（新建）
```python
+ DEFAULT_BASE_URL = "https://api.deepseek.com"; DEFAULT_MODEL = "deepseek-chat"
+ load_config(path) / save_config(path, cfg)      # 文件优先，回退环境变量 DEEPSEEK_API_KEY
+ chat(cfg, messages, timeout=150) -> str          # POST /chat/completions，HTTPError → DeepSeekError
```

### 文件: `ssd_vr_viewer.py`

**`AiWorker(QThread)`** — 后台推理
```python
+ done = Signal(str); failed = Signal(str)
+ run(): self.done.emit(deepseek_client.chat(self.cfg, self.messages))
```

**详情页子页 ③** — AI 引导补全
```python
+ self.detail_sub_ai = QPushButton("③ AI 引导补全")   # 加入子页切换条
+ ai_status_lb / [配置 API Key][AI 引导补全][应用建议到表单]
+ ai_prompt（指令输入） + ai_autosave（应用后自动保存）
+ ai_out（只读输出）
```

**`_ai_run()`** — 组装上下文并请求
```python
+ ctx = self._ai_context()          # template_fields / current / history_versions / case_basic
+ sys_msg = "只输出 JSON，键为字段 key…缺失写“待确认”"
+ user_msg = json.dumps({instruction, template_fields, current, history_versions, case_basic})
+ AiWorker(cfg, [system, user]).start()
```

**`_ai_apply()`** — 回填 + 留痕 + 自动存版本
```python
+ 逐字段写回 case_fields（空值跳过）
+ meta.ai_log.append({time, instruction, applied_keys})
+ if ai_autosave: self._arch_detail_save_clinical()   # 生成时间戳版本
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 依赖 | 标准库 urllib（不引入 requests/openai SDK） | 免新增依赖，冻结版易打包 |
| 密钥存放 | deepseek_config.json + 环境变量回退 | 二选一；文件已 gitignore |
| LLM 输出 | 强制 JSON（键=字段 key） | 可程序化"一键应用"，避免解析自然语言 |
| 上下文 | 模板 + 当前值 + **全部时间戳版本** | 满足"LLM 自动读取所有时间戳病例信息" |
| 应用后 | 可选自动保存 → 生成新时间戳版本 | 与历史版本子页闭环，可回溯 |
| 线程 | QThread（AiWorker） | 网络请求不阻塞 GUI |

## 6. 遗留问题与待办

- [ ] 未做流式输出（stream=False，长回复需等待）。
- [ ] 未对历史版本数量做上限/裁剪（上下文会随版本增多而变大）。
- [ ] 需用户自备 DeepSeek API Key 才能实测联网推理（本机未配 Key，仅验证了离线路径）。

## 7. 下一轮建议

1. 在「档案中心 → 病例详情 → ③ AI 引导补全」点「配置 API Key」填入 Key，然后「AI 引导补全」→「应用建议到表单」。
2. 可选：流式输出、上下文版本裁剪、把 AI 建议也存成一个独立的时间戳版本。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误（未配 Key，联网路径未实测） | - |

---

---

## 历史轮次
<details>
<summary>Round R-31 — 2026-09-25 | 病历信息加时间戳版本子页（revisions）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 31 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：病例信息（模板）**增加时间戳子页面**，以便**保存每次更新的内容**。本轮把病例详情页的正文改为**两个子页面**：**① 当前病例信息**（可编辑模板表单 + 保存病例信息）、**② 历史版本（时间戳）**（版本列表 + 只读预览 + 恢复此版本）。每次点「保存病例信息」都会：更新 `meta.json` 的 `clinical`，并在 `<档案目录>/revisions/<YYYYmmdd_HHMMSS>.json` 写入一份**时间戳快照**（含 time + clinical），同时把版本登记进 `meta.revisions[]`。历史版本子页可浏览每个时间戳的内容并**一键恢复**（恢复后需再保存才生效，从而形成新的版本）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 子页结构 | ✅ | ① 当前病例信息 ② 历史版本 |
| 2 | 版本快照 | ✅ | 每次保存写 revisions/<stamp>.json |
| 3 | 版本浏览 | ✅ | 时间戳列表 + 排版预览 |
| 4 | 恢复 | ✅ | 回填表单 → 保存即新版本 |
| 5 | 验证 | ✅ | 2 版 → 预览/恢复 → 第 3 版 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 详情页加子页切换与堆叠；保存时生成时间戳版本；`_arch_detail_load_history/_hist_selected/_restore`；`_case_text_from_values` |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**详情页（`_build_archive_tab`）** — 子页切换 + 堆叠
```diff
- self._build_case_form(dl); [保存病例信息]; self.detail_status
+ self.detail_sub_cur  = QPushButton("① 当前病例信息")
+ self.detail_sub_hist = QPushButton("② 历史版本（时间戳）")     # 互斥
+ self.detail_stack = QStackedWidget()
+   子页①：_build_case_form(_al) + detail_save + ...
+   子页②：detail_hist_list(最大高150) + detail_hist_view(只读) + [刷新版本][恢复此版本]
+ self.detail_sub_cur.setChecked(True)
+ 子页② 选中时自动 _arch_detail_load_history()
```

**`_arch_detail_save_clinical()`** — 每次保存即存版本
```python
+ import time as _t
  meta["clinical"] = self._case_form_values()
+ stamp = _t.strftime("%Y%m%d_%H%M%S"); rev_name = stamp + ".json"（重名加 _n）
+ json.dump({"time": ..., "clinical": meta["clinical"]}, revisions/rev_name)
+ meta["revisions"].append({"file": rev_name, "time": ...})
  写回 meta.json；状态显示「已存版本 …」；刷新历史列表
```

**新增方法**
```python
+ _case_text_from_values(vals)     # 按模板顺序排版预览文本
+ _arch_detail_load_history()      # 扫描 revisions/ → 列表（时间戳倒序）
+ _arch_detail_hist_selected()     # 选中 → 只读预览
+ _arch_detail_restore()           # 回填表单 + 切回子页① + 提示再保存
```

**`_archive_open_detail()`**：打开档案时回到子页①并载入历史版本。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 版本触发 | **每次保存病例信息**自动建版本 | 满足"保存每次更新的内容"，无需手动另存 |
| 存储位置 | `<档案>/revisions/<时间戳>.json` | 与档案同目录，随档迁移；文件名即时间戳 |
| 恢复语义 | 回填表单但**不自动保存** | 让用户确认后再落盘，避免误覆盖 |
| 索引 | `meta.revisions[]` 同步登记 | 打开详情后无需扫盘即可展示 |

## 6. 遗留问题与待办

- [ ] 版本未做数量上限/清理策略（长期使用会积累）。
- [ ] 版本差异对比（diff）未实现。

## 7. 下一轮建议

1. 真机：打开档案 → 改字段 → 保存两次 → 切「② 历史版本」看两条时间戳 → 选旧版「恢复此版本」→ 保存。
2. 可选：版本 diff 高亮、版本上限（如保留最近 50 版）、导出病例报告。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | `libpng Invalid IHDR`（offscreen VTK 抓图），有回退 | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-30 — 2026-09-25 | 渲染/MPR 视图右上角「保存到档案（截图）」 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 30 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：在 **MPR 阅片视图** 与 **渲染页面视图** 的**右上角**各加一个「**保存到档案（截图）**」按钮，点击后**截图当前显示状态**并保存到**对应病例档案**（病例信息模板所在档案）。本轮：① 右侧大区域外包一层容器，顶部右对齐放按钮（渲染视图右上角）；② MPR 页顶部右对齐放同名按钮；③ 新增 `_save_shot_to_archive(kind)`：定位当前序列对应的档案（详情页打开的 → 同 Sequence 最新 → **自动新建** → 提示），把截图存为 `shot_render_*.png` / `shot_mpr_*.png`，并在档案 `meta.json` 追加 `shots[]`（file/time/kind）；若档案尚无 `render.png`，渲染截图同时兼作封面；④ 病例详情页新增「截图：共 N 张 ｜ 最近 …」显示。页签切换时按钮自动显隐（MPR / 档案中心页不显示渲染按钮）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 渲染视图按钮 | ✅ | 右区顶部工具条（右上角） |
| 2 | MPR 视图按钮 | ✅ | MPR 页顶部右对齐 |
| 3 | 截图入库 | ✅ | shots[] + 自动建档 + 封面兼用 |
| 4 | 详情显示 | ✅ | 「共 N 张」计数 |
| 5 | 验证 | ✅ | 两按钮、自动建档、双截图落盘、计数 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 右区容器+工具条按钮；MPR 页按钮；`_save_shot_to_archive` / `_archive_target_for_current` / `_newest_archive_dir` / `_detail_shots_text`；详情页截图计数 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()` 右区** — 顶部右对齐按钮
```diff
- self.splitter.addWidget(self.right_stack)
+ self.right_box = QWidget(); _rb = QVBoxLayout(self.right_box)
+ _tb = QHBoxLayout(); _tb.addStretch(1)
+ self.btn_shot_render = CButton(..., text="保存到档案（截图）",
+                                command=lambda: self._save_shot_to_archive("render"))
+ _tb.addWidget(self.btn_shot_render)
+ _rb.addLayout(_tb); _rb.addWidget(self.right_stack, 1)
+ self.splitter.addWidget(self.right_box)
```

**`_on_tab_changed()`** — 按钮随页签显隐
```python
+ _container = getattr(self, "right_box", self.right_stack)
+ _container.setVisible(not is_reader)
+ self.btn_shot_render.setVisible(not (is_reader or is_arch))
```

**`_build_sam_tab()`** — MPR 右上角按钮
```python
+ _mpr_top = QHBoxLayout(); _mpr_top.addStretch(1)
+ self.btn_shot_mpr = CButton(..., command=lambda: self._save_shot_to_archive("mpr"))
+ _mpr_top.addWidget(self.btn_shot_mpr); sam_layout.addLayout(_mpr_top)
```

**新增 `_save_shot_to_archive(kind)`**
```python
+ d = self._archive_target_for_current()      # 详情页档 → 同序列档 → 自动新建
+ fn = "shot_%s_%s.png" % (kind, ts)
+ p = self._archive_grab_mpr(path) if kind == "mpr" else self._archive_grab_render(path)
+ meta["shots"].append({"file": fn, "time": ..., "kind": kind}); 写回 meta.json
+ if kind == "render" and 无 render.png: 兼作封面
+ 状态栏提示 + 刷新档案册 + 详情页刷新计数
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 按钮位置 | 渲染：右区顶部右对齐；MPR：页顶右对齐 | 两个视图各不相同，避免覆盖在 VTK 原生窗口上 |
| 无对应档案时 | **自动新建**该序列档案再存图 | 一键可用，不用先去建档 |
| 截图归属 | 档案 `meta.json` 的 `shots[]` | 与病例信息同档，载入详情即可见 |
| 封面 | 渲染截图在无封面时兼作 `render.png` | 顺手补齐卷宗封面 |

## 6. 遗留问题与待办

- [ ] 详情页仅显示截图计数，未列出缩略图墙（可后续补）。
- [ ] 未做截图删除/重命名。

## 7. 下一轮建议

1. 真机：渲染页调好视角 → 右上「保存到档案（截图）」；切 MPR 再截一张；在档案详情看「共 2 张」。
2. 可选：详情页加截图画廊（点开大图）、或导出病例报告含截图。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | `libpng Invalid IHDR`（offscreen VTK 抓图），已回退抓控件 | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-29 — 2026-09-25 | 点击案例进入右侧「病历信息」详情页 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 29 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户纠正显示方式：**点击案例应进入一个新界面，占据右侧显示区，显示该案例的病例信息**（此前病例信息表单挤在左侧参数栏）。本轮把右侧大区域从 2 页扩为 **3 页**：`0 = 3D 渲染视图`、`1 = 档案册网格`、`2 = 病例信息详情页`。在档案册里**单击**某档案 → 右侧进入详情页：显示档案标题、爱宠/序列/模态/时间、**render.png 与 mpr.png 两张缩略图**、以及按模板生成的**可编辑病例信息表单**；详情页有「← 返回档案册」「载入该档案的参数」「保存病例信息」。病历表单从左侧参数栏**移除**（不再占参数栏）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 右区第 3 页 | ✅ | 病例信息详情页加入 right_stack |
| 2 | 单击进入 | ✅ | `itemClicked → _archive_open_detail` |
| 3 | 详情内容 | ✅ | 标题/身份/双缩略图/模板表单 |
| 4 | 操作 | ✅ | 返回档案册 / 保存病例信息 / 载入参数 |
| 5 | 左侧瘦身 | ✅ | 从参数栏移除病例表单 |
| 6 | 验证 | ✅ | 3 页、进入/返回、保存生效 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | right_stack 加详情页；`_archive_open_detail/_arch_detail_back/_arch_detail_save_clinical/_arch_detail_apply_params`；左栏移除表单；单击进入 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_archive_tab()`**
```diff
- # 病例信息（按模板生成，可滚动）  ← 从左侧参数栏移除
- self._build_case_form(outer)
+ # ---- 右：档案详情（病例信息）----
+ self.detail_back / self.detail_apply
+ self.detail_title / self.detail_case
+ self.detail_thumb1(render.png) / self.detail_thumb2(mpr.png)   320x210
+ self._build_case_form(dl)          # 模板表单（24 字段）放进详情页
+ self.detail_save → _arch_detail_save_clinical
+ self.detail_status
+ self.arch_detail_host = detail; self.right_stack.addWidget(detail)
```

**单击档案进入详情**
```python
+ self.arch_grid.itemClicked.connect(self._archive_open_detail)
  self.arch_grid.itemDoubleClicked.connect(lambda _it: self._archive_load_selected())
```

**新增方法**
```python
+ _archive_open_detail(item)   # 读 meta → 标题/身份/缩略图/表单 → right_stack 切到详情页
+ _arch_detail_back()          # 回到档案册
+ _arch_detail_save_clinical() # 表单 → 写回该档案 meta.json 的 clinical
+ _arch_detail_apply_params()  # 载入该档案的渲染/MPR 参数（demo 给演示提示）
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 详情页位置 | 右侧大区域（right_stack 第 3 页） | 用户明确：占右侧显示区 |
| 触发展开 | **单击**即进入 | 与"点开卷宗"的直觉一致 |
| 双击 | 仍为直接载入参数 | 快速套用上次阅片状态 |
| 表单位置 | 详情页内（可滚动、可编辑） | 不再挤占左侧参数栏 |
| 缩略图 | render.png + mpr.png 并排 | 一眼看到渲染与阅片结果 |

## 6. 遗留问题与待办

- [ ] 缩略图不可点击放大（如需可加弹出预览）。
- [ ] 病例信息仅存 JSON，尚未导出 PDF/Word 报告。

## 7. 下一轮建议

1. 打开「档案中心」→ 单击档案 → 右侧看病例信息；改字段 → 保存病例信息；返回档案册。
2. 可选：详情页加「导出病例报告（含两张截图）」。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | `libpng Invalid IHDR`（offscreen VTK 抓图），有回退 | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-28 — 2026-09-25 | 宠物病历档案默认模板（Signalment+SOAP+影像）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 28 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求"自行搜索宠物的医学病例模板，作为档案的默认模板"。本轮先尝试联网检索（DuckDuckGo / Bing / AVMA / Wikipedia），**本机网络受限（Transport error / Incapsula 拦截 / 搜索引擎返回无关结果）**，遂按兽医病历通行标准自建模板：**Signalment（动物与主人）→ Subjective（病史）→ Objective（体格检查）→ Imaging（影像检查：技术参数/所见）→ Assessment/Plan（评估・诊断・偶然发现・建议）**，共 **5 段 24 字段**。模板写入项目根 `case_template.json`（可用户编辑覆盖），档案中心左栏新增「**病例信息（模板）**」可滚动表单（含「清空表单」「填入示例病例」），保存档案时连同 `clinical` 一起落盘、载入档案时回填；常驻「默认案例」预填一份示例病例（金毛犬髋关节 CT）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 联网检索 | ⚠️ 受限 | 网络被拦截/返回无关，改用领域标准 |
| 2 | 模板设计 | ✅ | 5 段 24 字段（Signalment/SOAP/影像） |
| 3 | 模板文件 | ✅ | `case_template.json`（不存在则写入默认） |
| 4 | 表单与持久化 | ✅ | 新建/清空/示例；随档案 save/load |
| 5 | 验证 | ✅ | 24 字段、保存回填、默认案例示例 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 新增 `DEFAULT_CASE_TEMPLATE` / `DEMO_CASE_CLINICAL`；`_case_template/_build_case_form/_case_form_values/_case_form_set/_case_form_clear`；档案 save/apply 带 `clinical`；默认案例预填 |
| `case_template.json` | 新建 | 默认病例档案模板（可编辑） |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**模块级常量**
```python
+ DEFAULT_CASE_TEMPLATE = {"version":1, "title":"宠物影像病例档案模板",
+   "sections":[
+     "① 动物与主人 (Signalment)" -> case_no/owner/species/breed/sex/age/weight/date
+     "② 病史 (Subjective)"        -> cc/history/meds
+     "③ 体格检查 (Objective)"     -> pe_vitals/pe_exam
+     "④ 影像检查 (Imaging)"       -> modality/region/contrast/anesthesia/technique/findings
+     "⑤ 评估与计划 (Assessment/Plan)" -> assessment/diagnosis/incidental/recommend/vet ]}
+ DEMO_CASE_CLINICAL = {...金毛犬髋关节 CT 示例...}
```

**新增模板/表单方法**
```python
+ _case_template_path()   # <root>/case_template.json
+ _case_template()        # 读取；不存在则写默认；坏文件回退
+ _build_case_form(layout)  # 按模板生成可滚动的表单 + [清空表单][填入示例病例]
+ _case_form_values() / _case_form_set(vals) / _case_form_clear()
```

**档案中心左栏**
```python
+ self._build_case_form(outer)     # 加在「选中档案」之后、状态之前，占剩余空间
```

**保存 / 载入**
```diff
  meta = {..., "case": info,
+         "clinical": self._case_form_values(),
          "render": ..., "mpr": ...}
...
  def _archive_apply(self, meta):
+     self._case_form_set(meta.get("clinical") or {})
```

**默认案例**：`meta["clinical"] = dict(DEMO_CASE_CLINICAL)`；老 demo 缺 `clinical` 时自动补。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 模板来源 | 联网失败 → 按兽医病历通行标准自建 | 网络受限；标准结构权威且稳定 |
| 模板落地 | 项目根 `case_template.json` | 用户可自行编辑/扩展字段，无需改代码 |
| 表单位置 | 档案中心左栏（可滚动） | 与"保存当前档案"同屏操作 |
| 病例信息存法 | 随档案 `meta["clinical"]` | 一档一病例，载入即回填 |

## 6. 遗留问题与待办

- [ ] 未能联网核对具体机构模板；字段可按需在 `case_template.json` 增删（改后重启生效）。
- [ ] 未做病例信息导出（PDF/Word 报告）与打印。

## 7. 下一轮建议

1. 打开「档案中心」→ 看「病例信息（模板）」；点「填入示例病例」→ 保存 → 换空档 → 载入验证回填。
2. 可选：一键导出「病例档案报告」（模板 + 两张截图）；或支持字段类型扩展（下拉/日期/数值）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 联网 | duckduckgo Transport error；AVMA 被 Incapsula 拦截；Bing 返回无关结果 | ⚠️ 改用领域标准自建模板 |

---

---

## 历史轮次
<details>
<summary>Round R-27 — 2026-09-25 | 封面合成到文件夹图标 + 默认案例 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 27 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：① `render.png` 要**贴在文件夹图标上**作封面（卷宗观感），而不是替换文件夹；② **增加一个默认案例**。本轮实现：封面改为 **QPainter 合成**——取系统文件夹图标（128×128）为底，把 `render.png` 等比裁剪成 92×62 的"照片"贴到文件夹正面（带 2px 深色边框），生成新 `QIcon`；无 `render.png` 时回退纯文件夹图标。并新增**常驻「默认案例」**：`_archive_ensure_demo()` 在档案册刷新时确保存在 `archives/0000_DEFAULT_DEMO/`（`meta.json` 标 `demo:true` + 用 QPainter 现场生成的示例封面 `render.png`）；该案例**置顶**显示、**禁止删除**、载入时提示"仅演示"。网格图标尺寸改为方形 136×136（格 198×200）以容纳文件夹+封面。离屏测试与合成图标目视确认均通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 封面合成 | ✅ | 文件夹 + render.png 缩略图（QPainter） |
| 2 | 默认案例 | ✅ | 自动生成 meta + 示例封面 |
| 3 | 保护与排序 | ✅ | 置顶、禁止删除、载入提示 |
| 4 | 图标尺寸 | ✅ | 136×136 / 198×200（方形） |
| 5 | 验证 | ✅ | 断言 + 目视合成图 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 新增 `_archive_ensure_demo()` / `_archive_icon()`；refresh 排序与图标；删除保护；载入提示 |
| `mcp_records/archives/0000_DEFAULT_DEMO/` | 新增（运行时） | 默认案例（meta.json + 示例 render.png） |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增 `_archive_ensure_demo()`** — 常驻默认案例
```python
+ _DEMO_ID = "0000_DEFAULT_DEMO"
+ def _archive_ensure_demo(self):
+     d = os.path.join(self._archive_root(), self._DEMO_ID)
+     if os.path.isfile(os.path.join(d, "meta.json")): return
+     meta = {"id": ..., "name": "默认案例（示例）", "demo": True, "case": {...}, "render": {}, "mpr": {}}
+     json.dump(...);  # + QPainter 生成 480x320 示例封面 render.png
```

**新增 `_archive_icon(path)`** — 文件夹 + 封面合成
```python
+ folder = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
+ base = folder.pixmap(128, 128)
+ canvas = QPixmap(128, 128); canvas.fill(transparent)
+ p = QPainter(canvas); p.drawPixmap(0, 0, base)
+ scaled = photo.scaled(92, 62, KeepAspectRatioByExpanding, Smooth); center-crop
+ p.drawRect(x-1, y-1, w+2, h+2)   # 边框
+ p.drawPixmap(x, y, scaled)       # 贴到文件夹正面
+ return QIcon(canvas)
```

**`_archive_refresh()`**
```diff
+ self._archive_ensure_demo()
  ...
+ is_demo = bool(meta.get("demo"))
+ rows.append((..., d, is_demo))
+ rows.sort(key=lambda r: (not r[5],))     # 默认案例置顶
+ it = QListWidgetItem(self._archive_icon(path), "%s\n%s" % (nm, tm))
+ it.setData(Qt.ItemDataRole.UserRole + 1, is_demo)
```

**`_archive_delete_selected()`** — 保护默认案例
```python
+ if it.data(Qt.ItemDataRole.UserRole + 1):
+     skipped += 1; continue
```

**`_archive_load_selected()`** — 演示提示
```python
+ if meta.get("demo"):
+     self.arch_status.setText("「默认案例」仅用于演示档案册外观，无实际参数可载入。")
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 封面呈现 | 合成在文件夹正面（不替换文件夹） | 用户明确"显示在文件夹图标上"，更像卷宗 |
| 图片来源 | 档案自带的 render.png | 已有截图即封面，零额外成本 |
| 默认案例 | 常驻、置顶、禁删 | 保证档案册不空且有外观示例 |
| 示例封面 | QPainter 现场生成 | 无需外部资源，随包即可用 |

## 6. 遗留问题与待办

- [ ] 默认案例固定 id `0000_DEFAULT_DEMO`，若用户不想要可后续加"隐藏示例"开关。
- [ ] 封面每次刷新实时合成（档案多时略慢），可加缓存。

## 7. 下一轮建议

1. 打开「档案中心」查看文件夹+封面网格与置顶的默认案例。
2. 可选：双击封面弹出大图预览；或加"隐藏默认案例"开关。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | `libpng Invalid IHDR`（offscreen VTK 抓图），有回退 | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-26 — 2026-09-25 | 档案册网格放到右侧大区域，render.png 作封面 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 26 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：① 每个档案用其 `render.png` 作**封面缩略图**（"卷宗"观感）；② 档案网格要放在**参数栏右侧的大区域**，而不是挤在参数栏里。本轮把右侧区域改为 `QStackedWidget`（**0 = 3D 渲染视图，1 = 档案册网格**）：切到「档案中心」页签时右区显示**档案册**（占满右侧大区域），切到「MPR 阅片」时隐藏整块右区让阅片铺满，其余页签显示 VTK 渲染。档案网格放大为 **160×120 图标 / 200×190 格**，图标即该档案目录下的 `render.png`（等比缩放），无封面时回退系统文件夹图标。左侧页签只留操作栏（当前爱宠/档案名称/保存·刷新·打开目录/仅当前序列/选中提示/载入·删除/状态）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 右区改 QStackedWidget | ✅ | 0=VTK，1=档案册 |
| 2 | 页签三态 | ✅ | 渲染→VTK / 档案中心→档案册 / MPR→隐藏右区 |
| 3 | 封面缩略图 | ✅ | render.png → 160×120 图标，缺失回退文件夹 |
| 4 | 布局 | ✅ | 档案中心：左操作栏(360) + 右档案册(放大) |
| 5 | 验证 | ✅ | right_stack 2 页、三态、封面非空 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 右区包 QStackedWidget；`_build_archive_tab` 拆「左操作页 + 右档案册」；`_on_tab_changed` 三态；`_archive_refresh` 用 render.png 封面 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()`** — 右区改为堆叠
```diff
- self.vtk_widget = QVTKRenderWindowInteractor(central)
- self.splitter.addWidget(self.vtk_widget)
+ self.right_stack = QtWidgets.QStackedWidget()
+ self.vtk_widget = QVTKRenderWindowInteractor(central)
+ self.right_stack.addWidget(self.vtk_widget)
+ self.splitter.addWidget(self.right_stack)
```
并把 `_build_archive_tab()` 调用**移到 vtk_widget/right_stack 之后**。

**`_on_tab_changed()`** — 三态
```python
+ is_reader = title.startswith("MPR")
+ is_arch = title.startswith("档案")
+ if is_arch and hasattr(self, "arch_grid_host"):
+     self.right_stack.setCurrentWidget(self.arch_grid_host)
+ else:
+     self.right_stack.setCurrentWidget(self.vtk_widget)
+ self.right_stack.setVisible(not is_reader)
+ splitter: is_reader -> [1,0] ; is_arch -> [360,1400] ; else -> [480,1040]
```

**`_build_archive_tab()`** — 左操作页 + 右档案册
```python
+ self.pages.addTab(page, "档案中心")          # 左：操作栏
+ self.arch_grid: IconMode, iconSize 160x120, gridSize 200x190
+ self.arch_grid_host = host
+ self.right_stack.addWidget(host)             # 右：档案册
```

**`_archive_refresh()`** — 封面
```python
+ _icon = _folder
+ _png = os.path.join(path, "render.png")
+ if os.path.isfile(_png):
+     _pm = QPixmap(_png)
+     if not _pm.isNull():
+         _icon = QIcon(_pm.scaled(160, 120, KeepAspectRatio, SmoothTransformation))
+ it = QListWidgetItem(_icon, "%s\n%s" % (nm, tm))
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 网格位置 | 右侧大区域（QStackedWidget 第 1 页） | 用户明确：放参数栏右侧，别挤在参数栏 |
| 与 MPR 的冲突 | MPR 时隐藏整个右区 | MPR 需要整窗宽度阅片 |
| 封面来源 | 档案目录里的 `render.png` | 每档一张渲染截图，天然"卷宗封面" |
| 缺失回退 | 系统文件夹图标 | 老档案没有截图时仍可辨识 |

## 6. 遗留问题与待办

- [ ] 封面为实时读取磁盘，档案很多时刷新略慢（未做缓存）。
- [ ] `mpr.png` 也可作副封面（当前仅用 render.png）。

## 7. 下一轮建议

1. 真机：保存几条档案 → 切到「档案中心」看封面网格；双击封面载入。
2. 可选：双击封面弹出大图预览；或把 `mpr.png` 作为悬停预览。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | `libpng Invalid IHDR`（offscreen VTK 抓图），有回退 | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-25 — 2026-09-25 | 档案中心右侧改为文件夹形象 GRID (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 25 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：档案中心页面**右侧以 grid 形式显示各档案名称（文件夹形象）**。本轮把原先的 `QTableWidget` 列表改为**左操作栏 + 右文件夹网格**布局：左侧固定宽 340 的「爱宠档案」（当前爱宠 / 档案名称 / 保存·刷新·打开目录 / 仅显示当前序列）与「选中档案」（选中提示 / 载入·删除）；右侧为 `QListWidget` 的 **IconMode 网格**，每项用系统**文件夹图标**（`QStyle.SP_DirIcon`，64×64）+ 档案名称 + 时间，支持多选、**双击即载入**、选中即在左侧显示提示。刷新/载入/删除改为基于网格项（`UserRole` 存目录路径）。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 布局改版 | ✅ | 左操作栏 + 右网格 |
| 2 | 文件夹形象 | ✅ | QListWidget IconMode + SP_DirIcon 64px |
| 3 | 交互 | ✅ | 双击载入 / 多选 / 选中提示 |
| 4 | 逻辑迁移 | ✅ | refresh/load/delete 改走网格项 |
| 5 | 验证 | ✅ | IconMode/图标/3 项/选中/载入/删除 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_build_archive_tab` 改双栏+网格；refresh/load/delete 改网格项；新增 `_archive_selection_changed` |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_archive_tab()`** — 左操作栏 + 右文件夹网格
```python
+ body = QHBoxLayout()
+ left = QWidget(); left.setFixedWidth(340)   # 爱宠档案 / 选中档案 / 状态
+ self.arch_grid = QListWidget()
+ self.arch_grid.setViewMode(QListView.ViewMode.IconMode)
+ self.arch_grid.setIconSize(QSize(64, 64)); setGridSize(QSize(134, 122))
+ self.arch_grid.setResizeMode(Adjust); setMovement(Static); setWordWrap(True)
+ self.arch_grid.itemDoubleClicked -> self._archive_load_selected
+ self.arch_grid.itemSelectionChanged -> self._archive_selection_changed
+ body.addWidget(left); body.addWidget(right, 1)
```

**`_archive_refresh()`** — 填充文件夹网格
```diff
- t = self.arch_table; t.setRowCount(0); ... setItem(i, c, it)
+ self.arch_grid.clear()
+ _icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
+ for (tm, nm, pt, se, path) in rows:
+     it = QListWidgetItem(_icon, "%s\n%s" % (nm, tm))
+     it.setToolTip("名称/时间/爱宠/序列/目录")
+     it.setData(Qt.ItemDataRole.UserRole, path)
+     self.arch_grid.addItem(it)
```

**`_archive_load_selected()` / `_archive_delete_selected()`**
```diff
- rows = ...self.arch_table.selectionModel().selectedRows()
+ items = self.arch_grid.selectedItems()
+ d = items[0].data(QtCore.Qt.ItemDataRole.UserRole)   # 目录路径
```

**新增 `_archive_selection_changed()`** — 左侧显示选中提示。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 网格控件 | `QListWidget` IconMode | 天然的"文件夹网格"，支持多选/双击/自动重排 |
| 图标 | `QStyle.SP_DirIcon`（系统文件夹） | 标准、任何平台都有，符合"文件夹形象" |
| 载荷 | 项 `UserRole` 存目录路径 | 名称只是显示，操作以路径为准 |
| 双击 | 直接载入档案 | 与"双击打开文件夹"的直觉一致 |

## 6. 遗留问题与待办

- [ ] 图标为系统默认文件夹（可后续换成带缩略图的文件夹，用 `render.png` 做封面）。
- [ ] 预处理开关回放后仍需重新加载序列才生效（历史遗留，未变）。

## 7. 下一轮建议

1. 真机看网格效果；保存几条档案，双击载入验证。
2. 可选：把档案的 `render.png` 作为图标封面（更像"卷宗"）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏 | vtkPNGWriter 在 offscreen 报 `libpng Invalid IHDR`（有回退） | ⚠️ 仅离屏 |

---

---

## 历史轮次
<details>
<summary>Round R-24 — 2026-09-25 | 档案中心改为顶层一级页签（渲染右侧）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 24 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户澄清：「档案中心」应是**放在渲染页右侧的一级菜单页面**，而不是渲染页参数栏里的一个分组。本轮把上一轮加在渲染参数栏的「档案中心」分组**整块移出**，改为独立的一级页签 `_build_archive_tab()`，并在 `_build_pacs_tab()` 之后 `pages.addTab(page, "档案中心")`，使其位于**「渲染」右侧**。页面内容不变（当前爱宠提示 / 档案名称 / 保存·刷新·打开目录 / 仅显示当前序列 / 档案表格 / 载入·删除 / 状态），档案的采集、存储、回放逻辑完全复用上一轮的 `_archive_*` 方法。离屏测试确认页签顺序为 `爱宠列表 → MPR 阅片 → 渲染 → 档案中心`，渲染页已无档案分组，且保存/载入/删除往返正常。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 移出渲染参数栏 | ✅ | 删除 `_build_ui` 中的档案分组块 |
| 2 | 新一级页签 | ✅ | `_build_archive_tab()` + addTab（渲染右侧） |
| 3 | 逻辑复用 | ✅ | `_archive_*` 方法不变 |
| 4 | 验证 | ✅ | 页签顺序/归属 + 保存载入删除 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 档案中心从渲染页移出；新增 `_build_archive_tab()` 顶层页签 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()`（渲染页）** — 移除档案分组
```diff
- # --- 档案中心：保存/载入本爱宠的 MPR 与渲染信息 ---
- arch_group = QGroupBox("档案中心（保存本爱宠的 MPR 与渲染信息）")
- ...（59 行控件）...
- render_layout.addWidget(arch_group)
  # render_page goes in its own scroll area for the tab
```

**`_build_ui()` 尾部** — 新增页签构建
```diff
  # === PACS 取片页（插到「爱宠列表」之后）===
  self._build_pacs_tab()
+ # === 档案中心页（放在「渲染」右侧）===
+ self._build_archive_tab()
```

**新增方法 `_build_archive_tab()`**
```python
+ def _build_archive_tab(self):
+     page = QWidget(); outer = QVBoxLayout(page)
+     tip = QLabel("…保存 / 载入该爱宠的 MPR 与渲染档案…")
+     grp = QGroupBox("爱宠档案")
+     self.arch_case_lb / self.arch_name / [保存当前档案][刷新][打开档案目录] / self.arch_only_series
+     self.arch_table(4列, min高220, stretch 1)
+     [载入选中档案][删除选中] / self.arch_status
+     self._arch_rows = []
+     self.pages.addTab(page, "档案中心")     # 位于「渲染」右侧
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 页面归属 | 顶层一级页签，置于「渲染」右侧 | 用户明确要求 |
| 是否隐藏右侧 VR | 不隐藏（与「渲染」页一致） | 管档案时仍能看到当前渲染 |
| 参数栏空间 | 档案分组整块移出 | 参数栏更短，滚动更少 |
| 逻辑 | `_archive_*` 原样复用 | 仅搬 UI，行为不变 |

## 6. 遗留问题与待办

- [ ] 页签现有 4 个（爱宠列表 / MPR 阅片 / 渲染 / 档案中心）；如需更靠左可调整 addTab 顺序。
- [ ] 预处理开关回放后仍需重新加载序列才生效（同上一轮说明）。

## 7. 下一轮建议

1. 打开「档案中心」页签，保存一条档案并列表/载入/删除各点一次。
2. 可选：档案列表按爱宠分组折叠；或导出 PDF 档案报告。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏截图 | 仍见 offscreen 下 `libpng Invalid IHDR`（已回退抓控件） | ⚠️ 仅离屏，真机不受影响 |

---

---

## 历史轮次
<details>
<summary>Round R-23 — 2026-09-25 | 渲染页新增「档案中心」（保存爱宠 MPR+渲染信息）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 23 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户说明程序逻辑：从「爱宠列表」选中爱宠的某个序列后，**MPR 阅片与渲染都自动指向该序列**；要求据此在**渲染页增加「档案中心」**，用于保存对应爱宠的 MPR 与渲染信息。本轮在渲染参数栏（加载进度下方）新增「档案中心」分组：显示当前爱宠/序列身份、档案名称输入、保存当前档案、档案列表（时间/名称/爱宠/序列，可「仅显示当前序列」）、载入选中档案、删除选中、打开档案目录。档案以**一档一目录**存于 `mcp_records/archives/<时间戳>/`：`meta.json`（身份 + 渲染参数 + MPR 参数 + 测量）+ `render.png`（VTK 渲染截图）+ `mpr.png`（MPR 网格截图）。「载入」会把渲染模式/滑块/阈值/预处理开关/相机 + MPR 窗宽窗位/切片/测量全部还原。离屏往返测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 档案中心 UI | ✅ | 名称/保存/刷新/列表/载入/删除/打开目录/仅当前序列 |
| 2 | 身份识别 | ✅ | 读序列第一张 DICOM 头（爱宠ID/名字/Study/Series/模态/日期） |
| 3 | 采集与回放 | ✅ | 渲染 11 滑块+3 区间+6 开关+相机；MPR 窗宽窗位/切片/测量 |
| 4 | 截图 | ✅ | VTK 渲染 PNG（带控件回退）+ MPR 网格 PNG |
| 5 | 验证 | ✅ | 保存/载入/删除 往返断言全通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | RangeSlider 加 `values()`；渲染页加「档案中心」；新增 `_archive_*` 系列方法；载入/启动时刷新 |
| `mcp_records/archives/` | 新增（运行时） | 档案目录（一档一目录） |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**类 `RangeSlider`** — 增加只读取值
```python
+ def values(self):
+     return (int(self._lower), int(self._upper))
```

**`_build_ui()`（加载进度之后）** — 档案中心 UI
```python
+ arch_group = QGroupBox("档案中心（保存本爱宠的 MPR 与渲染信息）")
+ self.arch_case_lb / arch_name / arch_btn_save / arch_btn_refresh / arch_btn_dir
+ self.arch_only_series（仅显示当前序列） + self.arch_table(4列)
+ self.arch_btn_load / arch_btn_del / arch_status
```

**新增 `_archive_*` 方法**
```python
+ _archive_root()            # <record_dir>/archives（mcp_records/archives）
+ _current_case_info()       # 读序列第一张 DICOM 头 → 爱宠/序列身份
+ _archive_capture_render()  # 模式/11滑块/3区间/6开关/相机/背景
+ _archive_capture_mpr()     # ww/wl/slices/measures/tool
+ _archive_grab_render/_grab_mpr()   # PNG 截图（带回退与大小校验）
+ _archive_save()            # 建目录 + meta.json + 两张 PNG
+ _archive_refresh()         # 列表（可按当前序列过滤）+ 当前爱宠提示
+ _archive_apply(meta)       # 回放渲染 + MPR 参数
+ _archive_load_selected() / _archive_delete_selected() / _archive_open_dir()
```

**`load_dicom()` 成功分支** — 刷新档案列表
```python
+ try: self._archive_refresh()
+ except Exception: pass
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 身份来源 | 读序列第一张 DICOM 头 | 不依赖是否从列表进入；路径加载也能识别 |
| 存储形态 | 一档一目录（meta.json + 2 PNG） | 便于人工查看/删除/迁移；截图即档案证据 |
| 恢复范围 | 渲染参数 + MPR + 测量 + 相机 | 一次载入即回到当时的阅片状态 |
| 预处理器开关 | 一并保存/回放（下次加载生效） | 预处理只在 load 时生效，如实说明 |
| 默认过滤 | 「仅显示当前序列」勾选 | 与"按爱宠序列归档"的心智一致 |

## 6. 遗留问题与待办

- [ ] 预处理开关（NLM/CLAHE/Frangi…）回放后需**重新加载**才生效（当前仅恢复勾选态）。
- [ ] 档案未与 PACS 取片联动（PACS 取回的序列同样可归档，路径已统一）。
- [ ] 未见 DICOM 身份时（非 DICOM 文件夹）身份字段为空，列表仍可保存。

## 7. 下一轮建议

1. 真机流程：爱宠列表选序列 → 调好渲染/MPR → 保存档案 → 换序列 → 回来载入档案核对。
2. 可选：档案导出为 PDF 报告（含两张截图 + 参数表）；或"载入档案后自动重载以套用预处理"。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 离屏截图 | offscreen 下 vtkPNGWriter 报 `libpng Invalid IHDR` | ✅ 已加"文件大小校验 + 控件 grab 回退"，真机不受影响 |

---

---

## 历史轮次
<details>
<summary>Round R-22 — 2026-09-25 | 爱宠列表留白区（无用 stretch）改为列表铺满 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 22 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户问「爱宠列表页面左下方的空白区域是做什么的？」。查明：那块**没有任何功能**，纯粹是布局遗留——`pat_tree` 被 `setMaximumHeight(280)` 限高，且 `_build_patient_section` 末尾又 `addStretch(1)` 把所有控件往上顶，于是下方留白。该限制来自早期「病人列表」还是『渲染』页内嵌子页（高度有限）的时期；现在它已是独立整页，留白就多余了。本轮**去掉高度上限与末尾 stretch**，让爱宠/序列树铺满整块区域。实测 `pat_tree` 高度 280 → **753**（面板 900），`maximumHeight` 解除。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 定位留白成因 | ✅ | 树限高 280 + 末尾 addStretch |
| 2 | 修复 | ✅ | 去限高（改最小 220）+ 去 stretch |
| 3 | 验证 | ✅ | 树高 753，铺满；截图确认 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_build_patient_section`：去 `setMaximumHeight(280)`；去末尾 `addStretch(1)` |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_patient_section()`（:3099 / :3113）** — 让列表铺满
```diff
- self.pat_tree.setMaximumHeight(280)
+ # 不再限制高度：让爱宠/序列树铺满下方空白区域
+ self.pat_tree.setMinimumHeight(220)
  ...
  parent_layout.addWidget(self.pat_status)
- parent_layout.addStretch(1)
  self._pat_patients = []
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 留白处理 | 让树铺满（而非填别的内容） | 该区域本就属于列表；铺满最直观 |
| 最小高度 | 220px | 保证窗口很小时树仍可见、可用 |

## 6. 遗留问题与待办

- [ ] 若希望该区域放**别的**内容（如序列缩略图、磁盘/病例统计、快捷操作），可再规划（当前改为列表铺满）。

## 7. 下一轮建议

1. 扫描病例后确认长列表滚动/选择正常。
2. 可选：在列表上方加筛选框（按姓名/模态）利用更多空间。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-21 — 2026-09-25 | 不规则形闭合明确化（松开/右击闭合） (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 21 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户问「不规则形如何闭合？」。原实现是"松开鼠标即闭合"，但没有提示、也没有可感知的反馈。本轮把不规则形的闭合行为**明确化并可预期**：① 按住左键沿轮廓描边，**松开即自动闭合成多边形**（首尾相连）；② 描边过程中用**虚线**从当前点连回首点，直观提示"将在此闭合"；③ 增加**右击提前闭合**（不必画完整圈）；④ 对轨迹**抽稀**（移动>1.2px 才记点），避免点数爆炸；⑤ 提示文案与按钮 tooltip 写明「松开即自动闭合（也可右击闭合）」。另外修复一个隐蔽 bug：新版 PySide6 的 `Qt.MouseButton` 枚举**不再与 int 相等**，`int(enum)` 也会抛错，导致右击判断失效——新增 `_sam_is_right_button()` 用 `.value` 稳健判�断。离屏测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 闭合行为 | ✅ | 松开自动闭合 + 右击闭合 |
| 2 | 可视化提示 | ✅ | 描边时虚线连回首点 |
| 3 | 健壮性 | ✅ | 抽稀 + 过小丢弃 + 枚举右键判断修复 |
| 4 | 文案 | ✅ | 图例/ tooltip 写明闭合方式 |
| 5 | 验证 | ✅ | 4 项断言全通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|----------|
| `ssd_vr_viewer.py` | 修改 | 不规则形闭合逻辑/预览/抽稀；新增 `_sam_is_right_button`；文案 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增 `_sam_is_right_button()`** — 兼容 int / 枚举
```python
+ @staticmethod
+ def _sam_is_right_button(btn):
+     try:
+         if btn == QtCore.Qt.MouseButton.RightButton: return True
+     except Exception: pass
+     try:
+         _rv = getattr(QtCore.Qt.MouseButton.RightButton, "value", QtCore.Qt.MouseButton.RightButton)
+         _bv = getattr(btn, "value", btn)
+         return int(_bv) == int(_rv)
+     except Exception: return False
```

**`_sam_meas_press()`** — 右击提前闭合 + 简单起点
```diff
+ if (self.sam_tool == "poly" and self._sam_is_right_button(info.get("button"))
+         and self._sam_meas_draft and self._sam_meas_draft.get("axis") == axis):
+     self._sam_meas_commit(); return
+ if self.sam_tool == "poly":
+     self._sam_meas_draft = {"axis": axis, "type": "poly", "pts": [pt]}
  else:
      self._sam_meas_draft = {"axis": axis, "type": self.sam_tool, "pts": [pt, pt]}
```

**`_sam_meas_move()`** — 轨迹抽稀
```diff
  if d["type"] == "poly":
+     last = d["pts"][-1]
+     if (pt[0]-last[0])**2 + (pt[1]-last[1])**2 >= 1.44:   # >1.2px
+         d["pts"].append(pt)
-     d["pts"].append(pt)
```

**`_sam_draw_measures()`** — 描边中虚线提示闭合
```diff
  if m.get("draft"):
+     p.drawPolyline(QPolygonF(...))
+     p.setPen(QPen(QColor(255,214,0,170), 2, DashLine))
+     p.drawLine(QPointF(*pts[-1]), QPointF(*pts[0]))   # 首尾虚线
  else:
      p.drawPolygon(...)
```

**文案**：tooltip「不规则形：按住左键沿轮廓描边，松开即自动闭合（也可右击闭合）」；图例「· 形 = 不规则形：按住左键描边，松开即自动闭合（右击也可闭合）→ 面积/周长」。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 闭合方式 | 松开自动闭合（主）+ 右击闭合（备） | 自由描边最自然；右击可提前收尾 |
| 可视化 | 描边时画虚线回首点 | 让"将闭合"可预期 |
| 轨迹点 | 抽稀 1.2px | 防止上千点拖慢重绘 |
| 过小图形 | <3 点或跨度过小则丢弃 | 避免误触产生垃圾测量 |

## 6. 遗留问题与待办

- [ ] 未提供"取消当前描边"的快捷键（如 Esc）；如需要可加 keyPressEvent。
- [ ] 测量仍未持久化。

## 7. 下一轮建议

1. 真机试：按住描一圈松手→自动闭合；描一半右击→提前闭合。
2. 可选：加 Esc 取消、或双击闭合（需与放大手势区分）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | error | 测量 | 新版 PySide6 枚举与 int 不相等、`int(enum)` 抛错 → 右击判断失效 | ✅ 改用 `.value` 比较后修复 |

---

---

## 历史轮次
<details>
<summary>Round R-20 — 2026-09-25 | 交互提示补充测量工具含义（看/长/角/矩/圆/形/清）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 20 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：在 MPR 页的**交互提示**里说明左侧测量工具「看/长/角/矩/圆/形/清」的对应含义。本轮把原来一句「· 左侧工具：长/角/矩/圆/形 测量」替换为**逐条说明**（看=浏览、长=长度 mm、角=角度 °、矩=矩形 宽×高+cm²、圆=圆形/椭圆 直径+cm²、形=不规则 面积+周长、清=清除全部测量），并说明操作方式（先选工具再在图上操作）。因提示变长，把图例放进 `QScrollArea` 以免在 2×2 小格里被裁切。离屏测试确认 7 条说明齐全且滚动区存在。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 文案更新 | ✅ | 7 个工具逐条含义 + 操作说明 |
| 2 | 防裁切 | ✅ | 图例包进滚动区 |
| 3 | 验证 | ✅ | 离屏检查 7 条齐全 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | MPR 图例文案补充工具含义；图例放入 QScrollArea |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_sam_tab()` 图例（:5490-）**
```diff
  leg = QtWidgets.QLabel(
      "交互提示\n"
-     "· 左侧工具：长/角/矩/圆/形 测量\n\n"
+     "\n左侧测量工具（先选工具，再在图上操作）\n"
+     "· 看 = 浏览（不测量，左键加提示点）\n"
+     "· 长 = 长度：按住拖一条线 → 显示 mm\n"
+     "· 角 = 角度：先点顶点，再点两臂（共 3 下）→ 显示 °\n"
+     "· 矩 = 矩形：按住拖出矩形 → 宽×高 与面积 cm²\n"
+     "· 圆 = 圆形/椭圆：按住拖出外接框 → 直径 与面积 cm²\n"
+     "· 形 = 不规则形：按住自由描边 → 面积 与周长\n"
+     "· 清 = 清除全部测量\n\n"
      "十字线图例\n" ...)
- info_col.addWidget(leg); info_col.addStretch()
+ info_scroll = QtWidgets.QScrollArea(); info_scroll.setWidget(leg)
+ info_col.addWidget(info_scroll, 1)
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 说明粒度 | 每个工具一行、含单位与操作方式 | 用户明确要求"对应含义" |
| 长文案处理 | 图例放入滚动区 | 2×2 信息格空间有限，避免被裁 |

## 6. 遗留问题与待办

- [ ] 图例较长时需在信息格内滚动查看（可接受）。
- [ ] 如需更醒目，可把测量说明做成点「?」弹出的浮层。

## 7. 下一轮建议

1. 打开 MPR 页查看图例，确认 7 条说明可读。
2. 可选：把测量工具也加上"悬停高亮说明"。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-19 — 2026-09-25 | 修复渲染报错（ITK Size mismatch，自动择一致系列） (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 19 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户反馈「当前程序渲染报错」。查运行日志定位到 `build_reader` 里 SimpleITK `ImageSeriesReader.Execute()` 抛 `ITK ERROR: Size mismatch! ... [546,608,1] does not match ... [530,608,1]`。根因：**病例目录里混放了多个序列/不同尺寸的实例**（实测该目录含 **28 个 SeriesInstanceUID**，其中一个 76 张的系列内部就有 26 种不同尺寸）。ITK 不带 UID 的 `GetGDCMSeriesFileNames(目录)` 会把它们混在一起读，尺寸不一致即中止。本轮新增 `pick_series_files()`：按 SeriesInstanceUID 取**文件数最多的系列**（ITK 已按空间位置排序），再在该系列内按 `(Rows,Columns)` 取**最大的一致子集**；另加"逐系列单独读"的兜底。实测该真实病例现在自动选用 **1829 张 512×512** 系列并成功读取（512,512,1829）；合成混合尺寸用例也通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 读日志定位 | ✅ | 日志：ITK Size mismatch（530 vs 546） |
| 2 | 排查真实目录 | ✅ | 28 个系列；有多尺寸混杂 |
| 3 | 修复 | ✅ | pick_series_files + 逐系列兜底 |
| 4 | 验证 | ✅ | 真实病例 + 合成用例均通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 新增 `_dicom_rc()` / `pick_series_files()`；`build_reader()` 目录分支改为择一一致系列 + 兜底 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增 `pick_series_files(root)`（build_reader 之前）**
```python
+ def _dicom_rc(fp):
+     ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
+     return (int(ds.Rows), int(ds.Columns))
+ def pick_series_files(root):
+     # 1) 按 SeriesInstanceUID 取文件数最多的系列（ITK 已排序）
+     for uid in sitk.ImageSeriesReader.GetGDCMSeriesIDs(root) or []:
+         names = list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(root, uid))
+         if len(names) > len(best): best = names
+     # 2) 无 UID 时回退目录全量
+     # 3) 在该集合内按 (Rows,Columns) 取最大一致子集
+     return max(groups.values(), key=len)
```

**`build_reader()` 目录分支**
```diff
- dicom_names = reader.GetGDCMSeriesFileNames(dicom_path)     # 会把多序列混在一起
+ dicom_names = pick_series_files(dicom_path)                 # 只取一致的一组
  reader.SetFileNames(dicom_names)
- image = reader.Execute()
+ try:
+     image = reader.Execute()
+ except RuntimeError as _e:
+     # 兜底：逐个 SeriesInstanceUID 单独读，取第一个成功的
+     for uid in (sitk.ImageSeriesReader.GetGDCMSeriesIDs(dicom_path) or []):
+         ... image = r2.Execute(); break
+     if image is None: raise _e
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 选系列依据 | **文件数最多**的一致系列 | 主序列通常切片最多；定位像/延迟期只有几张 |
| 同系列内混尺寸 | 取最大尺寸一致子集 | 兼容"重建参数串号"的脏数据 |
| 排序 | 沿用 ITK 返回顺序 | ITK 已按空间位置正确排序，避免自研排序出错 |
| 兜底 | 逐 SeriesUID 单读 | 极端情况下仍能出图 |

## 6. 遗留问题与待办

- [ ] 若用户想读**非最大**的系列（如某延迟期），目前会自动选最大；可后续在「爱宠列表」里按序列选择（该功能已有，扫描后点击具体序列加载）。
- [ ] 大批量目录（本例 28 系列、数千文件）用 pydicom 读头判断尺寸会有少量耗时；已在"最大系列"内做，可接受。

## 7. 下一轮建议

1. 重试加载该病例，确认能正常渲染；如想换系列，走「爱宠列表」扫描后点具体序列。
2. 可把"自动选中的系列"信息显示在状态栏/日志，便于用户确认选的是哪套。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| 22:48 | error | build_reader | ITK `Size mismatch`（目录多序列/多尺寸混杂） | ✅ 已修复（自动择一一致系列） |

---

---

## 历史轮次
<details>
<summary>Round R-18 — 2026-09-25 | MPR 三视图左侧测量工具（长/角/矩/圆/形）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 18 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：MPR 页的**轴向/冠状/矢状三视图左侧**增加一列**测量小工具**，支持 **长度 / 角度 / 圆形 / 矩形 / 不规则形**。本轮在 MPR 分组内把「2×2 视图网格」改为 **[左侧测量工具条 + 视图网格]** 横向布局；工具条含 6 个互斥工具按钮（看=浏览、长、角、矩、圆、形）+「清」清除键。`SamSliceLabel` 新增 `m_press/m_move/m_release` 信号与测量模式（测量时不再派发加点单击）；按**体素物理间距**换算 mm/mm²/角度，并在切片图上实时绘制形状与数值标签（拖拽即时预览）。离屏功能测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 布局改造 | ✅ | 工具条 + 网格 横向布局（工具条在最左） |
| 2 | 鼠标事件 | ✅ | SamSliceLabel 测量模式 + 三类原始事件 |
| 3 | 5 种测量 | ✅ | 长度/角度/矩形/圆形/不规则 + 草稿预览 |
| 4 | 数值换算 | ✅ | 按 spacing 得 mm / cm² / 度 |
| 5 | 验证 | ✅ | 数学断言 + 拖拽路径 + 清除 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|----------|
| `ssd_vr_viewer.py` | 修改 | 工具条 UI；SamSliceLabel 测量事件；`_sam_*` 测量方法；渲染绘制 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**类 `SamSliceLabel`（:1097-）** — 测量模式与原始鼠标事件
```python
+ m_press = QtCore.Signal(int, object)
+ m_move = QtCore.Signal(int, object)
+ m_release = QtCore.Signal(int, object)
+ def set_measure_mode(self, on):
+     self._measure = bool(on); self.setCursor(CrossCursor if on else ArrowCursor)
+ def mousePressEvent(self, ev):
+     if self._measure: self.m_press.emit(self.axis, self._ev_pos(ev)); ev.accept(); return
+     ...
+ def mouseMoveEvent / mouseReleaseEvent:   # 测量模式转发
+ def mouseDoubleClickEvent:                # 测量模式不触发放大
```

**`_build_sam_tab()`（:5276-）** — 左侧工具条
```python
+ content_row = QHBoxLayout()
+ tools = QWidget(); tools.setFixedWidth(46)
+ for key, sym, tip in [("none","看",...),("line","长",...),("angle","角",...),
+                       ("rect","矩",...),("ellipse","圆",...),("poly","形",...)]:
+     b = QToolButton(); b.setCheckable(True); b.clicked -> self._sam_set_tool(key)
+ clear = QToolButton("清") -> self._sam_meas_clear
+ content_row.addWidget(tools); content_row.addWidget(grid_host, 1)
+ mpr_outer.addWidget(content, 1)
```

**新增测量方法**：`_sam_set_tool` / `_sam_meas_clear` / `_sam_spacing_rc` / `_sam_pix_from_info` / `_sam_meas_press|move|release` / `_sam_meas_commit` / `_sam_meas_text` / `_sam_draw_measures`。
- 换算：`axis0: (row=y,col=x)`、`axis1: (row=z,col=x)`、`axis2: (row=z,col=y)`，取 `vr_image_data.GetSpacing()`。
- 角度：第 1 点为顶点，第 2/3 点为两臂（3 次点击）；其余为按住拖拽。
- 面积：rect=w·h；ellipse=π·a·b；poly=Shoelace（另给周长）。

**`_sam_render_view()`** — 叠加测量
```python
+ try: self._sam_draw_measures(p, axis)
+ except Exception: pass
```

**`_sam_reset()`** — 新数据清空测量并回到浏览
```python
+ self.sam_measures = {0: [], 1: [], 2: []}
+ self._sam_meas_draft = None
+ self.sam_tool = "none"; sam_tool_buttons["none"].setChecked(True)
+ for lb in sam_view_labels.values(): lb.set_measure_mode(False)
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 交互方式 | 拖拽（长/矩/圆/形）+ 3 次点击（角） | 符合直觉；角度需 3 点 |
| 与提示点冲突 | 测量激活时禁用 clicked（加点） | 一套鼠标不能两用 |
| 坐标存储 | 图像像素 (col,row) | 与十字线/切片同坐标系，缩放无关 |
| 数值单位 | 按物理间距 → mm / cm² / 度 | 临床测量必须真实尺度 |
| 工具图标 | 中文字（看/长/角/矩/圆/形/清） | 避免 emoji/符号缺字，任何环境可显示 |
| 放大态 | 工具条保留可见 | 放大后仍可测量 |

## 6. 遗留问题与待办

- [ ] 测量未持久化（切换序列/关闭即清空）；如需可写入 ROI/导出。
- [ ] 测量绘在切片 pixmap 上，视图缩小时线宽/字体会同比缩小（与十字线一致）。
- [ ] 未做按测量结果一键保存到「已保存 ROI」列表。

## 7. 下一轮建议

1. 真机拖拽 5 种测量，检查数值与形状绘制；确认与"加提示点"不再互相干扰。
2. 可选：测量结果导出（CSV/截图）、或"另存为 ROI"。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 测试 | 控制台 GBK 无法打印 `cm²` 导致测试中断 | ✅ 测试改 ASCII 转义输出后通过 |

---

---

## 历史轮次
<details>
<summary>Round R-17 — 2026-09-25 | 已保存ROI 清除 TotalSegmentator 遗留条目（492→5）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 17 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：MPR 页「已保存 ROI（列表/加载）」里**删除 TotalSegmentator 的默认列表信息**。排查发现 `mcp_records/roi_summary_3dsam.csv` 共 **492 行**，其中 **487 行**是旧「自动ROI检测」功能写入的 `TotalSegmentator:total / total_mr`（另有 4 条 test、1 条 ct_case）。本轮在 `_sam_roi_load_records()` 中增加**遗留条目过滤**：source 以 `TotalSegmentator`/`nnUNet` 开头或含 `SynthSeg` 的行不再加载，并**回写 CSV 永久删除**；同时删除已无调用方的死代码 `_sam_sync_roi_blocks`（其文档串/状态文案还在提 TotalSegmentator）。实测 CSV 492 → **5 行**（仅保留 test/ct_case），表格不再出现 TotalSegmentator。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 数据排查 | ✅ | CSV 492 行中 487 行是 TotalSegmentator |
| 2 | 过滤+清除 | ✅ | `_sam_roi_load_records` 过滤并回写 |
| 3 | 删死代码 | ✅ | 移除 `_sam_sync_roi_blocks` |
| 4 | 验证 | ✅ | CSV 492→5；表格无 TS；py_compile 通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_sam_roi_load_records` 过滤遗留条目并回写；删除 `_sam_sync_roi_blocks` |
| `mcp_records/roi_summary_3dsam.csv` | 修改 | 清除 487 条 TotalSegmentator 记录（备份 `.csv.bak_round17`） |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_sam_roi_load_records()`** — 过滤 + 永久清除遗留条目
```python
+ def _is_legacy(r):
+     src = (r.get("source") or "").strip().lower()
+     return (src.startswith("totalsegmentator") or src.startswith("nnunet")
+             or "synthseg" in src)
+ kept = [r for r in rows if not _is_legacy(r)]
+ removed = len(rows) - len(kept)
+ if "mask_npy" not in rows[0] or removed > 0:
+     with open(path, "w", ...) as f:            # 回写：永久删除
+         w = _csv.DictWriter(f, fieldnames=full_header, extrasaction="ignore")
+         w.writeheader()
+         for r in kept: w.writerow(r)
+ return kept
```

**删除 `_sam_sync_roi_blocks()`**（旧自动ROI→列表同步，已无调用方，且文案含 TotalSegmentator）。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 处理方式 | 读取时过滤 **且** 回写 CSV | 用户要"删除"，不只是隐藏；回写避免文件持续膨胀 |
| 过滤范围 | TotalSegmentator / SynthSeg / nnU-Net | 同属已移除的「自动ROI检测」功能 |
| 保留项 | `test` / `ct_case` 共 5 条 | 用户只要求删 TotalSegmentator，其余保留 |
| 备份 | `roi_summary_3dsam.csv.bak_round17` | 数据操作留退路 |

## 6. 遗留问题与待办

- [ ] `mcp_records/roi_summary_3dsam.csv.bak_round17` 为备份，确认无误后可删。
- [ ] 若还有其它历史 ROI 来源（如 `roi_summary.db`）需要清理，可另行处理（那是 dicom_tool 的记录，不属本页）。

## 7. 下一轮建议

1. 打开 MPR 页确认「已保存 ROI」仅剩 test/ct_case；后续用 3D SAM 保存的 ROI 会以「SAM-Med3D」来源出现。
2. 可加"按来源筛选/一键清空"按钮，方便管理列表。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-16 — 2026-09-25 | 渲染页参数栏防遮挡（最小宽 624→320）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 16 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户反馈「渲染」页左侧参数栏**内容被遮挡**。定位到根因：参数栏页（`render_page`）的**最小宽度被撑到 624 px**，而左侧面板实际约 480 px，且 `render_scroll` 关闭了横向滚动条（`ScrollBarAlwaysOff`），于是右侧内容被硬裁掉。两个主要撑宽源：① 两个下拉框按**最长选项**算 sizeHint（渲染模式名、Slicer 预设名）；② DoF 行里 `CSlider` 默认 280+10 px，把该行撑到 545 px。修复后参数栏最小宽度降到 **320 px**，无任何超宽子控件，离屏渲染确认无裁切。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 定位遮挡 | ✅ | 最小宽度 624 > 面板 480；横向滚动禁用 |
| 2 | 下拉框限宽 | ✅ | AdjustToMinimumContentsLength + 不再按最长项撑宽 |
| 3 | DoF 行限宽 | ✅ | 文案缩短 + slider width=140 |
| 4 | 兜底 | ✅ | 横向滚动改 AsNeeded |
| 5 | 验证 | ✅ | 最小宽度 320；0 超宽子控件；离屏渲染无裁切 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_apply_retro_skin` 统一下拉框尺寸策略；preset_combo 同理；DoF 行限宽；横向滚动 AsNeeded |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_apply_retro_skin()` — 所有 CComboBox 不再按最长项撑宽**
```python
  for cb in self.findChildren(CComboBox):
      cb.combo_box().setStyleSheet(combo_qss)
+     cb.combo_box().setSizeAdjustPolicy(
+         QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
+     cb.combo_box().setMinimumContentsLength(8)
+     cb.combo_box().setMinimumWidth(110)
+     cb.combo_box().setSizePolicy(Expanding, Fixed)
```

**`_build_ui()` — Slicer 预设下拉框**
```python
  self.preset_combo = QtWidgets.QComboBox(); ...
+ self.preset_combo.setSizeAdjustPolicy(
+     QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
+ self.preset_combo.setMinimumContentsLength(10)
+ self.preset_combo.setMinimumWidth(140)
+ self.preset_combo.setSizePolicy(Expanding, Fixed)
```

**`_build_ui()` — DoF 行（撑到 545 的元凶）**
```diff
- self.cr_dof_checkbox = QtWidgets.QCheckBox("景深 (Depth of Field)")
+ self.cr_dof_checkbox = QtWidgets.QCheckBox("景深 (DoF)")
- self.cr_dof_radius_slider = CSlider(master=render_page, minimum=0, maximum=50, value=10)
+ self.cr_dof_radius_slider = CSlider(master=render_page, width=140,
+                                     minimum=0, maximum=50, value=10)
```

**`_build_ui()` — 渲染参数滚动区**
```diff
- render_scroll.setHorizontalScrollBarPolicy(ScrollBarAlwaysOff)
+ render_scroll.setHorizontalScrollBarPolicy(ScrollBarAsNeeded)   # 兜底，避免被硬裁
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 根因处理 | 收缩内容最小宽度（而非加宽面板） | 保留渲染区宽度；从源头解决裁切 |
| 下拉框策略 | AdjustToMinimumContentsLengthWithIcon | 长选项不再决定控件宽度（弹层仍显示全名） |
| DoF 行 | 缩短文案 + 限宽 slider | 该行原 545px 是最大撑宽源 |
| 兜底 | 横向滚动 AsNeeded | 未来新增控件超宽时可滚动而非被裁 |

## 6. 遗留问题与待办

- [ ] 其它页面（PACS 节点、MPR 阅片）未逐一做最小宽度体检；如遇类似遮挡可同法处理。
- [ ] 三个 312px 宽的复选框文案较长，是当前 320 最小宽度的上限来源；如要更窄可缩短文案。

## 7. 下一轮建议

1. 在真机把左侧面板拖到最窄，确认无裁切、无横向滚动条。
2. 如需更紧凑，可把长复选框文案缩短或改为两行。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | 诊断 | 初次脚本把「下拉框被拉伸填满」误判为撑宽源 | ✅ 改用布局项 minimumSize() 测量后定位到 DoF 行 |

---

---

## 历史轮次
<details>
<summary>Round R-15 — 2026-09-25 | PACS 节点改为爱宠列表的二级子页（点击触发）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 15 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户澄清：**「PACS 节点」不要占顶层页签**，而要作为一个**独立的二级子页面**，点击「爱宠列表」里的 **PACS节点** 按钮才触发显示。本轮把「爱宠列表」顶层页签内容改为 `QStackedWidget`：**子页 0 = 爱宠列表**、**子页 1 = PACS 节点**（其内部仍有 ① 节点设置 / ② 取片 的三级子页）。PACS 页面顶部新增 **「← 返回爱宠列表」** 按钮；顶层页签从 4 个回到 **3 个：爱宠列表 / MPR 阅片 / 渲染**。离屏功能测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 去掉 PACS 顶层页签 | ✅ | `insertTab(...)` → `patient_stack.addWidget(...)` |
| 2 | 爱宠列表 改 stacked | ✅ | 子页 0 列表 / 子页 1 PACS |
| 3 | 触发与返回 | ✅ | 按钮→子页1；「← 返回爱宠列表」→子页0 |
| 4 | 验证 | ✅ | py_compile + 离屏测试（顶层 3 页签） |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 爱宠列表→QStackedWidget；PACS 作二级子页；返回按钮；触发/返回逻辑 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()` 爱宠列表页（:1956-）**
```diff
- patient_page = QWidget(); ...; self.pages.addTab(patient_page, "爱宠列表")
+ self.patient_stack = QStackedWidget()
+ patient_page = QWidget(); ...; self._build_patient_section(...)
+ self.patient_stack.addWidget(patient_page)          # 0 = 爱宠列表
+ self.pages.addTab(self.patient_stack, "爱宠列表")
```

**`_build_pacs_tab()` 结尾（:2617-）**
```diff
- self.pages.insertTab(1, scroll, "PACS 节点")
+ self.patient_stack.addWidget(scroll)                # 1 = PACS 节点（二级子页）
```
并在页面顶部加返回按钮：
```python
+ self.btn_pacs_back = CButton(master=page, width=140, text="← 返回爱宠列表",
+                              command=self._pacs_back_to_list)
```

**`_open_pacs_page()` / 新增 `_pacs_back_to_list()`**
```diff
  def _open_pacs_page(self):
-     i = self._tab_index_by_text("PACS"); self.pages.setCurrentIndex(i)
+     self.pages.setCurrentIndex(0)           # 爱宠列表
+     self.patient_stack.setCurrentIndex(1)   # PACS 二级子页
+ def _pacs_back_to_list(self):
+     self.patient_stack.setCurrentIndex(0)
```

**`_pacs_load_result()`** — 载入前先回列表子页
```python
+ try: self.patient_stack.setCurrentIndex(0)
+ except Exception: pass
  self.pages.setCurrentIndex(self._tab_index_by_text("渲染")); self.load_dicom()
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| PACS 的归属 | 爱宠列表页的二级子页（QStackedWidget） | 按钮在爱宠列表，就近嵌入；不污染顶层菜单 |
| 层级 | 爱宠列表(一级 tab) → PACS节点(二级) → 节点设置/取片(三级) | 满足"取片是节点的子页 + PACS 是二级页" |
| 返回方式 | 页面顶部「← 返回爱宠列表」按钮 | 明确的二级页出口 |
| 顶层页签 | 回到 3 个（爱宠列表/MPR阅片/渲染） | 用户要求 PACS 不占主菜单 |

## 6. 遗留问题与待办

- [ ] 真实 PACS 端到端未联调（失败路径已测）。
- [ ] 三级嵌套较深；若觉得繁琐可把「节点设置/取片」合并为一页。

## 7. 下一轮建议

1. 真实节点联调：爱宠列表 → PACS节点 → 节点设置 → 测试连接（自动进取片）→ 查询 → 取片 → 载入。
2. 可选：取片结果自动并入爱宠列表。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-14 — 2026-09-25 | PACS 取片改为「PACS 节点」子页（节点设置/取片） (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 14 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求把「PACS 取片」改为「PACS 节点」的**子页面**。本轮把 PACS 功能重构为：顶层页签 **「PACS 节点」** 内含一个分段子页切换条（① 节点设置 / ② 取片 (C-GET)）+ `QStackedWidget`；节点配置归入子页 0，查询/结果树/C-GET 归入子页 1。进度条与状态行提到顶层，两个子页都可见。默认进入「节点设置」；`C-ECHO` 成功后自动切到「取片」；「爱宠列表」的「PACS节点」按钮进入时回到「节点设置」子页。离屏功能测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 顶层页改名 | ✅ | PACS 取片 → PACS 节点 |
| 2 | 子页结构 | ✅ | 分段切换条 + QStackedWidget（节点设置 / 取片） |
| 3 | 状态/进度提到顶层 | ✅ | 两个子页都能看到 |
| 4 | 交互联动 | ✅ | C-ECHO 成功自动进「取片」；按钮进入默认「节点设置」 |
| 5 | 验证 | ✅ | py_compile + 离屏测试 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | `_build_pacs_tab` 重构为 顶层页+两个子页；`_open_pacs_page`/`_pacs_on_echo` 联动 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_pacs_tab()`（重构）** — 顶层「PACS 节点」+ 子页
```diff
- # 三组（节点/查询/取片）平铺 + addTab(scroll, "PACS 取片")
+ # 分段切换条：pacs_sub_node / pacs_sub_get
+ self.pacs_stack = QStackedWidget()
+ node_page: 节点配置组（保存/测试连接/前往取片）
+ get_page : 查询组 + pacs_tree + 取片组
+ self.pacs_stack.addWidget(node_page); addWidget(get_page)
+ pacs_prog / pacs_status 提到 outer（两子页共享）
+ self.pacs_sub_node.toggled -> setCurrentIndex(0)
+ self.pacs_sub_get.toggled  -> setCurrentIndex(1)
+ self.pacs_sub_node.setChecked(True)
+ self.pages.insertTab(1, scroll, "PACS 节点")
```

**`_open_pacs_page()`**
```diff
  i = self._tab_index_by_text("PACS")
  if i >= 0: self.pages.setCurrentIndex(i)
+ if hasattr(self, "pacs_sub_node"):
+     self.pacs_sub_node.setChecked(True)     # 默认回到节点设置
```

**`_pacs_on_echo()`**
```diff
  self.pacs_status.setText(("✅ " if ok else "❌ ") + msg)
+ if ok and hasattr(self, "pacs_sub_get"):
+     self.pacs_sub_get.setChecked(True)      # 连接成功 → 直接进入取片
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 层级结构 | 顶层「PACS 节点」+ 子页「节点设置 / 取片」 | 用户明确要取片作为节点的子页面 |
| 子页切换 | 自绘分段按钮（retro 风格）+ QStackedWidget | 与整体拟物皮肤一致；不新增顶层页签 |
| 状态/进度位置 | 提到顶层 | 子页切换后仍能看到连接/取片反馈 |
| 默认子页 | 节点设置 | 先配置再取片的自然顺序；ECHO 成功自动进取片 |

## 6. 遗留问题与待办

- [ ] 未对真实 PACS 联调（成功路径待现场验证；失败路径已测）。
- [ ] 部分 PACS 禁用 C-GET，如需改用 C-MOVE（需 PACS 端登记本机 AE/端口）。

## 7. 下一轮建议

1. 真实节点上跑一遍：节点设置 → 测试连接（自动进取片）→ 查询 → 取片 → 载入。
2. 如需，取片结果可直接并入「爱宠列表」（写入本地库并刷新列表）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | error | 补丁脚本 | 匹配 emoji 用了转义串导致 anchor 未命中（文件未被写入） | ✅ 改为按函数体索引插入后成功 |

---

---

## 历史轮次
<details>
<summary>Round R-13 — 2026-09-25 | 新增 PACS 取片（PACS节点按钮→新页，C-ECHO/C-FIND/C-GET） (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 13 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：把「爱宠列表」页的「用当前路径」按钮改成「**PACS节点**」，点击进入**新页面**，配置节点后可从 **CT / DR / MRI 主机（PACS）** 获取完整 DICOM 序列。本轮：① 安装 `pynetdicom 3.0.4`（mar 环境，并写入 requirements/build 配置）；② 新建 `pacs_client.py`（C-ECHO / C-FIND(STUDY·SERIES) / C-GET，阻塞式，带中文错误）；③ 新增 `PacsWorker(QThread)` 后台线程 + 「PACS 取片」页（节点配置与保存、查询过滤、结果两级树、C-GET 取回 + 进度 + 载入阅片）；④ 「用当前路径」按钮改为「PACS节点」并跳转到该页。离屏功能测试全通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 依赖 | ✅ | pip 安装 pynetdicom；requirements/.gitignore/build 配置更新 |
| 2 | 网络客户端 | ✅ | pacs_client.py：C-ECHO/C-FIND/C-GET |
| 3 | 后台线程 | ✅ | PacsWorker（QThread + 信号） |
| 4 | PACS 页面 | ✅ | 节点/查询/取片 三组；树 + 进度 + 日志 |
| 5 | 入口按钮 | ✅ | 用当前路径 → PACS节点 → 跳转 |
| 6 | 验证 | ✅ | py_compile + 离屏功能测试 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `pacs_client.py` | 新建 | PACS 客户端：C-ECHO / C-FIND / C-GET |
| `ssd_vr_viewer.py` | 修改 | import json/pacs_client；PacsWorker；PACS 页与方法；按钮改名跳转 |
| `requirements.txt` | 修改 | 加 pynetdicom / pydicom |
| `.gitignore` | 修改 | 忽略 pacs_nodes.json / pacs_downloads/ |
| `build.yaml` | 修改 | hidden-import pacs_client/pynetdicom/pydicom |
| `.github/workflows/main.yml` / `main-full.yml` | 修改 | 同上 hidden-import |
| `ssd_vr_viewer_macos.spec` | 修改 | hiddenimports 加 PACS 相关 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `pacs_client.py`（新建）

- `available()/require()`：探测 pynetdicom。
- `c_echo(host,port,called,calling,timeout)`：Verification 关联并 `send_c_echo`，成功返回 'OK'。
- `c_find_studies(...)`：STUDY 级 C-FIND，返回 patient/study 字段列表。
- `c_find_series(..., study_uid)`：SERIES 级 C-FIND。
- `c_get_series(..., series_uid, out_dir)`：C-GET + `StoragePresentationContexts` + `EVT_C_STORE` 保存到 `out_dir/<SeriesUID>/NT.dcm`。
- 连接失败统一抛 `PacsError`（中文提示：检查 主机/端口/被叫AE/网络）。

### 文件: `ssd_vr_viewer.py`

**类 `PacsWorker(QThread)`** — 后台执行，信号回 GUI
```python
+ class PacsWorker(QtCore.QThread):
+     echo_done / studies_ready / series_ready / retrieve_done / progress / failed
+     def run(self):  # 按 self.job 分派 echo/studies/series/get
```

**`_build_pacs_tab()`** — 新页三组
```python
+ 节点组: pacs_node_combo / name / host / port / called / calling / timeout + [保存节点][测试连接]
+ 查询组: name / id / modality / 日期区间 + [查询检查][查询选中检查的序列][清空]
+ self.pacs_tree (两级: 检查→序列)
+ 取片组: pacs_out + [浏览][获取选中序列 (C-GET)][载入到阅片] + progress + status
+ self.pages.insertTab(1, scroll, "PACS 取片")
```

**`_open_pacs_page()` / 按钮改动**
```diff
- self.btn_pat_sync = CButton(..., text="用当前路径", command=self._patient_sync_from_render_path)
+ self.btn_pat_pacs = CButton(..., text="PACS节点", command=self._open_pacs_page)
```

**`_pacs_*` 系列方法**：`_pacs_save_node/_pacs_read_nodes/_pacs_refresh_nodes/_pacs_node_selected`、`_pacs_echo/_pacs_find_studies/_pacs_find_series/_pacs_retrieve/_pacs_load_result`、回调 `_pacs_on_echo/_on_studies/_on_series/_on_retrieve/_on_progress/_on_failed`。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 网络库 | pynetdicom（DICOM 标准实现） | 手写 DIMSE 不现实；社区标准 |
| 取片方式 | **C-GET** 为主 | 客户端单连接即可接收，无需 PACS 侧配置接收 AE |
| 线程模型 | QThread + 信号 | 网络阻塞不能占 GUI 主线程 |
| 页面位置 | 新顶层页签，插在「爱宠列表」之后 | 与"获取数据"流程相邻；MPR 仍在渲染左侧 |
| 节点配置 | JSON 持久化到项目目录 | 简单、可多节点 |
| 取片落盘 | `pacs_downloads/<SeriesUID>/` | 与本地扫描/阅片复用同一条加载链路 |

## 6. 遗留问题与待办

- [ ] 未对真实 PACS 联调（本机无节点）；失败路径已测，成功路径需现场验证。
- [ ] 部分 PACS 禁用 C-GET，需改用 C-MOVE（需在 PACS 端登记本机 AE 与端口）；可按需增加 C-MOVE 模式。
- [ ] 目前无 TLS/认证（DICOM 明文），如需安全连接可加 TLS 与 AE 白名单。
- [ ] 冻结 EXE 需确保构建环境已装 pynetdicom（requirements 已加，CI 应自动安装）。
- [ ] `pacs_nodes.json` 记录明文节点，已被 .gitignore 忽略。

## 7. 下一轮建议

1. 在真实 CT/DR/MRI PACS 上：填节点 → 测试连接 → 查询 → 取一个序列 → 载入阅片，验证端到端。
2. 如需，补 C-MOVE 备选与"取片即自动载入"选项。
3. 可加"从 PACS 直接推送到本地库/病人列表"。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | pip | pynetdicom 安装后脚本目录未加入 PATH（仅提示） | ⚠️ 无影响 |

---

---

## 历史轮次
<details>
<summary>Round R-12 — 2026-09-25 | MPR 页新增窗宽/窗位滑块 + 预设 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 12 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求给「MPR 阅片」页加上**窗宽 / 窗位滑块**。本轮把 MPR 组由单一 `QGridLayout` 改为 `QVBoxLayout = [窗位行, 窗宽行, 网格容器]`，新增 MPR **专用**的 `sam_wl_slider`（-1000..3000 HU）与 `sam_ww_slider`（1..4000 HU）两条滑块 + 实时数值标签 + 5 个预设（软组织/肺窗/骨窗/脑窗/腹部）下拉；窗宽窗位**独立于「渲染」页**。同时把 MPR 灰度从"偏移/缩放"改为**真正的窗宽窗位映射**：CT 用 `[WL-WW/2, WL+WW/2] → 0..255`；MR/DR 无 HU 时用 1–99 百分位归一化，窗宽→对比度、窗位→亮度。放大态下窗宽窗位行仍可见（可边放大边调窗），新数据加载时复位为 400/40。离屏功能测试全部通过。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | MPR 加 WW/WL 滑块 | ✅ | 窗位/窗宽两行 + 数值标签 |
| 2 | 预设 | ✅ | 5 组预设下拉 |
| 3 | 真正 HU 窗映射 | ✅ | CT 窗宽窗位；MR/DR 对比度/亮度 |
| 4 | 验证 | ✅ | py_compile + 离屏测试（数值断言） |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|----------|
| `ssd_vr_viewer.py` | 修改 | MPR 组结构；WW/WL 滑块+预设+方法；灰度映射；复位 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_sam_tab()`（:4691-）** — MPR 组改为 竖向 布局 + WW/WL 行
```diff
- mpr_group = QGroupBox("MPR 切片 2×2（...）")
- mpr_grid = QGridLayout(mpr_group); mpr_grid.setSpacing(6)
+ mpr_group = QGroupBox("MPR 阅片（点击=加提示点；右击=加负点；双击=整页放大）")
+ mpr_outer = QVBoxLayout(mpr_group)
+ # 窗位行：sam_wl_slider(-1000..3000, 默认40) + sam_wl_value("40 HU")
+ # 窗宽行：sam_ww_slider(1..4000, 默认400)  + sam_ww_value("400 HU") + sam_ww_preset
+ grid_host = QWidget(); mpr_grid = QGridLayout(grid_host); mpr_outer.addWidget(grid_host, 1)
+ self.sam_mpr_grid = mpr_grid
```

**新增 `_sam_ww_wl_changed()` / `_sam_ww_preset_changed()`** — 值同步与预设
```python
+ def _sam_ww_wl_changed(self, *_):
+     self.sam_wl = int(self.sam_wl_slider.value()); self.sam_ww = int(self.sam_ww_slider.value())
+     self.sam_wl_value.setText(f"{self.sam_wl} HU"); self.sam_ww_value.setText(f"{self.sam_ww} HU")
+     self._sam_refresh_views()
+ def _sam_ww_preset_changed(self, index):
+     presets = {1:(400,40), 2:(1500,-600), 3:(2000,400), 4:(80,40), 5:(350,50)}
+     ...
```

**`_sam_render_view()`（:4972-）** — 真正的窗宽窗位映射
```diff
- wl_offset = self.wl_slider.slider().value(); ww_scale = self.ww_slider.slider().value()/100.0
- # CT：按 HU 窗（-1024..3072）线性映射
+ ww = float(getattr(self, "sam_ww", 400) or 400); wl = float(getattr(self, "sam_wl", 40))
+ if modality == "CT":
+     lo = wl - ww/2.0; hi = lo + ww
+     gray = clip((arrf - lo) / (hi - lo) * 255, 0, 255)
+ else:  # MR/DR：百分位归一化，窗宽→对比度，窗位→亮度
+     contrast = max(0.2, min(4.0, 400.0/max(1.0, ww))); bright = (wl-40.0)*0.25
+     gray = clip((norm-128)*contrast + 128 + bright, 0, 255)
```

**`_sam_reset()`（:5068-）** — 新数据复位 WW/WL
```python
+ with QSignalBlocker(self.sam_ww_slider), QSignalBlocker(self.sam_wl_slider):
+     self.sam_ww_slider.setValue(400); self.sam_wl_slider.setValue(40)
+ self._sam_ww_wl_changed()
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 是否与渲染页共用 | **独立**（MPR 专用） | 阅片窗值与 3D VR 窗互不干扰 |
| 灰度语义 | 真正的窗宽窗位（CT，HU） | 医生/兽医习惯按 WW/WL 读数 |
| MR/DR 处理 | 百分位归一化 + WW→对比度 WL→亮度 | 无 HU，无法用绝对窗 |
| 放大时是否隐藏滑块 | **不隐藏**（滑块在 MPR 组内） | 放大观片时仍可调窗 |
| 默认值 | 400/40（软组织） | 通用、对 CT/MR 都合理 |

## 6. 遗留问题与待办

- [ ] 窗宽/窗位目前用滑块（无直接数字输入框）；如需精确输入可加 SpinBox。
- [ ] 预设为固定常用值，未按物种/部位细分（可后续按犬猫头胸腹扩展）。
- [ ] 真机视觉确认未截图（多显示器截图受限）；逻辑已用离屏数值断言验证。

## 7. 下一轮建议

1. 载入真实 CT 序列，拖动窗宽/窗位看三视图实时变化，与渲染页窗互不影响。
2. 可加"同步到渲染页/从渲染页同步"按钮，或把预设按物种细分。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | 测试 | 首次取像素点落在十字线上导致断言偏差 | ✅ 改取偏心点后通过 |

---

---

## 历史轮次
<details>
<summary>Round R-11 — 2026-09-25 | MPR 页移到渲染左侧 + 三视图双击整页放大/还原 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 11 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求：① 把「MPR 阅片」页移到「渲染」页**左侧**；② MPR 的**轴向/冠状/矢状**三个视图可**双击放大占满整页**、再双击**缩小还原**。本轮用 `insertTab(1, ...)` 把 MPR 页放到「爱宠列表」之后、「渲染」之前（页签顺序变为 爱宠列表 → MPR 阅片 → 渲染）；并给 `SamSliceLabel` 增加双击信号 + **220ms 单击/双击判别**（避免双击时误加提示点），新增 `_sam_toggle_expand()` / `_sam_set_expanded()` 通过 `QGridLayout` 跨格（0,0,2,2）把选中视图铺满，同时隐藏其它两视图、信息格与模型/提示点/已保存 ROI 面板，双击再还原 2×2。离屏（offscreen）功能测试通过，程序已重启生效。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | MPR 页移到渲染左侧 | ✅ | `insertTab(1, ...)` |
| 2 | 双击放大/还原 | ✅ | SamSliceLabel 双击信号 + 网格跨格 |
| 3 | 单击/双击判别 | ✅ | 220ms 定时器，避免误加提示点 |
| 4 | 验证 | ✅ | py_compile + 离屏功能测试断言全过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 页签顺序；双击信号 + 布局放大/还原；复位时退出放大态 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**类 `SamSliceLabel`（:1085-）** — 双击信号 + 单击延时判别
```python
+ double_clicked = QtCore.Signal(int)   # (axis)
+ self._click_timer = QtCore.QTimer(self); setInterval(220); timeout-> _emit_single_click
+ def mousePressEvent(self, ev):
+     self._pending_click = {"x":..., "y":..., "button": ev.button()}
+     self._click_timer.start()
+ def mouseDoubleClickEvent(self, ev):
+     if ev.button() == LeftButton:
+         self._click_timer.stop(); self._pending_click = None
+         self.double_clicked.emit(self.axis); ev.accept(); return
```

**`_build_sam_tab()`（:4656-4780）** — 存引用 + 双击连接 + 页签插到渲染左侧
```python
+ self.sam_mpr_grid = mpr_grid; self.sam_cells = {}
+ self.sam_cell_pos = dict(_cell_pos); self.sam_info_cell = info_cell
+ self.sam_mdl_group = mdl_group; self.sam_pts_group = pts_group
+ self.sam_expanded_axis = None
  ...
+ lb.double_clicked.connect(self._sam_toggle_expand)
- self.pages.addTab(scroll, "MPR 阅片")
+ self.pages.insertTab(1, scroll, "MPR 阅片")   # 位于「渲染」左侧
```

**新增 `_sam_toggle_expand()` / `_sam_set_expanded()`** — 网格跨格放大/还原
```python
+ def _sam_set_expanded(self, axis):
+     # 先把上次跨格项放回原格
+     if cur ...: grid.removeWidget(cell); grid.addWidget(cell, *self.sam_cell_pos[cur])
+     if axis is None:   # 还原：显示全部 + 面板
+         for cell in cells: cell.setVisible(True); info.setVisible(True); panels.setVisible(True)
+     else:              # 放大：隐藏其它 + 跨满整格
+         for a, cell in self.sam_cells.items(): cell.setVisible(a == axis)
+         info.setVisible(False); panels.setVisible(False)
+         grid.removeWidget(cell); grid.addWidget(cell, 0, 0, rowCount, columnCount)
+     QtCore.QTimer.singleShot(0, self._sam_refresh_views)
```

**`_sam_label_mouse()`（:5043-）** — 入参由 QMouseEvent 改为 {x,y,button}
```diff
- def _sam_label_mouse(self, axis, ev):
-     px, py = ev.position().x(), ev.position().y()
-     if ev.button() == RightButton: positive = False
+ def _sam_label_mouse(self, axis, info):
+     px, py = float(info.get("x", 0.0)), float(info.get("y", 0.0))
+     if info.get("button") == RightButton: positive = False
```

**`_sam_reset()`** — 新数据退出放大态
```python
+ if getattr(self, "sam_expanded_axis", None) is not None:
+     self._sam_set_expanded(None)
```

**`_build_saved_roi_panel()`** — 存面板引用
```python
+ self.sam_roi_group = grp
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 页签定位 | `insertTab(1, ...)` | 需要 MPR 在「渲染」左侧，插入比追加简单 |
| 放大实现 | QGridLayout 跨格 (0,0,2,2) + 隐藏其它 | 无需重建视图，尺寸自适应最快 |
| 单击/双击冲突 | 220ms 定时器延时派发单击 | 双击会先产生 press，避免误加提示点 |
| 附属面板 | 放大时一并隐藏 | 让视图真正"占据整个页面" |

## 6. 遗留问题与待办

- [ ] 真机交互（真实双击）未自动化验证；已用 offscreen 功能测试断言布局跨格与显隐，逻辑等价。
- [ ] 放大态未做"点击标题栏退出"，仅双击视图还原。

## 7. 下一轮建议

1. 载入病例后实测：双击轴向→铺满→双击还原；确认滚轮翻层与单击加点仍正常。
2. 可加 ESC 退出放大、或放大时显示"双击还原"浮层提示。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | offscreen 测试 | VTK 无 GL 上下文报 shader 错误（仅 offscreen 环境） | ⚠️ 不影响功能，断言全通过 |

---

---

## 历史轮次
<details>
<summary>Round R-10 — 2026-09-25 | GUI 文案爱宠化（病人/患者 → 爱宠） (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 10 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求把 GUI 中所有「病人」内容改成「爱宠」。本轮对界面文案做了全局替换：`ssd_vr_viewer.py`（14 处）、`mcp_ssd_vr/dicom.py`（9 处，含扫描默认名）、`tools/screenshots/capture_modes.py`（3 处日志），共 **26 处** `病人/患者 → 爱宠`；并把爱宠信息 tooltip 的「姓名」改为「名字」。已确认相关文件 **0 残留**，`py_compile` 通过，截图确认页签「爱宠列表」、提示「爱宠 → 序列」、占位符「放爱宠数据的根目录…」、列头「爱宠 / 序列」、tooltip「爱宠ID / 名字」。内部标识（`patient_id`、`_patient_scan`、`kind:"patient"` 等）保持不变以免影响逻辑。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 定位所有病人文案 | ✅ | 三文件共 26 处 |
| 2 | 替换为爱宠 | ✅ | 病人/患者 → 爱宠；姓名 → 名字 |
| 3 | 验证 | ✅ | 0 残留 + py_compile + 截图 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 14 处文案：tab/提示/占位/列头/tooltip/状态/注释 |
| `mcp_ssd_vr/dicom.py` | 修改 | 9 处注释与「未知病人」默认名 |
| `tools/screenshots/capture_modes.py` | 修改 | 3 处日志文案 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_patient_section()`（:2372-2411）** — 界面文案
```diff
- "智能递归扫描：按 DICOM 标签归成「病人 → 序列」两级 ..."
+ "智能递归扫描：按 DICOM 标签归成「爱宠 → 序列」两级 ..."
- placeholder_text="放病人数据的根目录..."
+ placeholder_text="放爱宠数据的根目录..."
- ["病人 / 序列", "VR", "ID / 模态", "检查日期", "层数", "大小MB"]
+ ["爱宠 / 序列", "VR", "ID / 模态", "检查日期", "层数", "大小MB"]
```

**`_patient_scan()` 结果树（:2500-2501, :2562）** — tooltip 与状态
```diff
- f"患者ID: {p['patient_id'] or '—'}\n"
- f"姓名: {p['patient_name'] or '—'}\n"
+ f"爱宠ID: {p['patient_id'] or '—'}\n"
+ f"名字: {p['patient_name'] or '—'}\n"
- f"扫描完成：{res['patient_count']} 个病人 / {res['series_count']} 个序列"
+ f"扫描完成：{res['patient_count']} 个爱宠 / {res['series_count']} 个序列"
```

### 文件: `mcp_ssd_vr/dicom.py`

**`scan_patients()`（:589）** — 缺省名
```diff
- pkey = s["patient_id"] or s["patient_name"] or s["study_uid"] or "未知病人"
+ pkey = s["patient_id"] or s["patient_name"] or s["study_uid"] or "未知爱宠"
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 替换范围 | 用户可见文案（含注释/日志） | 用户要"GUI 中所有病人内容" |
| 内部标识 | 不改（patient_id / _patient_scan / kind:"patient"） | 避免牵动 DICOM 字段与逻辑，零风险 |
| 「姓名」 | 改为「名字」 | 宠物语义更自然 |

## 6. 遗留问题与待办

- [ ] 文档（README / HANDOVER 历史轮次）中仍有「病人」措辞，属非 GUI；如需统一可再处理。
- [ ] DICOM 标准字段 PatientName/PatientID 本身不改（数据标准）。

## 7. 下一轮建议

1. 若想彻底宠物化，可把列头「ID / 模态」调整为「编号 / 模态」等。
2. 载入真实病例验证列表显示与 tooltip 文案。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-9 — 2026-09-25 | 复古拟物皮肤 + 卡通猫吉祥物动画 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 9 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户（/custompy）要求界面更**复古**、更**拟物化**、更**三维**，并加入**可爱的卡通动画**。本轮新增两件事：① 新建复古拟物皮肤 `retro.qss`（凸起/凹陷斜面 bevel + 纵向渐变 gloss + 粗描边 + 圆角，营造"实体按键/黄铜面板"的立体手感），并新增 `ViewerWindow._apply_retro_skin()` 把同样的皮肤**直接下发**给 PyCt6 组件（CButton/CComboBox/CSlider/CLineEdit，因为它们用自身样式表覆盖应用级 qss）；② 新增 `CatMascot` 卡通猫吉祥物（纯 QPainter 手绘：呼吸起伏、尾巴摇摆、周期眨眼、忙碌时冒爱心），放在状态栏左侧，并在加载 DICOM 时进入"活泼"状态。皮肤优先级改为 `retro.qss > light.qss > dark.qss`。已验证：py_compile 通过；Qt 探针确认状态栏可见(h=57)、吉祥物可见(h=52)；截图确认三页签与拟物控件外观。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 复古拟物 QSS | ✅ | retro.qss：斜面/渐变/凹槽 |
| 2 | PyCt6 皮肤下发 | ✅ | `_apply_retro_skin()` |
| 3 | 卡通猫吉祥物 | ✅ | `CatMascot` + 状态栏 + 忙碌联动 |
| 4 | 验证 | ✅ | py_compile + Qt 探针 + 截图 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `retro.qss` | 新建 | 复古拟物浅色暖橙皮肤（全控件） |
| `ssd_vr_viewer.py` | 修改 | 新增 `CatMascot` 类；`_apply_retro_skin()`；状态栏放吉祥物；qss 优先级；load_dicom 忙碌联动；导入 QPainterPath/QLinearGradient/QPolygonF/QBrush |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增类 `CatMascot`（ViewerWindow 之前）** — 手绘卡通猫，QTimer 40ms 驱动
```python
+ class CatMascot(QtWidgets.QWidget):
+     def __init__(self, parent=None):
+         self.setFixedSize(140, 52); self._timer.start(40)
+     def set_busy(self, on): self._excited = bool(on)
+     def _tick(self):
+         self._t += 0.30 if self._excited else 0.13     # 忙碌时更活泼
+         ... # 眨眼计时 + 冒爱心
+     def paintEvent(self, ev):
+         # 尾巴 cubicTo 摇摆 / 身体圆角渐变 / 耳朵抖动 / 眼睛眨眼 / 鼻子嘴胡须 / 爱心
```

**`_build_ui()` 末尾** — 应用皮肤 + 放吉祥物
```python
+ self._apply_retro_skin()
+ self.cat_mascot = CatMascot(self)
+ self.statusBar().addWidget(self.cat_mascot)
```

**新增方法 `_apply_retro_skin()`** — 给 PyCt6 内层控件下发斜面渐变皮肤
```python
+ for b in self.findChildren(CButton):
+     b.button().setStyleSheet(btn_qss)     # 凸起渐变 + 四向 bevel + 按压缩进
+ for e in self.findChildren(CLineEdit): ...
+ for cb in self.findChildren(CComboBox): ...
+ for s in self.findChildren(CSlider):
+     s.slider().setStyleSheet(slider_qss)  # 金属凹槽 + 立体手柄
```

**`load_dicom()`（:4240 / :4609）** — 猫咪进入/退出忙碌
```python
+ if hasattr(self, "cat_mascot"): self.cat_mascot.set_busy(True)
...
+ if hasattr(self, "cat_mascot"): self.cat_mascot.set_busy(False)
```

**`main()`** — 皮肤优先级
```diff
- qss_path = .../"light.qss" (→ dark.qss)
+ qss_path = .../"retro.qss"; 缺失→ light.qss → dark.qss
```

**导入**：`from PySide6.QtGui import (..., QPainterPath, QLinearGradient, QRadialGradient, QPolygonF, QBrush)`

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 三维效果实现 | QSS 渐变 + 四向 bevel 边框（Qt 无 box-shadow） | QSS 原生能力内最接近拟物 |
| PyCt6 组件 | 运行时 `findChildren` 下发皮肤 | 其 setStyleSheet 会覆盖应用级 qss |
| 吉祥物实现 | 纯 QPainter 手绘，不依赖图片 | 便于动画（眨眼/摇尾），体积小 |
| 吉祥物位置 | 状态栏左侧 + 加载时加速 | 常驻可见且不遮挡阅片 |

## 6. 遗留问题与待办

- [ ] 本机多显示器 + 前台窗口竞争导致**屏幕/PrintWindow 截图取不到窗口底部状态栏**；已用 Qt 内部探针确认可见，如需截图证据可在单显示器/置前环境下复测。
- [ ] `_apply_retro_skin` 在主题切换（`_change_theme`）后可能被 PyCt6 覆盖；当前仅启动时应用一次，够用。
- [ ] 若想更强的复古感：可加木纹/纸纹背景图、九宫格按钮贴图。

## 7. 下一轮建议

1. 单显示器环境下确认状态栏猫咪动画（呼吸/摇尾/眨眼）与载入时的爱心。
2. 如需，把 `_apply_retro_skin` 挂到主题变化回调，保证换肤后仍保持拟物。
3. 可扩充吉祥物交互（点击叫一声/换姿势）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | 截图 | 多显示器/叠窗导致 GetWindowRect+CopyFromScreen 与 PrintWindow 均未取到窗口底部；非程序问题 | ⚠️ 已用 Qt 探针替代验证 |

---

---

## 历史轮次
<details>
<summary>Round R-8 — 2026-09-25 | 菜单三级化（爱宠列表/渲染/MPR阅片）+ 浅色明快橙主题 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 8 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求（/custompy）把左侧菜单层级改为 **爱宠列表 / 渲染 / MPR阅片** 三级，并针对宠物影像设计更轻松的配色——先是橙配色，随后明确改为**浅色、明快的暖橙主题**。本轮完成：① 把原「渲染」页内嵌的「病人列表」子页提升为顶层 **爱宠列表** 页，删除自绘子页切换条与 `QStackedWidget`，顶层只剩三页签；② 新建 PyCt6 主题 `warm_orange.json` 与 **`light.qss`**（暖白底 + 亮橙强调），`set_appearance_mode("light")`；③ VTK 渲染背景改为明亮奶油色、默认亮背景；④ 全量替换残留的冷色/深色内联色值与自绘 `RangeSlider` 的紫红渐变；⑤ 同步更新截图工具对旧子页 API 的引用。截图校验：三页签正常、浅色明快橙主题生效、MPR 页占满窗口。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 菜单三级化 | ✅ | 爱宠列表 / 渲染 / MPR阅片；删子页切换 |
| 2 | 深色暖橙主题 | ⏭️ 被替换 | 先做暖橙深色，用户改要浅色 |
| 3 | 浅色明快主题 | ✅ | light.qss + warm_orange.json + light mode |
| 4 | 内联色/控件重着色 | ✅ | 31 处色值 + RangeSlider + 选择行 |
| 5 | 验证 | ✅ | py_compile + 截图（三页签/渲染页/MPR页） |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 菜单三级化；light mode + light.qss；亮背景；内联色值重映射；RangeSlider 橙化 |
| `light.qss` | 新建 | 浅色明快暖橙 QSS（暖白底 + 亮橙） |
| `warm_orange.json` | 新建 | PyCt6 自定义主题（浅/深双色，浅色索引生效） |
| `dark.qss` | 保留 | 深色主题留作回退（main 里 fallback） |
| `tools/screenshots/capture_modes.py` | 修改 | 改用 `pages.setCurrentIndex(_tab_index_by_text(...))` |
| `tools/screenshots/capture_gui.py` | 修改 | 同上 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**`_build_ui()`（:1689-）** — 病人列表提升为顶层页签，渲染页去掉子页
```diff
- render_page = QWidget(); render_outer = QVBoxLayout(render_page)
- _seg_qss = (...); _bar = ...; self.subtab_patient = QPushButton("病人列表")
- self.subtab_params = QPushButton("渲染参数")
- self.render_stack = QStackedWidget(); render_outer.addWidget(self.render_stack,1)
- self.render_stack.addWidget(patient_page); self.render_stack.addWidget(params_page)
- self.subtab_patient.setChecked(True)
+ patient_page = QWidget(); patient_layout = QVBoxLayout(patient_page)
+ self._build_patient_section(patient_page, patient_layout)
+ self.pages.addTab(patient_page, "爱宠列表")
+ render_page = QWidget(); render_layout = QVBoxLayout(render_page)
```
（`render_scroll` 仍以 `render_page` 包装并 `addTab(..., "渲染")`；MPR 页签在 `_build_sam_tab` 内 `addTab(..., "MPR 阅片")`。）

**`_load_series_stable_cpu()`（:2470）** — 加载完成回「渲染」页
```diff
- self.render_stack.setCurrentIndex(1); self.subtab_params.setChecked(True)
+ _ri = self._tab_index_by_text("渲染")
+ if _ri >= 0: self.pages.setCurrentIndex(_ri)
```

**`main()`（:5507-）** — 浅色模式 + light.qss
```diff
- set_appearance_mode("dark")
+ set_appearance_mode("light")
- theme_path = .../"scientific.json"; set_color_theme(theme_path)
+ theme_path = .../"warm_orange.json"
+ set_color_theme(theme_path if os.path.exists(theme_path) else "orange")
- qss_path = .../"dark.qss"
+ qss_path = .../"light.qss"
+ if not os.path.exists(qss_path): qss_path = .../"dark.qss"
```

**`_build_ui()` 渲染背景 / `toggle_background()`（:1983,2019,3128）** — 亮奶油背景
```diff
- self.renderer.SetBackground(0.10,0.07,0.05)  # 深棕
+ self.renderer.SetBackground(0.97,0.95,0.92)  # Bright cream
- self.seg_renderer.SetBackground(0.08,0.055,0.04)
+ self.seg_renderer.SetBackground(0.94,0.91,0.86)
```

**`RangeSlider.paintEvent()`（:1008）** — 紫红渐变 → 明快橙
```diff
- painter.setBrush(QColor(60,24,34))     # 深轨道
+ painter.setBrush(QColor(236,222,202))  # 暖灰轨道
- painter.setBrush(QColor(140,48,64))    # 选中区
+ painter.setBrush(QColor(240,140,30))   # 橙
- painter.setPen(QColor(130,90,100)); painter.setBrush(QColor(215,185,194))
+ painter.setPen(QColor(212,118,26)); painter.setBrush(QColor(255,168,66))
```

**内联色值批量重映射（31 处）**：`#d9b48a→#a8641a`、`#ccb59a→#7a6650`、`#241c15→#fffdf9`、`#4a3a2c→#e2d3bf`、`#2c2218→#fbf3e8`、`#b7c98a→#4e7a2e`、`#e08a72→#c0392b`、选择行 `#f0a95a/#d4761a/#f6e5c8` 调为浅桃色等。

### 文件: `light.qss`（新建，全文）

暖白底 `#fdf8f2`、面板 `#fffdf9`、描边 `#e9dac2`、正文 `#3a2e22`、强调 `#f08c1e`；含 QTab/QGroupBox/QCheckBox/QSpinBox/QProgressBar/QComboBox/QScrollBar/QSplitter/QTree/QTable/QHeader 全套浅色规则。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 菜单层级 | 病人列表独立为顶层「爱宠列表」 | 用户明确要三级：爱宠列表/渲染/MPR阅片 |
| 子页切换条 | 删除（QStackedWidget 移除） | 顶层页签取代，减少嵌套 |
| 配色方向 | **浅色/明快**（用户二次澄清后从深色暖橙切换） | 用户要“浅色、明快背景” |
| 主题实现 | 新建 `warm_orange.json` + `light.qss`，保留 dark.qss 回退 | PyCt6 管组件、QSS 管原生控件，两层一致 |
| VTK 背景 | 明亮奶油色，默认亮背景 | 与浅色 UI 统一 |

## 6. 遗留问题与待办

- [ ] 运行时冒烟已用 mar 环境截图验证三页签与浅色主题；仍需在真实 DICOM 载入后确认 MPR 灰度与亮背景观感。
- [ ] 亮度/对比可在 `light.qss` 顶部配色注释处微调。
- [ ] 若需要深浅切换按钮，可在运行时 `set_appearance_mode` + 换 qss（dark.qss 已保留）。
- [ ] `_load_slicer_presets` 注释仍提到 dark.qss（文案，无功能影响）。

## 7. 下一轮建议

1. 载入真实病例，检查浅色主题下 MPR 阅片、ROI 绿色高亮、病人列表配色。
2. 如需，加一个「浅色/深色」切换按钮（主题文件已齐备）。
3. 继续打磨阅片器交互（窗宽窗位鼠标调、测量）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | error | edit | 批量色值脚本后出现 IndentationError（toggle_background 的 else 分支被误改缩进） | ✅ 已修复并 py_compile 通过 |

---

---

## 历史轮次
<details>
<summary>Round R-7 — 2026-09-25 | 用 logo.jpg 制作程序图标（.ico/.png/.icns + 运行时/构建接入）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 7 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求把 `logo.jpg`（猫咪插画）做成程序的**运行图标**。本轮用 Pillow 将该图按边距取色补成正方形后，导出 Windows 多尺寸 `logo.ico`(16–256)、跨平台 `logo.png`(256) 与 macOS `logo.icns`(PNG 分块 16–1024)。运行时把 `ViewerWindow` 的图标加载改为按 `.ico/.png/.jpg` 优先级查找的 `_icon_path()`（兼容 PyInstaller `_MEIPASS`），并在 `main()` 里设置 `QApplication.setWindowIcon` 与 Windows `AppUserModelID`（任务栏图标）。构建侧把 `build.yaml`、`ssd_vr_viewer_macos.spec` 与 3 个 GitHub workflow 的 `logo.jpg` 全部替换为新图标文件，并把图标加入 add-data/datas 以便冻结后运行时仍能加载。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 生成图标文件 | ✅ | .ico/.png/.icns 由 logo.jpg 派生，补方 + 多尺寸 |
| 2 | 运行时接入 | ✅ | `_icon_path()` + setWindowIcon + AppUserModelID |
| 3 | 构建接入 | ✅ | build.yaml / macos.spec / 3 workflows |
| 4 | 验证 | ✅ | py_compile；ICO/ICNS 结构校验；视觉预览通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `logo.ico` | 新建 | Windows 图标，7 尺寸 (16/24/32/48/64/128/256) |
| `logo.png` | 新建 | 256×256，运行时/跨平台 |
| `logo.icns` | 新建 | macOS 图标，PNG 分块 16–1024 |
| `ssd_vr_viewer.py` | 修改 | 新增 `_icon_path()`；`ViewerWindow` 用它；`main()` 设 app 图标 + AppUserModelID |
| `build.yaml` | 修改 | icon/datas/--icon/--add-data 改 logo.ico(+png) |
| `ssd_vr_viewer_macos.spec` | 修改 | datas 用 logo.png；EXE/BUNDLE icon='logo.icns' |
| `.github/workflows/main.yml` | 修改 | --icon=logo.ico；add-data logo.ico/logo.png |
| `.github/workflows/main-full.yml` | 修改 | 同上 |
| `.github/workflows/macos.yml` | 修改 | create-dmg --volicon logo.icns |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |
| `logo_512.png` | 生成后删除 | 未被引用，清理 |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增 `_icon_path()`（:1126）** — 跨平台/冻结目录定位图标
```python
+ def _icon_path() -> str:
+     candidates = []
+     if getattr(sys, "frozen", False):
+         candidates.append(getattr(sys, "_MEIPASS", ""))
+         candidates.append(os.path.dirname(sys.executable))
+     candidates.append(os.path.dirname(os.path.abspath(__file__)))
+     for d in candidates:
+         for name in ("logo.ico", "logo.png", "logo.jpg"):
+             if os.path.exists(os.path.join(d, name)):
+                 return os.path.join(d, name)
+     return ""
```

**类 `ViewerWindow.__init__`（:1147）** — 用统一解析替换硬编码 logo.jpg
```diff
- icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.jpg")
- if os.path.exists(icon_path):
-     self.setWindowIcon(QIcon(icon_path))
+ _ic = _icon_path()
+ if _ic:
+     self.setWindowIcon(QIcon(_ic))
```

**函数 `main()`（:5523）** — 应用级图标 + Windows 任务栏标识
```python
+ if sys.platform == "win32":
+     ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ssdvr.fusion.viewer")
+ app = QtWidgets.QApplication(sys.argv)
+ _app_icon = _icon_path()
+ if _app_icon:
+     app.setWindowIcon(QIcon(_app_icon))
```

### 文件: `build.yaml`

```diff
- icon: logo.jpg
+ icon: logo.ico
- --add-data="logo.jpg;."
+ --add-data="logo.ico;."
+ --add-data="logo.png;."
- --icon="logo.jpg"
+ --icon="logo.ico"
```

### 文件: `ssd_vr_viewer_macos.spec`

```diff
- ('logo.jpg', '.'),
+ ('logo.png', '.'),
- icon='logo.jpg',
+ icon='logo.icns',
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 原图非正方形 | 采样边框中位色补成正方形再加 LANCZOS 缩放 | 保留整只猫，避免裁剪；补色与原背景融合 |
| 运行时格式 | 优先 .ico → .png → .jpg | Windows 用 ico，其它平台 png；保留 jpg 兜底 |
| icns 生成 | 自写 ICNS（ic07–ic14 + icp4/5 PNG 分块） | Windows 环境无 iconutil，纯 Python 可生成有效 icns |
| 图标入包 | 加入 add-data/datas | 冻结后 `_MEIPASS` 里仍能取到，运行图标不丢 |

## 6. 遗留问题与待办

- [ ] 运行时冒烟仍待用户 PySide6 环境验证（窗口/任务栏图标是否显示）。
- [ ] `logo.icns` 未经 macOS 真机/iconutil 校验；若 CI 报 icns 无效，可退回用 iconutil 在 mac 上重制。
- [ ] 原 `logo.jpg` 保留未删（作为兜底 + 源文件）。

## 7. 下一轮建议

1. 用真实环境跑 `python ssd_vr_viewer.py`，确认任务栏与窗口左上角均为猫咪图标。
2. 触发一次 `main-full.yml` 构建，确认 EXE 图标（资源管理器缩略图）正确。
3. 若需更精致图标，可给原图加圆角/描边后重新生成。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | - | - | 本轮无错误 | - |

---

---

## 历史轮次
<details>
<summary>Round R-6 — 2026-09-25 | 石头/兽医阅片器改造：删 K-edge/自动ROI，MPR 放大，2D/MRI 分流 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 6 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要把本程序改造成**宠物/兽医 CT · DR · MRI 的离线 DICOM 阅读器**，保留当前 PySide6/VTK 桌面程序做减法。本轮实施了 5 项：① 删除「PCCT K-edge」整页及其方法/渲染层/`kedge_preprocess.py`；② 删除「自动ROI检测」整页及其方法/`ROIPipeline` 依赖；③ 渲染层数 3→2 并加固外部依赖（`gui_bridge.py`、截图工具）；④ 把「3D SAM」页的 MPR 升级为**主阅片器**（占满窗口、滚轮翻层、视图放大、更名「MPR 阅片」）；⑤ `build_reader` 增加**模态识别 + 2D(DR) 判定**，CT 走 HU 窗、MR/DR 走自适应灰度，2D 自动切阅片页；⑥ MCP 桥改为**默认关闭**。语法与 AST 静态检查通过；运行时冒烟因当前 Python 缺 PySide6/VTK 未能执行，留待用户环境。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 需求理解 | ✅ | 纯离线阅读器；复用当前程序 |
| 2 | 资产盘点 | ✅ | 保留/改造/删除/新建 四类清单 |
| 3 | 改造方案 | ✅ | 删两页 + MPR 放大 + 2D/MRI 分流 |
| 4 | 关键代码修改 | ✅ | 见 §4 |
| 5 | 依赖加固 | ✅ | gui_bridge / 截图工具 |
| 6 | 验证 | ⚠️ 部分 | py_compile + AST 通过；运行时冒烟受环境限制未跑 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| `ssd_vr_viewer.py` | 修改 | 主改造：删 K-edge/自动ROI 两页与全部相关方法；层数 3→2；MPR 放大；模态分流；MCP 默认关。-24785 字符 |
| `kedge_preprocess.py` | 删除 | PCCT K-edge 预处理脚本，随页面一并移除 |
| `mcp_ssd_vr/gui_bridge.py` | 修改 | `_trigger_roi` 加 hasattr 守卫并返回明确错误；roi_cancel/clear 加守卫 |
| `tools/screenshots/capture_modes.py` | 修改 | 截图标签列表去掉「自动ROI检测/PCCT K-edge」，改为「MPR 阅片」 |
| `session-dashboard.html` | 更新 | 本轮阶段与时间线 |
| `ssd_vr_viewer.py.bak_round5` | 新建 | 改造前完整备份（可删） |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**新增 `detect_modality()`（:333）** — 从 DICOM 头 `0008|0060` 读模态，供阅片分流
```python
+ def detect_modality(dicom_path: str) -> str:
+     if os.path.isdir(dicom_path):
+         names = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(dicom_path)
+         probe = sitk.ReadImage(names[0])
+     else:
+         probe = sitk.ReadImage(dicom_path)
+     if probe.HasMetaDataKey("0008|0060"):
+         return (probe.GetMetaData("0008|0060") or "").strip().upper()
```

**函数 `build_reader()`（:428）** — 记录模态与 2D 标志到模块全局
```python
+ global _LAST_LOAD_MODALITY, _LAST_LOAD_IS_2D
+ _LAST_LOAD_MODALITY = detect_modality(dicom_path) or "CT"
+ _LAST_LOAD_IS_2D = (image.GetDimension() < 3) or (
+     image.GetDimension() >= 3 and image.GetSize()[2] <= 1)
```

**函数 `ViewerWindow._on_tab_changed()`（:2087）** — 阅片页占满窗口（放大 MPR）
```python
+ is_reader = self.pages.tabText(index).startswith("MPR")
+ self.vtk_widget.setVisible(not is_reader)
+ self.splitter.setSizes([1, 0] if is_reader else [480, 1040])
```
（原方法只处理 K-edge 分屏 viewport，已整体替换。）

**函数 `ViewerWindow._build_sam_tab()`（:4395）** — MPR 视图放大 + tab 更名
```diff
- img_lb.setMinimumSize(160, 150)
+ img_lb.setMinimumSize(280, 240)
- sam_layout.addWidget(mpr_group)
+ sam_layout.addWidget(mpr_group, 1)   # MPR 占满剩余高度（主阅片区）
- sam_layout.addStretch()
- self.pages.addTab(scroll, "3D SAM")
+ self.pages.addTab(scroll, "MPR 阅片")
```

**类 `SamSliceLabel`（:1055+）** — 滚轮翻层信号
```python
+ scrolled = QtCore.Signal(int, int)    # (axis, slice_delta)
+ def wheelEvent(self, ev):
+     delta = ev.angleDelta().y()
+     if delta:
+         self.scrolled.emit(self.axis, -1 if delta > 0 else 1)
+         ev.accept()
```

**函数 `_sam_render_view()`（:4699）** — 阅片灰度按模态分流
```python
+ if getattr(self, "modality", "CT") == "CT":
+     gray = np.clip((arrf * ww_scale + wl_offset + 1024.0) / 4096.0 * 255.0, 0, 255)
+ else:  # MR/DR：无 HU，按 1–99 百分位自适应归一化
+     lo, hi = np.percentile(arrf, 1.0), np.percentile(arrf, 99.0)
+     norm = (arrf - lo) / max(hi - lo, 1.0) * 255.0
+     gray = np.clip((norm - 128.0) * ww_scale + 128.0 + wl_offset * 0.25, 0, 255)
```

**函数 `load_dicom()`（:4015, :4344）** — 记录模态；2D 自动切阅片页
```python
+ self.modality = _LAST_LOAD_MODALITY
+ self.is_2d = _LAST_LOAD_IS_2D
...
+ if self.is_2d:
+     _rd = self._tab_index_by_text("MPR")
+     if _rd >= 0: self.pages.setCurrentIndex(_rd)
```

**函数 `main()`（:5520 附近）** — MCP 默认关闭 + 去掉 TS 权重/ROI 自动跳页
```diff
- # 桥默认开启 ...
- args.mcp = _env_mcp not in ("0","false","off","no")
+ # 纯离线阅读器：MCP 桥默认关闭；需要时 --mcp 或 SSD_VR_MCP=1
+ args.mcp = _env_mcp in ("1","true","on","yes")
- local_weights = .../totalseg_weights ; os.environ["nnUNet_results"] = local_weights
- singleShot(5000, ... setCurrentIndex(_tab_index_by_text("自动ROI检测")))
```

**删除内容（成块）**：`from segmentation.roi_pipeline import ROIPipeline`；`_build_ui` 内 ROI 页与 K-edge 页构建；`_kedge_tab_index / _run_kedge_preprocess / _clear_kedge_volumes / _load_kedge_data / _render_kedge_material`；`_scan_weight_dir / _on_browse_weights / _on_weight_path_changed / init_weight_path / on_roi_start / on_roi_cancel / _on_roi_task_changed / _set_roi_task_combo / on_roi_clear / _on_roi_progress / _on_roi_finished / _blocks_from_tree_item / _on_roi_tree_selection_changed / _on_roi_error`；`__init__` 中 `kedge_*` 与 `roi_pipeline/roi_results/roi_detect_*` 属性；`toggle_background` 中 kedge 背景行；`SetNumberOfLayers(3)→(2)`。

### 文件: `mcp_ssd_vr/gui_bridge.py`

**函数 `_trigger_roi()`（:489）** — 自动 ROI 已移除，返回明确错误而非 AttributeError
```python
+ if not hasattr(win, "on_roi_start"):
+     return {"ok": False, "error": "自动ROI 功能已移除（纯离线阅读器）；请用 MPR 阅片 + 3D SAM 手动分割"}
```

**op 分发（:273）** — roi_cancel/clear 加守卫
```python
+ if hasattr(win, "on_roi_cancel"): win.on_roi_cancel()
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 改造方式 | 复用当前程序做减法 | 用户确认；保留 DICOM I/O、3D 内核、打包 |
| MPR 放大手段 | 阅片 tab 激活时隐藏右侧 VTK 视图、全宽显示 | 最小改动即获得最大阅片面积，不重写布局 |
| 灰度分流位置 | 放在 `_sam_render_view`（MPR 显示层） | CT/MR/DR 共用一个阅片控件，一处分流 |
| 2D(DR) 处理 | `build_reader` 已 reshape 成 z=1 体，仅自动切页提示 | 免改体渲染主链，风险低 |
| ROI 基础设施 | 保留 `_apply_roi_pixel_replacement` 等 | 3D SAM 绿色高亮仍依赖它 |

## 6. 遗留问题与待办

- [ ] **运行时冒烟未跑**：当前 `python` 无 PySide6/VTK；请在项目环境执行 `python ssd_vr_viewer.py` 验证 2 个 tab、MPR 放大、滚轮翻层、DR/MR 灰度。
- [ ] 仍有 MCP 工具 `ssdvr_trigger_roi/ssdvr_list_roi_blocks/...` 存在但功能已移除（默认关闭 MCP 时无影响；调用会返回错误）。
- [ ] `ROI_RESULT_BED_OFFSET_MM`、`_sam_sync_roi_blocks`、`seg_pipeline/seg_visualizer` 等成为未用残留，可后续清理。
- [ ] `_external_dir()` 现已无调用点。
- [ ] 纯离线阅读器尚未做 DR 专用的 2D 单幅优化（当前靠 z=1 体 + MPR）；测量/标注、DICOM tag 浏览器仍未实现。
- [ ] `ssd_vr_viewer.py.bak_round5` 备份可在确认无误后删除。

## 7. 下一轮建议

1. 在真实环境跑一次：`python ssd_vr_viewer.py --input <犬猫病例目录>`，重点看 2D DR 与 MRI 的 MPR 灰度是否符合预期。
2. 给 MPR 加窗宽窗位鼠标交互（右键拖拽调 WW/WL）与长度/角度测量，做成真正可用的阅片器。
3. 清理 MCP 中已失效的 ROI 工具与 §6 的未用残留代码。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | LSP | `gui_bridge.py` PySide6 未解析等类型告警属既有环境噪声，非本轮引入 | ⚠️ 环境限制 |
| - | warn | 验证 | 无 PySide6/VTK，无法运行时冒烟；已用 py_compile + AST 静态检查替代 | ⚠️ 待用户环境验证 |

---

---

## 历史轮次
<details>
<summary>Round R-5 — 2026-09-25 | 系统结构与功能分析（四层架构 + 遺留清单）(点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer (animal_dicom)

> 轮次: Round 5 | 时间: 2026-09-25 | 模型: mimo-v2.6-flash-free | 会话: ses_arch_analysis

---

## 1. 任务摘要

用户要求「系统分析这个程序的结构和功能」。本轮以**只读方式**对 `I:\animal_dicom` 的 SSD+VR Fusion Viewer 全栈做了系统性结构/功能分析：梳理出四层架构（渲染内核 / PySide6 GUI / 分割包 / MCP 桥 + CLI），产出各子系统职责、调用链、12 种渲染模式、36 个 MCP 工具、事件/线程模型与一份死代码/坑清单。**未修改任何程序源码**，仅新建/更新了 `session-dashboard.html` 与本 `HANDOVER.md`。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 意图分析 | ✅ 完成 | 确定分析对象=SSD+VR Fusion Viewer（PySide6+VTK 单体 GUI + 分割 + MCP 桥 + CLI） |
| 2 | 结构扫描 | ✅ 完成 | 5633 行 viewer / 163 方法 / 4 tab；MCP 36 工具 26 op；segmentation 13 模块 |
| 3 | 深度阅读 | ✅ 完成 | 3 个 explore agent 并行分析 MCP 层、分割包、渲染核心 |
| 4 | 分析归纳 | ✅ 完成 | 四层架构 + 功能矩阵 + 死代码/坑清单 |
| 5 | 输出结论 | ✅ 完成 | 向用户输出结构化中文报告 |

状态图例: ✅ 完成 | ⚠️ 部分完成 | ❌ 失败 | ⏭️ 跳过 | ⏸️ 暂停

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| session-dashboard.html | 新建/更新 | 本轮会话仪表盘（5 阶段全部 done） |
| .handover_round5.md | 新建 | 本轮交接内容暂存（供 APPEND 到 HANDOVER.md） |
| HANDOVER.md | 更新 | 追加 Round 5，旧内容折叠进历史轮次 |
| （程序源码） | 无变更 | 纯只读分析 |

## 4. 关键代码 Diff 摘要

本轮为只读分析，无源码改动。核心发现以「文件:行号」形式固化如下，供后续接手者定位：

### 文件: `ssd_vr_viewer.py`

**内核 `build_reader()`（:403）** — DICOM→VTK 全预处理管线（SimpleITK 读→降采样→去噪→CLAHE→Frangi→距离场→2D TF→打包）。

**类 `FusionController._build_fused_transfer()`（:913）** — SSD/VR 融合 TF：`a_mix=1-(1-a_ssd)(1-a_vr)` + Exposure tonemap。

**函数 `on_mode_change()`（:3602）** — 12 种渲染模式分发，切模式重建整条管线。

**函数 `_map_block_to_vr_view()`（:4129）** — ROI 掩膜映射到（可能降采样的）VR 网格，**已知错位问题所在**。

**函数 `main()` / `__main__`（:5988 / :6083）** — `freeze_support()` 首行是 EXE 卡死的根因修复。

### 文件: `mcp_ssd_vr/gui_bridge.py`

**类 `_Dispatcher`（:184）** — QueuedConnection 把网络线程的 op 投递到 Qt 主线程执行；`_execute` 分发 26 个 op（:232-289）。

### 文件: `segmentation/roi_pipeline.py`

**类 `ROIPipeline(QThread)`（:21）** — 任务分发 total/total_v3/total_mr/brain_structures/synthseg/temporal_lobe；`finished_signal(list,label_map)`。

### 文件: `segmentation/sam_adapter.py`

**函数 `predict_from_volume()`（:218）** — 128³ 点 prompt 交互分割，前景 ZNorm + embedding 缓存 + 超窗多 crop 取 max。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 分析方式 | 只读 + 并行 explore agent | 用户只要分析，不要求改动；subagent 降低主上下文占用 |
| 分析粒度 | 架构层 + 关键调用链 + 问题清单 | 用户明确要"系统分析结构与功能" |
| 交互点击范式 | 未展开（文档已说明） | GUI 事件细节非本轮重点 |

## 6. 遗留问题与待办

- [ ] `debug-roi-result-drift.md` 标记的 ROI 整体偏移一个床位问题仍 `[OPEN]`，根因锁定在 `_map_block_to_vr_view`（:4129）的网格映射，可用 `.dbg/repro-roi-surface-misalignment.py` 复现。
- [ ] `mcp_ssd_vr` 协议 `timeout` 字段未启用；`wait_event` 会吞事件；`errors` 表无写入方（`ssdvr_query_errors` 恒空）——如需修复可作为单独一轮。
- [ ] `total_v3` 无专用标签映射，存在 id 错配风险。
- [ ] `segmentation/` 存在死代码（nnunet_adapter 桩、bone/tissue/vessel_detector、visualizer.py、旧 pipeline.py + pre/postprocessor）可考虑清理。

## 7. 下一轮建议

1. 若要修 ROI 偏移：从 `_map_block_to_vr_view`（:4129）+ `.dbg/repro-roi-surface-misalignment.py` 入手，核对 orig/vr spacing+origin 与 bbox 映射。
2. 若要补 MCP 能力：暴露桥侧已有的 `render_roi_labels`（多结构高亮）与 `use_2d_tf` 等参数为工具。
3. 若要精简代码库：清理上述死代码并补 `total_v3` 标签表。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | warn | task:explore | 首个 segmentation 分析 agent 被中断，已重新派发并成功 | ✅ 已解决 |

---

## 历史轮次
<details>
<summary>Round R-4 — 2026-08-31 | 新增「3D SAM」交互分割页 (点击展开)</summary>

# 项目交接文档 — SSD+VR Viewer MCP 封装

> 轮次: Round 4 | 时间: 2026-08-31 | 模型: deepseek-v4-pro | 会话: ses_ssdvr_mcp

---

## 1. 任务摘要

新增「3D SAM」交互分割页：加载 SAM-Med3D(medim, vit_b_ori, 128³) → 3×MPR 切片视图(轴/冠/矢)点击加正/负点 → 后台 128³ crop 推理 → VR 绿色高亮(复用 roi_array overlay)。权重 sam_med3d_turbo.pth(383MB, HF blueyo0/SAM-Med3D)已下载到 frame/SAM-Med3D-main/ckpt/ 并通过 medim 验证(CUDA 2.4s 加载)。集成闭环通过：合成 CT 球心 1 正点 → mask 召回~93% → VR 截图 2882 绿像素，清结果后归零。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 资源/模型 | ✅ | 下载权重+medim 加载验证 |
| 2 | adapter 改造 | ✅ | predict_from_volume（关键修复：model.eval + 零 low-res mask 初始 + 前景(x>0) ZNorm，误用整块 zscore 曾致 IoU 0.13） |
| 3 | 后台管线 | ✅ | segmentation/sam_pipeline.py SamSession/SamWorker |
| 4 | GUI 页 | ✅ | 新 tab「3D SAM」+ MPR 三视图 + 点击/负点/撤销/清空 + 绿 overlay |
| 5 | 闭环验证 | ✅ | 集成测试(合成CT 1点召回~93%，绿色高亮 2882px→0) |

## 3. 文件变更清单

| 文件 | 操作 | 摘要 |
|------|------|------|
| download_sam_med3d.py | 新建 | 下载 383MB 权重到 segmentation/config.SAM_CKPT，含 --check |
| frame/SAM-Med3D-main/ckpt/sam_med3d_turbo.pth | 新增 | 权重 402MB |
| segmentation/sam_adapter.py | 修改 | predict_from_volume: 128³ 以点区为中心 crop + 前景归一化 + eval + 零mask初始 + 多点 prompt；返回全卷 bool/prob |
| segmentation/sam_pipeline.py | 新建 | SamWorker(QThread 常驻, 信号 progress/finished/error + ensure 预热) + SamSession(request 信号桥接) |
| ssd_vr_viewer.py | 修改 | SamSliceLabel、_build_sam_tab(4th tab)、_sam_* 系列方法、closeEvent、load 后 _sam_reset |

## 4. 关键代码 Diff 摘要

### 文件: `segmentation/sam_adapter.py`

**函数 `predict_from_volume()`** — 交互点 prompt 分割
```python
+ def predict_from_volume(self, vol_zyx, pts_zyx, neg_pts_zyx=None, threshold=None, return_prob=False):
+     self._model.eval()
+     # 128³ 窗口包住全部点(锚=点包络中心)；越界补零；前景(x>0) ZNorm（复刻官方 val）
+     crop = np.zeros((tile,tile,tile), np.float32); crop[:aD,:aH,:aW] = content_zscore
+     # 单轮 sparse prompt + 零低清 mask 初始(与官方 no-GT 路径一致)
+     prev_low_res_mask = torch.zeros((1,1,32,32,32), dtype=torch.float, device=self.device)
+     sparse, dense = prompt_encoder(points=(pts_t, lbs_t), masks=prev_low_res_mask)
+     low,_ = mask_decoder(...); prob = sigmoid(interpolate(low, (128,128,128)))
+     full_prob[z0:z1,y0:y1,x0:x1] = prob_crop
```

### 文件: `ssd_vr_viewer.py`

**类 `SamSliceLabel`** — 切片点击信号
```python
+ class SamSliceLabel(QtWidgets.QLabel):
+     clicked = QtCore.Signal(int, object)   # (axis, QMouseEvent)
```

**函数 `_build_sam_tab()` / `_sam_render_view()` / `_sam_run()` / `_sam_write_overlay()`** — MPR 三视图由 vr_work_array 切片→灰度→绿 mask 叠加+十字标记；点击即自动跑 SAM；结果写 roi_array(=4096 绿) 显示于 VR。

## 5. 关键决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 模型 | SAM-Med3D(medim 128³) 而非 cached facebook/sam3 | 用户确认；sam3=图像/视频非 3D 体 |
| 归一化 | 前景(x>0) ZNorm（非整块 zscore） | 复刻官方；整块曾 IoU0.13→0.55 |
| 交互范式 | 多点累积 prompt + 单轮 decode；未用跨轮 mask 反馈 | 足够（1点召回93%@小球；大目标多点增长 IoU0.60） |
| 绿色高亮 | 直接复用 roi_array/roi_volume overlay(4096→(0.15,1,0.18)) | ROI/SAM 同绿，互斥显示，零新渲染 |

## 6. 遗留问题与待办

- [ ] GUI 尚未实测：任意角切片窗口缩放映射、超大目标跨 128³ 的多 crop 合并、CT 真实组织 (非合成) 的 SAM 质量。
- [ ] MCP `ssdvr_sam_*` 工具未加（本轮 GUI-first）。交互点击为 GUI 原生；程序化可用 ssdvr_trigger_roi 之外再加 `ssdvr_sam_predict(points)`。
- [ ] `_sam_load_model` 用 invokeMethod('ensure') 预热；未验证按钮路径（集成测试走首推自动加载）。
- [ ] viewer 改动需 `ssdvr_restart_gui()` 生效。

## 7. 下一轮建议

1. 真实 CT 载入后手工点器官验证 SAM 分割；若欠分割再加负点/多点 refine 或实现跨轮 mask 反馈。
2. 需要 MCP 化时加 ssdvr_sam_set_points/predict/clear 并推送 roi_done-like 事件。
3. 评估超 128³ 目标的多 crop 拼接策略(anchor 迁移)。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 状态 |
|------|------|------|------|------|
| - | error | 集成测试 | tick 定时器 close 后访问 image_data（测试自身问题） | ✅ 测试脚本内，不影响功能 |
| - | error | 早期 | 整块 zscore→SAM 欠分割(IoU0.13)；PySide slot 直接 .emit 报错→SamSession.request 桥接 | ✅ 已修复 |

---

## 历史轮次
<details>
<summary>Round R-3 — 2026-08-31 | 程序更新补充到 MCP (synthseg 支持)（点击展开）</summary>
# 项目交接文档 — SSD+VR Viewer MCP 封装

> 轮次: Round 3 | 时间: 2026-08-31 | 模型: deepseek-v4-pro | 会话: ses_ssdvr_mcp

---

## 1. 任务摘要

程序更新（ssd_vr_viewer.py / segmentation 包 / ssd_vr_cli.py）新增 **SynthSeg (DIPY) 脑部分割** 能力：viewer 增加 `roi_task_combo`（total/total_v3/total_mr/brain_structures/synthseg）与 `_set_roi_task_combo` 同步；segmentation 新增 `synthseg_detector.py`、`SYNTHSEG_LABELS`（32 结构，全 tissue 类）、ROIPipeline `_run_synthseg` 分支。本轮把该新能力补充进 MCP 封装。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 变更识别 | ✅ 完成 | roi_task_combo/synthseg/SYNTHSEG_LABELS/CLI --seg-task |
| 2 | MCP 补充 | ✅ 完成 | trigger_roi 透传 synthseg + 同步 GUI 任务下拉框 |
| 3 | 回归验证 | ✅ 完成 | launch/query_state/set_roi_weight_path/trigger_roi/shutdown 通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| mcp_ssd_vr/gui_bridge.py | 修改 | `_trigger_roi` 设置任务后调用 `win._set_roi_task_combo(task)` 同步 GUI 下拉框 |
| mcp_ssd_vr/tools/control.py | 修改 | `ssdvr_trigger_roi` 文档加入 `synthseg`（SynthSeg DIPY 脑结构，32 类，权重首次自动下载 ~3.2GB） |
| mcp_ssd_vr/server.py | 修改 | 指令说明加入 synthseg |

## 4. 关键代码 Diff 摘要

### 文件: `mcp_ssd_vr/gui_bridge.py`

**函数 `_trigger_roi()`** — 同步程序更新后新增的 ROI 任务下拉框
```diff
  win.roi_current_task = task
+ # 同步 GUI 的分割任务下拉框（程序更新后新增 roi_task_combo）
+ if hasattr(win, "_set_roi_task_combo"):
+     try:
+         win._set_roi_task_combo(task)
+     except Exception:
+         pass
  QtCore.QTimer.singleShot(0, win.on_roi_start)
```

### 文件: `mcp_ssd_vr/tools/control.py`

**函数 `ssdvr_trigger_roi()`** — 文档补充 synthseg
```diff
- task: total|total_v3|total_mr|brain_structures；不传或 auto 按 Modality 推断
+ task: total|total_v3|total_mr|brain_structures|synthseg；不传或 auto 按 Modality 推断
+ synthseg = SynthSeg(DIPY) 脑结构分割（CT/MR 任意对比度，32 结构，权重首次运行自动下载 ~3.2GB）
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| synthseg 支持方式 | task 字符串透传（bridge 不做特殊处理） | viewer ROIPipeline 已内置 `_run_synthseg` 分支 |
| GUI 任务同步 | `_set_roi_task_combo` 让 GUI 下拉框跟随 MCP 选择 | 程序更新后下拉框是当前任务权威来源，需保持一致 |
| dipy 依赖 | 已装 dipy 1.12.1（mar env） | SynthSeg 权重首次运行自动下载到 ~/.dipy/synthseg |

## 6. 遗留问题与待办

- [ ] SynthSeg 权重（~3.2GB）未预下载，首次触发 `synthseg` 分割时会自动下载（需联网+时间），可预先 `python -c "from dipy.nn.torch.synthseg import SynthSeg; SynthSeg(use_cuda=True).fetch_default_weights()"`。
- [ ] `brain_structures`（Dataset409）仍缺 license 权重。
- [ ] 更新 MCP 工具描述后需**重启 opencode 会话**生效；viewer 改动已由本轮回归验证覆盖（`ssdvr_restart_gui` 生效）。

## 7. 下一轮建议

1. 重启 opencode 会话后，用真实 CT/MRI 数据跑一次 `ssdvr_trigger_roi(task="synthseg")` → `ssdvr_wait_event(roi_done)` → `ssdvr_list_roi_blocks()` 验证 SynthSeg 全流程。
2. 有 license 后下载 brain_structures 权重并接入同一触发路径。
3. 为 MRI 分割结果补齐 total_mr 标签映射（当前用 CT 标签命名会不准确）。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | 无 | - | 本轮无运行时错误（回归验证全通过） | - |

---

## 历史轮次
<details>
<summary>Round R-2 — 2026-08-31 | 下载 total_mr / brain_structures 权重（点击展开）</summary>

# 项目交接文档 — SSD+VR Viewer MCP 封装

> 轮次: Round 2 | 时间: 2026-08-31 | 模型: deepseek-v4-pro | 会话: ses_ssdvr_mcp

---

## 1. 任务摘要

用户要求「下载 total_mr / brain_structures 权重」。已下载 TotalSegmentator MRI 权重到 `totalseg_weights/`（viewer 的 `nnUNet_results` 目录）：Dataset852（total_mr 3mm fast，detect_semantic 默认用）从 GitHub 下载（126MB），Dataset850/851（total_mr 1.5mm）从本机 `~/.totalsegmentator/nnunet/results` 复制。`brain_structures`（Dataset409，商业授权模型）用户选择暂时跳过（需 license）。

同步改进：新增 `download_ts_mr_weights.py` 脚本（一键下载/校验）；`ssd_vr_viewer.py::_scan_weight_dir` 增加 `total_mr`（850/851/852）与 `brain_structures`（409）任务识别。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 需求分析 | ✅ 完成 | total_mr 权重 + brain_structures 权重 |
| 2 | 任务→权重解析 | ✅ 完成 | total_mr fast=852、default=850/851、brain=409(商业) |
| 3 | 下载 total_mr | ✅ 完成 | 852 下载 + 850/851 复制 |
| 4 | 集成识别 | ✅ 完成 | _scan_weight_dir 支持 MRI/脑区任务 |
| 5 | brain_structures | ⏭️ 跳过 | 需 license（用户选择暂缓） |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| download_ts_mr_weights.py | 新建 | 下载 total_mr(850/851/852) 与 brain_structures(409) 权重的脚本，含 --check/--brain 参数，优先从本机默认权重目录复制 |
| ssd_vr_viewer.py | 修改 | `_scan_weight_dir` 的 TASK_DEFS 增加 total_mr 与 brain_structures |
| totalseg_weights/Dataset852_TotalSegMRI_total_3mm_1088subj | 新增 | total_mr fast 权重（126MB，GitHub v2.5.0-weights） |
| totalseg_weights/Dataset850_TotalSegMRI_part1_organs_1088subj | 新增 | total_mr 1.5mm 器官（从 ~/.totalsegmentator 复制，~251MB） |
| totalseg_weights/Dataset851_TotalSegMRI_part2_muscles_1088subj | 新增 | total_mr 1.5mm 肌肉（从 ~/.totalsegmentator 复制，~250MB） |

## 4. 关键代码 Diff 摘要

### 文件: `download_ts_mr_weights.py`（新建）

**主流程** — 下载/复制三类 MRI 权重；`--check` 校验；`--brain <license>` 走商业授权下载
```python
+ GITHUB_URLS = {
+     "Dataset852_TotalSegMRI_total_3mm_1088subj": f"{GITHUB}/v2.5.0-weights/...zip",
+     "Dataset850_TotalSegMRI_part1_organs_1088subj": ...,
+     "Dataset851_TotalSegMRI_part2_muscles_1088subj": ...,
+ }
+ def download_github(folder, url):
+     # 优先复制 HOME_RESULTS 已有权重，否则下载解压到 totalseg_weights/
+ def download_brain(license_number):
+     # 写 license 到 ~/.totalsegmentator/config.json 后调
+     # totalsegmentator.libs.download_model_with_license_and_unpack("brain_structures", ...)
```

### 文件: `ssd_vr_viewer.py`

**函数 `_scan_weight_dir()`** — 增加 MRI / 脑区任务识别
```diff
  TASK_DEFS = {
      "total":     {"label": "total (v2 1.5mm) 1559例", "ids": {291,292,293,294,295,297}},
      "total_v3":  {"label": "total_v3 (v3 1.5mm) 1830例", "ids": {831,832,833,834,835,836}},
+     "total_mr":  {"label": "total_mr (MRI) 1088例", "ids": {850,851,852}},
+     "brain_structures": {"label": "brain_structures (脑区16类, 商业授权)", "ids": {409}},
  }
```

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 权重存放目录 | `totalseg_weights/`（viewer 的 nnUNet_results） | viewer 启动即设 nnUNet_results=totalseg_weights，放这里才能被 detect_semantic 找到 |
| 850/851 获取方式 | 从 ~/.totalsegmentator 复制而非重新下载 | 本机已有完整权重（~500MB），省流量 |
| 852 下载源 | GitHub v2.5.0-weights 官方 release | 与 TASK_ID_WEIGHTS_CONFIGS 的 foldername/version 一致 |
| brain_structures | 暂缓（需 license） | 商业授权模型，用户无 license，选择跳过 |

## 6. 遗留问题与待办

- [ ] `brain_structures`（Dataset409）权重未下载：学术免费 license 申请 `https://backend.totalsegmentator.com/license-academic/`（教育邮箱），然后 `python download_ts_mr_weights.py --brain <license编号>`。
- [ ] `ROIPipeline`/`_on_roi_finished` 目前用 `TOTALSEG_TOTAL`（CT 标签映射）提取结构；total_mr 的标签 ID 不同，MRI 分割结果的解剖命名会不准确（需新增 total_mr 标签映射表才完整支持）。
- [ ] 无 MRI DICOM 数据，total_mr 推理未实测（仅校验权重结构正确：checkpoint_final.pth / plans.json 齐全）。
- [ ] viewer 改动后需 `ssdvr_restart_gui` 生效（MCP 会话内）。

## 7. 下一轮建议

1. 有 license 后运行 `python download_ts_mr_weights.py --brain <license>` 下载 Dataset409。
2. 若需完整支持 MRI 分割命名，为 `segmentation/roi_label_map.py` 增加 total_mr 标签映射（TotalSegmentator MRI 类表）。
3. 用真实 MRI 病例验证 `task="total_mr"` 的触发→roi_done 全流程。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| - | error | download_ts_mr_weights.py | LSP 报 `json possibly unbound`（import 位置） | ✅ 已解决（import json 移到函数顶部） |
| - | 提示 | backend | Dataset409 商业授权需 license | ⏭️ 用户选择暂缓 |

</details>
<details>
<summary>Round R-1 — 2026-08-31 | SSD+VR Viewer MCP 封装（35 工具 + TCP 桥 + viewer 改造，点击展开）</summary>

# 项目交接文档 — SSD+VR Viewer MCP 封装

> 轮次: Round 1 | 时间: 2026-08-31 | 模型: deepseek-v4-pro | 会话: ses_ssdvr_mcp

---

## 1. 任务摘要

用户要求「按照 MCP封装交接文档.md 方案，把程序封装为 opencode 的 MCP 服务」，本机运行环境为 conda 的 `mar`（D:\python\envs\mar，Python 3.11.14，torch 2.6.0+cu124，CUDA 可用）。

已完成：从零实现了 `mcp_ssd_vr/` 包（FastMCP stdio server + TCP 桥 + 事件推送 + 35 个 `ssdvr_*` 工具），并改造 `ssd_vr_viewer.py` 接入桥（`--mcp` 启动桥、load_busy 忙碌拒绝、load_done/roi_done/progress/error 事件推送、MCP 模式跳过模态框）。注册到 `opencode.json` 的 `mcp.ssd_vr`。端到端验证通过：launch → query_state → scan_case → load_dicom → load_done → screenshot（2044 色）→ set_mode/set_camera → shutdown 全闭环。

## 2. 阶段进度

| # | 阶段 | 状态 | 说明 |
|---|------|------|------|
| 1 | 意图分析 | ✅ 完成 | 解析封装需求与文档方案 |
| 2 | 环境调研 | ✅ 完成 | mar 环境 vtk9.3.1/SimpleITK/totalsegmentator/GPU 齐备；fastmcp 3.4.7 已安装 |
| 3 | 架构设计 | ✅ 完成 | TCP line-delimited JSON 桥 + 主线程 QueuedConnection 调度 |
| 4 | 编码实现 | ✅ 完成 | mcp_ssd_vr 包(13 文件) + viewer 改造 |
| 5 | 验证收尾 | ✅ 完成 | 端到端闭环验证通过 |

## 3. 文件变更清单

| 文件 | 操作 | 变更摘要 |
|------|------|---------|
| mcp_ssd_vr/__init__.py | 新建 | 包说明 |
| mcp_ssd_vr/config.py | 新建 | 路径/端口/解释器配置（默认 mar python） |
| mcp_ssd_vr/state.py | 新建 | 事件类型 + 渲染模式映射 + task 推断 |
| mcp_ssd_vr/context.py | 新建 | 工具层共享上下文单例 |
| mcp_ssd_vr/recorder.py | 新建 | SQLite + JSONL 双写记录 |
| mcp_ssd_vr/bridge_client.py | 新建 | TCP 客户端（MCP server 侧） |
| mcp_ssd_vr/gui_bridge.py | 新建 | TCP server + 主线程调度（GUI 进程内） |
| mcp_ssd_vr/dicom.py | 新建 | scan_case / resolve_series_path（DICOM 头分析） |
| mcp_ssd_vr/server.py | 新建 | FastMCP 入口（35 工具注册） |
| mcp_ssd_vr/tools/__init__.py | 新建 | 工具层注册器（热重载） |
| mcp_ssd_vr/tools/_util.py | 新建 | 共享 call/get_client 辅助 |
| mcp_ssd_vr/tools/lifecycle.py | 新建 | launch/restart_gui/reload_tools |
| mcp_ssd_vr/tools/control.py | 新建 | load_dicom/set_mode/opacity/camera/roi 等 24 工具 |
| mcp_ssd_vr/tools/dicom_scan.py | 新建 | scan_case |
| mcp_ssd_vr/tools/inspect.py | 新建 | status/state_snapshot/wait_event |
| mcp_ssd_vr/tools/capture.py | 新建 | screenshot |
| mcp_ssd_vr/tools/recording.py | 新建 | query_events/errors/tool_calls/export/sessions/hello |
| ssd_vr_viewer.py | 修改 | 接入桥（见 §4） |
| C:\Users\chris\.config\opencode\opencode.json | 修改 | 注册 mcp.ssd_vr |

## 4. 关键代码 Diff 摘要

### 文件: `ssd_vr_viewer.py`

**类 `ViewerWindow.__init__`** — 新增 MCP 状态字段
```python
  self.initial_input = initial_input or ""
+ self.load_busy = False
+ self.last_error = None
+ self._mcp_mode = False
+ self._mcp_bridge = None
```

**函数 `_mcp_push(etype, data)`** — 事件推送（无桥时安全 no-op）
```python
+ def _mcp_push(self, etype, data):
+     bridge = getattr(self, "_mcp_bridge", None)
+     if bridge is not None:
+         try: bridge.push_event(etype, data)
+         except Exception: pass
```

**函数 `load_dicom()`** — 忙碌拒绝 + 事件 + MCP 免模态框
```python
+ if self.load_busy:
+     self.last_error = "load in progress: busy"
+     self._mcp_push("error", {"error": self.last_error, "load_busy": True})
+     return
+ self.load_busy = True
+ self._mcp_push("load_start", {"path": dicom_path})
  ...
+ self._mcp_push("load_done", {"path": dicom_path, "dims": [...], "spacing": [...]})
  ...
+ finally:
+     self.load_busy = False
```

**函数 `main()`** — 新增 `--mcp` / `--mcp-port` 并启动桥
```python
+ parser.add_argument("--mcp", action="store_true", ...)
+ parser.add_argument("--mcp-port", type=int, default=7799, ...)
  ...
+ if args.mcp:
+     win._mcp_mode = True
+     from mcp_ssd_vr.gui_bridge import start_bridge
+     start_bridge(win, args.mcp_port)
```

### 文件: `mcp_ssd_vr/gui_bridge.py`（新建，核心）

**类 `_Dispatcher(QObject)`** — 跨线程主线程调度，`_cmd` 信号 QueuedConnection 到 `_on_cmd` 槽，`submit()` 用 per-request `threading.Event` 阻塞等待主线程执行结果。

**类 `GuiBridgeServer`** — TCP line-delimited JSON server，`push_event()` 广播事件，`_serve()` 端口占用自动向后探测。

### 文件: `mcp_ssd_vr/bridge_client.py`（新建）

**类 `BridgeClient`** — `call()` 同步请求/响应，`wait_event()` 阻塞等事件；修复了「reader 线程与 call 双重 pop pending 导致 no result」的竞态（`_handle_line` 改 `get` 不 pop，由 `call` 独占 pop）。

## 5. 关键决策与分支

| 决策点 | 选择 | 原因 |
|--------|------|------|
| MCP server 运行环境 | mar 环境（与 GUI 同环境） | mar 已含全部依赖；仅新增 fastmcp |
| 桥协议 | TCP line-delimited JSON（本地 127.0.0.1） | 与文档一致；进程分离，GUI 崩溃不影响 MCP |
| 桥实现 | 不改 viewer 过多，桥内 `_execute` 直接读写 ViewerWindow 属性 | 最小侵入，`--mcp` 才加载桥 |
| 截图 | vtkWindowToImageFilter → vtk_to_numpy → PIL | 避开 mar 环境 vtkPNGWriter 已知故障 |
| FastMCP 版本 | 3.4.7 | 新版同名工具 add_tool 直接覆盖（仅 WARNING），无需 monkeypatch |
| 事件缓存 | 客户端队列永不 drain，wait_event 直接消费 | 事件「已发生再等待」也能返回 |

## 6. 遗留问题与待办

- [ ] 需**重启 opencode 会话**后，`ssd_vr` MCP 工具才在模型侧可见（当前会话工具清单固定）。
- [ ] `fast` 参数（3mm vs 1.5mm）目前透传但 ROIPipeline 内部恒 `fast=True`，未做 1.5mm 分支（total_mr 需 Dataset850/851，本地未下载）。
- [ ] `brain_structures` 权重（Dataset409，商业授权）本地未安装，需 license 下载。
- [ ] `total_mr`/MRI 病例的 modality 推断依赖 SimpleITK 元数据，仅简单实现，未实测 MRI。
- [ ] 未实测真实大体积（50GB 血管铸型）加载与分割全流程（下采样/内存保护路径）。

## 7. 下一轮建议

1. 重启 opencode 会话，用真实 DICOM 病例跑一次完整标准工作流（§3.2 流程：status→launch→scan_case→load_dicom→wait_event(load_done)→screenshot→trigger_roi→wait_event(roi_done)→list_roi_blocks→render_roi_label）。
2. 若需 1.5mm 高精度 total_mr，下载 Dataset850/851 到 totalseg_weights 并让 ROIPipeline 支持 fast=False。
3. 多序列真实病例重点验证 `ssdvr_scan_case` 的 series 选择与 `resolve_series_path` 是否正确指向主序列。

## 8. 错误记录

| 时间 | 级别 | 来源 | 描述 | 解决状态 |
|------|------|------|------|---------|
| 00:30 | error | bridge_client | `call()` 返回 "no result"：reader 线程与 call 同时 pop pending 导致结果丢失 | ✅ 已解决（reader 改 get 不 pop） |
| 00:30 | error | 测试 | 合成 DICOM 全空气密度，截图 1 色（正确行为，非 bug） | ✅ 已解决（改用 -1000/250/800 HU 验证） |
| 00:40 | error | 测试 | fastmcp `list_tools()` 为异步函数，同步调用报错 | ✅ 已解决（reload 逻辑避开 list_tools） |

</details>
<details>
<summary>Round R-0 — 2025-07-23 | 准光子CT 2048×2048 全身血管铸型数据分析项目（旧交接文档，与本轮无关，点击展开）</summary>

# 准光子CT 2048×2048 全身血管铸型数据分析项目 — 交接文档

> **日期**: 2025-07-23  
> **数据**: 全球首例准光子CT 2048×2048矩阵重建，19岁男性全身血管铸型  
> **扫描参数**: 120 kVp, 螺旋模式, 0.156 mm 层厚, 0.244×0.244 mm 面内  
> **扫描覆盖**: Z=484–2,418 mm (1,934 mm), 12,398 层

---

## 1. 项目概述

对全球首例准光子探测器CT 2048×2048矩阵重建的19岁男性全身血管铸型数据集进行了系统性分析，涵盖体积组装、血管分割、形态计量、对称性分析、分辨率表征、三维体渲染和系统组学分析。

### 1.1 核心成果

| 交付物 | 文件 | 大小 |
|--------|------|------|
| **HTML 学术报告** | `vascular_research_report.html` | ~51 MB |
| **Word 草稿** | `准光子CT_全身血管铸型_中华放射学.docx` | ~37 MB |
| **DICOM 数据** | `01_Skull_Vault/` ~ `13_Foot_Ankle_Distal/` | ~50 GB |

### 1.2 关键定量发现

| 指标 | 值 |
|------|-----|
| 中位血管直径 | 1.48 mm |
| 最小可检测血管 | 0.275 mm |
| MTF10% 等效分辨率 | 0.136 mm |
| 亚毫米血管（全身上腹区） | 8,381 个 (2048²) → 0 个 (512²) |
| 峰值血管密度 | 上腹部 16.7% |
| 解剖区域数 | 11 个（修正后） |

---

## 2. 目录结构

```
good/
├── 01_Skull_Vault/          # Z=484–640mm, 1000 DICOMs
├── ...（共 13 个区域，按 Z 轴顺序）...
├── 13_Foot_Ankle_Distal/    # Z=2356–2418mm, 398 DICOMs
```

---

## 3. 环境配置

- **mar** 环境: `D:\python\envs\mar\` — VTK 9.6.1, SimpleITK 2.3.1, PySide6, PyCt6
- VR 渲染器: `ssd_vr_viewer.py`，启动 `D:\python\envs\mar\python.exe ssd_vr_viewer.py --input <DICOM目录>`

---

## 4-8. 关键技术要点 / 已完成章节 / 待办 / 文件映射

（详见旧文档全文，含：解剖标注修正 HFS 体位、字典序排序陷阱、SimpleITK 多文件夹混读、vtkPNGWriter 故障、SSD+VR 体渲染管线参数等）

</details>
</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>

</details>
