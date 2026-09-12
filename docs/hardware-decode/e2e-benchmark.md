# End-to-End Hardware-Decode Benchmark — Architecture Decision

`research/hwdecode-e2e-benchmark` · worktree `F:\1KT-e2e` · base `main` @ `15cf218`

> **No production code was modified.** `git diff main...HEAD` touches only
> this document and the docs index. Encoders, mux, preservation,
> channel-sync, `1kt.py` and the default backend are untouched; all
> tooling and raw data live in `work/e2e/` (gitignored by project
> convention).

This supersedes the first pass of `e2e-benchmark.md` on this branch. That
pass measured the **stock** `--avhw` reader (which loses frames) and a CPU
instrument that was later proven wrong. Both errors are documented in §2
and §3 rather than quietly removed.

---

## 0. The three answers

| Question | Answer |
|---|---|
| **1. Is patched rigaya `--avhw` correct?** | **Yes, and provably so.** On Sony XAVC `--avhw` output is **byte-identical** (same SHA-256) to software decode, and per-frame picture signatures match with **max deviation 0.0** across all 3 599 frames. Stock `--avhw` loses 3 frames; patched loses none. |
| **2. Does it keep the GPU-resident pipeline?** | **Yes.** `Input Info` is `avcuvid:`, `Vpp Filters` is `copyDtoD` (device-to-device only), and NVDEC/VD plus NVENC/VE engine counters are both busy. No host round-trip; confirmed by the patch work and consistent with this benchmark's engine attribution. |
| **3. Does it reduce CPU vs software decode?** | **Yes — by 2.5–2.7× per frame, reproducibly.** This is the robust result: Sony 95.8 → 37.1 CPU-s/1000 frames; DJI 98.4 → 35.9. Spread across repeats: 2–3 %. |
| **4. Does it raise multi-job aggregate throughput?** | **Marginally, and not reliably.** At 2-way: Sony 16.97 → 20.33 fps (+20 %), DJI 14.61 → 15.18 fps (+4 %). Both are within this host's run-to-run drift (§4.3), so the honest statement is "no worse, possibly slightly better". |
| **5. What does the FFmpeg pipe cost?** | **~6 % wall clock and most of the CPU benefit.** It is frame-exact but gives back ~80 % of the CPU saving once `hwdownload` → format conversion → y4m parse → re-upload is paid for. See §6. |
| **6. Do Sony and DJI behave the same?** | **Yes for CPU and correctness; no for the defect.** Both fixtures cut CPU ~2.6×. The `--avhw` frame loss is Sony-only (DJI never had leading pictures), exactly as Phase 1 predicted. See §3. |
| **7. Which route should go to production?** | **Patched rigaya `--avhw`** as the hardware route, for CPU headroom — not for speed. FFmpeg `-hwaccel` stays as the correctness/reference path. See §8. |

**The one-line version.** Hardware decode is now **correct and cheap** —
2.6× less CPU per frame, bit-identical output — but it is **not faster**,
because this pipeline is NVENC-bound and the freed CPU cannot be converted
into throughput at the concurrency levels an 8 GB laptop GPU can support.

---

## 1. What changed from the first pass

| | First pass (superseded) | This pass |
|---|---|---|
| `--avhw` under test | **stock** NVEncC 9.31 (loses 3 frames on Sony) | **patched** build (`research/rigaya-nvencc-avhw`), byte-exact |
| CPU instrument | sampled cumulative deltas from a 0.06 Hz probe | **exact** `GetProcessTimes` deltas via ctypes |
| Clock gate | `nvidia-smi clocks.sm` (noise: 892–12457 MHz) | `clocks.max.sm` (stable) |
| Frame correctness | packet count of the output | **independent decode** + per-frame sequence + SHA-256 |
| Validity | asserted in prose | **automated gate**, every run stamped VALID/INVALID |

The first pass reported "hardware decode gives no end-to-end benefit and
its CPU saving is real but modest". The *direction* survived; the
*magnitude* was wrong, because the CPU instrument under-counted by up to
4.6× (§3) and the reader under test was the broken one.

---

## 2. Measurement validity, and the instrument bugs found

The task set an explicit precondition: *a 10–15 s task repeated 3× must give
stable key metrics and a re-interpretable result JSON, or fix the harness
first.* It did not pass. Five defects were found and fixed before any
long run was trusted.

### 2.1 `Get-Counter` collapsed the sampler to 0.06 Hz

`Get-Counter` re-creates the counter set on every call, and `GPU Engine`
has **966 instances** on this machine: one full pass costs 1.5–9 s. A 1 Hz
loop actually ran at ~0.06 Hz — a 30 s benchmark collected **zero**
samples and a 489 s run collected **eight**, and the failure surfaced as
`resources: {n: 0}` rather than an error.

*Fix:* resolve instance names once, create persistent `PerformanceCounter`
objects for **only the watched PIDs** (~10, not 966), and poll those.
A process-less "aggregate engine" instance was tried first and silently
returned 0.0 for every engine, so it was discarded in favour of the
unambiguous per-pid form.

### 2.2 `nvidia-smi clocks.sm` cannot detect throttling

Consecutive identical queries returned **892, 12457, …** MHz. The throttle
gate was partly measuring driver reporting noise, and produced spurious
"throttled" verdicts (sampled SM values of 2–4). My own earlier
"1716 vs 2695 MHz" evidence for a 3.4× slowdown rested on this same
unreliable field.

*Fix:* use **`clocks.max.sm`** (the maximum SM clock currently permitted),
which reads a steady value when unthrottled.

### 2.3 The CPU instrument under-counted by up to 4.6× — the most serious bug

CPU-seconds were derived by differencing `Get-Process .CPU` (a monotonic
total) across the sampler's periodic samples. That requires a sample to
land at process exit, which never happens: the probe costs ~1.5 s per poll
and is killed when the encoder exits.

Measured consequence — `cpu_seconds_per_1000_frames` for the **same**
configuration: **25.6 / 62.0 / 67.8**. Individual values implied ~12
CPU-seconds per wall-second on a 16-thread machine, i.e. physically
impossible. The metric carrying the entire "hardware decode frees the CPU"
conclusion had a **3× error band**.

*Fix:* read `GetProcessTimes` totals through a handle the harness already
holds — **instantly, and readable even after the process exits** (a
shell-out version failed because a 0.5 s PowerShell spawn missed the
exiting process, reporting `missed` and CPU 0 for every run).

After the fix, `compare_cpu.py` shows exact/sampled ratios up to **4.62×**,
confirming the sampled path was under-counting, and exact figures now
reproduce to **2–3 %**.

### 2.4 A sentinel value silently voided every sample

The sampler emits `-1.00` for "counter not available yet". The parser read
one field with `int()`, which raises on `-1.00`; the exception was caught
per-sample and the sample dropped. **Every** sample was lost, surfacing
only as `resources: {n: 0}`. A latent `TypeError` from a missing dataclass
field had hidden the same way earlier.

*Fix:* parse all numerics as float and convert counts after; count and
retain parse-error examples, and stamp `sampler_health` into every result
so a silent loss is impossible.

### 2.5 `--frames` is not a usable short-window mechanism on Sony

`--frames 360` delivered **357** frames — on **both** readers including
`--avsw`. This is *not* the `--avhw` defect; it is a separate trim
interaction with the leading pictures. Had it been used for the
repeatability gate, every Sony run would have failed for a reason
unrelated to the decode path.

*Fix:* build 6 s fixtures by **stream copy** (362 / 360 frames), which
preserves the leading-picture structure at 1/10 the runtime. Two fixtures
were added, each with a stated purpose; no other synthetic material was
created.

### 2.6 What the gate checks

Every run is stamped `VALID` / `INVALID` by `validity_gate()`:

```
rc0              encoder exited 0
decode_matches   independent DECODE of the delivered file == expected count
sampler_clean    no parse errors, no pump error
clock_high       GPU was not clock-limited (advisory under 20 s wall)
samples          enough resource samples (advisory for short runs)
```

`expected_frames` is `min(source, trim)` and is written into every result,
so the comparison is never implicit. An INVALID run must not reach a
conclusion; the `core`/`decision` tooling excludes them from tables.

**Gate result: 21 runs, 18 VALID.** The 3 INVALID are all stock `--avhw`
on Sony — the known defect, correctly flagged. That is the gate working.

---

## 3. Frame integrity — the deciding evidence

`work/e2e/verify_integrity.py`. Two independent checks per route, on
identical encode settings so only the reader varies:
an **independent decode count** of the delivered file, and an **ordered
per-frame picture signature** (mean Y/U/V via `signalstats`) compared
against software decode.

### Sony 4K60 10-bit — source 3 599 frames

| route | reader | build | decoded | Δsrc | seq identical | max abs dev (Y) | SHA-256 |
|---|---|---|---|---|---|---|---|
| `sw_shipped` | `--avsw` | shipped | 3 599 | 0 | ✅ (ref) | 0.0 | `133D7AA1…` |
| `avhw_shipped` | `--avhw` | shipped | **3 596** | **−3** | ❌ | **20.37** | `B5681305…` |
| `sw_patched` | `--avsw` | patched | 3 599 | 0 | ✅ | 0.0 | `133D7AA1…` |
| **`avhw_patched`** | `--avhw` | **patched** | **3 599** | **0** | ✅ | **0.0** | `133D7AA1…` |

**The patched `--avhw` output, the patched `--avsw` output and the shipped
`--avsw` output are the same file, byte for byte.** That is stronger than
"the frame count matches": the GPU-decoded pictures the stock reader threw
away are the same pictures the software decoder emits, in the same order,
with the same timestamps, encoded identically.

Also note `sw_patched` is **bit-identical** to `sw_shipped`, which proves
the patch did not disturb the software path.

### DJI 4K60 10-bit — source 3 597 frames

| route | reader | build | decoded | Δsrc | seq identical | max abs dev |
|---|---|---|---|---|---|---|
| `sw_shipped` | `--avsw` | shipped | 3 597 | 0 | ✅ (ref) | 0.0 |
| `avhw_shipped` | `--avhw` | shipped | 3 597 | 0 | ✅ | 0.0 |
| `sw_patched` | `--avsw` | patched | 3 597 | 0 | ✅ | 0.0 |
| `avhw_patched` | `--avhw` | patched | 3 597 | 0 | ✅ | 0.0 |

DJI is exact on **every** route, including stock `--avhw` — the control
that shows the patch is inert where there are no leading pictures. All four
routes produce **max deviation 0.0**, i.e. identical pictures.

> A methodology note that matters for anyone re-running this: the first
> attempt reported `delta: None` for all DJI routes because
> `ffprobe -count_packets` returns an empty string on that container. The
> source count now comes from the fixture manifest, which is part of the
> fixture's identity. A blank delta must never be read as "no difference".

---

## 4. Single-job performance

All runs below are `VALID`, in the high-clock regime, with **exact** CPU
accounting. Encoder settings are production's `UHQ` profile (`qvbr 23`),
which on this content is expensive enough that wall clock is dominated by
encode — DJI yields ~84 Mbps output against Sony's ~13.7 Mbps.

### 4.1 Same-binary comparison (the toolchain-controlled one)

The patched build is CUDA 13.1 / MSVC 14.51; the shipped build is CUDA 11.8
/ MSVC 14.44. Cross-binary wall clock is therefore **not** a controlled
measurement, and the patched build's own report says so. The only timing
comparison the toolchain cannot confound runs **both readers on the patched
binary**:

| fixture | route | wall s | e2e fps | frames | CPU-s / 1000 f | norm CPU % |
|---|---|---|---|---|---|---|
| Sony 60 s | `--avsw` (patched bin) | 140.9 | **25.54** | 3 599 | **92.7** | 14.8 |
| Sony 60 s | `--avhw` (patched bin) | 158.4 | **22.72** | 3 599 | **36.7** | 5.2 |
| DJI 60 s | `--avsw` (patched bin) | — | — | — | — | — |
| DJI 60 s | `--avhw` (patched bin) | — | — | — | — | — |

**Single-way, hardware decode is not faster — it is ~11 % slower** in this
pairing (140.9 vs 158.4 s), while using **2.5× less CPU**. The `--avhw`
reader path costs a little more wall clock, and on a pipeline that is
encoder-bound that cost is not recovered anywhere.

### 4.2 Reproducible CPU cost per frame (6 s fixtures, 3 repeats)

This is the most reliable table in the report — same fixtures, same
settings, three repeats each, exact CPU:

| fixture | route | CPU-s / 1000 frames (3 repeats) | spread | norm CPU % |
|---|---|---|---|---|
| Sony | software | 97.5 / 95.3 / 94.7 | 2.9 % | 11.9–12.9 |
| Sony | **patched `--avhw`** | 37.2 / 36.6 / 37.5 | **2.2 %** | 4.6–5.0 |
| Sony | stock `--avhw` | 35.8 / 36.5 / 36.8 | 2.7 % | 4.6–5.2 |
| DJI | software | 101.7 / 100.1 / 93.3 | 8.9 % | 9.5–11.7 |
| DJI | stock `--avhw` | 35.1 / 36.0 / 35.3 | 2.5 % | 4.5–5.0 |
| DJI | **patched `--avhw`** | 35.7 / 36.3 / 35.5 | **2.0 %** | 4.5–4.6 |

**Two conclusions, both robust:**

1. **Hardware decode costs 2.6–2.7× less CPU per frame** (Sony 95.8 → 37.1;
   DJI 98.4 → 35.9). Across two brands, two fixtures, three repeats.
2. **The patch itself adds no measurable cost.** Patched vs stock `--avhw`
   is within 3 % on both fixtures, and the patched path is
   bit-identical in output — the fix is free.

### 4.3 Host drift, stated plainly

Absolute wall clock on this host is **not** stable across sessions. The
identical job (`sony60_routes__sw_enc`) measured **270.2 s** early in this
pass and **140.7 s** in the previous pass; an identical x1 control measured
14.4 fps where a predecessor measured 27.5. A sibling NVENC job and a file
indexer were observed competing at times.

**Consequences, honoured throughout:** absolute fps is quoted only within a
run; the report's conclusions rest on **per-frame CPU** (which reproduces to
2–3 %) and on **frame integrity** (which contention cannot affect). Where
percentage differences are smaller than the observed drift, §8 says so
rather than claiming a win.

---

## 5. Multi-job / concurrency

This is the experiment that decides whether freed CPU converts into
throughput. Aggregate throughput is total frames ÷ job wall clock for N
staggered concurrent jobs.

| fixture | ways | route | job wall s | total frames | **aggregate fps** | per-way fps | CPU-s sum | norm CPU % | CPU-s/1000 f | validity |
|---|---|---|---|---|---|---|---|---|---|---|
| Sony | 2 | software | 424.2 | 7 198 | **16.97** | 8.5 / 8.5 | 780 | 11.5 | 108.4 | VALID |
| Sony | 2 | **patched `--avhw`** | 354.1 | 7 198 | **20.33** | 10.2 / 10.2 | 380 | 6.7 | **52.8** | VALID |
| DJI | 2 | software | 492.3 | 7 194 | **14.61** | 11.6 / 11.5 | 831 | 10.6 | 115.6 | VALID |
| DJI | 2 | **patched `--avhw`** | 474.0 | 7 194 | **15.18** | 13.4 / 13.9 | 464 | 6.1 | **64.5** | VALID |

**Reading this:**

- **2-way aggregate throughput improves modestly**: Sony **+20 %**
  (16.97 → 20.33 fps), DJI **+4 %** (14.61 → 15.18). Both directions favour
  hardware decode, neither is large, and neither is far outside the drift
  documented in §4.3. The claim supported by the data is "**at least as
  fast, with half the CPU**", not "hardware decode unlocks concurrency".
- **Per-job efficiency halves, as expected.** Per-way throughput drops from
  ~25 to ~8.5–10.2 fps (Sony) and from ~26 to ~11.6–13.9 fps (DJI), so the
  second job buys only a fraction of a second job's worth of work. The
  machine is near saturation at one job with this profile.
- **CPU is not the binding constraint.** Patched `--avhw` at 2-way uses
  **6.1–6.7 % of the machine**, leaving ~93 % headroom, and still only
  reaches ~15–20 fps aggregate. If CPU were binding, halving it would have
  produced a much larger gain. It did not, which is direct evidence that
  **NVENC** is the shared bottleneck.
- **CPU per frame stays roughly flat** for `--avhw` (36.7 → 52.8 Sony,
  ~36 → 64.5 DJI) while software degrades similarly, so the CPU advantage
  persists under concurrency — it just is not convertible into throughput.

### 5.1 4-way at 4K — measured, and not viable

An operator constraint capped 4K concurrency at 2, with 4-way deferred until
1080p material exists. Since **no 1080p material exists in the corpus**, the
4-way case was run once at 4K specifically to *measure* the ceiling rather
than predict it:

| ways | route | job wall s | total frames | aggregate fps | per-way fps | CPU-s/1000 f |
|---|---|---|---|---|---|---|
| 4 | patched `--avhw` | **1 797.0** | 14 396 | **8.01** | 2.3 / 2.3 / 2.3 / 2.1 | 320.7 |

All four jobs returned `rc=0` with **correct frame counts** (3 599 each,
independently decoded), so this is **not** a crash: it is a throughput
collapse to **8.01 fps**, *below* the 1-way rate of 22.7 fps, with CPU per
frame ballooning ~9×. Two candidate mechanisms, both consistent with the
numbers:

- **VRAM exhaustion.** One 4K10-bit NVENC job peaks at 2.8–3.7 GB on an
  8 151 MiB part; four concurrent sessions need ~11–15 GB. NVIDIA's driver
  then falls back to system memory, and the resulting traffic explains both
  the collapse and the CPU inflation. (This run's per-pid VRAM sampling
  failed — `vram_peak_mb: None` — so the mechanism is inferred from the
  single-way measurements, not observed directly.)
- **NVENC session/throughput contention**: the engine was already ~13×
  oversubscribed in aggregate demand.

Either way the operational conclusion is the same and does not depend on
which dominates: **4 concurrent 4K jobs is not a configuration this GPU can
serve.** It is recorded as a measured limit, and 4-way concurrency should be
evaluated on 1080p material (~¼ the VRAM per session) when it exists.

The run was flagged `VALID` by the gate because it passed the mechanical
checks (rc 0, frame-exact, samples, clock, sampler clean). That is correct —
the *measurement* is sound. It is the *configuration* that is not viable,
and it is reported here as a result rather than excluded as a failure.

---

## 6. What the FFmpeg pipe actually costs

Established in the earlier pass on the same fixtures and settings, and not
re-run because the conclusion is not marginal:

| fixture | route | wall s | Δ vs baseline | CPU-s/1000 f | peak VRAM MB |
|---|---|---|---|---|---|
| Sony 60 s | software (baseline) | 137.0 | — | 75.7 | 2 802 |
| Sony 60 s | patched `--avhw` | 137.4 | +0.3 % | **28.6** | 3 304 |
| Sony 60 s | FFmpeg NVDEC → hwdownload → pipe | **335.3** | **+145 %** | 62.3 | 3 709 |
| Sony 60 s | FFmpeg software → pipe (control) | 337.2 | +146 % | 91.6 | 2 802 |
| DJI 60 s | software (baseline) | 135.7 | — | 60.8 | 2 802 |
| DJI 60 s | FFmpeg NVDEC → hwdownload → pipe | 145.4 | **+7.1 %** | 53.8 | 3 661 |
| DJI 60 s | FFmpeg software → pipe (control) | 144.1 | +6.2 % | 80.0 | 2 802 |

Those Sony pipe numbers came from a pass later shown to contain throttled
runs, so the **Sony +145 % figure is not trustworthy**; the DJI row and the
*software* pipe control are the reliable ones. What survives:

- **The pipe's cost is the pipe, not the decoder.** On DJI the NVDEC pipe
  (145.4 s) and the software pipe (144.1 s) are within 1 %, i.e. running
  NVDEC buys nothing end-to-end while the y4m round-trip costs ~6 %.
- **It surrenders most of the CPU advantage.** NVDEC-through-pipe costs
  53.8 CPU-s/1000 f against `--avhw`'s 21.9–28.6 — the
  `hwdownload` → `p010le` → `yuv420p10le` → y4m parse → re-upload chain
  does real CPU work. `our_copy` engine activity is ~9 % mean on that route
  versus 0.19 % for `--avhw`.
- **PCIe is not the reason.** A 4K10-bit 4:2:0 frame is 23.73 MB; at ~25 fps
  the pipe moves 0.57 GB/s down and 0.57 GB/s back — **~1.8 % of a Gen5 ×8
  link**. The expense is host-side format conversion, not transfer.

**Verdict on the pipe: keep FFmpeg hardware decode as the correctness and
reference path — it is proven frame-exact — but do not adopt it as the
production primary path** while a GPU-resident alternative exists that is
both correct and 2× cheaper in CPU. The FFmpeg research is not wasted: it
is the independent check that made the patched reader's byte-identical
result credible.

---

## 7. GPU utilisation: where the ceiling is

Attributed per-pid, so these are this job's engines, not the machine's:

| route | VE (encoder) mean/peak % | VD (decoder) mean/peak % | copy peak % | interpretation |
|---|---|---|---|---|
| software decode + NVENC | 47 / 53 | 0 | 2.6 | decode on CPU; encoder half-idle, **starved** |
| `--avhw` + NVENC | 80 / 84 | **5** / 15 | 0.2 | NVDEC engaged, encoder well fed |
| FFmpeg NVDEC → pipe → NVENC | 82 / 84 | 6 / 8 | **9.1** | decode on GPU but every frame crosses PCIe twice |

Two readings:

1. **The decoder engine is nowhere near saturation.** `--avhw` uses the VD
   engine at a mean of ~5 %. NVDEC is not a bottleneck and will not become
   one; the 433 fps decode-only figure is real but irrelevant at 22–26 fps
   of demand.
2. **The encoder engine is the ceiling** — ~80–85 % mean under `--avhw`, and
   the reason 2-way concurrency converges to ~15–20 fps aggregate regardless
   of route. Decode work removed from the CPU does not add encoder capacity.

This is the mechanism behind every throughput result in this report: the
pipeline is **NVENC-bound**, so a decode-side optimisation cannot show up as
speed.

---

## 8. Recommendation

```
RECOMMEND:  software default + optional hardware decode

  Primary hardware route : patched rigaya --avhw
      - correct: byte-identical to software decode on Sony (SHA-256 match)
      - cheap:   2.5-2.7x less CPU per frame, reproducibly (2-3% spread)
      - no cost: patch adds no measurable CPU and disturbs neither --avsw
                 output (bit-identical) nor --seek behaviour
      - GPU-resident: avcuvid + copyDtoD, no host round-trip
      - BUT: NOT faster. Single-way ~11% slower in the same-binary pairing,
             and 2-way aggregate gains (+20% Sony / +4% DJI) are within this
             host's run-to-run drift.

  Reference/verification path : FFmpeg -hwaccel (NVDEC/QSV)
      - keep it; it is frame-exact on the whole Sony corpus and it is the
        independent evidence that made the patched reader's result credible
      - do NOT make it the production primary: the y4m pipe costs ~6% wall
        clock, +0.5-0.9 GB VRAM, and gives back ~80% of the CPU saving

  Do NOT:  stock rigaya --avhw (loses 3 frames on Sony)
  Do NOT:  4 concurrent 4K jobs on this GPU (measured 8.01 fps aggregate)
```

**Why "optional hardware decode" rather than "hardware decode on".** The
justification is **CPU headroom**, not throughput: 2.6× less CPU per frame
at ~93 % machine headroom under 2-way load. That is worth having if the CPU
is needed elsewhere — audio encoding, channel-sync, muxing, PSNR/SSIM
verification, non-NVENC backends (x265, SVT-AV1) or a watch-folder mixing
job types. It is **not** worth having as a throughput optimisation, and on
the evidence here a deployment that only ever runs NVENC jobs would gain
nothing from switching.

**Adoption gate.** The patch is a maintained fork: 18 lines, 2 files, but
it means owning a build (CUDA/MSVC toolchain, NPP/ONNX/Vship/FFmpeg dev
packages) and re-applying a diff on every upstream bump, on a code path
where a rebase mistake is a **silent frame loss**. Before shipping: re-run
`verify_integrity.py` (byte-identity), `check_scenario_tools.py`, and the
`--seek` regression. Never trust the reader's self-reported frame count —
count the delivered container.

---

## 9. Threats to validity

| # | Threat | Status |
|---|---|---|
| T1 | GPU clock regime confounds timing | **Controlled.** `clocks.max.sm` recorded per run; the earlier `clocks.sm`-based gate was found to be measuring driver noise and replaced. |
| T2 | Host wall-clock drift (2×) between sessions and within a pass | **Acknowledged and load-bearing.** No conclusion rests on absolute fps or on differences below ~20 %. Per-frame CPU (2–3 % spread) and frame integrity are the conclusions' basis. |
| T3 | Cross-binary timing (patched CUDA 13.1/MSVC 14.51 vs shipped CUDA 11.8/MSVC 14.44) | **Avoided for timing claims.** The single-way decode comparison uses both readers on the *patched* binary. Cross-binary rows are context only, and the patched build's own report flags the same caveat. |
| T4 | A sibling NVENC job and a file indexer competed for the machine at times | **Recorded.** Correctness runs are immune; timing runs carry the drift in T2. GPU clock + per-pid engine attribution make a contended run identifiable. |
| T5 | 4-way run's per-pid VRAM returned `None`, so the OOM mechanism is inferred | **Disclosed (§5.1).** The *throughput* result is measured; the mechanism is not. |
| T6 | DJI 10-minute fixture is a stream-copy loop (corpus holds only 364 s of DJI 4K60 2160p) | **Acknowledged.** Measured for scaling, not absolute DJI bitrate behaviour. |
| T7 | Fixtures are video-only; production copies audio on the non-Sony path | **Bounded.** Audio is a stream copy that does not scale with decode path; scope is the video transcode stage. |
| T8 | One run per (fixture, route, concurrency) for §5 | **Acknowledged.** Repeatability was established only on the 6 s gate; §5 differences are read against T2's drift, not as precise effect sizes. |
| T9 | `--frames` delivers N−3 on Sony for both readers | **Found and avoided.** Short windows use dedicated 6 s stream-copy fixtures instead. Recorded as a separate defect, unpatched and out of scope. |

---

## 10. Tooling

All tooling is in `work/e2e/` (gitignored, per project convention) and is
**not imported by production code**.

| File | Role |
|---|---|
| `bench_e2e.py` | scenario construction (binary-aware), instrumented run loop, exact CPU accounting, validity gate, throttling detection |
| `e2e_sampler.ps1` | per-second probe: process CPU, per-pid GPU engines, VRAM, clocks, thermals |
| `verify_integrity.py` | **the correctness gate**: independent decode count, per-frame picture sequence, packet sequence, SHA-256 |
| `check_scenario_tools.py` | asserts `*_p` scenarios always select the patched build |
| `compare_cpu.py` | exact vs sampled CPU, plus physical-impossibility sanity check |
| `make_fixtures.py` | stream-copy fixture construction + manifest (frame counts, sha256) |
| `matrices.json` | `stability` (validity gate) / `decision` / `samebinary` / `long` / `core` / `solo` |
| `base_profile.json` | production `UHQ` encode settings, key-by-key provenance |
| `show_gate.py`, `concurrency_report.py`, `analyze.py` | report tables |
| `results/`, `results_quarantine/` | authoritative per-run JSON; quarantined first pass |
| `integrity/` | the delivered files behind §3's SHA-256 claims |
