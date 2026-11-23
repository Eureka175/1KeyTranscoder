# Archive Structure — How to Navigate `F:\1KeyTranscoder\1kt_calibrate`

This document is the maintainer's guide. Six months from now, this is the only
file you should need to read to understand the calibration workspace without
re-reading thousands of logs.

---

## 1. The one-paragraph version

`1kt_calibrate\` is a **preserved copy** of a portable transcoder workspace whose
original root (`D:\1KT-x265-portable`) no longer exists. Underneath it,
`calibration\` holds the **collected dataset**, `logs\` holds the **per-source
evidence**, `archive\` holds a **logical index layer** built on top (no raw file
was moved), and `calibration\reconstructed\` holds the **derived canonical
dataset**. `archive\manifest.json` is the entry point: it lists every file count,
every content fingerprint and every evidence-hierarchy decision. Read the archive,
never the raw logs, until you need to prove something.

> **Nothing under `1kt_calibrate` was moved, renamed or deleted.** Historical
> absolute paths inside the logs (`D:\1KT-x265-portable\...`) are deliberately
> preserved, because future parsers and past log lines depend on them.

---

## 2. Layer model

```
F:\1KeyTranscoder\1kt_calibrate\
│
├─ calibration\                        ── RAW ────────── (read-only, never move)
│  ├─ samples\            10 files · 16.7 MB    THE DATASET: 1839 records
│  ├─ events\archive\     16986 files · 90.9 MB  every collector event (10 day dirs)
│  ├─ events\pending\     empty                  the collector's inbox
│  ├─ events\failed\      empty                  validation failures go here
│  ├─ summaries\          10 files · 71 KB       per-day rollups (derived)
│  ├─ errors\             collector.log (31.7 MB) + rejected_events.jsonl (2.1 MB)
│  ├─ state\              collector bookkeeping + jsonl_offsets.json
│  ├─ manifest.json       the collector's own manifest
│  ├─ README_FIRST.txt    the collector's own documentation
│  └─ reconstructed\      ── DERIVED ── canonical dataset (regenerable)
│
├─ logs\                               ── RAW ────────── (read-only, never move)
│  ├─ files\校团宣\<project>\<stem>_<PRESET>.log
│  │                      1914 files · 17.2 MB  append-only per-source history
│  ├─ failed_details\     2546 files · 4.2 MB   windowed failure captures
│  ├─ total.log / error.log / warn.log / dashboard.json
│  ├─ env_versions.json / env_versions.csv
│  ├─ failed_files.json / scaling.csv / preprobe*.csv / postprobe*.csv
│
├─ archive\                            ── INDEX + REPORT + SNAPSHOT ──
│  ├─ README.md                        (this file's installed copy)
│  ├─ manifest.json                    ★ start here
│  ├─ sources.json                     one entry per distinct source (1838)
│  ├─ runs.json                        17 reconstructed run sessions
│  ├─ failures.json                    208 failures + class/subclass
│  ├─ parameters.json                  derived parameter index + provenance
│  ├─ timeline.json / timeline.jsonl   chronological attempt timeline
│  ├─ index\                           16 queryable projections
│  ├─ reports\                         rendered reports
│  ├─ forensics\                       failure taxonomy, chains, fingerprints
│  ├─ dataset\records.jsonl            canonical dataset (27 MB)
│  └─ snapshots\latest\                frozen derived+manifest copy
│
└─ bin\ tools\ work\                   ── NOT ARCHIVED ── ~630 MB binaries
```

### Layer contract

| Layer | Location | Mutability | Regeneration | Invalidated by |
|---|---|---|---|---|
| **RAW** | `calibration/samples`, `calibration/events`, `calibration/summaries`, `calibration/errors`, `calibration/state`, `logs/**` | **immutable** | never — this is the evidence | nothing (adding new collection is a *new* dataset version) |
| **DERIVED** | `calibration/reconstructed/` | regenerable | `tools/x265_calibration/archive/materialise_reconstructed.py` | any change to RAW |
| **INDEX** | `archive/*.json`, `archive/index/` | regenerable | `tools/x265_calibration/archive/export.py` | any change to DERIVED |
| **REPORT** | `docs/x265-calibration/`, `archive/reports/` | hand-authored | re-authored | a material finding change |
| **SNAPSHOT** | `archive/snapshots/latest/` | frozen copy | delete + re-export | deliberately manual |

**Never write to RAW.** Never treat DERIVED or INDEX as evidence: they are
recomputable, and if they disagree with RAW the RAW wins and the pipeline has a
bug.

---

## 3. Evidence hierarchy

When two families disagree, this is the precedence:

| Rank | Family | Authoritative for |
|---:|---|---|
| 1 | `calibration/samples/*.jsonl` | the **attempt outcome** and its scored metrics |
| 2 | `calibration/events/archive/**` | **per-event payloads**, including parameters for attempts that never encoded |
| 3 | `logs/files/**` | the **full session history of a source** (repeat encodes, later re-runs) |
| 4 | `logs/failed_details/*.txt` | windowed failure captures around a specific failure |
| 5 | `calibration/summaries/*.json` | daily rollups (derived, can be stale) |
| 6 | `calibration/state/*` | collector bookkeeping |
| 7 | `logs/total.log`, `logs/error.log` | transcoder-side aggregate |

* **"samples are the only truth" is wrong** — `preservation` is `null` in all 1839
  records even when the failure reason names a preservation pipeline.
* **"logs are the only truth" is wrong** — the per-source log is append-only, so
  its last `EFFECTIVE_X265` may belong to a *later* re-run (52 records).

Worked example of the hierarchy in action: for the 201 `TypeError` records,
`samples` has the parameters but no `COMMAND`, `logs` has neither, and only
`events/archive` carries the complete picture. Using any single family alone
produces the wrong conclusion.

---

## 4. Identity model (deterministic, collision-analysed)

```
source_key       = "sha256:" + source_sha256                      # 1838 values
                 = "path:"   + normalised relative_path (fallback)
source_id_stable = "SRC-"  + sha256(source_key)[:12]
run_id           = "RUN-"  + sha256(host, pid, start[:10])[:16]   # 17 values
attempt_id       = <job_id> + "-A001"                             # 1839 values
record_id        = attempt_id
failure_id       = "FAIL-" + sha256(record_id)[:12]               # 208 values
```

| Pitfall | Consequence if ignored |
|---|---|
| using **basename** as identity | 141 stems collide across 288 records — dedup would delete 288 valid rows |
| using **`run_id` from the record** | it is the *empty string* in all 1839 records |
| expecting **retries** | `attempt_number` is 1 and `attempt_kind` is `primary` everywhere; there are no retries |
| expecting a **`source_id`** for every event | `job_started`/`job_failed` events carry no `source_id`; use `job_id` |

---

## 5. Manifest — `archive/manifest.json`

| Field | Meaning | Invariant |
|---|---|---|
| `manifest_version`, `archive_version`, `schema_version`, `parser_version` | format identification | must match the producing tools |
| `dataset_version` | `samples-2026-09-13..2026-09-25` | the RAW samples are unchanged |
| `generated.deterministic` | `true` | **no wall-clock is written anywhere** |
| `raw_evidence_root` | `F:\1KeyTranscoder\1kt_calibrate` | the preserved copy |
| `raw_evidence_root_reported_by_collector` | `D:\1KT-x265-portable\calibration` | the **original**, now non-existent root — kept for provenance honesty |
| `file_counts.*` | per-family file counts | must equal a fresh directory walk |
| `bytes.*` | per-family byte totals | — |
| `record_counts.*` | 1839 records / 1631 succeeded / 208 failed / 1838 sources / 17 runs / 208 failures / 16986 events / 1914 logs / 2546 captures | must equal `samples` line counts |
| `date_range.days` | the 10 active days | `day_gaps` (`09-16`, `09-17`, `09-18`) are *no activity*, not loss |
| `hash_policy` | sha256 over RAW, plus a content fingerprint; media never hashed or copied | — |
| `excluded_from_archive` | `bin/`, `tools/`, `input/`, `output/`, `work/` | the archive stays small |
| `evidence_hierarchy` | the ranked family list (§3) | — |
| `totals_reported_by_collector` | the collector's own counters | compared against reality by `recon.py verify` |
| `content_fingerprints.*` | tree hashes of samples / summaries / events / file logs / failed_details | **tamper detection** |

### Archive size budget

| | Bytes |
|---|---:|
| RAW evidence referenced (not copied) — 21 466 files | **123.0 MB** |
| Archive **actually written** — 49 files | **68.2 MB** |
| … `dataset/records.jsonl` | 26.1 MB |
| … `parameters.json` | 10.2 MB |
| … snapshot copy (`snapshots/latest/`) | 16.4 MB |
| … forensics | 6.4 MB |
| … indexes (16 files) | 2.7 MB |
| … reports (9 files) | 0.2 MB |
| Bundled binaries **excluded** | ~630 MB (`bin/`, `tools/`) |
| Media **excluded** | unbounded (`input/`, `output/`) |

The archive is ~55 % of the raw metadata volume it indexes, and negligible next to
a single 4K source file. It does not grow with media volume.

---

## 6. Indexes — `archive/index/`

| File | Key | Cardinality | Example questions it answers |
|---|---|---:|---|
| `by_date.json` | local calendar day | 10 | *What ran on 2026-09-22? How many succeeded that day?* |
| `by_source.json` | `source_key` | 1838 | *Which records belong to this file? What is its hash and full evidence chain?* |
| `by_run.json` | `run_id` | 17 | *Which recorder session produced this record?* |
| `by_status.json` | `SUCCEEDED` / `FAILED` | 2 | *Give me all failures.* |
| `by_failure.json` | `failure_id` | 208 | *Show the evidence chain for this one failure.* |
| `by_failure_class.json` | `ORCHESTRATION` / `X265_ENCODE` / `PRESERVATION` / `UNKNOWN` | 4 | *Which failures are real encoder failures?* |
| `by_codec.json` | source codec | few | *Which sources are h264 vs hevc?* |
| `by_resolution.json` | `WxH` | few | *Which sources are 4K?* |
| `by_fps.json` | fps string | ~7 | *Which sources are 4K60?* |
| `by_source_class.json` | `HIGH_BITRATE_LONG_GOP` / … | 3 | *Where are the high-bitrate long-GOP sources?* |
| `by_parameter_completeness.json` | `complete` / `partial` / `none` + provenance | 3 + 2 | *Which records have complete parameters? Which were recovered?* |
| `by_output_metric_completeness.json` | has / lacks `compression_ratio` | 2 | *Which records can support ratio analysis?* |
| `by_verification_status.json` | `passed` / `skipped` / `not_run` | 3 | *What did the verification gate do?* |
| `by_project.json` | top-level project | few | *Per-project coverage.* |
| `by_bit_depth.json` | 8 / 10 | 2 | *Bit-depth coverage.* |
| `by_preset.json` | `HQ` | 1 | *Was any other preset used?* (no) |

Every index value carries the record ids **and**, through `by_source` /
`by_failure`, the `evidence_files` list — so any answer is traceable to a raw file.

### Reverse lookup (index → record → evidence)

```
index/by_date.json            -> record_ids
records.jsonl (record_id)     -> provenance.evidence_files[]  (sample file + per-source log)
archive/parameters.json[rid]  -> evidence.{sample_file,event_id,event_file,file_log}
archive/forensics/evidence_chains.json -> the per-failure chain
```

---

## 7. Timeline — `archive/timeline.json` / `.jsonl`

One row per attempt:

```jsonc
{ "timestamp_utc": "2026-09-13T16:19:55.916Z", "day": "2026-09-14",
  "record_id": "...", "source_key": "...", "relative_path": "...",
  "run_id": "...", "event": "attempt_succeeded", "status": "SUCCEEDED",
  "failure_id": null, "failure_class": null, "failure_subclass": null,
  "wall_time_sec": 2019.672, "source_class": "NORMAL_LONG_GOP",
  "resolution": "3840x2160" }
```

**Timezone policy.** Both are stored: `timestamp_utc` (from the record) and `day`
(the **local** calendar day, UTC+8, which is what the sample files and daily
summaries use). The `day` here is authoritative for grouping; `timestamp_utc` is
authoritative for ordering.

**Ordering guarantee.** Sorted by `(timestamp_utc, record_id)`, stable.

### The collection history

| Local day | Records | Succeeded | Failed | Note |
|---|---:|---:|---:|---|
| 2026-09-13 | 125 | 125 | 0 | collection begins 07:35 UTC |
| 2026-09-14 | 287 | 282 | 5 | first `TypeError` (4) + the GpacError record |
| 2026-09-15 | 28 | 28 | 0 | — |
| 2026-09-16 … 09-18 | 0 | 0 | 0 | **no activity** (gap, not loss) |
| 2026-09-19 | 34 | 31 | 3 | — |
| 2026-09-20 | 174 | 174 | 0 | — |
| 2026-09-21 | 130 | 130 | 0 | — |
| 2026-09-22 | 176 | 167 | 9 | first DJI encoder crashes |
| 2026-09-23 | 279 | 250 | 29 | — |
| 2026-09-24 | 426 | 422 | 4 | — |
| **2026-09-25** | 180 | 22 | **158** | the watcher's long final re-scan of the C-series backlog; last dataset record 19:29 UTC |
| **2026-09-28** | **0** | 0 | — | **58 events, no sample record** — the deployed defect reproducing after the dataset was closed |

Totals: 1839 / 1631 / 208 ✔

What a gap means: the watcher was not running, or nothing was newly added to
`input\`. It is **not** missing data — the daily summaries for those days do not
exist at all, and `records_written` reconciles exactly.

---

## 8. Failure archive — `archive/failures.json`

Per failure: `failure_id, record_id, source_key, source_id_stable,
source_id_reported, run_id, timestamp, stage, reason_raw, error_summary,
exit_code, x265_started, command_present, effective_params_present,
output_exists_documented, parameter_available, parameter_provenance,
evidence_files, failure_class, failure_subclass, failure_component, confidence`.

Fingerprint matching contract (`archive/forensics/failure_fingerprints.json`):
apply the patterns **in order**; the first match wins. Each pattern is an anchored
regular expression over the collapsed failure text, verified to match all 2535
observed failure events with zero overlap and zero unmatched. New errors that
match nothing fall through to `UNKNOWN` / `INSUFFICIENT_EVIDENCE` — **never**
force a classification.

---

## 9. Parameter archive — `archive/parameters.json`

**A DERIVED index, never an override.** Raw `samples` files are untouched.

Per record: `preset, parameter_count, parameter_provenance, parameters,
corroboration, conflicts{...}, evidence{sample_file,event_id,event_file,file_log},
dynamic_values{...}, effective_params_audit`.

`parameter_provenance` values: `SAMPLE_ACTUAL_X265_PARAMS` (1638) ·
`SAMPLE_EFFECTIVE_PARAMS` (201) · `SAMPLE_COMMAND` · `LOG_EFFECTIVE_X265` ·
`LOG_COMMAND` · `EVENT_EFFECTIVE_PARAMS`.

Conflict representation: `conflicts.actual_x265_params_missing`,
`conflicts.command_missing`, `conflicts.log_effective_params`,
`conflicts.log_params_match` (`exact` 1586 / `differs` 52 / `null` 201),
`conflicts.event_params` (event id).

---

## 10. Snapshot policy

`archive/snapshots/latest/` contains **derived JSON + manifest only**:

```
manifest.json  sources.json  runs.json  failures.json
parameters.json  timeline.json  timeline.jsonl
```

* **Never snapshotted:** media, encoder output, bundled binaries, `input/`,
  `output/`, `bin/`, `tools/`.
* **Regenerate:** delete `snapshots/latest/` and re-run `export.py`.
* **Retention:** one `latest` snapshot by convention. Add dated snapshot
  directories only when a dataset version is frozen for publication.

---

## 11. Verification / consistency contract

The archive **must fail loudly**, never silently reconcile. Every check below is
run by `recon.py verify` (14 checks) and `recon.py reconcile` (100 fields):

| Check | Failure semantics |
|---|---|
| every record traces to a sample file + per-source log | `records_with_evidence_link != 0` → FAIL |
| every source_key in `sources.json` appears in `records.jsonl` | symmetric difference must be 0 |
| every failure has ≥1 evidence file | count must be 0 |
| every recovered parameter has provenance | `params_event_source` or `file_log` required |
| every statistic recomputable | `recon.py` modes re-emit the same numbers |
| index ↔ dataset agreement | index record-id sets are subsets of the dataset |
| manifest ↔ reality | `file_counts`, `record_counts`, `date_range` re-walked |
| summary ↔ records | 100 daily fields recomputed; **0 discrepancies** |
| hash stability | `content_fingerprints` stable across runs |
| determinism | `export.py --check` rebuilds and compares fingerprints |

**If `archive says 1839 but raw evidence has 1830`, the check FAILS and the
archive is not published.** The reconciliation that produced this archive found
**0** such discrepancies, so the "1839 vs 1830" class of silent drift does not
exist here.

---

## 12. Determinism contract

| Aspect | Rule |
|---|---|
| sorting | records by `(start_time, attempt_id)`; sources by `source_key`; failures by `(timestamp, record_id)`; events by `(timestamp, event_file)`; logs by relpath |
| JSON | UTF-8, LF line endings, `sort_keys=True`, indent 2 for documents, one object per line for JSONL |
| dynamic fields | **none** — no `generated_at`, no wall-clock, nothing time-dependent |
| ids | pure hashes of content/keys (§4) |
| proof | `python tools\x265_calibration\archive\export.py --check` |

Last verified result: **49 files, identical fingerprint
`c6e2847bad34ef1159c026df…`, `DETERMINISTIC: True`.**

> **Operational note.** Never edit a file that `export.py` reads (anything under
> `docs/x265-calibration/`, `work/x265_calibration/forensics/`,
> `1kt_calibrate/calibration/reconstructed/`) between the export and the `--check`.
> The check compares the export against a fresh rebuild, so an edit in flight makes
> it report `DETERMINISTIC: False` for a reason that is not a determinism defect.

---

## 13. Query recipes

### Which day had the most failures?
```powershell
$i = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\index\by_date.json -Raw | ConvertFrom-Json
$i.PSObject.Properties | ForEach-Object { [pscustomobject]@{day=$_.Name; failed=$_.Value.failed; n=$_.Value.record_count} } |
  Sort-Object failed -Descending | Format-Table
```

### What ran on 2026-09-22?
```powershell
(Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\index\by_date.json -Raw | ConvertFrom-Json).'2026-09-22'
```

### Was any source encoded more than once?
```powershell
$s = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\sources.json -Raw | ConvertFrom-Json
$s | Where-Object { $_.record_count -gt 1 } | Select-Object relative_path, record_count
```

### Which failures are genuinely x265/encoder failures?
```powershell
$f = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\failures.json -Raw | ConvertFrom-Json
$f | Where-Object { $_.failure_class -eq 'X265_ENCODE' } | Measure-Object   # -> 6
```

### How many failures are orchestration-only?
```powershell
($f | Where-Object { $_.failure_class -eq 'ORCHESTRATION' }).Count            # -> 201
```

### Which sources are 4K60?
```powershell
$r = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\dataset\records.jsonl |
     ForEach-Object { $_ | ConvertFrom-Json }
$r | Where-Object { $_.source_format.height -eq 2160 -and $_.source_format.fps -gt 59 } |
     Select-Object -Expand source_id_reported -Unique
```

### Which records have complete parameters?
```powershell
(Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\index\by_parameter_completeness.json -Raw |
  ConvertFrom-Json).complete.record_count                                        # -> 1638
```

### Recompute the success rate for any day
```powershell
$r = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\dataset\records.jsonl |
     ForEach-Object { $_ | ConvertFrom-Json }
$r | Group-Object { $_.provenance.sample_file } | ForEach-Object {
  $ok = ($_.Group | Where-Object { $_.status.record_status -eq 'SUCCEEDED' }).Count
  [pscustomobject]@{file=$_.Name; n=$_.Count; ok=$ok; rate=[math]::Round($ok/$_.Count,4)} }
```

### Full evidence chain for one failure
```powershell
$fp = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\forensics\evidence_chains.json -Raw | ConvertFrom-Json
$fp.chains | Where-Object { $_.failure_id -eq 'FAIL-PRES-003' } | ConvertTo-Json -Depth 8
```

### Where did a specific source's parameters come from?
```powershell
$p = Get-Content F:\1KeyTranscoder\1kt_calibrate\archive\parameters.json -Raw | ConvertFrom-Json
$p.'C0002-2e9ecde4-HQ-A001' | Select-Object parameter_provenance, evidence, corroboration
```

### Show every record that is missing actual_x265_params
```powershell
Get-Content F:\1KeyTranscoder\1kt_calibrate\calibration\reconstructed\missing.jsonl |
  ForEach-Object { ($_ | ConvertFrom-Json) | Select-Object record_id, recovery_class, recovered_parameter_count }
```

### Recompute every headline number from scratch
```powershell
cd F:\1KeyTranscoder
python tools\x265_calibration\reconstruct\recon.py all
python tools\x265_calibration\reconstruct\forensics.py
python tools\x265_calibration\archive\export.py --check
```

### Find the raw log for a record
```powershell
($r | Where-Object { $_.record_id -eq 'DSC_5381-0fadea91-HQ-A001' }).provenance | Select-Object sample_file, file_log
```

### Which sources never produced a sample record?
```powershell
# compare log basenames against record log names
$logs = Get-ChildItem -Recurse F:\1KeyTranscoder\1kt_calibrate\logs\files -Filter *.log |
        Select-Object -Expand Name
$have = $r | ForEach-Object { $_.provenance.file_log_name }
Compare-Object $logs $have | Where-Object SideIndicator -eq '<='      # -> 75 orphans
```

### Verify the archive has not drifted from raw evidence
```powershell
python tools\x265_calibration\reconstruct\recon.py verify     # 14 checks
python tools\x265_calibration\reconstruct\recon.py reconcile  # 100 fields
```

---

## 14. Maintainer onboarding — the six-month walkthrough

1. **Where is the raw evidence?** `1kt_calibrate\calibration\` (dataset + events)
   and `1kt_calibrate\logs\` (per-source logs + failure captures). Do not modify.
2. **Where is the dataset?** `calibration\samples\*.jsonl` — 1839 records, ten
   daily files, one immutable record per encode attempt. **This is the data.**
3. **Where is the rebuilt dataset?** `calibration\reconstructed\records.jsonl`
   (canonical, regenerable) and `archive\dataset\records.jsonl` (a copy).
4. **Where is the archive?** `1kt_calibrate\archive\`. Start at `manifest.json`.
5. **What is the manifest?** The inventory: file counts, record counts, date
   range, evidence hierarchy, and per-family content fingerprints for tamper
   detection.
6. **What are the indexes?** Pre-computed projections (`archive\index\`) that
   answer common questions without scanning 16986 events. See §6.
7. **How do I locate one source?** `index\by_source.json` → `source_key` → the
   record ids → `provenance.evidence_files`.
8. **How do I locate one failure?** `index\by_failure.json` (by id) or
   `index\by_failure_class.json` (by class) → `forensics\evidence_chains.json`.
9. **How do I locate one run?** `index\by_run.json` → `run_id` → records →
   `runs.json` for the session boundaries.
10. **How do I regenerate every statistic?** Run the four commands in §13's last
    "recompute" recipe, then `export.py --check` to prove determinism.
11. **What must I NOT touch?**
    * `calibration\samples\`, `calibration\events\`, `logs\**` — raw evidence;
    * `bin\`, `tools\` — ~630 MB of bundled binaries, never archived;
    * `x265.json`, `x265_scaling.json`, `core\`, `encoders\`, `1kt.py` —
      production, frozen.
12. **What if the archive and the raw evidence disagree?** Stop. The raw evidence
    wins, the archive is stale or buggy, and `recon.py verify` should be showing
    a failure. Re-run the pipeline; do not hand-edit `archive\`.
13. **What is the single most important thing to know about this dataset?**
    201 of the 208 "failures" are a Python orchestration defect with no encoder
    involvement, and `actual_x265_params` is missing on exactly those 201 because
    it is a success-path serialisation of `effective_params`, which is present.
