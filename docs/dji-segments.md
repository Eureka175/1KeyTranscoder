# DJI Action split recordings

DJI Action cameras start a new file when a recording reaches roughly 16 GB
(`DJI_YYYYMMDDHHMMSS_NNNN_D.MP4`).  1KeyTranscoder can detect those contiguous
segments, join them losslessly in one MP4Box pass, and then run the normal
encode pipeline on the joined source.  Joining is **progressive**: the first
group is joined before the first encode starts, every following group is
joined in the background while the previous group is encoding, and a delivered
group's joined source is deleted immediately.  Peak disk use is the current
group plus the next one, not the sum of every group.

The feature is part of the main program:

```text
python 1kt.py ... --dji-join
```

**`--dji-join` is off by default.**  Without it, every file is processed
exactly as before (split segments remain independent jobs).

## Usage

Single input root (the normal case):

```text
python 1kt.py \
  --input "F:\Pictures\2026\202611\20261115" \
  --output "F:\archive\202611" \
  --encoder nvenc --preset fast --scale 0.25 --check basic --headless \
  --dji-join
```

Several input roots (e.g. two sibling date folders): the `dji_join.py` wrapper
accepts multiple roots and forwards them one by one to `1kt.py --dji-join`:

```text
python dji_join.py \
  --input "F:\Pictures\2026\202611\20261114" "F:\Pictures\2026\202611\20261115" \
  --output "F:\archive\202611" \
  --encoder nvenc --preset fast --scale 0.25 --check basic --headless
```

Related options:

| Option | Meaning |
|---|---|
| `--dji-join` | Enable split detection + progressive lossless joining, then encode. Default off. |
| `--dji-join-keep-temp` | Keep every joined file and the `.dji_join_work_*` tree even after success. Disk use returns to the sum of all joined groups. |
| `--dry-run --dji-join` | Detect/report groups only; no join, no encode. |
| `dji_join.py --join-only` | Materialise the complete staging tree eagerly and stop before encoding (inspection / manual use). |

Rejected combinations (explicit `FATAL`, never a silent fallback):
`--repair`, `--retry-list`, `--channel-sync-transparent`.

## Detection

Only files matching `DJI_YYYYMMDDHHMMSS_NNNN_D.*` with a `djmd` or `dbgi`
data track are considered.  Segments are grouped only when:

- file numbering is consecutive;
- the directory and media parameters match;
- the previous segment is near the 16 GB split threshold;
- filename start time plus duration is continuous with the next segment.

Non-matching recordings remain independent jobs.  A file that cannot be
probed is skipped from detection (and reported) instead of aborting the batch;
the ordinary pipeline gets its own chance to handle it.

## Join implementation

The feature uses existing tools and APIs, not a custom MP4 writer:

1. ffprobe (`core.probe.probe_streams`) identifies candidates and groups them.
2. `GpacContainerBackend.concat()` runs one `MP4Box -cat` pass over the
   segment files, with `-unalign-cat` and `-flat`.
3. `join_group()` validates the result with ffprobe: duration vs the sum of
   segments, `djmd`/`dbgi` after the join, and the first segment's camera model.
4. The normal `1kt.py` pipeline performs the actual hardware/software encode.

Why `-unalign-cat`: a plain `MP4Box -cat` aligns the next segment at the
previous *movie* duration.  The AAC track is a few milliseconds longer than the
video track, so every boundary leaves a one-frame gap, the joined video looks
VFR, and the hardware-integrity gate then rejects hardware decode.
`-unalign-cat` concatenates each track's samples continuously; measured on the
user's Action 4 segments the joined video stays exactly CFR `30000/1001` with
`time_base=1/30000`, frame count = sum of segments, and the data tracks are
carried by the same pass.

Why `-flat`: without it GPAC 26.02 writes a full-size temporary file beside
the destination (peak = 2x the joined size; measured 1.30 GB temp + 1.30 GB
output for a 1.30 GB join).  With `-flat` MP4Box writes the destination
directly (1x peak) and the video samples stay contiguous, which is also the
faster layout for the encoder's sequential reader.  This replaces the older
per-track extraction + concat + final mux sequence, whose scratch peak was
roughly 3x the joined size.

## Input staging, outputs and cleanup

Everything is materialised under `<output>/.dji_join_work_<timestamp>/`
(relative directory layout preserved; ungrouped files are hardlinked, or
symlinked across volumes — sources are never modified or copied).  The
ordinary pipeline treats that staging tree as its input root, so output names
are as if the user had supplied one file per recording:

```text
DJI_20261115164247_0025_D__joined.MP4
```

Progressive lifecycle:

1. detection writes the plan;
2. group 0 is joined synchronously; encoding starts immediately afterwards;
3. when group N's encode starts, the stager joins group N+1 in a background
   thread (space guard permitting);
4. group N's joined source is removed as soon as its output is delivered;
5. this repeats for every group; at most two joined groups exist on disk.

`--dji-join-keep-temp` disables step 4, so it keeps every joined file and needs
the sum of all groups free; the same applies to `dji_join.py --join-only`.

Staging lifecycle:

- success, no failed files → staging tree removed automatically;
- any failed/never-joined group, Ctrl+C, a join error, or
  `--dji-join-keep-temp` → staging kept and its path logged.  Joined sources
  that were already delivered have already been reclaimed; the kept tree then
  contains the hardlinked ungrouped files plus any failed group's joined file.

Reports are written under the run's normal logs root:

```text
<output>\logs\dji_join\dji_join_plan.json
<output>\logs\dji_join\dji_join_joined.json
```

`--dry-run --dji-join` writes only the plan and stops; it does not create the
staging tree.

## Metadata policy

The camera model tag (`tool: DJI OsmoAction4`) lives on the movie box, not on
a track, so MP4Box's rewrite drops it in the joined file.  The join step
records the first segment's model, and joined Action-series sources are
therefore preserved by default even though the joined file no longer carries
the tag.  `--preserve-metadata` still forces preservation for non-Action DJI
sources; `--no-metadata` keeps its existing behavior of skipping the
preservation path.

### Compatibility with `--no-metadata`

`--dji-join --no-metadata` is supported: the split recording is still joined
losslessly (the join is about input completeness, not metadata), but the
joined source is encoded through the ordinary video+audio transcode path, so
`djmd`/`dbgi`/`tmcd` are not rebuilt and no preservation report is produced.
Every data/timecode track that the encoder intermediate inherited from the
source is stripped again by the container re-mux (measurable case: NVEncC
carries the source `tmcd` track into its MOV intermediate), so the delivery
contains video+audio only.  Explicit off wins over both the split-join
preserve-by-default rule and `--preserve-metadata`, on hardware and software
backends alike.  Hardware-first decode and the integrity gate are unchanged by
`--no-metadata`.

## Implementation

- `1kt.py` — `--dji-join` switch, input-set substitution, staging lifecycle,
  per-file preservation override for joined sources, encoder-progress hook.
- `core/dji_join.py` — detection, grouping, one-pass lossless join,
  `ProgressiveStager` (join-ahead, space guard, reclaim, reports).
- `core/batch_hw.py` / `encoders/hw.py` — joined-source gate before probe,
  per-file finish notification, and progress forwarding to the stager.
- `dji_join.py` — multi-root / `--join-only` convenience wrapper; it does not
  duplicate the join or encode logic.

## Verified result

2026-11-15, the user's two DJI Action 4 directories:

- 21 source files → 7 split groups (17 segments) + 4 independent files;
- 7/7 groups joined losslessly, all `djmd`/`dbgi`/`tmcd` tracks intact;
- encode: 11/11 outputs, NVENC FAST, `--scale 0.25` → 960x720 HEVC;
- DJI preservation: 11/11 reports `15 PRESERVED / 0 MODIFIED / 0 MISSING`;
- hardware integrity: 11/11 PASS;
- source files unmodified; staging cleaned after success.

The merged `1kt.py --dji-join` path was re-verified end-to-end on a real
16 GB + 4 GB split pair (0025 + 0026): join `tool='DJI OsmoAction4'`, encode
at 294.8 fps (399.6 s, 61,167 frames), hardware integrity PASS,
`PRESERVED=15 / MODIFIED=0 / MISSING=0`, staging removed.  The switch-off path
on the same input produced two independent jobs, and `--dji-join-keep-temp`
kept the staging tree as documented.  New selftests: L1 23 / L2 10 / L3 10
all passing.  Full evidence: `work/dji_join_merge/TEST_REPORT.md`.

### 2026-11-23: one-pass join + progressive staging

The join algorithm was reduced to a single `MP4Box -cat -unalign-cat -flat`
pass and the eager "join everything first" staging was replaced with
`ProgressiveStager`.

Real full segments (the user's 16.03 GB + 4.24 GB pair 0025 + 0026):

- one-pass join: 81.6 s, destination 21.76 GB / 20.27 GiB — exactly **1x** the
  source read+write, with no GPAC temp file (measured free-space delta 20.27
  GiB; the old path needed ~3x scratch and could not fit on the 118 GB free
  volume that failed);
- video: `30000/1001`, `time_base=1/30000`, **61,167 frames** — the same frame
  count the previous track-wise algorithm produced;
- `djmd`/`dbgi`/`tmcd`: 61,167/61,167 samples + timecode track, all intact;
- joined duration 2040.960 s vs expected 2040.977 s; camera model
  `DJI OsmoAction4`, `keep_metadata=True`; validation `ok=True`.

End-to-end `--dji-join` runs on two synthetic split groups (real MP4Box join +
real encoder + real cleanup) passed on:

- classic NVENC (`--no-metadata`): 2/2 outputs, staging removed, every joined
  source reclaimed after delivery;
- software x265 (`--scale 0.25 --no-metadata`): 2/2 outputs, same lifecycle;
- DJI preservation (`--preserve-metadata`): 2/2 outputs,
  `PRESERVED=15 / MODIFIED=0 / MISSING=0` for both groups.

Log sequence in those runs: `join start` → `join done` → `prefetch armed
(encode-start)` → `reclaimed` per group → `joined report` → `staging removed`.

### 2026-11-23: async hardware integrity (default on)

Hardware-decode integrity is a full independent software decode of the
encoded output.  On a long 4K group it can take tens of minutes; the old
inline gate made the batch look frozen after the encoder had already exited.

Async mode is now **on by default** for hardware backends (use
`--no-async-integrity` to fall back to the old synchronous gate):

- `--jobs N` limits only **encoder invocations**.  A job waiting for
  verification does not hold an encode slot, so the next file can be encoded
  while the verifier runs.  The pool keeps two extra workers for pending
  verification; `--jobs 1` still means one encoder at a time.
- The verifier queue is one slot (`[INTEGRITY] waiting for the single
  verifier slot`).  Extra artifacts may accumulate while disk is healthy; the
  scheduler checks free space before starting another encode and waits when
  it drops below `KTS_ASYNC_DISK_RESERVE_GB` (default 10 GB).
- The DJI stager releases the next group's encode gate as soon as the current
  intermediate is complete.  The joined source is retained until
  `note_finished()` (after verification and delivery), because
  `verify_encode()` still needs the source container's expected sample count.
- Enabled for classic, Sony-preservation and DJI-preservation hardware paths.
- `--no-metadata` deliberately **skips** hardware-decode integrity
  (`integrity=SKIPPED`, WARNING in the log).  This is the requested fast path
  for metadata-free DJI transcodes.
- `--experimental-multihw` disables async mode (single-backend scheduler only).

A failed hardware gate still discards the artifact and reruns that rung with
`--avsw`; the software replacement is checked with `verify_container_counts`
(container sample count; independent decode within one frame) before delivery.
The verifier slot serializes that check too.

Verified on the 3-group 2 s DJI synthetic set:
- classic `--no-metadata`: `integrity=SKIPPED` for every group;
- classic with integrity: `[ASYNC-INTEGRITY]` release logs,
  `[INTEGRITY] waiting for the single verifier slot`, all PASS;
- DJI preservation with `--jobs 1`: group 2 started encoding while group 1's
  verifier was running; preservation reports `PRESERVED=15 / MODIFIED=0 /
  MISSING=0`; staging removed.
