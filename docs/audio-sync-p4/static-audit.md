# P4 — Stage 26: final static audit

**Automated checks over P4's own code and over the dependency direction.**
Run at P4 end. Every result below is a command's output, not an inspection claim.

---

## 1. Forbidden-pattern scan — `core/audio_sync_tv.py`

| pattern | result |
|---|---|
| `shifts[-1]` | **only in the module docstring**, as an explicit prohibition (I3) |
| `previous_chunk` | **only in the module docstring**, as an explicit prohibition |
| `previous_window` | **not present** |
| `^global ` | **not present** |
| `except …: pass` | **not present** |
| bare `except Exception:` with an empty body | **not present** |
| `np.abs` | **not used.** Appears 4×: once as a docstring prohibition, once as a comment on `SHIFT_LIMIT`, and twice explaining why the guard uses `abs(float)` instead — because `np.abs(INT_MIN)` silently wraps to `INT_MIN` |
| `[-1]` outside the docstring | **not present** |

**The `[-1]` result is the important one.** P3 recorded that a chunk beginning
inside a gap reading `shift[a-1]` at local index 0 hits numpy's `shift[-1]`, which
**silently resolves to the last window of the chunk** — a plausible number from
the wrong place. P4's module contains no negative indexing at all.

## 2. Silent-exception audit

Every `except` clause in the module:

| line | clause | body |
|---|---|---|
| 387 | `except (TypeError, ValueError) as exc:` | `raise AudioSyncPlanError(...) from exc` |

**One `except` clause. It re-raises a specific type. There is no swallowing path
in the module.**

The 49 `raise` statements are enumerated in the audit output by line; they are the
error surface, all `AudioSyncError` subclasses.

## 3. Module-level state audit

| name | binding | mutable? |
|---|---|---|
| `__all__` | list literal | no |
| `ALGO_VERSION` | str | no |
| `DEFAULT_WINDOW_SAMPLES` / `DEFAULT_HOP_SAMPLES` / `DEFAULT_SEARCH_SAMPLES` | int | no |
| `DEFAULT_POLICY` / `DEFAULT_COORDINATE` | str | no |
| `GAP_POLICIES` | `Mapping` (dict literal, never written) | **not written anywhere** |
| `GAP_POLICY_NEEDS_ENDPOINTS` | same | **not written anywhere** |
| `COORDINATES` | tuple | no |
| `SHIFT_LIMIT` | int | no |
| `WINDOW_STATES` | `Mapping` (dict literal) | **not written anywhere** |
| `_FILLERS` | `Mapping` (dict literal) | **not written anywhere** |

**There is no mutable module-level state.** Every module-level binding is a
constant, a tuple, or a mapping that is only ever read. This matters because the
brief's §54 names *mutable global state* as an audit target, and because a global
cursor was one of the ways a chunk-local state could have re-entered.

`ShiftPlanStats` and `CorrectionPlan` are `frozen=True, slots=True` dataclasses;
`CorrectionPlan.__post_init__` uses `object.__setattr__` **only** to replace the
caller-supplied arrays with validated copies, and the class exposes no mutator.

## 4. Dependency-direction scan

| check | result |
|---|---|
| `core/audio_sync_tv.py` import list | exactly `{__future__, math, dataclasses, enum, typing, numpy, .sync_estimate}` |
| imports `encoders` / `preservation` / hardware backend / Sony / DJI | **no** |
| imports `core.audio_plan` | **no** |
| imported by any other `core/*.py` | **no** (asserted by test T9.6) |
| referenced by `1kt.py` | **no** (T9.7) |
| referenced by `core/audio_plan.py` | **no** (T9.8) |
| `core/channel_sync.py`, `core/sync_fix.py`, `core/sync_estimate.py` | reference it **nowhere** (T9.1–T9.3) |

### 4.1 A scan that would have produced a false positive

A naive substring scan for `encoders` / `preservation` / `sony` / `dji` in the
module's **source text** reports a violation, because the module's docstring
deliberately **names the things it must not depend on**:

> 不 import ``encoders`` / ``preservation`` / 硬件后端 / Sony 元数据;

The test therefore parses the module's **AST** and asserts on its **import list**,
which is the property that actually matters. Recorded because it is the same class
of mistake P3 documents repeatedly: a check that measures the wrong thing.

### 4.2 The architecture boundary

```text
Audio domain  ||  cross-domain orchestration  ||  Video domain
```

The new module is entirely inside the audio domain. Its only `core/` dependency is
`core/sync_estimate`'s **bounded stream reader** (`RawStream`), which is a read
primitive, not an estimator.

## 5. Research → production leak scan

| check | result |
|---|---|
| does P4's module import anything from `work/`? | **no** |
| does P4's test suite import anything from `work/`? | **no** |
| does P4 read `work/` at runtime in any production path? | **no** |
| does any production module import `work/p3/p38/semantics/gap_semantics.py`? | **no** |
| does P4's module contain any copy of P3 research code? | **no** — the coordinate functions, state derivation, gap policies and applier are independent implementations, which is why the 30/30 byte-exact reproduction is meaningful evidence rather than a tautology |

`work/p4/verify_p311_reproduction.py` **reads** P3.11's frozen plan arrays and
apply records. That is a **verification input**, not a production dependency, and
it lives under gitignored `work/`.

## 6. Unbounded-memory scan

| check | result |
|---|---|
| any `np.zeros(n_samples)` / full-stream allocation in the module | **no** |
| any per-sample coordinate label | **no** — the join is evaluated by closed form per segment |
| any `np.asarray(stream)` on the whole stream | **no** — reads are per segment |
| measured peak, stream 11.5 MB → 1382.4 MB | **9.10 MB, flat** (120× stream range) |
| measured growth over 2.42 M decisions | **traced live +0.01 MB, bounded** |

## 7. Filesystem state at P4 end

```text
HEAD unchanged                      8bb229764f551955db2c392e4b2bee6bfd7e9418
VERSION                             unchanged
tags                                unchanged (v0.8.0 only)
```

`git status --short` shows:

```text
 M 1kt.py                          <- user-owned, untouched (9/9 hashes MATCH)
 M README.md                       <- P4 (one added link)
 M VERSION                         <- user-owned, untouched
 M core/batch_hw.py                <- user-owned, untouched
 M docs/README.md                  <- P4 (new index section)
 M docs/design/architecture.md     <- P4 (new §6.8)
 M preservation/checker.py         <- user-owned, untouched
 M preservation/gpac.py            <- user-owned, untouched
 M preservation/validate.py        <- user-owned, untouched
 M tests/selftest/runner/engine.py <- user-owned, untouched
 M tests/selftest/suites/core.py   <- user-owned, untouched
 M tests/selftest/suites/toolchain.py <- user-owned, untouched
?? core/audio_sync_tv.py           <- P4
?? docs/audio-sync-p4/             <- P4
?? docs/deploy/                    <- EXTERNAL, untouched
?? docs/hardware-decode/xavc-hs-frame-count.md <- EXTERNAL, untouched
?? docs/x265-calibration/          <- EXTERNAL, untouched
?? tests/audio_sync_p4/            <- P4
?? upstream-issues/                <- EXTERNAL, untouched
```

**No P3 file appears in either list.** `docs/audio-sync-p3/`, `work/p3/`,
`work/p2/` and `work/archive/` are untouched.

## 8. Audit verdict

```text
no negative indexing                                PASS
no mutable global state                             PASS
no silent exception swallowing                      PASS
no research -> production import                    PASS
no video / preservation / hardware / metadata coupling  PASS
no unbounded memory                                 PASS
no per-sample coordinate label                      PASS
default path structurally unreachable               PASS, asserted by test
historical reports unmodified                       PASS
user-owned files unmodified                         PASS, 9/9 hashes
```

**Every check above is also covered by an executable test**, so this audit is a
summary of `tests/audio_sync_p4/test_production_sync.py` rather than a separate
one-time inspection. The audit adds no claim the suite does not already enforce.
