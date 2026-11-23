# Failure Forensics — x265 Calibration Historical Run

**Evidence base.** 1839 sample records · 16 986 archived events · 1914 per-source
logs · 2546 `failed_details` captures · `calibration/errors/collector.log` ·
`logs/error.log` · `logs/total.log`.

**Method.** Every failure was classified from evidence — event sequence,
`COMMAND`, `EFFECTIVE_X265`, `PROGRESS` frame counts, exit codes, output
existence, filesystem state and the per-source log — never from the error string
alone. Where the evidence cannot decide, the verdict is `UNKNOWN` /
`INSUFFICIENT_EVIDENCE`.

---

## 1. Three granularities — never conflate them

| Level | Count | Meaning |
|---|---:|---|
| **Failure events** | **2535** | every failure occurrence observed in the evidence |
| **Failed sources** | **207** | distinct media files that failed |
| **Failed sample records** | **208** | rows in `calibration/samples/*.jsonl` with `status=FAILED` |

Plus a **seventh family outside the 208**: 11 probe failures for one source that
never produced a sample record at all.

Why 208 records but 207 sources: `C0051_1.MP4` and `C0051_2.MP4` are
byte-identical (`sha256:8524e7993a1d688e…`, 25 170 662 bytes). They are one
source with two names.

Why 2535 events but 208 records: the watcher re-scanned every 60 s and retried
every failing job. The `3月31日` source alone accumulated 13 failure captures;
`C0001`…`C0014` each accumulated 14; the two DJI sources each 22.

---

## 2. Taxonomy — every family, two levels deep

| # | Failure ID | Class | Subclass | Events | Records | Sources | Real x265 failure? |
|---|---|---|---:|---:|---:|---|
| 1 | `FAIL-ORCH-001` | `ORCHESTRATION` | `SIGNATURE_MISMATCH` / `UNEXPECTED_KWARG` | 2443 | **201** | 200 | **NO** — x265 never started |
| 2 | `FAIL-PRES-001` | `X265_ENCODE` | `ENCODER_CRASH_ACCESS_VIOLATION` | 67 | 5 | 5 | encode did not complete; attribution to libx265 **UNPROVEN** |
| 3 | `FAIL-PRES-002` | `X265_ENCODE` | `ENCODER_CRASH_STACK_BUFFER_OVERRUN` | 1 | 1 | 1 | encode did not complete; attribution **UNPROVEN** |
| 4 | `FAIL-PRES-003` | `PRESERVATION` | `MP4BOX_SOURCE_MOOV_UNREADABLE` | 13 | 1 | 1 | **NO** — x265 PASSed, preservation FAILed |
| 5 | `FAIL-COL-001` | `DATA_COLLECTION` | `PROBE_FAILURE` | 11 | **0** | 1 | NO — pre-encode |
| 6 | `UNRESOLVED-001` | `UNKNOWN` | `INSUFFICIENT_EVIDENCE` | 11 | 0 | 0 | unknown — `ERROR \| FAILED` anchors with no stage and no traceback |

Record-level totals: `ORCHESTRATION` 201 · `X265_ENCODE` 6 · `PRESERVATION` 1 ·
`DATA_COLLECTION` 0 (no record) = **208 records**.

---

## 3. `FAIL-ORCH-001` — the 201 (96.6 % of all failures)

### Observed

```
TypeError: encode_one_sony() got an unexpected keyword argument 'video_entry'
```

### Was x265 started? **No. Not once.**

Evidence chain, strongest first:

| Evidence | Value | Source |
|---|---|---|
| `command` present in record | **0 / 201** | `calibration/samples` |
| `effective_params` present in record | 201 / 201 (67 keys) | `calibration/samples` |
| `COMMAND` line in per-source log | **0 / 201** | `logs/files` |
| `EFFECTIVE_X265` line in per-source log | **0 / 201** | `logs/files` |
| `PROGRESS` line in per-source log | **0 / 201** | `logs/files` |
| `DONE` line in per-source log | **0 / 201** | `logs/files` |
| whole event vocabulary of those logs | `{'ENCODE RAISED': 2443}` | `logs/files` |
| `attempt_failed.output_exists` | **false** | `events/archive` |
| `wall_time_sec` | 0.000 – 0.016 s | both |

The per-source logs of the 201 contain **nothing but tracebacks**. They are the
202 event-less logs found by the schema audit (201 TypeError + 1 unrelated
`PREPROBE FAILED` log).

```
2026-09-14 07:57:24 | ERROR | ENCODE RAISED
Traceback (most recent call last):
  File "1kt.py", line 1420, in run_x265_job
  File "1kt.py", line 2473, in _encode_run
TypeError: encode_one_sony() got an unexpected keyword argument 'video_entry'
```
— `logs/failed_details/C0001-8deff42b_20260914_075725.txt`

### Mechanism

The traceback names a **keyword-only** argument. CPython binds keyword arguments
before the callee's first bytecode executes, so the call fails immediately:
`log_encode_header()` — the only emitter of `COMMAND` / `EFFECTIVE_X265` — is
never reached. That is why the parameters exist in the record (computed earlier,
on the classification path) but no command exists (built later, on the encode
path).

The line numbers place the deployed binary at a revision whose
`_encode_run` is at line 2473 and calls

```python
return encode_one_sony(
    ...
    codec=codec,
    video_entry=video_entry,     # line 2474 — rejected by the callee
    ...
)
```

while `encode_one_sony`'s signature does not declare `video_entry`, and
`encode_one_dji_x265` **does**. So the keyword is legal for the DJI callee and
illegal for the Sony callee — a caller/callee mismatch in one call site.

> **Evidence contradiction, reported not smoothed:** the deployed build
> self-reports `project_version = "1KeyTranscoder 0.7.0"` in every record and
> event, but the traceback line numbers do not fit tag `v0.7.0` (whose `1kt.py`
> has no `run_x265_job` symbol). The self-reported version is almost certainly a
> stale `VERSION` file in the frozen build. **Treat the recorded
> `project_version` as unreliable provenance.**
> (Code-path reconstruction contributed by the failure-forensics audit; the
> current tree at branch `feature/audio-sync-p2-drift` no longer exhibits the
> defect — `1kt.py:2044–2058` does not pass `video_entry` to `encode_one_sony`,
> while `1kt.py:2067` does pass it to `encode_one_dji_x265`.)

### Population

Not a random sample — it is exactly the Sony / `rtmd` C-series corpus:

| Group | Records |
|---|---:|
| `2024.9.5新生报到\新建文件夹\C000x–C001x` | 14 |
| `2025.5.30缉毒\C00xx` | 45 |
| `2025.9.27迎新晚会彩排\C89xx` | 47 |
| `2025.9.28迎新晚会现场\C90xx–C91xx` | 92 |
| `2025.10.21ifyou转场\C933x` | 3 |
| **total** | **201** |

All 201: `codec=h264`, `source_class=HIGH_BITRATE_LONG_GOP`, preset `HQ`,
container `.MP4`, `attempt_kind=primary`. Resolution split 143 × 3840×2160 and
58 × 1920×1080; fps split 188 × 29.97 and 13 × 59.94.

**Chroma:** 142 of the 201 are `chroma=4:2:2` — and **all 142 4:2:2 sources in the
entire corpus are in this failure family**, so the dataset contains **zero
successful 4:2:2 encodings**. The remaining 59 are 4:2:0. This is the single most
consequential coverage consequence of the defect, because it removes an entire
camera/chroma stratum from the calibration matrix.

### Timing — the failure is a late-window explosion

| Local day | `TypeError` records |
|---|---:|
| 2026-09-14 | 4 |
| 2026-09-19 | 3 |
| 2026-09-22 | 9 |
| 2026-09-23 | 27 |
| 2026-09-25 | **158** |
| **total** | **201** |

The defect fired from the first day (2026-09-13T23:57:25Z) but the population
exploded on 2026-09-25 00:30–03:29 local, when the watcher's final long re-scan
reached the C-series backlog. **The dataset was not lost to a sudden event; it
eroded over the whole window and was never noticed.**

**The defect never stopped during the collection window** — first occurrence
`2026-09-13T23:57:25Z`, last `2026-09-24T19:29:04Z` — and it **reproduced after
it**:

### Post-window reproduction — 2026-09-28

`calibration/events/archive/2026-09-28/` holds 58 events that are **not** in
`calibration/samples` (that dataset ends 2026-09-25):

| Field | Value |
|---|---|
| events | 58 (`job_started` 15, `attempt_started` 15, `job_failed` 14, `attempt_failed` 14) |
| failures | 28 failure events, **all** `FAIL-ORCH-001` |
| PIDs | `10120` (single run) |
| `project_version` | `1KeyTranscoder 0.7.0` |
| `wall_time_sec` | 0.0, 0.015, 0.016, 0.031, 0.063 |
| `attempt_failed.output_exists` | `false` for all 14 |

This is why **no reproduction was needed** for this family: the defect
demonstrably reproduced itself, in the deployed build, with sub-20 ms aborts and
no output, at a documented timestamp.

### Verdict

| Question | Answer |
|---|---|
| Did x265 start? | **No** |
| Did x265 produce output? | **No** |
| Is this a real x265 failure? | **No** |
| Is it a preservation failure? | **No** |
| Is it an orchestration (Python) failure? | **Yes** — `CONFIRMED`, confidence **HIGH** |
| Is the parameter vector lost? | **No** — 67/67 keys recovered from the record and from five byte-identical event representations |
| Is the *output* lost? | The output never existed. These 201 sources have **no encode at all** in the dataset. |

### Data-loss assessment

Zero analytical loss. The 201 contribute no output metric, so dropping them costs
no training rows. They do reduce corpus coverage: 201 of 1692 distinct
`(basename, preset)` sources (≈11.9 %) were never encoded.

---

## 4. `FAIL-PRES-001` / `FAIL-PRES-002` — the DJI encoder crashes

### Observed

```
2026-09-25 03:56:26 | INFO  | PROGRESS | frames=0/12948 | elapsed=1.078 sec | bitrate=N/A kbps
2026-09-25 03:56:38 | INFO  | PROGRESS | frames=0/12948 | elapsed=11.515 sec | bitrate=N/A kbps
2026-09-25 03:56:48 | INFO  | PROGRESS | frames=0/12948 | elapsed=21.828 sec | bitrate=N/A kbps
2026-09-25 03:56:58 | INFO  | PROGRESS | frames=0/12948 | elapsed=32.140 sec | bitrate=N/A kbps
2026-09-25 03:57:08 | ERROR | DJI X265 PIPELINE FAILED
Traceback (most recent call last):
  File "1kt.py", line 946, in encode_one_dji_x265
RuntimeError: video encode failed (rc=3221225477)
```

### Return-code mapping

| rc (decimal) | hex | NTSTATUS | occurrences |
|---|---|---|---:|
| 3221225477 | `0xC0000005` | `STATUS_ACCESS_VIOLATION` | 67 |
| 3221226505 | `0xC0000409` | `STATUS_STACK_BUFFER_OVERRUN` | 1 |

Both are **native memory faults** of a child process, not application exit codes.

### Which stage failed?

The wrapper's message — *"dji preservation pipeline failed: RuntimeError: video
encode failed"* — is a **pipeline-scoped label**, not the stage. The evidence:

* The raise site (`1kt.py:946`) is **upstream** of the `video ready` pipe_log and
  of `dji_rebuild`.
* **Zero** `DJI_PIPELINE | video ready:` lines exist in any of these sessions
  (the line exists and appears in the successful DJI sessions).
* `PROGRESS` shows 0, 1 or 2 frames of 8385 / 8388 / 12948 / 13343, with
  `bitrate=N/A` (sibling successful sessions show real bitrates).

**So the video encode itself did not complete.** There was no encoded artifact to
preserve. This is an **encode-side** failure, not a preservation failure.

### Is x265 itself at fault? — `INSUFFICIENT_EVIDENCE`

The crashed child is `ffmpeg.exe`, which hosts demuxer + HEVC decoder + libx265 +
scaler + MOV muxer in **one** process. Its stderr was written to
`encoded.mov.ffmpeg.log`, which was **not archived**. Without that stream the
crash cannot be localised to libx265 rather than, say, the decoder or the muxer.

Therefore: `encode_terminated_without_output = CONFIRMED` (HIGH),
`libx265_at_fault = UNKNOWN`, `repro_required = true` — with the exact command
and the discriminator to collect (`encoded.mov.ffmpeg.log`) recorded, **not
executed** (long-encode prohibition).

### Reproducibility

Deterministic per input: 11–12 repeats each, same crash line, byte-identical
commands, stable rc per file. **Honest denominator is 4 inputs, not 2**: there
are four distinct `sha256` values with ~4 KB size deltas between the
`2026.4.22运动会开幕式\杨紫绶\` copies and the `运动会\` copies, and their rc
distributions differ. "The two copies behave identically" is **not** established.

---

## 5. `FAIL-PRES-003` — the one clean `x265 PASS / preservation FAIL`

This record is the reason failure counts must not be flattened.

```
2026-09-14 08:16:48 | INFO  | PROGRESS | frames=1366/1388 | elapsed=682.110 sec | bitrate=31932.7 kbps
2026-09-14 08:16:54 | INFO  | DJI_PIPELINE | video ready: D:\...\.1ktwork\DJI_20251014141954_0002_V-935ca0ea\video\encoded.mov
2026-09-14 08:16:54 | ERROR | DJI X265 PIPELINE FAILED
Traceback (most recent call last):
  File "1kt.py", line 990, in encode_one_dji_x265
  File "preservation\dji.py", line 271, in dji_rebuild
  File "preservation\dji.py", line 55, in track_manifest
  File "preservation\gpac.py", line 138, in info
  File "preservation\gpac.py", line 50, in _run
preservation.gpac.GpacError: MP4Box -info DJI_20251014141954_0002_V.MP4 failed (rc=1):
[iso file] Unknown box type djmd in parent gmhd
...
[isom] not enough bytes in box tx3g: 8 left, reading 38 (file ..\..\src\isomedia\box_code_3gpp.c, line 327) - try specifying -no-check (might crash)
[iso file] Read Box "tx3g" (start 366578842) failed (Invalid IsoMedia File) - skipping
...
Error opening file ...\DJI_20251014141954_0002_V.MP4: Invalid IsoMedia File
```
— `logs/files/校团宣/2025.10.16河理建筑运镜/DJI_20251014141954_0002_V_HQ.log`, lines 69–96

| Fact | Value |
|---|---|
| `PROGRESS` lines | 67, `0/1388` → `1366/1388` |
| real bitrate at the end | `31932.7 kbps` |
| `DJI_PIPELINE \| video ready` | **present** — `encoded.mov` written |
| record `exit_code` | **0** (the ffmpeg child's return code) |
| `wall_time_sec` | 687.844 |
| failure locus | `MP4Box -info` on the **original source**, i.e. `track_manifest(gpac, original)` |
| fatal defect | an **unterminated `tx3g` box** cascading to `stsd`/`stbl`/`minf`/`mdia`/`trak`/`moov` read failures |
| `djmd` / `dbgi` warnings | **not fatal** — 633 sibling DJI muxes succeeded with the same box types |

**Verdict.** `x265 encode = PASS`. `preservation (MP4Box -info on source) = FAIL`.
Recording this as an "x265 failure" would be wrong. Note also that the last
`PROGRESS` shows **1366/1388** frames: the pipeline advanced to preservation
before all frames were emitted, so the intermediate MOV was not fully finalised —
this is *not* a clean x265 PASS either. The honest statement is
**"encode substantially complete (1366/1388 frames, real bitrate), intermediate
MOV written, preservation rebuild failed on an unreadable source container."**

---

## 6. `FAIL-COL-001` — the family with no sample record

One source, `DJI_20260331160404_0380_D_ZX1.MP4`, failed **11 times** with

```
failure_stage: probe_failed
failure_reason: ffprobe found no streams.
wall_time_sec: 0.032
```

on 2026-09-24 and 2026-09-25, and **produced no sample record at all** —
verified against all 1839 records. `source_id` is absent from the events and the
`job_started` payload carries no `source_info` / `effective_params`, i.e. the job
aborted before the probe stage populated anything.

**Consequence:** the 208 failed records **under-report** the failed sources. The
true numerator is 207 distinct sources among the records, plus at least one more
outside them.

This is consistent with the 75 per-source logs that have no matching record.

---

## 7. Evidence chains

Full per-failure chains are in
`1kt_calibrate/archive/forensics/evidence_chains.json` (208 chains) with the shape

```
source → run → command → process → output → postprocess → verification → failure
```

Summary of the chain verdicts:

| Failure ID | command | x265 ran | output | postprocess reached | verification | stage that actually failed |
|---|---|---|---|---|---|---|
| `FAIL-ORCH-001` | absent | **no** | none | no | `not_run` | Python call binding |
| `FAIL-PRES-001/002` | present | yes, aborted | none | no | `not_run` | encode child process (crash) |
| `FAIL-PRES-003` | present | yes, `encoded.mov` written | intermediate only | **yes** | `not_run` | preservation rebuild (`MP4Box -info`) |
| `FAIL-COL-001` | absent | no | none | no | no record | probe (`ffprobe`) |

---

## 8. Failure fingerprints

Machine-readable set: `1kt_calibrate/archive/forensics/failure_fingerprints.json`.
Every regex was re-matched against all 2535 failure events: each pattern matches
exactly its declared count, every event matches exactly one pattern, **zero
unmatched**.

| failure_id | error_pattern (abridged) | x265_started | output_produced | true_x265_failure | repro_required |
|---|---|---|---|---|---|
| `FAIL-ORCH-001` | `TypeError: encode_one_sony\(\) got an unexpected keyword argument 'video_entry'` | false | false | **false** | **false** (self-reproduced 2026-09-28) |
| `FAIL-PRES-001` | `video encode failed \(rc=3221225477\)` | true | false | unproven | true (recorded, not run) |
| `FAIL-PRES-002` | `video encode failed \(rc=3221226505\)` | true | false | unproven | true (recorded, not run) |
| `FAIL-PRES-003` | `preservation\.gpac\.GpacError: MP4Box -info .* failed` | true | **true** (`encoded.mov`) | **false** | false |
| `FAIL-COL-001` | `ffprobe found no streams` | false | false | false | false |
| `UNRESOLVED-001` | `\|\s*FAILED\s*\|\s*return=\d+` | unknown | unknown | unknown | — |

The first five regexes are intended for automatic future classification.

---

## 9. Contradictions found — reported, not smoothed

1. **Version self-report vs code path.** Records say `1KeyTranscoder 0.7.0`; the
   traceback line numbers do not fit tag `v0.7.0`.
2. **`failure_stage = "encode_failed"` on a preservation failure.** The GpacError
   record is staged `encode_failed` although the encode succeeded.
3. **`preservation` is `null` in all 1839 records**, including the 7 whose
   `failure_reason` explicitly names a preservation pipeline. The collector never
   populates that block — preservation detail exists only in logs.
4. **`exit_code = 0` on a failed job** (the GPAC record): the field carries the
   encoder child's code, not the job outcome.
5. **11 `ERROR | FAILED` anchors cannot be attributed.** They carry
   `return=3221225477 | elapsed=…` with no stage name and no traceback, which is
   why `job_failed` counts (2546 captures) exceed `attempt_failed` (2535).
6. **TypeError logs have no `SOURCE` / `CLASSIFICATION` lines** even though the
   record has both — a consequence of a 1-second abort, not a logging outage.
7. **`failed_details` stem count (208) coincides with the record count (208)**
   but is not the same set: one stem, `3月31日`, is project-level and belongs to
   no record.

---

## 10. UNKNOWN / INSUFFICIENT_EVIDENCE register

| ID | Question | Status | What would settle it |
|---|---|---|---|
| `UNRESOLVED-001` | What are the 11 stage-less `ERROR \| FAILED` anchors? | `INSUFFICIENT_EVIDENCE` | child stderr, or a `DJI X265 PIPELINE FAILED` line in the same window |
| `UNRESOLVED-002` | Which commit is the deployed `1kt.exe`? | `UNKNOWN` | the bundle's `VERSION` / build manifest (portable root `D:\1KT-x265-portable` no longer exists) |
| `UNRESOLVED-003` | Did libx265 cause the DJI crashes? | `INSUFFICIENT_EVIDENCE` | `encoded.mov.ffmpeg.log` stderr, or a WER dump |
| `UNRESOLVED-004` | Do the 4 near-duplicate DJI inputs behave identically? | `UNKNOWN` | repeated short encoding of each of the 4 (not run) |
| `UNRESOLVED-005` | Why did the probe-failure source produce no record? | `UNKNOWN` | the collector's record-emission rule for pre-probe aborts |
| — | Would the 201 have produced exactly these 67 parameter values? | `INSUFFICIENT_EVIDENCE` | an actual encode — **prohibited** |

---

## 11. What this means for the calibration dataset

* The dataset's `failed` column is **not** a measure of encoder quality.
  **201 of 208 (96.6 %)** failures are an application defect with no encoder
  involvement.
* Only **6 records** evidence an encode that started and did not complete, and
  even those cannot be pinned on libx265 from the archived evidence.
* **1 record** is a preservation failure with a substantially complete encode —
  it must be excluded from failure statistics and considered *separately* for the
  compression-ratio analysis.
* **11 failure events for ≥1 source** exist outside the sample dataset entirely.
* A "x265 failure rate" computed naively from this dataset
  (208/1839 = 11.3 %) is **meaningless**. The defensible figure is
  6 encoder-side non-completions out of 1638 attempted encodes (0.37 %), plus
  1 preservation failure (0.06 %).
