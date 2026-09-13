"""Frozen entry point: offline dataset analyzer.

WHY THIS FILE EXISTS
--------------------
``tools/x265_calibration/analyze.py`` is a top-level script, but when frozen
with PyInstaller it must be imported as a module so ``__file__`` resolves
inside the bundle rather than to a path on the build machine. Freezing this
launcher instead also means the sibling .py tools beside it stay untouched for
developers.

Same pattern as release/frozen_entry.py (the collector launcher).
"""

from __future__ import annotations

import os
import sys

# Force UTF-8 console output before CPython initializes the std streams.
# Without this a Windows console would encode the report (and any non-ASCII
# path it prints) in the OEM code page, so a Chinese host path shows up as
# mojibake in captured output. This only affects display; the dataset and the
# --json report are always UTF-8.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    from analyze import main as analyze_main

    result = analyze_main()
    if result is None:
        return 0
    if isinstance(result, bool):
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
            "[FATAL] analyzer: %s: %s\n" % (type(exc).__name__, exc)
        )
        sys.exit(1)
