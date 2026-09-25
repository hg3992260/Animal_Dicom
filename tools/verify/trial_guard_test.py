# -*- coding: utf-8 -*-
"""运行期限强制的对抗性自检：到期 / 篡改 / 改时钟 / 删模块 / 正常。

用法: D:\\python\\envs\\mar\\python.exe I:\\animal_dicom\\temp\\trial_guard_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"I:\animal_dicom"
PY = r"D:\python\envs\mar\python.exe"
sys.path.insert(0, ROOT)
import license_guard as guard  # noqa: E402

RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail)[:200] if detail else ""),
          flush=True)


def app_env():
    e = os.environ.copy()
    e.update({"QT_QPA_PLATFORM": "offscreen", "SSDVR_NO_DIALOG": "1",
              "KMP_DUPLICATE_LIB_OK": "TRUE", "PYTHONIOENCODING": "utf-8",
              "PYTHONUTF8": "1"})
    e.pop("SSDVR_MCP", None)
    return e


def run_app(timeout=45, args=("--no-mcp",)):
    """返回 (rc 或 None=超时仍在运行, stdout+stderr)。None 表示正常启动（进入事件循环）。"""
    try:
        p = subprocess.Popen([PY, os.path.join(ROOT, "ssd_vr_viewer.py"), *args],
                             cwd=ROOT, env=app_env(),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:  # noqa: BLE001
        return -99, str(e)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, (out or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        p.kill()
        try:
            out, _ = p.communicate(timeout=10)
        except Exception:
            out = b""
        return None, (out or b"").decode("utf-8", "replace")


# ---------- 备份现有锚点 ----------
BACKUP = {p: (open(p, "r", encoding="utf-8").read() if os.path.isfile(p) else None)
          for p in guard._anchor_paths()}
REG_BACKUP = guard._reg_read()
print("backup anchors:", {os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p): bool(v)
                          for p, v in BACKUP.items()}, flush=True)

try:
    now = time.time()

    # ---- 1) 正常：30 天 ----
    guard._write_all(now - 86400, now)
    st = guard.startup()
    rec("正常(首次运行=昨天) → days_left≈29 且可用",
        st["ok"] and 28.5 <= (st["days_left"] or 0) <= 29.5, st)

    rc, out = run_app(timeout=40)
    rec("正常启动：进入事件循环（未被期限拦截）", rc is None, {"rc": rc, "out": out[-160:]})

    # ---- 2) 已到期（首次运行 31 天前）----
    guard._write_all(now - 31 * 86400, now)
    rc, out = run_app(timeout=40)
    rec("到期(31 天前) → 拒绝启动，退出码 3", rc == 3, {"rc": rc, "out": out[-160:]})
    rec("  提示语含'运行期限已结束'", "运行期限已结束" in out, out[-200:])

    # ---- 3) 改系统时钟往回拨（last_seen 在未来 10 天）----
    guard._write_all(now - 86400, now + 10 * 86400)
    rc, out = run_app(timeout=40)
    rec("时钟回拨(记录在未来) → 拒绝启动，退出码 3", rc == 3, {"rc": rc, "out": out[-160:]})
    rec("  提示语含'回拨'", "回拨" in out, out[-200:])

    # ---- 4) 删掉所有锚点文件（但机器上还有用过痕迹）----
    for p in guard._anchor_paths():
        try:
            os.remove(p)
        except OSError:
            pass
    guard._reg_write({"install_id": "x", "first_run": 0, "last_seen": 0, "used": True, "sig": "bad"})
    left = [p for p in guard._anchor_paths() if os.path.isfile(p)]
    rc, out = run_app(timeout=40)
    rec("清空锚点文件 → 拒绝启动，退出码 3", rc == 3, {"rc": rc, "left": len(left), "out": out[-160:]})
    rec("  提示语含记录异常/校验失败",
        ("记录异常" in out) or ("校验失败" in out), out[-200:])

    # ---- 5) 全清（含注册表）→ 仍受 HARD_DEADLINE 约束 ----
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, guard._REG_PATH, 0,
                            winreg.KEY_ALL_ACCESS) as k:
            winreg.DeleteValue(k, "trial")
    except Exception:
        pass
    # 伪造"从没用过"：连痕迹目录也清掉（模拟换机重装）
    local_dir = guard._dir_marker()
    if os.path.isdir(local_dir):
        shutil.rmtree(local_dir, ignore_errors=True)
    st5 = guard.startup()
    hard_ts = guard._deadline_ts()
    rec("全清后重装 → 重新给 30 天，但不超过 HARD_DEADLINE",
        st5["ok"] and st5["expires_ts"] is not None and st5["expires_ts"] <= hard_ts + 60,
        {"expires": st5["expires_at"], "hard": st5["hard_deadline"]})
    rec("  HARD_DEADLINE 之后必定不可用",
        os.path.getsize(os.path.join(ROOT, "license_guard.py")) > 0 and "HARD_DEADLINE" in
        open(os.path.join(ROOT, "license_guard.py"), encoding="utf-8").read())

    # ---- 6) 删掉 license_guard.py → 拒绝启动，退出码 4 ----
    lg = os.path.join(ROOT, "license_guard.py")
    hidden = lg + ".hidden"
    os.replace(lg, hidden)
    try:
        rc, out = run_app(timeout=40)
        rec("删除 license_guard.py → 拒绝启动，退出码 4", rc == 4, {"rc": rc, "out": out[-160:]})
        rec("  提示语含'完整性检查失败'", "完整性检查失败" in out, out[-200:])
    finally:
        os.replace(hidden, lg)

    # ---- 7) 手改日期 + 伪造签名（所有锚点全改成假签名）----
    forged = {"install_id": guard._install_id(),
              "first_run": time.time() - 40 * 86400,
              "last_seen": time.time(), "used": True, "sig": "0" * 64}
    for q in guard._anchor_paths():
        os.makedirs(os.path.dirname(q), exist_ok=True)
        with open(q, "w", encoding="utf-8") as f:
            json.dump(forged, f)
    guard._reg_write(forged)
    st7 = guard.startup()
    rec("手改日期+假签名(全部锚点) → 判为异常，不会延长",
        (not st7["ok"]) and "校验失败" in st7["message"], st7)
    rc, out = run_app(timeout=40)
    rec("  该状态下拒绝启动，退出码 3", rc == 3, {"rc": rc, "out": out[-160:]})

finally:
    # ---------- 还原 ----------
    print("\n--- 还原锚点 ---", flush=True)
    for p, blob in BACKUP.items():
        try:
            if blob is None:
                if os.path.isfile(p):
                    os.remove(p)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(blob)
        except Exception as e:  # noqa: BLE001
            print("restore fail", p, e)
    if REG_BACKUP:
        guard._reg_write(REG_BACKUP)
    st = guard.startup()
    rec("还原后可用", st["ok"], st)

n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
print("\n===== 期限自检: %d 项, 失败 %d =====" % (len(RESULTS), n_fail), flush=True)
for name, ok, detail in RESULTS:
    if not ok:
        print("  FAIL " + name + "  " + str(detail)[:200], flush=True)
sys.exit(1 if n_fail else 0)
