# x265 Calibration — Historical Reconstruction, Failure Forensics & Archive

**Scope.** This document set covers the completed x265 calibration collection run
whose evidence lives in `F:\1KeyTranscoder\1kt_calibrate`. It reconstructs the
historical dataset, performs forensic analysis of every failure, recovers
parameter provenance, audits data quality, and installs a structured long-term
archive.

**Period covered.** 2026-09-13 → 2026-09-28 (dataset records 2026-09-13 →
2026-09-25; failure evidence to 2026-09-28).

## Headline results

| Question | Answer |
|---|---|
| Raw records | **1839** (1631 SUCCEEDED / 208 FAILED) |
| Distinct sources | **1838** (one duplicate-content pair collapses) |
| Reconstructed runs | **17** (host × PID × day) |
| Records missing `actual_x265_params` | **201** — **not data loss**; all 201 carry a complete 67-key `effective_params` |
| Parameter recovery | **201 / 201 recovered** (`RECOVERED_FROM_EVENT`), 0 not recoverable |
| **Unrecorded encodes recovered from rejected events** | **73** — complete successes with output metrics, absent from `samples` |
| Failures that are real encoder-side failures | **6** (plus 1 preservation failure and 11 probe failures outside the 208) |
| Failures that are *not* x265 failures | **201** — a Python orchestration defect (`FAIL-ORCH-001`) aborting before any encoder process starts |
| Production configuration changed | **No** |
| Long-duration tests executed | **No** |

## The three findings that reframe the dataset

**1. The 201 "missing parameters" are not missing data.** `actual_x265_params` is a
flattened serialisation of `effective_params`, and the identity holds on
**1638/1638** records that have both. All 201 carry a complete 67-key parameter
vector plus the full computation audit. They also have no `COMMAND`, no
`PROGRESS`, no `DONE`, and `wall_time_sec ≤ 0.016 s` — because they are 201
attempts that aborted inside Python before any encoder was spawned:

```
TypeError: encode_one_sony() got an unexpected keyword argument 'video_entry'
```

**2. The dataset is missing 73 completed encodes that were never lost.**
The collector rejected 338 events because their job ids contained CJK characters
and failed a "filesystem-safe" check. Those rejected entries carry the **full
payloads**, and 73 of them are *successful* transcodes with output size,
compression ratio, encode fps, parameters and audit. None of their 73 sources
appear in the base dataset at all. They are recovered into a clearly-flagged
**supplement** dataset (`archive/dataset/records_supplement.jsonl`), never merged.

| Population | Records | With `compression_ratio` | Distinct sources |
|---|---:|---:|---:|
| Base (`calibration/samples`) | 1839 | 998 | 1838 |
| **Supplement (recovered)** | **73** | **73** | **73** |
| Combined *if counted* | **1912** | **1071** | **1911** |

**3. A 28.5-hour hung encode consumed the machine after the collection window.**
`logs/files/校团宣/2026.3.31挑战杯决赛/视频/3月31日_HQ.log` holds 12 sessions of the
same 3840×6826 clip: 11 aborted with `STATUS_ACCESS_VIOLATION`
(`return=3221225477`, 0 frames), and session 12 then ran **9 940 `PROGRESS`
records over 102 522.75 s (28.5 h) at `frames=0/1739`**, ending with no terminal
event. It started at 2026-09-25 04:39:14 local — after the last dataset record —
so the whole hang is outside the dataset, and it is the only session that leaves a
log without a terminal event.

## Documents

| Document | Contents |
|---|---|
| [`dataset-reconstruction.md`](dataset-reconstruction.md) | identity model, canonical record, reconciliation, the 939/75/338/119 gaps |
| [`failure-forensics.md`](failure-forensics.md) | failure taxonomy, evidence chains, failure fingerprints, what is and is not an x265 failure |
| [`parameter-analysis.md`](parameter-analysis.md) | parameter census, dynamic vs static keys, scaling rule verification, the 201 recovery |
| [`scaling-analysis.md`](scaling-analysis.md) | how the dynamic rules behaved against source properties |
| [`data-quality.md`](data-quality.md) | missing / duplicate / conflict / orphan / invalid, verification coverage split |
| [`archive-structure.md`](archive-structure.md) | the archive layout, indexes, manifest, timeline, how to query it |
| [`reconstruction-report.md`](reconstruction-report.md) | consolidated run report and dataset readiness verdict |

## Reproducing everything

```powershell
cd F:\1KeyTranscoder
python tools\x265_calibration\reconstruct\recon.py all             # discover/events/logs/canonical/reconcile/verify
python tools\x265_calibration\reconstruct\forensics.py             # failure taxonomy + evidence chains
python tools\x265_calibration\archive\materialise_reconstructed.py # calibration/reconstructed/
python tools\x265_calibration\archive\recover_rejected.py          # +73 unrecorded successes
python tools\x265_calibration\archive\reproject_metrics.py         # +633 re-projected metrics
python tools\x265_calibration\archive\export.py                    # build the archive (atomic publish)
python tools\x265_calibration\archive\export.py --check            # byte-identical rebuild proof
```

Last verified: `verify` **14/14 checks pass** (0 discrepancies), `reconcile`
**100/100 daily-summary fields recomputed** (0 discrepancies),
`export --check` **53 files, identical fingerprint, `DETERMINISTIC: True`**.

> Run `--check` only when no file under `docs/x265-calibration/`,
> `work/x265_calibration/forensics/` or `calibration/reconstructed/` is being
> edited: it compares the published archive against a fresh rebuild, so an edit in
> flight reports `DETERMINISTIC: False` for a reason that is not a determinism
> defect.

## Hard boundaries observed

* No modification to `x265.json`, `x265_scaling.json`, `core/`, `encoders/`,
  `1kt.py`, or the production pipeline. `x265.json` and `x265_scaling.json` still
  hash-match (`0c522f47…`, `0cdc44cf…`) the `config_hash` recorded in all 1839
  records.
* No long-duration encode of any kind. **No encoder process was invoked in this
  study at all** — not `ffmpeg`, not `x265`, not `MP4Box`, not `GPAC`.
* No file under `1kt_calibrate` was moved, renamed or deleted. Raw evidence
  modification times were re-audited after the study and are unchanged; the only
  files newer than the collection are the study's own derived outputs in
  `calibration/reconstructed/` and `archive/`, which are unmistakably named and
  fully regenerable.
* The Audio Sync P2 work (`core/channel_sync.py`, `tools/p2/`, `work/p2/`,
  audio-sync docs/tests) was not touched.
* Git: no commit, no push, no merge, no branch change (still
  `feature/audio-sync-p2-drift`).
