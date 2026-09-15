#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 契约单元测试 (v0.6.2 起)。"""

from __future__ import annotations

import sys
from pathlib import Path
from ..paths import ROOT
from ..paths import record
from ..paths import section

def l1_cli_v062() -> None:
    """§1 preset case / §4 backend autoselect / §7 tiered logging."""
    section("L1 CLI v0.6.2")
    sys.path.insert(0, str(ROOT))
    import logging as _logging

    # ---- §1 CLI 大小写不敏感 (argparse type=str.lower) ----
    sys.argv = ["1kt.py"]
    from importlib import reload
    import importlib
    kt = importlib.import_module("1kt") if "1kt" in sys.modules else None
    parser_ok = True
    try:
        if kt is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("kt_mod",
                                                          ROOT / "1kt.py")
            kt = importlib.util.module_from_spec(spec)
            sys.modules["kt_mod"] = kt
            spec.loader.exec_module(kt)
        for argv_preset, want in (("HQ", "hq"), ("hq", "hq"), ("Hq", "hq"),
                                  ("uhq", "uhq"), ("ALL", "all")):
            sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                        "--preset", argv_preset]
            ns = kt.parse_args()
            record(f"v062.preset 大小写 {argv_preset} -> {want}",
                   ns.preset == want, f"got={ns.preset}")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--encoder", "NVENC"]
        record("v062.encoder 大小写 NVENC -> nvenc",
               kt.parse_args().encoder == "nvenc")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--check", "FULL"]
        record("v062.check 大小写 FULL -> full",
               kt.parse_args().check == "full")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--log-level", "DEBUG"]
        record("v062.log-level 大小写 DEBUG -> debug",
               kt.parse_args().log_level == "debug")
    except Exception as exc:               # noqa: BLE001
        parser_ok = False
        record("v062.parse_args 可导入可调用", False,
               f"{type(exc).__name__}: {exc}")
    if not parser_ok:
        return

    # ---- §4 默认后端自动选择顺序 ----
    record("v062.autoselect 顺序 = NVENC -> QSV",
           kt.AUTOSELECT_ORDER == ("nvenc", "qsv"),
           f"{kt.AUTOSELECT_ORDER}")
    record("v062.autoselect 兜底 = x265",
           kt.HW_AUTOSELECT_FALLBACK == "x265")
    record("v062.autoselect 不含 AV1 (不静默改编码格式)",
           all("av1" not in n for n in kt.AUTOSELECT_ORDER))
    seen: list[str] = []

    class _Ns:
        no_hw_autoselect = True
    got = kt.resolve_default_backend(_Ns(), ROOT, log=lambda m: seen.append(m))
    record("v062.--no-hw-autoselect 固定 x265", got == "x265", f"got={got}")
    record("v062.--no-hw-autoselect 不探测硬件",
           any("disabled" in m for m in seen), f"{seen[:1]}")

    # ---- §7 分层日志 ----
    from core.logging_utils import (
        DEBUG_LOG_NAME, ERROR_LOG_NAME, WARN_LOG_NAME, resolve_log_level,
        setup_logger,
    )
    record("v062.resolve_log_level 映射",
           resolve_log_level("error") == _logging.ERROR
           and resolve_log_level("warn") == _logging.WARNING
           and resolve_log_level("info") == _logging.INFO
           and resolve_log_level("debug") == _logging.DEBUG
           and resolve_log_level(None) == _logging.INFO
           and resolve_log_level("WARN") == _logging.WARNING,
           "error/warn/info/debug/None/WARN")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="1kt-l1-log-") as td:
        tdir = Path(td)
        lg = setup_logger(tdir / "total.log",
                          log_level=resolve_log_level("info"))
        lg.debug("DBG-X"); lg.info("INFO-X")
        lg.warning("WARN-X"); lg.error("ERROR-X")
        for h in lg.handlers:
            h.flush()

        def _txt(name: str) -> str:
            # Always re-read: setup_logger reopens total.log per call, so a
            # previously captured string goes stale.
            p = tdir / name
            return p.read_text(encoding="utf-8") if p.is_file() else ""

        total1 = _txt("total.log")
        warn1 = _txt(WARN_LOG_NAME)
        err1 = _txt(ERROR_LOG_NAME)
        record("v062.total.log 收 INFO 及以上",
               "INFO-X" in total1 and "WARN-X" in total1
               and "ERROR-X" in total1,
               f"{len(total1.splitlines())} lines")
        record("v062.warn.log 只收 WARNING 及以上",
               "WARN-X" in warn1 and "ERROR-X" in warn1
               and "INFO-X" not in warn1,
               f"{len(warn1.splitlines())} lines")
        record("v062.error.log 只收 ERROR",
               "ERROR-X" in err1 and "WARN-X" not in err1
               and "INFO-X" not in err1,
               f"{len(err1.splitlines())} lines")
        record("v062.info 级不建 debug.log",
               not (tdir / DEBUG_LOG_NAME).is_file())
        lg2 = setup_logger(tdir / "total.log",
                           log_level=resolve_log_level("debug"))
        lg2.debug("DBG-Y")
        for h in lg2.handlers:
            h.flush()
        record("v062.debug 级建 debug.log 且收 DEBUG",
               (tdir / DEBUG_LOG_NAME).is_file()
               and "DBG-Y" in _txt(DEBUG_LOG_NAME))
        lg3 = setup_logger(tdir / "total.log",
                           log_level=resolve_log_level("error"))
        lg3.info("INFO-Z"); lg3.error("ERROR-Z")
        for h in lg3.handlers:
            h.flush()
        total3 = _txt("total.log")
        record("v062.--log-level error 时 total.log 不收 INFO",
               "ERROR-Z" in total3 and "INFO-Z" not in total3,
               f"{len(total3.splitlines())} lines "
               f"(INFO-X={'INFO-X' in total3} INFO-Z={'INFO-Z' in total3})")
        for _lg in (lg, lg2, lg3):
            for _h in list(_lg.handlers):
                _h.close()
                _lg.removeHandler(_h)
