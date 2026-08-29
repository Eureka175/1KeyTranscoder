"""独立的编码后验证模块 (check runner).

统一封装三级验证强度, 对所有后端 (硬件 + x265) 的 Sony 输出生效:

    basic    : 仅必要核心元数据 (validate.compare level=basic)
    advanced : 完整结构校验 (validate.compare) + Gyroflow 消费端校验
    full     : advanced + 详细自检 (64 项, preservation/selfcheck);
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
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Run the level-gated post-encode checks; returns the report."""
    compare_level = "basic" if level == "basic" else "advanced"
    report = compare(
        original=original,
        final=final,
        gpac=gpac,
        ffprobe=ffprobe,
        scratch=work_dir / "validate",
        known_facts=known_facts,
        level=compare_level,
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
        elif level == "full":
            log(
                "WARNING: Gyroflow 未安装 — full 检查的消费端对比将跳过"
                "（结构检查照常执行）"
            )

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

    return report
