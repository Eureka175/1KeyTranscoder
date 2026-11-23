# Dataset Reconstruction — Identity, Linkage and Reconciliation

**Inputs.** `calibration/samples/*.jsonl` · `calibration/events/archive/**` ·
`logs/files/**` · `logs/failed_details/*.txt` · `calibration/summaries/*.json` ·
`calibration/state/*` · `calibration/errors/*` · `calibration/manifest.json`

**Outputs.** `calibration/reconstructed/` (derived dataset) and
`archive/` (logical archive).

---

## 1. Evidence hierarchy

| Rank | Family | Authoritative for | Weakness |
|---:|---|---|---|
| 1 | `calibration/samples/*.jsonl` | the attempt outcome and its scored metrics | success-path-only fields; `preservation` never populated |
| 2 | `calibration/events/archive/**` | per-event payloads, including params for attempts that never encoded, and the 73 recovered successes | 16 986 files, 6 event types, 14 distinct key-sets |
| 3 | `logs/files/**` | the **full session history per source** (repeat encodes, later re-runs) | append-only; its last line may belong to a later re-run |
| 4 | `logs/failed_details/*.txt` | **not a separate capture** — verified contiguous 40-line tail windows of exactly one parent log | 22 files contain no traceback |
| 5 | `calibration/summaries/*.json` | daily rollups | derived; `failure_reasons` keys are lossy (truncated at 120 chars) |
| 6 | `calibration/state/collector_state.json` | collector bookkeeping, open jobs | internal format |
| 7 | `logs/total.log`, `logs/error.log`, `logs/warn.log` | transcoder-side aggregate, **a completely different bracket-tag vocabulary** (`[SKIP]`, `[FAIL]`, `[PRESERVE-OK]`, `[PROBE-FAIL]`, …) | `error.log` and `warn.log` are **byte-identical**; `warn.log` has zero WARN records |
| 8 | `calibration/errors/rejected_events.jsonl` | the 338 events the collector refused, **with full payloads** — the court for the 73 recovered successes | events that never passed validation |

**Neither `samples` nor `logs` is "the truth" alone.** Concrete disagreements this
study had to resolve:

* `samples` says `preservation: null` while its own `failure_reason` names a
  preservation pipeline → the log is authoritative for preservation detail.
* `logs` say `vbv-maxrate=70152` while `samples` say `78791` for the same source
  → `samples` is authoritative for the *scored attempt* (see
  [`parameter-analysis.md`](parameter-analysis.md) §5).
* For the 201 `TypeError` records, `samples` has the parameters but no `COMMAND`;
  `logs` has neither. The **event archive** is the only family that carries the
  full picture.

---

## 2. Identity model

### Source identity

```
source_key = "sha256:" + source_sha256            if source_sha256 present
           = "path:"   + normalised relative_path otherwise
source_id_stable = "SRC-" + sha256(source_key)[:12]
```

* **`basename` is NOT identity.** 141 `source_id` (basename stem) values collide
  across genuinely different files. Example: `DJI_0783` appears twice —
  `2025.3.1百团大战\DJI_0783.MP4` (184 461 123 B, `sha256 a932baef…`) and
  `2025.9.27迎新晚会彩排\DJI_0783.MP4` (164 786 490 B, `sha256 b6d307d4…`).
  Deduplicating on basename would wrongly delete 288 records.
* **`relative_path` is unique** across all 1839 records.
* **`source_sha256` has exactly one duplicate pair**: `C0051_1.MP4` and
  `C0051_2.MP4` share `sha256:8524e7993a1d688e…` (25 170 662 B), both FAILED.
* **Distinct sources: 1838** (1839 records − 1 collapsed duplicate pair).

### Run identity

`run_id` is the **empty string in all 1839 records** — the collector never
populated it. It is reconstructed as:

```
run_id = "RUN-" + sha256(host, pid, first10(start_time))[:16]
```

| Property | Value |
|---|---:|
| reconstructed runs (this model) | **17** |
| hosts | 1 (`DESKTOP-LG5H5AR`) |
| distinct PIDs carrying records | **10** |

A run is a **transcoder process session**, not a batch definition. PID reuse across
days is why the day component is in the key.

> **"Run" is undefined in the data, and two defensible models disagree.**
> An independent audit derived a **finer** model from the gapless 6-digit sequence
> counter embedded in every `event_id`: **15 producer invocations**, every PID a
> single contiguous `1..N` range with **no PID reuse** and **zero missing sequence
> numbers** (so no producer event was lost), including 2 invocations that produced
> no records at all.
>
> The disagreement is not an error in either method — it is an ambiguity:
> on 2026-09-25 local, **7 different short-lived PIDs** (8324, 4088, 876, 9944,
> 3948, 14276, 13880, 10080) each produced **only failures**, in sequence. It is
> **not decidable from the records** whether those are separate top-level
> invocations or per-job workers under one wrapper. Both numbers are therefore
> reported; `archive/index/by_run.json` uses the `(host, pid, day)` model, and any
> claim about run counts must name the model.

### Attempt identity

| Field | Status |
|---|---|
| `attempt_id` | always `job_id + "-A001"` (1839/1839) |
| `attempt_number` | **1** in every record |
| `attempt_sequence` | **1 on 1677 records, 2 on 162** — not constant (the rule that sets 2 is not identified) |
| `attempt_kind` | `primary` in every record |
| `retry_of_attempt_id` | empty in every record |
| `accepted_attempt_id` | equals `attempt_id` for 1631 successes; **`""` for all 208 failures** |
| `job_attempt_count` | 1 for every record |
| `job_failed_attempt_count` | 0 or 1 |
| `jobs_with_retry` | **0** on all 10 days |

**No retry ever occurred inside the dataset.** But note the distinction that
matters: **the producer re-ran failing jobs many times** — the 201 `TypeError`
sources accumulated 2443 failure events, and the collector rejected 939
re-announcements because `attempt_id` is a pure function of
`(stem, hash8, preset)` and so is *identical* on a re-encode. So there were
thousands of producer retries and **zero collector-level retries**.

`record_id := attempt_id` — a strict 1:1 with the sample record.

> **`attempt_sequence = 2` on 162 records is unexplained.** It does not partition
> cleanly by family, day, status, or source class. Reported as `UNKNOWN` rather
> than guessed.

### Failure identity

```
failure_id = "FAIL-" + sha256(record_id)[:12]
```

---

## 3. Canonical record

```jsonc
{
  "record_id": "<attempt_id>",
  "source":       { "source_key", "source_id_stable", "source_id_reported",
                    "path_raw", "path_normalized", "relative_path", "basename",
                    "sha256", "size_bytes" },
  "run":          { "run_id", "attempt_id", "attempt_number", "attempt_kind",
                    "retry_of_attempt_id", "run_id_reported", "job_id",
                    "host", "pid", "start", "end", "wall_time_sec" },
  "status":       { "record_status", "job_final_status", "exit_code",
                    "failure_stage", "failure_reason", "error_summary",
                    "verification_status", "verification_result",
                    "encoder_format", "backend", "preset", "crf" },
  "source_format":{ width, height, fps, codec, profile, pix_fmt, bit_depth,
                    chroma, duration_sec, ob_kbps, color, streams_raw, ... },
  "classification":{ "source_class", "normalized_ob", "evidence",
                     "scaling_context" },
  "x265":         { "preset", "parameters", "parameter_count",
                    "parameter_provenance", "actual_x265_params",
                    "effective_params", "effective_params_audit", "command",
                    "log_effective_params", "params_log_match",
                    "params_corroboration", "params_event_source", ... },
  "output":       { path, size_bytes, codec, width, height, fps, frame_count,
                    duration_sec, video_bitrate_kbps, compression_ratio,
                    encode_fps, realtime_factor },
  "preservation": null,
  "quality":      { "flags": [ ... ] },
  "provenance":   { "sample_file", "sample_line", "source_event_id",
                    "attempt_started_event_id", "attempt_finished_event_id",
                    "job_final_event_id", "file_log", "evidence_files" },
  "collector":    { "collector_version", "completeness", "project_version",
                    "tool_versions", "warnings", ... }
}
```

Written to `calibration/reconstructed/records.jsonl` (one canonical JSON object
per line, sorted keys, UTF-8, LF).

---

## 4. Timezone — resolved and proven

Log timestamps are **local**; sample and event timestamps are **UTC `Z`**.

Proof: shifting each record's UTC `start_time` by *h* hours and comparing the
result with the day named by the sample file that actually contains it:

| offset | records landing on the file's named day (of 1839) |
|---|---:|
| **UTC+8** | **1831** |
| UTC+9 | 1759 |
| UTC+10 | 1685 |
| UTC+7 | 1674 |
| UTC+11 | 1619 |

**UTC+8 is confirmed.** The remaining 8 records straddle local midnight: the
attempt starts a few minutes before 00:00 local and the job finishes after it —
all 8 start between 23:46 and 23:59 local, one per collection day except 09-13.
They are listed explicitly in
`work/x265_calibration/recon/summary_reconciliation.json` →
`days[].midnight_straddlers`, and are **not** treated as errors.

The canonical record therefore carries `_flat.day_local` (the local calendar day,
used by the samples/summaries) *and* `_flat.day_utc`.

---

## 5. Reconciliation of the three gaps

### 5.1 `records_skipped_duplicate = 939`

Collector totals:

```
events_seen    = 16986      records_written          = 1839
events_valid   = 16648      records_skipped_duplicate =  939
events_invalid =   338      open_jobs_in_memory       =  119
events_buffered=     5
```

**Resolution — the collector states the mechanism verbatim.** `collector.log`
contains exactly **939** `ERROR | DUPLICATE` lines, one per skipped record, e.g.:

```
2026-09-19 13:21:40 | ERROR | DUPLICATE attempt_id C0003-c39c5715-HQ-A001 in job
C0003-c39c5715-HQ (preset 'HQ', backend 'x265') — the existing record was kept and
this one was NOT persisted; the producer must give each attempt a unique id
```

So the mechanism is confirmed, not inferred:

* the **transcoder re-announced the same job with the same `attempt_id`** on every
  watcher scan;
* the **collector de-duplicated at the record level on `attempt_id`**, keeping the
  first record;
* the collector itself calls this a **producer defect** — "the producer must give
  each attempt a unique id".

Corroborating counts:

| Measurement | Value |
|---|---:|
| `collector.log` `DUPLICATE` lines | **939** (= `records_skipped_duplicate`) |
| distinct `event_id` in the archive | 16 986 of 16 986 (0 literal duplicates) |
| `job_started` events for 1839 records | 4254 (2.31 re-announcements per record) |
| `job_started` events for the 201 `TypeError` sources alone | 2443 (≈12 each) |
| `events_duplicate` counter | 0 — de-duplication ran on the **record** key, not `event_id` |
| `jsonl_offsets.json` | records the byte offset consumed per daily file, i.e. the collector re-reads its own output to rebuild its index |

**Incidental finding.** `collector.log` contains **275 711** lines of
`dataset index rebuilt from raw JSONL: 5517 event id(s), 1839 attempt(s)` — the
collector rebuilds its index continuously. This is a **performance defect in the
collector, not a data defect**: the rebuilt counts are stable and correct, and the
manifest totals never changed. It explains the 31.7 MB `collector.log`.

**Conclusion.** The 939 are re-announcements of already-recorded attempts. They
carry no lost information. Note also that the re-announcement is **the same
mechanism that produced the 2443 `TypeError` failure events for 201 records** — the
watcher retried the failing jobs on every scan.

### 5.2 The 75 orphan per-source logs (1914 logs vs 1839 records → 1692 distinct log names)

75 log files match no sample record. Classes:

| Class | Evidence |
|---|---|
| never transcoded during a collected window | log has `SOURCE` but no `DONE`/`FAILED` |
| probe-stage abort, no record emitted | e.g. the `ffprobe found no streams` source |
| recorded outside the dataset window (2026-09-28 batch) | `logs/files/.../DJI_20251014141954_0002_V_HQ.log` session lines dated 09-28 |
| project-level stems not corresponding to a single source | e.g. `3月31日_HQ.log`, `2025.7.3三下乡_HQ.log` |

The example names (`10月17日(1)_HQ.log`, `2025.9.19军训00000000_V1-0012_HQ.log`)
show that some orphan logs belong to media that was reorganised or renamed on the
input side — the log kept the original stem. The `2025.9.19军训…_V1-00xx` family
also appears in `rejected_events.jsonl`, linking the orphan logs to the CJK
filesystem-safety rejection.

**Not every orphan log is informative.** 202 of the 1914 logs contain **no
structured events at all** (only a traceback) — the 201 `TypeError` logs plus one
`PREPROBE FAILED` log. Conversely, 75 logs contain `SOURCE` activity but no record.
So orphans exist in both directions and neither family is silent-by-default.

### 5.3 `events_invalid = 338`

`calibration/errors/rejected_events.jsonl` (338 records) explains them exactly:
the events were rejected because their `event_id` / `job_id` / `attempt_id` were
**"not filesystem-safe"** — practical cause: CJK characters in the job id
(`ifyou转场-df8f6eec-HQ`, `3月31日-…`, `原视频_过调色…`, `迪迦1-…`). Each rejected
entry carries the **full `raw_event`**, including `effective_params`.

Composition: `job_started` 85 · `attempt_started` 85 · `job_succeeded` 73 ·
`attempt_succeeded` 73 · `attempt_failed` 11 · `job_failed` 11.

**Resolution.** These 338 are *recoverable* information that never entered
`samples`, and they correspond to **no** record in the 1839 — the affected sources
(`ifyou转场`, `10月21日`, `12月20日`, `迪迦1-4`, and the `2025.9.19军训…_V1-00xx`
family) appear in the **orphan-log** set described in §5.2. They are retained in
the archive as first-class evidence.

### 5.4 `open_jobs_in_memory = 119`

119 jobs were open in the collector's registry at the last sweep.

**Resolution — a registry size, not a defect count.** `119 ==
len(collector_state.json.jobs)`. Of those 119: **118 carry a terminal
`job_final_event`, and all 119 already have a persisted sample record** (all
FAILED / `encode_failed`; none of the 1631 successes). **Exactly 1 job is
genuinely unfinished** — `DJI_20251014141954_0002_V-935ca0ea-HQ`, whose
`job_started` is the *last event of the whole run*
(`2026-09-28T08:53:20.008Z`, PID 10120) — and even that one already has a recorded
attempt. **No data is lost.** The registry-pruning rule that keeps 119 of 1914 jobs
is `UNKNOWN`.

### 5.5 `events_buffered = 5`

**No data lost.** The arithmetic closes without them: the sum of the collector's
`sweep seen` counters equals the archive file count (16 986) equals the producer's
gapless event total, and the producer's sequence numbers have **zero gaps**. The
word `buffer*` never appears in `collector.log`, and `last_sweep.counters` is all
zeros — so the counter's semantics are `UNKNOWN` and it is contradicted by the
evidence.

Also explained: the only 2 archive events that were neither registered nor
rejected are the **first two events ever emitted** (from the earliest collector
session, which never saved state). That accounts for
`events_valid = 16 648 = 16 646 + 2`.

---

## 6. Event archive completeness

Complete event vocabulary of `logs/files` — **15 tokens**, verified across all
1914 files with zero unclassified lines:

```
SOURCE 1799 · SOURCE_FORMAT 1799 · CLASSIFICATION 1799 · COMMAND 1799
EFFECTIVE_X265 1799 · PROGRESS 73486 · DONE 1704 · SKIP 19087 · POLICY 716
DJI_PIPELINE 3178 · DJI_PRESERVATION_REPORT 633
ENCODE RAISED 2443 (ERROR) · DJI X265 PIPELINE FAILED 81 (ERROR)
FAILED 11 (ERROR) · PREPROBE FAILED 11 (ERROR)
```

**Two corrected assumptions:**

* **`ERROR` is a *level*, not an event.** The event token is the text after the
  level (`ENCODE RAISED`, `FAILED`, `PREPROBE FAILED`, …). `logs/files` levels:
  INFO 107 799 / ERROR 2546 / WARN 0.
* **`PREPROBE FAILED` is a real event token** and had been omitted from the
  working vocabulary. It appears 11 times, is the cause of the single
  event-less-non-TypeError log, and links to `FAIL-COL-001`.

Line grammars: **13 distinct**, EBNF in
`archive/reports/log-schema.md`. Two file-level notes worth knowing: `logs/files`
and `logs/failed_details` are CRLF while `logs/total.log` and `collector.log` are
LF (two different writers); and `collector.log` contains **8 384 NUL bytes in two
holes** that swallowed record separators, producing the corpus's only 8 353-character
line — invisible to any UTF-8 decode check, and the reason a naive line-oriented
reader can silently mis-parse the tail of that file.

| Measurement | Value |
|---|---:|
| event files | 16 986 |
| distinct `event_id`s | **16 986** (0 collisions) |
| event-type totals | `job_started` 4254 · `attempt_started` 4243 · `job_succeeded` 1704 · `attempt_succeeded` 1704 · `job_failed` 2546 · `attempt_failed` 2535 |
| distinct event-key variants (schemas) | **14** |
| events with `effective_params` | 16 964 |
| events with `command` | **1796** |
| events with `preservation` | **0** (but 1266 carry `preservation_summary`) |
| record-referenced events missing from the archive | **0** |
| events belonging to the 201 `TypeError` sources | 2443 (start/fail events only — never a command or progress) |
| events dated 2026-09-28 (post-window) | 58 |
| events the collector rejected | 338, of which 146 are **successes** → 73 recovered records |

All four event ids referenced by a record (`source_event_id`,
`attempt_started_event_id`, `attempt_finished_event_id`, `job_final_event_id`)
resolve to an existing archive file for **all 1839 records** — verified by
`recon.py verify` check `record_event_refs_present` (0 missing).

### 6b. The 75 record-less jobs — resolved

75 jobs produced per-source logs (74 of them with a `DONE` line) and events, but
no sample record. They are explained:

* **74** were rejected by the collector as *"not filesystem-safe"* — their
  `job_id` contained CJK characters (`ifyou转场`, `原视频 过调色…`, `10月21日活力杯`,
  `12月20日(1)`, `迪迦1`, `2025.9.19军训…_V1-00xx`, …). 338 events were rejected in
  total; 146 of them are `job_succeeded`/`attempt_succeeded`.
* **73 of those** are therefore **complete, successful transcodes that were never
  recorded** — recovered into the supplement (see §2b of the consolidated report
  and `calibration/reconstructed/records_supplement.jsonl`).
* **1** is unexplained: `DJI_20260331160404_0380_D_ZX1-bab7a9b6-HQ`, whose source
  failed 11 times with `ffprobe found no streams` (`FAIL-COL-001`) and produced no
  record.

The root cause is a **producer naming defect**, not a collector bug: the collector
correctly refuses ids that cannot become safe filenames. Cost: **6.1 % of attempted
jobs**, systematically the CJK-named media.

The **14 distinct key-sets** are the schema-variant count: event types do not share
one schema (`job_started` carries `source_info` and `relative_path`;
`attempt_failed` carries `output_exists`; `job_succeeded` carries
`output_profile` and `compression_ratio`; the collector warned
`unknown field 'preservation_summary' (kept as-is)` **1266 times**). A parser must
not assume a single event schema — this is the finding that makes the
`EFFECTIVE_X265` recovery path, the `command`-presence test, and the 73-success
recovery all meaningful.

---

## 7. Summary reconciliation — 100 fields, 0 discrepancies

Every daily summary field was recomputed from the records:

| Field | Days checked | Discrepancies |
|---|---:|---:|
| `records` | 10 | 0 |
| `attempts.succeeded` / `attempts.failed` | 10 | 0 |
| `jobs.total` | 10 | 0 |
| `presets` | 10 | 0 |
| `backends` | 10 | 0 |
| `source_classes` | 10 | 0 |
| `failure_reasons` (normalised signature + count) | 10 | 0 |
| `failure_stages` | 10 | 0 |
| `completeness.incomplete` | 10 | 0 |
| partition rule | 10 | 0 |

Totals: summaries cover **1839/1839** records, **1631** succeeded, **208** failed —
identical to the raw sample line count.

> Both the raw comparison of `failure_reasons` **keys** and the raw byte
> comparison of reasons fail spuriously: the collector truncates long multi-line
> reasons at an unspecified cut point. The check therefore compares the failure
> **signature** (exception class + collapsed prefix) and the count. This is
> reported rather than worked around silently.

---

## 8. Deterministic reconstruction

| Requirement | Status |
|---|---|
| stable source / run / attempt / record / failure ids | yes (§2) |
| stable sort order | yes — records by `(start_time, attempt_id)`; sources by `source_key`; failures by `(timestamp, record_id)` |
| stable JSON serialisation | yes — UTF-8, LF, sorted keys, indent 2 for documents, one object per line for JSONL |
| no wall-clock inside data | yes — no `generated_at` field is written into any derived artefact |
| two runs identical | yes — `export.py --check` rebuilds into a temp directory and compares content fingerprints: **DETERMINISTIC: True** (49 files, fingerprint match) |
| reproducible statistics | yes — every headline number in these documents is emitted by `recon.py`, `forensics.py` or `export.py` |

Re-run the proof:

```powershell
python tools\x265_calibration\archive\export.py --check
# archive files: 49 fingerprint: c6e2847bad34ef11
# rebuilt files: 49 fingerprint: c6e2847bad34ef11
# DETERMINISTIC: True
```

---

## 9. Residual unknowns

| Item | Status |
|---|---|
| Which commit the deployed `1kt.exe` came from | `UNKNOWN` — the portable root `D:\1KT-x265-portable` no longer exists, and the recorded `project_version` contradicts the traceback line numbers |
| Why the probe-failure source produced no sample record | `UNKNOWN` — the collector's emission rule for pre-probe aborts was not recovered |
| Content of the 5 buffered events | their events are in the archive; no dataset impact |
| Whether the 338 rejected events describe work not present in `samples` | `INSUFFICIENT_EVIDENCE` — they carry full payloads but no matching record or log |
