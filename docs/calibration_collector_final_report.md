# x265 Calibration Collector — Final Report

**Task:** build an offline, headless, standalone x265 calibration data
collection system for a ~1 month / 500 GB+ soak on a Windows host with no
external network.
**Scope discipline:** this session performed **no parameter optimization**.
The collector only observes and persists. Nothing in it can influence a
production encode.

---

## 0. Archive Status

```
ARCHIVED FOR LONG-TERM SOAK

Branch:    feature/x265-calibration-collector
Purpose:   offline long-term calibration data collection
Baseline:  v0.7.0 / 1a41fff
HEAD:      2813fcb  (08f75eb, 5e850ff, 1294a44, 2813fcb
                     on top of 1a41fff)
Status:    READY FOR 1-MONTH SOAK
Merged to main: NO — and it must not be merged
主开发线:  main

Deployable artifact (this is what goes to the soak host):
  dist/x265_release/x265-calibration-collector-v0.7.0.zip   (7.63 MiB)
  rebuild: python tools/x265_calibration/release/make_zip.py
  verify : python tools/x265_calibration/tests/zip_distribution.py
  exe sha256: 7bc90332b5a24cb1125223e3d87bc441d98730904fd057f6b33114442003ad84
```

The collector is **not** part of `main`'s default production execution path.
It is an opt-in research/experiment tool that lives only on this branch.

| Boundary | Rule |
| --- | --- |
| Process model | Collector and Production Transcoder are two **independent sibling processes**. Neither launches the other. |
| Control | The collector does **not** participate in any production decision. |
| Retry | The collector does **not** retry. Retry belongs entirely to production. |
| Transcoding | The collector does **not** transcode, decode, or run ffmpeg/x265. |
| Failure isolation | A collector failure **must not block production**. A missing, crashed or disk-full collector can only ever mean "calibration data incomplete". |
| Data path | The collector only **consumes the filesystem spool** and writes `samples/`. It never writes back to production. |

**One-month intent.** The dataset collected by this branch will be brought
back to `main` after the soak and used for the *next* session's x265
parameter / scaling work (`x265_scaling.json`, source classification, scaling
ratios, scaling caps, dynamic VBV, `merange`, lookahead, and parameter
behaviour across resolution / FPS / bitrate). No optimization is done here.

**This branch is not the 1.0.0 development line.** New features, Audio Drift
and all 1.0.0 mainline work continue on `main`. This branch is frozen and is
only used for:

1. the 1-month offline real-data soak,
2. calibration data analysis,
3. fixing a **blocking** collector defect if one appears during the soak.

**Status wording.** `READY FOR 1-MONTH SOAK` is deliberately **not**
`production ready`. Nothing here has been exercised for a month on real
data yet; see §13 "Unverified / residual risk".

### Note on divergence from current `main`

The three lines forked from **`b3245b7`** ("docs: 记录分支归档约定"),
which is `main`'s current tip-minus-two:

```
                          b3245b7  (main's ancestor)
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   main (62c5504)    feature/hardware-      feature/x265-calibration-
                     decode-integration     collector (1294a44)
                     (ca72e09)              = v0.7.0 + calibration
   = b3245b7 + 2 licensing commits
```

| Direction | Content |
| --- | --- |
| `main` has, this branch does not | 2 commits, licensing reorganisation only: `015bee8`, `62c5504` (`LICENSE`, `NOTICE`, `licenses/GPL-3.0.txt`, `README.md`, `docs/README.md`, `release/build_release.py`, `release/verify_package.py`) |
| This branch has, `main` does not | **12 commits**: the 10-commit v0.7.0 / hardware-decode line (`17ab8a7` … `1a41fff`, 47 files / ~20k insertions) **plus** this session's 3 calibration commits |

Two consequences that matter for archival:

1. **`main` is still on `VERSION` 0.6.2**, and v0.7.0 is *not yet merged into
   `main`*. This branch is therefore based on the released v0.7.0 tag, which
   currently lives only on `feature/hardware-decode-integration` and here.
   That is correct and intended for a soak host — it runs the released
   version — but it means **neither v0.7.0 nor the collector is in `main`
   yet**.
2. **Do not merge this branch into `main`.** Doing so would drag the whole
   hardware-decode line into `main` as a side effect of "archiving the
   collector", which is exactly what this task forbids. The collector is not
   a delivery vehicle for unrelated work.

If the collector is ever revived for new development, rebase it onto the
then-current `main` at that time — and decide the v0.7.0/hwdecode merge
question separately, on its own merits.

---

## 1. Branch and commit range

```
Branch:        feature/x265-calibration-collector   (from v0.7.0, NOT main,
               NOT feature/hardware-decode-integration)
Commit range:  1a41fff (v0.7.0 "chore(release): bump version to 0.7.0")
               .. 5e850ff (fix(calibration): unique job identity per
                            (source, preset))
               08f75eb  feat(calibration): offline x265 calibration
                        collector + standalone exe
               5e850ff  fix(calibration): unique job identity per
                        (source, preset); log duplicate attempt ids as errors
Baseline tag:  1a41fff == VERSION 0.7.0 (== refs/tags/v0.7.0)
                NB: main is still at VERSION 0.6.2 and does not yet contain
                v0.7.0 — see "Note on divergence from current main" in §0
Merged to main: NO (deliberately out of scope for this session)
Push state:    local only; origin has main + feature/hardware-decode-integration
```

Changed this session (21 files, +9923/-125):

| Area | Files |
| --- | --- |
| Production (observer only) | `core/calibration_events.py` (new), `1kt.py`, `core/batch_hw.py`, `core/postprobe.py` |
| Collector (standalone) | `tools/x265_calibration/collector/{__init__,collector_core,collector_cli}.py` |
| Contract | `tools/x265_calibration/schema.json` |
| Analyzer | `tools/x265_calibration/analyze.py` |
| Packaging | `tools/x265_calibration/release/{x265_calibration_collector.spec,frozen_entry.py,build_standalone.py,verify_frozen_exe.py,deploy_helpers.py}` |
| Deployment docs/config | `tools/x265_calibration/{README.txt,collector.toml}` |
| Tests | `tools/x265_calibration/tests/{smoke_pipeline,robustness,dep_matrix,ladder_integration}.py` |
| Repo hygiene | `.gitignore` (tools/x265_calibration is now tracked) |

Second commit on the same branch fixes an identity defect found by the
regression sweep below:
`fix(calibration): unique job identity per (source, preset) — preset-all
collision`.

---

## 2. Collector architecture

### Process model

Two **sibling** processes; neither launches the other, no RPC, no shared
memory, no stdout/stderr coupling.

```
┌────────────────────────────────────┐
│        Offline Windows Host        │
│                                    │
│  ┌──────────────────────────────┐  │
│  │ Production Transcoder (1kt)  │  │
│  │ probe → classify → scaling   │  │
│  │ → x265 → verify → output     │  │
│  └──────────────┬───────────────┘  │
│                 │ atomic event files
│                 ▼                  │
│      calibration/events/pending/   │
│                 │                  │
│  ┌──────────────┴───────────────┐  │
│  │ Calibration Collector (exe)  │  │
│  │ watch → validate → persist   │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

* Production never waits for, reads from, or checks the collector.
* The collector never starts ffmpeg/x265, never decodes video, never
  re-encodes, never retries, never repairs, never reclassifies.
* Both are independent scheduled tasks; either can start/stop/restart
  without affecting the other.

### Event transport

Local filesystem spool under one calibration root, because that is the only
persistent boundary that survives crashes on both sides:

```
calibration/
├── events/pending/           producer → collector (atomic rename in)
├── events/archive/<day>/     consumed events, verbatim, for auditing
├── events/failed/            invalid events + <name>.error.txt
├── samples/YYYY-MM-DD.jsonl  THE dataset (authoritative, append-only)
├── summaries/YYYY-MM-DD.json daily rollups (always rebuildable)
├── errors/collector.log
├── errors/rejected_events.jsonl
├── state/                    consumer state (offsets, buffer, seen ids, lock)
└── manifest.json
```

* Producer write is atomic: `.tmp` → write → flush → fsync → close →
  `os.replace()`. The collector only consumes final names; `.tmp`/dot files
  are invisible to it (verified: DEP-06).
* Producer write is **best effort**: on failure it logs
  `[CALIBRATION] telemetry failure … (production continues; calibration data
  incomplete)` and production proceeds unchanged.

### Lifecycle

```
job_started          proof the job existed + full production snapshot
  attempt_started    one per REAL encode attempt, own attempt_id
    attempt_succeeded  only after encoder success AND production
                       verification passed AND output committed
    attempt_failed     failure_stage/reason/exit_code/output/verification
job_succeeded        final state SUCCESS (+ accepted attempt id/number)
job_failed           final state FAILED
```

### Collector pipeline (per sweep)

```
1. schema validation    (required fields, types, enums, timestamps, ids)
2. atomicity check      (.tmp ignored, torn file quarantined)
3. persist              one immutable record into samples/<day>.jsonl (fsync)
4. mark consumed        archive the event under events/archive/<day>/
```

Plus: state recovery on start (pending backlog, byte offsets, open-job
buffer, dataset-derived id index), daily summary regeneration, manifest
counters, and an advisory single-instance lock.

---

## 3. Event lifecycle (as implemented)

| Event | Written when | Key contents |
| --- | --- | --- |
| `job_started` | after probe/classify/scaling, before any encode | job_id, source_id, source_sha256, source_path, relative_path, project_version, start_time, requested_preset, requested_encoder, full `source_info` / `classification` / `scaling_context` / `effective_params` / `effective_params_audit`, config + scaling config hashes |
| `attempt_started` | before each real encode attempt | job_id, attempt_id, attempt_number, backend, preset, start_time, attempt_kind, trigger, the rebuilt command line, encoder format/reader, source + classification + scaling snapshot |
| `attempt_succeeded` | **after** production verification and output commit | status=SUCCEEDED, end_time, wall_time_sec, exit_code, `verification_result`, output size/duration/width/height/bitrate/frames/fps/pix_fmt, compression_ratio, encode_fps, ffmpeg + libx265 versions, the literal `-x265-params` |
| `attempt_failed` | on any failed attempt (kept forever) | status=FAILED, failure_stage, failure_reason, exit_code, output_exists/output_size, verification_status, error_summary, tool versions |
| `job_succeeded` | all attempts done, ≥1 accepted | status=SUCCESS, accepted_attempt_id/number, attempt_count, wall_time_sec |
| `job_failed` | all attempts failed | status=FAILED, failure_stage/reason, attempt_count, failed_attempt_count |

`failure_stage` ∈ `startup_failed | encode_failed | verification_failed |
output_commit_failed | probe_failed | unknown`. Production's existing reason
text is recorded verbatim; the collector never re-classifies it.

**"Transcode complete" is defined as:** encoder success **AND** production
output verification passed **AND** output committed/accepted. Never
`rc == 0` alone, never "output file exists", never "ffmpeg produced
packets". Concretely, in `encode_one()` the success event is emitted only
after the quality gate (when `--check full`), the channel-sync step and the
final `os.replace(part_dst, dst)`, with the output facts taken from
production's own post-probe.

---

## 4. Retry model

Retries belong **entirely to production**. The collector only records them.

```
job J
 ├── attempt A001  hardware / primary       FAILED   (kept, with stage+reason)
 └── attempt A002  software / retry         SUCCEEDED
job final state: SUCCESS, accepted = A002
```

Event order produced: `job_started`, `attempt_started A001`,
`attempt_failed A001`, `attempt_started A002`, `attempt_succeeded A002`,
`job_succeeded`.

Nothing overwrites A001: each attempt gets a new `attempt_id`
(`<job_id>-A<nnn>`), a new `attempt_number` and a new event file, and each
becomes its own immutable JSONL line. `attempt_kind` distinguishes
`primary | retry | downgrade | reader_fallback |
hardware_decode_fallback`, and `retry_of_attempt_id` / `trigger` record why.

Verified against **real production retries**, not just synthetic events:

* hardware downgrade ladder (NVEncC, real encode): A001 `FAILED` format
  `4:2:0/8` → A002 `SUCCEEDED` format `4:2:0/10`, trigger
  "downgrade ladder rung 2/2" (`tests/ladder_integration.py`, PASS).
* hardware-decode integrity fallback and reader (MP4Box strip) fallback are
  instrumented the same way in `hw_encode_with_fallback()`.

Analytical consequences retained for the future session: hardware failure
rate, software fallback rate, failure class, the specific inputs that caused
a retry, and job-level recovery rate.

---

## 5. Failure handling

* Every failed attempt produces `attempt_failed` with a distinct
  `failure_stage`, production's own reason text, exit code, whether an
  output existed and its size, and the verification verdict.
* A job whose attempts all fail produces `job_failed`; a job with at least
  one accepted attempt produces `job_succeeded`. The collector never
  invents or overrides either state — it derives them from what production
  recorded.
* If a job never reached a decision (production killed), the record is
  still persisted after a bounded wait (`--job-final-timeout`, default
  900 s) with `completeness.job_finished_seen = false` instead of a
  fabricated outcome. `--drain-buffered` does the same explicitly at the
  end of the soak.
* If telemetry itself fails mid-attempt, production's log gets the
  telemetry warning and the job is recorded as `unknown`, never as a
  production failure.
* Collector-side faults never stop production: schema errors quarantine the
  event (with a reason file and a rejected-event log), disk-full keeps the
  event pending, a crash leaves it pending, and a concurrent instance is
  refused by the lock.

---

## 6. Production impact

| Property | Status |
| --- | --- |
| Parameter / preset / profile / scaling / bitrate changes | none — the emitter has no write path into production |
| Retry, re-encode, repair, reclassify by the collector | none |
| Production waits for the collector | never (best-effort, async file writes) |
| Collector absent / crashed / disk-full | production unaffected (tested) |
| Hostile spool (events path is a file) | production still exits 0, emits telemetry warnings (tested) |
| Overhead when disabled | module import + a boolean check per job |
| Overhead when enabled | one hash of the source, one JSON write per event, one cached `ffmpeg -version` + one cached libx265 probe per process |
| Video decode / encode / ffmpeg work by the collector | none |

---

## 7. Standalone packaging

```
technology:      PyInstaller 6.22.2, single-file, console, UPX disabled
entry:           release/frozen_entry.py  (NOT collector_cli.py — see below)
executable:      dist/x265_calibration/x265-calibration-collector.exe
size:            8,152,101 bytes (7.77 MiB)
sha256:          7bc90332b5a24cb1125223e3d87bc441d98730904fd057f6b33114442003ad84
bundled:         Python 3.11.9 runtime + stdlib; schema.json at the bundle
                 root; collector package. 0 site-packages entries, no
                 numpy/scipy (explicitly excluded), no ffmpeg, no tool DLLs.
release package: dist/x265_release/x265-calibration-collector/ containing
                 the exe, README.txt, schema.json, collector.toml, VERSION,
                 tools/analyze.py, tools/verify_frozen_exe.py,
                 tools/deploy_helpers.py, SHA256SUMS.txt

deployable zip:  dist/x265_release/x265-calibration-collector-v0.7.0.zip
                 (7.63 MiB) — THE artifact to hand to the soak host. On a
                 machine with no Python it extracts into ONE folder that is
                 complete and self-describing:

                   START-HERE.txt                one-paragraph pointer
                   QUICKSTART.zh-CN.txt          Chinese quick start
                   README.txt                    full English document
                   x265-calibration-collector.exe
                   START-COLLECTOR.bat           start (foreground)
                   CHECK-INSTALL.bat             read-only self test
                   INSTALL-SCHEDULED-TASK.bat    run at boot
                   UNINSTALL-SCHEDULED-TASK.bat  stop running at boot
                   schema.json / collector.toml / VERSION
                   PACKAGE-CONTENTS.txt          size + SHA-256 of every file
                   SHA256SUMS.txt
                   calibration/                  data dir, pre-created empty,
                                                 README in pending/ explains
                                                 that its content is producer
                                                 input and is never counted
                   tools/                        dev-machine tools only

                 Built by tools/x265_calibration/release/make_zip.py (also
                 reachable as `deploy_helpers.py make-zip`). Deterministic
                 content, fixed timestamps, DEFLATE. The pre-created
                 calibration/ directory removes one manual step from the
                 deployment and makes the `ONEXKT_CALIBRATION_SPOOL` value
                 obvious.
```

Two packaging findings worth recording:

1. **Freezing `collector_cli.py` directly produces a dead binary.**
   `collector_cli` uses package-relative imports, and PyInstaller runs the
   entry script as `__main__` with no parent package:
   `ImportError: attempted relative import with no known parent package`.
   `pathex`/`--paths` cannot fix it (they affect analysis, not the entry
   module's `__package__`). The spec therefore ships
   `release/frozen_entry.py`, which imports `collector.collector_cli`
   under its real qualified name.
2. **Schema discovery must prefer the bundle.** `__file__` inside a onefile
   bundle resolves through `_MEIPASS`, which on the build machine can also
   match the source tree. The lookup order is module dir → bundle root →
   cwd → exe dir → `argv[0]` dir, and `verify_frozen_exe.py` rejects a
   `schema_source` that does not contain `_MEI`, so a missing bundle copy
   cannot be masked by the development checkout.

The binary is oversized for its function on purpose: no aggressive pruning,
no UPX (antivirus false positives), because the task's priority order is
correctness / offline completeness / reproducibility / deployment
simplicity over size.

---

## 8. Verification results

All suites run on the development machine (Windows, RTX 5070 Laptop, Python
3.11.9). Commands and outcomes:

| Suite | Command | Result |
| --- | --- | --- |
| Producer → collector smoke | `python tools/x265_calibration/tests/smoke_pipeline.py` | **PASSED** (24 checks) |
| Robustness / recovery | `python tools/x265_calibration/tests/robustness.py` | **14/14 PASSED** |
| DEP matrix | `python tools/x265_calibration/tests/dep_matrix.py` | **18/18 PASSED** |
| Real hardware ladder | `python tools/x265_calibration/tests/ladder_integration.py` | **PASS** |
| Frozen exe verification | `python tools/x265_calibration/release/verify_frozen_exe.py --inspect-archive` | **VERIFY OK** |
| Offline static check | `python tools/x265_calibration/release/deploy_helpers.py offline-check` | **OK** |
| Deployable ZIP, end to end | `python tools/x265_calibration/tests/zip_distribution.py` | **46/46 PASSED** |

The ZIP suite reproduces the receiving operator's experience rather than just
inspecting the archive: it extracts into a path containing a space, checks
that every pre-created data directory survived the zip, verifies
`SHA256SUMS.txt` against every packaged byte, runs the exe with no usable
Python on `PATH`/`PYTHONHOME`/`PYTHONPATH`, writes a real event through the
production emitter into the shipped `calibration/` directory and consumes it
with the packaged exe, then **executes** `CHECK-INSTALL.bat` and
`START-COLLECTOR.bat` and asserts from the collector's own log that it watched
`<pkg>\calibration` and created nothing outside the package.

That last assertion caught a real defect: the `.bat` launchers derived the
data directory as `%~dp0..\calibration`, i.e. the folder *above* the extracted
package, so double-clicking `START-COLLECTOR.bat` would have watched an empty
directory while the packaged `calibration/` stayed empty forever. They now use
`%~dp0calibration`. The same suite then caught two bugs in itself (an
off-by-one event count, and running the Python analyzer under the deliberately
broken Python environment), both fixed.

### Closing re-verification (archival pass)

The archival pass re-ran only what changed, and re-checked the recorded
artifacts against the report rather than repeating the expensive suites:

| Check | Result |
| --- | --- |
| `git status --porcelain --untracked-files=all` | empty (working tree clean) |
| EXE SHA-256 vs the value recorded in this report | **MATCH** (`7bc90332…3ad84`) |
| EXE size vs the value recorded in this report | **MATCH** (8,152,101 bytes) |
| Release package `SHA256SUMS.txt` vs the packaged files | consistent (8 entries) |
| DEP matrix, re-run after the test-path fix below | **18/18 PASSED** |
| Collector source traceable on this branch | all 16 deliverables tracked (`git ls-tree`) |
| `docs/calibration_collector_final_report.md` present | yes (tracked) |
| Branch history linear, no merge commits, no unrelated changes | yes (3 non-merge commits; the 22 files changed since `1a41fff` are calibration-only) |
| `main` untouched by this task | yes — `main` still `62c5504`, not an ancestor of this branch, contains none of these commits |

One leak was fixed in this pass: `tests/dep_matrix.py` wrote its clean-machine
encode output into `dist/x265_calibration/_dep_corpus/`. `dist/` is gitignored
so it never reached a commit, but a test must not deposit output in the
release directory. It now writes under `work/clean_machine_out/`, and the
stray directory was removed. The release package contents were not affected.

### DEP matrix detail

| ID | Requirement | Result | Evidence |
| --- | --- | --- | --- |
| DEP-01 | Collector starts independently | PASS | exe consumed an 8-event backlog in `--once` mode with no production process |
| DEP-02 | Production starts without collector | PASS | 2 encodes, rc=0, 8 events spooled, no collector ever started |
| DEP-03 | Collector starts after production | PASS | backlog produced earlier was fully consumed |
| DEP-04 | Collector restart recovers pending events | PASS | replayed archived event ignored, torn file quarantined, record count unchanged |
| DEP-05 | Collector failure does not fail encode | PASS | rc=0 with the collector absent; rc=0 with a hostile spool + telemetry warnings |
| DEP-06 | Event atomicity | PASS | no `.tmp` visible; single completed file is valid JSON; `.tmp` ignored by the consumer |
| DEP-07 | Failed attempt preserved | PASS | A001 FAILED with `failure_stage=verification_failed` |
| DEP-08 | Retry creates new attempt | PASS | A002, `attempt_number=2`, `attempt_kind=retry` |
| DEP-09 | Successful retry preserves previous failure | PASS | both records present, A001 still FAILED |
| DEP-10 | Job final state correct | PASS | SUCCESS with `accepted_attempt=A002`; all-failed job → FAILED for both attempts |
| DEP-11 | Offline standalone binary | PASS | exe runs, `frozen: True`, package built and copied outside the repo |
| DEP-12 | Clean-machine execution | PASS | package copied to `%TEMP%`, run with minimal env, stayed alive, wrote log + manifest |
| DEP-13 | No external network | PASS | no network APIs in the binary (`wininet`/`ws2_32`/`WinHttp`/`libssl` absent); static check finds no URL/socket/HTTP construct |
| DEP-14 | No Python installation required | PASS | ran with `PATH` stripped to system32, bogus `PYTHONHOME`/`PYTHONPATH`, and consumed a real backlog |

### Robustness detail (§30 fault cases)

| Case | Result |
| --- | --- |
| R1 collector process kill (real exe, mid-sweep, 504-event backlog) | PASS — persisted data survived; restart completed 132/132 records, 0 duplicates, 0 pending |
| R2 power loss (torn JSONL tail) | PASS — partial trailing line dropped on restart, good records kept |
| R3 partial event | PASS — quarantined to `events/failed/` with an `.error.txt`; never counted, never reprocessed |
| R5 duplicate event | PASS — replayed archived events ignored (idempotent), counted once |
| R6 disk full | PASS — persist raised ENOSPC inside a real collector process; event stayed pending; next healthy sweep wrote it once |
| Lock | PASS — second instance refused, stale lock (dead pid) broken automatically |
| Restart | PASS — offsets, buffer and id index recovered from disk + raw JSONL |

### Bugs found and fixed by these tests

1. `os.kill(pid, 0)` is **not** a liveness probe on Windows (it takes a
   console event code), so lock acquisition raised and the collector exited
   with a `SystemError`. Replaced with
   `OpenProcess`/`GetExitCodeProcess` plus a staleness fallback.
2. Success records were persisted before the job's final event arrived, so
   the `source → job → attempt` join was incomplete. Records now wait for
   the job final event (bounded by `--job-final-timeout`) and are written
   with complete join keys; the fallback path flags the gap instead of
   inventing it.
3. Producers that legitimately lack a value (hardware backends have no
   x265 scaling context) would have been rejected as schema errors. Those
   fields are now optional and the producer records an explicit
   `not_applicable` marker, so "not applicable" is never confused with
   "lost".
4. **`--preset all` silently collapsed four attempts into one.** All four
   preset runs of one source shared the production job id
   (`core.paths.job_id_for`, source-derived), so they also shared
   `attempt_id`, and the collector's uniqueness check — correctly — treated
   them as one attempt and dropped three. The calibration job id is now
   qualified with the preset (`<source-id>-<PRESET>`), so every
   (source, preset) job has its own attempt namespace. Because this class
   of defect was silent, the collector now logs a duplicate `attempt_id` as
   an **error** naming the job, preset and backend instead of skipping it
   quietly.

---

## 9. Data schema

* Contract: `tools/x265_calibration/schema.json` (11.9 KB), hand-validated
  by the collector with stdlib only (no external schema library, so the
  frozen exe stays dependency-free).
* Every event carries `schema_version`, `event_id`, `timestamp`,
  `event_type`, `job_id` (`attempt_id`/`attempt_number` on attempt events,
  `producer`, `project_version`, `host`, `pid`).
* Versioned for future upgrades: `event_schema_version` and
  `record_schema_version` are separate from the payload definitions;
  unknown fields are **warnings** (forward compatible) while missing or
  mistyped required fields are **errors** (`--strict` promotes warnings).
* `event_id` is human-sortable and filesystem-safe:
  `<UTCts>-<job>-<attempt|->-<type>-<serial>-<random>`.

Calibration record (one immutable JSONL line per attempt) carries, all
copied verbatim from production:

```
record_version, record_type, event_schema_version, collected_at
project_version, job_id, attempt_id, attempt_number, attempt_sequence,
attempt_kind, retry_of_attempt_id, trigger, run_id
source_id, source_sha256, source_path, relative_path, source_size_bytes
source_info (resolution, fps, codec, profile, pix_fmt, bit depth, chroma,
             OB bitrate, stream list, HDR color side data)
classification (source_class, normalized_ob, evidence)
scaling_context (reference, spatial/temporal/pixel-rate factors,
                 aspect_ratio, source_class, normalized_ob)
effective_params (every resolved x265 parameter, verbatim)
effective_params_audit (the mode/formula trail per scaled parameter)
actual_x265_params (the literal string handed to ffmpeg)
backend, codec, encoder_format, reader, preset, crf, pix_fmt
config_hash, scaling_config_hash
start_time, end_time, wall_time_sec, encode_fps
status, exit_code, verification_result, verification_status
output_path, output_size, output_duration, output_width, output_height,
output_video_bitrate, output_frame_count, output_fps, compression_ratio
failure_stage, failure_reason, error_summary
ffmpeg_version, libx265_version, tool_versions, warnings, command
job_final_status, accepted_attempt_id, accepted_attempt_number,
job_attempt_count, job_failed_attempt_count
source_event_id, attempt_started_event_id, attempt_finished_event_id
host, pid, producer, collector_version
completeness {complete, missing[], attempt_started_seen, job_started_seen,
              job_finished_seen}
```

The collector performs **no** recalculation of source classification,
scaling factors, merange, lookahead or VBV. There is no
`CollectorScalingEngine`. If production did not compute something, the field
is left absent and named in `completeness.missing`.

---

## 10. Dataset persistence

* `samples/YYYY-MM-DD.jsonl` — one immutable record per line, one file per
  day. No growing monolithic JSON, no rewriting of persisted lines.
* Authority: **raw JSONL**. Summaries (`summaries/YYYY-MM-DD.json`) are
  always rebuildable via `--rebuild-summary`, and validation reports are
  derived from the raw files.
* Append is durable (flush + fsync) and restart-safe: a torn trailing line
  is truncated away on start, persisted lines are never touched.
* Archive (`events/archive/<day>/`) keeps every consumed event verbatim, so
  the dataset can be re-derived from events if ever needed.
* Volume estimate: ~3-6 KB per event and per record; 5000 encodes ≈ 40 MB
  of events + 30 MB of records.

---

## 11. Analyzer

`tools/x265_calibration/analyze.py` — development-side, offline, read-only,
stdlib only. Not deployed to the soak host and not run during collection.

Reports (console summary and/or `--json`):

* **Source:** resolution, FPS (+ buckets), codec, profile, bit depth, chroma,
  duration, container bitrate, source class, `normalized_ob` distribution and
  buckets, source-class × preset matrix.
* **Scaling:** reference config, spatial/temporal/pixel-rate factor
  distributions and buckets, and per-parameter behaviour (merange,
  rc-lookahead, gop-lookahead, min-keyint, keyint, vbv-maxrate,
  vbv-bufsize, bframes, ref) overall and per input resolution.
* **Dynamic VBV:** mode census (static / dynamic_vbv / cpb_clamp) and the
  achieved `final_maxrate / OB` ratio distribution from the audit trail.
* **Production:** success / failure / retry / fallback / recovery rates,
  failure-stage and failure-reason census, verification status, wall time,
  output size, compression ratio, encode fps and realtime factor, sliced by
  backend and preset.
* **Data quality:** complete vs incomplete records, missing-field census,
  jobs without a final event, records missing their `attempt_started`.

It never writes `x265.json` or `x265_scaling.json` and never feeds a value
back into production.

---

## 12. Deployment

```
1. mkdir D:\1kt\calibration
2. Environment variables for the production account:
       ONEXKT_CALIBRATION=1
       ONEXKT_CALIBRATION_SPOOL=D:\1kt\calibration
3. x265-calibration-collector.exe --headless --watch D:\1kt\calibration
4. Verify: --status / --validate, or errors\collector.log
```

Recommended long-run setup: one extra Windows Task Scheduler entry next to
the existing production task, "At startup", restart-on-failure enabled,
stop-on-timeout disabled, do-not-start-a-new-instance — all printed by
`deploy_helpers.py schedule-task`, and generated as ready-to-run
`.cmd`/`.bat` files by `deploy_helpers.py install-dir <dir>`.

The collector may start first, last, or be restarted at any time; production
is never affected. Recommended end-of-soak step: `--drain-buffered` once, to
persist any terminal attempt whose job final event was lost, with explicit
completeness flags.

`job_final_timeout_sec` (default 900 s; `collector.toml` or
`--job-final-timeout`) bounds how long a terminal attempt waits for its
job's final event before being persisted with
`completeness.job_finished_seen = false`. Raise it above the longest
expected single encode if a soak host ever encodes for more than 15 minutes
per file; set `0` to persist immediately without waiting for the join.

---

## 13. Unverified / residual risk

Honest list of what this session did **not** prove:

1. **A true clean Windows machine was not available.** DEP-12/DEP-14 ran the
   packaged exe from outside the repository with a minimal environment and
   with Python removed from `PATH`/`PYTHONHOME`/`PYTHONPATH`, but on a host
   that still has a Python installation and the Visual C++ runtime present.
   The first real soak-host start remains the definitive check; the
   documented fallback is `--runtime-tmpdir` if a host blocks `%TEMP%`.
2. **No antivirus / SmartScreen / EDR evaluation.** A brand-new unsigned
   single-file exe is a common heuristic target. If a host quarantines it,
   whitelist the package directory rather than disabling protection.
3. **No 1-month soak was run.** Long-horizon behaviours (multi-day runtime,
   month-long state-file growth, disk pressure over weeks, scheduler
   restarts) are inferred from the restart/robustness tests, not observed.
   State files are bounded by design (`seen_event_ids` is capped, closed
   jobs are pruned, one summary per day).
4. **Sony/DJI preservation paths were not exercised end to end.** They are
   instrumented identically (single-attempt job wrapper, same verification/
   commit semantics), but no Sony rtmd or DJI djmd source was available to
   encode in this session.
5. **The experimental `--experimental-multihw` dual-backend pool is not
   instrumented** beyond the per-file hardware instrumentation it reuses;
   its scheduler-level routing is not additionally recorded. It is
   explicitly experimental and not part of the recommended soak config.
6. **Power-loss simulation is a file-level simulation** (torn JSONL tail,
   torn event file, left-over `.tmp`), not an actual sudden power cut; it
   verifies the recovery logic, not hardware-level durability.
7. **Non-ASCII host paths**: verified working end to end here
   (`C:\Users\吴汶睿\...`), but console rendering of such paths can be
   mojibake in captured logs depending on the code page. The JSON data is
   Unicode-clean (`ensure_ascii=False`, UTF-8).
8. **Reproducibility of the exe**: PyInstaller embeds build-machine paths,
   so the exe is reproducible per machine/toolchain, not bit-identical
   across machines. The deployment identity is the recorded SHA-256.

---

## 14. Boundary statement

```
Production Transcoder  = authoritative executor
Production             → immutable calibration event
Calibration Collector  = independent observer / persister
Analyzer               = offline analysis
Future calibration session = parameter optimization
```

Strictly absent from the collector: any production decision, retry,
re-encode, scaling/preset/output alteration, network access, remote upload,
online learning, scheduling or transcode control.

The artefact this month produces is not "optimal parameters". It is a real
production dataset that can be re-counted, re-classified, re-fitted and
audited — for one month of production, with the failed attempts intact.

---

## 15. Archival: tag recommendation (NOT created — decision left to the maintainer)

This branch was **not** tagged. The reason is a namespace conflict with the
project's own documented convention, so the decision is recorded here instead
of being taken unilaterally.

The project's archive convention (`b3245b7`, `olddocs/README.md` §5) defines
`archived/*` as **annotated tags for branches that were merged into `main`
and then deleted** — the tag is the only remainder of that line
(`archived/rigaya-nvencc-avhw`, `archived/hwdecode-e2e-benchmark`,
`archived/av1`, …). Every one of those tags means *"merged and retired"*.

`feature/x265-calibration-collector` is the opposite case: it is
**deliberately not merged**, is **not deleted**, and stays **live** for a
month. Reusing `archived/*` here would state something false ("merged"), and
inventing a new namespace (`soak/*`, `frozen/*`) would create new version
semantics that this task was told not to invent.

**Recommendation for the maintainer** — pick one when the moment is right:

| Option | When it fits | Effect |
| --- | --- | --- |
| **Do nothing** (current state) | If the branch simply stays and gets pushed — enough for reproducibility, since the branch ref *is* the marker. | none |
| `git tag -a soak/x265-calibration-collector-2026-09 <HEAD>` | If a machine-readable "frozen soak point" is wanted without claiming merge. | new namespace: explicitly *not* `archived/*`, so the "merged" meaning stays intact |
| `git tag -a archived/x265-calibration-collector <HEAD>` **after** the soak, if the branch is then merged-and-retired | Only once the branch truly reaches the end state the `archived/*` tags describe. | consistent with the existing convention |

Two mechanical steps remain for the maintainer, both outside this task's
scope (they touch `main` or the network):

1. **Push the branch** — it is currently local only; `origin` has only `main`
   and `feature/hardware-decode-integration`. Pushing is what makes the
   1-month experiment reproducible from a fresh clone. It is not required for
   the offline host, which only receives the release package.
2. **Record the tag table** in `olddocs/README.md` §5 if a tag is created,
   per that file's own instructions.

---

## 16. Final

```
Branch:            feature/x265-calibration-collector
HEAD:              2813fcb
Commit range:      1a41fff .. 2813fcb
Offline:           PASS
Headless:          PASS
Clean-machine test: PASS (approximated: outside repo, minimal env, no Python
                          on PATH; see "Unverified" item 1)
Restart recovery:  PASS
Standalone binary: PASS (7.77 MiB, self-contained, schema bundled)
Deployable zip:    PASS (7.63 MiB, extract-and-run, 46/46 zip checks)
Merged to main:    NO (must stay NO)
main modified:     NO

Final:             ARCHIVED / READY FOR 1-MONTH SOAK
```
