================================================================================
x265 CALIBRATION COLLECTOR — DEPLOYMENT README
1KeyTranscoder v0.7.0 / feature/x265-calibration-collector
================================================================================

WHAT THIS IS
------------
An offline, headless, standalone data collector that records what the
production transcoder ACTUALLY computed and ACTUALLY decided while encoding a
real corpus for roughly one month (500 GB+ of produced output).

The collected dataset is the input to a LATER calibration session. This
collector does NOT optimise, tune, retry, re-encode or change anything.

    Production Transcoder  = authoritative executor
    Calibration Collector  = independent observer / persister
    Analyzer (analyze.py)  = offline analysis, run on the dev machine later
    Future session         = parameter optimisation (not this one)


================================================================================
1. THE THREE THINGS YOU PUT ON THE SOAK HOST
================================================================================
From the release package directory (flat layout, no subdirectories needed):

    x265-calibration-collector.exe     the standalone collector (self-contained)
    schema.json                        the event contract it validates against
    README.txt                         this file
    collector.toml                     optional runtime configuration

Requirements on the soak host:

    * Windows x64
    * NO Python
    * NO pip / no package installation
    * NO internet access
    * NO network of any kind (the collector never opens a socket)
    * Nothing to install, no registry changes, no service

Optional but recommended:

    * `analyze.py` (development machine only — see section 8)


================================================================================
2. QUICK START (5 minutes)
================================================================================
Step 1 — Create the calibration directory. It can be anywhere that has space
         for JSON text only (roughly 2-20 MB per 1000 encodes):

    mkdir D:\1kt\calibration

Step 2 — Tell the production transcoder where to write its events. Set these
         two environment variables for the account that runs production
         (Windows Task Scheduler -> the task -> "Start in" is irrelevant; use
         a system environment variable, or a wrapper .bat):

    ONEXKT_CALIBRATION=1
    ONEXKT_CALIBRATION_SPOOL=D:\1kt\calibration

    Both are opt-in. With ONEXKT_CALIBRATION unset the transcode path is
    completely unchanged (the emitter is a no-op).

Step 3 — Start the collector (it can start before, after, or be restarted at
         any time; production does not wait for it and never reads from it):

    x265-calibration-collector.exe --headless --watch D:\1kt\calibration

    Add --verbose if you want one log line per sweep. Without it, only
    warnings and errors reach the console; everything is always written to
    D:\1kt\calibration\errors\collector.log.

Step 4 — Verify:

    x265-calibration-collector.exe --headless --status   --spool D:\1kt\calibration
    x265-calibration-collector.exe --version

That is the whole deployment. Everything below is detail.


================================================================================
3. COMMAND LINE
================================================================================
    --headless                  never prompt, never open a window (deployment)
    --watch <path>              watch mode; <path> = calibration root, its
                                events/ dir or its events/pending/ dir
    --validate <path>           validate a collected dataset (read-only)
    --version                   print version / schema / python info
    --once                      watch: process the current backlog once, exit
    --status                    print one JSON status object and exit
    --rebuild-summary           rebuild every daily summary from raw JSONL
    --drain-buffered            end-of-soak: persist terminal attempts whose
                                job final event was never seen (flagged)
    --spool <path>              calibration root when not implied above
    --config <path>             config file (default: collector.toml found in
                                cwd, then next to the exe)
    --schema <path>             schema.json to validate against
    --poll-interval <sec>       pending/ scan interval (default 2.0)
    --job-final-timeout <sec>   wait for a job's final event before persisting
                                its attempts as flagged-incomplete (default 900)
    --strict                    treat schema warnings (unknown fields) as errors
    --dry-run                   validate and report, write nothing
    --verbose / --quiet         console verbosity (the log file gets everything)
    --json-out <path>           --validate: also write the report as JSON

Exit codes:  0 ok | 2 usage | 3 another collector holds the lock
             4 internal error | 5 dataset validation found problems


================================================================================
4. RECOMMENDED LONG-RUN DEPLOYMENT (Windows Task Scheduler)
================================================================================
Create ONE additional scheduled task next to the existing production task.
Both are independent sibling processes; neither launches the other.

    Task:     1KeyTranscoder Calibration Collector
    Program:  D:\1kt\collector\x265-calibration-collector.exe
    Arguments: --headless --watch D:\1kt\calibration
    Start in:  D:\1kt\collector
    Trigger:   At startup
    Settings:  [x] Restart the task if it fails (1 minute, 999 times)
               [x] Stop the task if it runs longer than: DISABLED
               [x] If the task is already running: Do not start a new instance
    Account:   any account that can read D:\1kt\calibration and write into it

The collector survives being killed, restarted, or never started at all:

    * production keeps running with the collector stopped (events accumulate
      in events/pending/)
    * on restart the collector rescans pending/ and consumes the backlog
    * events already archived are never counted twice (idempotent by event id
      and by attempt id, checked against the raw JSONL itself)
    * a torn/partial event file is quarantined to events/failed/, never
      counted, and never breaks the run

A single collector per calibration directory is enforced by a lock file
(state/collector.lock); a second instance exits with code 3 instead of
corrupting the dataset. A stale lock (dead pid) is broken automatically.


================================================================================
5. WHAT IS COLLECTED (AND WHAT IS NOT)
================================================================================
Every ENCODE ATTEMPT becomes one immutable JSONL line in
<root>/samples/YYYY-MM-DD.jsonl. Failed attempts are kept forever; a later
successful retry never overwrites or removes them.

One attempt record contains, all copied verbatim from production:

    identity      project_version, job_id, attempt_id, attempt_number,
                  attempt_sequence, attempt_kind, retry_of_attempt_id,
                  source_id, source_sha256, source_path, relative_path
    source        source_info (full probe: resolution, fps, codec, profile,
                  pix_fmt, bit depth, chroma, OB bitrate, streams, HDR color)
    classification source_class, normalized_ob, evidence
    scaling       scaling_context (reference, spatial/temporal/pixel-rate
                  factors, aspect ratio) + effective_params + the complete
                  effective_params_audit trail + the literal -x265-params
                  string that was handed to ffmpeg
    tools         ffmpeg version, libx265 version, tool_versions
    timing        start_time, end_time, wall_time_sec, encode_fps
    output        output_size, duration, width, height, video bitrate, frame
                  count, fps, pix_fmt, codec, compression_ratio
    outcome       status, exit_code, verification_result / verification_status
    failure       failure_stage (startup_failed | encode_failed |
                  verification_failed | output_commit_failed | probe_failed),
                  failure_reason, error_summary, output_exists
    joins         job_final_status, accepted_attempt_id, event ids, and a
                  `completeness` block naming any field production could not
                  supply

The collector does NOT collect:

    * video pixels, frames or decoded data (it never runs ffmpeg/x265)
    * CPU/GPU samples, process metrics, or anything time-series
    * anything it would have to COMPUTE: no scaling, no merange, no
      lookahead, no VBV, no classification is ever recalculated. If production
      did not compute it, the field is left absent and listed in
      `completeness.missing`.

On hardware backends (NVEncC/QSVEncC) the record carries
`scaling_context.status = "not_applicable"` and an `actual_x265_params`
marker explaining why: those paths do not run the scaling engine, so there is
no honest value to record. This is deliberate — an explicit marker beats an
empty field, and it lets the analyzer separate "not applicable" from "lost".


================================================================================
6. ON-DISK LAYOUT
================================================================================
    D:\1kt\calibration\
      events\pending\        producer -> collector (atomic rename in)
      events\archive\<day>\  consumed events, verbatim, for auditing
      events\failed\         invalid events + <name>.error.txt
      samples\YYYY-MM-DD.jsonl   THE DATASET (authoritative, append-only)
      summaries\YYYY-MM-DD.json  daily rollups, always rebuildable
      errors\collector.log       collector log
      errors\rejected_events.jsonl  every rejected event + why
      state\                     consumer state (safe to delete only when the
                                 collector is stopped)
      manifest.json              counters and dataset inventory

Sizes: one event is 3-6 KB, one record 3-6 KB. A month of 5000 encodes is
roughly 40 MB of events plus 30 MB of records — a few hundred MB at most.
The data is text; it compresses very well for transport.

The ONLY data you need to bring back to the development machine is the
`samples\` directory (plus `events\archive\` and `errors\` if you want the
full audit trail).


================================================================================
7. EVENT LIFECYCLE (what production writes and when)
================================================================================
    job_started        production starts processing a source (proof the job
                       existed; carries the full source snapshot)
    attempt_started    before each real encode attempt (own attempt_id)
    attempt_succeeded  ONLY after: encoder success AND production output
                       verification passed AND output committed/accepted
    attempt_failed     any failed attempt, with failure_stage + reason
    job_succeeded      the job's final state is SUCCESS
    job_failed         the job's final state is FAILED

Retry model: retries belong entirely to production. A job that fails on
attempt A001 and succeeds on A002 produces:

    job_started
    attempt_started A001
    attempt_failed  A001        <- kept forever
    attempt_started A002
    attempt_succeeded A002
    job_succeeded

so that hardware failure rate, software fallback rate and the specific inputs
that caused a retry stay analysable a month later.

Event writes are atomic (.tmp -> flush -> fsync -> rename), so the collector
never reads a half-written event. Event writing is best effort: if it fails,
production logs "[CALIBRATION] telemetry failure ..." and CONTINUES. A missing
or crashed collector can only ever mean "calibration data incomplete" — never
"production encode failed".


================================================================================
8. AFTER THE MONTH — ANALYZER (development machine)
================================================================================
analyze.py is a development-side tool. It is NOT deployed to the soak host and
is NOT run during the collection period.

    python analyze.py <dataset-dir>                  console summary
    python analyze.py <dataset-dir> --json report.json

It reports sources (resolution/fps/codec/profile/bit depth/chroma/bitrate/
duration/source class), scaling (spatial, temporal, pixel-rate factors,
merange, rc-lookahead, gop-lookahead, min-keyint, dynamic VBV modes and
achieved ratios), and production behaviour (success/failure/retry/fallback
rates, encode speed, output size, compression ratio), plus data-quality
counters.

It is strictly read-only: it never writes x265.json or x265_scaling.json and
never feeds a value back into production. Standard library only, offline.


================================================================================
9. VALIDATION AND TROUBLESHOOTING
================================================================================
Validate the dataset at any time, including while the collector is running:

    x265-calibration-collector.exe --headless --validate D:\1kt\calibration

It checks JSONL integrity (including a torn final line), record shape,
duplicate attempt/event ids, missing failure stages, records whose
attempt_started event was never seen, and the source -> job -> attempt join.
Exit code 5 means problems were found; the JSON printout lists them.

    Nothing in samples\                -> ONEXKT_CALIBRATION=1 is not set for
                                          the account that runs production, or
                                          ONEXKT_CALIBRATION_SPOOL points
                                          somewhere else
    pending\ grows but nothing is       -> the collector is not running, or it
    persisted                             watches a different directory
    events\failed\ has files            -> an event did not satisfy schema.json;
                                          see the sibling .error.txt and
                                          errors\rejected_events.jsonl
    exit code 3                         -> another collector instance is
                                          running against this directory
    "incomplete": true in a record      -> production could not supply a field;
                                          `completeness.missing` names it. This
                                          is recorded, never invented.
    Record has job_final_status ""      -> the job's final event never arrived
                                          (production killed mid-file, or the
                                          event was lost). Persisted after
                                          --job-final-timeout with a flag.

Diagnostics that never disturb the soak:

    --status            inventory + counters as JSON
    --dry-run --once    validate the backlog without writing anything
    --rebuild-summary   regenerate every daily summary from the raw JSONL


================================================================================
10. GUARANTEES AND NON-GUARANTEES
================================================================================
Guaranteed:

    * production never waits for, reads from, or depends on the collector
    * the collector never runs ffmpeg/x265, never decodes video, never encodes
    * the collector never changes a preset, parameter, scaling value, bitrate,
      output path, verification or retry decision
    * no network access of any kind (no HTTP, DNS, telemetry, cloud, license
      server, package manager)
    * raw samples\*.jsonl is authoritative; summaries are always rebuildable
    * restart-safe: pending events are recovered, archived ones are never
      double-counted

Not guaranteed:

    * the collector is not a backup of the output files, only of the metadata
    * a record can be incomplete (explicitly flagged) if production could not
      supply a value; nothing is reconstructed from guesses

================================================================================
END
================================================================================
