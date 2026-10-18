"""独立的编码后验证模块 (check runner).

统一封装三级验证强度, 对所有后端 (硬件 + x265) 的 Sony 输出生效:

    basic    : 仅必要核心元数据 (validate.compare level=basic)
    advanced : 完整结构校验 (validate.compare) + Gyroflow 消费端校验
    full     : advanced + 详细自检 (64 项, preservation/selfcheck)
               + PSNR/SSIM 质量抽样 (1-in-N 短视频, 防花屏阈值);
               先探测 Gyroflow, 未安装则提示并跳过消费端对比

返回与 validate.compare 相同形态的 report dict (含 gyroflow /
selfcheck 键, full 自检失败会置 structural_success=False 并追加
critical 项)。保留管线 (preservation/pipeline) 在 uuid/时长补丁之后
调用本模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .gpac import GpacContainerBackend
from .gyroflow import SKIPPED as SKIP
from .gyroflow import check as gyroflow_check
from .validate import compare


def run_checks(
    *,
    original: Path,
    final: Path,
    gpac: GpacContainerBackend,
    ffprobe: Path,
    level: str,
    gyroflow: Path | None,
    work_dir: Path,
    known_facts: dict[str, Any],
    codec: str = "hevc",
    ffmpeg: Path | None = None,
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    video_reencoded: bool = True,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Run the level-gated post-encode checks; returns the report.

    codec: expected re-encoded video codec ("hevc" | "av1"); drives the
    sample-entry/codec expectations and the XAVC-brand policy.
    At level='full' additionally runs the PSNR/SSIM quality sample
    (1-in-N short clips; FAIL escalates to structural failure).

    video_reencoded: False when the caller's output carries a **stream
    copy** of the source video (即 `--channel-sync-transparent` 的透明
    预处理). PSNR/SSIM 在那种情况下没有定义物: 两侧的编码帧逐位相同,
    度量只会给出一个无信息量的极大值, 所以它被**显式列为不适用**而不是
    跑一遍、也不是静默跳过 —— 静默跳过会让报告看起来"全绿"却少做了一项,
    而跑一遍会把一个恒真的数字当成质量证据。其余各级检查与正式编码
    **完全同一套**, 因为 `compare` / `selfcheck` / Gyroflow 都是
    "源 vs 成品" 的对比, 本来就不关心成品怎么来的。
    """
    compare_level = "basic" if level == "basic" else "advanced"
    report = compare(
        original=original,
        final=final,
        gpac=gpac,
        ffprobe=ffprobe,
        scratch=work_dir / "validate",
        known_facts=known_facts,
        level=compare_level,
        codec=codec,
    )

    if level != "basic":
        if gyroflow is not None:
            log("Gyroflow headless validation...")
            try:
                report["gyroflow"] = gyroflow_check(
                    original=original,
                    final=final,
                    gyroflow=gyroflow,
                    scratch=work_dir / "validate",
                )
                log(
                    f"gyroflow: {report['gyroflow']['status']} "
                    f"({report['gyroflow']['detail']})"
                )
            except Exception as exc:
                report["gyroflow"] = {
                    "status": "FAIL",
                    "detail": f"Gyroflow validation error: {exc}",
                }
                log(f"gyroflow: FAIL ({exc})")
        else:
            # Never silent, at ANY level above basic. The consumer-side
            # check is the only thing that catches a file whose metadata
            # structure is intact but unreadable — exactly the v0.8.0
            # container-remux defect, which shipped through 709 files
            # because this branch used to warn only for `full` while the
            # batch ran at `basic`. Recorded in the report as well, so
            # the skip is auditable after the fact instead of leaving
            # `"gyroflow": null` to be read as "passed".
            detail = (
                "Gyroflow not installed — the consumer-side check "
                "(IMU sample round-trip) was SKIPPED; container structure "
                "was still verified. Install Gyroflow or pass --gyroflow "
                "to make this a real gate."
            )
            report["gyroflow"] = {"status": SKIP, "detail": detail}
            log(f"WARNING: {detail}")
    else:
        detail = (
            "check=basic — consumer-side (Gyroflow) validation is not "
            "part of this level; container structure alone does not prove "
            "the metadata is readable downstream."
        )
        report["gyroflow"] = {"status": SKIP, "detail": detail}

    if level == "full":
        from .selfcheck import detailed_compare as detailed

        log("detailed metadata selfcheck (full)...")
        try:
            selfcheck_dir = work_dir / "validate" / "selfcheck"
            sc = detailed(
                original=original,
                final=final,
                gpac=gpac,
                ffprobe=ffprobe,
                log_dir=selfcheck_dir,
                gyroflow=gyroflow,
                codec=codec,
            )
            report["selfcheck"] = {
                "overall": sc["overall"],
                "summary": sc["summary"],
                "log_dir": str(selfcheck_dir),
            }
            log(f"selfcheck: {sc['overall']} {sc['summary']}")
            if sc["overall"] != "PASS":
                report["structural_success"] = False
                report["critical_missing"].append(
                    {
                        "item": "selfcheck.full",
                        "status": "FAIL",
                        "detail": (
                            f"{sc['summary']} — see {selfcheck_dir}"
                        ),
                    }
                )
        except Exception as exc:
            report["selfcheck"] = {
                "overall": "FAIL",
                "detail": f"selfcheck error: {exc}",
            }
            report["structural_success"] = False
            report["critical_missing"].append(
                {
                    "item": "selfcheck.full",
                    "status": "FAIL",
                    "detail": f"selfcheck error: {exc}",
                }
            )
            log(f"selfcheck: FAIL ({exc})")

        if not video_reencoded:
            # 透明预处理: 视频是 stream copy, 编码帧逐位相同 -> PSNR/SSIM
            # 没有定义物。**单列**成一条不适用项, 并打 INFO, 让报告能一眼
            # 看出"少做的是哪一项、为什么"。
            report["quality"] = {
                "status": "SKIPPED_NOT_APPLICABLE",
                "detail": "video stream-copied (no re-encode) — "
                          "PSNR/SSIM is not defined for identical frames",
            }
            log(
                "INFO: full 检查的 PSNR/SSIM 抽样在本模式下不适用 "
                "(视频为 stream copy, 编码帧逐位相同), 已单列为 "
                "SKIPPED_NOT_APPLICABLE"
            )
        elif ffmpeg is not None:
            from .quality import run_quality_sample

            log("PSNR/SSIM quality sample (full)...")
            try:
                q = run_quality_sample(
                    original=original,
                    final=final,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    scratch=work_dir / "validate" / "quality",
                    opts=quality_opts,
                    csv_path=quality_csv,
                    log=log,
                )
                report["quality"] = q
                if q["status"] == "FAIL":
                    report["structural_success"] = False
                    report["critical_missing"].append(
                        {
                            "item": "quality.psnr_ssim",
                            "status": "FAIL",
                            "detail": q["detail"],
                        }
                    )
                    log(f"quality sample: FAIL ({q['detail']})")
            except Exception as exc:
                report["quality"] = {
                    "status": "FAIL",
                    "detail": f"quality sample error: {exc}",
                }
                report["structural_success"] = False
                report["critical_missing"].append(
                    {
                        "item": "quality.psnr_ssim",
                        "status": "FAIL",
                        "detail": f"quality sample error: {exc}",
                    }
                )
                log(f"quality sample: FAIL ({exc})")
        elif level == "full":
            log(
                "WARNING: ffmpeg 路径缺失 — full 检查的 PSNR/SSIM 抽样跳过"
            )

    return report
