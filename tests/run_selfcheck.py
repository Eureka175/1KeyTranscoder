#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部自检运行器: 主程序输出 + 内置校验逻辑 + 详细 Sony 元数据对比.

流程:
  1. subprocess 调用主程序 1keytransc.py 处理一个小型 Sony 素材
     (默认 testsets/a7m5.../20260823_C0886.MP4, 6s 4K60 XAVC HS)
  2. 校验主程序退出码 = 0 且交付物存在
  3. 读取主程序写入的 preservation report (logs/preserve_reports/),
     确认内置校验 structural_success=True 且 Gyroflow PASS
  4. tests/sony_selfcheck.detailed_compare 做更细的逐轨对比, 写
     selfcheck_<stem>.json/.txt 入盘
  5. 汇总 PASS/FAIL, 退出码 0/1

定位: 内部测试 (非产品功能), 输入输出保持最简.
用法:
    python tests/run_selfcheck.py [--encoder nvenc|qsv|x265]
                                  [--preset hq] [--source <file>]
                                  [--log-dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.sony_selfcheck import detailed_compare  # noqa: E402
from preservation.gpac import GpacContainerBackend  # noqa: E402
from preservation.gyroflow import find_gyroflow  # noqa: E402


def run_main(
    main_py: Path,
    source: Path,
    work_root: Path,
    encoder: str,
    preset: str,
) -> tuple[int, str]:
    """Run the main program on one file; return (rc, tail of output)."""
    cmd = [
        sys.executable, str(main_py),
        "--input", str(source.parent),
        "--output", str(work_root),
        "--encoder", encoder,
        "--preset", preset,
        "--auto-downgrade",
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="1KeyTranscoder selfcheck")
    parser.add_argument("--encoder", default="nvenc",
                        choices=["nvenc", "qsv", "x265"])
    parser.add_argument("--preset", default="hq")
    parser.add_argument(
        "--source",
        default=str(
            ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
            / "20260823_C0886.MP4"
        ),
    )
    parser.add_argument("--log-dir", default=str(ROOT / "work" / "selfcheck"))
    args = parser.parse_args()

    source = Path(args.source)
    log_dir = Path(args.log_dir)
    work_root = ROOT / "work" / f"selfcheck_out_{args.encoder}"
    log_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(
            {"item": name, "status": "PASS" if ok else "FAIL", "detail": detail}
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    # 1. main program run
    rc, output = run_main(
        ROOT / "1keytransc.py", source, work_root, args.encoder, args.preset
    )
    check("main.exit_code", rc == 0, f"rc={rc}")

    delivered = work_root / source.name.replace(source.suffix, ".MP4")
    check(
        "main.delivered_output",
        delivered.is_file() and delivered.stat().st_size > 0,
        f"{delivered} "
        f"({delivered.stat().st_size / 1024 / 1024:.1f} MB)"
        if delivered.is_file() else f"missing: {delivered}",
    )

    # 2. built-in validation evidence (preservation report in logs)
    logs_root = source.parent.parent / "logs" / "preserve_reports"
    report_path: Path | None = None
    for cand in logs_root.glob("*.json"):
        if source.stem in cand.name:
            report_path = cand
            break
    builtin_ok = False
    gyro_ok = False
    if report_path is not None:
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        builtin_ok = bool(rep.get("structural_success"))
        gyro = rep.get("gyroflow") or {}
        gyro_ok = gyro.get("status") == "PASS"
        check(
            "builtin.structural_validation",
            builtin_ok,
            f"summary={rep.get('summary')}",
        )
        check("builtin.gyroflow", gyro_ok, str(gyro.get("detail")))
    else:
        check("builtin.preservation_report", False,
              f"no report found under {logs_root}")

    # 3. detailed Sony metadata comparison (selfcheck log to disk)
    if delivered.is_file():
        gpac = GpacContainerBackend(Path(r"C:\Program Files\GPAC"))
        ffprobe = Path(subprocess.run(
            ["where", "ffprobe"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace",
        ).stdout.strip().splitlines()[0])
        try:
            report = detailed_compare(
                original=source,
                final=delivered,
                gpac=gpac,
                ffprobe=ffprobe,
                log_dir=log_dir,
                gyroflow=find_gyroflow(None),
            )
            check(
                "detailed.sony_metadata",
                report["overall"] == "PASS",
                f"summary={report['summary']} -> {log_dir}",
            )
        except Exception as exc:
            check("detailed.sony_metadata", False, f"error: {exc}")
    else:
        check("detailed.sony_metadata", False, "no delivered output")

    summary = {
        s: sum(1 for r in results if r["status"] == s)
        for s in ("PASS", "FAIL")
    }
    overall = summary.get("FAIL", 0) == 0
    log_path = log_dir / f"selfcheck_run_{args.encoder}.json"
    log_path.write_text(
        json.dumps(
            {
                "overall": "PASS" if overall else "FAIL",
                "encoder": args.encoder,
                "preset": args.preset,
                "source": str(source),
                "elapsed_sec": round(time.time() - t0, 2),
                "items": results,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"\nselfcheck overall: {'PASS' if overall else 'FAIL'} "
          f"{summary} ({time.time() - t0:.1f}s) -> {log_path}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
