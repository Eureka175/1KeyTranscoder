# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the portable 1KeyTranscoder test bundle.

Builds the two executables that let a machine with NO Python run the real
production x265 workflow, driven by dropping media into a folder:

    bin/1kt.exe         the production transcoder (1kt.py), full behaviour
    bin/1kt-watch.exe   the watchfolder driver (polls input/, runs 1kt)

Sibling executables are built by the other specs:
    x265_calibration_collector.spec  -> x265-calibration-collector.exe
    x265_all_exes.spec               -> x265-analyze/verify/deploy.exe

Layout facts this spec relies on
--------------------------------
* ``SPECPATH`` is injected by PyInstaller and is this file's directory, so
  every path below is absolute and the spec is cwd-independent.
* The entry scripts are launchers in this directory (frozen_1kt.py,
  frozen_watch.py), not the project's top-level scripts. Freezing a script
  directly runs it as ``__main__`` with no package context; the launcher
  imports it as a module instead, which keeps ``__file__`` and the sibling
  package imports (``core``, ``encoders``, ``preservation``) predictable.
* The repo root is on ``pathex`` so those packages are collected.
* ``VERSION``, ``x265.json`` and ``x265_scaling.json`` are bundled as data:
  ``1kt.py`` resolves its configs from ``Path(__file__).parent`` (which is
  ``_MEIPASS`` when frozen) and ``core.version`` probes ``_MEIPASS`` for
  VERSION. Without these the frozen transcoder dies at startup while
  argparse evaluates ``--version``.
* ``numpy`` and ``scipy`` are REQUIRED (core/sync_estimate.py,
  core/channel_sync.py, core/mp4_channel_sync.py). They are not excluded and
  not trimmed: correctness over size.
* Hardware-decode/benchmark tool drops (ffmpeg, GPAC, NVEncC, QSVEncC, avhw)
  are NOT bundled: they are large third-party binaries that live in the
  package's tools/ folder so they can be swapped without rebuilding this exe.
"""

import os

_SPEC_DIR = os.path.abspath(SPECPATH) if "SPECPATH" in globals() else \
    os.path.dirname(os.path.abspath(SPEC))
TOOLS_DIR = os.path.abspath(os.path.join(_SPEC_DIR, ".."))       # tools/x265_calibration
REPO_ROOT = os.path.abspath(os.path.join(_SPEC_DIR, "..", "..", ".."))

_ENTRY_1KT = os.path.join(_SPEC_DIR, "frozen_1kt.py")
_ENTRY_WATCH = os.path.join(_SPEC_DIR, "frozen_watch.py")

_REQUIRED_INPUTS = {
    "1kt.py": os.path.join(REPO_ROOT, "1kt.py"),
    "VERSION": os.path.join(REPO_ROOT, "VERSION"),
    "x265.json": os.path.join(REPO_ROOT, "x265.json"),
    "x265_scaling.json": os.path.join(REPO_ROOT, "x265_scaling.json"),
    "frozen_1kt.py": _ENTRY_1KT,
    "frozen_watch.py": _ENTRY_WATCH,
    "core/": os.path.join(REPO_ROOT, "core"),
    "encoders/": os.path.join(REPO_ROOT, "encoders"),
    "preservation/": os.path.join(REPO_ROOT, "preservation"),
}
for _label, _path in _REQUIRED_INPUTS.items():
    if not os.path.exists(_path):
        raise SystemExit("spec error: %s not found at %s" % (_label, _path))

# Config + version metadata -> bundle root, where the frozen code looks.
datas = [
    (_REQUIRED_INPUTS["VERSION"], "."),
    (_REQUIRED_INPUTS["x265.json"], "."),
    (_REQUIRED_INPUTS["x265_scaling.json"], "."),
]
# The other encoder profiles are bundled too: 1kt.py can be told
# --encoder svtav1 / nvenc etc. and must find its profile file rather than
# failing with a confusing "not found beside the script". They are a few KB.
for _extra in ("svtav1.json", "svtav1_scaling.json", "nvenc.json",
               "nvenc_av1.json", "qsv.json", "qsv_av1.json", "qsv_aligned.json",
               "vce.json"):
    _p = os.path.join(REPO_ROOT, _extra)
    if os.path.isfile(_p):
        datas.append((_p, "."))

# Modules imported lazily inside functions, so the static graph can miss them.
# "1kt" is reached through importlib (its name starts with a digit, so no
# import statement can name it) and therefore MUST be listed here.
hiddenimports = [
    "1kt",
    "core.dashboard_ui",
    "core.channel_sync",
    "core.mp4_channel_sync",
    "core.sync_estimate",
    "core.sync_fix",
    "core.versions",
    "preservation.quality",
    "preservation.audio_sync",
    "preservation.dji",
    "preservation.gyroflow",
    "preservation.pipeline",
    "numpy",
    "scipy",
    "scipy.fft",
    "scipy.signal",
    "scipy.interpolate",
]

# Nothing that the transcoder actually needs is excluded. Only build-machine
# tooling that cannot be reachable from a clean machine is filtered out.
excludes = [
    "mesonbuild",
    "mesonpy",
    "ninja",
    "pip",
    "setuptools",
    "pkg_resources",
    "_distutils_hack",
    "cffi",
    "_cffi_backend",
    "pycparser",
    "PyInstaller",
]

_COMMON = dict(
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    module_collection_mode={},
)

_EXE_COMMON = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

for _name, _entry in (("1kt", _ENTRY_1KT), ("1kt-watch", _ENTRY_WATCH)):
    _a = Analysis(  # noqa: F821
        [_entry],
        pathex=[REPO_ROOT, TOOLS_DIR],
        **_COMMON,
    )
    _pyz = PYZ(_a.pure)  # noqa: F821
    EXE(  # noqa: F821
        _pyz,
        _a.scripts,
        _a.binaries,
        _a.datas,
        [],
        name=_name,
        **_EXE_COMMON,
    )
