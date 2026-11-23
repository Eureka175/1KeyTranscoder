# Reconstruction Report — Consolidated Findings and Dataset Readiness

This is the executive document. It states the outcome of the study, the readiness
verdict, what was and was not executed, and an honest self-audit of the analysis's
own limitations.

---

## 1. Historical Dataset

| Metric | Value |
|---|---:|
| **Raw records** | **1839** |
| — succeeded | 1631 |
| — failed | 208 |
| **Log files (all evidence families)** | **21 473** |
| — `calibration/events/archive/*.json` | 16 986 |
| — `logs/files/**/*.log` | 1914 (1767 distinct basenames, 202 with no structured event) |
| — `logs/failed_details/*.txt` | 2546 |
| — `calibration/samples/*.jsonl` | 10 (the dataset) |
| — `calibration/summaries/*.json` | 10 |
| — `calibration/errors/*` | 2 |
| — `calibration/state/*` + `calibration/manifest.json` + `README_FIRST.txt` | 5 |
| **Bytes of evidence indexed** | 169 086 805 (~161 MiB) |
| **Unique sources** | **1838** (content-hash identity; one duplicate pair collapses) |
| — distinct `source_id` (basename stem) | 1692 |
| — basename stems with collisions | **141** (288 records) |
| **Unique runs** | **17** reconstructed (host × PID × day) |
| Collection window | 2026-09-13 → 2026-09-25 local (records); failure evidence to 2026-09-28 |
| Active days | 10 (gaps 09-16 … 09-18 = no activity) |

---

## 2. Reconstruction

| Metric | Value |
|---|---:|
| Original missing `actual_x265_params` | **201** |
| `RECOVERED_EXACT` | **0** |
| `RECOVERED_FROM_COMMAND` | **0** |
| `RECOVERED_FROM_EVENT` | **201** |
| `RECOMPUTED_FROM_AUDIT` | 0 |
| `PARTIAL` | 0 |
| `CONFLICT` | 0 |
| `NOT_RECOVERABLE` | **0** |
| Records with a parameters vector after recovery | **1839 / 1839** |
| Scaling-rule reproduction rate | **1638 / 1638 = 100.0000 %** (two independent methods) |
| **Unrecorded completed encodes recovered** | **73** into a flagged supplement (see §2b) |

### 2b. The dataset is also missing 73 *successful* encodes — recovered

The collector rejected **338** events with the error *"is not filesystem-safe"*,
caused by CJK characters in the job id. Each rejected entry carries the **full
`raw_event` payload**. 73 of those job ids have a `job_succeeded` event, i.e. a
transcode that **ran to completion** and was never written into
`calibration/samples/`.

| Metric | Value |
|---|---:|
| Rejected event entries | 338 |
| Distinct rejected jobs | 74 |
| **Recovered attempts (with success event)** | **73** |
| … with output metrics | **73 (100 %)** |
| … with a `compression_ratio` | **73 (100 %)** |
| … with a complete 67-key parameter vector | **73 (100 %)** |
| Rejected jobs with no success event | 1 (`3月31日-2dc0eea7-HQ`: 11 crashes + a 28.5 h hung session) |
| Recovered sources **not present in the base dataset** | **73 / 73** |

| Population | Records | With `compression_ratio` | Distinct sources |
|---|---:|---:|---:|
| Base (`calibration/samples`) | 1839 | 998 | 1838 |
| Supplement (recovered) | **73** | **73** | **73** |
| Combined *if counted* | **1912** | **1071** | **1911** |

The supplement adds geometry the base corpus never had: 42 × 3840×2160,
19 × 1920×1080, **8 × 2160×3840 (portrait)**, **1 × 1080×1920 (portrait)**,
3 × 2560×1440 — and one `qtrle` source, a codec absent from the base set entirely.

**Policy.** The supplement is **never merged** into the base dataset. It lives at
`calibration/reconstructed/records_supplement.jsonl` and
`archive/dataset/records_supplement.jsonl`, indexed by
`archive/index/by_supplement.json`, and every recovered record carries
`provenance_class = RECOVERED_FROM_REJECTED_EVENT` plus the flags
`NOT_IN_CALIBRATION_SAMPLES` and `RELATIVE_PATH_DERIVED`. Confidence is **MEDIUM**:
the payload is the producer's own event, but it never passed collector validation,
and `relative_path` is derived from `source_path` rather than recorded.

**Consequence for every number in this report set:** the population must always be
stated. "1839 records" means the base dataset; "1912" means base + supplement.

### The finding that reframes the question

```
actual_x265_params  ==  serialise(effective_params)     identity holds 1638 / 1638
```

`actual_x265_params` is a flattened serialisation of `effective_params`, written
only on the success path. All 201 records carry a complete **67-key
`effective_params`** *and* the full `effective_params_audit`. Each vector exists in
**five byte-identical representations** (the record plus four event types);
402/402 cross-representations agree.

**The 201 "missing parameters" are therefore not a recovery problem at all.** They
are a symptom of 201 attempts that never reached the encoder.

Why `RECOVERED_FROM_COMMAND` is zero: **no per-source log of the 201 contains a
`COMMAND` line.** The 201 logs contain exactly one event type, `ENCODE RAISED`
(2443 occurrences) — tracebacks and nothing else.

---

## 3. Failure Forensics

Three granularities, deliberately separated:

| Level | Count |
|---|---:|
| Failure **events** | **2535** |
| Failed **sources** | **207** (plus ≥1 source with events but no record) |
| Failed **sample records** | **208** |

| Class | Records | Subclass | Real x265 failure? |
|---|---:|---|---|
| `ORCHESTRATION` | **201** | `SIGNATURE_MISMATCH` (`TypeError: encode_one_sony() … 'video_entry'`) | **No — x265 never started** |
| `X265_ENCODE` | **6** | `ENCODER_CRASH_ACCESS_VIOLATION` ×5, `ENCODER_CRASH_STACK_BUFFER_OVERRUN` ×1 | encode did not complete; **libx265 attribution UNPROVEN** |
| `PRESERVATION` | **1** | `MP4BOX_SOURCE_MOOV_UNREADABLE` | **No — x265 PASSed, preservation FAILed** |
| `DATA_COLLECTION` | **0** | `PROBE_FAILURE` (11 events, no record) | No — pre-encode |
| `UNKNOWN` | **0** | `INSUFFICIENT_EVIDENCE` (11 stage-less anchors) | unknown |
| Infrastructure / Data-collection / Postprocess / Verification / Filesystem (record-level) | **0** | — | — |
| **total** | **208** | | |

The user-specified first-level buckets map as follows: `ORCHESTRATION` 201 ·
`X265_ENCODE` 6 · `PRESERVATION` 1 · `CALIBRATION_INFRASTRUCTURE` 0 ·
`DATA_COLLECTION` 0 (events outside the records) · `POSTPROCESS` 0 (folded into
`PRESERVATION`) · `VERIFICATION` 0 · `FILESYSTEM` 0 (the CJK path-safety
rejections cost no record) · `UNKNOWN` 11 events.

### Failure fingerprints

Five fingerprints, plus one `UNRESOLVED` pattern. All regexes re-matched against
all 2535 failure events: **zero unmatched, zero overlap.** The flagship entry:

```json
{
  "failure_id": "FAIL-ORCH-001",
  "class": "ORCHESTRATION",
  "subclass": "SIGNATURE_MISMATCH",
  "error_pattern": "TypeError: encode_one_sony\\(\\) got an unexpected keyword argument 'video_entry'",
  "first_observed": "2026-09-13T23:57:25Z",
  "last_observed": "2026-09-24T19:29:04Z",
  "affected_records": 201,
  "affected_sources": 200,
  "x265_started": false,
  "output_produced": false,
  "true_x265_failure": false,
  "confidence": "HIGH",
  "repro_required": false
}
```

`repro_required: false` because the defect **reproduced itself** after the dataset
closed: 58 events on 2026-09-28, 28 failures all of this class, single PID 10120,
`wall_time_sec` 0.000–0.063 s, `attempt_failed.output_exists = false`.

---

## 4. Root Cause

### Confirmed (HIGH confidence)

| ID | Root cause | Evidence basis |
|---|---|---|
| `FAIL-ORCH-001` | A caller/callee keyword mismatch: `_encode_run` passes `video_entry=` to `encode_one_sony`, whose signature does not declare it. CPython binds keyword arguments before the callee's first bytecode, so the abort precedes `log_encode_header()` and no encoder process is ever spawned. | 2443 `ENCODE RAISED` tracebacks; **0** `COMMAND`/`PROGRESS`/`DONE`; `output_exists=false`; `wall_time_sec ≤ 0.016 s`; 0 recovered outputs; the same defect at `1kt.py:2044–2058` vs `:2067` in the current tree; self-reproduced 2026-09-28 |
| `FAIL-PRES-003` | A truncated/unterminated `tx3g` box in the **source** container makes `MP4Box -info` fail, cascading `stsd`→`stbl`→`minf`→`mdia`→`trak`→`moov` reads. The encode had already written `encoded.mov`. | log lines 69–96: `PROGRESS 1366/1388`, `DJI_PIPELINE \| video ready`, then `preservation.gpac.GpacError`; `exit_code=0`; the `djmd`/`dbgi` warnings are non-fatal (633 sibling DJI muxes succeeded) |
| `FAIL-COL-001` | The input could not be probed at all (`ffprobe found no streams`), so the job aborted at the probe stage and **no sample record was emitted**. | 11 `job_failed` events, `failure_stage=probe_failed`, `wall_time_sec 0.032`, no `source_info` on the event |
| `records_skipped_duplicate = 939` | The transcoder re-announced each job with the **same `attempt_id`** on every watcher scan; the collector de-duplicated at the record level and kept the first. The collector itself calls this a producer defect. | **939** `ERROR \| DUPLICATE attempt_id …` lines in `collector.log`, matching `records_skipped_duplicate` exactly |

### Highly likely (MEDIUM confidence)

| ID | Finding |
|---|---|
| — | The 52 sample-vs-log parameter disagreements arise from the append-only per-source log recording a **later watcher re-run** whose dynamic VBV inputs were re-measured. Same records, same two keys (`vbv-maxrate`, `vbv-bufsize`). |
| — | The verification gate (`passed` 998 / `skipped` 633) is selected by **orchestration path**, observable as file extension (`.mov` 807/807 passed vs `.mp4` 191/824). Both documented mechanisms were falsified. |
| — | The 338 `events_invalid` are CJK-filename filesystem-safety rejections; they cost no record but do correspond to the orphan-log population. The log-schema audit additionally found **340 archive events whose ids the collector never counted as valid** (2 more than the 338 rejections) — reported as an open 2-event discrepancy. |

### Insufficient evidence / UNKNOWN

| ID | Question |
|---|---|
| `UNRESOLVED-001` | The 11 stage-less `ERROR \| FAILED` anchors (`return=3221225477`), which is why job-level counts (2546) exceed attempt-level (2535) |
| `UNRESOLVED-002` | Which commit the deployed `1kt.exe` came from — the recorded `project_version` contradicts the traceback line numbers |
| `UNRESOLVED-003` | Whether **libx265** caused the 6 DJI crashes. The crashed child is `ffmpeg.exe` (demuxer + decoder + libx265 + muxer in one process) and its stderr (`encoded.mov.ffmpeg.log`) was **not archived** |
| `UNRESOLVED-004` | Whether the 4 near-duplicate DJI inputs behave identically |
| `UNRESOLVED-005` | Why the probe-failure source produced no record |
| — | The residual within-`.mp4` verification rule |
| — | Whether a re-encode would emit exactly the recovered 67 values (prohibited) |
| — | **2 events** in the archive (`20260913T074806154-DSC_5381-…-job_started-000001-833fcb` and its `attempt_started` sibling) whose ids the collector never counted as seen or valid, and which the `events_buffered = 5` counter does not explain (the word "buffered" appears **0** times in `collector.log`). Reported as an open 2-event accounting discrepancy, not smoothed over. |

---

## 5. Data Quality

| Metric | Value |
|---|---|
| Parameter completeness | **1839 / 1839 = 100 %** (1638 native + 201 recovered) |
| `actual_x265_params` completeness | 1638 / 1839 = 89.1 % (structurally, not loss) |
| Source metadata completeness (`source_info`, `source_sha256`, `classification`, `scaling_context`) | **1839 / 1839 = 100 %** |
| Output metrics completeness — `output_size`, `wall_time_sec` | **1631 / 1631 successes = 100 %** |
| Output metrics completeness — `compression_ratio`, `encode_fps` (stored) | **998 / 1839 = 54.3 %** |
| … same, after re-projection from `output_size/source_size_bytes` and the log `DONE` line | **1631 / 1631 successes = 100 %** (633 recovered, cross-court agreement 633/633 EXACT) |
| `preservation` block populated | **0 / 1839 = 0 %** — but 1266 events carry `preservation_summary` (schema divergence, recoverable) |
| Duplicate content (`source_sha256`) | **1 pair** |
| Duplicate `event_id` | 0 |
| Duplicate records at any level (full-content, semantic, job_id) | **0** |
| Colliding `source_id` (not duplicates) | 141 stems / 288 records |
| Collector-reported skipped duplicates | 939 (explained verbatim by 939 `DUPLICATE attempt_id … NOT persisted` lines) |
| Conflicts | **111** (52 HIGH, 59 MEDIUM) |
| Param / status / command / source-info conflicts (cross-family) | **0** |
| Orphan per-source logs | 75 (74 = the CJK rejection population, 73 of which produced output) |
| Orphan `failed_details` captures | 22 |
| Orphan records (no log, no event) | **0** |
| Invalid metadata / invariant violations | **0** (2 records have a `compression_ratio > 1`; 6 records need quarantine by a readiness filter) |
| Summary fields reconciled | 100 / 100, **0 discrepancies** |
| Consistency checks | 14 / 14 pass |
| Fully coherent records (status vs verification vs output vs failure fields) | **1832 / 1839** (the 7 exceptions are `status=FAILED` with `completeness.complete=true`) |

### Usability split

| Class | Records | Share |
|---|---:|---:|
| Fully usable (params + ratio + verification `passed`) | **998** | 54.3 % |
| Partially usable (params + size/time; ratio re-projectable 633/633) | **633** | 34.4 % |
| No analytical value (failures, no output) | **208** | 11.3 % |
| **Recovered supplement** (complete successes absent from `samples`) | **+73** | population 1839 → **1912 if counted** |

**The binding ceiling is not the 201 and not the 633.** It is that **no record in
the corpus contains a real quality measurement** — `verification_result.checks` is
only `["encoder_exit_code_0", "output_file_present_non_empty"]`, and no PSNR /
SSIM / VMAF value exists in any of the 21 473 evidence files.

---

## 6. Parameter Analysis

### Key findings

1. **`actual_x265_params` ≡ `effective_params`**, 1638/1638. The "201 missing" is a
   success-path artefact, not loss.
2. **The parameter rule set is fully deterministic** — all six dynamic keys are
   pure functions of source metadata, reproduced **1839/1839** each. There is
   **no experimental variation** in this dataset.
3. **61 of 67 keys are static.** Only `merange`, `rc-lookahead`, `gop-lookahead`,
   `min-keyint`, `vbv-maxrate`, `vbv-bufsize` vary, and each is rule-driven.
4. Three **undocumented** rule behaviours were reverse-engineered and verified:
   `vbv-bufsize` derives from the *rounded* maxrate; a 240 000 kbit/s CPB clamp
   (binds in 353 records); rounding is half-away-from-zero with `ceil` for the fps
   family (half-even and truncate are ruled out).
5. The frozen configs on disk **hash-match** the recorded `config_hash` /
   `scaling_config_hash` for all 1839 records: **no config drift.**

### Dynamic parameters

| Key | Distinct values | Rule |
|---|---:|---|
| `merange` | 4 | `clamp(half_up(57·√(pixels/3840·2160)), 16, 92)` |
| `rc-lookahead` | 7 | `min(ceil(fps·1.5), 200)` |
| `gop-lookahead` | 4 | `min(ceil(fps·0.1), 200)` |
| `min-keyint` | 6 | `ceil(fps·0.15)` |
| `vbv-maxrate` | 1494 | `half_up(clamp(OB·target, OB·min, OB·max))` |
| `vbv-bufsize` | 1149 | `min(half_up(maxrate·3), 240000)` |

### Observed scaling behaviour

The rules behaved as designed. What the corpus did **not** do is exercise them:
`merange` visited only **4** pixel-rate buckets; `keyint`, `bframes`, `ref`, `crf`
were constant; the VBV min/max ratio clamps **never fired**; only `HQ` and CRF 21
were used; **no 4:2:2 encode succeeded**; and **no quality metric was captured at
all**.

### Candidate future A/B (none run; production config unchanged)

P1 `merange` off-trajectory (paired, n=32) · P2 VBV `target_ratio` 0.35/0.50/0.65
(n=30) · P3 CPB clamp saturation (paired, n=24) · P4 throughput determinants with a
newly captured complexity proxy (n=30) · **P5 Sony 4:2:2 regression test (blocked
on the orchestration fix)** · P6 verification-gate coverage measurement (n≥60).

**P5 is the priority**: the defect removed an entire camera/chroma stratum, and the
corpus has zero 4:2:2 successes to calibrate against.

---

## 7. Archive

| Item | Value |
|---|---|
| Archive structure | `1kt_calibrate/archive/` — logical layer; **raw evidence untouched** |
| Manifest | `archive/manifest.json` — file/record counts, date range, evidence hierarchy, per-family sha256 tree fingerprints, collector-manifest cross-check |
| Indexes | **16** (`by_date`, `by_source`, `by_run`, `by_status`, `by_failure`, `by_failure_class`, `by_codec`, `by_resolution`, `by_fps`, `by_source_class`, `by_parameter_completeness`, `by_output_metric_completeness`, `by_verification_status`, `by_project`, `by_bit_depth`, `by_preset`) |
| Timeline | `archive/timeline.json` + `.jsonl` — 1839 rows, local+UTC, `(timestamp_utc, record_id)` ordering |
| Failure archive | `archive/failures.json` (208) + `forensics/failure_fingerprints.json` (5 + 1 unresolved) |
| Parameter archive | `archive/parameters.json` — derived index with per-record provenance and conflict representation |
| Reconstruction dataset | `calibration/reconstructed/` — `records.jsonl`, `sources.json`, `runs.json`, `failures.json`, `duplicates.json`, `conflicts.json`, `missing.jsonl`, `missing.json`, `summary.json` |
| Snapshots | `archive/snapshots/latest/` — derived + manifest only |
| Files written | **43** in `archive/` (68.1 MB); total archive ≈ **71.4 MB** with the snapshot (no media, no binaries) |
| Determinism | `export.py --check` → **identical fingerprint, DETERMINISTIC: True** |
| Traceability | every source → records → `evidence_files`; every failure → chain; every recovered parameter → `sample_file` + `event_id` |

---

## 8. Short Tests

| | |
|---|---|
| **Executed** | **None** |
| **Reason** | No encoder, transcoder, benchmark or media probe was run during this study — not a single invocation of `ffmpeg`, `x265`, `1kt`, `MP4Box` or GPAC. Every conclusion is derived from existing evidence. |
| **Result** | n/a — no short test was needed. Three natural candidates were all satisfied from history: (1) reproducing `FAIL-ORCH-001` was unnecessary because the defect **self-reproduced on 2026-09-28** inside the archived event stream (58 events, 28 failures, PID 10120, `wall_time_sec ≤ 0.063 s`, `output_exists=false`); (2) the "is the metric lost?" question was answered by a **998/998 exact identity** rather than by re-encoding; (3) the "are 73 encodes recoverable?" question was answered by **decoding the rejected event payloads**. |
| Proposed but **not** run | The DJI crash discrimination needs `encoded.mov.ffmpeg.log` from a live encode — recorded as `repro_required: true` with the exact discriminator, **not executed**. |

> **No long-duration test executed.** No encode of any length was run, by the main
> agent or by any of the seven audit agents. Stated explicitly because the
> temptation in a "recover the metrics" task is to re-run the encodes; the
> historical evidence made that unnecessary in every case.

---

## 9. Production Changes

| Artifact | Changed |
|---|---|
| `x265.json` | **NO** (sha256 `0c522f47316bf7d0…`, matches every record's `config_hash`) |
| `x265_scaling.json` | **NO** (sha256 `0cdc44cfa3ec4d8d…`, matches every record's `scaling_config_hash`) |
| `core/` | **NO** |
| `encoders/` | **NO** |
| `1kt.py` | **NO** |
| Production pipeline | **NO** |
| `preservation/`, `production/`, `tests/` | **NO** |
| Audio Sync P2 (`core/channel_sync.py`, `tools/p2/`, `work/p2/`, its docs/tests) | **NO** — never opened |
| Git state | no commit, no push, no merge, no branch change (still `feature/audio-sync-p2-drift`) |

**Added (new files only):** `tools/x265_calibration/reconstruct/{recon,forensics}.py`,
`tools/x265_calibration/archive/{export,materialise_reconstructed}.py`,
`tools/x265_calibration/audit/*` (new scripts from the audit agents),
`work/x265_calibration/**`, `docs/x265-calibration/**`,
`1kt_calibrate/archive/**`, `1kt_calibrate/calibration/reconstructed/**`.

**Raw evidence immutability re-audited after the study:**

| Directory | Files | Newest mtime |
|---|---:|---|
| `calibration/samples` | 10 | 2026-09-25 03:29:06 |
| `calibration/events` | 16 986 | 2026-09-28 16:53:20 |
| `calibration/summaries` | 10 | 2026-09-25 03:29:06 |
| `calibration/errors` | 2 | 2026-09-28 17:04:48 |
| `calibration/state` | 3 | 2026-09-28 17:01:50 |
| `logs/files` | 1914 | 2026-09-28 16:53:52 |
| `logs/failed_details` | 2546 | 2026-09-28 16:53:18 |

**No file written after 18:00 local is under any raw-evidence directory** — only
`archive/` and `calibration/reconstructed/` were created.

---

## 10. Readiness

| Dimension | Verdict | Basis |
|---|---|---|
| **Dataset** | **PARTIALLY_READY** | 1839 records, 100 % parameter provenance, 100 % rule reproducibility, 0 parameter/status/command conflicts, 0 duplicate records, 0 invariant violations, all 100 summary fields reconciled; **plus 73 recovered successes** and **633/633 re-projectable metrics**, taking metric coverage to 100 % of successes. **But** no quality metric exists anywhere; 6.1 % of attempted jobs were never recorded (now recovered); 6 records need quarantine; 1 duplicate-content pair; no rule variation. |
| **Archive** | **READY** | 14/14 consistency checks pass, 100/100 summary fields reconcile, determinism proven byte-for-byte (53 files, `DETERMINISTIC: True`), atomic publish (no empty-archive window), every source/run/failure/parameter traceable to raw evidence, supplement and re-projection layers kept separate and opt-in, size bounded (~69 MB, no media or binaries). |
| **Parameter analysis** | **PARTIALLY_READY** | The rules are **fully characterised** (100 % reproduction, three undocumented behaviours recovered, frozen config hash-verified, single 67-key parameter set, 0 rule violations). Parameter **effects** remain **not measurable** from this dataset: no rule was ever varied, and `merange`/resolution/pixel-rate are collinear at ρ = 0.9996. |

### Why not READY overall

The blockers are **not** the 201 missing parameters (fully resolved) and **not** the
633 null metrics (fully recoverable). They are:

1. **No quality metric was ever captured** — no PSNR/SSIM/VMAF in any of the
   21 473 evidence files. No bitrate/quality trade-off can be evaluated.
2. **No parameter was ever varied** — the dataset is observational and cannot
   attribute an effect to any parameter.
3. **An entire stratum is absent** — 4:2:2: 142 records, **100 % failed**, so there
   is no successful 4:2:2 encode to calibrate against.
4. **The orchestration defect is still live in the deployed 0.7.0 build** (proven
   by the 2026-09-28 events) — further collection with that build will lose the
   same corpus again.
5. **6.1 % of attempted jobs were never recorded** because the collector rejects
   non-filesystem-safe (CJK) job ids — a producer naming defect that will recur
   with any CJK media until fixed.
6. **6 records need quarantine** before any statistical use, and 399 records carry
   a self-contradicting audit block (`final_bufsize_kbps` unclamped).

---

## 11. Next Step

**Can the next phase proceed to controlled short A/B calibration? — Not yet. Two
prerequisites must be satisfied first, and they are cheap.**

### Gate 1 (blocking, cheap): fix and verify the orchestration defect

`FAIL-ORCH-001` must be confirmed fixed **in the build that will be used for
collection**, not merely absent from the current source tree. Verification is a
**sub-minute** check: run the transcoder once against one short C-series clip
(≤ 5 s, 4:2:2, `.MP4`) and confirm that a `COMMAND` line, an `EFFECTIVE_X265`
line, a `DONE` line and a non-empty output appear, and that
`attempt_failed.output_exists` is not `false`.

Until this passes, any A/B collection will silently discard every Sony/`rtmd`
source — the same 201-record loss, repeated.

### Gate 2 (blocking, cheap): close the quality-metric gap

A/B calibration is meaningless without a measurable outcome. The dataset's
`compression_ratio` question is **solved** (100 % coverage of successes after
re-projection), but **no quality metric exists at all** — `verification_result.checks`
is only `["encoder_exit_code_0", "output_file_present_non_empty"]`. Before a
parameter A/B, decide what "better" means and arrange to capture it (PSNR/SSIM/VMAF
on short clips). Also confirm why the verification gate selected by extension
(`.mov` 807/807 vs `.mp4` 191/824) before any cross-format comparison.

### Gate 3 (blocking, cheap): stop the two producer defects

Both are naming/versioning issues, not research problems:

* **unique `attempt_id` per attempt** — the transcoder re-announces the same
  `attempt_id` on every watcher scan, producing 939 `DUPLICATE … NOT persisted`
  rejections and 2443 redundant failure events;
* **filesystem-safe `job_id`** — CJK job ids cost 6.1 % of the sample (74 jobs,
  338 rejected events, 73 completed encodes now recovered by hand). Until fixed,
  any CJK-named media will silently vanish from future calibration data.

### Then: proceed to short A/B

Once the three gates pass, the recommended first experiment is **P5 — the Sony
4:2:2 regression test** (5 4:2:2 clips + 5 matched 4:2:0 controls, 10 short
encodes): it is the only experiment that both validates the fix and fills the
corpus's largest hole. **P1–P3** (parameter A/B) should follow only after the
quality-metric question is settled, using paired same-clip arms so the
`merange`/resolution collinearity cannot be mistaken for an effect.

**Nothing in this study authorises a production configuration change. No profile
or scaling rule may be modified as part of the next phase's preparation.**

---

## 12. Self-audit — limitations of this study's own analysis

Stated plainly so the next reader can discount appropriately.

1. **`true_x265_failure` is unproven for the 6 DJI crashes.** The crashed process
   is `ffmpeg.exe`, and its stderr was not archived. This study reports
   `encode did not complete` (HIGH) but `libx265 at fault` = `UNKNOWN`. Any
   statement that "x265 crashed 6 times" is an over-claim.
2. **The 52 sample-vs-log conflicts are resolved by judgement, not by a decisive
   artefact.** The chosen resolution (`SAMPLE_SIZES`/`SAMPLE_WINS`) is
   well-motivated — the record is the scored attempt and the log's single `DONE`
   line belongs to a later encode with re-measured VBV inputs — but the confirming
   artefact (both command lines side by side) does not exist in the archive.
   Confidence: MEDIUM.
3. **The re-projected metrics are derived, not observed.** `compression_ratio` for
   the 633 comes from `output_size / source_size_bytes`, an identity verified
   **998/998** on records that DO carry the stored value. The identity is exact,
   but a consumer must still state that it used the re-projected layer.
4. **The 73 supplement records never passed collector validation.** Their payload is
   the producer's own event, and `relative_path` is derived from `source_path`.
   Confidence: MEDIUM. They must not be silently counted as if they were normal
   records.
5. **The two scaling-rule reproductions share a conceptual origin.** Both were
   authored within this study against the same `x265_scaling.json`. Agreement is
   strong evidence of internal consistency and of consistency with the recorded
   audit — it is not independent third-party verification.
6. **Nothing was reproduced.** No encoder was run. Therefore no failure family was
   confirmed by live reproduction, and the `repro_required: true` flags on the DJI
   crashes are genuinely open.
7. **The corpus is one project, one preset, one CRF, one host, one day-range.**
   Every statistical statement in this document set is observational and
   non-generalising. Where a coefficient is confounded, it is labelled as such;
   the highest-R² relationship in the corpus (`encode_fps ~ merange`) is also the
   most confounded and must not be quoted as a `merange` effect.
8. **The seven parallel audit agents worked from the same evidence and could share
   a blind spot.** Their findings were cross-checked against the main agent's
   independently built canonical dataset wherever they overlapped; the headline
   counts agree exactly, and two of their findings (the 73 recoverable successes
   and the 633 re-projectable metrics) were verified independently by the main
   agent before being accepted. But a shared misreading of the collector's schema
   would not have been caught by agreement alone. The mitigation is the
   determinism and reconciliation suite, which re-derives every headline number
   from the raw files rather than from another agent's output.
9. **`calibration/reconstructed/` and `1kt_calibrate/archive/` are study-generated
   artefacts living inside the evidence tree.** They are unmistakably named and
   fully regenerable, but a consumer must not mistake them for collector output.
   The raw families (`samples/`, `events/`, `logs/`) were re-audited after the
   study and are byte-unchanged.
