#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchfolder 循环: 每隔 --interval 秒把主程序 (1kt.py) 作为子进程跑
一轮批处理 (主程序自带断点续跑: 已存在的输出会被跳过, 重复轮询开销
极小)。Ctrl+C 停止; --once 只跑一轮 (测试用)。

透传开关: --encoder/--preset/--check/--jobs/--headless/
--experimental-multihw/--dry-run/--channel-sync/--channel-sync-groups/
--no-channel-sync。

**为什么这里必须有 --channel-sync**: 这个脚本原本不透传它, 于是经由
watchfolder / start.bat 产出的成品**从来没有做过声道对齐** —— 实测归档盘上
751 个成品的抽样件仍带着 19.5~20.8 ms 的通道偏移, 且与源素材逐采样相同
(说明归档过程既没引入也没消除偏移, 是那条路径从来没开过对齐)。
`--channel-sync` 本身仍然是 1kt.py 的显式 opt-in; 这里只是让批处理入口
有能力把它传下去, 并给 `--no-channel-sync` 一个显式否定的写法。

用法:
    python watchfolder.py --input <dir> --output <dir> --interval 300
                          [--encoder nvenc|qsv|x265] [--preset hq]
                          [--check basic|advanced|full] [--jobs 1|N|auto]
                          [--channel-sync] [--channel-sync-groups]
                          [--headless] [--experimental-multihw]
                          [--dry-run] [--once]
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
                    choices=["nvenc", "qsv", "x265", "svtav1",
                             "nvenc-av1", "qsv-av1"])
    ap.add_argument("--preset", default="hq")
    ap.add_argument("--check", default="basic",
                    choices=["basic", "advanced", "full"])
    ap.add_argument("--jobs", default="1",
                    help="1 (默认) | N | auto (自适应调度)")
    ap.add_argument("--interval", type=int, default=300,
                    help="poll interval in seconds (default 300)")
    ap.add_argument("--channel-sync", action="store_true",
                    help="透传 --channel-sync: 4 声道恒定延时对齐 (1kt.py 的 "
                         "显式 opt-in, 不指定时批处理不做对齐)")
    ap.add_argument("--channel-sync-groups", action="store_true",
                    help="透传 --channel-sync-groups: 分组锚点 (隐含 "
                         "--channel-sync; 只在\"一支片子里有两支互不相干的"
                         "麦克风\"时才有动作)")
    ap.add_argument("--no-channel-sync", action="store_true",
                    help="显式关闭声道对齐 (与 --channel-sync 互斥; 用于"
                         "把手写参数里的开关一次性否掉)")
    ap.add_argument("--headless", action="store_true",
                    help="无人值守: 不弹进度看板窗口")
    ap.add_argument("--experimental-multihw", action="store_true",
                    help="实验性: NVENC+QSV 多后端并行")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="run one pass and exit (testing)")
    args = ap.parse_args()

    # 三态: 没给 -> 不传 (交给 1kt.py 的默认, 即不对齐);
    #       给了 --channel-sync / --channel-sync-groups -> 传下去;
    #       给了 --no-channel-sync -> 两个都不传 (显式否掉)。
    if args.no_channel_sync and (args.channel_sync
                                 or args.channel_sync_groups):
        print("[watchfolder] --no-channel-sync 与 --channel-sync* 互斥",
              file=sys.stderr)
        return 2
    want_sync = bool(args.channel_sync or args.channel_sync_groups)
    want_groups = bool(args.channel_sync_groups)

    cmd = [
        sys.executable, str(ROOT / "1kt.py"),
        "--input", args.input,
        "--output", args.output,
        "--encoder", args.encoder,
        "--preset", args.preset,
        "--check", args.check,
        "--jobs", args.jobs,
    ]
    if want_sync:
        cmd.append("--channel-sync")
    if want_groups:
        # --channel-sync-groups 在 1kt.py 里隐含 --channel-sync; 两个都传是
        # 幂等的, 但显式写全便于在日志里一眼看出这次到底开了什么。
        cmd.append("--channel-sync-groups")
    if args.headless:
        cmd.append("--headless")
    if args.experimental_multihw:
        cmd.append("--experimental-multihw")
    if args.dry_run:
        cmd.append("--dry-run")

    print(
        f"[watchfolder] input={args.input} output={args.output} "
        f"encoder={args.encoder} preset={args.preset} check={args.check} "
        f"jobs={args.jobs} interval={args.interval}s "
        f"channel_sync={'on' if want_sync else 'off'}"
        f"{' (groups)' if want_groups else ''}"
    )
    if not want_sync:
        print("[watchfolder] 注意: 未开启 --channel-sync, 本轮不做声道对齐")
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
