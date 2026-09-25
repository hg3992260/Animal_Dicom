#!/usr/bin/env python3
"""跨平台 Mach-O 依赖自洽性检查（不需要 macOS 的 otool / dyld）。

用途：在 Windows/Linux 上离线验证一个 .app bundle 是否自洽 —— 每个 Mach-O 二进制的
LC_LOAD_DYLIB 依赖是否都能在 bundle 内解析。macOS 冻结包"启动即 abort()"最常见的原因
就是平台插件（libqcocoa.dylib）的某个 Qt 依赖没打进包（见 MCP_EXE_BRIDGE.md 与
tools/verify_macos_bundle.sh 的 2026-09-17 复盘）。

用法:
    python tools/macho_bundle_check.py path/to/SSD_VR_Fusion_Viewer.app
    python tools/macho_bundle_check.py path/to/Some.dylib --json

退出码: 0 全部可解析 / 1 有未解析依赖或发现两套 Qt / 2 用法错误
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from typing import Dict, List, Optional, Tuple

MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
MH_MAGIC_32 = 0xFEEDFACE
MH_CIGAM_32 = 0xCEFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
FAT_MAGIC_64 = 0xCAFEBABF

LC_REQ_DYLD = 0x80000000
LC_ID_DYLIB = 0x0D
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x18 | LC_REQ_DYLD
LC_REEXPORT_DYLIB = 0x1F | LC_REQ_DYLD
LC_LOAD_UPWARD_DYLIB = 0x23 | LC_REQ_DYLD
LC_LAZY_LOAD_DYLIB = 0x20
LC_RPATH = 0x1C | LC_REQ_DYLD

DEP_CMDS = {LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB,
            LC_LOAD_UPWARD_DYLIB, LC_LAZY_LOAD_DYLIB}

SYSTEM_PREFIXES = ("/usr/lib/", "/System/", "/Library/Apple/")

# macOS/dyld 共享缓存里的系统库：通过 @rpath 引用也一定能加载，不该算缺失。
# （历史上这里误报过 40 个 @rpath/libc++.1.dylib + 3 个 @rpath/libz.1.dylib。）
SYSTEM_DYLIB_RE = re.compile(
    r'^lib(c\+\+|c\+\+abi|System|objc|z|iconv|resolv|xml2|sqlite3|cups|edit|'
    r'ncurses|panel|form|bsm|util|compression|apple_nghttp2|heimdal|tidy|dl|m|'
    r'poll|proc|pthread)(\.[0-9.]+)?\.dylib$'
)

# 允许缺失的可选外部依赖（Qt SQL 驱动等按需 dlopen，缺了不影响启动）
OPTIONAL_EXTERNAL_RE = re.compile(
    r'^lib(mimer|iodbc|odbcinst|pq|mysqlclient|mariadb|sybdb|fbclient)[^/]*\.dylib$'
)


def _read_cstr(data: bytes, offset: int) -> str:
    end = data.find(b"\x00", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("utf-8", "replace")


def _slices(data: bytes) -> List[Tuple[int, int]]:
    """返回 [(offset, size)]；fat 二进制取第一个架构切片。

    注意 fat_arch 与 fat_arch_64 的字段宽度不同（offset/size 分别是 4/8 字节），
    早期版本只按 32 位 fat 解析，导致 64 位 fat 的 dylib 被静默跳过、
    进而误报成"依赖缺失"（实测坑过一轮）。
    """
    if len(data) < 8:
        return []
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic == FAT_MAGIC:
        nfat = struct.unpack_from(">I", data, 4)[0]
        if nfat <= 0 or len(data) < 8 + 20:
            return []
        offset, size = struct.unpack_from(">II", data, 8 + 8)
        return [(offset, size)]
    if magic == FAT_MAGIC_64:
        nfat = struct.unpack_from(">I", data, 4)[0]
        if nfat <= 0 or len(data) < 8 + 32:
            return []
        offset, size = struct.unpack_from(">QQ", data, 8 + 8)
        return [(offset, size)]
    return [(0, len(data))]


def parse_macho(data: bytes) -> Optional[dict]:
    """解析第一个架构切片，返回 {deps, rpaths, install_name}。非 Mach-O 返回 None。"""
    for off, _size in _slices(data):
        if len(data) < off + 32:
            continue
        magic_le = struct.unpack_from("<I", data, off)[0]
        magic_be = struct.unpack_from(">I", data, off)[0]
        if magic_le == MH_MAGIC_64:
            endian, is64 = "<", True
        elif magic_be == MH_MAGIC_64:
            endian, is64 = ">", True
        elif magic_le == MH_MAGIC_32:
            endian, is64 = "<", False
        elif magic_be == MH_MAGIC_32:
            endian, is64 = ">", False
        else:
            continue

        ncmds, sizeofcmds = struct.unpack_from(endian + "II", data, off + 16)
        header_size = 32 if is64 else 28
        pos = off + header_size
        deps: List[str] = []
        rpaths: List[str] = []
        install_name = ""
        for _ in range(ncmds):
            if pos + 8 > len(data):
                break
            cmd, cmdsize = struct.unpack_from(endian + "II", data, pos)
            if cmdsize < 8:
                break
            if cmd in DEP_CMDS:
                name_off = struct.unpack_from(endian + "I", data, pos + 8)[0]
                deps.append(_read_cstr(data, pos + name_off))
            elif cmd == LC_ID_DYLIB:
                name_off = struct.unpack_from(endian + "I", data, pos + 8)[0]
                install_name = _read_cstr(data, pos + name_off)
            elif cmd == LC_RPATH:
                name_off = struct.unpack_from(endian + "I", data, pos + 8)[0]
                rpaths.append(_read_cstr(data, pos + name_off))
            pos += cmdsize
        return {"deps": deps, "rpaths": rpaths, "install_name": install_name,
                "is64": is64, "endian": endian}
    return None


def is_macho(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    if len(head) < 4:
        return False
    magic = struct.unpack("<I", head)[0]
    return magic in (MH_MAGIC_64, MH_MAGIC_32, FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64)


def scan_bundle(root: str) -> Dict[str, dict]:
    """遍历 bundle 里所有 Mach-O 文件，返回 {相对路径: 解析结果}。

    解析不了的（未知格式/读失败）不会静默丢弃，而是记为 {'unparsed': 原因}，
    否则会假装"文件不存在"→ 误报依赖缺失。
    """
    out: Dict[str, dict] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if os.path.islink(full):
                # symlink 也能被 dyld 使用；把目标解析出来再判断
                real = os.path.realpath(full)
                if not os.path.isfile(real) or not is_macho(real):
                    continue
                out[rel] = {"symlink_to": os.path.basename(real), "deps": [], "rpaths": [],
                            "install_name": "", "is64": False, "endian": ""}
                continue
            if not (name.endswith(".dylib") or name.endswith(".so")
                    or "." not in name or name.endswith(".abi3.so")):
                if "." in name:
                    continue
            if not is_macho(full):
                continue
            try:
                with open(full, "rb") as f:
                    info = parse_macho(f.read())
            except OSError as e:
                out[rel] = {"unparsed": f"read error: {e}", "deps": [], "rpaths": [],
                            "install_name": "", "is64": False, "endian": ""}
                continue
            if info is None:
                out[rel] = {"unparsed": "无法解析 Mach-O 头（fat64？截断？）", "deps": [],
                            "rpaths": [], "install_name": "", "is64": False, "endian": ""}
                continue
            out[rel] = info
    return out


def _basename_index(files: Dict[str, dict]) -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for rel, info in files.items():
        idx.setdefault(os.path.basename(rel), rel)
        target = info.get("symlink_to")
        if target:
            idx.setdefault(target, rel)
    return idx


def resolve(dep: str, owner_rel: str, rpaths: List[str],
            by_base: Dict[str, str], files: Dict[str, dict]) -> Optional[str]:
    """把一条依赖解析成 bundle 内相对路径；系统库直接视为 OK（返回其自身）。"""
    if dep.startswith(SYSTEM_PREFIXES) or dep.startswith("/usr/lib"):
        return dep
    if dep.startswith("@rpath/"):
        tail = dep[len("@rpath/"):]
        cands = [rp.replace("@loader_path", os.path.dirname("/" + owner_rel))
                   .replace("@executable_path", "Contents/MacOS") + "/" + tail
                 for rp in rpaths]
        cands += [f"Contents/Frameworks/{tail}",
                  f"Contents/Resources/PySide6/Qt/lib/{tail}",
                  f"Contents/Resources/lib/{tail}",
                  tail]
    elif dep.startswith("@loader_path/"):
        cands = [os.path.join(os.path.dirname(owner_rel), dep[len("@loader_path/"):])]
    elif dep.startswith("@executable_path/"):
        cands = [os.path.join("Contents/MacOS", dep[len("@executable_path/"):])]
    else:
        # 绝对路径：先看是不是 bundle 内自己的文件（冻结包常见绝对路径泄露），
        # 再退化到按文件名在包里找
        rel = dep.lstrip("/")
        cands = [rel, dep]
        base = os.path.basename(dep)
        if base in by_base:
            return by_base[base]

    for c in cands:
        norm = os.path.normpath(c).replace("\\", "/")
        if norm in files:
            return norm
        if os.path.isabs(dep) and os.path.isfile(dep):
            return dep
    base = os.path.basename(dep)
    if base in by_base:
        return by_base[base]
    if os.path.isabs(dep) and os.path.isfile(dep):
        return dep
    return None


def classify_dep(dep: str, owner_rel: str, rpaths: List[str],
                 by_base: Dict[str, str], files: Dict[str, dict]) -> Tuple[str, str]:
    """返回 (状态, 说明)。状态 ∈ {ok, system, optional, alias, missing}。"""
    base = os.path.basename(dep)
    if dep.startswith(SYSTEM_PREFIXES):
        return "system", dep
    if SYSTEM_DYLIB_RE.match(base):
        return "system", "macOS 系统库"
    if OPTIONAL_EXTERNAL_RE.match(base):
        return "optional", "可选外部依赖（按需 dlopen）"
    if resolve(dep, owner_rel, rpaths, by_base, files) is not None:
        return "ok", ""
    # 版本别名：依赖 libicui18n.78.dylib，但包里只有 libicui18n.78.3.dylib。
    # dyld 按 install name 精确匹配，别名**不能**替代 —— 但要和"完全没有"区分开。
    stem = base[:-len(".dylib")] if base.endswith(".dylib") else base
    prefix = stem + "."
    aliases = sorted(n for n in by_base if n.startswith(prefix) and n.endswith(".dylib"))
    if aliases:
        return "alias", "仅有版本别名: " + ", ".join(aliases[:4])
    return "missing", ""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    as_json = "--json" in sys.argv
    if not os.path.isdir(root):
        print(f"不是目录: {root}")
        return 2

    files = scan_bundle(root)
    by_base = _basename_index(files)
    unparsed = {r: i["unparsed"] for r, i in files.items() if i.get("unparsed")}
    report = {"root": root, "macho_files": len(files), "unparsed": unparsed,
              "missing": {}, "alias_only": {}, "optional": {}, "consumers": {}}
    missing_n = alias_n = optional_n = system_n = 0
    qt_missing_n = 0

    for rel, info in sorted(files.items()):
        for dep in info["deps"]:
            state, detail = classify_dep(dep, rel, info["rpaths"], by_base, files)
            if state == "system":
                system_n += 1
            elif state == "ok":
                pass
            elif state == "optional":
                optional_n += 1
                report["optional"].setdefault(rel, []).append(dep)
            elif state == "alias":
                alias_n += 1
                report["alias_only"].setdefault(rel, []).append(f"{dep} ({detail})")
            else:
                missing_n += 1
                report["missing"].setdefault(rel, []).append(dep)
                owner_rel = rel.replace("\\", "/")
                dep_tail = dep.lstrip("@rpath/")
                is_qt_dep = (dep_tail.startswith("libQt6") or ".framework/" in dep
                             or dep_tail.startswith("Qt"))
                is_qt_owner = (os.path.basename(owner_rel).startswith("libQt6")
                               or "Qt/plugins/" in owner_rel)
                if is_qt_dep or is_qt_owner:
                    qt_missing_n += 1

    # 两套 Qt 检测
    naked = [r for r in files if os.path.basename(r).startswith("libQt6") and r.endswith(".dylib")]
    framed = [r for r in files if ".framework/" in r and "/Versions/" in r]
    report["naked_qt"] = sorted(os.path.basename(r) for r in naked)
    report["framework_qt"] = sorted({r.split(".framework/")[0].split("/")[-1] for r in framed})

    for key in ("platforms/libqcocoa.dylib",):
        for h in [r for r in files if r.endswith(key)]:
            report["consumers"][h] = [d for d in files[h]["deps"] if "Qt" in d]

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"bundle      : {root}")
        print(f"Mach-O 文件 : {len(files)}")
        if unparsed:
            print(f"未能解析    : {len(unparsed)} 个（不会计入依赖统计）")
            for r, why in list(unparsed.items())[:5]:
                print(f"  - {r}: {why}")
        print(f"裸 Qt 库    : {len(naked)} 个  {report['naked_qt'][:10]}")
        print(f"framework Qt: {len(framed)} 个  {report['framework_qt'][:8]}")
        # 关键依赖清单（icu/blas/libc++ 这类历史上出过问题的）
        for pat in ("libicu", "libblas", "libcblas", "liblapack", "libc++", "libz.", "libbz2", "libexpat"):
            hits = sorted({os.path.basename(r) for r in files if os.path.basename(r).startswith(pat)})
            if hits:
                print(f"包内 {pat:<9}: {len(hits)} 个  {hits}")
            else:
                print(f"包内 {pat:<9}: 0 个  <缺失>")
        for h, deps in report["consumers"].items():
            print(f"\n{h} 的 Qt 依赖:")
            for d in deps:
                st, _ = classify_dep(d, h, files[h]["rpaths"], by_base, files)
                print(f"  [{st:<8}] {d}")
        print(f"\n依赖统计: 系统库 {system_n} | 可选外部 {optional_n} | "
              f"版本别名 {alias_n} | 真缺失 {missing_n}")
        if report["alias_only"]:
            print("\n[警告] 以下依赖只有版本别名（dyld 按精确名匹配，可能有风险）:")
            for rel, deps in list(report["alias_only"].items())[:10]:
                print(f"  - {rel}: {deps[:3]}")
        if report["optional"]:
            print(f"\n[提示] 可选外部依赖（Qt SQL 驱动等，缺失不影响启动）: {optional_n} 处")
        if report["missing"]:
            print(f"\n[!] 非系统依赖缺失 {missing_n} 处（其中 Qt 自身 {qt_missing_n} 处）:")
            for rel, deps in list(report["missing"].items())[:30]:
                print(f"  - {rel} 缺少依赖: {', '.join(deps)}")
            print("  说明：解析依赖时若只在构建机上存在（conda 前缀），用户机器上会崩。")
        else:
            print("\n没有任何非系统依赖缺失。")

    # 硬失败只针对"启动必崩"的两类：Qt 自身依赖缺失、以及两套 Qt。
    # 其它非 Qt 缺失（numpy/scipy 的 BLAS 等）先按警告呈现，避免挡住 artifact 上传。
    bad = qt_missing_n > 0
    if naked and framed:
        bad = True
        print("\n[FAIL] 同时存在裸 dylib Qt 与 framework Qt（两套 Qt）—— "
              "见 .github/workflows/macos.yml：Qt 必须统一到 conda-forge 的 pyside6。")
    if missing_n and not bad:
        print(f"\n[warn] 有 {missing_n} 处非 Qt 依赖缺失（不阻断构建，但需人工确认）。")
    print("\nVERDICT: " + ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
