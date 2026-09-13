# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the FULL portable toolset (one folder, four exes).

Builds every executable the package ships, so the soak host never needs
Python for anything - not even for the analysis step:

    x265-calibration-collector.exe   the collector itself (headless watcher)
    x265-analyze.exe                 offline dataset analyzer
    x265-verify.exe                  package / frozen-exe verifier
    x265-deploy.exe                  deployment helper (task command, checks)

Run through release/build_standalone.py, which builds the COLLECTOR from
x265_calibration_collector.spec and this spec for the three tool exes, or
directly:

    pyinstaller tools/x265_calibration/release/x265_all_exes.spec \
        --noconfirm --distpath dist/x265_calibration \
        --workpath build/x265_calibration

Layout facts this spec relies on
--------------------------------
* ``SPECPATH`` is injected by PyInstaller and is this file's directory, so
  every path below is absolute and the spec is cwd-independent.
* Each exe is built from a LAUNCHER module in this directory
  (``frozen_*.py``) rather than from the tool script itself. Freezing a
  tool script directly would execute it as ``__main__`` and resolve
  ``__file__`` outside the bundle; the launcher imports the real tool as a
  module so ``__file__`` points into ``_MEIPASS`` and sibling lookups
  (schema.json, package.json, the staged release folder) behave exactly as
  they do in a source checkout.
* The tool sources live one directory up (``tools/x265_calibration/*.py``),
  which is why that directory is on ``pathex``. Built as modules, they are
  importable by their plain names - no script-directory trickery needed.
* ``schema.json`` and ``package.json`` are bundled as data so the tools work
  on a host that has no copy of the repository.
* Binary size is explicitly not a constraint: nothing is pruned aggressively
  and UPX is disabled (UPX also triggers antivirus false positives).
"""

import os

_SPEC_DIR = os.path.abspath(SPECPATH) if "SPECPATH" in globals() else \
    os.path.dirname(os.path.abspath(SPEC))
TOOLS_DIR = os.path.abspath(os.path.join(_SPEC_DIR, ".."))

# ---------------------------------------------------------------------------
# fail fast if the inputs moved
# ---------------------------------------------------------------------------
_INPUTS = {
    "analyze.py": os.path.join(TOOLS_DIR, "analyze.py"),
    "schema.json": os.path.join(TOOLS_DIR, "schema.json"),
    "frozen_analyze.py": os.path.join(_SPEC_DIR, "frozen_analyze.py"),
    "frozen_verify.py": os.path.join(_SPEC_DIR, "frozen_verify.py"),
    "frozen_deploy.py": os.path.join(_SPEC_DIR, "frozen_deploy.py"),
    "verify_frozen_exe.py": os.path.join(_SPEC_DIR, "verify_frozen_exe.py"),
    "deploy_helpers.py": os.path.join(_SPEC_DIR, "deploy_helpers.py"),
    "make_zip.py": os.path.join(_SPEC_DIR, "make_zip.py"),
}
for _label, _path in _INPUTS.items():
    if not os.path.isfile(_path):
        raise SystemExit("spec error: %s not found at %s" % (_label, _path))

# schema.json -> bundle root so <pkg>/tools/ stays self-describing on a host
# with no copy of the repository; VERSION likewise.
datas = [(_INPUTS["schema.json"], ".")]
_version_file = os.path.abspath(
    os.path.join(_SPEC_DIR, "..", "..", "..", "VERSION")
)
if os.path.isfile(_version_file):
    datas.append((_version_file, "."))

# Names collected as modules; naming them converts a future packaging
# accident into a build-time error instead of a startup ImportError on the
# soaking host. Size is not a constraint.
hiddenimports = [
    "analyze",
    "verify_frozen_exe",
    "deploy_helpers",
    "make_zip",
    "tomllib",
    "datetime",
    "csv",
    "statistics",
]

# This interpreter has numpy/scipy/meson/pip installed; none are reachable
# from these tools, so excluding them is belt-and-braces, not behaviour.
#
# cffi is the one that actually mattered: cffi._shimmed_dist_utils imports
# setuptools (optionally), so PyInstaller swept setuptools' vendored
# importlib_metadata .dist-info files into the tool bundles. Nothing here
# imports cffi either (PyInstaller's own runtime uses ctypes, not cffi), so
# excluding it keeps the bundle stdlib-only - which is exactly what
# x265-verify.exe asserts when it refuses "unexpected third-party entries".
excludes = [
    "numpy",
    "scipy",
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

_ENTRY_POINTS = (
    ("x265-analyze", "frozen_analyze.py"),
    ("x265-verify", "frozen_verify.py"),
    ("x265-deploy", "frozen_deploy.py"),
)

for _name, _entry in _ENTRY_POINTS:
    _entry_path = os.path.join(_SPEC_DIR, _entry)
    _a = Analysis(  # noqa: F821
        [_entry_path],
        pathex=[TOOLS_DIR, _SPEC_DIR],
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
