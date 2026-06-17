# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件

使用方法:
    pyinstaller tools/build/build.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

spec_dir = Path(SPECPATH)
project_root = spec_dir.parent.parent


def _optional_tree(relative_path: str, target_path: str) -> list[tuple[str, str]]:
    """按目录树打包本地资源，缺失时静默跳过。"""
    src = project_root / relative_path
    if src.exists():
        return [(str(src), target_path)]
    return []


def _safe_collect_data_files(package: str, includes: list[str] | None = None) -> list[tuple[str, str]]:
    """收集第三方包数据文件，避免包缺失时直接让 spec 失败。"""
    try:
        return collect_data_files(package, includes=includes or ["*"])
    except Exception:
        return []


def _exclude_binary(entry) -> bool:
    dest = str(entry[0]).replace("\\", "/").lower()
    blocked = {
        "cv2/opencv_videoio_ffmpeg4130_64.dll",
        "pyqt6/qt6/plugins/generic/qtuiotouchplugin.dll",
        "pyqt6/qt6/plugins/platforms/qminimal.dll",
        "pyqt6/qt6/plugins/platforms/qoffscreen.dll",
        "pyqt6/qt6/plugins/styles/qmodernwindowsstyle.dll",
        "pyqt6/qt6/plugins/imageformats/qtga.dll",
        "pyqt6/qt6/plugins/imageformats/qtiff.dll",
        "pyqt6/qt6/plugins/imageformats/qpdf.dll",
        "pyqt6/qt6/plugins/imageformats/qicns.dll",
        "pyqt6/qt6/plugins/imageformats/qwbmp.dll",
        "pyqt6/qt6/plugins/imageformats/qwebp.dll",
        "pyqt6/qt6/plugins/imageformats/qgif.dll",
    }
    return dest in blocked


u2_datas = _safe_collect_data_files(
    "uiautomator2",
    includes=[
        "assets/app-uiautomator.apk",
        "assets/u2.jar",
        "assets/version.json",
    ],
)
gui_datas = _optional_tree("src/gui/assets", "src/gui/assets")

a = Analysis(
    [str(project_root / "src" / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        *u2_datas,
        *gui_datas,
    ],
    hiddenimports=[
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "uiautomator2",
        "apscheduler",
        "apscheduler.schedulers.background",
        "apscheduler.triggers.cron",
        "apscheduler.events",
        "chinese_calendar",
        "loguru",
        "pydantic",
        "PIL",
        "PIL.Image",
        "src.utils.notifier",
        "requests",
        "adbutils",
    ],
    hookspath=[str(project_root / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "pytest",
        "jedi",
        "parso",
        "nbformat",
        "jsonschema",
        "sqlalchemy",
        "bcrypt",
        "pygments",
        "rich",
        "wcwidth",
        "setuptools",
        "pkg_resources",
        "unittest",
        "doctest",
        "pdb",
        "xmlrpc",
        "torch",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "tensorboard",
        "keras",
        "pyarrow",
        "fsspec",
        "lz4",
        "opentelemetry",
        "grpc",
        "orjson",
        "jinja2",
        "sympy",
        "bokeh",
        "plotly",
        "sklearn",
        "scikit-learn",
        "scikit-image",
        "statsmodels",
        "distributed",
        "dask",
        "uiautomator2.image",
        "uiautomator2.screenrecord",
        "PIL.ImageQt",
        "PIL.ImageShow",
        "PIL.ImageTk",
        "PIL.PdfImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.AvifImagePlugin",
        "PIL.BufrStubImagePlugin",
        "PIL.GribStubImagePlugin",
        "PIL.Hdf5StubImagePlugin",
        "PIL._imagingtk",
        "PIL._webp",
        "PIL._avif",
        "mypy",
        "mypy_extensions",
        "pydantic.mypy",
        "pydantic.v1.mypy",
    ],
    optimize=2,
    noarchive=False,
)

a.binaries = [entry for entry in a.binaries if not _exclude_binary(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='tongkatong_v2.2.25',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "src" / "gui" / "assets" / "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='tongkatong_v2.2.25',
)
