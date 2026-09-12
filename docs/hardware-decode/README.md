# Hardware Decode Adaptation — Investigation (Phase 1)

`research/hardware-decode-adaptation` · base `main` @ `f721b1f`

> **Phase 1 is investigation, verification, root-cause analysis and design
> only. No production decode, encode, mux, preservation or channel-sync
> code was modified.** Forbidden in this phase and not done: production
> QSV/NVDEC implementation changes, encoder/mux/preservation changes,
> changing the default backend, adding production fallback, padding or
> dropping frames to align counts, rewriting PTS, or weakening tests.

## Feasibility verdict — the question this phase answers

**Question: can 1KeyTranscoder use QSV / NVDEC hardware decoding for Sony
XAVC without corrupting the frame sequence?**

| Verdict | Answer | Basis |
|---|---|---|
| **FFmpeg QSV hardware decode** | ✅ **FEASIBLE for HEVC 4:2:0** · ❌ **NOT available for H.264 4:2:2 10-bit** | 146/146 bit-identical to software; the other 5 fail loudly (`rc=69`), never silently |
| **FFmpeg NVDEC hardware decode** | ✅ **FEASIBLE for the whole corpus** | 151/151 bit-identical to software, both codecs, both chroma formats |
| **rigaya `--avhw` reader** | ❌ **NOT FEASIBLE** | deterministic frame loss on 151/151 files, predicted exactly by container structure |
| **1KeyTranscoder today** | ✅ frame-exact, because it decodes in software | 8/8 real production deliverables, `delta = 0` |
| **Can it be adopted?** | ✅ **Yes, through FFmpeg `-hwaccel`, with format-level capability routing.** ❌ No, through the rigaya `--avhw` reader. | the two hardware decoders do not cover the same formats, so a single switch is the wrong abstraction |

What "feasible" means here, concretely — for every one of the 188,475
corpus frames, the hardware decode produced an **identical picture
sequence, identical PTS sequence and identical keyframe sequence** to
software decode. Not "the same count" — the same frames in the same
order. That is the property the project actually needs, and it is proven.

What remains **unproven** (and must not be assumed): All-I / XAVC S-I,
8-bit XAVC, 1080p, 4:2:2 HEVC, HDR — no such material exists in this
environment, so their feasibility is `Unconfirmed`.

**Throughput is not part of this verdict.** Indicative warm-up figures on
one long 4K60 clip (1,740 frames, thermally loaded laptop, not a
benchmark) were: software ≈71 fps, QSV ≈60 fps, NVDEC ≈93–148 fps. They
suggest NVDEC is faster and QSV is not, but the feasibility conclusion
above rests entirely on frame-sequence integrity, which was measured on
the whole corpus rather than on one clip.

## Start here

| Document | What it answers |
|---|---|
| [`investigation.md`](investigation.md) | the full structured report (12 sections) |
| [`corpus.md`](corpus.md) | which Sony material exists, and its identifying features |
| [`ground-truth.md`](ground-truth.md) | how software decode was established and validated |
| [`qsv.md`](qsv.md) | QSV results, per-parameter association, throughput |
| [`nvdec.md`](nvdec.md) | NVDEC results |
| [`divergence.md`](divergence.md) | per-frame fingerprinting and first-divergence analysis |
| [`root-cause.md`](root-cause.md) | Observation/Evidence/Hypothesis/Experiment/Result/Conclusion |
| [`design.md`](design.md) | target decoder architecture |
| [`implementation-plan.md`](implementation-plan.md) | Phase 2 plan, acceptance criteria, risks |
| [`nvencc-avhw-experiment.md`](nvencc-avhw-experiment.md) | **Phase 2, `research/rigaya-nvencc-avhw`** — where inside NVEncC `--avhw` the frames are actually lost, the reader-side patch candidates, and whether the reader is worth fixing |
| [`test-results/`](test-results/) | machine-readable results |

## Conclusion table

| 项目 | 结论 |
|---|---|
| **Sony corpus** | 151 files, 188,475 container frames, 64.87 GiB, all 3840×2160, all 10-bit LongGOP. Two modes only: **146 × XAVC HS** (HEVC Main10 4:2:0, 59.94p, GOP 60) and **5 × XAVC S** (H.264 High 4:2:2, 29.97p, GOP 30). No All-I, no 8-bit, no 1080p material exists in the environment. |
| **QSV affected** | **Not for frame integrity** — 146/146 HEVC files bit-identical to software. 5 failures are **capability**, not loss: H.264 High 4:2:2 10-bit has no Arc decoder (`Error querying IO surface: unsupported (-3)`, rc=69, 0 frames, loud). Zero silent frame loss. |
| **NVDEC affected** | **Not at all** — 151/151 bit-identical to software, both codecs, both chroma formats. |
| **Common failure** | Only the **rigaya `--avhw` reader** (NVEncC `avcuvid` + QSVEncC `avqsv`): drops frames on **151/151** Sony files. Not decoder-specific, not vendor-specific. The loss is **predicted exactly from container boxes**: `avhw_frames = container_samples − pictures_before_first_keyframe`, validated on **297/297** measured runs (NVEncC 151/151, QSVEncC 146/146). |
| **First divergence** | rigaya only, always at **frame index 0**: a strict **head truncation** of the leading pictures (3 for HEVC, 2 for H.264). FFmpeg QSV/NVDEC: **no divergence at all**. |
| **Flush-related** | **No.** Divergence is at the head; `in_avhw_not_avsw = ∅`; a drain defect would truncate the tail. `FLUSH_LOSS` and `REORDER_DELAY_ERROR` counts are zero everywhere. |
| **Reorder-related** | **No.** No reorder, no duplication, no PTS-only change. `ctts` reorder depth is fully honoured by both FFmpeg hardware paths. |
| **Project integration-related** | **Not the source of the observed loss.** Hardware decode is unreachable (`--avsw` literal at `encoders/nvencc.py:107`, `encoders/qsvencc.py:102`); 8/8 real production deliverables were frame-exact (delta 0). But the integration has **latent verification holes** that would let a future hardware path ship a wrong count at exit 0. |
| **Root cause confidence** | `Confirmed` for: the rigaya reader drops leading pictures; the loss equals `pictures_before_first_keyframe`; the edit list is **not** the trigger; FFmpeg over the same silicon is exact. `Unconfirmed`: the exact line of rigaya code, and behaviour on All-I / 8-bit / 1080p material. **Superseded in part by Phase 2:** the exact line *is* now known (see [`nvencc-avhw-experiment.md`](nvencc-avhw-experiment.md) §3) — the pictures are **decoded correctly and then discarded by NVEncC's hardware output stage**, not lost in the reader/decoder. The frame-loss *prediction* above is unaffected. |
| **Recommended fix** | Never use rigaya `--avhw` for XAVC. If hardware decode is adopted, drive it via **FFmpeg `-hwaccel`** (proven frame-exact) with **format-level capability routing** (QSV: HEVC/4:2:0 only; H.264 4:2:2 → NVDEC or software). Make decode a stage with a contract; validate the frame expectation from container boxes *before* decoding; reconcile four frame counts incl. the currently discarded `encoded N frames`. |
| **Fallback needed** | **Yes**, but as a safety net, not the control mechanism. Primary control is plan-time capability + structural preconditions; fallback triggers on capability miss, init failure, count mismatch or sequence mismatch. Must be automatic but **never silent**. |
| **Expected performance gain** | Indicative only, and **not** the basis of the verdict: one long 4K60 clip, warm-up runs on a thermally loaded laptop → software ≈71 fps, QSV ≈60 fps, NVDEC ≈93 fps, NVDEC with explicit `cuda` surfaces ≈148 fps. So **NVDEC is faster than software; QSV is not.** The durable justification for hardware decode is **CPU headroom for concurrent encodes**, decided per vendor. Not a benchmark — see `implementation-plan.md` §17.7. |
| **Implementation complexity** | **Medium.** Abstraction + verification + capability routing are self-contained and reuse existing seams (`build_args`, `EncoderBackend`, `caps.py`). The hard part is not the decoder — it is deciding correctly up front and verifying honestly. Verification fixes (S1–S3 + S8) are worth doing even if hardware decode never ships. |

## Headline findings

1. **The reported symptom does not reproduce through FFmpeg.** FFmpeg
   `-hwaccel qsv` and `-hwaccel cuda` are **bit-identical** to software
   decode across the entire Sony corpus (146/146 and 151/151). Not a
   single frame drop, duplicate, reorder, PTS error, flush loss or drain
   failure.

2. **The frame loss is real, and it is in the rigaya reader layer.**
   NVEncC `--avhw` and QSVEncC `--avhw` both lose frames on **100 %** of
   the Sony corpus — the same hardware decoders that FFmpeg drives
   correctly.

   > **Phase 2 refinement.** "Reader layer" means the rigaya *integration*
   > layer above the decoder, and specifically NVEncC's hardware **output
   > stage**, not the CUVID/NVDEC decoder. NVEncC's own trace shows all 30
   > packets reaching NVDEC and NVDEC emitting all 30 pictures, including the
   > three previously attributed to the reader, which are then dropped on a
   > timestamp test. See
   > [`nvencc-avhw-experiment.md`](nvencc-avhw-experiment.md) §2.2 and §3.

3. **The mechanism is known and predictable.** Sony XAVC clips code
   pictures *before* their first keyframe in presentation order, and carry
   an edit list whose presentation start is not a keyframe. The hardware
   reader emits only from the first IRAP and silently discards those
   leading pictures:

   ```
   avhw_frames = container_samples − pictures_before_first_keyframe
               = container_samples − 3   (XAVC HS, 146 files)
               = container_samples − 2   (H.264 4:2:2, 5 files)
   ```

   Verified against every measured case.

4. **It is not the edit list.** A Matroska remux with no edit list at all
   still loses 3 frames; an x265 re-encode carrying the *same* edit list
   and the *same* `ctts` loses none. The difference is the bitstream, not
   the container (`root-cause.md` RC-4).

5. **1KeyTranscoder cannot currently produce this bug**, because hardware
   decode is hard-coded off — but it has verification holes that would
   let the bug through the moment someone flips that flag
   (`investigation.md` §10).

6. **QSV and NVDEC are not interchangeable.** QSV has no H.264
   High 4:2:2 10-bit decode on this hardware and fails loudly; NVDEC
   handles it. A single "hardware decode" switch is the wrong abstraction.

## Classification taxonomy

Defined in the task and used verbatim by
`work/hwdecode/compare.py`; the counts below are the *entire* corpus.

| Classification | QSV | NVDEC | rigaya `--avhw` |
|---|---|---|---|
| `PASS` | 146 | 151 | 0 |
| `FRAME_DROP` | 0 | 0 | 151 |
| `FRAME_DUPLICATE` | 0 | 0 | 0 |
| `FRAME_REORDER` | 0 | 0 | 0 |
| `PTS_ERROR` | 0 | 0 | 0 |
| `FLUSH_LOSS` | 0 | 0 | 0 |
| `REORDER_DELAY_ERROR` | 0 | 0 | 0 |
| `DEMUX_MISMATCH` | 0 | 0 | 0 |
| `DECODER_ERROR` | 5 (capability) | 0 | 0 |
| `PROJECT_PIPELINE_ERROR` | 0 | 0 | 0 |
| `UNKNOWN` | 0 | 0 | 0 (by prediction) |

## Reproduction

```powershell
$FF = 'F:\1KeyTranscoder\tools\ffmpeg.exe'      # FFmpeg 9.0.1 — NOT the PATH build

# 1. corpus scan + classifications
python work/hwdecode/scan_corpus.py
python work/hwdecode/refacts.py
python work/hwdecode/leading.py

# 2. software / QSV / NVDEC, per-frame fingerprints
python work/hwdecode/run_batch.py --modes sw --modes qsv_native `
       --modes nvdec_cuvid --modes nvdec_auto --workers 3

# 3. classify + publish
python work/hwdecode/compare.py --modes qsv_native --modes nvdec_cuvid
python work/hwdecode/publish.py

# 4. the rigaya reader defect (the project's would-be hardware path)
python work/hwdecode/rigaya_reader.py --tools nvenc --tools qsv --readers=--avhw
python work/hwdecode/rigaya_divergence.py <sony-clip> --tool nvenc

# 5. root-cause isolation (container variants)
python work/hwdecode/experiment_container.py --sony <clip> --other <dji-clip> `
       --tool nvenc --out raw/exp.json

# 6. decoder identity proof + controlled benchmark
python work/hwdecode/verify_decoders.py --hevc <clip> --h264 <clip> --out raw/verify.json
python work/hwdecode/bench.py --file <clip> --modes sw qsv_native nvdec_cuvid --repeats 3 --out raw/bench.json
```

### Minimal reproduction of the defect

```powershell
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'   # 30 frames

& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avsw -c raw --output-res 64x64 -o NUL
#   -> encoded 30 frames
& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avhw -c raw --output-res 64x64 -o NUL
#   -> encoded 27 frames      <-- the defect, encoder removed from the loop
```

## Tooling

All investigation tooling lives in `work/hwdecode/` and is **not**
imported by production code.

> **Note on commits.** `work/` is listed in the project's `.gitignore`
> (line 37, "Preservation POC intermediates"), so the tooling, the raw
> per-frame fingerprints and the static-analysis notes stay local by
> project convention. Everything needed to reproduce the investigation is
> in this repository's history as documentation plus the published
> `test-results/*.json`; the scripts are re-creatable from the
> *Reproduction* section above and are described file-by-file below.
> Production code is untouched: `git diff main...HEAD --stat` is empty.

| File | Role |
|---|---|
| `hwenv.py` | pinned tool paths (bundled FFmpeg only), versions, subprocess helpers |
| `mp4struct.py` | read-only ISOBMFF parser; `leading_picture_analysis` predictor |
| `scan_corpus.py` / `summarize_corpus.py` | corpus discovery and ffprobe/XML metadata |
| `fingerprint.py` | per-frame fingerprint decoder (all modes) |
| `run_batch.py` | resumable corpus batch |
| `compare.py` | first-divergence + classification engine |
| `leading.py` / `refacts.py` | container-structure facts |
| `rigaya_reader.py` | rigaya reader measurements, encoder removed |
| `rigaya_divergence.py` | fingerprint-level divergence for the rigaya path |
| `experiment_container.py` | controlled container-variant experiment |
| `verify_decoders.py` | proves which decoder actually ran |
| `bench.py` / `perf.py` | controlled throughput measurement |
| `project_artifacts.py` | real production deliverables vs sources |
| `publish.py` | assembles `test-results/*.json` |

## Deliverables in this phase

```
docs/hardware-decode/README.md                     <- this file
docs/hardware-decode/investigation.md
docs/hardware-decode/corpus.md
docs/hardware-decode/ground-truth.md
docs/hardware-decode/qsv.md
docs/hardware-decode/nvdec.md
docs/hardware-decode/divergence.md
docs/hardware-decode/root-cause.md
docs/hardware-decode/design.md
docs/hardware-decode/implementation-plan.md
docs/hardware-decode/test-results/corpus.json
docs/hardware-decode/test-results/software-ground-truth.json
docs/hardware-decode/test-results/qsv-results.json
docs/hardware-decode/test-results/nvdec-results.json
docs/hardware-decode/test-results/comparison.json
docs/hardware-decode/test-results/summary.json

work/hwdecode/**            investigation tooling, raw data, static analysis
```

## Methodological cautions (read before trusting any number here)

1. **Always use `tools/ffmpeg.exe` (9.0.1)**, never the `PATH` build
   (8.0.1). All results in this report are from the bundled build.
2. **`-fps_mode passthrough` is mandatory** when counting frames.
   Without it FFmpeg inserts/drops frames to reach CFR and the measuring
   instrument invents the divergence.
3. **`hevc (native)` in FFmpeg's stream mapping does not mean software
   decode.** Decoder identity is asserted from
   `hwaccel ... initialisation` / `Loaded lib: nvcuvid.dll` /
   `Using device ...` lines.
4. **A frame count is not an integrity check.** An equal count can hide
   reorder or duplication; unequal counts say nothing about *which* frame
   moved. Always fingerprint.
5. **Byte-exact hashes are only valid within one pipeline.** Across a
   colour-conversion boundary (rigaya's `yv12(10bit)→p010→yv12(10bit)`)
   the same picture shifts by ±1 luma code. Use the tolerant tier.
6. **QSV frames require `hwdownload` with a depth-matched format.**
   `-hwaccel qsv` alone gives `AV_PIX_FMT_QSV` and swscale fails with
   `-40 (Function not implemented)`; `nv12` is invalid for 10-bit and
   format negotiation silently picks `monow`.
7. **A tool crash is not a decoder failure.** One apparent NVDEC failure
   was an investigation-tooling `KeyError`; it is documented and was
   re-run to PASS.
8. **Separate tooling failures from decoder failures — they look
   identical in a summary table.** Twelve `(file, mode)` runs initially
   reported no usable result. On inspection, **seven** were a defect in
   the investigation tooling (a `KeyError` raised when the `showinfo`
   record stream and the raw frame stream disagreed in length — fixed,
   and the runs redone) and **five** were genuine QSV capability refusals
   on H.264 High 4:2:2 10-bit (`rc=69`) that must be *preserved* as
   findings, not repaired away. `work/hwdecode/repair_exceptions.py`
   re-runs the former and refuses to overwrite the latter; every outcome
   is in `work/hwdecode/raw/repair-report.json`.
10. **Pair streamed metadata by the producer's own counter, never by
    position.** A positional merge of `showinfo` records against raw
    frames turned one lost log line out of ~700,000 into a fabricated
    `PTS_ERROR` (a one-frame PTS shift at index 3121 of a 3540-frame run,
    with bit-identical pictures). The merge is now keyed on `showinfo`'s
    `n:` counter, and `work/hwdecode/audit_alignment.py` verifies the
    invariant across every stored run. Post-fix: **0 skewed runs out of
    614**. This is the single most valuable methodological lesson in the
    investigation — it is exactly the class of error that produces
    convincing but wrong findings.
9. **Concurrent GPU load can corrupt a measurement without corrupting the
   file.** One rigaya run (`20260904_C1184.MP4`) aborted mid-decode with
   `Break in task NVDEC: unknown error`, `rc=1`, 1400 of 1407 frames,
   while a second GPU-heavy job ran concurrently. On a quiet re-run it
   returned exactly the predicted 1407. Benchmark numbers in
   `implementation-plan.md` §17.7 come from *uncontended sequential* runs
   for this reason; do not run two GPU-heavy measurement jobs at once.
