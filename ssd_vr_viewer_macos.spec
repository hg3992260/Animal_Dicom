# -*- mode: python ; coding: utf-8 -*-
"""macOS .app 打包（PyInstaller spec）。

════════════════════════════════════════════════════════════════════════════
两次崩溃的复盘（务必先读；改这个文件前请确认你理解这两条）
════════════════════════════════════════════════════════════════════════════
【第一次】SIGABRT：QApplication 构造时 qFatal
  现象: "Could not load the Qt platform plugin cocoa"，
        QT_DEBUG_PLUGINS=1 显示 libqcocoa.dylib 需要 @rpath/libQt6Gui.6.dylib 而包里没有。
  原因: CI 上同时装了两套 Qt —— conda-forge 的 vtk 链接**裸 dylib** Qt，
        pip 的 PySide6 是 **framework** Qt；旧 spec 有两段代码把裸命名的
        libQt6*.dylib 全部删掉（"avoid conflict with PySide6's framework Qt"），
        于是裸命名的插件成了孤儿。
  修法: 不再删任何 Qt（本文件已无任何过滤）。

【第二次】EXC_BAD_ACCESS：事件循环里随机崩（processExposeEvent → NSOpenGLView）
  现象: 启动日志里 "Class QMetalLayer is implemented in both
        .../libQt6Gui.6.dylib and .../PySide6/Qt/lib/QtGui.framework/.../QtGui"。
  原因: 只"不删"还不够 —— 两个发行版的 Qt 会**同时进包**，同一个 Qt 出现三份：
        Frameworks/libQt6*.dylib（conda）+ Frameworks/PySide6/Qt/lib/libQt6*.6.11.2.dylib
        （pip wheel）+ Frameworks/PySide6/Qt/lib/Qt*.framework（pip wheel）。
        ObjC 类重复注册 → 随机崩溃。
  修法: ① CI 侧把 Qt 统一到 conda-forge 的 pyside6（见 .github/workflows/macos.yml，
        关键是 `pip install --no-deps PyCt6`，否则 pip 又把 wheel 版拉回来）；
        ② 本文件和 CI 都有"只允许一套 Qt"的守卫：宁可构建失败，也不产出会崩的包。

用户级结论：**Qt 只能有一个来源**。本 spec 只做三件事：
  1. 四个 collect_all（PyCt6 / vtkmodules / SimpleITK / PySide6）—— 与已知能跑的
     DICOM_Analysis_Tool.app 的 spec 同构（它没有任何 Qt 过滤）；
  2. Qt 库/插件**不做显式重复收集**（交给 PyInstaller 的 PySide6 hook + 依赖分析，
     显式再收一份只会制造 "implemented in both"）；只有在 hook 完全没收到
     cocoa 插件时才补一次兜底；
  3. 构建期守卫 _qt_audit()：出现两套 Qt 发行版、或同一个 Qt 组件落在两个 dest，
     直接 SystemExit 并打印修复指引。
"""
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# ── 四个包全量收集 ───────────────────────────────────────────────────────────
pct6_datas, pct6_bins, pct6_hidden = collect_all('PyCt6')
vtk_datas, vtk_bins, vtk_hidden = collect_all('vtkmodules')
sitk_datas, sitk_bins, sitk_hidden = collect_all('SimpleITK')
ps6_datas, ps6_bins, ps6_hidden = collect_all('PySide6')

# ── 兜底用的 Qt 插件候选目录（只在 hook 没跑到时才用；正常构建不会用到） ─────
QT_PLUGIN_DST = 'PySide6/Qt/plugins'


def _prefixes():
    out = []
    for p in (os.environ.get('CONDA_PREFIX'), os.environ.get('PREFIX'),
              sys.prefix, os.path.dirname(os.path.dirname(sys.executable)),
              os.path.dirname(sys.executable)):
        if p and p not in out:
            out.append(p)
    return out


def _pyside6_roots():
    out = []
    try:
        import PySide6  # noqa: PLC0415
        out.append(Path(PySide6.__path__[0]))
    except Exception:
        pass
    for p in _prefixes():
        for cand in (Path(p) / 'lib', Path(p)):
            try:
                out += list(cand.glob('python3.*/site-packages/PySide6'))
            except Exception:
                pass
    return [r for r in out if r.is_dir()]


def _plugin_dirs():
    out = []
    for root in _pyside6_roots():
        out += [root / 'Qt' / 'plugins', root / 'plugins']
    for p in _prefixes():
        base = Path(p)
        out += [base / 'lib' / 'qt6' / 'plugins', base / 'plugins',
                base / 'lib' / 'plugins', base / 'share' / 'qt6' / 'plugins']
    return [d for d in out if d.is_dir()]


a = Analysis(
    ['ssd_vr_viewer.py'],
    pathex=[],
    binaries=pct6_bins + vtk_bins + sitk_bins + ps6_bins,
    datas=pct6_datas + vtk_datas + sitk_datas + ps6_datas + [
        ('scientific.json', '.'),
        ('dark.qss', '.'),
        ('light.qss', '.'),          # 浅色主题（皮肤优先级 retro > light > dark）
        ('retro.qss', '.'),          # 暖橙复古拟物皮肤（首选）
        ('warm_orange.json', '.'),   # PyCt6 暖橙主题
        ('case_template.json', '.'), # 病例档案模板（5 段 24 字段）
        ('presets.xml', '.'),
        ('render_templates.json', '.'),
        ('logo.png', '.'),
        ('logo.icns', '.'),
        ('segmentation', 'segmentation'),
        ('mcp_ssd_vr', 'mcp_ssd_vr'),
    ],
    hiddenimports=[
        'PyCt6',
        'PyCt6.widgets.c_button', 'PyCt6.widgets.c_label', 'PyCt6.widgets.c_line_edit',
        'PyCt6.widgets.c_combo_box', 'PyCt6.widgets.c_slider', 'PyCt6.widgets.c_frame',
        'PyCt6.widgets.c_text_edit',
        'PyCt6.windows.c_main_window',
        'PyCt6.appearance.theme_manager', 'PyCt6.appearance.mode_manager',
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSvg',
        'PySide6.QtNetwork', 'PySide6.QtDBus', 'PySide6.QtPrintSupport',
        'vtkmodules.qt.QVTKRenderWindowInteractor', 'vtkmodules.util.numpy_support',
        'scipy.ndimage', 'skimage.restoration', 'skimage.filters',
        'nibabel', 'nibabel.nifti1',
        'pacs_client', 'pynetdicom', 'pynetdicom.sop_class', 'pydicom',
        # 期限校验：缺失时 main() 会直接拒绝启动（退出码 4）→ 必须随包
        'license_guard', 'deepseek_client', 'openpyxl',
        'mcp_ssd_vr.gui_bridge', 'mcp_ssd_vr.bridge_client',
        'mcp_ssd_vr.bridge_registry', 'mcp_ssd_vr.state', 'mcp_ssd_vr.config',
        'mcp_ssd_vr.recorder', 'mcp_ssd_vr.dicom',
        'mcp_ssd_vr.tools', 'mcp_ssd_vr.tools.cases',
    ],
    hookspath=[],
    hooksconfig={},
    # macOS 上必须用 runtime hook 提前设好 dyld/Qt 搜索路径：
    # ssd_vr_viewer.py 在**模块级**就 import PySide6，等 main() 再设就太迟了。
    runtime_hooks=['pyi_rth_macos_qt.py'],
    # 注意：**不能**排除 PySide6.QtNetwork / QtDBus —— cocoa 插件会用到。
    excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets'],
    noarchive=False,
)


# ════════════════════════════════════════════════════════════════════════════
# 构建期守卫 1/2：cocoa 插件兜底（只在 hook 一个都没收到时才补，绝不重复）
# ════════════════════════════════════════════════════════════════════════════
def _has_cocoa(toc):
    return any('libqcocoa' in str(e[0]) for e in toc)


if not (_has_cocoa(a.binaries) or _has_cocoa(a.datas)):
    print('[spec] WARNING: PySide6 hook 没收集到平台的 libqcocoa，改用兜底收集')
    _seen = set()
    for _d in _plugin_dirs():
        for _f in sorted(_d.rglob('*')):
            if not _f.is_file() or _f.is_symlink():
                continue
            _rel = _f.relative_to(_d).as_posix()
            if _rel in _seen:
                continue
            _seen.add(_rel)
            _sub = os.path.dirname(_rel)
            _dest = f'{QT_PLUGIN_DST}/{_sub}' if _sub else QT_PLUGIN_DST
            if _f.suffix in ('.dylib', '.so'):
                a.binaries.append((_dest, str(_f), 'BINARY'))
            else:
                a.datas.append((_dest, str(_f), 'DATA'))
    print(f'[spec] 兜底补入 {len(_seen)} 个插件文件')
else:
    print('[spec] cocoa 插件已由 hook 收集，跳过兜底')


# ════════════════════════════════════════════════════════════════════════════
# 构建期守卫：只允许**一套** Qt
#
# 判据（对应两次崩溃，全部只用确定性证据，不用文件名猜）：
#   ① PySide6 必须来自 conda —— `$CONDA_PREFIX/conda-meta/pyside6-*.json` 存在。
#      conda 装的包也会出现在 `pip list` 里，所以**不能用 pip list 判断**！
#      （2026-09-17 CI 就因为这条误判把已经修好的构建拦下来了。）
#   ② PySide6 包内不应出现 Qt*.framework —— 那是 pip wheel 的特征
#      （conda-forge 的 pyside6 实测 framework 数为 0）。
#   ③ 同一个 Qt 组件不应落在两个 dest（CONDA_PREFIX/lib 的裸库 vs 包内 Qt 库），
#      这正是 "Class QMetalLayer is implemented in both ..." 的来源。
# ════════════════════════════════════════════════════════════════════════════
_QT_FW_RE = re.compile(r'(?:^|/)Qt[A-Za-z0-9_]*\.framework/')


def _qt_provider_problems(prefix=None, pyside6_root=None):
    """返回问题列表（空 = 只有一套 Qt 且来源正确）。参数可注入，便于单元测试。"""
    problems = []
    if prefix is None:
        prefix = os.environ.get('CONDA_PREFIX') or os.environ.get('PREFIX') or sys.prefix
    meta = Path(prefix) / 'conda-meta'
    has_pyside6 = bool(list(meta.glob('pyside6-*.json'))) if meta.is_dir() else False
    if not has_pyside6:
        problems.append(
            f'PySide6 不是 conda 装的（{meta} 下没有 pyside6-*.json）'
            ' → 会和 conda 的 vtk/Qt 混装')

    if pyside6_root is None:
        try:
            import PySide6  # noqa: PLC0415
            pyside6_root = Path(PySide6.__path__[0])
        except Exception:
            pyside6_root = None
    fw = []
    if pyside6_root is not None and Path(pyside6_root).is_dir():
        fw = list(Path(pyside6_root).rglob('Qt*.framework'))
    if fw:
        problems.append(f'PySide6 内含 Qt framework（pip wheel 特征）: {[str(p) for p in fw[:2]]}')
    return problems


def _qt_dupes(tocs):
    """同一个 Qt 组件落在多个 dest 的情况（key -> [dest, ...]）。"""
    dests_by_key = {}
    for toc in tocs:
        for entry in toc:
            dest = '/' + str(entry[0]).replace('\\', '/').lstrip('/')
            base = dest.rsplit('/', 1)[-1]
            is_lib = base.startswith('libQt6') and base.endswith('.dylib')
            is_fw = bool(_QT_FW_RE.search(dest))
            if not (is_lib or is_fw):
                continue
            if is_lib:
                key, what = base, dest
            else:
                # framework 内部有很多文件：key 用 framework 名，值归一到 framework 根，
                # 这样"同一 framework 的多个文件"不会被误判成重复，
                # 而"同名 framework 出现在两个目录"会被正确检出。
                root = dest.split('.framework', 1)[0] + '.framework'
                key, what = root.rsplit('/', 1)[-1], root
            dests_by_key.setdefault(key, []).append(what)
    return {k: sorted(set(v)) for k, v in dests_by_key.items() if len(set(v)) > 1}


def _qt_counts(tocs):
    naked, wheel_dir, fw = 0, 0, 0
    for toc in tocs:
        for entry in toc:
            dest = '/' + str(entry[0]).replace('\\', '/').lstrip('/')
            base = dest.rsplit('/', 1)[-1]
            if not (base.startswith('libQt6') and base.endswith('.dylib')):
                continue
            if '/PySide6/Qt/' in dest:
                wheel_dir += 1
            else:
                naked += 1
    return {'naked': naked, 'under_PySide6_Qt': wheel_dir}


_provider_problems = _qt_provider_problems()
_counts = _qt_counts([a.binaries, a.datas])
_dupes = _qt_dupes([a.binaries, a.datas])
print(f'[spec] Qt 计数: {_counts}')
print(f'[spec] PySide6 来源问题: {_provider_problems or "无"}')
if _dupes:
    print(f'[spec] Qt 重复组件: {list(_dupes.items())[:8]}')

_FIX_HINT = (
    '\n'
    '──────────────────────────────────────────────────────────────────────\n'
    'Qt 只能有一个来源，但当前构建检测到问题：\n'
    f'  来源问题 : {_provider_problems}\n'
    f'  重复组件 : {list(_dupes)[:8]}\n'
    '\n'
    'CI（.github/workflows/macos.yml）必须是：\n'
    '  conda install -y -c conda-forge numpy scipy matplotlib vtk pyside6\n'
    '  pip install --no-deps PyCt6                     # 不能让它把 wheel 版 PySide6 拉回来\n'
    '  # 绝不要再 pip install PySide6；注意 pip list 里能看见 conda 装的包，'
    '不能用它判断来源\n'
    '本地构建同理：先把 pip 的 PySide6 / PyQt 卸干净再打包。\n'
    '背景见本文件顶部与 tools/verify_macos_bundle.sh。\n'
    '──────────────────────────────────────────────────────────────────────\n'
)
if _provider_problems:
    raise SystemExit('[spec] FAIL: Qt 来源不唯一 ' + str(_provider_problems) + _FIX_HINT)
if _dupes:
    raise SystemExit('[spec] FAIL: 同一个 Qt 组件被装了两遍 ' + str(list(_dupes)[:8]) + _FIX_HINT)
print('[spec] OK: 只有一套 Qt（conda-forge），无重复组件')


# ════════════════════════════════════════════════════════════════════════════
# 构建期守卫 3/3：依赖「名字」闭合
#
# conda 的 libicu/libbz2/libexpat 用 symlink 提供 soname：
#     libicuuc.78.dylib -> libicuuc.78.3.dylib
# PyInstaller 收集时解析 symlink，包里只剩 libicuuc.78.3.dylib，而
# libQt6Core 的 install name 写的是 @rpath/libicuuc.78.dylib →
#   · CI 上 conda 目录还在，恰好能找到 → 冒烟测试通过（假阳性）
#   · 用户机器上没有 conda → 启动即崩
# 这里把「被引用但没有精确同名文件」的依赖从 conda 前缀补进包（名字保持原样）。
# ════════════════════════════════════════════════════════════════════════════
_SYSTEM_DEP_RE = re.compile(
    r'^lib(c\+\+|c\+\+abi|System|objc|z|iconv|resolv|xml2|sqlite3|cups|edit|'
    r'ncurses|panel|form|bsm|util|compression|apple_nghttp2|heimdal|tidy|dl|m|'
    r'poll|proc|pthread)(\.[0-9.]+)?\.dylib$'
)
_OPTIONAL_DEP_RE = re.compile(
    r'^lib(mimer|iodbc|odbcinst|pq|mysqlclient|mariadb|sybdb|fbclient)[^/]*\.dylib$'
)


def _deps_via_otool(src):
    """用 otool 读一个 Mach-O 的依赖名（macOS 自带）。失败返回 []。"""
    import subprocess
    try:
        out = subprocess.run(['otool', '-L', str(src)], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        return []
    deps = []
    for line in out.splitlines()[1:]:
        dep = line.strip().split(' ')[0].strip()
        if dep:
            deps.append(dep)
    return deps


_SONAME_STAGE = Path('build_sonames')
_SONAME_STAGE.mkdir(exist_ok=True)


def _stage_soname(name, src):
    """把 symlink 目标实体化成**以 soname 命名**的真实文件，返回其路径。

    为什么必须这么做：PyInstaller 收集二进制时会解析 symlink，并按**源文件名**决定
    目标名。直接把 conda 的 symlink（libicuuc.78.dylib -> libicuuc.78.3.dylib）塞进
    TOC，最终包里只会出现 libicuuc.78.3.dylib —— 而 Qt 的 install name 要的是
    @rpath/libicuuc.78.dylib，于是"CI 能跑、用户机器崩"。
    先 hardlink（同盘零拷贝）/copy 成一个真名叫 soname 的实体文件，就能保住名字。
    """
    dst = _SONAME_STAGE / name
    if dst.exists():
        return str(dst)
    real = os.path.realpath(src)
    try:
        os.link(real, dst)          # 同文件系统：硬链接，不额外占空间
    except OSError:
        import shutil
        shutil.copy2(real, dst)
    return str(dst)


def _dep_closure(binaries, lib_dirs, dep_reader=_deps_via_otool, stager=_stage_soname):
    """补入缺失的 soname 名字。返回 (added, still_missing)。

    注意：**必须作为 datas（dest='lib/<name>'）挂进去，不能作为 binaries**。
    PyInstaller 6 对 binaries 按内容去重：conda 的 libicuuc.78.dylib 与已收集的
    libicuuc.78.3.dylib 内容相同 → 我加进去的那份会被静默丢掉（实测：文件数不变）。
    datas 不做内容去重，落到 Contents/Resources/lib/，而 runtime hook 已经把
    Contents/Resources/lib 放进 DYLD_FALLBACK_LIBRARY_PATH，dyld 解析
    @rpath/libicuuc.78.dylib 失败时会去那里找。

    返回的 added 是名字列表；调用方负责把 staged 文件加进 a.datas。
    """
    have = {os.path.basename(str(e[0]).replace('\\', '/')) for e in binaries}
    added, missing = [], []
    for dest, src, _typ in list(binaries):
        if not str(src).endswith(('.dylib', '.so')):
            continue
        for dep in dep_reader(src):
            if not dep.startswith('@rpath/'):
                continue
            name = dep[len('@rpath/'):]
            if '/' in name or name in have:
                continue
            if _SYSTEM_DEP_RE.match(name) or _OPTIONAL_DEP_RE.match(name):
                continue
            placed = False
            for d in lib_dirs:
                cand = os.path.join(str(d), name)
                if os.path.isfile(cand) or os.path.islink(cand):
                    added.append((name, stager(name, cand)))
                    have.add(name)
                    placed = True
                    break
            if not placed:
                missing.append(name)
    return added, sorted(set(missing))


_lib_search = []
for _p in _prefixes():
    _lib_search += [os.path.join(_p, 'lib'), os.path.join(_p, 'lib', 'qt6'), os.path.join(_p, 'lib64')]
try:
    _imported = __import__('PySide6')
    import pathlib as _pl
    _ps_root = _pl.Path(_imported.__path__[0])
    _lib_search += [str(_ps_root / 'Qt' / 'lib'), str(_ps_root / 'Qt' / 'libexec')]
except Exception:
    pass
_lib_search = [d for d in _lib_search if os.path.isdir(d)]

_added, _still_missing = _dep_closure(a.binaries, _lib_search)
if _added:
    already = {os.path.basename(str(e[0]).replace('\\', '/')) for e in a.datas}
    for _name, _path in _added:
        if _name not in already:
            a.datas.append((f'lib/{_name}', _path, 'DATA'))
print(f'[spec] 依赖名闭合: 补入 {len(_added)} 个 soname -> Contents/Resources/lib/ '
      f'{sorted({n for n, _ in _added})[:12]}')
if _still_missing:
    print(f'[spec] 仍未解析的非系统依赖: {_still_missing[:20]}')

# Qt 库是启动必需的：任何非系统依赖仍缺失就失败，别产出"只在 CI 上能跑"的包
_qt_missing = []
_have_after = {os.path.basename(str(e[0]).replace('\\', '/'))
               for e in list(a.binaries) + list(a.datas)}
for _dest, _src, _t in a.binaries:
    _b = os.path.basename(str(_dest).replace('\\', '/'))
    if not (_b.startswith('libQt6') and _b.endswith('.dylib')):
        continue
    for _dep in _deps_via_otool(_src):
        if not _dep.startswith('@rpath/'):
            continue
        _n = _dep[len('@rpath/'):]
        if '/' in _n or _n in _have_after:
            continue
        if _SYSTEM_DEP_RE.match(_n) or _OPTIONAL_DEP_RE.match(_n):
            continue
        _qt_missing.append(f'{_b} -> {_n}')
if _qt_missing:
    raise SystemExit(
        '[spec] FAIL: Qt 库仍有未打包的非系统依赖 ' + str(sorted(set(_qt_missing))[:10])
        + '\n  说明依赖名闭合没生效（conda 前缀里找不到这些文件？）。'
        + '\n  这类包在 CI 上能启动、到用户机器上必崩，因此直接构建失败。\n')
print('[spec] OK: Qt 库依赖名闭合')

# 去重兜底（万一 hook 与 collect_all 撞了同一 dest，保留第一个）
try:
    from PyInstaller.building.datastruct import TOC

    def _dedupe(toc):
        seen, out = set(), []
        for entry in toc:
            if entry[0] in seen:
                continue
            seen.add(entry[0])
            out.append(entry)
        return TOC(out)

    a.binaries = _dedupe(a.binaries)
    a.datas = _dedupe(a.datas)
except Exception as _e:  # pragma: no cover
    print(f'[spec] dedupe skipped: {_e}')

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SSD_VR_Fusion_Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # macOS 上 UPX 无意义且可能破坏签名
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SSD_VR_Fusion_Viewer',
)

app = BUNDLE(
    coll,
    name='SSD_VR_Fusion_Viewer.app',
    icon='logo.icns',
    bundle_identifier='com.ssdvr.fusion-viewer',
    info_plist={
        'NSHighResolutionCapable': True,
        # 不放 LSEnvironment：LaunchServices 不会展开 @executable_path。
        # 插件路径由 PySide6 自带的 qt.conf + ssd_vr_viewer._setup_macos_qt_paths() 兜底。
    },
)
