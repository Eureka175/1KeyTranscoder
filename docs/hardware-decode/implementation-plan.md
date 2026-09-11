# Hardware Decode Adaptation — Implementation Plan (Phase 2, not yet executed)

> **Phase 1 stops here.** This document specifies *what would be changed,
> why, how, how it would be verified, how it would fall back, and what
> the risks are*. No production code has been modified.
>
> Evidence: [`root-cause.md`](root-cause.md) ·
> [`divergence.md`](divergence.md) · [`qsv.md`](qsv.md) ·
> [`nvdec.md`](nvdec.md) · technical design: [`design.md`](design.md)

---

## 17.1 Current architecture problems

Current data flow:

```
input
  ↓
demux           (rigaya reader: libavformat)
  ↓
decoder         (rigaya reader: avsw | avhw — chosen by a literal flag)
  ↓
frame           (converted to p010/yv12 and pushed to the encoder)
  ↓
encoder         (NVEncC / QSVEncC)
  ↓
intermediate .mov → MP4Box mux → preservation → deliverable
```

Where the problems are:

### P1 — Decode is an encoder flag, not a stage

`encoders/nvencc.py:107` and `encoders/qsvencc.py:102` emit
`["--avsw", "--video-track", "1", "-c", codec, "--output-depth", depth, ...]`
as an unconditional literal block. Decode has no interface, no capability
model, no contract and no verification of its own. Switching to hardware
is a one-token edit (`--avhw`), which is why the defect is reachable at
all (`docs/design/hardware_backend_design.md` §4.3 pre-authorises exactly
that edit).

### P2 — The reader silently substitutes itself

QSVEncC falls back to `avsw` when its hardware reader cannot handle a
format, **without failing**. `hardware_backend_design.md` §5.6 records
this. A run can therefore report success while the effective decoder is
not the requested one. Nothing in the current code asserts which reader
actually ran.

### P3 — The frame-count gate counts the wrong thing

`core/batch_hw.py:336-363` compares `count_frames(output)` with
`count_frames(source)`, and `core/probe.py:316-333` returns
`nb_read_packets` — **demuxed packets**, not decoded frames, despite its
docstring. Consequences:

- it cannot observe a decoder that emits fewer frames than packets it consumed;
- it cannot detect reorder, duplication, or a PTS-only change;
- on this corpus packets == frames, so the gate passes for the wrong reason.

### P4 — The most direct in-band signal is discarded

rigaya prints `encoded N frames`. `core/hw.py:217` parses it into
`last_frame`, and `1kt.py:175` reads it — but it never reaches a gate. It
is the cheapest possible decode-drop detector and it is thrown away.

### P5 — A rejected result is not invalidated

`hw_encode_with_fallback` does not unlink the output that failed the 1:1
check, and `_encoded_ok` (`preservation/pipeline.py:175-196`) accepts any
output with ≥1 packet. A second run can therefore reuse a
frame-count-rejected intermediate and skip the gate entirely.

### P6 — Frame-count divergence is non-fatal at the default check level

`preservation/validate.py:213-214` does record it:

```python
eq("video.frame_count", sv.get("nb_frames"), ov.get("nb_frames"))
```

but `eq` (`validate.py:141-145`) records a difference as **MODIFIED**, and
the `critical_modified` whitelist (`validate.py:452-477`) covers
`video.timescale`, `video.track_duration`, `video.stts`, `video.elst`,
`video.codec` and `video.color_*` — **not `video.frame_count`**.
`structural_success` is defined as
`not critical_missing and not critical_modified` (`validate.py:482`), so a
frame-count divergence yields `structural_success=True`.

Note the asymmetry that makes this easy to misread: a *MISSING*
`video.frame_count` **would** be critical, because `critical_missing`
(`validate.py:443-449`) catches every `MISSING` item whose name starts with
`video.`. The divergence case is *MODIFIED*, which is not on the list. At
`--check basic` — the level the real COLD production runs used — a wrong
frame count therefore exits 0. Only DJI and `--check full` make it fatal.

### P7 — VFR handling is asymmetric between backends

The rigaya paths emit `--avsync forcecfr` for VFR sources
(`nvencc.py:123-124`, `qsvencc.py:120-121`), which duplicates/drops
frames to force a constant rate, while the FFmpeg paths use
`-fps_mode passthrough` (`x265.py:201,246`, `svtav1.py:149,194`). Any
comparison between a hardware and a software run of the same file on a
VFR source is structurally guaranteed to differ in frame count.

### P8 — `_stts_sum` is ctts-blind

`preservation/isobmf.py:210-237` sums `stts` to recompute durations;
`isobmf` has no `ctts` handling at all. For these streams `stts` is
constant (1001) so it works today, but it is a latent timing defect
adjacent to the frame-sequence question.

---

## 17.2 Recommended architecture

```
Input
  ↓
ContainerFacts                (ffprobe + ISOBMFF box parse; no decode)
   • sample_count
   • pictures_before_first_keyframe      <-- the predictor
   • ctts reorder depth, stss/gop, elst
  ↓
Decoder abstraction
  ├── Software   (libavcodec native)             ← reference & fallback
  ├── QSV        (ffmpeg -hwaccel qsv)           ← measured exact
  └── NVDEC      (ffmpeg -hwaccel cuda)          ← measured exact
  ↓
Validated frame output
   • count reconciliation (container / decoder / encoder-reported / ffprobe)
   • debug: per-frame fingerprint sequence check
  ↓
Encoder  (rigaya / x265 / svt-av1 — interface unchanged)
  ↓
mux + preservation  (unchanged)
```

Two decisions this architecture makes, both forced by measurement:

1. **Hardware decode goes through FFmpeg `-hwaccel`, not the rigaya
   `--avhw` reader.** FFmpeg QSV/NVDEC was bit-exact on the entire Sony
   corpus; rigaya `--avhw` loses the leading pictures on 151/151 files
   (`root-cause.md` RC-2/RC-3).
2. **Decode gets a contract.** The defect was invisible for as long as it
   was an encoder flag.

---

## 17.3 Decoder API

The project already has an encoder protocol (`encoders/base.py`,
`EncoderBackend`) and a single funnel through which every hardware
command passes (`backend.command()` → `build_args`). The decoder
abstraction should reuse that seam rather than introduce a parallel
design.

```python
@dataclass
class ContainerFacts:                 # already prototyped in this phase
    sample_count: int
    pictures_before_first_keyframe: int
    ctts_reorder_depth: int
    max_gop: int | None
    elst_media_time: int | None
    timescale: int
    frame_duration_ticks: int

@dataclass
class DecodePlan:
    backend: str                      # "software" | "qsv" | "nvdec"
    reason: str                       # machine-readable, goes to logs/CSV/report
    expected_frames: int
    command: list[str]

class FrameSource(Protocol):
    name: str
    def plan(self, src: Path, facts: ContainerFacts, caps: DecodeCaps,
             policy: str) -> DecodePlan: ...
    def command(self, src: Path, plan: DecodePlan, fmt: Format) -> list[str]: ...
    def effective_reader(self, raw_log: str) -> str | None: ...
    def verify(self, facts: ContainerFacts, produced: FrameCounts,
               plan: DecodePlan) -> VerifyResult: ...
```

Lifecycle verbs the plan must cover, mapped to how they are realised in
each option:

| Verb | Option A (subprocess, recommended) | Option B (in-process, only if needed) |
|---|---|---|
| `open()` | spawn FFmpeg / rigaya | `av.open()` + `hwdevice` setup |
| `send_packet()` | implicit in the CLI | explicit; handle `EAGAIN` |
| `receive_frame()` | implicit | explicit; `EAGAIN` = feed more |
| `drain()` | CLI flush at EOF | explicit flush loop until `AVERROR_EOF` |
| `flush()` | `-fps_mode passthrough` | reset decoder state |
| `close()` | process exit + rc | `av.close()` |

**Reuse**: `encoders/base.py` protocol shape, `encoders/hw.py`
(`run_hw_tool`, `classify_failure`, `ask_fallback_new_console`),
`encoders/caps.py` probing + versioned cache, the `build_args` seam.

**Do not** introduce a third backend idiom. If the project later adopts
`EncoderBackend`-style protocols uniformly, `FrameSource` should be
declared next to it in `encoders/base.py`.

---

## 17.4 Frame Integrity

Ordered by cost; the first tier is free.

### Tier 0 — structural precondition (no decode)

```python
expected_frames = facts.sample_count
head_loss       = facts.pictures_before_first_keyframe
```

Validate the plan *before* decoding:

- a reader that cannot promise `expected_frames` is not usable for XAVC;
- `pictures_before_first_keyframe > 0` is the confirmed precondition for
  the rigaya `--avhw` loss and must block that reader.

### Tier 1 — count reconciliation (every run)

Reconcile four numbers instead of two, and record all four:

| # | Quantity | Source | Status today |
|---|---|---|---|
| 1 | container sample count (source) | `stsz` / `nb_frames` | used |
| 2 | output packets | `ffprobe -count_packets` | used |
| 3 | **encoder-reported frames** | rigaya `encoded N frames` | **parsed and discarded — wire it up** |
| 4 | decoded frames (debug) | `ffprobe -count_frames` | not used |

Policy for Sony: tolerance **zero** on counts 1↔2↔3. A mismatch fails the
file and triggers the software retry; it never pads, never drops, never
rewrites PTS.

### Tier 2 — sequence integrity (debug / CI)

The fingerprint pass built in this phase:

```
[hwdownload → format=p010le] → scale=64:64:flags=bilinear → format=gray
    → sha1 per 64x64 frame  (exact tier)
    → 8x8 block-mean signature (tolerant tier, for cross-pipeline)
```

- Enabled by a debug flag (`--verify-frame-sequence`) and in the
  regression suite.
- **Must** be compared with the tolerant tier when the two sides differ
  in colour conversion; a byte-exact comparison across a
  `yv12(10bit) → p010 → yv12(10bit)` boundary yields false "every frame
  differs" (measured: ±1 luma code, 30/30 frames within 2 codes).

### Drain / EAGAIN / EOF contract

```
send all packets
  EAGAIN from send    -> means "receive first"; never fatal
receive until EAGAIN  -> means "send more"; never fatal
at EOF: flush decoder
receive until EOF     -> AVERROR_EOF terminates the loop
assert: drain_tail == frames_received_after_last_packet
```

Record `drain_tail`. For these streams `ctts` reorder depth gives the
expected magnitude (5 frames for XAVC HS, 3 for the H.264 group). A
`drain_tail` of 0 on a reordered stream is a red flag.

---

## 17.5 Fallback

```
decide BEFORE decoding:
    capability missing (probe)                 -> SOFTWARE   (INFO, planned)
    policy == software                         -> SOFTWARE
    facts.pictures_before_first_keyframe > 0
        AND reader is rigaya avhw              -> SOFTWARE   (INFO, planned)
    otherwise                                  -> HARDWARE

during/after decoding (safety net):
    decoder init failure (rc != 0, 0 frames)   -> SOFTWARE retry (WARNING)
    produced != expected                       -> FAIL FILE + SOFTWARE retry (ERROR)
    sequence mismatch (debug tier)             -> FAIL FILE + SOFTWARE retry (ERROR)
```

Answers to the required questions:

- **When may QSV/NVDEC be used?** Only through FFmpeg `-hwaccel`, only for
  formats whose capability probe passes, and only when the structural
  precondition holds.
- **When must it fall back?** Any capability miss, any init failure, any
  count mismatch, any sequence mismatch. Also unconditionally for
  Sony XAVC if the rigaya `--avhw` reader were used.
- **How is fallback triggered?** Plan-time decisions (cheap, deterministic)
  plus runtime failure classification using the existing
  `hw.classify_failure` taxonomy.
- **Is fallback automatic?** Yes — but **never silent**. Every fallback
  writes a reason code to the per-file log, the CSV and `report.json`,
  and the run summary reports the hardware/software split. The QSVEncC
  silent `avsw` substitution is precisely the failure mode this prevents.

**Is fallback needed at all?** Yes, for three reasons: hardware
capability is format-specific (QSV has no H.264 4:2:2 10-bit and fails
with `rc=69`); the regression surface is large; and a driver or FFmpeg
update can change behaviour between releases. But fallback is a *safety
net*, not the control mechanism — the control is deciding correctly up
front.

---

## 17.6 Compatibility

| Dimension | Assessment | Basis |
|---|---|---|
| 10-bit 4:2:0 HEVC (XAVC HS) | ✅ QSV and NVDEC both exact | corpus-wide, bit-identical |
| 4:2:2 (H.264 High 4:2:2 10-bit) | QSV ❌ (`rc=69`), NVDEC ✅ | 5/5 files |
| 4:2:2 HEVC | NVDEC expected ✅ (Blackwell), QSV probe required | project capability notes; not in corpus |
| HEVC (LongGOP, GOP 60, `ctts` depth 5) | ✅ exact on both | corpus-wide |
| H.264 (LongGOP, GOP 30, `ctts` depth 3) | NVDEC ✅ | 5/5 files |
| All-I / XAVC S-I | **unverified** — no corpus material | — |
| 8-bit XAVC | **unverified** — no corpus material | — |
| 1080p | **unverified** — no corpus material | — |
| Sony XAVC (edit list, priming, `rtmd`) | ✅ under FFmpeg hwaccel; ❌ under rigaya `--avhw` | corpus-wide |
| DJI (no edit list, no ctts) | ✅ exact | measured |
| Existing encode pipeline | **unchanged** — encoder interface untouched | by design |
| `nvenc.json` / `qsv.json` / `x265.json` profiles | **unchanged** | by design |

Rules that follow:

- Never enable QSV hardware decode for H.264 4:2:2; route to NVDEC or
  software.
- Never enable the rigaya `--avhw` reader for Sony XAVC.
- Treat All-I / 8-bit / non-4K as unsupported for hardware decode until a
  regression fixture exists.

---

## 17.7 Performance

**Performance is not what this phase decided.** The feasibility question —
can hardware decode be used without corrupting the frame sequence — is
answered in [`README.md`](README.md) and
[`investigation.md`](investigation.md) §11 from frame-integrity evidence,
not from speed. What follows is *context for Phase 2 ordering only*.

Incidental warm-up measurements, one long 4K60 HEVC Main10 clip
(1,740 frames, `车内高晃动适中噪点.MP4`), single process, canonical
scale+format graph included, on a laptop that had been under load:

| Path | fps | vs software |
|---|---|---|
| software (`libavcodec` native) | ≈71 | 1.00× |
| QSV (`-hwaccel qsv` + `hwdownload`) | ≈60 | 0.85× |
| QSV with FFmpeg-side download | ≈52 | 0.73× |
| NVDEC (`-hwaccel cuda -c:v hevc_cuvid`) | ≈93 | 1.31× |
| NVDEC (`-hwaccel cuda` auto) | ≈103 | 1.45× |
| **NVDEC (`-hwaccel_output_format cuda` + `hwdownload`)** | **≈148** | **2.07×** |

Explicitly **not a benchmark**: single warm-up run per mode, no repeats,
no round-robin interleaving, and a machine whose thermal state drifted
measurably (an earlier sequential run of the same software path spanned
48 → 79 fps). Treat these as direction, not as results. A Phase-2
decision to enable hardware decode by default must rest on its own
controlled end-to-end measurement, and §17.9 S9 exists for that purpose.

What the direction does tell us for planning:

- **NVDEC is the only hardware path worth enabling for speed.** It beats
  software even in the least favourable configuration measured.
- **QSV is not a speed win on this machine** for this content: the
  `hwdownload` transfer of a 4K10-bit surface costs more than the decode
  saves. Its value would be freeing CPU, not raw decode rate.
- **The real lever is CPU headroom.** Decode is concurrent with encode in
  the production pipeline, and encode is the long pole; hardware decode
  matters most when several jobs run at once.
- **Fallback cost** is a full decode before the software retry (~2× for
  that file), which is exactly why the design decides at plan time
  (Tier 0, §17.4) rather than discovering mismatches at runtime.

---

## 17.8 Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Driver dependency: Intel/NVIDIA decode behaviour changes with driver updates | High | capability probe keyed on driver version (existing cache pattern); regression corpus re-run on driver change |
| R2 | FFmpeg version dependency: hwaccel defaults differ across builds | High | pin the bundled `tools/ffmpeg.exe` (9.0.1) — the `PATH` copy is 8.0.1 and must not be used; assert the version at startup |
| R3 | Decoder-specific behaviour: `hevc (native)` with `-hwaccel` is ambiguous in the stream-mapping line | Medium | assert the `hwaccel ... initialisation` / `Loaded lib:` evidence lines, not the mapping string (`root-cause.md` RC-6) |
| R4 | Pixel-format conversion: `hwdownload` needs a depth-matched format; `nv12` is invalid for 10-bit and negotiation silently picks `monow` | Medium | always pair `hwdownload` with `format=p010le` (10-bit) / `nv12` (8-bit); this bit the investigation tooling first |
| R5 | Hardware frame transfer: 24.9 MB/frame download dominates runtime | Medium | keep `hwdownload` off the hot path; measure before enabling |
| R6 | Silent reader substitution (QSVEncC → `avsw`) | High | assert the effective reader from `Input Info`; record it in the log and report |
| R7 | False negatives from byte-exact verification across a conversion boundary | Medium | two-tier fingerprint; tolerant tier for cross-pipeline |
| R8 | Regression risk to the preservation pipeline | Medium | the design does not touch mux/preservation; full-chain regression is an acceptance gate |
| R9 | Frame-count "fixes" that hide the defect (padding/dropping/PTS rewrite) | High | explicitly forbidden; a mismatch fails the file |
| R10 | Verification cost on every file | Low | Tier 0/1 free; Tier 2 debug-only |
| R11 | Resume reuses a rejected intermediate (P5) | Medium | unlink the rejected output before retry |
| R12 | VFR asymmetry between backends (P7) | Medium | unify on `-fps_mode passthrough` semantics or document the divergence explicitly |

---

## 17.9 Implementation steps

Ordered by dependency and risk. Each step ships with its own tests and
leaves the tree releasable.

```
S1  Decoder abstraction skeleton (Option A)
      • ContainerFacts extractor (reuse the Phase-1 mp4struct code)
      • FrameSource protocol + DecodePlan + reason codes
      • wire into the existing build_args seam; NO behaviour change yet
      • tests: container-facts fixtures, plan decisions, no decode

S2  Integrity Tier 0 + Tier 1
      • structural expectation check before decode
      • reconcile the four frame counts (incl. wirering up rigaya's
        "encoded N frames", currently discarded)
      • unlink rejected output (P5)
      • tests: unit + synthetic mismatch injection

S3  Fix the frame-count metric in core/probe.py (P3)
      • rename/fix count_frames semantics; keep a packets variant
      • make video.frame_count fatal for Sony at --check basic (P6)
      • tests: assert the packet vs frame distinction with a fixture
      • NOTE: this changes gate behaviour -> full regression required

S4  FFmpeg-based QSV hardware decode (Sony path)
      • -hwaccel qsv + hwdownload + depth-matched format
      • capability probe: refuse H.264 4:2:2 on QSV
      • tests: corpus subset, count + tolerant sequence

S5  FFmpeg-based NVDEC hardware decode (Sony path)
      • -hwaccel cuda -c:v <codec>_cuvid (+ optional hw_output_format)
      • assert hwaccel evidence lines
      • tests: corpus subset incl. the H.264 4:2:2 group

S6  Fallback state machine
      • plan-time decisions + runtime classification
      • reason codes into log / CSV / report.json
      • tests: forced capability miss, forced init failure, forced
        count mismatch; assert audit trail and non-silence

S7  Unify VFR handling (P7) and ctts awareness in isobmf (P8)
      • make the backend frame-rate semantics consistent
      • tests: VFR fixture, ctts non-constant stts fixture

S8  Regression corpus + CI gate
      • freeze a stratified corpus: per recording mode, at least one
        30/150/360/3000+ frame clip, plus the H.264 4:2:2 group and one
        DJI negative control
      • golden profile: container sample count + leading-picture count +
        fingerprint sequence
      • full-chain: valid ate compare + Gyroflow on Sony material

S9  Performance benchmark and go/no-go
      • end-to-end wall clock, software vs QSV vs NVDEC, on a long clip
      • go/no-go on enabling hardware decode by default
      • if the gain is not material, ship the abstraction + verification
        and keep software decode as the default

S10 Full project regression + merge
      • tests/full_autotest.py, tests/run_selfcheck.py
      • preservation chain on Sony + DJI
      • then merge research/hardware-decode-adaptation -> main
```

Ordering rationale: **S1–S3 are valuable even if hardware decode is never
enabled.** They fix a mislabelled metric, an unwired signal, a
non-fatal gate and a resume hole — defects that exist today, in the
software-only path. Hardware decode (S4–S6) sits behind them so that it
lands on top of working verification rather than replacing it.

**Stop condition for the whole effort**: if S9 shows no material
end-to-end gain, the correct outcome is to keep software decode as the
default and ship only S1–S3 + S8. The measurements in §17.7 make that a
real possibility, and it should be decided on data, not momentum.
