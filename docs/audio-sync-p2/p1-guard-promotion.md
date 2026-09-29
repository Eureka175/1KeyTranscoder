# P1 Regression Guard — Promotion Assessment

> Stage 5 of the Audio Sync final session: now that P2 is formally `CLOSED`, should
> `work/p2/scripts/p1_regression_guard.py` be promoted into the project's test suite?
>
> **Decision: NO — keep it in `work/p2/scripts/` as the documented on-demand guard.**
> This chapter records the evidence for that decision so it does not have to be
> re-derived, and states the exact conditions that would reverse it.

## 1. What the guard is worth (unchanged)

`p1_regression_guard.py` is the only artefact in the repository that asserts the
**shipped** P1 decisions on the **real** corpus: `measure_failed`, anchor CH3,
`algo_version 2.3.0-p1`, four per-track rows, every non-anchor track `untouched`,
refusal reason `non_constant`, and **no track reported `fixed`**. It also re-checks
the known-answer controls that stop the baseline from being vacuous.

Current result (`work/p2/out/p1_guard_recovery.json`, exit code 0):

```text
27 PASS / 0 FAIL
```

It is now operational again: the two full channel-sync recovery runs rebuilt its
inputs (see `p1-artifact-recovery.md`, `work/p2/p1-recovery-run-{1,2}.json`).

## 2. The framework the promotion would target

The repository does **not** use pytest. Its suites are custom:

```text
tests/full_autotest.py --level {unit,toolchain,full}
  → tests/selftest/runner/engine.py  build_registry()  (the single discovery point)
  → tests/selftest/suites/*.py       functions calling record(name, ok, detail)
```

`record()` in `tests/selftest/paths.py:60` has exactly **two states**:

```python
"status": "PASS" if ok else "FAIL"
```

There is no skip, no xfail, no "not applicable" — and no suite in the tree
introduces one. Every existing suite that cannot run records a **FAIL** with a
`本组跳过` ("this group is skipped") detail string (e.g. the two numpy guards at
`tests/selftest/suites/hardware.py:20` and `:841`).

## 3. The five promotion conditions, checked honestly

| condition (from the session brief) | verdict | evidence |
|---|---|---|
| guard deterministic | **PASS** | no RNG, no time dependence, no environment input; recovery reproduced the historical transparent-file SHA256 **exactly** (`8e587168b804…`) |
| guard has stable input | **PARTIAL** | section B: yes. Section A: the input is the **50 GB private corpus + derived artefacts that are git-excluded by design** |
| guard does not depend on a private temp directory | **PASS for `work/`** | `work/p2/` is not a temp directory: it is tracked-in-tree research storage that has survived every cleanup — and the loss that motivated this session was an accidental relative-path delete, not a policy exclusion |
| guard output reproducible | **PASS** | JSON record written per run; two independent runs on C1305/C1306 gave identical decisions, reasons and anchors |
| guard can be collected correctly by the existing selftest framework | **FAIL** | see §4 |

## 4. The decisive finding — a missing input is indistinguishable from a regression

This is the whole argument, and it is not about style.

The failure mode this session existed to repair was:

```text
guard's inputs deleted
  → guard reports 0 PASS / 3 FAIL
  → a reader sees RED and must investigate
  → the red was NOT a regression: it was 3 × MISSING INPUT
```

If the guard is wired into the suite **as it stands**, that same failure mode
reappears *inside* the suite, and one layer worse:

* `record("...", ok=False, "missing work/p2/out/agent_j/...")` renders in the report
  exactly like a real assertion failure. Nothing in the report format distinguishes
  "the feature broke" from "the fixture is gone".
* There is no third state that could carry "not applicable — corpus absent, this is
  not a regression", which is the only honest verdict in that situation.
* The corpus check needs the **50 GB** private material plus the recovered
  `agent_j/` artefacts. Neither can be committed, so on any machine but this one the
  suite would turn permanently red for a reason unrelated to the code.
* `--level unit` is documented as *"pure logic, no external tools, seconds"*
  (`tests/full_autotest.py:6`). The corpus check takes **739 s of measured wall time
  for both files** (403 s C1305 + 336 s C1306) — about 3000× that budget.
* The `full` level is already ~10–15 minutes. Adding a third independent full-file
  P1 measurement would make a routine run depend on the diagnosis corpus.

**Promoting the corpus half would therefore re-create, inside the project's own
green/red signal, exactly the ambiguity that made this session necessary.** A
regression guard whose red light does not mean "regression" is worse than an
on-demand guard whose red light does.

## 5. Section B on its own would be near-duplicate coverage

Stripping section A and shipping only the controls is the tempting middle option.
It is not worth it, because the controls are **already covered inside the suite, on
committed fixtures that need no private material**:

| guard section B control | already covered by |
|---|---|
| constant offset corrected, exact integer shift | `C15_sync_partial_报告` / `_决策` / `_输出复测`, `C27_负延迟_shift负数`, `C27_负延迟_修正轨已对齐`, `C28_96kHz_整数延迟`, `C29_s32le_f64管线` |
| transparent mode byte-identity on all-aligned input | `C18_transparent_全对齐_字节一致` |
| non-eligible input copied through unchanged | `C23_transparent_非资格_原样拷贝`, `C23_transparent_纯音频_探测回退拷贝` |
| silent / low-SNR track stays `untouched` | `C24_低SNR轨_untouched`, `C21_non_finite轨_untouched`, `C22_过短轨_untouched` |
| determinism | `C19_确定性_JSON字节一致` |

(`tests/selftest/suites/hardware.py:24` `l3_channel_sync_p1` and `:832`
`l3_channel_sync_p1_algo`; all fixtures are synthesised at run time from `lavfi`
with fixed seeds, and both are registered in `engine.build_registry()`.)

So section B's marginal value is the *real-corpus* KAT record specifically — i.e.
the part that carries the corpus dependency. The redundancy is the load-bearing
control, not the cheap one.

## 6. Decision

```text
PROMOTION = NO
LOCATION  = work/p2/scripts/p1_regression_guard.py   (unchanged)
STATUS    = OPERATIONAL (27 PASS / 0 FAIL)
```

`work/p2/scripts/` is not a holding pen for "not-yet-official" tests. It is where
the audio-sync diagnosis lives, it is referenced from `docs/audio-sync-p2/README.md`,
and it will keep existing. Moving the guard into `tests/` would relocate a
**corpus-dependent measurement** into a framework whose only vocabulary is
pass/fail.

Two things were wrong with promotion *before* this session, and only one of them
has been fixed:

| blocker | state now |
|---|---|
| the guard's inputs were deleted, so it could not run | **FIXED** — recovery runs 1 & 2 restored them; guard is operational |
| the target framework cannot express "input absent ≠ regression" | **STILL TRUE** — and it is a framework property, not a paperwork gate |

The old justification (`p1-baseline.md` §4.1: "do not promote until the §19 gate is
met") is **withdrawn** — see §7. The decision survives on the operational ground
above, which is a stronger reason.

## 7. Why the old justification is withdrawn

`p1-baseline.md` §4.1 and `validation.md` §5 blocked promotion behind the §19 gate:
*"adding a file under `tests/` is a production change, and the §19 gate is not met."*

That reasoning does not survive the closure. The §19 gate existed to authorise a
**P2 production change**; P2 is `CLOSED` with no production change, so the gate is
now moot rather than unmet, and it cannot keep doing work as a promotion blocker for
a test that would guard **P1**. Leaving it in place would be using a withdrawn gate
as if it still applied.

The corrected status of the gate itself, for the record:

| §19 conditions | status now |
|---|---|
| 1 real corpus proves time-varying delay exists | PASS |
| 2 calibrated estimator | PENDING |
| 3 model selection has evidence | FAIL |
| 4 correction prototype passes whole-timeline verification | FAIL |
| 5 long-duration residual significantly reduced | FAIL |
| 6 verification independent of the plan's geometry | FAIL |
| 7 no P1 regression | **PASS — and now continuously checkable again** |
| 8 fallback honoured | PASS for P1 / FAIL for the prototype |
| 9 determinism, O(window) memory, counts preserved | PASS |
| 10 integration constraints | PENDING |

Conditions **3–6 are gates on shipping a P2 correction.** Since P2 will not ship a
correction, they are `N/A — not exercised, by decision` rather than outstanding
failures. Conditions **2 and 10** concern a P2 estimator that does not exist and
will not be built; they are `N/A — no artifact to assess`. Condition 7 is the only
one that still does live work, which is precisely why the guard matters.

## 8. Conditions that would reverse this decision

Promote the guard into `tests/selftest/suites/` **only if** all of the following
become true:

1. the selftest framework gains an explicit third state (`record(..., status=...)`
   or an equivalent skip) so that an absent corpus fixture cannot render as a
   functional failure; **and either**
2. a corpus-independent baseline fixture is committed at a size the repository will
   accept (a 30–60 s excerpt carrying the same `non_constant` refusal), **or**
   the corpus half stays out of the suite and only a genuinely non-redundant
   remainder is promoted.

Until then the canonical invocation stays:

```text
python work/p2/scripts/p1_regression_guard.py --json work/p2/out/p1_guard_recovery.json
```

with its preconditions and provenance recorded in `p1-artifact-recovery.md` and
`work/p2/p1-artifact-manifest.json`.
