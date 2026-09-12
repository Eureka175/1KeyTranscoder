# Hardware Decode Architecture — Design

> **Status: design only.** Phase 1 forbids implementation. Nothing in this
> document has been applied to production code.
>
> This is the *technical* design. The phased execution plan, performance
> budget, risk register and acceptance criteria are in
> [`implementation-plan.md`](implementation-plan.md); the evidence behind
> every decision is in [`root-cause.md`](root-cause.md).

## 1. Design constraints derived from the evidence

Every constraint below is traced to a measured result, not a preference.

| # | Constraint | Evidence |
|---|---|---|
| C1 | Hardware decode must **not** go through the rigaya `--avhw` reader for XAVC | rigaya `--avhw` drops the leading pictures on 151/151 Sony files (`root-cause.md` RC-2/RC-3) |
| C2 | Hardware decode **may** go through FFmpeg `-hwaccel` for the same material | FFmpeg QSV and NVDEC produced bit-identical picture sequences to software decode (`divergence.md` §7.2) |
| C3 | Decode must be treated as a *pipeline stage with its own contract*, not as an encoder option | the defect sat in the reader layer of an encoder tool; the encoder was never at fault |
| C4 | Software decode must remain the reference and the fallback | software decode was frame-exact on 151/151 files and is also the fallback for formats with no hardware support |
| C5 | Hardware capability is format-specific and must be probed, never assumed | QSV on Arc 140T has **no** H.264 High 4:2:2 10-bit support; it fails with `rc=69` |
| C6 | A frame **count** is not an integrity check | `avhw` head-truncation and a hypothetical reorder/duplicate can both survive a count comparison; count is necessary, not sufficient |
| C7 | Verification must be decode-based, not packet-based | the project's current gate compares `nb_read_packets` (`batch_hw.py:336-363`), i.e. container bookkeeping |
| C8 | The fix must not depend on the edit list | removing the edit list (Matroska) does not remove the loss (`root-cause.md` RC-4) |

## 2. What is reusable from the current code

Deliberately conservative: the existing project already has the right
seams in several places.

| Component | Verdict | Rationale |
|---|---|---|
| `preservation/` (GPAC mux, validate, sony.py, isobmf.py) | **Reuse unchanged** | the defect is upstream of muxing; production deliverables were frame-exact (8/8) |
| `run_sony_pipeline(encode_video=...)` injection point | **Reuse** | backends are already decoupled from mux/preservation; a decoder change does not have to touch it |
| `encoder.command()` → `build_args` seam (`nvencc.py:107`, `qsvencc.py:102`) | **Reuse the seam, change the payload** | it is the single narrowest point through which every hardware path passes |
| `encoders/caps.py` capability probing + cache | **Extend** | currently encodes only; needs a decode-side capability model |
| `encoders/hw.py::run_hw_tool` (progress parsing, failure taxonomy, fallback prompt) | **Reuse** | independent of the decode question |
| `hw.py::plan_initial_format` (chroma/depth planning) | **Reuse** | orthogonal to frame integrity |
| `core/probe.py::count_frames` | **Rename + fix semantics** | returns packets, documents "decoded packet count" |
| `core/batch_hw.py` 1:1 gate | **Replace** | packet-count comparison; cannot express sequence integrity |
| `--avsync forcecfr` on the rigaya path | **Revisit** | duplicates/drops frames to force CFR, while the FFmpeg paths use `-fps_mode passthrough`; a structural asymmetry |
| `docs/design/hardware_backend_design.md` §4.3 ("avhw first, avsw on 1:1 failure") | **Supersede** | its premise (hardware decode is a speed win worth the risk) is not supported by measurement here; see §6 |

## 3. Target architecture

```
                    Input (Sony XAVC / DJI / classic)
                              │
                    ┌─────────▼─────────┐
                    │  demux / probe    │   ffprobe + ISOBMFF box parse
                    │  (container facts)│   stsz stts ctts stss elst
                    └─────────┬─────────┘
                              │  ContainerFacts
                              │   • sample_count
                              │   • pictures_before_first_keyframe   <-- predictor
                              │   • ctts reorder depth
                              │   • supported-format decision
                    ┌─────────▼─────────┐
                    │ Decoder selection │   capability × policy
                    │  (pure function)  │   hardware allowed?
                    └─────────┬─────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼────┐          ┌─────▼─────┐         ┌─────▼─────┐
   │ Software│          │   QSV     │         │  NVDEC    │
   │ libavc  │          │ -hwaccel  │         │ -hwaccel  │
   │         │          │   qsv     │         │   cuda    │
   └────┬────┘          └─────┬─────┘         └─────┬─────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │  FrameSource contract
                              │   open/send_packet/receive_frame/drain/close
                    ┌─────────▼─────────┐
                    │ Integrity stage   │   count + sequence + drain proof
                    │ (debug: full)     │   on violation -> software retry
                    └─────────┬─────────┘
                              │  ValidatedFrameSource
                    ┌─────────▼─────────┐
                    │     Encoder       │   rigaya / x265 / svt-av1  (unchanged)
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │ mux + preservation│   unchanged
                    └───────────────────┘
```

The single structural change: **decode becomes its own stage with its own
contract and its own verification**, instead of being a flag on an
encoder command line.

## 4. Decoder API

The project already has an encoder protocol (`encoders/base.py`). The
decoder abstraction should mirror it rather than invent a third idiom.
The **narrowest** change reuses the existing `build_args` seam: a decoder
is a *frame source* that the encoder consumes.

Two implementation shapes are possible; the choice is deferred to
Phase 2 and is a genuine trade-off, not a detail:

### Option A — process-level (recommended first)

The decoder stays a subprocess, driven exactly as it is today, but its
command line and its verification become the decoder abstraction's
responsibility.

```
class FrameSource(Protocol):
    def command(self, src: Path, out: Path, fmt: Format) -> list[str]: ...
    def expected_frames(self, facts: ContainerFacts) -> FrameExpectation: ...
    def verify(self, facts, produced: FrameCount) -> VerifyResult: ...
```

- Minimal blast radius; reuses `run_hw_tool` progress parsing and the
  existing failure taxonomy.
- Verification is limited to what a subprocess boundary exposes:
  output frame count (from `ffprobe` **and** the tool's own
  encoder-reported count) plus, in a debug mode, a fingerprint pass.
- Cannot observe `EAGAIN` / `AVERROR_EOF` directly — but with the FFmpeg
  CLI those are internal and the CLI's drain is correct, which is proven
  by measurement.

### Option B — in-process (only if A proves insufficient)

A real `send_packet` / `receive_frame` / `drain` loop via PyAV or raw
libavcodec bindings.

```
open(source) -> Decoder
Decoder.send_packet(pkt) -> None | raises EAGAIN
Decoder.receive_frame() -> Frame | EAGAIN | EOF
Decoder.drain()          -> iterate until AVERROR_EOF
Decoder.close()          -> None
```

- Needed only if a future defect requires observing decoder state
  (`hw_frames_ctx`, surface queue depth, per-packet acceptance).
- Adds a hard dependency (PyAV/FFmpeg ABI) to a project that is currently
  pure-stdlib + external binaries — a real cost for a self-contained
  Windows package.

**Recommendation: implement Option A first.** The evidence does not
require in-process decoder control; it requires *choosing the right
reader* and *verifying the result*.

## 5. Frame integrity

Three tiers, cheapest first. Cost is the reason they are tiers and not
one big check.

### Tier 0 — structural expectation (free, no decode)

From `ContainerFacts` (already implemented in this investigation as
`mp4struct.leading_picture_function`):

```
expected_frames      = container sample_count
hardware_head_loss   = pictures_before_first_keyframe     # 3 for XAVC HS, 2 for H.264 422
```

This is the check that would have caught the defect **before** running
anything, and it costs one moov parse. It is also the acceptance
criterion for a hardware path: if the reader cannot promise
`expected_frames`, it is not usable for XAVC.

### Tier 1 — count reconciliation (cheap, every run)

Collect **all** frame counts that are already available and compare them:

| Source | Currently |
|---|---|
| container sample count (source) | used |
| decoder-produced frame count (output intermediate) | used, via `ffprobe` packets |
| **encoder-reported frame count** (rigaya `encoded N frames`) | **parsed then discarded** (`hw.py:217`, `1kt.py:175`) |
| progress-line final value | parsed |
| `ffprobe -count_frames` on the output | not used |

The third row is free and is the most direct in-band signal of a decode
drop. Reconciliation of these four, with a documented tolerance of zero
for Sony, closes the hole.

### Tier 2 — sequence integrity (debug / CI only)

A fingerprint pass, exactly as built in this investigation:
`hwdownload → scale=64:64 → format=gray → sha1 per frame`.
Run gated behind a debug flag and in the regression suite, not on every
production file, because it costs a second decode.

Drain handling in either option:

- **EAGAIN** is never an error and never terminates a loop; it means
  "feed another packet" / "no frame ready".
- **EOF** is reached only after the decoder has been flushed; the drain
  loop runs until it returns `AVERROR_EOF`, never until the last packet
  is sent. FFmpeg CLI gets this right; any in-process loop must replicate
  it explicitly.
- The **count of frames obtained after the last packet was sent** should
  be recorded. For these streams a correct decoder yields a non-zero
  drain tail; a zero drain tail is a red flag. Reorder depth from `ctts`
  gives the expected magnitude.

## 6. Fallback

Fallback is required, but **not for the reason it is usually required**.

- A fallback that triggers on a *count mismatch* is too late and too
  coarse: it cannot repair a sequence error, it cannot distinguish
  "decoder dropped a frame" from "encoder dropped a frame", and on this
  corpus it would never fire for FFmpeg hwaccel (which is exact) while
  firing on 100 % of rigaya `--avhw` runs.
- The correct trigger is **capability + structural preconditions**, i.e.
  decide *before* decoding.

```
allowed_hardware(src, facts, caps):
    if not caps.has_decoder(codec, profile, chroma, depth):
        return SOFTWARE          # e.g. QSV + H.264 High 4:2:2 10-bit -> rc=69
    if policy == "software":
        return SOFTWARE
    if facts.pictures_before_first_keyframe > 0 and backend_is_rigaya_reader:
        return SOFTWARE          # the confirmed loss case
    return HARDWARE
```

Fallback is then a **safety net**, not the primary control:

| Trigger | Action |
|---|---|
| capability absent (probe) | software, before decoding, logged as INFO not WARNING (it is a plan, not a failure) |
| decoder init fails (`rc != 0`, 0 frames) | software retry, counted, logged WARNING (this is the QSV/H.264-422 case) |
| frames produced ≠ expected | **fail the file** and retry in software; never silently accept, never pad, never drop |
| debug fingerprint mismatch | fail the file, retry in software, record the divergence window |

**Automatic, yes — but it must be observable.** Every fallback carries a
reason code into the log, the CSV and `report.json`, and the run summary
reports the hardware/software split. A silent fallback is how QSVEncC's
"requested `--avhw`, silently used `avsw`" behaviour
(`hardware_backend_design.md` §5.6) went unnoticed; this design makes the
effective reader an asserted output, not an assumption.

## 7. Compatibility matrix

Derived from measurement on this machine (Arc 140T + RTX 5070), and from
the project's own capability notes. "Hardware decode" = FFmpeg hwaccel.

| Source format | QSV (Arc 140T) | NVDEC (RTX 5070) | Recommended |
|---|---|---|---|
| HEVC Main10 4:2:0, 8/10-bit, LongGOP | ✅ measured exact | ✅ measured exact | either; NVDEC faster here |
| H.264 High 4:2:2 10-bit | ❌ `rc=69`, `IO surface unsupported` | ✅ measured exact | NVDEC, else software |
| H.264 4:2:2 8-bit | probe required | ✅ | NVDEC |
| HEVC 4:2:2 8/10-bit | probe required | ✅ (Blackwell NVDEC) | NVDEC |
| All-I / intra-only | not in corpus — **unverified** | not in corpus — **unverified** | software until tested |
| 1080p | not in corpus — expected safe | not in corpus | either |
| 8-bit XAVC | not in corpus — **unverified** | not in corpus | software until tested |
| DJI (HEVC Main10, no edit list, no ctts) | — | ✅ measured exact | either |
| AV1 source | probe required | probe required | software |

Encode pipeline impact: **none**. The design keeps the encoder interface
byte-for-byte; the decoder sits in front of it and hands it a frame
stream. `nvenc.json` / `qsv.json` / `x265.json` are untouched.

## 8. What this design deliberately does not do

- It does not add hardware decode where software decode is adequate.
  Indicative warm-up figures on one long 4K60 HEVC Main10 clip
  (not a benchmark; see
  [`implementation-plan.md`](implementation-plan.md) §17.7):

  | Path | fps |
  |---|---|
  | software | ≈71 (1.00×) |
  | QSV (`-hwaccel qsv` + hwdownload) | ≈60 (0.85×) |
  | NVDEC (`-hwaccel cuda -c:v hevc_cuvid`) | ≈93 (1.31×) |
  | NVDEC (auto) | ≈103 (1.45×) |
  | NVDEC (explicit `cuda` surfaces + hwdownload) | ≈148 (2.07×) |

  NVDEC is faster than software; **QSV is not** for this content, because
  the `hwdownload` transfer of a 4K 10-bit surface costs more than the
  decode saves. So "hardware decode" is not one decision — the two
  vendors behave differently, and the configuration within one vendor
  differs by more than 2×. Hardware decode should be justified as *CPU
  headroom for parallel work*, and per-vendor, not as a blanket speed-up.
  The feasibility conclusion does not depend on any of these numbers; it
  rests on frame-sequence integrity (see [`README.md`](README.md)).

- It does not add a "correct the frame count" step. Padding, dropping, or
  PTS rewriting to make counts agree are explicitly out of scope. A
  mismatch fails the file.

- It does not change the mux, the preservation pipeline, or the encoder
  interface.
