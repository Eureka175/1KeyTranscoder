"""Hardware-decode integration test package.

This package holds the automated half of the Hardware Decode Integration
Test Matrix (``docs/hardware-decode/integration-test-matrix.md``).

Layout:

* ``probe.py``     — ffprobe / MP4Box based input characterisation
* ``checks.py``    — verification primitives (counts, fingerprints,
                     metadata, ordering)
* ``runners.py``   — invoke a backend binary with a chosen decode reader
* ``fixtures.py``  — resolve matrix inputs against the on-disk corpus
* ``matrix.json``  — machine-readable test matrix (single source of truth
                     for IDs, categories, severity, automation status)
* ``harness.py``   — execute test IDs, emit JSON/CSV results

Nothing in this package is imported by the production pipeline; it is a
test-only tree.
"""

__all__ = ["probe", "checks", "runners", "fixtures", "harness"]
