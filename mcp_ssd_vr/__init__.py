"""SSD+VR Viewer MCP 封装包。

把「GUI 进程 + TCP 桥 + 事件推送」封装成一组确定性 MCP 工具，
供 opencode agent 完成 DICOM 加载 / 渲染 / 截图 / 语义分割的完整闭环。
"""

__version__ = "1.0.0"
