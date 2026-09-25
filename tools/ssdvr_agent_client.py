#!/usr/bin/env python
"""任何 agent / 任何语言都能用的最小 SSD+VR 桥客户端（纯标准库，不 import 本项目）。

为什么需要它：MCP（stdio JSON-RPC）适合"支持 MCP 的客户端"，
但如果对方 agent 只能执行 shell 命令、或你想在别的语言/别的机器上驱动 GUI，
直接用这个脚本即可——它只做三件事：找到端口、发一行 JSON、读一行 JSON。

用法:
    python tools/ssdvr_agent_client.py discover                 # 找桥：{port,pid,exe,frozen}
    python tools/ssdvr_agent_client.py ops                      # 列出所有桥 op
    python tools/ssdvr_agent_client.py call query_state
    python tools/ssdvr_agent_client.py call set_mode '{"mode":"cinematic"}'
    python tools/ssdvr_agent_client.py call load_dicom '{"path":"D:\\case"}'
    python tools/ssdvr_agent_client.py wait load_done 300
    python tools/ssdvr_agent_client.py shot --out D:\\out
    python tools/ssdvr_agent_client.py call shutdown

端口发现顺序（与 MCP 客户端完全一致）:
    1. --port 显式指定
    2. 环境变量 SSD_VR_MCP_PORT
    3. 发现文件 %LOCALAPPDATA%\\SSD_VR_MCP\\bridge.json（可用 SSD_VR_BRIDGE_FILE 覆盖）
    4. 协议握手扫描 7799-7898

退出码: 0 成功 / 1 失败（响应 ok:false 或连不上）——便于 shell 里 && 串联。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time
from typing import Any, Dict, Optional

HOST = "127.0.0.1"
DEFAULT_PORT = 7799
SCAN_SPAN = 100
PROBE_ID = "ssdvr-agent-probe"

# 25 个桥 op（GUI 进程内 gui_bridge.py 的 _execute 分派表）
BRIDGE_OPS = [
    "load_dicom", "query_state", "screenshot", "shutdown",
    "set_mode", "set_opacity", "set_camera", "set_window_level",
    "set_ssd_threshold", "set_vr_threshold", "get_thresholds",
    "set_cr_params", "set_preprocess", "set_crop", "toggle_background",
    "get_render_params", "apply_preset", "list_presets",
    "trigger_roi", "roi_cancel", "roi_clear", "list_roi_blocks",
    "render_roi_label", "set_custom_roi", "set_roi_weight_path",
]

EVENT_TYPES = [
    "load_start", "load_done", "progress", "error", "state", "user_action",
    "roi_done", "roi_error", "gui_ready", "gui_exit",
]


# ---------------------------------------------------------------------------
# 端口发现
# ---------------------------------------------------------------------------

def bridge_file_path() -> str:
    env = (os.environ.get("SSD_VR_BRIDGE_FILE") or "").strip()
    if env:
        return env
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return os.path.join(local, "SSD_VR_MCP", "bridge.json")
    return os.path.join(os.path.expanduser("~"), ".ssd_vr_mcp", "bridge.json")


def read_bridge_file() -> Optional[Dict[str, Any]]:
    try:
        with open(bridge_file_path(), "r", encoding="utf-8") as f:
            info = json.load(f)
        info["port"] = int(info["port"])
        if not (0 < info["port"] < 65536):
            return None
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _tcp_open(port: int, timeout: float = 0.25) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((HOST, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def handshake(port: int, timeout: float = 0.8) -> bool:
    """发一次 query_state 并确认收到同 id 的桥响应。

    只做 TCP 连接是不够的：本机 7799-7899 段可能被别的软件占用
    （实测 MaccCore 占 7893-7895），必须用协议确认。
    """
    if not _tcp_open(port, min(timeout, 0.35)):
        return False
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, int(port)))
        s.sendall(json.dumps({"id": PROBE_ID, "op": "query_state", "args": {}}).encode() + b"\n")
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline and b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError:
        return False
    finally:
        s.close()
    for line in buf.split(b"\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == PROBE_ID:
            return True
    return False


def discover_port(explicit: Optional[int] = None) -> Optional[int]:
    if explicit:
        return int(explicit) if handshake(int(explicit)) else None
    env = (os.environ.get("SSD_VR_MCP_PORT") or "").strip()
    base = int(env) if env.isdigit() else DEFAULT_PORT
    info = read_bridge_file()
    if info and handshake(info["port"]):
        return int(info["port"])
    for port in range(base, base + SCAN_SPAN):
        if handshake(port):
            return port
    return None


# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------

class Bridge:
    """TCP line-delimited JSON: 请求 {"id","op","args"} / 响应 {"id","ok","data"} / 事件 {"type","data","ts"}"""

    def __init__(self, port: int, timeout: float = 30.0) -> None:
        self.port = port
        self.timeout = timeout
        self._sock = socket.create_connection((HOST, port), timeout=10.0)
        self._sock.settimeout(timeout)
        self._buf = b""
        self._seq = 0

    def _read_line(self, deadline: float) -> Optional[bytes]:
        while b"\n" not in self._buf:
            remain = deadline - time.time()
            if remain <= 0:
                return None
            self._sock.settimeout(max(0.1, remain))
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return None
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def call(self, op: str, args: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None) -> Dict[str, Any]:
        self._seq += 1
        req_id = f"agent-{self._seq}"
        payload = json.dumps({"id": req_id, "op": op, "args": args or {}},
                             ensure_ascii=False).encode("utf-8") + b"\n"
        self._sock.sendall(payload)
        deadline = time.time() + (timeout or self.timeout)
        while True:
            line = self._read_line(deadline)
            if line is None:
                return {"ok": False, "data": {"error": f"timeout waiting for op '{op}'"}}
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "id" in msg:                      # 响应
                if msg.get("id") != req_id:
                    continue
                return {"ok": bool(msg.get("ok")), "data": msg.get("data", {})}
            # 事件（load_start/load_done/progress/...）：打印到 stderr 不干扰 stdout 的 JSON
            print(f"[event] {msg.get('type')} {json.dumps(msg.get('data'), ensure_ascii=False)[:200]}",
                  file=sys.stderr)

    def wait_event(self, etype: str, timeout: float = 300.0,
                   contains: str = "") -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout
        while True:
            line = self._read_line(deadline)
            if line is None:
                return None
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "id" in msg:
                continue
            if msg.get("type") != etype:
                continue
            if contains and contains not in json.dumps(msg.get("data"), ensure_ascii=False):
                continue
            return msg

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def connect(explicit_port: Optional[int] = None) -> Optional[Bridge]:
    port = discover_port(explicit_port)
    if port is None:
        return None
    return Bridge(port)


def parse_args(spec: str) -> Dict[str, Any]:
    """解析命令行参数，两种写法都支持：

        '{"mode":"cinematic","azimuth":45}'   完整 JSON（值含空格时必须用这种）
        mode=cinematic azimuth=45             简写（跨 shell 不会被引号吃掉）

    简写值会自动转型: 45 -> int, 0.5 -> float, true/false -> bool, [1,2] -> list。
    """
    s = (spec or "").strip()
    if not s:
        return {}
    if s.startswith("{"):
        return json.loads(s)
    out: Dict[str, Any] = {}
    for tok in s.split():
        key, sep, val = tok.partition("=")
        if not sep:
            raise ValueError(f"参数片段 '{tok}' 不是 key=value 也不是 JSON")
        out[key] = _coerce(val)
    return out


def _coerce(val: str) -> Any:
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except ValueError:
            pass
    return val


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="SSD+VR Viewer 桥最小客户端（纯标准库）",
        epilog="参数写法: call set_mode mode=cinematic  或  call set_mode '{\"mode\":\"cinematic\"}'")
    ap.add_argument("--port", type=int, default=None, help="显式端口（省略=自动发现）")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次 op 超时秒数")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("discover", help="打印发现到的桥信息")
    sub.add_parser("ops", help="列出所有桥 op 与事件类型")
    p_call = sub.add_parser("call", help="调用一个 op")
    p_call.add_argument("op")
    p_call.add_argument("args", nargs="?", default="", help='JSON 或 key=value 简写（可多个）')
    p_call.add_argument("more", nargs="*", help="额外的 key=value（当 args 里含空格时用）")
    p_wait = sub.add_parser("wait", help="等待事件")
    p_wait.add_argument("etype")
    p_wait.add_argument("seconds", nargs="?", type=float, default=300.0)
    p_wait.add_argument("--contains", default="")
    p_shot = sub.add_parser("shot", help="截图并保存")
    p_shot.add_argument("--out", default=".")

    args = ap.parse_args()

    if args.cmd == "ops":
        print(json.dumps({"ops": BRIDGE_OPS, "events": EVENT_TYPES}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "discover":
        info = read_bridge_file()
        port = discover_port(args.port)
        out = {"found": port is not None, "port": port,
               "registry_file": bridge_file_path(),
               "registry": info if info else None}
        if port is not None:
            b = Bridge(port, timeout=10.0)
            try:
                resp = b.call("query_state", {}, timeout=10.0)
                out["query_state"] = resp.get("data")
            finally:
                b.close()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if port is not None else 1

    if not args.cmd:
        ap.print_help()
        return 2

    bridge = connect(args.port)
    if bridge is None:
        print(json.dumps({"ok": False, "error":
                          f"没有找到桥：确认 GUI 已启动（EXE 双击即可，或 ssdvr_launch）；"
                          f"发现文件 {bridge_file_path()}；扫描 {DEFAULT_PORT}-{DEFAULT_PORT+SCAN_SPAN-1}"},
                         ensure_ascii=False))
        return 1

    try:
        if args.cmd == "call":
            raw_spec = " ".join([args.args] + list(getattr(args, "more", []) or [])).strip()
            try:
                payload = parse_args(raw_spec)
            except ValueError as e:
                print(json.dumps({"ok": False, "error": f"参数解析失败: {e}"}, ensure_ascii=False))
                return 2
            resp = bridge.call(args.op, payload, timeout=args.timeout)
            print(json.dumps({"ok": resp["ok"], "port": bridge.port, "op": args.op, "data": resp["data"]},
                             ensure_ascii=False, indent=2, default=str))
            return 0 if resp["ok"] else 1

        if args.cmd == "wait":
            evt = bridge.wait_event(args.etype, timeout=args.seconds, contains=args.contains)
            if evt is None:
                print(json.dumps({"ok": False, "error": f"等待 {args.etype} 超时 ({args.seconds}s)"},
                                 ensure_ascii=False))
                return 1
            print(json.dumps({"ok": True, "event": evt.get("type"), "data": evt.get("data")},
                             ensure_ascii=False, indent=2, default=str))
            return 0

        if args.cmd == "shot":
            out_dir = os.path.abspath(args.out)
            os.makedirs(out_dir, exist_ok=True)
            resp = bridge.call("screenshot", {"out_dir": out_dir}, timeout=120.0)
            data = resp.get("data") or {}
            path = data.get("path") or ""
            if data.get("png_base64") and not path:
                path = os.path.join(out_dir, f"shot_{int(time.time()*1000)}.png")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(data["png_base64"]))
            print(json.dumps({"ok": resp["ok"], "path": path, "bytes": data.get("bytes"),
                              "stats": data.get("stats")}, ensure_ascii=False, indent=2))
            return 0 if resp["ok"] else 1
    finally:
        bridge.close()

    return 2


if __name__ == "__main__":
    sys.exit(main())
