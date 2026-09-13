"""Frozen entry point: the 1KeyTranscoder production transcoder (1kt).

WHY THIS FILE EXISTS
--------------------
``1kt.py`` is the project's top-level script. Freezing it directly would run
it as ``__main__`` with no package context, and its sibling directories
(``core/``, ``encoders/``, ``preservation/``) are imported as top-level
packages relative to the repository root. Importing it as a module from a
launcher keeps ``__file__`` resolution and package imports predictable, and
mirrors how the collector's own launcher works.

This is the FULL transcoder, not a wrapper: every code path, profile and
preset is the production one. Nothing here changes behaviour, and the
calibration collector reads it as an independent sibling process exactly as
it does on a source checkout.
"""

from __future__ import annotations

import os
import sys

# UTF-8 console output, so non-ASCII source paths are not mojibake in the
# captured log (see the collector's frozen_analyze.py for the same reasoning).
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    # The module name starts with a digit, so `from 1kt import ...` is not
    # valid Python syntax; importlib is the only way to reach it.
    #
    # 1kt.py defines main() and only calls it under an `if __name__ ==
    # "__main__"` guard, so importing it is side-effect free. Importing (as
    # opposed to runpy-executing it) also lets PyInstaller prove the module
    # graph at build time: a rename breaks the build, not the deployed binary.
    import importlib

    transcoder = importlib.import_module("1kt")
    result = transcoder.main()
    if result is None:
        return 0
    if isinstance(result, bool):
        return 0 if result else 1
    return int(result)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:            # never a bare traceback in the field
        sys.stderr.write(
            "[FATAL] 1kt launcher: %s: %s\n" % (type(exc).__name__, exc)
        )
        sys.exit(1)
