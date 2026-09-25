"""生命周期工具：ssdvr_launch / ssdvr_restart_gui / ssdvr_reload_tools。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from .. import config
from .. import bridge_registry as registry
from ..bridge_client import BridgeClient
from ..context import get_ctx
from ._util import get_client

TOOL_META = {"name": "ssdvr_launch", "version": "1.0"}


def _free_port(start: int) -> int:
    port = start
    while port < start + 100:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            port += 1
        finally:
            try:
                s.close()
            except Exception:
                pass
    return start


def _launch_gui(dicom_path: Optional[str] = None) -> Dict[str, Any]:
    ctx = get_ctx()
    port = _free_port(config.default_port())
    target, path = config.resolve_launch_target()

    if target == "exe" and not path:
        return {"ok": False,
                "error": "SSD_VR_LAUNCH=exe 但找不到冻结版 EXE（可用 SSD_VR_EXE 指定路径）"}
    if target == "source" and not os.path.isfile(path):
        return {"ok": False, "error": f"找不到 viewer 脚本: {path}"}

    config.ensure_dirs()

    if target == "exe":
        cmd = [path, "--mcp", "--mcp-port", str(port)]
        # cwd 用 EXE 所在目录：onedir 版要能解析同级的 _internal\
        workdir = os.path.dirname(os.path.abspath(path))
    else:
        cmd = [config.gui_python(), path, "--mcp", "--mcp-port", str(port)]
        workdir = config.repo_root()
    if dicom_path:
        cmd += ["--input", dicom_path]

    logf = open(config.gui_log_path(), "ab", buffering=0)  # noqa: SIM115

    # Windows 兼容：torch 的 libiomp5md.dll 与 vtk/SimpleITK 的 OpenMP 重复初始化会导致
    # TotalSegmentator 在 Qt 线程里 import torch 时崩溃（QThread destroyed while running）。
    # 同时把 stdout 编码强制为 utf-8，避免 print('1024³') 触发 GBK 编码崩溃。
    env = os.environ.copy()
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    # 让桥把实际端口写进发现文件（EXE 内嵌桥也会读这个变量）
    env.setdefault("SSD_VR_MCP_PORT", str(port))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            cwd=workdir,
            env=env,
        )
    except Exception as e:
        return {"ok": False, "error": f"启动 GUI 进程失败: {e}"}

    ctx.gui_process = proc
    ctx.gui_pid = proc.pid
    ctx.gui_python = config.gui_python() if target == "source" else path
    ctx.port = port

    client = BridgeClient(port=port)
    ctx.client = client

    deadline = time.time() + 120.0  # 冻结版冷启动要解包/加载 torch+vtk，给足时间
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            return {"ok": False, "error": f"GUI 进程已退出（exit={proc.returncode}），见 {config.gui_log_path()}",
                    "pid": proc.pid, "port": port, "target": target}
        if client.connected or client.connect(timeout=2.0):
            # 等 gui_ready 事件（最多再等几秒）
            evt = client.wait_event("gui_ready", timeout=8.0)
            if evt is not None or client.connected:
                # 桥被占用时会向后探测端口，gui_ready 里的才是实际端口
                real = (evt or {}).get("data", {}).get("port") if evt else None
                if isinstance(real, int) and real > 0 and real != port:
                    client.close()
                    port = real
                    ctx.port = real
                    client = BridgeClient(port=port)
                    ctx.client = client
                    client.connect(timeout=5.0)
                ready = True
                break
        time.sleep(0.5)

    if not ready:
        return {"ok": False, "error": "GUI 桥在 120s 内未就绪（见 mcp_records/gui.log）",
                "pid": proc.pid, "port": port, "target": target}

    return {"ok": True, "pid": proc.pid, "port": port, "target": target,
            "exe": path if target == "exe" else None}


def register(mcp) -> None:
    @mcp.tool()
    def ssdvr_launch(dicom_path: Optional[str] = None) -> dict:
        """启动 SSD+VR Viewer GUI 并等待 TCP 桥就绪。

        auto 模式下优先启动冻结版 EXE（SSD_VR_LAUNCH=source 可强制源码）。
        dicom_path 可指定启动即加载的 DICOM；省略=干净启动（无默认数据）。
        已运行（含手工双击启动的 EXE）时直接接管，不重复启动。
        返回 {ok, pid, port, target}。
        """
        ctx = get_ctx()
        client = get_client(auto_connect=True)
        if client is not None and client.connected:
            return {"ok": True, "already_connected": True, "port": ctx.port, "pid": ctx.gui_pid,
                    "attached": True}
        proc = getattr(ctx, "gui_process", None)
        if proc is not None and proc.poll() is None:
            return {"ok": True, "already_running": True, "pid": proc.pid, "port": ctx.port}
        result = _launch_gui(dicom_path)
        if result.get("ok") and ctx.recorder is not None:
            ctx.recorder.record_event("gui_ready", {"pid": result.get("pid"), "port": result.get("port"),
                                                    "target": result.get("target")})
        return result

    @mcp.tool()
    def ssdvr_restart_gui() -> dict:
        """重启 GUI 进程（viewer/gui_bridge 改动后调用生效）。

        先安全关闭旧进程再启动新进程。若当前是"接管"手工启动的实例，
        同样会先通过桥 shutdown 再重启。返回 {ok, pid, port, old_pid, target, reconnected}。
        """
        ctx = get_ctx()
        old_pid = ctx.gui_pid
        proc = getattr(ctx, "gui_process", None)
        client = get_client(auto_connect=False)

        # 情况一：我们自己启动的进程
        if proc is not None and proc.poll() is None:
            if client is not None and client.connected:
                try:
                    client.call("shutdown", {}, timeout=5.0)
                except Exception:
                    pass
            try:
                proc.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        # 情况二：接管的外部实例（手工双击的 EXE）——只能靠桥让它退出
        elif client is not None and client.connected:
            info = registry.registry_info() if registry is not None else {}
            old_pid = old_pid or info.get("pid")
            try:
                client.call("shutdown", {}, timeout=5.0)
            except Exception:
                pass
            deadline = time.time() + 10.0
            while time.time() < deadline and registry is not None and registry.probe_port(client.port):
                time.sleep(0.4)

        # 断开旧 client
        old_client = ctx.client
        if old_client is not None:
            old_client.close()
        ctx.client = None
        ctx.gui_process = None
        ctx.gui_pid = None
        time.sleep(1.0)

        result = _launch_gui(None)
        result["old_pid"] = old_pid
        result["reconnected"] = bool(result.get("ok"))
        return result

    @mcp.tool()
    def ssdvr_reload_tools() -> dict:
        """热重载 MCP 工具层（mcp_ssd_vr/tools/*.py 改动后调用）。返回重载清单。"""
        from . import reload_tools
        from ..context import get_ctx as _g
        ctx = _g()
        registered = reload_tools(ctx.mcp)
        return {"ok": True, "reloaded": registered}
