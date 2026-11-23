# Data Quality — Missing, Duplicate, Conflict, Orphan, Invalid

**Policy.** Nothing was deleted, repaired or "cleaned". Raw evidence under
`1kt_calibrate` was re-audited after the study and every file's modification time
is unchanged. Anomalies are recorded as `quality_flags` on a derived layer only.

Full machine-readable detail:
`1kt_calibrate/archive/dataset/records.jsonl` → `quality.flags`,
`1kt_calibrate/calibration/reconstructed/conflicts.json`,
`1kt_calibrate/calibration/reconstructed/duplicates.json`.

---

## 1. Completeness (measured)

| Field group | Present | Total | Rate |
|---|---:|---:|---:|
| `effective_params` | 1839 | 1839 | **100 %** |
| `effective_params_audit` | 1839 | 1839 | **100 %** |
| `classification` / `scaling_context` | 1839 | 1839 | **100 %** |
| `source_info` | 1839 | 1839 | **100 %** |
| `source_sha256` | 1839 | 1839 | **100 %** |
| `actual_x265_params` | 1638 | 1839 | **89.1 %** (the 201 — see below) |
| `command` | 1638 | 1839 | 89.1 % |
| `output_size` | 1631 | 1839 | 88.7 % |
| `wall_time_sec` | 1839 | 1839 | 100 % |
| `compression_ratio` | 998 | 1839 | **54.3 %** |
| `encode_fps` | 998 | 1839 | 54.3 % |
| `preservation` block | **0** | 1839 | **0 %** |
| `warnings` non-empty | 0 | 1839 | 0 % |
| `run_id` (reported) | **0** | 1839 | **0 %** |

### The two completeness gaps — both recoverable without re-encoding

**(a) `compression_ratio` / `encode_fps` are stored on only 998 of 1839 (54.3 %).**
This is a **storage** gap, not a measurement gap:

| Metric | Stored | Recoverable by re-projection | Total |
|---|---:|---:|---:|
| `compression_ratio` | 998 | **633** | **1631 / 1631 = 100 %** |
| `encode_fps` | 998 | **633** | **1631 / 1631 = 100 %** |

`compression_ratio` **is exactly** `output_size / source_size_bytes` — verified
**998/998 exact, 0 mismatches** on the records that already carry it.
`encode_fps` derives from `output_frame_count / wall_time_sec` (928/998 within
0.01; the residual is the definitional difference between job-level
`wall_time_sec` and the encoder's own `elapsed`).

For all 633, the ratio is independently present in two other courts — the
per-source log's `DONE ratio=` field and `logs/postprobe.csv` — and cross-court
agreement is **633/633 EXACT**. Recovered into
`calibration/reconstructed/metrics_reprojected.jsonl` and
`archive/index/by_metric_reprojection.json`, **never merged** into the base
records.

What the gap still costs: any analysis that must read the metric directly from the
record, and — because `verification_result.checks` is empty for these 633 — any
quality-gate claim. It costs nothing in throughput, size or bitrate analysis.

**(b) The `preservation` block is `null` in all 1839 records — but the data
exists.** 1266 archived events carry a `preservation_summary` for exactly the 633
skipped-verification jobs (`PRESERVED` 18 230/2 · `MODIFIED` 4/2 · `MISSING` 0/0),
and the collector logged `unknown field 'preservation_summary' (kept as-is)`
**1266 times**. This is a **producer/consumer schema divergence**, not a collection
gap, and it too is recoverable from the event archive by re-projection.

### (c) A self-contradicting audit block on 399 records

`effective_params_audit["vbv-maxrate"]["final_bufsize_kbps"]` reports the
**unclamped** buffer (e.g. 260 301) while the applied
`effective_params["vbv-bufsize"]` is correctly `240000`. Any consumer reading that
audit field over-states the VBV buffer for **21.7 %** of the corpus. Trust
`effective_params`, not the audit block's `final_bufsize_kbps`.

---

## 2. Duplicates

| Kind | Count | Detail |
|---|---:|---|
| Identical `source_sha256` | **1 pair** | `C0051_1.MP4` = `C0051_2.MP4`, `sha256:8524e7993a1d688e…`, 25 170 662 B, both FAILED |
| Identical `relative_path` | 0 | unique across all 1839 |
| Identical `attempt_id` / `record_id` | 0 | unique |
| Identical `event_id` | **0** | 16 986 distinct ids in 16 986 files |
| Identical `(sha256, path)` | 0 | — |
| Colliding `source_id` (basename stem) | **141 stems / 288 records** | **not duplicates** — different recordings sharing a filename |
| Collector-reported `records_skipped_duplicate` | 939 | re-announcements, no lost information |

**The 141 collisions are the trap.** Example — `DJI_0783`:

| path | size | `sha256` |
|---|---:|---|
| `2025.3.1百团大战\DJI_0783.MP4` | 184 461 123 | `a932baefab27…` |
| `2025.9.27迎新晚会彩排\DJI_0783.MP4` | 164 786 490 | `b6d307d4c6dd…` |

Deduplicating on `source_id` would wrongly delete **288 records**. Deduplicating on
`source_sha256` removes exactly **1**, so the dedup replications in the statistics
are **no-ops** (998 → 998).

---

## 3. Conflicts (111 total, none silently resolved)

| Kind | Count | Severity | Resolution |
|---|---:|---|---|
| `PARAMS_SAMPLE_VS_FILE_LOG` | **52** | HIGH | `SAMPLE_WINS` — the per-source log is append-only and its last `EFFECTIVE_X265` belongs to a later watcher re-run whose dynamic VBV inputs were re-measured |
| `COMMAND_PAYLOAD_SAMPLE_VS_LOG` | **52** | MEDIUM | same 52 records and the same two keys — the `-x265-params` payload in the log's `COMMAND` line carries the later re-measured VBV values |
| `PRESERVATION_FIELD_NULL_BUT_FAILURE_MENTIONS_PRESERVATION` | 7 | MEDIUM | `SCHEMA_GAP` — the collector never populates the block |
| `PARAMS_ACTUAL_VS_EFFECTIVE` | 0 | — | identity holds 1638/1638 |
| `SUCCEEDED_WITHOUT_OUTPUT_SIZE` | 0 | — | — |
| `FAILED_WITH_OUTPUT_SIZE` | 0 | — | — |
| `VERIFICATION_SKIPPED_BUT_METRICS_PRESENT` | 0 | — | — |
| **total** | **111** | 52 HIGH / 59 MEDIUM | |

The 52 `PARAMS_SAMPLE_VS_FILE_LOG` cases differ in **exactly two keys**,
`vbv-maxrate` and `vbv-bufsize` — both functions of the source's measured `OB`.
Example `DSC_2980`: sample `vbv-maxrate=78791 / vbv-bufsize=236373`, log
`70152 / 210456`. The 52 `COMMAND_PAYLOAD_*` entries are the **same records**
observed through a second field (the log's `COMMAND` line), which corroborates
the diagnosis rather than adding a new class.

**Consequence for analysis.** For those 52 records, the per-source log must not be
used to re-derive the record's parameters, and conversely, the sample record's
parameters describe the attempt that was actually scored.

---

## 4. Orphans (both directions)

| Kind | Count | Note |
|---|---:|---|
| Per-source logs with no matching record | **75** | **74 are the CJK rejection population** (see below); 1 unexplained probe-failure source |
| … of those, with a `DONE` line (i.e. a completed encode) | **73** | recovered into the supplement — see [`dataset-reconstruction.md`](dataset-reconstruction.md) §2b |
| Per-source logs with **no structured events at all** | **202** | the 201 `TypeError` logs + 1 `PREPROBE FAILED` log |
| Records with no per-source log | **0** | every record has one |
| Records with no log **and** no event evidence | **0** | verified (`records_with_evidence_link` = 0) |
| `failed_details` captures with no matching record | **22** | includes the project-level stem `3月31日` |
| Rejected events with no matching record | **338** | CJK filesystem-safety rejections; they carry full payloads, and 73 are recoverable successes |
| Jobs with events but no record | **75** | 74 rejected-as-not-filesystem-safe + 1 unresolved (`DJI_20260331160404_0380_D_ZX1-bab7a9b6-HQ`) |
| Events referencing a missing record | 0 | all four record event refs resolve |
| Orphan summary entries | 0 | — |

The orphan logs and the rejected events are **the same population**: their job ids
contain CJK characters (`ifyou转场`, `原视频 过调色…`, `10月21日活力杯`,
`12月20日(1)`, `迪迦1`, …), which fails the collector's filesystem-safe check. This
is **6.1 % sample loss** and it was systematic, not random.

> **`source_id` is sanitised.** `Timeline 2` → `Timeline_2`, `2025.10.16` →
> `2025_10_16`. Joining on `source_id` manufactures phantom orphans; join on
> `relative_path` or the content hash instead.

---

## 5. The verification gap — measured and explained

`verification_status` distribution over the 1839 records:

| Status | Count |
|---|---:|
| `passed` | 998 |
| `skipped` | 633 |
| `not_run` (= all failures) | 208 |

The 633 `skipped` records have `verification_result.notes =
"verification not reached yet"`, and they are exactly the records with **null**
`compression_ratio` and `encode_fps`.

### It is not random, and it is not duration-gated

| Extension | `passed` | `skipped` | `not_run` |
|---|---:|---:|---:|
| `.mov` | **807** | 0 | 0 |
| `.mp4` | 191 | **633** | 208 |

Cramér's V = **0.788**, χ² = 1013.1, p ≈ 2.5e-222, n = 1631.

Two plausible documented mechanisms were tested and **falsified**:

* *"sources longer than 60 s are skipped"* — **18 `passed` records exceed 60 s**
  (max 216.4 s), and the skipped group's duration median is marginally *shorter*;
* *"a deterministic hash-based 1-in-N sample"* — no hash rule beats chance.

So the `quality_check` block in `x265.json` **did not govern this run**; the gate
is an orchestration path selection correlated with the source container.

### What that makes unsafe

* Treating the n=998 `encode_fps` / `compression_ratio` population as
  representative of the corpus;
* extrapolating those to whole-project throughput;
* any `.mov`-vs-`.mp4` cross-family comparison of quality or speed.

### What remains safe

* all n=1631 `wall_time_sec` / `output_size` / output-bitrate analysis;
* all n=1839 rule-conformance and failure analysis;
* within-extension comparisons.

The residual within-`.mp4` rule is **not identified** and is reported as
unexplained rather than guessed.

---

## 6. Invalid metadata and plausibility checks

| Check | Result |
|---|---|
| `SUCCEEDED` with null `output_size` | **0** |
| `FAILED` with non-null `output_size` | **0** |
| `end_time` before `start_time` | 0 |
| `wall_time_sec ≤ 0` with output present | 0 |
| negative or zero source size | 0 |
| fps ≤ 0 or NaN in `source_info.fps` | 0 |
| width/height 0 | 0 |
| `compression_ratio` outside (0, 1] | 0 |
| `encode_fps ≤ 0` | 0 |
| timestamps outside the collection window | 0 (2026-09-13 → 2026-09-25 records; events to 2026-09-28) |
| non-UTF8 or control characters in string fields | none found; CJK renders correctly |
| `presets` value other than `HQ` | 0 |
| `backend` other than `x265` | 0 |
| parameter key-set variants | **1** (all records use the same 67 keys) |
| parameter values contradicting the frozen rules | **0** |

Notable **legitimate** oddities that are not defects:

* `duration_sec = 0.0` appears on some very short clips — real, not a parse error.
* One source is `3840×6826` (an unusual geometry, `3月31日.mp4`); the pipeline
  handled it and the record is internally consistent.
* 8 records are assigned to the sample file of the **local** day the job began
  rather than of the attempt start — the collector's partition rule (see
  [`dataset-reconstruction.md`](dataset-reconstruction.md) §4).

---

## 7. Coverage gaps that matter for a future calibration

| Gap | Measurement |
|---|---|
| **No successful 4:2:2 encode** | 142 records are `chroma=4:2:2` and **100 % of them failed** (all 142 are `TypeError` records). **0 successful 4:2:2 encodings exist.** |
| **No `INTRA_LIKE` source class** | 0 of 1839 |
| **No CRF variation** | CRF 21 only |
| **No preset variation** | `HQ` only |
| **No quality metric at all** | No PSNR / SSIM / VMAF / quality CSV in any record (field-scanned) |
| **`merange` visited only 4 values** | i.e. only 4 distinct pixel-rate buckets |
| **Thin strata** | 12 strata with n < 5 (13 records); only 6 strata reach n ≥ 30; top-3 strata are 80.6 % of the corpus |
| **No within-source repeat** | 0 sources encoded twice under identical rules with both outcomes recorded |

---

## 8. Failure contamination

| Statement | Value |
|---|---:|
| Raw failed records | 208 |
| … that are an orchestration defect with no encoder involvement | **201 (96.6 %)** |
| … that evidence an encode that started and did not complete | **6 (2.9 %)** |
| … that are a preservation failure after a substantially complete encode | **1 (0.5 %)** |
| Failed sources **not** represented by any record | ≥ 1 (11 probe-failure events) |

**A naive failure rate of 208/1839 = 11.3 % is meaningless.** The defensible
figures are 6/1638 = **0.37 %** encoder-side non-completions and 1/1638 = **0.06 %**
preservation failures.

**Selection-bias note.** Failure is not independent of source properties: the
`TypeError` family is exactly the Sony/`rtmd` C-series population (all `h264`,
`HIGH_BITRATE_LONG_GOP`, and all 142 `4:2:2` sources). Any analysis that drops
failures silently will therefore drop an entire camera family and the entire
4:2:2 chroma stratum. Failures must be reported as a **coverage** property, not
merely excluded.

---

## 9. Usability split

| Class | Records | Can support | Cannot support |
|---|---:|---|---|
| **Fully usable** — parameters + output metrics + verification `passed` | **998** | throughput, output size, bitrate, compression ratio; rule conformance | parameter effects (no variation); quality (no metric) |
| **Partially usable** — parameters + `output_size` / `wall_time_sec` but no stored ratio | **633** | throughput and size analysis; rule conformance; source-metadata analysis; **compression ratio and encode fps after re-projection (633/633 recoverable)** | using the stored metric as-is; any PSNR/SSIM quality gate; any preservation claim from the record |
| **No analytical value** — failures with no output | **208** | failure taxonomy and provenance; coverage accounting | any output or performance metric |

Totals: 998 + 633 + 208 = **1839** ✔

Plus **73 recovered supplementary successes** (see
[`dataset-reconstruction.md`](dataset-reconstruction.md) §2b) that carry
parameters, output metrics and a ratio — population 1839 → **1912 if counted**.

### The ceiling applies to everyone

**No record in this dataset contains a real quality measurement.**
`verification_result.checks` is only
`["encoder_exit_code_0", "output_file_present_non_empty"]`, and there is no PSNR /
SSIM / VMAF / quality CSV anywhere in the 21 473 evidence files. Every conclusion
this corpus can support is about **bitrate, size, throughput and rule
conformance** — never about visual quality.

---

## 10. Verification of the quality claims

`recon.py verify` — 14 checks, all pass:

```
raw_records                1839 = 1839
raw_succeeded              1631 = 1631
raw_failed                  208 =  208
raw_missing_actual_params   201 =  201
failures_file               208 =  208
failures_have_evidence        0 =    0
source_keys_match          1838 = 1838
source_keys_orphan            0 =    0
records_with_parameters    1839 = 1839
records_with_evidence_link    0 =    0
manifest_records           1839 = 1839
manifest_sample_files        10 =   10
manifest_archive_events   16986 = 16986
record_event_refs_present     0 =    0
```

`recon.py reconcile` — **100** daily-summary fields recomputed, **0 discrepancies**,
all 1839 records covered by a summary, 8 midnight-straddling assignments recorded
explicitly.

---

## 11. Residual unknowns

| Item | Status |
|---|---|
| The within-`.mp4` verification rule (§5) | **not identified** — reported, not guessed |
| Why the collector re-builds its index continuously (275 711 log lines) | `UNKNOWN` — cosmetic/performance, no data impact |
| Whether the 338 rejected events describe work absent from `samples` | `INSUFFICIENT_EVIDENCE` |
| Content of the 5 buffered events | events present in the archive; no dataset impact |
| Whether the 22 unmatched `failed_details` captures belong to the orphan-log sources | `MEDIUM` confidence — the stem namespace collides |
