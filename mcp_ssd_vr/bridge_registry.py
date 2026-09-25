"""TCP 桥发现文件（跨进程 / 跨安装目录）。

GUI 进程在桥监听成功之后，把**实际**端口写到一个用户级文件里；
任何 MCP 客户端（DSH / opencode / Claude Desktop / 自己写的脚本）
都可以不依赖环境变量、不依赖启动顺序，直接找到正在运行的实例。

文件位置（优先级从高到低）:
  1. 环境变量 SSD_VR_BRIDGE_FILE 指定的路径
  2. Windows: %LOCALAPPDATA%\\SSD_VR_MCP\\bridge.json
  3. 其他平台: ~/.ssd_vr_mcp/bridge.json

文件内容（JSON）:
  {"port": 7799, "host": "127.0.0.1", "pid": 1234, "exe": "...",
   "frozen": true, "started_at": 1789123456.7, "version": 1}

本模块只用标准库：会被冻结进 EXE（gui_bridge 内嵌桥调用）也会被
MCP server 端调用。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import Any, Dict, Optional

REGISTRY_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7799
PORT_SPAN = 100


# ---------------------------------------------------------------------------
# 文件位置
# ---------------------------------------------------------------------------

def bridge_file_path() -> str:
    env = (os.environ.get("SSD_VR_BRIDGE_FILE") or "").strip()
    if env:
        return os.path.abspath(env)
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return os.path.join(local, "SSD_VR_MCP", "bridge.json")
    return os.path.join(os.path.expanduser("~"), ".ssd_vr_mcp", "bridge.json")


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def write_bridge_file(port: int, host: str = DEFAULT_HOST, pid: Optional[int] = None,
                      **extra: Any) -> str:
    """原子写入发现文件，返回路径（失败时返回 ""，绝不抛异常）。"""
    path = bridge_file_path()
    payload: Dict[str, Any] = {
        "version": REGISTRY_VERSION,
        "host": host,
        "port": int(port),
        "pid": int(pid if pid is not None else os.getpid()),
        "started_at": time.time(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "exe": os.path.abspath(sys.executable),
    }
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except OSError:
        return ""


def read_bridge_file() -> Optional[Dict[str, Any]]:
    """读取发现文件；不存在/损坏返回 None。"""
    try:
        with open(bridge_file_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["port"] = int(data.get("port"))
    except (TypeError, ValueError):
        return None
    if not (0 < data["port"] < 65536):
        return None
    if not data.get("host"):
        data["host"] = DEFAULT_HOST
    return data


def remove_bridge_file(port: Optional[int] = None) -> None:
    """删除发现文件。传入 port 时只在文件记录的端口一致时才删（避免删掉别人的）。"""
    path = bridge_file_path()
    try:
        if port is not None:
            cur = read_bridge_file()
            if cur is not None and int(cur.get("port", -1)) != int(port):
                return
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 探活 / 发现
# ---------------------------------------------------------------------------

def probe_port(port: int, host: str = DEFAULT_HOST, timeout: float = 0.35) -> bool:
    """TCP 连接测试：端口上是否有东西在监听。端口非法一律返回 False。

    注意：只说明"有服务"，不说明"是我们的桥"——本机 7799-7899 段
    可能被别的软件占用（实测 MaccCore 占 7893-7895），要判断是不是桥
    请用 probe_bridge()。
    """
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    if not (0 < p < 65536):
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, p)) == 0
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


PROBE_ID = "discover-probe"


def probe_bridge(port: int, host: str = DEFAULT_HOST, timeout: float = 0.8) -> bool:
    """握手验证：连上端口后发一次 query_state，看是否收到同 id 的桥响应。

    只做 TCP 连接是不够的——同网段/本机其他软件可能恰好占用该端口
    （实测 MaccCore 占 7893），必须用协议确认对方是 SSD+VR 桥。
    """
    if not probe_port(port, host=host, timeout=min(timeout, 0.35)):
        return False
    payload = json.dumps({"id": PROBE_ID, "op": "query_state", "args": {}}).encode("utf-8") + b"\n"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall(payload)
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = s.recv(65536)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass

    for line in buf.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == PROBE_ID:
            return True
    return False


def discover_port(base: int = DEFAULT_PORT, span: int = PORT_SPAN,
                  host: str = DEFAULT_HOST, use_registry: bool = True) -> Optional[int]:
    """找到正在运行的 GUI 桥端口（带协议握手，不会误认其他服务）。

    顺序: 发现文件 -> base..base+span 顺序扫描。
    localhost 上被拒绝的连接是立即返回的，所以整段扫描通常在毫秒级。
    """
    if use_registry:
        info = read_bridge_file()
        if info and probe_bridge(info["port"], host=info.get("host", host)):
            return int(info["port"])

    for port in range(int(base), min(int(base) + int(span), 65536)):
        if probe_bridge(port, host=host, timeout=0.6):
            return port
    return None


def registry_info() -> Dict[str, Any]:
    """给 ssdvr_status 用：发现文件内容 + 是否仍是一个活着的桥。"""
    info = read_bridge_file()
    if info is None:
        return {"path": bridge_file_path(), "present": False, "alive": False}
    alive = probe_bridge(info["port"], host=info.get("host", DEFAULT_HOST))
    return {"path": bridge_file_path(), "present": True, "alive": alive, **info}
