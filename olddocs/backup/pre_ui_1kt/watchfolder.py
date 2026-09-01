#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最 barebone 的 watchfolder 循环.

每隔 --interval 秒把主程序作为子进程跑一遍批处理 (1keytransc.py 自带
断点续跑: 已存在的输出会被跳过, 重复轮询开销极小)。Ctrl+C 停止;
--once 只跑一轮 (测试用)。

用法:
    python watchfolder.py --input <dir> --output <dir> --interval 300
                          [--encoder nvenc|qsv|x265] [--preset hq]
                          [--auto-downgrade] [--dry-run] [--once]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="1KeyTranscoder watchfolder")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--encoder", default="nvenc",
                    choices=["nvenc", "qsv", "x265"])
    ap.add_argument("--preset", default="hq")
    ap.add_argument("--interval", type=int, default=300,
                    help="poll interval in seconds (default 300)")
    ap.add_argument("--auto-downgrade", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="run one pass and exit (testing)")
    args = ap.parse_args()

    cmd = [
        sys.executable, str(ROOT / "1keytransc.py"),
        "--input", args.input,
        "--output", args.output,
        "--encoder", args.encoder,
        "--preset", args.preset,
    ]
    if args.auto_downgrade:
        cmd.append("--auto-downgrade")
    if args.dry_run:
        cmd.append("--dry-run")

    print(
        f"[watchfolder] input={args.input} output={args.output} "
        f"encoder={args.encoder} preset={args.preset} "
        f"interval={args.interval}s"
    )
    pass_no = 0
    while True:
        pass_no += 1
        print(
            f"[watchfolder] pass {pass_no} at {time.strftime('%H:%M:%S')} "
            f"| running: {' '.join(cmd)}"
        )
        proc = subprocess.run(cmd, cwd=str(ROOT))
        print(f"[watchfolder] pass {pass_no} done rc={proc.returncode}")
        if args.once:
            return proc.returncode
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[watchfolder] stopped by user")
            return 0


if __name__ == "__main__":
    sys.exit(main())
