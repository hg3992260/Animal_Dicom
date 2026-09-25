"""运行期限强制校验（30 天）。

设计目标（**纯离线、无网络、无 GUI 开关**）：
  1. 到期时间 = min(首次运行 + TRIAL_DAYS 天, HARD_DEADLINE)。
     HARD_DEADLINE 是代码常量，即使把本机所有记录清空、重装，程序也不会活过它。
  2. 首次运行时间写入 **4 份文件锚点 + 注册表**（HKCU），任一份存活即恢复；
     每份都带 HMAC 校验，手改日期会被识别为篡改。
  3. 「已使用」标记与锚点同处存放：若所有锚点都不见了、但机器上仍有本程序
     用过的痕迹（LOCALAPPDATA 目录 / 注册表键），判定为**刻意清除** → 立即到期。
  4. 时钟回拨检测：记录 last_seen，若系统时间比 last_seen 早了超过
     CLOCK_TOLERANCE_DAYS 天 → 判定为改时钟延长试用 → 立即到期。
  5. 程序侧不允许「缺少本模块就继续跑」：删掉 license_guard.py 会直接拒绝启动。

**必须知情的局限**：本地校验无法做到数学意义上的"不可解除"——
  改源码常量、反汇编打补丁（PyInstaller EXE）、换一台干净机器/虚拟机，
  都能绕过。要做到真正不可解除，只能服务端签发许可证 + 联网校验。
  本模块保证的是：普通用户**不能**通过改配置、删文件、改系统时钟绕过。

作者需要延长/重建期限时：改下面 TRIAL_DAYS / HARD_DEADLINE 后重新构建分发。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 期限参数（唯一的两个"旋钮"，都在代码里 —— 没有配置文件/环境变量旁路）
# ---------------------------------------------------------------------------
TRIAL_DAYS = 30
# 绝对上限：代码常量。出厂即固定；清空本机记录重新安装也活不过这一天。
HARD_DEADLINE = "2026-10-26T23:59:59"
CLOCK_TOLERANCE_DAYS = 2
WARN_DAYS = (7, 3, 1)

_APP = "SSD_VR_Fusion_Viewer"
_SIG_KEY = b"ssdvr-trial-v1-\x9a\x3f\x11\xcd\x7e\x42"
_REG_PATH = r"Software\%s" % _APP

# 进程内快照（供 GUI/桥读取，避免每次查询都做磁盘 IO）
_SNAPSHOT: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _sig(install_id: str, first_run: float, last_seen: float) -> str:
    msg = ("%s|%.0f|%.0f" % (install_id, first_run, last_seen)).encode("utf-8")
    return hmac.new(_SIG_KEY, msg, hashlib.sha256).hexdigest()


def _install_id() -> str:
    """安装标识：优先本机 MAC 派生（同一台机器稳定），退化为主目录路径哈希。"""
    seed = ""
    try:
        import uuid
        seed = str(uuid.getnode())
    except Exception:
        seed = ""
    if not seed:
        seed = os.path.expanduser("~")
    return hashlib.sha256((seed + _APP).encode("utf-8")).hexdigest()[:24]


def _anchor_paths() -> List[str]:
    """4 份文件锚点（跨目录，避免只删一个就重置）。"""
    out = []
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, _APP, "trial.json"))
    # 程序目录（跟随程序一起被复制/删除）
    out.append(os.path.join(_app_dir(), ".trial_anchor.json"))
    # 程序目录下的 mcp_records（用户一般不会连这里一起清）
    out.append(os.path.join(_app_dir(), "mcp_records", ".trial_anchor.json"))
    # 去重
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _dir_marker() -> str:
    """LOCALAPPDATA 下的目录存在 = 本程序在这台机器上跑过。"""
    base = os.environ.get("LOCALAPPDATA")
    return os.path.join(base, _APP) if base else ""


# ---------------------------------------------------------------------------
# 锚点读写（文件 + 注册表）
# ---------------------------------------------------------------------------

def _reg_read() -> Optional[Dict[str, Any]]:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            raw, _ = winreg.QueryValueEx(k, "trial")
        return json.loads(raw)
    except Exception:
        return None


def _reg_write(payload: Dict[str, Any]) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg  # noqa: PLC0415
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            winreg.SetValueEx(k, "trial", 0, winreg.REG_SZ,
                              json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _decode(payload: Any) -> Optional[Dict[str, Any]]:
    """校验 HMAC；签名不符 → 返回 None（视为不可信）。"""
    if not isinstance(payload, dict):
        return None
    try:
        iid = str(payload["install_id"])
        first = float(payload["first_run"])
        last = float(payload["last_seen"])
        if not payload.get("used"):
            return None
        want = _sig(iid, first, last)
        return {"install_id": iid, "first_run": first, "last_seen": last,
                "valid": hmac.compare_digest(want, str(payload.get("sig", "")))}
    except Exception:
        return None


def _read_files() -> List[Dict[str, Any]]:
    out = []
    for p in _anchor_paths():
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = _decode(json.load(f))
            if rec:
                out.append(rec)
        except Exception:
            continue
    return out


def _write_all(first_run: float, last_seen: float) -> None:
    payload = {
        "install_id": _install_id(),
        "first_run": first_run,
        "last_seen": last_seen,
        "used": True,
        "app": _APP,
        "v": 1,
    }
    payload["sig"] = _sig(payload["install_id"], first_run, last_seen)
    blob = json.dumps(payload, ensure_ascii=False)
    for p in _anchor_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, p)
        except Exception:
            continue
    _reg_write(payload)


def _deadline_ts() -> float:
    """绝对上限时间戳（解析失败则退化为很远的未来，不影响正常使用）。"""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(HARD_DEADLINE, fmt))
        except Exception:
            continue
    return _now() + TRIAL_DAYS * 86400 + 86400


def _fmt(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return "-"


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def startup() -> Dict[str, Any]:
    """启动时调用一次：读/建锚点、检测篡改与时钟回拨，返回状态。

    返回: {ok, status: ok|warning|expired|tampered|integrity,
           first_run, last_seen, expires_at, days_left, hard_deadline, message}
    """
    now = _now()
    hard = _deadline_ts()
    files = _read_files()
    reg = _decode(_reg_read())
    if reg:
        files.append(reg)

    valid = [r for r in files if r["valid"]]
    invalid = [r for r in files if not r["valid"]]

    # --- 篡改判定之一：存在签名不符的记录（被手改过日期）---
    # 锚点写入是原子的（tmp + os.replace），正常使用不会出现坏签名；
    # 出现即说明有人编辑过记录，直接判定异常（不给"改了也没用"这种空间）。
    if invalid:
        st = _make_status("tampered", None, None, hard, now)
        st["message"] = "运行期限记录校验失败（记录被修改）"
        _SNAPSHOT.clear()
        _SNAPSHOT.update(st)
        return st

    # --- 篡改判定之二：一条可信记录都没有，但机器上已有"用过"的痕迹 ---
    if not valid:
        local_dir = _dir_marker()
        ran_before = bool(local_dir and os.path.isdir(local_dir)) or bool(files)
        if ran_before:
            st = _make_status("tampered", None, None, hard, now)
            _SNAPSHOT.clear()
            _SNAPSHOT.update(st)
            return st
        # 真正的首次运行
        first = now
        _write_all(first, now)
        st = _make_status("ok", first, now, hard, now)
        _SNAPSHOT.clear()
        _SNAPSHOT.update(st)
        return st

    first = min(r["first_run"] for r in valid)
    last = max(r["last_seen"] for r in valid)

    # --- 时钟回拨：系统时间早于最后记录超过容差 → 视为改时钟 ---
    if now < last - CLOCK_TOLERANCE_DAYS * 86400:
        st = _make_status("tampered", first, last, hard, now)
        st["message"] += "（检测到系统时间被回拨）"
        _SNAPSHOT.clear()
        _SNAPSHOT.update(st)
        return st

    # 正常推进 last_seen（只前进不后退），并补齐缺失锚点
    new_last = max(last, now)
    _write_all(first, new_last)
    expires = min(first + TRIAL_DAYS * 86400, hard)
    st = _make_status("ok" if now < expires else "expired", first, new_last, hard, now)
    _SNAPSHOT.clear()
    _SNAPSHOT.update(st)
    return st


def note_seen() -> Dict[str, Any]:
    """运行期周期性调用：刷新 last_seen 并重新判定（后台守护用）。"""
    return startup()


def snapshot() -> Dict[str, Any]:
    """进程内快照（无磁盘 IO）；未初始化时返回空壳。"""
    if _SNAPSHOT:
        return dict(_SNAPSHOT)
    return {"ok": True, "status": "unknown", "days_left": None,
            "trial_days": TRIAL_DAYS, "hard_deadline": HARD_DEADLINE}


def _make_status(status: str, first: Optional[float], last: Optional[float],
                 hard: float, now: float) -> Dict[str, Any]:
    if first is None:
        expires = None
        days_left = None
    else:
        expires = min(first + TRIAL_DAYS * 86400, hard)
        days_left = max(0.0, (expires - now) / 86400.0)
    ok = status in ("ok", "warning")
    msg = {
        "ok": "运行期限正常",
        "warning": "运行期限即将结束",
        "expired": "运行期限已结束",
        "tampered": "运行期限记录异常",
    }.get(status, status)
    if status == "ok" and days_left is not None and days_left <= max(WARN_DAYS):
        status = "warning"
        msg = "运行期限即将结束"
    if expires and not ok:
        msg += "（到期时间 %s）" % _fmt(expires)
    return {
        "ok": ok and status != "expired",
        "status": status,
        "message": msg,
        "first_run": _fmt(first),
        "last_seen": _fmt(last),
        "expires_at": _fmt(expires),
        "expires_ts": expires,
        "days_left": None if days_left is None else round(days_left, 2),
        "trial_days": TRIAL_DAYS,
        "hard_deadline": HARD_DEADLINE,
    }


# ---------------------------------------------------------------------------
# 命令行自查（作者用）：python license_guard.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    st = startup()
    for k in ("ok", "status", "message", "first_run", "last_seen",
              "expires_at", "days_left", "trial_days", "hard_deadline"):
        print("%-14s %s" % (k, st.get(k)))
    print("%-14s %s" % ("anchors", "\n               ".join(_anchor_paths())))
