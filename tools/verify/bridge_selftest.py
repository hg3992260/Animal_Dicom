# -*- coding: utf-8 -*-
"""MCP bridge 自检：启动带 --mcp 的 GUI → 逐一验证 op → 关闭 GUI。

用法: D:\\python\\envs\\mar\\python.exe I:\\animal_dicom\\temp\\bridge_selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"I:\animal_dicom"
PY = r"D:\python\envs\mar\python.exe"
PORT = 7801
CFG = os.path.join(ROOT, "deepseek_config.json")

sys.path.insert(0, ROOT)
from mcp_ssd_vr.bridge_client import BridgeClient  # noqa: E402

os.makedirs(os.path.join(ROOT, "temp"), exist_ok=True)
logf = open(os.path.join(ROOT, "temp", "selftest_gui.log"), "wb")
env = os.environ.copy()
env.update({"KMP_DUPLICATE_LIB_OK": "TRUE", "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1", "SSD_VR_MCP_PORT": str(PORT)})

proc = subprocess.Popen([PY, os.path.join(ROOT, "ssd_vr_viewer.py"),
                         "--mcp", "--mcp-port", str(PORT)],
                        cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, env=env)

RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    d = ""
    if detail:
        d = "  | " + json.dumps(detail, ensure_ascii=True)[:240]
    print(("PASS " if ok else "FAIL ") + name + d, flush=True)


client = BridgeClient(port=PORT)
ready = False
deadline = time.time() + 120
while time.time() < deadline:
    if proc.poll() is not None:
        print("GUI exited early rc=%s" % proc.returncode)
        break
    if client.connected or client.connect(timeout=2.0):
        evt = client.wait_event("gui_ready", timeout=10.0)
        if evt is not None or client.connected:
            ready = True
            break
    time.sleep(0.5)
rec("bridge_connect + gui_ready", ready)
if not ready:
    proc.terminate()
    sys.exit(1)


def call(op, args=None, timeout=30.0):
    return client.call(op, args or {}, timeout=timeout)


def op_ok(data):
    """op 级别的 ok（在响应 data 里）；桥的 envelope ok 只代表传输成功。"""
    if isinstance(data, dict) and "ok" in data:
        return bool(data.get("ok"))
    return True


def expect(name, op, args=None, timeout=30.0, check=None, want_fail=False):
    env_ok, data = call(op, args, timeout)
    if want_fail:
        good = (not op_ok(data)) if isinstance(data, dict) else (not env_ok)
        rec(name, good, data)
        return data
    good = bool(env_ok) and op_ok(data) and (check(data) if check else True)
    rec(name, good, data if not good else "")
    return data


# ---- 页签 / 总览 ----
t = expect("list_tabs", "list_tabs",
           check=lambda d: isinstance(d.get("tabs"), list) and len(d["tabs"]) == 5)
rec("  tabs == 5 个顶层页签", isinstance(t.get("tabs"), list) and len(t.get("tabs", [])) == 5, t.get("tabs"))
rec("  tabs 含 MPR/档案/设置",
    all(any(k in x for x in t.get("tabs", [])) for k in ("MPR", "档案", "设置")), t.get("tabs"))

st = expect("query_state（新 UI 字段）", "query_state",
            check=lambda d: {"tabs", "mpr", "archive", "settings", "pacs", "case", "ai",
                             "pets", "roi_saved", "right_area_visible",
                             "current_tab", "modality", "is_2d"} <= set(d.keys()))
for k in ("tabs", "right_area_visible", "current_tab", "mpr", "archive", "settings",
          "pacs", "case", "ai", "pets", "roi_saved", "legacy_auto_roi_removed"):
    rec("  state.%-22s" % k, k in st, st.get(k) if k in ("right_area_visible", "current_tab") else "")
rec("  初始页=爱宠列表 → 右区隐藏",
    st.get("current_tab") == "爱宠列表" and st.get("right_area_visible") is False,
    {"tab": st.get("current_tab"), "vis": st.get("right_area_visible")})
rec("  旧键 roi_task/roi_running 仍在",
    "roi_task" in st and "roi_running" in st, {"roi_task": st.get("roi_task")})

u = expect("get_ui_state", "get_ui_state",
           check=lambda d: all(k in d for k in ("mpr", "archive", "settings", "pacs", "case", "ai", "pets")))

# ---- 页签切换 + 右区联动 ----
s1 = expect("switch_tab(设置)", "switch_tab", {"title": "设置"},
            check=lambda d: d.get("current_tab") == "设置" and d.get("right_area_visible") is False)
s2 = expect("switch_tab(渲染)", "switch_tab", {"title": "渲染"},
            check=lambda d: d.get("current_tab") == "渲染" and d.get("right_area_visible") is True)
s3 = expect("switch_tab(档案)", "switch_tab", {"title": "档案"},
            check=lambda d: d.get("current_tab") == "档案中心" and d.get("right_area_visible") is True)
a = call("query_state", {})[1].get("archive", {})
rec("  档案页 right_stack=档案册", a.get("right_stack_index") == 1, a.get("right_stack_index"))
expect("switch_tab(MPR)", "switch_tab", {"title": "MPR"},
       check=lambda d: d.get("right_area_visible") is False)
expect("switch_tab(爱宠)", "switch_tab", {"title": "爱宠"},
       check=lambda d: d.get("right_area_visible") is False)
expect("switch_tab(index=越界) 应报错", "switch_tab", {"index": 99}, want_fail=True)
expect("switch_tab(不存在的页) 应报错", "switch_tab", {"title": "不存在页"}, want_fail=True)

# ---- MPR ----
m1 = expect("mpr_set_window_level(wl=-600,ww=1500)", "mpr_set_window_level",
            {"wl": -600, "ww": 1500},
            check=lambda d: d.get("wl") == -600 and d.get("ww") == 1500)
expect("mpr_set_tool(line)", "mpr_set_tool", {"tool": "line"},
       check=lambda d: d.get("tool") == "line")
expect("mpr_set_tool(中文 角度)", "mpr_set_tool", {"tool": "角度"},
       check=lambda d: d.get("tool") == "angle")
expect("mpr_set_tool(短标签 圆)", "mpr_set_tool", {"tool": "圆"},
       check=lambda d: d.get("tool") == "ellipse")
expect("mpr_set_tool(短标签 形)", "mpr_set_tool", {"tool": "形"},
       check=lambda d: d.get("tool") == "poly")
expect("mpr_set_tool(非法值) 应报错", "mpr_set_tool", {"tool": "zzz"}, want_fail=True)
m = expect("mpr_clear(measures)", "mpr_clear_measures", {}, check=lambda d: d.get("ok") is True)
expect("mpr_clear(points)", "mpr_clear_points", {}, check=lambda d: d.get("ok") is True)
expect("mpr_clear(result)", "mpr_clear_result", {}, check=lambda d: d.get("ok") is True)

# ---- 3D SAM / 已保存 ROI ----
d = expect("sam_run(无提示点) 应明确报错", "sam_run", {}, want_fail=True)
rec("  报错含提示点说明", isinstance(d.get("error"), str) and "提示点" in d.get("error", ""), d.get("error"))
rl = expect("sam_list_rois", "sam_list_rois",
            check=lambda d: isinstance(d.get("records"), list))
blocks = expect("list_roi_blocks（兼容旧字段）", "list_roi_blocks",
                check=lambda d: isinstance(d.get("blocks"), list))
rec("  blocks 与 records 数量一致",
    len(blocks.get("blocks", [])) == len(rl.get("records", [])),
    {"blocks": len(blocks.get("blocks", [])), "records": len(rl.get("records", []))})
if blocks.get("blocks"):
    b0 = blocks["blocks"][0]
    rec("  block 含 label_id/volume_cm3/voxel_count/mean_hu",
        {"label_id", "volume_cm3", "voxel_count", "mean_hu"} <= set(b0.keys()), sorted(b0.keys()))
expect("render_roi_labels([]) = 清除高亮", "render_roi_labels", {"label_ids": []},
       check=lambda d: d.get("ok") is True)
expect("sam_delete_roi(不存在的 name) 应报错", "sam_delete_roi", {"name": "__no_such__"},
       want_fail=True)
expect("roi_clear（旧接口→SAM 清空）", "roi_clear", {}, check=lambda d: bool(d.get("called")))
expect("roi_cancel（旧接口→清结果）", "roi_cancel", {}, check=lambda d: d.get("ok") is True)

# ---- 运行期限（30 天强制）----
tr = expect("trial_status", "trial_status",
            check=lambda d: isinstance(d.get("trial"), dict))
tj = tr.get("trial", {})
rec("  期限状态 ok 且剩余天数>0", tj.get("status") == "ok" and (tj.get("days_left") or 0) > 0, tj)
rec("  含 hard_deadline 与 trial_days", bool(tj.get("hard_deadline")) and tj.get("trial_days") == 30, tj)
rec("  query_state 里也有 trial", "trial" in call("query_state", {})[1], "")

# ---- 旧自动 ROI 接口应给废弃说明（而不是静默成功）----
d = expect("trigger_roi(total) 应 deprecated", "trigger_roi", {"task": "total"}, want_fail=True)
rec("  deprecated 标记", d.get("deprecated") is True and "已移除" in str(d.get("error", "")), d.get("error"))
d = expect("set_roi_weight_path 应 deprecated", "set_roi_weight_path", {"path": "C:\\x"}, want_fail=True)
rec("  deprecated 标记", d.get("deprecated") is True, d.get("error"))

# ---- 档案中心 ----
al = expect("archive_list", "archive_list",
            check=lambda d: isinstance(d.get("archives"), list) and int(d.get("count") or 0) >= 1)
names = [x.get("id") for x in al.get("archives", [])]
rec("  含默认案例 0000_DEFAULT_DEMO", any("0000_DEFAULT_DEMO" in str(n) for n in names), names)
rec("  默认案例置顶", bool(names) and "0000_DEFAULT_DEMO" in str(names[0]), names[:2])
rec("  档案项含 title/id",
    all(("title" in x and "id" in x) for x in al.get("archives", [])) if al.get("archives") else False,
    al.get("archives", [])[:1])
rec("  默认案例标记 is_demo", bool(al.get("archives")) and al["archives"][0].get("is_demo") is True)
expect("archive_refresh", "archive_refresh", {}, check=lambda d: d.get("ok") is True)
od = expect("archive_open(默认案例)", "archive_open", {"name": "0000_DEFAULT_DEMO"},
            check=lambda d: d.get("ok") is True and isinstance(d.get("detail"), dict))
rec("  详情含 id/is_demo",
    isinstance(od.get("detail"), dict) and od["detail"].get("is_demo") is True, od.get("detail"))
expect("archive_back", "archive_back", {}, check=lambda d: d.get("ok") is True)
expect("archive_save_shot(非法 kind) 应报错", "archive_save_shot", {"kind": "zzz"}, want_fail=True)

# ---- 设置 ----
sg = expect("settings_get", "settings_get",
            check=lambda d: "key_set" in d and "config_path" in d)
rec("  默认不返回 api_key 明文字段", "api_key" not in sg, sorted(sg.keys()))
ss = expect("settings_set(model/temperature)", "settings_set",
            {"model": "deepseek-chat", "temperature": 0.2},
            check=lambda d: d.get("saved", {}).get("model") == "deepseek-chat")
rec("  配置文件已生成", os.path.isfile(CFG), CFG)
expect("settings_set(无字段) 应报错", "settings_set", {}, want_fail=True)
expect("settings_set(api_key 无 allow_key) 应拒绝", "settings_set",
       {"api_key": "sk-test"}, want_fail=True)

# ---- 病例档案 ----
cg = expect("case_get", "case_get",
            check=lambda d: int(d.get("field_count") or 0) >= 20 and isinstance(d.get("values"), dict))
rec("  字段数=24", cg.get("field_count") == 24, cg.get("field_count"))
cs = expect("case_set(species/breed)", "case_set",
            {"values": {"species": "犬", "breed": "泰迪"}},
            check=lambda d: d.get("values", {}).get("species") == "犬")
expect("case_set(空) 应报错", "case_set", {"values": {}}, want_fail=True)
expect("case_set(未知键) 应回报 unknown_keys", "case_set",
       {"values": {"__nope__": "1"}},
       check=lambda d: "__nope__" in (d.get("unknown_keys") or []))

# ---- 爱宠列表 ----
p0 = expect("pets_get_state", "pets_get_state", check=lambda d: "root" in d and "status" in d)
ps = expect("pets_scan(dummy_dicom)", "pets_scan", {"root": os.path.join(ROOT, "dummy_dicom")},
            timeout=180.0, check=lambda d: d.get("ok") is True)
rec("  扫描后 status 非空", bool(ps.get("status") or ps.get("root")), ps.get("status"))

# ---- PACS ----
pc = expect("pacs_get_state", "pacs_get_state",
            check=lambda d: "node" in d and "status" in d and "studies" in d)
expect("pacs_retrieve(无检查) 应报错", "pacs_retrieve", {}, want_fail=True)

# ---- AI ----
ai = expect("ai_get_state", "ai_get_state", check=lambda d: "key_set" in d)
if ai.get("key_set") is False:
    expect("ai_run(未配置 Key) 应报错", "ai_run", {}, want_fail=True)
else:
    rec("ai_run 跳过（已配置 Key，避免真实外部调用）", True)

# ---- 截图（VTK + 整窗）----
SHOTS = os.path.join(ROOT, "mcp_records", "shots")
env, sc = call("screenshot", {"out_dir": SHOTS}, 30.0)
rec("screenshot(VTK 渲染窗)", env and op_ok(sc) and "vtk_visible" in sc,
    {"vtk_visible": sc.get("vtk_visible")})
rec("  screenshot 报告渲染区隐藏(爱宠列表页→隐藏)",
    sc.get("vtk_visible") is False and bool(sc.get("hint")), sc.get("hint"))
env2, sc2 = call("screenshot", {"out_dir": SHOTS, "switch_to_render": True}, 30.0)
rec("screenshot(switch_to_render=True) 自动切到渲染页",
    env2 and op_ok(sc2) and sc2.get("vtk_visible") is True and (sc2.get("hint") or "") == "",
    {"vtk_visible": sc2.get("vtk_visible"), "hint": sc2.get("hint")})
st2 = call("query_state", {})[1]
rec("  切页后 current_tab=渲染 且右区可见",
    st2.get("current_tab") == "渲染" and st2.get("right_area_visible") is True,
    {"tab": st2.get("current_tab"), "vis": st2.get("right_area_visible")})
for tab in ("设置", "档案", "爱宠", "MPR"):
    env, w = call("screenshot_window",
                  {"tab": tab, "out_dir": SHOTS}, 30.0)
    stats = w.get("stats", {})
    rec("screenshot_window(%s)" % tab,
        env and op_ok(w) and stats.get("unique_colors", 0) > 50,
        {"tab": w.get("tab"), "stats": stats})

# ---- 旧的渲染参数接口（回归）----
expect("list_presets", "list_presets", check=lambda d: isinstance(d.get("presets"), list))
expect("get_thresholds", "get_thresholds", check=lambda d: "ssd" in d and "vr" in d)
expect("get_render_params", "get_render_params", check=lambda d: "render_mode" in d)
expect("set_mode(stable)", "set_mode", {"mode": "stable"},
       check=lambda d: d.get("requested") == "stable")
expect("set_mode(非法) 应报错", "set_mode", {"mode": "nope"}, want_fail=True)
expect("toggle_background", "toggle_background", check=lambda d: "bg_is_white" in d)

# ---- 清理：删掉自检生成的配置文件，回到"未配置"状态 ----
try:
    if os.path.isfile(CFG):
        os.remove(CFG)
        rec("清理 deepseek_config.json", True)
except Exception as e:  # noqa: BLE001
    rec("清理 deepseek_config.json", False, str(e))

# ---- shutdown ----
ok, data = call("shutdown", {}, 10.0)
rec("shutdown(op)", ok, data)
exited = False
for _ in range(40):
    if proc.poll() is not None:
        exited = True
        break
    time.sleep(0.5)
rec("GUI 进程已退出", exited, proc.poll())
if not exited:
    proc.terminate()

n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
print("\n===== 自检汇总: %d 项, 失败 %d =====" % (len(RESULTS), n_fail), flush=True)
for name, ok, detail in RESULTS:
    if not ok:
        print("  FAIL " + name + "  " + json.dumps(detail, ensure_ascii=True)[:300], flush=True)
sys.exit(1 if n_fail else 0)
