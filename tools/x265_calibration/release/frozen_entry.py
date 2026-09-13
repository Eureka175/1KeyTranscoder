# -*- coding: utf-8 -*-
"""Frozen entry point for the standalone x265 calibration collector.

WHY THIS FILE EXISTS
--------------------
``collector/collector_cli.py`` uses package-relative imports
(``from . import collector_core``).  PyInstaller executes the *entry script* as
the module ``__main__`` with no parent package, so freezing ``collector_cli.py``
directly produces a binary that dies at startup with::

    ImportError: attempted relative import with no known parent package

Verified against PyInstaller 6.22.2 on this repo: adding the package parent to
the spec's ``pathex`` does NOT fix it, because ``pathex`` only affects
*analysis* (which modules get collected), not the runtime ``__package__`` of
the entry module.

WHAT THIS DOES INSTEAD
----------------------
Imports the CLI module under its real fully qualified name
(``collector.collector_cli``) and calls its ``main()``.  Under that name the
package context exists, so ``from . import collector_core`` resolves, and
``collector_core``'s ``__file__``/``sys._MEIPASS`` schema search behaves
exactly as it does for ``python -m collector.collector_cli``.

Notes
-----
* ``collector_cli`` has an ``if __name__ == "__main__"`` guard, so importing it
  is side-effect free: it only defines ``main()``.
* Deliberately NOT using ``runpy.run_module``: it needs an import-system
  ``find_spec`` for the frozen module during the bootstrap phase, which is a
  needless dependency when a plain import is enough.
* Standard library only.
"""

from __future__ import annotations

import sys


def main() -> int:
    from collector.collector_cli import main as collector_main

    result = collector_main()
    if result is None:
        return 0
    if isinstance(result, bool):        # bool is an int subclass
        return 0 if result else 1
    return int(result)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:            # never a bare traceback in the field
        sys.stderr.write(
            "[FATAL] frozen launcher: %s: %s\n" % (type(exc).__name__, exc)
        )
        sys.exit(1)
