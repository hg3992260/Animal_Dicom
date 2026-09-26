# -*- mode: python ; coding: utf-8 -*-
"""Animal Dicom v1.0.0 — Windows 打包 spec（PyInstaller 6.x, onedir）。

两种形态（同一个 spec，用环境变量切换）：

  # 基础版（默认）：424 MB zip / 1.16 GB 解压
  pyinstaller --clean --noconfirm animal_dicom.spec

  # 完整版：含 GPU 加速（cupy + CUDA 运行时）与 3D SAM（torch + medim + torchio + monai）
  #   预计 ~8 GB 解压；zip 需拆成两个 <2 GB 的包（GitHub 单资产上限）
  set ANIMAL_DICOM_FULL=1 && pyinstaller --clean --noconfirm animal_dicom.spec

完整版构建后**必须**把 frame/SAM-Med3D-main 拷到 exe 同级目录
（segmentation/config.py 在冻结模式下按 sys.executable 目录找 SAM_MED3D_DIR）：
  xcopy /E /I /Y frame\\SAM-Med3D-main dist_full\\Animal_Dicom\\frame\\SAM-Med3D-main
权重 sam_med3d_turbo.pth（384 MB）**不随包分发**（第三方权重许可 + 体积）：
首次使用 3D SAM 时由 sam_adapter 自动从 HuggingFace 下载，或手动放入
frame/SAM-Med3D-main/ckpt/。

与 build.yaml 的差异（为什么单独写 spec）：
  1. **明确排除** 4.5 GB 的 torch 及 totalsegmentator/nnunetv2 等（基础版）；
     因此不能对 segmentation 用 collect_submodules（会把 import torch 的检测器全拉进来）。
  2. 基础版排除 cupy/cupyx/nvidia（约 1.9 GB CUDA 运行时），完整版保留。
  3. 显式带上 license_guard / 皮肤 / 病例模板等运行期资源（缺失会拒启或丢皮肤）。
  4. **PyCt6 必须连数据文件收集**（widgets/themes/*.json 等），否则启动即崩。
"""
import os

from PyInstaller.utils.hooks import (collect_all, collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

FULL = os.environ.get('ANIMAL_DICOM_FULL') == '1'
DIST_NAME = 'Animal_Dicom_Full' if FULL else 'Animal_Dicom'
print('[spec] 形态: %s' % ('完整版（含 GPU + 3D SAM）' if FULL else '基础版（无 GPU/3D SAM）'))

PROJ = os.path.abspath(os.getcwd())

# ---------------------------------------------------------------- datas
datas = [
    ('retro.qss', '.'),            # 皮肤（优先级最高）
    ('light.qss', '.'),
    ('dark.qss', '.'),
    ('warm_orange.json', '.'),     # PyCt6 暖橙主题
    ('case_template.json', '.'),   # 病例档案模板
    ('presets.xml', '.'),          # Slicer 风格 VR 预设
    ('render_templates.json', '.'),
    ('scientific.json', '.'),
    ('logo.ico', '.'),
    ('logo.png', '.'),
    ('segmentation', 'segmentation'),
    ('mcp_ssd_vr', 'mcp_ssd_vr'),
]

# ---------------------------------------------------------------- binaries
binaries = []
# ErCore.dll（CUDA 路径追踪引擎，CR / Exposure Render 需要）：
# 优先用仓库内 vendored 的 packaging/ErCore.dll —— 因为 exposure-render-master/ 在
# .gitignore 里（体积原因不入库），CI 拿不到它，冻结版就会缺 CR 支持。
_er = os.path.join('packaging', 'ErCore.dll')
if not os.path.exists(_er):
    _er = os.path.join('exposure-render-master', 'exposure-render-master', 'Source', 'build',
                       'Release', 'ErCore.dll')
if os.path.exists(_er):
    binaries.append((_er, '.'))
else:
    print('[spec] WARNING: 未找到 ErCore.dll，CR 路径追踪模式将不可用')

# ---------------------------------------------------------------- 依赖整体收集
# 注意 PyCt6：它是纯 Python 包，但**带数据文件**（widgets/themes/*.json、
# widgets/images/*.png、windows/images/logo.png）。只写 hiddenimports 会把 .py 收进
# PYZ，而主题 JSON 不会随之打包 → set_color_theme() 启动即 FileNotFoundError。
# 因此必须连数据一起收集。
_PKGS = ['PySide6', 'PyCt6', 'vtkmodules', 'SimpleITK', 'skimage', 'scipy']
if FULL:
    # GPU 加速：cupy/cupyx 的 collect_all 正常
    _PKGS += ['cupy', 'cupyx']
for pkg in _PKGS:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports_extra = h
    except Exception as exc:  # noqa: BLE001
        print('[spec] collect_all(%s) 失败: %s' % (pkg, exc))
        hiddenimports_extra = []
    if pkg == 'PySide6':
        _ps_hidden = hiddenimports_extra
    else:
        globals().setdefault('_extra_hidden', []).extend(hiddenimports_extra)

# ---------------------------------------------------------------- hidden imports
hiddenimports = list(globals().get('_ps_hidden', [])) + list(globals().get('_extra_hidden', []))
hiddenimports += [
    # GUI / 主题
    'PyCt6',
    'PyCt6.widgets.c_button', 'PyCt6.widgets.c_label', 'PyCt6.widgets.c_line_edit',
    'PyCt6.widgets.c_combo_box', 'PyCt6.widgets.c_slider', 'PyCt6.widgets.c_frame',
    'PyCt6.widgets.c_text_edit', 'PyCt6.windows.c_main_window',
    'PyCt6.appearance.theme_manager', 'PyCt6.appearance.mode_manager',
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets', 'PySide6.QtSvg', 'PySide6.QtNetwork', 'PySide6.QtPrintSupport',
    # VTK
    'vtk', 'vtkmodules', 'vtkmodules.qt.QVTKRenderWindowInteractor',
    'vtkmodules.util.numpy_support', 'vtkRenderingOpenGL2', 'vtkRenderingVolumeOpenGL2',
    'vtkRenderingUI', 'vtkInteractionStyle', 'vtkFiltersSources', 'vtkIOImage',
    # 科学计算 / 影像
    'numpy', 'scipy.ndimage', 'skimage.restoration', 'skimage.exposure', 'skimage.filters',
    'nibabel', 'nibabel.nifti1', 'openpyxl', 'matplotlib', 'matplotlib.backends.backend_agg',
    'PIL', 'PIL.Image',
    # 项目内模块（注意：**不含** import torch 的检测器）
    'license_guard', 'deepseek_client', 'pacs_client',
    'pynetdicom', 'pynetdicom.sop_class', 'pydicom',
    'segmentation.roi_types', 'segmentation.roi_label_map', 'segmentation.postprocessor',
    'segmentation.preprocessor', 'segmentation.config',
    'mcp_ssd_vr', 'mcp_ssd_vr.config', 'mcp_ssd_vr.context', 'mcp_ssd_vr.state',
    'mcp_ssd_vr.recorder', 'mcp_ssd_vr.bridge_registry', 'mcp_ssd_vr.bridge_client',
    'mcp_ssd_vr.gui_bridge', 'mcp_ssd_vr.dicom',
    'mcp_ssd_vr.tools', 'mcp_ssd_vr.tools._util', 'mcp_ssd_vr.tools.capture',
    'mcp_ssd_vr.tools.cases', 'mcp_ssd_vr.tools.control', 'mcp_ssd_vr.tools.dicom_scan',
    'mcp_ssd_vr.tools.inspect', 'mcp_ssd_vr.tools.lifecycle', 'mcp_ssd_vr.tools.recording',
]

if FULL:
    hiddenimports += [
        # 3D SAM 链路：viewer → segmentation.sam_pipeline → sam_adapter(medim/torchio/torch)
        'torch', 'torch.nn', 'torch.utils', 'torch.serialization', 'torch.cuda',
        'torchio', 'medim', 'monai',
        'cupy', 'cupyx',
        'segmentation.sam_pipeline', 'segmentation.sam_adapter',
        'segment_anything', 'segment_anything.modeling',
    ]
    # 注：medim/torchio/monai **不能**用 collect_all/collect_submodules ——
    # 它们 import torch，而 PyInstaller 的"收集子模块"是**独立子进程**，会因
    # torch 的 libiomp5 与 VTK/SimpleITK 的 OpenMP 重复初始化而崩溃（exit code 3）。
    # 只收数据文件，模块交给静态分析 + 上面的 hiddenimports。
    for _p in ('medim', 'torchio', 'monai'):
        try:
            datas += collect_data_files(_p)
        except Exception as _e:  # noqa: BLE001
            print('[spec] collect_data_files(%s) 失败: %s' % (_p, _e))
    # cupy 不 import torch → 可以安全收集子模块。
    # 必须收 cupy_backends.*：cupy 的 CUDA 后端是**动态 softlink 模块**
    # （cupy_backends.cuda._softlink），漏了就会 ModuleNotFoundError →
    # import cupy 失败 → Frangi 静默回退 CPU。
    for _p in ('cupy', 'cupyx', 'cupy_backends'):
        try:
            hiddenimports += collect_submodules(_p)
        except Exception as _e:  # noqa: BLE001
            print('[spec] collect_submodules(%s) 失败: %s' % (_p, _e))
    # torch 的原生库必须**显式**收集：PyInstaller 自带的 torch hook 在 CI 上
    # 没把 torch/lib 里的深层 CUDA 库（torch_cuda.dll 913MB / cudnn / cublasLt …）
    # 全带进包，结果是"装的是 cu124 但产物没有 GPU 支持"（本地与 CI 表现不一致）。
    for _p in ('torch', 'cupy'):
        try:
            _libs = collect_dynamic_libs(_p)
            binaries += _libs
            print('[spec] collect_dynamic_libs(%s): %d 项' % (_p, len(_libs)))
        except Exception as _e:  # noqa: BLE001
            print('[spec] collect_dynamic_libs(%s) 失败: %s' % (_p, _e))
    hiddenimports += [
        'cupy_backends.cuda._softlink', 'cupy_backends.cuda.libs',
        'fastrlock', 'fastrlock.rlock',
        # monai 的 import 链依赖 sympy（mpmath）；monai 是**延迟** import，
        # PyInstaller 静态分析看不到 → 必须显式写进来，否则 monai ImportError
        'sympy', 'mpmath',
    ]

# ---------------------------------------------------------------- excludes
excludes = [
    # 自动 ROI 已移除 → 这些检测器依赖不进包（无论基础版/完整版）
    'totalsegmentator', 'nnunetv2', 'batchgenerators', 'medpy', 'dipy',
    # 未被本程序使用的重依赖
    'pyarrow', 'av', 'pandas', 'sqlalchemy', 'fsspec', 's3fs', 'boto3',
    # 本环境装的其它 Qt 绑定：PyInstaller 不允许与 PySide6 混用（会直接 Aborting）
    'PyQt5', 'PyQt5.sip', 'PyQt6', 'PyQt6.sip', 'PySide2',
    'matplotlib.backends.backend_qt', 'matplotlib.backends.backend_qt5',
    'matplotlib.backends.backend_qtagg', 'matplotlib.backends.backend_qt5agg',
    'matplotlib.backends.backend_qt6agg', 'matplotlib.backends.backend_qtquick',
    'qtpy', 'QtPy',
    'tests', 'pytest', 'IPython', 'notebook', 'jupyter', 'jupyter_core',
    'tkinter', 'cv2', 'onnx', 'tensorboard', 'tensorflow',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQuick',
    'PySide6.QtQml', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia',
]

if not FULL:
    # 基础版：剔除 GPU 加速（cupy + CUDA 运行时 ~1.9 GB）与 3D SAM（torch ~4.5 GB）
    excludes += [
        'torch', 'torchvision', 'torchio', 'medim', 'monai', 'sympy',
        'cupy', 'cupyx', 'cupy_backends', 'nvidia',
    ]
# 完整版保留 sympy：monai 的 import 链依赖它（缺了 monai 直接 ImportError）

# collect_all('PySide6') 会把 QtWebEngine 的二进制/调试资源一起塞进来（~270 MB），
# 本程序完全用不到 → 在 Analysis 之后按名字过滤掉。
_DROP_BIN = ('qtwebengine', 'qt6webengine', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt63d',
             'qt6multimedia', 'qt6charts', 'qt6datavisualization', 'qt6virtualkeyboard',
             'qt6sensors', 'qt6designer', 'qt6test', 'qt6sql')


a = Analysis(
    ['ssd_vr_viewer.py'],
    pathex=[PROJ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_vcruntime.py'],   # vcruntime/msvcp 兜底
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

# 过滤掉未使用的 Qt WebEngine/Quick/Qml 二进制与资源（见 _DROP_BIN 说明）
_before = len(a.binaries) + len(a.datas)
a.binaries = [b for b in a.binaries
              if not any(k in str(b[0]).lower() for k in _DROP_BIN)]
a.datas = [d for d in a.datas
           if not any(k in str(d[0]).lower() for k in _DROP_BIN)]
print('[spec] 过滤未用 Qt 组件: %d -> %d 项' % (_before, len(a.binaries) + len(a.datas)))

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Animal_Dicom',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 窗口程序；MCP 桥端口通过 bridge.json 发布，不依赖 stdout
    disable_windowed_traceback=False,
    icon='logo.ico',
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=False,
    name=DIST_NAME,
)
print('[spec] 产物目录: %s' % DIST_NAME)
