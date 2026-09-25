# -*- coding: utf-8 -*-
"""工具层静态自检：注册所有 tools 模块并列出工具名（不需要 GUI）。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"I:\animal_dicom")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class Stub:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


from mcp_ssd_vr import tools  # noqa: E402

stub = Stub()
registered = tools.register_all(stub)
names = sorted(stub.tools)
print("modules=", registered)
print("tool_count=", len(names))
for n in names:
    print("  " + n)

dupes = [n for n in names if names.count(n) > 1]
print("dupes=", dupes)
new_expected = [
    "ssdvr_list_tabs", "ssdvr_switch_tab", "ssdvr_ui_state", "ssdvr_screenshot_window",
    "ssdvr_mpr_set_window_level", "ssdvr_mpr_set_tool", "ssdvr_mpr_clear",
    "ssdvr_sam_run", "ssdvr_sam_list_rois", "ssdvr_sam_load_roi", "ssdvr_sam_delete_roi",
    "ssdvr_archive_list", "ssdvr_archive_refresh", "ssdvr_archive_open", "ssdvr_archive_save_shot",
    "ssdvr_settings_get", "ssdvr_settings_set", "ssdvr_case_get", "ssdvr_case_set",
    "ssdvr_pets_scan", "ssdvr_pacs_status", "ssdvr_pacs_echo", "ssdvr_pacs_find_studies",
    "ssdvr_pacs_retrieve", "ssdvr_ai_status", "ssdvr_ai_run",
]
missing = [n for n in new_expected if n not in stub.tools]
print("missing_new_tools=", missing)
print("OK" if not missing and not dupes else "PROBLEM")
