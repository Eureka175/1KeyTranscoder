# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone x265 calibration collector.

Target: ONE self-contained single-file console executable, offline, with no
Python/pip/site-packages required on the data-collector host.

    x265-calibration-collector.exe   (console, onefile)

Layout facts this spec relies on
--------------------------------
* ``SPECPATH`` is injected by PyInstaller and is the directory containing this
  spec file, so every path below is absolute and this spec works from any cwd.
* The entry script is the launcher ``release/frozen_entry.py``, NOT
  ``collector/collector_cli.py``.  ``collector_cli`` uses package-relative
  imports (``from . import collector_core``), and PyInstaller executes an entry
  script as ``__main__`` with no parent package, so freezing it directly
  produces a binary that dies at startup with::

      ImportError: attempted relative import with no known parent package

  This was measured on this repo with PyInstaller 6.22.2; putting the package
  parent on ``pathex`` does not help, because ``pathex`` only influences
  *analysis*, not the runtime ``__package__`` of the entry module.  The
  launcher instead does ``from collector.collector_cli import main`` and calls
  it, which is why ``collector`` / ``collector.collector_cli`` /
  ``collector.collector_core`` appear in ``hiddenimports`` below.
* ``collector_core.load_schema()`` searches, in order: next to the module,
  one directory up, ``sys._MEIPASS/schema.json``, ``cwd``, the directory of
  ``sys.executable``, and the directory of ``sys.argv[0]``.  In a onefile
  build ``sys._MEIPASS`` is the only one of those that exists on a clean
  target host, so ``schema.json`` MUST be bundled as a data file and MUST
  land at the bundle root as exactly ``schema.json``.
* Binary size is not a constraint: nothing is excluded aggressively and UPX is
  disabled (UPX also triggers antivirus false positives on collected hosts).
* ``--workpath`` / ``--distpath`` are CLI options (they populate CONF before
  this spec runs), so the build script passes them explicitly instead of
  relying on a ``workpath`` variable here.
"""

import os

# --------------------------------------------------------------------------
# paths (all derived from the spec location -> cwd independent)
# --------------------------------------------------------------------------
_SPEC_DIR = os.path.abspath(SPECPATH) if "SPECPATH" in globals() else \
    os.path.dirname(os.path.abspath(SPEC))

PROJECT_ROOT = os.path.abspath(os.path.join(_SPEC_DIR, ".."))          # tools/x265_calibration

COLLECTOR_PKG = os.path.join(PROJECT_ROOT, "collector")
COLLECTOR_PARENT = PROJECT_ROOT                     # -> "import collector" works
ENTRY_SCRIPT = os.path.join(_SPEC_DIR, "frozen_entry.py")
SCHEMA_FILE = os.path.join(PROJECT_ROOT, "schema.json")

APP_NAME = "x265-calibration-collector"

# --------------------------------------------------------------------------
# fail fast and loudly if the inputs moved (better than a cryptic build error)
# --------------------------------------------------------------------------
for _label, _path in (
    ("frozen entry launcher", ENTRY_SCRIPT),
    ("collector package __init__", os.path.join(COLLECTOR_PKG, "__init__.py")),
    ("collector_core.py", os.path.join(COLLECTOR_PKG, "collector_core.py")),
    ("collector_cli.py", os.path.join(COLLECTOR_PKG, "collector_cli.py")),
    ("schema.json", SCHEMA_FILE),
):
    if not os.path.isfile(_path):
        raise SystemExit("spec error: %s not found at %s" % (_label, _path))

# --------------------------------------------------------------------------
# datas: schema.json -> bundle root
# --------------------------------------------------------------------------
# dest_dir MUST be "." (PyInstaller 6.x rejects an empty string with
# "Invalid (SRC, DEST_DIR) tuple ... Empty DEST_DIR is not allowed").
# "." == the application's top-level directory, i.e. exactly
# sys._MEIPASS/schema.json in a onefile build.  That is the copy
# load_schema() finds on a clean target host.  Do NOT also ship a second copy
# in a subdirectory: two copies can silently diverge.
datas = [
    (SCHEMA_FILE, "."),
]

# --------------------------------------------------------------------------
# hiddenimports
# --------------------------------------------------------------------------
# frozen_entry.py does "from collector.collector_cli import main", which the
# module graph can follow directly through pathex, and collector_cli's
# "from . import collector_core" is likewise resolvable because the package is
# now imported under its full name.  These names are therefore redundant-but-
# harmless: naming them converts any future packaging accident (a renamed
# module, a hook that skips a dynamically-touched import) from a startup
# ImportError on the collector's host into a build-time error here.  Size is
# explicitly not a constraint.
#   tomllib  - stdlib on 3.11, imported lazily inside load_config()
#   datetime - collector_core does "from datetime import datetime, timezone"
hiddenimports = [
    "collector",
    "collector.collector_cli",
    "collector.collector_core",
    "tomllib",
    "datetime",
]

# --------------------------------------------------------------------------
# nothing is excluded.  A *conservative* allow-list of third-party packages is
# excluded anyway: this interpreter has numpy/scipy/meson/pip installed, and
# excluding them can only shrink the binary while making the build more
# reproducible (they are not reachable from the collector's imports, so this
# is belt-and-braces, not a behavioural change).
# --------------------------------------------------------------------------
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
]

a = Analysis(  # noqa: F821  (injected by PyInstaller)
    [ENTRY_SCRIPT],
    pathex=[COLLECTOR_PARENT, COLLECTOR_PKG],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,          # keep the normal PYZ: faster start, fewer files
    module_collection_mode={},  # default collection; nothing forced to source
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # never UPX: AV false positives, no size pressure
    upx_exclude=[],
    runtime_tmpdir=None,      # extract to %TEMP%\_MEIxxxxxx (default).  A
                              # non-ASCII %TEMP% is fine: verified working on
                              # this host, where %TEMP% is
                              # C:\Users\吴汶睿\AppData\Local\Temp.  Set an
                              # ASCII path here only if a collector host has a
                              # hardened/blocked %TEMP%.
    console=True,             # headless console application, no window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=os.path.join(_SPEC_DIR, "collector.ico"),  # no icon is shipped
)
