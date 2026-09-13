"""Frozen entry point: deployment helper.

See release/frozen_analyze.py for why a launcher module is used instead of
freezing the tool script directly.
"""

from __future__ import annotations

import os
import sys

# Force UTF-8 console output before CPython initializes the std streams (see
# frozen_analyze.py for the full rationale).
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    from deploy_helpers import main as deploy_main

    result = deploy_main()
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
    except Exception as exc:
        sys.stderr.write(
            "[FATAL] deploy helper: %s: %s\n" % (type(exc).__name__, exc)
        )
        sys.exit(1)
