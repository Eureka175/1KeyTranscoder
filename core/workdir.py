"""Per-job scratch-directory lifecycle.

The rule this module enforces is narrower than "clean up the work tree":

    a job's own scratch directory is removed when **that job** ends,
    on every exit path, after its reports are persisted -- and nothing
    outside that directory is ever touched.

Why a context manager rather than a call at each return
------------------------------------------------------

The measured state before this module: ``cleanup_work_dir`` had exactly two
call sites, both placed immediately before ``return "done"`` in the Sony and
DJI hardware paths. Everything else leaked -- the classic hardware path never
cleaned even on success, all three software paths never cleaned at all, and
no path cleaned on Ctrl-C (no ``atexit``, no signal handler, no ``try`` around
the pool runners). A batch of N non-Sony files therefore left N job
directories behind.

Those paths contain dozens of ``return`` statements. Adding a cleanup call to
each one is a change that silently regresses the first time somebody adds a
new early return, so the lifetime is expressed structurally instead: the
directory exists inside a ``with`` block, and ``__exit__`` runs for
``BaseException`` as well as ``Exception`` -- which is what makes an
interrupted run clean up too.

What it must never do
---------------------

* delete the ``.1ktwork`` root. Shared state lives there (the capability cache
  under ``caps/``) and other jobs may still be running under it. Only
  ``<root>/<job_id>`` is ever removed.
* delete the capability cache, or any other job's directory.
* remove the directory before the job's report has been persisted. If the
  report cannot be copied outward the directory is **retained**, because
  discarding the only copy of a report to satisfy a cleanup deadline is data
  loss, not tidiness.
* change any module's internal temp-file semantics. ``channel_sync`` already
  removes its own intermediates in a ``finally``; ``production/output``
  removes its encoded scratch on failure only. Those behaviours are untouched
  -- this module only decides the *directory* lifetime.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path


def gc_job_dir(
    *,
    work_dir: Path,
    job_id: str,
    preserve_reports: Path | None,
    keep_work: bool,
    logger=None,
    file_logger=None,
) -> bool:
    """Remove one job's scratch directory. Returns True when it is gone.

    ``keep_work`` suppresses removal (and says so). A failure to persist
    ``report.json`` also suppresses it: the report is the artefact that
    outlives the job, so it is copied out **first** and a copy failure is
    treated as a reason to keep the directory rather than a warning to log on
    the way to deleting it.
    """
    work_dir = Path(work_dir)

    def _log(level: str, msg: str) -> None:
        if file_logger is not None:
            getattr(file_logger, level)("GC | %s", msg)
        elif logger is not None:
            getattr(logger, level)("GC | %s", msg)

    if keep_work:
        _log("info", f"KEEP-WORK | work dir retained | {work_dir}")
        return False

    if not work_dir.exists():
        return True

    # --- persist the report BEFORE removing anything ---
    report_src = work_dir / "report.json"
    if report_src.is_file() and preserve_reports is not None:
        target = Path(preserve_reports) / f"{job_id}.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report_src, target)
            _log("info", f"preservation report kept | {target}")
        except OSError as exc:
            # Deliberately NOT "warn and delete anyway": that loses the only
            # copy of the report.
            _log(
                "warning",
                f"report copy failed ({exc}); work dir RETAINED so the "
                f"report is not lost | {work_dir}",
            )
            return False

    for attempt in range(3):
        try:
            # Defensive: refuse to walk a reparse point. A junction inside the
            # scratch tree pointing elsewhere would make rmtree delete its
            # target, which is how a cleanup step destroys unrelated data.
            shutil.rmtree(work_dir)
            _log("info", f"work dir removed | {work_dir}")
            return True
        except OSError as exc:
            if attempt == 2:
                _log("warning", f"work dir retained: {work_dir} | {exc}")
                return False
            time.sleep(1.0)
    return False


@contextlib.contextmanager
def job_workdir(
    *,
    work_root: Path,
    job_id: str,
    keep_work: bool = False,
    preserve_reports: Path | None = None,
    logger=None,
    file_logger=None,
):
    """Yield a job's scratch directory and guarantee its end-of-job decision.

    The directory is created on entry. On exit -- normal return, exception, or
    ``KeyboardInterrupt`` -- :func:`gc_job_dir` runs exactly once.

    ``work_root`` itself is never created, removed or otherwise touched.
    """
    work_dir = Path(work_root) / job_id
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        yield work_dir
    finally:
        gc_job_dir(
            work_dir=work_dir,
            job_id=job_id,
            preserve_reports=preserve_reports,
            keep_work=keep_work,
            logger=logger,
            file_logger=file_logger,
        )
