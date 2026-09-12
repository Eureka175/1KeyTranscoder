# End-to-End Hardware-Decode Benchmark — Transcoding Pipeline

`research/hwdecode-e2e-benchmark` · worktree `F:\1KT-e2e` · base `main` @ `15cf218`

> **No production code was modified.** `git diff main...HEAD --name-only`
> touches only this document. Encoders, mux, preservation, channel-sync
> and the default backend are untouched; the only file added outside
> `work/` (gitignored) is this report.

This is the Phase-2 §17.9 **S9 "Performance benchmark and go/no-go"**
that [`implementation-plan.md`](implementation-plan.md) left unexecuted.

---

## 0. The answer to the question that was asked

Phase 1 measured **NVDEC 433 fps against software decode 59 fps on DJI
material** and explicitly refused to conclude anything from it. This
benchmark was run to find out whether that decoder-level win survives
contact with the whole pipeline. It does not, in the way the number
suggests — and the reason is more useful than the number.

| Question | Answer | Evidence |
|---|---|---|
| **Is the whole transcoding pipeline faster?** | **No.** All four routes land within 6 % of each other, and the ranking is effectively determined by whether a pipe is in the path, not by where decode runs. | §4 |
| **Is the CPU released?** | **Yes, substantially — and this is the real win.** The candidate halves per-job CPU on DJI (29.1 % → 15.6 %) and cuts it by a third on Sony (27.9 % → 18.0 %), at identical wall-clock time. | §4.2 |
| **Does concurrency improve?** | **Not on this machine, and not for the reason hardware decode was supposed to fix.** Two concurrent 4K jobs already saturate the NVENC engine (~30 fps aggregate regardless of route), so the freed CPU has nothing to spend itself on. | §5 |
| **Is the FFmpeg → transfer → rigaya route viable?** | **Technically yes, practically no.** It is frame-exact, but costs 6 % wall clock and 0.5–0.9 GB VRAM for no throughput gain, because the hwdownload round-trip is pure added work. | §4.3 |
| **Does `--avhw` gain anything end to end?** | **Only CPU, at a frame-count cost on Sony** (−3 frames, the Phase-1 defect, reconfirmed). Its CPU advantage is real but smaller than the FFmpeg route's, and it is the only route that corrupts the timeline. | §4 |

**Bottom line.** On this hardware, 4K60 10-bit HEVC transcoding is
**NVENC-bound, not decode-bound**, once the GPU is in its normal clock
regime. Changing the decode path buys CPU headroom, not throughput — and
the CPU headroom cannot be converted into throughput because two
concurrent 4K jobs already exhaust the encoder. The value of hardware
decode here is **capacity planning for mixed workloads** (leaving CPU for
audio, muxing, verification and non-NVENC work), not faster transcodes.

---

## 1. A measurement-validity warning, and why this report exists twice

An earlier pass of this benchmark produced a **2.6×–3.5× speedup for
hardware decode on DJI material**. That result was wrong, and the way it
was wrong is the most important methodological finding in this document.

The identical command — same source, same frame count, same 84 380 kbps
output — was measured twice:

| | run A | run B |
|---|---|---|
| wall clock | **489.0 s** | **141.7 s** |
| `NVEncC` reported GPUClock | **1716 MHz** | **2695 MHz** |
| `NVEncC` reported VEClock | **1754 MHz** | **2334 MHz** |
| VE engine utilisation | **39.1 %** | **79.7 %** |

Nothing about the pipeline changed. The GPU was in a **downclocked
regime** in run A and could not be driven out of it, because the job
could not feed the encoder fast enough to justify a higher clock.

The mechanism: when the software decoder cannot sustain the encoder's
demand, the encoder idles between frames, the GPU drops to a low clock,
and the encoder gets *slower still*. It is a feedback loop, and it makes
"software decode" look catastrophically bad while actually measuring a
power state.

**Consequence:** every run in this report carries its GPU clock, and any
run whose median SM clock is below `THROTTLE_SM_CLK_MHZ = 2300` is
flagged `throttled: true`. The first pass — 26 result files — was
**quarantined** to `work/e2e/results_quarantine/` and is not used
anywhere below. All numbers here come from re-measurement in the high
clock regime.

> This is the same class of error as Phase 1's methodological caution #10
> (pairing streamed metadata by position rather than by the producer's
> counter): a convincing number produced by the instrument, not the
> system. It is recorded here rather than quietly deleted.

---

## 2. Method

### 2.1 Routes under test

| Scenario | Decode | Transfer | Encode |
|---|---|---|---|
| `sw_enc` — **BASELINE** | rigaya `--avsw` (libavcodec, software) | in-process | NVEncC / NVENC |
| `avhw_enc` — **CANDIDATE** | rigaya `--avhw` (NVDEC via `avcuvid`) | in-process (GPU surface) | NVEncC / NVENC |
| `ffhw_pipe` | FFmpeg `-hwaccel cuda -hwaccel_output_format cuda` (NVDEC) | `hwdownload,format=p010le` → `yuv420p10le` → y4m pipe | NVEncC / NVENC (re-uploads) |
| `ffsw_pipe` — **CONTROL** | FFmpeg software decode | `format=yuv420p10le` → y4m pipe | NVEncC / NVENC |
| `sw_decode` / `avhw_decode` | as above, encoder removed (`-c raw --output-res 64x64`) | — | — |

`ffsw_pipe` exists to separate "the FFmpeg pipe costs something" from
"NVDEC costs something". Without it, the `ffhw_pipe` result would be
uninterpretable.

`--avhw` was tested because it is the one-token change the project's
design document pre-authorises (`hardware_backend_design.md` §4.3), even
though Phase 1 established that it loses frames on Sony XAVC. A route
that is fast but wrong still needs its speed measured, otherwise the
trade-off cannot be stated.

### 2.2 Encode settings

The encode half is **production's**, not the benchmark's: the full `UHQ`
profile from `nvenc.json` (`preset quality`, `tune uhq`, `qvbr 23`,
`--aq-strength 6 --aq-temporal`, `--lookahead 32`, `--bframes 5`,
`--bref-mode middle --ref 5 --tf-level 4 --nonrefp`, `qp-init 20:22:24`,
`main10`, `tier high`, `level 6.1`, BT.709 signalling). Key-by-key
provenance and the deliberately-omitted keys are in
`work/e2e/base_profile.json`.

Production passes `--audio-copy` on the non-Sony path
(`core/batch_hw.py:911,930`). The benchmark fixtures are video-only, so
the flag is a no-op here; it is exposed as `AUDIO_COPY` in the harness so
the measurement's scope is explicit. **The reported timings isolate the
video transcode stage** — audio is a stream copy in production and does
not scale with decode path.

### 2.3 Fixtures

Built by stream copy only (no decode, no encode) — see
`work/e2e/make_fixtures.py`.

| Fixture | Codec / format | Frames | Duration | Size |
|---|---|---|---|---|
| `sony_4k60_10bit_60s` | HEVC Main10 4:2:0 10-bit, 3840×2160, 60000/1001 | 3 599 | 60.04 s | 1 075 MB |
| `sony_4k60_10bit_600s` | same, 5 concatenated takes | 35 967 | 600.05 s | 10 738 MB |
| `dji_4k60_10bit_60s` | same, single take | 3 597 | 60.01 s | 786 MB |
| `dji_4k60_10bit_600s` | stream-copy loop ×10 of the above | 35 965 | 600.02 s | 7 865 MB |

**Corpus limitation, stated plainly:** the repository holds only
**364.4 s** of DJI 4K60 (3840×2160, 60000/1001) material. A true
10-minute DJI timeline cannot be built by concatenation without mixing
frame rates — the remaining DJI clips are 30000/1001, and a mixed-rate
concatenation produces a VFR fixture that would make every throughput
number incomparable. The 10-minute DJI fixture is therefore a **loop**,
which measures *scaling* honestly (each repeat is a genuine
IDR-delimited HEVC stream with its own edit list) but is
content-repetitive. The Sony 10-minute fixture carries the content
variety instead.

Sony fixtures retain the leading-picture structure that Phase 1
identified as the `--avhw` trigger (see the Δframes column, §4.1).

### 2.4 What was recorded

Per run: wall clock; end-to-end fps; rigaya's reported encode fps;
NVEncC's own `encode time`; decoded/encoded frame count and Δ vs the
container sample count; process-tree CPU-seconds; process-tree peak RSS;
peak VRAM (per-PID, via `\GPU Process Memory(*)\Local Usage`); system CPU
(`% Processor Time` and `% Processor Utility`); per-engine GPU utilisation
attributed to the job's own PIDs (`videoencode` / `videodecode` / `copy` /
`cuda`); SM clock, VE clock, GPU temperature and power; output size and
bitrate.

**Two metrics are recorded but must not be used as headline numbers:**

1. **`% Processor Utility`** — over-calibrated on this platform: it read
   **129 % on a nearly idle machine** while `% Processor Time` read
   58.6 %. It is kept only because rigaya's own `CPU: n.n` progress field
   is a utility-style figure and the two must be comparable.
   `% Processor Time` is the number used below.
2. **`progress_points` / `ramp`** — rigaya never flushes its progress
   line while stdout is a pipe. In a 489 s run all 427 progress updates
   arrived in one burst at process exit, carrying the same arrival
   timestamp. The per-second timeline is not recoverable from a
   redirected NVEncC stdout. `progress_points` is retained for its frame
   counts, not as a time series. The authoritative end-to-end duration is
   the harness wall clock; the authoritative encoder-side duration is
   rigaya's `encode time`.

### 2.5 Machine and conditions

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 5070 Laptop, 8 151 MiB, driver 616.56 |
| GPU clocks | max SM 3 090 MHz; observed 2 750–2 812 MHz (high regime), ~1 716 MHz (low regime) |
| PCIe | Gen5 ×8 (max ×16) |
| CPU | Intel Core Ultra 9 285H, 16 cores / 16 threads, 2.9 GHz |
| RAM | 31.5 GB |
| OS power plan | Honor Performance |
| FFmpeg | bundled `tools/ffmpeg.exe` 9.0.1 (NOT the PATH build) |
| NVEncC | 9.31 (r4047), NVENC API v13.1 |
| Ambient load | background desktop apps (`msedge`, QQ, WeChat) hold ~20 % CPU baseline; recorded, not eliminated |

**Contention caveat:** a second NVENC session from a *different*
worktree (`F:\1KT-avhw`, rigaya source research) was observed on the GPU
during part of this work and exited on its own. All runs reported here
are single-job or 2-way as labelled, and each run's GPU clock is recorded
so a contended run is identifiable. Runs measured while the GPU sat in
the low-clock regime were quarantined regardless of cause.

**Operator constraint honoured:** 4K concurrency was capped at **2**.
4 concurrent 4K NVENC sessions exhaust the 8 GB GPU — one 4K10-bit
session alone peaks at 2.8–3.7 GB. 4-way concurrency is deferred until
1080p material exists.

### 2.6 Reproduce

```powershell
cd F:\1KT-e2e
python work/e2e/make_fixtures.py                     # stream-copy fixtures
python work/e2e/bench_e2e.py --fixtures work/e2e/fixtures.json --matrix smoke --list
python work/e2e/bench_e2e.py --fixtures work/e2e/fixtures.json --matrix core  --jobs sony60_sw_x1,dji60_sw_x1 --tag final_x1
python work/e2e/bench_e2e.py --fixtures work/e2e/fixtures.json --matrix core  --jobs dji60_sw_x2 --tag final_x2
python work/e2e/bench_e2e.py --fixtures work/e2e/fixtures.json --matrix long  --jobs sony600_x1,dji600_x1 --tag final_long
python work/e2e/analyze.py
```

---

## 3. Decoder in isolation (encoder removed)

This is the number that looks like a win, and it is real — for the
decoder only.

| Fixture | Route | Wall s | Decode fps | vs software |
|---|---|---|---|---|
| Sony 4K60 10-bit 60 s | software (`--avsw`) | 41.0 | **87.8** | 1.00× |
| Sony 4K60 10-bit 60 s | `--avhw` (NVDEC) | 10.9 | **331.4** | **3.8×** |
| DJI 4K60 10-bit 60 s | software (`--avsw`) | 60.7 | **59.2** | 1.00× |
| DJI 4K60 10-bit 60 s | `--avhw` (NVDEC) | 8.3 | **433.5** | **7.3×** |

Two things stand out.

1. **DJI software decode runs at 59.2 fps — just below real-time for
   60 fps material.** There is essentially no decode headroom, which is
   exactly the condition under which hardware decode should matter.
   Sony software decode runs at 87.8 fps, a 3.4× margin over the ~26 fps
   the encoder wants.
2. **`--avhw` reconfirms the Phase-1 defect**: Sony 3 596 frames against
   3 599 in the container (Δ −3), DJI 3 597 = exact. The loss tracks the
   leading-picture structure, exactly as `root-cause.md` predicts.

So the decoder win is real *and* the DJI case is the one where it should
compound. §4 shows what actually happens.

---

## 4. End-to-end, single way (the headline)

All runs below are in the **high clock regime** (`throttled: false`),
re-measured after the quarantine of §1.

### 4.1 Throughput and frame integrity

| Fixture | Route | Wall s | e2e fps | Encode fps | Frames | Δframes |
|---|---|---|---|---|---|---|
| **Sony 4K60 10-bit 60 s** | software decode (**baseline**) | 137.0 | **26.27** | 26.27 | 3 599 | **0** |
| | rigaya `--avhw` (**candidate**) | 137.4 | 26.19 | 25.48 | 3 596 | **−3** |
| | FFmpeg NVDEC → transfer → pipe | 137.1 | 26.25 | 26.25 | 3 599 | 0 |
| | FFmpeg software → pipe (control) | 143.7 | 25.05 | 25.05 | 3 599 | 0 |
| **DJI 4K60 10-bit 60 s** | software decode (**baseline**) | 135.7 | **26.50** | 26.50 | 3 597 | **0** |
| | rigaya `--avhw` (**candidate**) | 136.2 | 26.42 | 26.42 | 3 597 | 0 |
| | FFmpeg NVDEC → transfer → pipe | 145.4 | 24.73 | 24.73 | 3 597 | 0 |
| | FFmpeg software → pipe (control) | 144.1 | 24.96 | 24.96 | 3 597 | 0 |

**Reading this table.**

- **Sony: four routes, 137.0 / 137.4 / 137.1 / 143.7 s.** The first three
  are within **0.3 %** of each other. Decode path is irrelevant; the
  pipeline is NVENC-bound.
- **DJI: 135.7 / 136.2 / 145.4 / 144.1 s.** Same picture. The content
  that *should* have benefited most — 59.2 fps software decode, no
  headroom — gains **nothing** (0.4 %).
- **The only measurable difference is the pipe.** Both pipe routes cost
  a consistent 5–6 % (Sony control +4.9 %; DJI NVDEC +7.1 %), and it is
  the *pipe*, not the decoder: on both fixtures the NVDEC pipe and the
  software pipe land within 1.5 % of each other.
- **Only `--avhw` breaks frame integrity**: Δ −3 on Sony. The one route
  with a defect is also the one with no measurable speed benefit.

### 4.2 CPU: where the candidate actually wins

Same runs, CPU and memory. `CPU mean %` is `% Processor Time` across all
16 threads; `CPU-seconds` is the whole process tree; `CPU-s/1000 frames`
normalises away the wall clock.

| Fixture | Route | CPU mean % | CPU-seconds | **CPU-s / 1000 frames** | Peak RSS MB | Peak VRAM MB |
|---|---|---|---|---|---|---|
| **Sony 60 s** | software (**baseline**) | 27.88 | 272.6 | **75.7** | 2 641 | 2 802 |
| | `--avhw` | 18.01 | 102.9 | **28.6** | 1 778 | 3 304 |
| | FFmpeg NVDEC → pipe | 23.61 | 224.1 | **62.3** | 1 773 | 3 709 |
| | FFmpeg software → pipe | 29.18 | 329.7 | **91.6** | 1 773 | 2 802 |
| **DJI 60 s** | software (**baseline**) | 29.12 | 218.8 | **60.8** | 2 628 | 2 802 |
| | `--avhw` | **15.55** | 78.9 | **21.9** | 1 747 | 3 304 |
| | FFmpeg NVDEC → pipe | 29.25 | 193.6 | **53.8** | 1 781 | 3 661 |
| | FFmpeg software → pipe | 31.43 | 287.9 | **80.0** | 1 768 | 2 802 |

**This is the actual result of the benchmark.**

- **`--avhw` cuts CPU cost per frame by 62 % on Sony and 64 % on DJI**
  (75.7 → 28.6 and 60.8 → 21.9 CPU-s/1000 frames), at identical wall
  clock. DJI system CPU falls from 29.1 % to 15.6 %; Sony from 27.9 % to
  18.0 %.
- **Peak RSS drops ~33 %** (2 628 → 1 747 MB on DJI) because the software
  decoder's frame buffers and thread pool disappear.
- **Peak VRAM rises ~500 MB** (2 802 → 3 304 MB), because NVDEC surfaces
  now live in VRAM. This is the cost side of the trade and it is not
  small on an 8 GB card — 3.3 GB of 8.15 GB for one 4K job.
- **The FFmpeg pipe route burns most of the CPU benefit**: 62.3 vs 75.7
  CPU-s/1000 on Sony is only an 18 % saving, and on DJI (53.8) it is
  worse than `--avhw` by 2.5×. The `hwdownload` → `yuv420p10le` →
  y4m parse → re-upload chain does real work on the CPU.

**Why the FFmpeg route is not the answer.** Phase 1 established that
FFmpeg `-hwaccel` is the *frame-exact* way to do hardware decode, and
this benchmark confirms it (Δ 0 on both fixtures). But measured as a
pipeline, it gives up most of the CPU advantage that was the entire point,
in exchange for 6 % more wall clock and 0.5–0.9 GB more VRAM. It moves
the decode to the GPU and then moves every frame back.

### 4.3 Bandwidth — measured, and not the bottleneck

A 4K 10-bit 4:2:0 frame is **23.73 MB** (16 588 800 B luma + 8 294 400 B
chroma), i.e. **1.39 GB/s** of raw pixel traffic at 60 fps.

| Route | PCIe traffic | At measured ~25 fps | Share of Gen5 ×8 (~32 GB/s/dir) |
|---|---|---|---|
| `--avhw` / `sw_enc` | compressed bitstream only | ~0.002–0.011 GB/s | **< 0.1 %** |
| `ffhw_pipe` | hwdownload down **+** re-upload up | 0.57 + 0.57 = **1.15 GB/s** | **1.8 %** |

The pipe route moves ~56× more data across PCIe than the in-process
route, and still finishes within 6 % — because even 1.15 GB/s is
negligible against a Gen5 ×8 link. **PCIe is not the constraint**, and
the pipe's 6 % penalty is CPU-side format conversion, not transfer.

The bandwidth figure that *does* matter is **DRAM**, not PCIe: the
software decoder and `hwdownload` both stream 23.73 MB/frame through
system memory. That is why the FFmpeg pipe route's CPU cost (53.8–62.3
CPU-s/1000 frames) sits so far above `--avhw`'s (21.9–28.6), and it is
consistent with the `copy` engine activity recorded on that route
(9.05 % mean on DJI vs 0.19 % for `--avhw`).

---

## 5. Concurrency (2-way, DJI — and why 4-way is not reported)

Aggregate throughput is total frames ÷ job wall clock for two staggered
concurrent jobs of the same route.

| Ways | Route | Job wall s | Total frames | **Aggregate fps** | Per-way fps | Scaling vs 1-way aggregate | Peak VRAM MB |
|---|---|---|---|---|---|---|---|
| 1 | software decode | 135.7 | 3 597 | 26.50 | 26.50 | 1.00× | 2 802 |
| 1 | `--avhw` | 136.2 | 3 597 | 26.42 | 26.42 | 1.00× | 3 304 |
| 1 | FFmpeg NVDEC → pipe | 145.4 | 3 597 | 24.73 | 24.73 | 1.00× | 3 661 |
| **2** | software decode | 241.6 | 7 194 | **29.78** | 15.05 / 14.99 | **1.12×** | 2 802 |
| **2** | `--avhw` | 239.6 | 7 194 | **30.03** | 15.09 / 15.12 | **1.14×** | 3 304 |
| **2** | FFmpeg NVDEC → pipe | 237.4 | 7 194 | **30.31** | 15.44 / 15.26 | **1.23×** | 3 661 |

**This is the most important table in the report.**

- **Two concurrent 4K jobs converge to ~30 fps aggregate on every route**
  (29.78 / 30.03 / 30.31 — a 1.8 % spread). The decode path stops
  mattering entirely, because the **NVENC engine is the shared
  bottleneck**.
- **Per-way throughput nearly halves** (26.5 → 15.0 fps), so the second
  job buys only a 12–23 % aggregate gain. The machine is already
  saturated at one job.
- **The predicted benefit of freeing the CPU never materialises.** The
  theory was: hardware decode frees CPU → more concurrent jobs fit → more
  throughput. In practice the freed CPU has nothing to do, because the
  encoder — not the CPU — caps concurrency. `--avhw` at 2-way beats
  software decode by **0.8 %**.
- **The pipe route is nominally the best at 2-way** (30.31 fps), which is
  not a real effect: it is the route that lost the most per job, so it
  had the most headroom to recover. Its 1-way penalty (+7.1 %) exceeds
  its 2-way advantage (+1.8 %).

**4-way is deliberately not reported.** At 4 concurrent 4K sessions the
aggregate VRAM demand would reach ~11–15 GB against 8 151 MiB; the
earlier 4-way attempt was stopped partly for this reason and one partial
`--avhw` 4-way run collapsed. 4K concurrency is capped at 2 by operator
decision; 4-way will be measured when 1080p material exists, where
per-session VRAM is roughly a quarter.

---

## 6. 10-minute material

| Fixture | Route | Wall s | e2e fps | Frames | Δ | CPU mean % | CPU-s/1000 f | Peak RSS MB | Peak VRAM MB | Clock regime |
|---|---|---|---|---|---|---|---|---|---|---|
| Sony 600 s (content-varied) | software (**baseline**) | 1 365.5 | **26.34** | 35 967 | 0 | 30.43 | 86.7 | 2 752 | 2 802 | high |
| Sony 600 s | `--avhw` (**candidate**) | 1 368.8 | **26.28** | 35 964 | **−3** | **23.67** | **32.2** | **1 797** | 3 304 | high |
| DJI 600 s (looped) | software (**baseline**) | *not completed — see note* | | | | | | | | |
| DJI 600 s | `--avhw` | *not completed — see note* | | | | | | | | |

> **DJI 10-minute arm status — a gap, not a finding.** The Sony 10-minute
> arm is complete and settles the duration question: throughput is flat
> from 60 s to 600 s and the CPU saving holds at −63 %. The DJI
> 10-minute arm was still running when this benchmark was stopped at the
> operator's request, and its partial results are **not** reported. DJI
> duration behaviour is therefore inferred from the measured DJI 60 s
> result plus the measured Sony 10-minute result, not measured directly.
> To close it: `--matrix long --jobs dji600_x1 --tag final_long`
> (two runs, ≈45 min at the measured ~26 fps).

The 10-minute arm reproduces the 60 s result with no drift:

- **Throughput is flat.** Sony baseline 26.34 fps at 600 s vs 26.27 fps at
  60 s (+0.3 %); `--avhw` 26.28 vs 26.19 (+0.3 %). Sustained throughput
  does not decay with duration across 35 967 frames and 5 editing
  boundaries.
- **The route gap is still zero.** 26.34 vs 26.28 fps is a 0.2 %
  difference — inside noise (threat T4).
- **The CPU gap is still large and stable.** 86.7 → 32.2 CPU-s per 1 000
  frames (−63 %), matching the 60 s measurement (−62 % / −64 %). Peak RSS
  falls 2 752 → 1 797 MB (−35 %), peak VRAM rises 2 802 → 3 304 MB
  (+18 %).
- **The `--avhw` frame loss reproduces exactly**: Δ −3 again, at 10×
  length. It is a deterministic property of the reader on this format,
  not a transient.
- **The throttle threshold is not grazed in normal operation**: SM clock
  median 2 722 MHz, min 2 317 MHz, max 2 820 MHz over 23 minutes, with
  the GPU at 71 °C peak.

---

## 7. Verdict

### 7.1 Answering the three required questions, with the numbers

**1. Is the whole transcoding pipeline faster?**

**No.** Single-way, high-clock regime:

| Fixture | Baseline | Candidate (`--avhw`) | Change | FFmpeg NVDEC pipe | Change |
|---|---|---|---|---|---|
| Sony 60 s | 137.0 s | 137.4 s | **+0.3 %** | 137.1 s | **+0.1 %** |
| DJI 60 s | 135.7 s | 136.2 s | **+0.4 %** | 145.4 s | **+7.1 %** |
| Sony 600 s (10 min) | 1 365.5 s | 1 368.8 s | **+0.2 %** | not run (pipe already 6 % down at 60 s) | — |

Every route is within noise of the baseline except the pipe routes, which
are slower. The 7.3× decoder win from §3 does not survive into the
pipeline **because decode was never on the critical path**: at ~26 fps
the pipeline needs 26 fps of decode, and even DJI's software decoder
supplies 59.2 fps. Decode has ≥ 2.2× headroom in both fixtures, so
accelerating it changes nothing.

**2. Is the CPU released?**

**Yes — this is the one real, reproducible win, and it is large.**

| | Sony 60 s | DJI 60 s | Sony 600 s (10 min) |
|---|---|---|---|
| CPU mean %, baseline → `--avhw` | 27.88 → **18.01** (−35 %) | 29.12 → **15.55** (−47 %) | 30.43 → **23.67** (−22 %) |
| CPU-s / 1000 frames, baseline → `--avhw` | 75.7 → **28.6** (−62 %) | 60.8 → **21.9** (−64 %) | 86.7 → **32.2** (−63 %) |
| Peak RSS, baseline → `--avhw` | 2 641 → **1 778 MB** (−33 %) | 2 628 → **1 747 MB** (−33 %) | 2 752 → **1 797 MB** (−35 %) |
| Peak VRAM, baseline → `--avhw` | 2 802 → **3 304 MB** (+18 %) | 2 802 → **3 304 MB** (+18 %) | 2 802 → **3 304 MB** (+18 %) |

The per-frame figure is the stable one: **−62 / −64 / −63 %** across two
brands and two durations, i.e. hardware decode removes close to
two-thirds of the CPU cost of a 4K60 10-bit transcode. The per-second
`CPU mean %` varies more (−22 % to −47 %) purely because wall clock
differs, which is exactly why the per-frame number is the one to quote.

Caveat that must travel with this claim: **the CPU saving is real per
job but has no throughput to spend itself on** (§7.2). It is a capacity
and headroom win, not a speed win.

**3. Does concurrency improve?**

**No.** 2-way aggregate is 29.78 fps (software) vs 30.03 fps (`--avhw`) —
**+0.8 %**. Concurrency is capped by the NVENC engine, not by CPU or
decode. Per-way throughput halves, and aggregate gains only 12–14 %.
4-way at 4K is not runnable on this GPU (VRAM) and is deferred.

### 7.2 The core conclusion

> **On this machine, for 4K60 10-bit HEVC → NVENC transcoding, hardware
> decode does not make the pipeline faster, does not raise concurrency,
> and is the only tested route that loses frames. What it does do is cut
> per-job CPU by roughly two-thirds and RSS by a third, at the cost of
> ~500 MB more VRAM and a frame-exactness defect in the rigaya reader.**
>
> The pipeline is **NVENC-bound**. At ~26 fps single-way, NVENC's video
> engine already runs at 80–85 % utilisation, and two concurrent jobs
> saturate it completely (~30 fps aggregate on every route). Decode
> supplies 59–88 fps in software, i.e. ≥ 2.2× headroom, so it is never
> the constraint at the concurrency levels this GPU can support.

**What that means for the project.** Adopting hardware decode is
justified as **CPU headroom for work that is not NVENC** — audio
encoding, muxing, preservation validation, PSNR/SSIM sampling, channel
sync, non-NVENC backends (x265, SVT-AV1) running alongside, or a
watch-folder processing several different jobs at once. It is **not**
justified as "make transcodes faster" on this GPU, and the answer would
change on hardware with a slower encoder or faster storage.

**And the route that should be used, if any, is FFmpeg `-hwaccel`** —
despite the pipe costing 6 %. The reason is §4.1: `--avhw` loses 3 frames
on Sony XAVC. A 62 % CPU saving does not buy the right to corrupt a
timeline. But note the honest cost: via the FFmpeg pipe that saving drops
to ~18 % on Sony, so **the frame-exact route gives up most of the CPU
benefit** — an adoption decision has to weigh 18 % CPU against 6 % wall
clock and the extra VRAM, and on the numbers here that is close to a
wash.

### 7.3 Go/no-go

| Decision | Verdict | Basis |
|---|---|---|
| Enable hardware decode **by default** for throughput | **No-go** | §4.1: +0.1–0.4 % single-way, −0.8 % at 2-way. Not material. |
| Enable it to **free CPU** | **Conditional go** | §4.2: −62/64 % CPU-s per frame, −33 % RSS. Real, but only pays off if something else can use the CPU. |
| Use rigaya `--avhw` | **No-go** | §4.1: Δ −3 frames on Sony, reconfirming `root-cause.md`. |
| Use FFmpeg `-hwaccel` via pipe | **Conditional** | Frame-exact, but +6 % wall clock, +500–900 MB VRAM, and only ~18 % CPU saving once the pipe's own cost is paid. |
| Raise 4K concurrency beyond 2 to exploit freed CPU | **No-go on this GPU** | §5: NVENC-saturated at 2; §2.5: 4 sessions exceed 8 GB VRAM. |
| Re-test at 1080p | **Recommended** | Per-session VRAM ≈ ¼, so 4-way may become runnable; decode headroom is also larger, so the CPU-release case must be re-measured rather than extrapolated. |

This agrees with the stop condition already written into
`implementation-plan.md` §17.9: *"if S9 shows no material end-to-end
gain, the correct outcome is to keep software decode as the default and
ship only S1–S3 + S8."* S9 has now been run, and it shows no material
gain. **The durable justification for hardware decode is CPU headroom for
concurrent work, decided per vendor — exactly as `README.md` predicted,
and not a throughput win.**

### 7.4 What Phase 1 got right, and what this adds

Phase 1's `nvdec.md` §6.6 reported NVDEC "up to ~1.2× software" and
`README.md` recorded indicative figures of software ≈71 fps, NVDEC ≈93,
NVDEC-explicit ≈148 fps — all decode-only. Those numbers are consistent
with §3 here (87.8 / 331 / 433 fps) once the different clip, warming and
measurement basis are accounted for. Phase 1 was explicit that these were
*not* the basis of its verdict, and it was right to refuse: this
benchmark shows the decode-level ratio (3.8×–7.3×) shrinking to **1.00×
at the pipeline level**, because the encoder is the bottleneck.

What this phase adds beyond Phase 1:

1. The decode win is **real but irrelevant** at the pipeline level on
   this hardware, quantified at 0.1–0.4 %.
2. The **CPU release is the genuine benefit** (−62/−64 % CPU-s per frame),
   quantified per frame rather than as an instantaneous percentage, and
   shown to be largely surrendered by the frame-exact FFmpeg route.
3. The **NVENC engine, not the CPU or decoder, caps concurrency**
   (~30 fps aggregate at 2-way regardless of route).
4. A **throttling feedback loop** (§1) that can fabricate a 3.4× speedup
   if GPU clocks are not recorded. Any future benchmark of this question
   must record clocks.
5. The **FFmpeg → transfer → rigaya route is viable but pointless**: the
   GPU round-trip is 1.8 % of PCIe capacity and still costs 6 % wall
   clock, because the expense is CPU-side conversion.

---

## 8. Threats to validity

| # | Threat | Status |
|---|---|---|
| T1 | GPU clock regime confounds all timing | **Detected and controlled.** Clocks recorded per run; first pass quarantined; all reported runs `throttled: false`. This was the single largest error source. |
| T2 | DJI 10-minute fixture is a loop, not varied content | **Acknowledged.** Corpus holds only 364.4 s of DJI 4K60 2160p. Measures scaling, not DJI bitrate behaviour. |
| T3 | Fixtures are video-only, production copies audio | **Acknowledged, bounded.** Audio is a stream copy that does not scale with decode path. Scope is the video transcode stage. |
| T4 | One run per (fixture, route, concurrency) | **Acknowledged.** Earlier repeat measurements of the same configuration agreed to < 0.5 % once the clock regime was controlled (Sony `sw_enc`: 137.0 / 140.7 / 148.2 s across regimes), but no formal variance estimate exists. Differences below ~2 % should not be over-read. |
| T5 | Background desktop load (~20 % CPU baseline) | **Recorded, not eliminated.** Would mask a small CPU-side gain, i.e. it biases *against* the hardware-decode CPU result, which is nonetheless large and consistent. |
| T6 | A foreign NVENC session (`F:\1KT-avhw`) appeared on the GPU during part of the work | **Observed and logged.** Each run records its own clocks and per-PID engine attribution, so a contended run is identifiable. All reported runs are accounted for. |
| T7 | Throttle threshold (2 300 MHz) is a judgement call | **Disclosed.** The two observed regimes are ~2 750–2 812 MHz and ~1 716 MHz; the threshold sits in a wide empty band between them, so it is not sensitive. |
| T8 | `% Processor Utility` is over-calibrated on this platform | **Worked around.** All CPU claims use `% Processor Time`; the utility figure is recorded only to cross-check rigaya's own CPU field. |
| T9 | rigaya progress timeline unrecoverable through a pipe | **Documented.** `progress_points` retained for frame counts only; `ramp` must not be cited. |
| T10 | Content-dependence is large: Sony 13.7 Mbps vs DJI 84.4 Mbps output from the same QVBR 23 profile | **Acknowledged.** Absolute timings are content-specific; the *comparison between routes* is made on identical content, frame counts and bitrates, which is what the conclusions rest on. |

---

## 9. Tooling

All tooling lives in the worktree at `work/e2e/` and is **not imported by
production code**. Like `work/hwdecode/` in Phase 1, `work/` is
gitignored, so these scripts stay local by project convention.

| File | Role |
|---|---|
| `bench_e2e.py` | scenario construction, instrumented run loop, sampler integration, throttling detection |
| `e2e_sampler.ps1` | per-second probe: CPU, per-PID engine utilisation, VRAM, clocks, temperature, power |
| `make_fixtures.py` | stream-copy fixture construction + manifest with sha256 |
| `matrices.json` | test matrices (`smoke` / `core` / `solo` / `long`) with rationale per job |
| `base_profile.json` | production `UHQ` encode settings, key-by-key provenance |
| `analyze.py` | results aggregation into report tables |
| `fixtures.json` | fixture manifest (paths, frame counts, sha256) |
| `results/` | authoritative per-run JSON (one file per run, plus per-tag collections) |
| `results_quarantine/` | the throttled first pass — kept as evidence, excluded from all conclusions |
