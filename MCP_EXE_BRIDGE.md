# 冻结版 EXE 的 MCP 控制桥 —— 任何 agent 直接接入

> 结论先说：**桥已经编进 EXE，不需要额外的编译步骤**。冻结版 `SSD_VR_Fusion_Viewer.exe`
> 现在**默认就开桥**（双击即可被 agent 接管），MCP server 端会自动发现端口。
> 本文记录架构、开关、发现机制、各客户端配置与实测数据。

---

## 1. 架构

```
┌──────────────────────────────── EXE 进程（SSD_VR_Fusion_Viewer.exe，PyInstaller onedir）┐
│  Qt GUI (ViewerWindow)                                                                │
│    └── mcp_ssd_vr/gui_bridge.py  ← 内嵌 TCP server（127.0.0.1:7799 起，被占则 +1）      │
│          · 主线程调度（QueuedConnection）：所有 op 都在 Qt 主线程执行                    │
│          · 事件推送：load_start / load_done / progress / roi_done / error / gui_ready  │
│          · 启动成功后写"发现文件"（%LOCALAPPDATA%\SSD_VR_MCP\bridge.json）              │
└───────────────────────────────────────────────────────────────────────────────────────┘
                                   ▲   TCP line-delimited JSON
                                   │   {"id","op","args"} → {"id","ok","data"}
                                   │   事件 {"type","data","ts"}
        ┌──────────────────────────┴───────────────────────────┐
        │  mcp_ssd_vr/server.py（FastMCP, stdio）—— 每个 agent 一份，互相独立 │
        │    tools/*  36 个 ssdvr_* 工具                        │
        │    bridge_registry.py  发现文件 + 协议握手 + 端口扫描  │
        └───────┬──────────────────┬──────────────────┬────────┘
                │                  │                  │
             DSH                opencode          Claude / 自写脚本
```

要点：

- **GUI 与 MCP server 是两个进程**。GUI 崩溃/重启不影响 agent 会话；MCP server 重启也不影响 GUI。
- **桥在 GUI 进程内**，直接操作 `ViewerWindow`（无截图点击、无 UI 自动化），因此稳定且快。
- **多个 agent 可以各自起一个 MCP server**，都连同一个 GUI 桥。⚠️ 同时操作会互相打断（见 §6）。

---

## 2. 启动开关

| 命令 | 行为 |
|---|---|
| `SSD_VR_Fusion_Viewer.exe` | **默认开桥**（双击即可被 agent 接管），端口 7799，被占自动 +1 |
| `SSD_VR_Fusion_Viewer.exe --no-mcp` | 纯 GUI，不开桥 |
| `SSD_VR_Fusion_Viewer.exe --mcp` | 显式开桥（兼容旧命令，行为同默认） |
| `SSD_VR_Fusion_Viewer.exe --mcp-port 7931` | 指定起始端口 |
| `SSD_VR_Fusion_Viewer.exe --input D:\case` | 启动即加载 DICOM（可与上面组合） |
| 环境变量 `SSD_VR_MCP=0` | 等价 `--no-mcp` |
| 环境变量 `SSD_VR_MCP_PORT=7931` | 等价 `--mcp-port 7931`（源码运行时也认） |

源码运行完全一致：`python ssd_vr_viewer.py` 也默认开桥，`--no-mcp` 关闭。

> 安全性：只 bind `127.0.0.1`，不监听外网。

---

## 3. 发现机制（agent 为什么不用配端口）

MCP server 端不再假设"桥一定在 7799"，按以下顺序找：

1. **发现文件**：GUI 桥绑定**成功之后**写入
   `%LOCALAPPDATA%\SSD_VR_MCP\bridge.json`（可用 `SSD_VR_BRIDGE_FILE` 覆盖）：

   ```json
   {
     "version": 1,
     "host": "127.0.0.1",
     "port": 7931,
     "pid": 45800,
     "started_at": 1789566002.4,
     "frozen": true,
     "exe": "I:\\SSD+VR_github\\SSD_VR_Fusion_Viewer_Full_Win\\SSD_VR_Fusion_Viewer.exe",
     "mode": "mcp"
   }
   ```

2. **协议握手**：连上端口后发一次 `{"id":"discover-probe","op":"query_state","args":{}}`，
   必须收到同 id 的桥响应才算数。
3. **顺序扫描**：`7799 … 7898`（`SSD_VR_MCP_PORT` 起 100 个）。

退出时（`--no-mcp` 之外的正常退出、`shutdown` op、关窗口）删除发现文件。

### 为什么必须握手，不能只做 TCP 连接

实测本机 **MaccCore 占用 7893-7895**（在默认扫描段内）。只做 `connect()` 的实现在
第一轮扫描就会把 MaccCore 误认成桥，`ssdvr_status` 报"已连接"但任何 op 都超时。
所以 `discover_port()` 一律用 `probe_bridge()`（TCP + 协议）判定；
`probe_port()` 只用于"端口上有没有东西"这种弱判断。

回归测试：`temp/patient_probe/test_exe_bridge.py` 的 `[A2] probe_bridge 协议握手`。

---

## 4. 各客户端配置

MCP server 的启动命令在所有客户端里都一样：

```
D:\python\envs\mar\python.exe   I:\SSD+VR_github\mcp_ssd_vr\server.py      （stdio）
```

### 4.1 DSH

`C:\Users\chris\.dsh\profiles\web\cordis.patch.yml` 已加入 `mcp-ssd-vr`（stdio）：

```yaml
    - id: mcp-ssd-vr
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: ssd_vr
        transport: stdio
        command: 'D:\python\envs\mar\python.exe'
        args:
          - 'I:\SSD+VR_github\mcp_ssd_vr\server.py'
        cwd: 'I:\SSD+VR_github'
        env:
          KMP_DUPLICATE_LIB_OK: 'TRUE'
          OMP_NUM_THREADS: '1'
          PYTHONIOENCODING: utf-8
          PYTHONUTF8: '1'
        toolCallTimeoutMs: 300000      # 语义分割/wait_event 要跑几分钟
        failOnStartupError: false
        reconnect: { enabled: true, maxAttempts: 5 }
```

工具名形如 `mcp__ssd_vr__ssdvr_launch`。**修改配置后需重启 DSH**（工具清单在会话启动时固定）。

### 4.2 opencode

`C:\Users\chris\.config\opencode\opencode.json` → `mcp.ssd_vr`（已存在）：

```json
"ssd_vr": {
  "type": "local",
  "command": ["D:\\python\\envs\\mar\\python.exe", "I:\\SSD+VR_github\\mcp_ssd_vr\\server.py"],
  "environment": {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "KMP_DUPLICATE_LIB_OK": "TRUE"
  },
  "enabled": true,
  "timeout": 300000
}
```

### 4.3 Claude Code / Claude Desktop

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "ssd_vr": {
      "command": "D:\\python\\envs\\mar\\python.exe",
      "args": ["I:\\SSD+VR_github\\mcp_ssd_vr\\server.py"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "KMP_DUPLICATE_LIB_OK": "TRUE" }
    }
  }
}
```

Claude Code CLI：`claude mcp add ssd_vr -- "D:\python\envs\mar\python.exe" I:\SSD+VR_github\mcp_ssd_vr\server.py`

### 4.4 不用 MCP 也行：裸 TCP

任何语言都能直接驱动（协议只有 3 行 JSON）：

```python
import json, socket
s = socket.create_connection(("127.0.0.1", 7931), timeout=5)
s.sendall(b'{"id":"1","op":"query_state","args":{}}\n')
print(json.loads(s.recv(65536).decode("utf-8")))   # {"id":"1","ok":true,"data":{...}}
```

25 个桥 op：`load_dicom, query_state, screenshot, shutdown, set_mode, set_opacity, set_camera,
set_window_level, set_ssd_threshold, set_vr_threshold, get_thresholds, set_cr_params, set_preprocess,
set_crop, toggle_background, get_render_params, apply_preset, list_presets, trigger_roi, roi_cancel,
roi_clear, list_roi_blocks, render_roi_label, set_custom_roi, set_roi_weight_path`。

### 4.5 其他 agent 怎么操作这个程序（三条路，按省事程度排序）

| 路线 | 适用 | agent 要做什么 | 实测 |
|---|---|---|---|
| **A. 标准 MCP 客户端** | Claude Code/Desktop、Cursor、Cline、Continue、DSH、opencode… | 只写一段配置（§4.1–4.3），重启客户端 | **PASS 19/19**（手写 JSON-RPC 握手 → `tools/list` 36 个工具 → `tools/call` 打到 GUI） |
| **B. 现成脚本** | 只能执行 shell 的 agent、临时排查、自动化脚本 | `python tools/ssdvr_agent_client.py call set_mode mode=cinematic` | 通过（`discover/call/wait/shot` 全通） |
| **C. 自己写 20 行** | 别的语言、嵌入式、想自己控制重试 | 照 §4.6 抄一段，或把 `mcp_ssd_vr/bridge_registry.py` 一起 vendor | Python / Node / PowerShell 三种都实测通过 |

三者操作的是**同一个正在运行的 GUI**：端口由发现文件 + 协议握手确定，agent 不需要知道端口。

### 4.6 各语言最小客户端（都实测过）

**Python（纯标准库）**

```python
import json, os, socket
reg = os.path.join(os.environ.get("LOCALAPPDATA", ""), "SSD_VR_MCP", "bridge.json")
port = json.load(open(reg, encoding="utf-8"))["port"]        # 或直接试 7799
s = socket.create_connection(("127.0.0.1", port), timeout=5)
s.sendall(json.dumps({"id": "1", "op": "set_camera",
                      "args": {"azimuth": 45, "elevation": 12}}).encode() + b"\n")
while True:                                                   # 事件行没有 id，跳过
    msg = json.loads(s.recv(65536).decode("utf-8").split("\n")[0])
    if msg.get("id") == "1":
        print(msg); break
```

**Node（无依赖）**：完整可复制版见 `tools/ssdvr_agent_client.js`

```js
const net=require("net"),fs=require("fs"),path=require("path");
const port=JSON.parse(fs.readFileSync(path.join(process.env.LOCALAPPDATA,"SSD_VR_MCP","bridge.json"),"utf8")).port;
const s=net.connect(port,"127.0.0.1",()=>s.write(JSON.stringify({id:"1",op:"query_state",args:{}})+"\n"));
s.on("data",d=>{process.stdout.write(d);s.end();});
```

**PowerShell（无依赖）**：完整可复制版见 `tools/ssdvr_agent_client.ps1`

```powershell
$port = [int](Get-Content "$env:LOCALAPPDATA\SSD_VR_MCP\bridge.json" -Raw | ConvertFrom-Json).port
$c = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $port); $st = $c.GetStream()
$b = [Text.Encoding]::UTF8.GetBytes('{"id":"1","op":"query_state","args":{}}' + "`n")
$st.Write($b, 0, $b.Length); $st.Flush()
$buf = [byte[]]::new(65536); $n = $st.Read($buf, 0, $buf.Length)
[Text.Encoding]::UTF8.GetString($buf, 0, $n); $c.Close()
```

> ⚠️ **PowerShell 5.1 编码坑（踩过）**：BOM-less 的 `.ps1` 被当成 ANSI（中文系统 = cp936）读取，
> 中文注释的字节会把**下一行代码吃掉**，表现为"变量莫名是 `$null` / 方法调用报 null"。
> 最小客户端脚本请保持**纯 ASCII**，或存成 **UTF-8 with BOM**。

### 4.7 参数怎么传（跨 shell 不被引号吃掉）

```bash
python tools/ssdvr_agent_client.py call set_mode mode=cinematic
python tools/ssdvr_agent_client.py call set_camera azimuth=45 elevation=12 dolly=1.2
python tools/ssdvr_agent_client.py call load_dicom path=D:\case\series
python tools/ssdvr_agent_client.py call set_camera '{"azimuth":45,"elevation":12}'   # JSON 也行
```

`key=value` 简写会自动转型（`45`→int、`1.2`→float、`true`→bool、`[1,2]`→list）；
**值里带空格时必须用完整 JSON**（PowerShell/cmd 会把 JSON 里的双引号吃掉，简写就是为了绕开这点）。

### 4.8 异步事件（长任务必须等）

`load_dicom` / `trigger_roi` 是**异步**的：桥先把命令投给 Qt 主线程，然后推事件：

```
load_start → progress{percent,message} × N → load_done        # 加载并渲染完
roi_done{...}                                                  # 语义分割完（1-5 分钟）
error{error}                                                   # 失败
```

- 走 MCP：`ssdvr_wait_event(etype="load_done")`
- 走脚本：`python tools/ssdvr_agent_client.py wait load_done 300`
- 自己写：循环读行，`{"type":...}` 是事件、`{"id":...}` 是响应（§4.6 的循环已经这么做了）

### 4.9 别的机器上的 agent

桥只绑 `127.0.0.1`，跨机用 SSH 隧道（把远端 7799 映到本地）：

```bash
ssh -N -L 7799:127.0.0.1:7799 user@渲染机
# 然后本地 agent 照常连 127.0.0.1:7799（发现文件不存在时扫描默认段即可）
```

或者干脆**在渲染机上跑 MCP server**（`mcp_ssd_vr/server.py` 在哪台机器都行，它只是客户端），
客户端用 SSH 把 stdio 转过去。

### 4.10 谁先谁后

1. GUI 必须先起来（双击 EXE，或让 agent 调 `ssdvr_launch`）
2. 任何 agent 都能随后接管；**同一时刻只让一个 agent 操作**（见 §6）
3. agent 挂了/GUI 重启都不影响对方：桥是独立进程内的独立线程，靠发现文件重新握手即可

---

## 5. `ssdvr_launch` 的启动目标

| `SSD_VR_LAUNCH` | 行为 |
|---|---|
| 未设 / `auto`（默认） | 找到冻结版 EXE 就启动 EXE，否则回退源码 |
| `source` | 强制源码（`python ssd_vr_viewer.py`），开发调试用 |
| `exe` | 强制 EXE，找不到直接报错（不会静默回退） |

EXE 查找顺序：`SSD_VR_EXE`（可给 exe 或目录）→ 仓库根下
`SSD_VR_Fusion_Viewer_Full_Win\`、`dist\SSD_VR_Fusion_Viewer\`、`SSD_VR_Fusion_Viewer\`、
`SSD_VR_Fusion_Viewer_Win\` → 仓库根 / 包目录。只看仓库与本包，**不看 cwd**（结果可复现）。

```python
ssdvr_launch()                      # → {"ok":true,"pid":3924,"port":7941,"target":"exe","exe":"...exe"}
ssdvr_launch(dicom_path="D:\\case") # 启动即加载
ssdvr_restart_gui()                 # 关闭旧的（含接管来的手工实例）再启动，返回 old_pid
ssdvr_status()                      # 未连接时也会告诉你"打算启动谁 / 去哪里找桥"
```

> ⚠️ `auto` 会优先用仓库里那个已下载的 EXE。如果它是旧构建（缺最新 GUI 功能），
> 调试时请用 `SSD_VR_LAUNCH=source`，或下载最新 CI 产物替换。

---

## 6. 安全与并发

- 只监听 `127.0.0.1`，**没有鉴权**：本机任何进程都能驱动 GUI。
  多用户/共享机器上如不希望被控制，用 `--no-mcp`。
- **一个 GUI 只应由一个 agent 操作**。多个 agent 同时 `load_dicom` 会互相打断
  （桥会返回 `load_busy` / 后一次加载取消前一次）。需要并行请各自
  `SSD_VR_MCP_PORT=79xx` 起独立 GUI 实例。
- 同时开多个 GUI 实例时，发现文件只有最后启动的那个（后写覆盖先写）；
  其他实例仍能被扫描找到，但**优先用发现文件里的那个**，不要指望"自动挑对"。
- agent 拿到的能力等同于坐在电脑前点鼠标，包括 `shutdown`（关掉窗口）。

---

## 7. 故障排查

| 现象 | 原因 / 处置 |
|---|---|
| `ssdvr_status` → `bridge_connected:false` | 看返回里的 `discovery` 与 `launch`：`discovery.present=false` 说明 GUI 没开或用了 `--no-mcp`；`gui_state:null` + `present=true` 说明发现文件过期 |
| 端口上有别的东西（如 MaccCore:7893） | 已被协议握手排除；若自定义端口撞车，换 `SSD_VR_MCP_PORT` |
| EXE 起不来 / 桥没起 | 看 `mcp_records/gui.log`（`ssdvr_launch` 会把子进程 stdout/stderr 写进去）；EXE 的桥启动行是 `[MCP bridge] listening on 127.0.0.1:<port>` |
| 桥端口和预期不一致 | 被占用会自动 +1；以发现文件/`gui_ready` 事件里的实际端口为准（`ssdvr_status().port`） |
| 想让 agent 接管手工双击的 EXE | 直接 `ssdvr_status` / 任意工具即可——发现文件 + 扫描会自动接管，无需 `ssdvr_launch` |

---

## 8. 实测记录（2026-09-16，RTX 3080 / Windows 11 / mar 环境 py3.11）

| 验证 | 结果 |
|---|---|
| `SSD_VR_Fusion_Viewer.exe --mcp --mcp-port 7801`（旧构建） | 9.2 s 起桥；`query_state`、`screenshot` 正常 |
| E2E `temp/mcp_exe_probe/e2e_agent_attach.py` | **PASS 29/29** |
| P1 冻结版 EXE 手工启动 → agent 无端口提示接管 | ok（旧构建无发现文件，靠协议握手扫描找到） |
| P2 源码 `python ssd_vr_viewer.py`（无 MCP 参数） | ok：桥默认开启、发现文件写出真实端口、`load_dicom` → 256×256×80、截图 770 色 |
| P3 源码 `--no-mcp` | ok：无发现文件，agent 扫描不到桥 |
| 工具级 `temp/mcp_exe_probe/test_launch_tool.py` | **PASS 30/30**：`target=exe` / `target=source` 各跑通 launch→status→snapshot→screenshot→重复 launch 复用→restart 换 pid |
| 单元 `temp/patient_probe/test_exe_bridge.py` | **PASS 63/63**（发现文件、协议握手、EXE 查找、命令行拼装、默认开关） |
| 既有 8 个回归套件 | 全部 PASS |

### 8.1 CI 新构建产物实测（2026-09-17 06:14 构建，commit `1d7db0f8`）

FULL 构建 `35105037096` 全绿（26 min，DLL 依赖检查/VC+OpenMP 门禁均通过），
产物直接解到 `SSD_VR_Fusion_Viewer_Full_Win\` 后：

| 验证 | 结果 |
|---|---|
| `temp/mcp_exe_probe/test_shipped_exe_port.py`（对**已下载的交付 EXE**） | **PASS 29/29** |
| 不带任何参数启动（双击等价） | 3.0 s 写出发现文件，端口默认 7799，`frozen:true`，`exe` 指向自身 |
| 发现文件 host | 仅 `127.0.0.1`（不监听外网） |
| 裸 TCP + 手写 JSON（不依赖本项目代码） | `{"id":"raw-1","ok":true,...}` 正常往返 |
| agent 侧自动发现 | `discover_port()` / `get_client()` 无需预设端口即接管 |
| 操作类 | `query_state` / `get_thresholds`（ssd `[320,1300]`、vr `[20,150]`）/ `list_presets` / `get_render_params` 全部可用 |
| 加载 + 渲染 | `load_dicom` → `load_done` → dims `256×256×80`；`set_mode` / `set_opacity` / `set_camera` 生效 |
| 截图 | stable 439 色 → cinematic 117570 色（与 GUI 同步、肉眼可核对） |
| `--no-mcp` | 无发现文件、扫描不到桥、纯 GUI |
| 一次性 Windows 构建 `35105037116`（onefile 644 MB） | 同样 17/17 通过（默认开桥 20.4 s，渲染 587 色） |
| macOS 构建 `35105037035` | 8m22s success |

---

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `mcp_ssd_vr/gui_bridge.py` | EXE 内嵌 TCP server；`_bind()` 同步绑定 → `_publish()` 写发现文件 + 广播 `gui_ready`（真实端口） |
| `mcp_ssd_vr/bridge_registry.py` | 发现文件读写、`probe_port`、`probe_bridge`（协议握手）、`discover_port` |
| `mcp_ssd_vr/config.py` | `gui_exe()` / `launch_preference()` / `resolve_launch_target()` |
| `mcp_ssd_vr/tools/lifecycle.py` | `ssdvr_launch` / `ssdvr_restart_gui`（EXE 或源码、接管外部实例） |
| `mcp_ssd_vr/tools/_util.py` | `get_client()` 自动发现端口 |
| `mcp_ssd_vr/tools/inspect.py` | `ssdvr_status` 暴露 discovery / launch 诊断信息 |
| `ssd_vr_viewer.py` `main()` | `--mcp` 默认开、`--no-mcp` 关、`--mcp-port` 认 `SSD_VR_MCP_PORT` |
| `.github/workflows/main-full.yml` `main.yml` | `--add-data=mcp_ssd_vr;mcp_ssd_vr` + `--hidden-import=mcp_ssd_vr.bridge_registry` |
| `tools/ssdvr_agent_client.py` | 现成命令行客户端（纯标准库）：`discover` / `ops` / `call` / `wait` / `shot` |
| `tools/ssdvr_agent_client.js` | 同上，Node 版（无依赖），给 JS agent 抄 |
| `tools/ssdvr_agent_client.ps1` | 同上，PowerShell 版（**纯 ASCII**，见 §4.6 的编码坑） |
