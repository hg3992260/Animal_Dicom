"""PyInstaller runtime hook —— macOS 冻结包：在任何用户代码之前修好动态库/插件搜索路径。

为什么必须是 runtime hook（而不是 main() 里的代码）：
    ssd_vr_viewer.py **模块级**就 import PySide6/PyCt6，也就是在 main() 之前 Qt 的动态库
    就已经被 dlopen 了。等到 main() 再设 DYLD_FALLBACK_LIBRARY_PATH 已经太迟。
    runtime hook 由 PyInstaller 的 bootloader 在最早期执行，是唯一来得及的位置。

解决什么问题（2026-09-17 第三轮排查）：
    conda 的 libicu/libbz2/libexpat 等以 **symlink** 提供 soname
    （libicuuc.78.dylib -> libicuuc.78.3.dylib）。PyInstaller 收集时解析了 symlink，
    包里只有 libicuuc.78.3.dylib，而 libQt6Core 的 install name 写的是
    @rpath/libicuuc.78.dylib —— 于是：
      · CI 上因为 conda 环境目录还在，恰好能找到 → 冒烟测试通过
      · 用户机器上没有 conda 环境 → 启动即崩
    spec 侧已把 soname 名字补进包（_qt_dep_closure）；本 hook 负责让 dyld 真的去
    Contents/Frameworks 这些目录里找（@rpath 解析失败时的 fallback）。

只做三件事，全部 try/except 包裹，绝不会因为找不到目录而影响启动：
    1. DYLD_FALLBACK_LIBRARY_PATH / DYLD_LIBRARY_PATH 前置候选目录
    2. QT_PLUGIN_PATH / QT_QPA_PLATFORM_PLUGIN_PATH 指向含 libqcocoa 的插件目录
    3. 把结果打印到 stderr（ASCII），方便崩溃时复盘
"""
import os
import sys

_PLUGIN_SUBDIRS = (
    os.path.join('PySide6', 'Qt', 'plugins'),
    os.path.join('PySide6', 'plugins'),
    'plugins',
    os.path.join('qt6', 'plugins'),
)


def _bundle_dirs():
    """返回 (frameworks, resources, exe_dir)；非 macOS 冻结包时尽量给出合理值。"""
    exe = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe)
    meipass = getattr(sys, '_MEIPASS', '')
    contents = os.path.dirname(exe_dir)          # .../X.app/Contents
    frameworks = meipass or os.path.join(contents, 'Frameworks')
    resources = os.path.join(contents, 'Resources')
    if not os.path.isdir(resources):
        resources = meipass or resources
    return frameworks, resources, exe_dir


def _prepend_path(env_name, dirs):
    uniq = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in uniq:
            uniq.append(d)
    if not uniq:
        return []
    old = [p for p in os.environ.get(env_name, '').split(os.pathsep) if p]
    merged = uniq + [p for p in old if p not in uniq]
    os.environ[env_name] = os.pathsep.join(merged)
    return uniq


def _find_plugin_dir(frameworks, resources, exe_dir):
    candidates = []
    for base in (frameworks, resources, exe_dir):
        for sub in _PLUGIN_SUBDIRS:
            candidates.append(os.path.join(base, sub))
    candidates.append(os.path.join(os.path.dirname(frameworks), 'PlugIns'))
    found = ''
    for d in candidates:
        if os.path.isfile(os.path.join(d, 'platforms', 'libqcocoa.dylib')):
            found = d
            break
    if not found:
        for d in candidates:
            if os.path.isdir(os.path.join(d, 'platforms')):
                found = d
                break
    return found


def _main():
    if sys.platform != 'darwin':
        return
    frameworks, resources, exe_dir = _bundle_dirs()
    lib_dirs = [
        frameworks,
        os.path.join(frameworks, 'PySide6', 'Qt', 'lib'),
        os.path.join(resources, 'lib'),          # spec 的 soname 实体就落在这里
        resources,
        os.path.join(resources, 'PySide6', 'Qt', 'lib'),
        exe_dir,
    ]
    lib_hits = _prepend_path('DYLD_FALLBACK_LIBRARY_PATH', lib_dirs)
    _prepend_path('DYLD_LIBRARY_PATH', lib_dirs)

    plugin_dir = _find_plugin_dir(frameworks, resources, exe_dir)
    if plugin_dir:
        os.environ['QT_PLUGIN_PATH'] = plugin_dir + (
            os.pathsep + os.environ['QT_PLUGIN_PATH'] if os.environ.get('QT_PLUGIN_PATH') else '')
        platforms = os.path.join(plugin_dir, 'platforms')
        if os.path.isdir(platforms):
            os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = platforms

    try:
        sys.stderr.write(
            '[rthook-macos-qt] frameworks=%s\n'
            '[rthook-macos-qt] dyld_fallback=%s\n'
            '[rthook-macos-qt] plugin_dir=%s\n' % (frameworks, lib_hits, plugin_dir or '(none)'))
        sys.stderr.flush()
    except Exception:
        pass


try:
    _main()
except Exception as _e:  # noqa: BLE001 —— 任何异常都不该阻止应用启动
    try:
        sys.stderr.write(f'[rthook-macos-qt] skipped: {_e}\n')
    except Exception:
        pass
