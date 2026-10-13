"""Result-aggregation contract for the hardware-decode matrix harness.

These are pure-logic tests over ``summarise`` / ``_partition`` and the
append-only history writer. They matter because the failure mode here is not a
crash -- it is a summary that reads as "84 tests passed" when 84 is the number
of *definitions*, or as "the production gate is green" when the green came from
a previous session's results.

Everything is driven with synthetic records carrying explicit timestamps, so
the cases are deterministic and do not need hardware, fixtures or a toolchain.

Cases covered, per the aggregation contract:

    a single run
    repeated runs of the same test
    a full run followed by a partial run
    the current run not selecting a test that has an older result
    old PASS / current FAIL
    old FAIL / current PASS
    mixed BLOCKED and SKIP
    records with no timestamp at all
"""

from __future__ import annotations

from .. import paths

record = paths.record


def _res_row(tid: str, status: str, ts: str, severity: str = "P0",
             category: str = "B", reason: str = "") -> dict:
    return {
        "test_id": tid, "category": category, "severity": severity,
        "title": tid, "status": status, "input": "", "backend": "",
        "actual": "", "reason": reason, "repeats": 1, "duration_s": 0.0,
        "timestamp": ts,
    }


def l1_hwdecode_results() -> None:
    """The matrix summary must state its scope and never inherit a stale PASS."""
    import importlib

    try:
        H = importlib.import_module("tests.hwdecode.harness")
    except Exception as exc:                       # noqa: BLE001
        record("agg.harness 可导入", False, f"{type(exc).__name__}: {exc}")
        return

    paths.section("L1 硬解矩阵结果汇总范围契约 (tests/hwdecode/harness.py)")

    # ---------------------------------------------------------------- scope
    # A bare summary (no run window) must say so, and must not claim a window.
    rows = [
        _res_row("HD-B01", "PASS", "2026-10-01T00:00:00+00:00"),
        _res_row("HD-B02", "FAIL", "2026-10-01T00:01:00+00:00"),
    ]
    s = H.summarise(rows)
    record("agg.独立 summary 的 scope 是 latest_per_test",
           s["scope"] == "latest_per_test", s["scope"])
    record("agg.独立 summary 不声称有运行窗口",
           s["run_started"] == "", repr(s["run_started"]))
    record("agg.独立 summary 报告矩阵定义数",
           s["matrix_definitions"] == len(H._requirement_map()),
           f"matrix_definitions={s['matrix_definitions']}")
    record("agg.独立 summary 区分 considered / in_scope / sat_out",
           (s["records_considered"], s["records_in_scope"],
            s["records_sat_out"]) == (2, 2, 0),
           f"{s['records_considered']}/{s['records_in_scope']}/"
           f"{s['records_sat_out']}")
    record("agg.状态计数与 in_scope 相加一致",
           s["PASS"] + s["FAIL"] + s["BLOCKED"] + s["SKIP"]
           == s["records_in_scope"],
           f"{s['PASS']}+{s['FAIL']}+{s['BLOCKED']}+{s['SKIP']}"
           f"={s['records_in_scope']}")

    # ------------------------------------------------------- a single run
    run1 = "2026-10-02T00:00:00+00:00"
    rows = [
        _res_row("HD-B01", "PASS", "2026-10-02T00:00:05+00:00"),
        _res_row("HD-B02", "PASS", "2026-10-02T00:00:06+00:00"),
    ]
    s = H.summarise(rows, run1)
    record("agg.单次运行 scope=current_invocation",
           s["scope"] == "current_invocation", s["scope"])
    record("agg.单次运行全部计入",
           s["records_in_scope"] == 2 and s["records_sat_out"] == 0
           and s["PASS"] == 2,
           f"in_scope={s['records_in_scope']} PASS={s['PASS']}")

    # --------------------- full run, then a partial run (the Phase-1 case)
    # A full sweep recorded PASSes; a later --phase 1 run touches only one
    # test. The later summary must count ONE test, and the older PASSes must
    # be reported as sat out -- never counted as this run's evidence.
    full = [
        _res_row("HD-B01", "PASS", "2026-10-01T00:00:00+00:00"),
        _res_row("HD-B05", "PASS", "2026-10-01T00:00:01+00:00"),
        _res_row("HD-G06", "FAIL", "2026-10-01T00:00:02+00:00"),
    ]
    partial_start = "2026-10-03T00:00:00+00:00"
    partial = [
        _res_row("HD-B01", "PASS", "2026-10-03T00:00:05+00:00"),
        _res_row("HD-B05", "PASS", "2026-10-01T00:00:01+00:00"),
        _res_row("HD-G06", "FAIL", "2026-10-01T00:00:02+00:00"),
    ]
    s = H.summarise(partial, partial_start)
    record("agg.部分运行只计入本次执行的测试",
           s["records_in_scope"] == 1 and s["PASS"] == 1,
           f"in_scope={s['records_in_scope']} PASS={s['PASS']}")
    record("agg.部分运行把旧的 PASS 计入 sat out (不冒充本次证据)",
           s["records_sat_out"] == 2, f"sat_out={s['records_sat_out']}")
    prod = s["gates"]["production"]
    record("agg.旧 PASS 不会出现在本次 gate 的 PASS 计数里",
           prod["PASS"] == 1 and prod["total"] == 1,
           f"gate PASS={prod['PASS']} total={prod['total']}")
    # All three IDs in this scenario are production-gate tests, so both
    # untouched records (the old PASS and the old FAIL) sit out of that gate.
    record("agg.gate 报告被排除的记录数 (按 gate 分别统计)",
           prod["stale_total"] == 2 and prod["stale_pass"] == 1,
           f"stale_total={prod['stale_total']} stale_pass={prod['stale_pass']}")
    record("agg.未参与本次运行的旧 FAIL 不被静默抹除 (仍在 sat out 列表)",
           any("HD-G06" in b for b in prod["stale_blockers"]),
           str(prod["stale_blockers"])[:120])

    # ------------------------------------- old PASS / current FAIL and back
    s = H.summarise(
        [_res_row("HD-B01", "FAIL", "2026-10-04T00:00:05+00:00")],
        "2026-10-04T00:00:00+00:00",
    )
    record("agg.旧 PASS 当前 FAIL -> 本次 gate 判 BLOCKED",
           s["gates"]["production"]["FAIL"] == 1
           and s["gates"]["production"]["status"] == "BLOCKED",
           f"FAIL={s['gates']['production']['FAIL']} "
           f"status={s['gates']['production']['status']}")
    record("agg.当前 FAIL 不会被旧 PASS 覆盖",
           s["PASS"] == 0 and s["FAIL"] == 1, f"{s['PASS']}/{s['FAIL']}")

    s = H.summarise(
        [_res_row("HD-B01", "PASS", "2026-10-05T00:00:05+00:00")],
        "2026-10-05T00:00:00+00:00",
    )
    record("agg.旧 FAIL 当前 PASS -> 本次判 READY FOR REVIEW",
           s["gates"]["production"]["status"] == "READY FOR REVIEW"
           and s["FAIL"] == 0,
           f"status={s['gates']['production']['status']} FAIL={s['FAIL']}")

    # --------------------------------------------- mixed BLOCKED and SKIP
    rows = [
        _res_row("HD-A01", "BLOCKED", "2026-10-06T00:00:05+00:00",
                 reason="research build absent"),
        _res_row("HD-B03", "SKIP", "2026-10-06T00:00:06+00:00",
                 reason="fixture missing on disk"),
        _res_row("HD-B05", "PASS", "2026-10-06T00:00:07+00:00"),
    ]
    s = H.summarise(rows, "2026-10-06T00:00:00+00:00")
    record("agg.BLOCKED 与 SKIP 都不计入 PASS",
           s["PASS"] == 1 and s["BLOCKED"] == 1 and s["SKIP"] == 1,
           f"PASS={s['PASS']} BLOCKED={s['BLOCKED']} SKIP={s['SKIP']}")
    record("agg.SKIP 被归类为素材缺失",
           s["gates"]["production"]["skip_kinds"].get("fixture_absent") == 1,
           str(s["gates"]["production"]["skip_kinds"]))
    research = s["gates"]["research"]
    record("agg.research 用例如实记为 BLOCKED (未转为 PASS)",
           research["BLOCKED"] == 1 and research["PASS"] == 0,
           f"BLOCKED={research['BLOCKED']} PASS={research['PASS']}")
    record("agg.P0 BLOCKED 使 research gate 判 BLOCKED",
           research["status"] == "BLOCKED", research["status"])

    # ------------------------------------------ missing / unparsable stamps
    rows = [
        _res_row("HD-B01", "PASS", "2026-10-07T00:00:05+00:00"),
        _res_row("HD-B05", "PASS", ""),                # no timestamp at all
    ]
    s = H.summarise(rows, "2026-10-07T00:00:00+00:00")
    record("agg.无时间戳的记录不计入本次运行 (保守方向)",
           s["records_in_scope"] == 1 and s["records_sat_out"] == 1,
           f"in_scope={s['records_in_scope']} "
           f"sat_out={s['records_sat_out']}")
    record("agg.无时间戳的记录在 sat out 中可辨认",
           any("no timestamp" in b for b in
               s["gates"]["production"]["stale_blockers"])
           or s["gates"]["production"]["stale_total"] == 1,
           str(s["gates"]["production"]["stale_blockers"])[:100])

    # A garbage timestamp must not raise, and must not count as fresh.
    s = H.summarise(
        [_res_row("HD-B01", "PASS", "not-a-date")],
        "2026-10-07T00:00:00+00:00",
    )
    record("agg.无法解析的时间戳被排除而不抛异常",
           s["records_in_scope"] == 0 and s["records_sat_out"] == 1,
           f"in_scope={s['records_in_scope']} sat_out={s['records_sat_out']}")

    # An unparsable *window* must degrade to latest_per_test, not crash.
    s = H.summarise([_res_row("HD-B01", "PASS", "2026-10-08T00:00:00+00:00")],
                    "garbage-window")
    record("agg.无法解析的运行窗口退化为 latest_per_test",
           s["scope"] == "latest_per_test", s["scope"])

    # ------------------------------------------------- explicit scope flag
    s = H.summarise(
        [_res_row("HD-B01", "PASS", "2026-10-09T00:00:01+00:00")],
        "2026-10-09T00:00:00+00:00", scope="all_results",
    )
    record("agg.scope=all_results 跳过窗口过滤",
           s["scope"] == "all_results" and s["records_in_scope"] == 1,
           f"{s['scope']}/{s['records_in_scope']}")

    # ------------------------------------------------------ _partition unit
    a = _res_row("HD-B01", "PASS", "2026-10-10T00:00:01+00:00")
    b = _res_row("HD-B02", "PASS", "2026-09-01T00:00:00+00:00")
    fresh, stale, name = H._partition([a, b], "2026-10-10T00:00:00+00:00")
    record("agg._partition 只把窗口内的记录算作本次",
           [r["test_id"] for r in fresh] == ["HD-B01"]
           and [r["test_id"] for r in stale] == ["HD-B02"],
           f"fresh={[r['test_id'] for r in fresh]} "
           f"stale={[r['test_id'] for r in stale]}")
    fresh, stale, name = H._partition([a, b], None)
    record("agg._partition 无窗口时全部计入且 sat out 为空",
           len(fresh) == 2 and not stale, f"fresh={len(fresh)}")

    # ------------------------------------------------- history is preserved
    # `save_result` replaces the latest-per-test record but must *append* to
    # the history file, so a later FAIL cannot erase an earlier PASS from the
    # audit trail (and vice versa).
    import json
    import tempfile
    from pathlib import Path as _Path

    from tests.hwdecode.harness import TestResult as _TR

    tmpdir = _Path(tempfile.mkdtemp(prefix="1kt-agg-"))
    real_results = H.RESULTS
    try:
        H.RESULTS = tmpdir
        H.save_result(_TR("HD-B01", "B", "P0", "t", "PASS",
                          timestamp="2026-10-11T00:00:01+00:00"))
        H.save_result(_TR("HD-B01", "B", "P0", "t", "FAIL",
                          timestamp="2026-10-11T00:00:02+00:00"))
        latest = json.loads((tmpdir / "results.json").read_text(encoding="utf-8"))
        hist = json.loads(
            (tmpdir / "results_history.json").read_text(encoding="utf-8"))
        record("hist.latest 文件每测试一条 (被覆盖)",
               len(latest) == 1 and latest[0]["status"] == "FAIL",
               f"{len(latest)} record(s), status={latest[0]['status']}")
        record("hist.history 文件是追加的 (旧 PASS 保留)",
               len(hist) == 2
               and [r["status"] for r in hist] == ["PASS", "FAIL"],
               f"{len(hist)} record(s): "
               f"{[r['status'] for r in hist]}")
        record("hist.load_history 读回全部记录",
               len(H.load_history()) == 2, f"{len(H.load_history())}")
    finally:
        H.RESULTS = real_results
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------ no orphans/dupes
    # `save_result` keys the latest file on test_id, so repeated runs of the
    # same test cannot grow a duplicate or leave an orphan behind. Asserted on
    # a fresh temp store rather than on the real one.
    import tempfile as _tf
    from pathlib import Path as _P2

    from tests.hwdecode.harness import TestResult as _TR2

    tmp2 = _P2(_tf.mkdtemp(prefix="1kt-agg2-"))
    real2 = H.RESULTS
    try:
        H.RESULTS = tmp2
        for i, tid in enumerate(("HD-B01", "HD-B01", "HD-B02", "HD-B01")):
            H.save_result(_TR2(tid, "B", "P0", "t", "PASS",
                               timestamp=f"2026-10-12T00:00:0{i}+00:00"))
        latest2 = json.loads((tmp2 / "results.json").read_text(encoding="utf-8"))
        ids = [r["test_id"] for r in latest2]
        record("agg.最新结果每个测试只有一条 (无重复/无孤儿)",
               sorted(ids) == ["HD-B01", "HD-B02"] and len(ids) == 2,
               f"{len(ids)} record(s): {sorted(ids)}")
        hist2 = json.loads(
            (tmp2 / "results_history.json").read_text(encoding="utf-8"))
        record("agg.历史文件记录每一次执行 (4 次 save -> 4 条)",
               len(hist2) == 4, f"{len(hist2)} record(s)")
    finally:
        H.RESULTS = real2
        import shutil as _sh2
        _sh2.rmtree(tmp2, ignore_errors=True)
