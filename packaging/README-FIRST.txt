Animal Dicom · 爱宠影像工作站 v1.0.0（Windows 64 位 · 便携免安装）
================================================================

一、运行
  1) 双击 Animal_Dicom.exe 即可（无需安装 Python）。
  2) 本目录必须保持完整：Animal_Dicom.exe 依赖同级的 _internal 文件夹，
     不要只拷贝 exe 单个文件。
  3) 建议放在可写目录（如 D:\Animal_Dicom）。若放在 C:\Program Files 下，
     程序写入 mcp_records（档案/ROI/事件记录）可能被系统拒绝。

二、五个页签
  ① 爱宠列表   扫描病例目录（含 PACS 取片子页）
  ② MPR 阅片   三视图阅片 + 测量（长度/角度/矩形/椭圆/不规则）+ 窗宽窗位 + 3D SAM 分割
  ③ 渲染       12 种 SSD+VR 融合渲染模式 + 预设 + CR 参数
  ④ 档案中心   病例档案（24 字段模板）+ 时间戳版本 + AI 引导补全
  ⑤ 设置       DeepSeek API Key / Base URL / 模型 / 温度

三、两种分发包
  ── 基础版 ──────────────────────────────────────────────
  文件：Animal_Dicom_v1.0.0_win64_portable.zip（约 424 MB）
     · 渲染 12 模式 / MPR / 测量 / PACS / 档案 / AI / 期限 —— 完整
     · 已附带 ErCore.dll（CR 路径追踪需要 NVIDIA GPU + CUDA）
     · **不含** GPU 加速库（cupy/CUDA）→ Frangi 等走 CPU，明显更慢
     · **不含** 3D SAM 推理依赖（torch ≈ 4.5 GB）→ 该页分割按钮不可用
  解压即用（只有一个压缩包）。

  ── 完整版（含 GPU 加速 + 3D SAM）──────────────────────
  文件：Animal_Dicom_v1.0.0_win64_full_part1of4.zip（582 MB）
        Animal_Dicom_v1.0.0_win64_full_part2of4.zip（101 MB）
        Animal_Dicom_v1.0.0_win64_full_part3of4.zip（1023 MB）
        Animal_Dicom_v1.0.0_win64_full_part4of4.zip（1271 MB）
     · 含 GPU 加速（cupy + CUDA 运行时，位于 _internal/torch/lib）与
       3D SAM（torch / medim / torchio / monai）
     · **解压方法：四个包全部解压到同一个目录**（任选一个空文件夹，例如
       D:\Animal_Dicom_Full），会合并成：Animal_Dicom.exe + _internal\ + frame\。
       四个包的内部路径互不重叠，逐个解压到同一目录即可，无需改名或覆盖顺序。
     · 3D SAM 首次使用会从 HuggingFace 下载权重 sam_med3d_turbo.pth（约 384 MB）
       到 frame\SAM-Med3D-main\ckpt\；现场无网络时请手动放入该目录。
     · 需要 NVIDIA 显卡 + 驱动（CUDA 12）；建议可用显存 ≥ 6 GB
       （显存紧张时 Frangi 等 GPU 预处理可能失败/退化，这是已知限制）。

四、命令行 / 让 AI Agent 接管
  Animal_Dicom.exe --mcp                 启动时开启 MCP 控制桥（默认端口 7799）
  Animal_Dicom.exe --input "D:\病例\泰迪"  启动即加载指定目录/文件
  Animal_Dicom.exe --help                查看全部参数
  说明：桥只绑定 127.0.0.1，端口写入 %LOCALAPPDATA%\SSD_VR_MCP\bridge.json 供自动发现。
  自检：MCP 工具 ssdvr_sam_probe（GPU/3D SAM 依赖）、ssdvr_state_snapshot（整体状态）。

五、运行期限
  本程序为期限版本：自首次运行起 30 天，且不超过硬上限 2026-10-26。
  到期或检测到记录被篡改/系统时钟回拨时会拒绝启动。

六、常见问题
  · 启动失败：查看同目录 startup_error.log（程序自动生成）。
  · 提示"缺少运行期限校验模块"：打包漏了 license_guard（请用 animal_dicom.spec 构建）。
  · CR 电影级 / Exposure Render 模式不可用：需要 NVIDIA GPU + CUDA + ErCore.dll。
  · 皮肤变成深色：说明 retro.qss / light.qss 丢失，请重新解压完整包。
  · 3D SAM 报 "No module named 'torch'"：这是基础版；请下载完整版的四个包。
  · 3D SAM 报权重下载失败：手动下载 sam_med3d_turbo.pth 放到 frame\SAM-Med3D-main\ckpt\。

版本 v1.0.0 · 2026-09-26
