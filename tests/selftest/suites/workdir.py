"""Per-job scratch-directory lifecycle (core/workdir.py).

These are pure-logic tests: no encoder, no media, no GPU. They exist because
the property being protected is "the directory is gone when the job ends, and
the report survives" -- and every failure mode of that property is a silent
one, so it needs a test rather than a code review.

The measured state before the change: cleanup happened at exactly two call
sites, both immediately before ``return "done"``. Every other exit path leaked
(three software paths never cleaned at all, and nothing cleaned on Ctrl-C).
Each case below is one of those paths.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .. import paths

record = paths.record


def _scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="1kt_workdir_"))


def l1_workdir() -> None:
    from core.workdir import gc_job_dir, job_workdir

    paths.section("L1 每 job 工作目录生命周期 (core/workdir.py)")

    # --- 1. normal completion removes the directory -----------------------
    root = _scratch()
    with job_workdir(work_root=root, job_id="job-ok") as wd:
        (wd / "intermediate.bin").write_bytes(b"x" * 32)
        seen = wd
    record("workdir.正常结束 -> job 目录被删除",
           seen.is_dir() is False, f"exists={seen.exists()}")
    record("workdir.正常结束 -> .1ktwork 根目录保留",
           root.is_dir(), f"root={root.exists()}")

    # --- 2. an exception still removes it --------------------------------
    root = _scratch()
    seen = None
    try:
        with job_workdir(work_root=root, job_id="job-boom") as wd:
            seen = wd
            (wd / "partial.bin").write_bytes(b"y" * 32)
            raise RuntimeError("encode failed")
    except RuntimeError:
        pass
    record("workdir.异常退出 -> job 目录仍被删除",
           seen is not None and not seen.exists(),
           f"exists={seen.exists() if seen else None}")

    # --- 3. KeyboardInterrupt (Ctrl-C) removes it ------------------------
    # The path nothing covered before: no atexit, no signal handler, and no
    # try/finally around the pool runners.
    root = _scratch()
    seen = None
    try:
        with job_workdir(work_root=root, job_id="job-int") as wd:
            seen = wd
            (wd / "mid-encode.bin").write_bytes(b"z" * 32)
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    record("workdir.Ctrl-C 中断 -> job 目录被删除",
           seen is not None and not seen.exists(),
           f"exists={seen.exists() if seen else None}")

    # --- 4. the report is persisted BEFORE removal -----------------------
    root = _scratch()
    reports = root.parent / (root.name + "_reports")
    with job_workdir(work_root=root, job_id="job-rep",
                     preserve_reports=reports) as wd:
        (wd / "report.json").write_text(
            json.dumps({"structural_success": True}), encoding="utf-8")
    kept = reports / "job-rep.json"
    ok = kept.is_file()
    payload = json.loads(kept.read_text(encoding="utf-8")) if ok else {}
    record("workdir.report.json 在删除前被持久化",
           ok and payload.get("structural_success") is True,
           f"kept={kept}")

    # --- 5. report copy failure RETAINS the directory --------------------
    # Deliberately different from the previous behaviour, which warned and
    # deleted anyway -- that discarded the only copy of the report.
    root = _scratch()
    seen = None
    blocked = root.parent / (root.name + "_blocked")
    blocked.write_text("this is a file, not a directory", encoding="utf-8")
    with job_workdir(work_root=root, job_id="job-keep",
                     preserve_reports=blocked / "sub") as wd:
        seen = wd
        (wd / "report.json").write_text("{}", encoding="utf-8")
    record("workdir.报告无法持久化时 -> 目录被保留 (不丢报告)",
           seen is not None and seen.exists(),
           f"exists={seen.exists() if seen else None}")

    # --- 6. --keep-work suppresses removal ------------------------------
    root = _scratch()
    with job_workdir(work_root=root, job_id="job-kw", keep_work=True) as wd:
        (wd / "kept.bin").write_bytes(b"k" * 16)
    record("workdir.--keep-work -> job 目录保留",
           wd.is_dir() and (wd / "kept.bin").is_file(),
           f"exists={wd.exists()}")

    # --- 7. the root and other jobs are never touched -------------------
    # The property the brief is explicit about: per-job cleanup must not be
    # implemented by removing the .1ktwork root, which holds shared state and
    # may contain jobs that are still running.
    root = _scratch()
    caps = root / "caps"
    caps.mkdir()
    (caps / "ffmpeg_decode_caps.json").write_text("{}", encoding="utf-8")
    sibling = root / "job-other"
    sibling.mkdir()
    (sibling / "in-flight.bin").write_bytes(b"o" * 16)
    with job_workdir(work_root=root, job_id="job-a") as wd:
        (wd / "a.bin").write_bytes(b"a" * 16)
    record("workdir.共享 caps 缓存不被删除",
           (caps / "ffmpeg_decode_caps.json").is_file())
    record("workdir.并发中其它 job 目录不被删除",
           (sibling / "in-flight.bin").is_file())
    record("workdir..1ktwork 根目录不被删除", root.is_dir())

    # --- 8. gc_job_dir is idempotent ------------------------------------
    root = _scratch()
    d = root / "job-idem"
    d.mkdir()
    first = gc_job_dir(work_dir=d, job_id="job-idem",
                       preserve_reports=None, keep_work=False)
    second = gc_job_dir(work_dir=d, job_id="job-idem",
                        preserve_reports=None, keep_work=False)
    record("workdir.重复清理是幂等的 (已不存在 = 成功)",
           first is True and second is True, f"{first}/{second}")

    # --- 9. batch_hw.cleanup_work_dir delegates to the same logic -------
    # The hardware paths call this wrapper; if it stopped delegating, hardware
    # and software would silently diverge again.
    from core import batch_hw
    import inspect

    src = inspect.getsource(batch_hw.cleanup_work_dir)
    record("workdir.硬件路径 cleanup_work_dir 复用同一实现",
           "gc_job_dir" in src, "delegates" if "gc_job_dir" in src else src[:60])
