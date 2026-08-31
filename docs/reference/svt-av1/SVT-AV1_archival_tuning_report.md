# SVT-AV1 Tuning for Archival 4K Transcoding — Research Report

**Scope:** 4K 3840×2160 @ 30/60 fps camera footage, 10-bit 4:2:0 AV1 output from HDR / H.264 / H.265 10-bit sources.
**Codec baseline:** SVT-AV1. All parameter ranges/defaults below are taken from the **master branch of the canonical repository (latest tag `v4.2.0`, released 2026-07-14)** unless a specific version is stated. Canonical repo: <https://gitlab.com/AOMediaCodec/SVT-AV1> (GitHub mirror used for raw file reads).

> ⚠️ **CRITICAL framing note:** SVT-AV1's `crf` scale is **not** the same as x265's. The official docs state that CRF values that approximate x264/x265 visual quality "will tend to be **higher** in SVT-AV1" ([Ffmpeg.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Ffmpeg.md)), and a widely-cited community rule of thumb is **SVT-AV1 `crf 30` ≈ x265 `crf 21`** (1080p reference) ([dvaupel guide](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)). Do **not** paste x265 CRF numbers into SVT-AV1 directly.

---

## A. Parameter Reference Table

Sources: [Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md), [svt-av1_encoder_user_guide.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/svt-av1_encoder_user_guide.md), [Appendix-Rate-Control.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Appendix-Rate-Control.md), [CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md).

| Parameter (CLI) | Range | Default | Meaning | Archival setting |
|---|---|---|---|---|
| `--preset` | [-1–13] (docs); ≤0 = debug/reference (`-1` = MR "max quality reference"; PSY forks add `-2`) | 8 | Speed/efficiency dial. Lower = slower + more efficient. **v3.0 repositioned presets and removed one → max *distinct* preset is M10** (values 11–13 alias down; see §E) | UHQ 1–2; HQ 4; SMALL 6; FAST 8 |
| `--tune` | 0–5 | 1 | 0=VQ (visual quality), 1=PSNR, 2=SSIM, 3=IQ (still image only), 4=MS-SSIM, 5=VMAF (video only, new in v4.2.0) | **0 (VQ)** for real camera content |
| `--rc` | 0–2 | 0 | 0=CRF/CQP, 1=VBR, 2=CBR | 0 (CRF) for archival |
| `--crf` | 1–70 (0.25 steps) | 35 | Constant Rate Factor. Implies `--rc 0 --aq-mode 2 --qp x` | see §B |
| `--cqp` | 1–70 (0.25 steps) | 35 | Constant QP, implies `--rc 0 --aq-mode 0` (new v4.2.0) | rarely used |
| `--qp` | 1–63 | 35 | Initial QP; with `--rc 0` uses whatever `aq-mode` is set | rarely used (use `--crf`) |
| `--tbr` | 1–100000 kbps | 2000 | Target bitrate (VBR/CBR only) | only if VBR chosen |
| `--mbr` | 1–100000 kbps | 0 | Max bitrate (capped CRF only) | UHQ 100000; HQ 80000; SMALL 40000; FAST 60000 |
| `--min-qp` / `--max-qp` | 0–63 / 0–63 | 0 / 63 | QP floor/ceiling | defaults (or `max-qp` clamp for size tier) |
| `--aq-mode` | 0–2 | 2 | 0=off, 1=variance (AV1 segments), 2=deltaq pred efficiency | 2 (default) |
| `--keyint` | -2…2³¹-1 (`s` suffix = seconds) | -2 (~5 s) | GOP size (max intra distance) | 10s (300 @30fps, 600 @60fps); -1 = "infinite" (CRF only) |
| `--irefresh-type` | 1–2 | 2 | 1=FWD (open GOP), 2=KEY (closed GOP) | 2 (closed) for clean seek |
| `--scd` | 0–1 | 0 | Scene-change detection | 1 — but note it does **not** insert keyframes (§D) |
| `--lookahead` | -1, 0–120 | -1 (auto) | Future frames for ME/RC/TPL | HQ/SMALL 60–120; UHQ 120 |
| `--hierarchical-levels` | 0–5 | ≤M12: 5 (6 layers), else 4 | Temporal layers; mini-GOP = 1 << levels (default = 32 frames) | default |
| `--pred-struct` | 0–2 | 2 | 0=all-intra, 1=low-delay, 2=**random access** (hierarchical B/alt-ref) | 2 |
| `--enable-tf` | 0–2 | 1 | Alt-ref temporal filtering (MCTF). 2=adaptive (experimental) | 1 (2 for extra quality) |
| `--enable-kf-tf` | 0–1 | 1 | MCTF for keyframes (new v4.2.0) | default |
| `--enable-overlays` | 0–1 | 0 | Overlay frames as extra reference for base layer | **1** (UHQ/HQ/SMALL) |
| `--film-grain` | 0–50 | 0 | Film-grain synthesis strength (denoise level) | 8–15 for camera footage |
| `--film-grain-denoise` | 0–1 | 0 | 0=no source denoise (grain table only), 1=denoise by film-grain level | 0 (default) |
| `--adaptive-film-grain` | 0–1 | 1 | Resolution-adaptive grain block size | 1 (default) |
| `--enable-qm` | 0–1 | 0 | Quantization matrices (psychovisual) | 1 (+ `qm-min 0`) with tune 0 |
| `--qm-min` / `--qm-max` | 0–15 / 0–15 | 8 / 15 | QM flatness (0 = strongest) | qm-min 0 for compression |
| `--ac-bias` | 0.0–8.0 | 0.0 | RD bias toward high-frequency (texture/grain retention) | 1.0–1.5 (UHQ/HQ) |
| `--tf-strength` | 0–4 | 3 | Temporal-filtering strength | default |
| `--sharpness` | -7…7 | 0 | Deblock/RD sharpness bias | 0 (or +1..+2) |
| `--max-tx-size` | 32/64 | 64 | Cap transform size (64-pt zeroes top coeffs → blur) | 32 (detail retention) |
| `--qp-scale-compress-strength` | 0–3 | 0 | Compress QP across temporal layers (consistency) | 1–2 (UHQ) |
| `--luminance-qp-bias` | 0–100 | 0 | Lower QP in dark scenes | 0 (or ~30–50 for dark footage) |
| `--enable-variance-boost` | 0–1 | 0 | Preserve low-contrast fine texture | 1 (tune 0) |
| `--variance-boost-strength` / `--variance-octile` | 1–4 / 1–8 | 2 / 5 | Variance boost curve / 8×8 selectivity | defaults |
| `--enable-dlf` | 0–2 | 1 | Deblocking loop filter (2 = more accurate) | 1 |
| `--enable-cdef` | 0–1 | 1 | Constrained Directional Enhancement Filter | 1 (0 only for max grain/detail) |
| `--enable-restoration` | 0–1 | 1 | Loop restoration (Wiener + self-guided) | 1 (0 only for max detail) |
| `--enable-mfmv` | -1…1 | -1 (auto) | Motion Field Motion Vectors | auto |
| `--scm` | 0–3 | 2 | Screen-content detection | 2 (default) |
| `--enable-intrabc` | 0–1 | 1 (preset-based) | Intra block copy | default |
| `--fast-decode` | 0–2 | 0 | Decode-speed optimization (2 = fastest decode) | 0 for archival |
| `--lp` | 0–6 | 0 (auto) | Level of parallelism (threads + picture buffers) | auto, or tune per §Q7 |
| `--pin` | N (cores) | — | Pin to first N cores | for multi-encode partitioning |
| `--input-depth` | 8, 10 | 8 | Input/output bit depth | 10 (via ffmpeg `-pix_fmt yuv420p10le`) |
| `--color-format` | 0–3 | 1 | **Only yuv420 (4:2:0) supported** | 1 (downsample 4:2:2 upstream) |
| `--level` | 0, 2.0–7.3 | 0 (auto) | AV1 level | auto |
| `--color-primaries` / `--transfer-characteristics` / `--matrix-coefficients` | see doc | 2 | Color metadata | HDR: `bt2020` / `smpte2084` / `bt2020-ncl` + `--mastering-display`, `--content-light` |
| `--passes` / `--pass` | 1–2 / 0–2 | 1 / 0 | Multi-pass (VBR) | 2-pass only for VBR targets |

---

## B. Four-Tier Recommendation (x265 philosophy → SVT-AV1)

All tiers: **`tune=0` (VQ)**, **`rc=0` (CRF)**, **10-bit** (`-pix_fmt yuv420p10le`), `pred-struct=2`, `irefresh-type=2`, `keyint` = 10 s. CRF values are **initial guesses** to verify against your own footage with SSIMULACRA2/VMAF (CRF is content- and resolution-dependent).

| Tier | preset | tune | crf (start) | Key params | lookahead | film-grain | enable-tf / overlays | aq | mbr (kbps) |
|---|---|---|---|---|---|---|---|---|---|
| **UHQ** (max detail benchmark, not batch-practical) | 1 (0/`-1` for absolute) | 0 | **18** (16–20) | `enable-qm=1:qm-min=0`, `ac-bias=1.0-1.5`, `max-tx-size=32`, `qp-scale-compress-strength=1`, `enable-cdef=1`, `enable-restoration=1` | 120 | 8 (or 0) | tf=1 / overlays=1 | 2 | 100000 |
| **HQ** (slow production main) | **4** | 0 | **22** (20–24) | `enable-qm=1:qm-min=0`, `ac-bias=1.0`, `enable-overlays=1` | 60–120 | 8 | tf=1 / overlays=1 | 2 | 80000 |
| **SMALL** (size-first) | **6** | 0 | **27** (26–30) | `enable-qm=1:qm-min=0`, `enable-overlays=1`, higher `film-grain` | 120 | 12 (10–15) | tf=1 / overlays=1 | 2 | 40000 |
| **FAST** (batch speed) | **8** | 0 | **24** (23–26) | defaults (`enable-qm=0`, `enable-overlays=0`) | 60 | 8 (or 0 for speed) | tf=1 / overlays=0 | 2 | 60000 |

**Rationale:**
- **CRF anchor:** SVT `crf 30` ≈ x265 `crf 21` at ~1080p ([dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95); corroborated by "good 1080p start = crf 30" in [Ffmpeg.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Ffmpeg.md)). For **4K archival** the community drops CRF relative to 1080p ("for 4K it can be lowered", [Tenets gist](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)), yielding the transparent range **crf ≈ 18–24**, good ≈ 24–30, small ≈ 30–36 that you already identified in Q2. The tiers above place UHQ/HQ at the transparent end and SMALL in "good→small".
- **Preset choice:** presets 1–3 = "extremely high efficiency, encode time not important"; 4–6 = "balance"; 7–13 = "fast/real-time" ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md)). `preset 8 ≈ x265 medium` speed, `preset 6 ≈ x265 slow` ([dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95); [Fora Soft](https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1)). For 4K on consumer CPUs, preset 4 is ~1–5 fps and preset 6 roughly 2–3× faster (§C).
- **FAST tier** uses preset 8 + slightly lower CRF than HQ to compensate for the lower efficiency of the fast preset (so it still beats hardware encoders on detail/size).
- **`enable-overlays=1`** improves keyframe/base-layer quality; it is off by default but recommended for quality tiers ("leave always on", [dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)).
- **`enable-qm=1` with `qm-min=0`** is the psychovisual/compression lever analogous to x265 psy-rd; it was default-on in the SVT-AV1-PSY fork and is most effective with `tune=0` ([PSY README](https://github.com/nekotrix/svt-av1-psy)).

---

## C. Benchmark Citations (numbers)

1. **SVT-AV1 vs x264/x265 vs hardware, BD-rate anchored on x264 medium, VMAF default model, single machine, 1080p content** ([Fora Soft, June 2026](https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1)):

   | Config | BD-rate vs x264 medium | fps |
   |---|---|---|
   | SVT-AV1 **preset 8** | −51.0% | ~30 |
   | SVT-AV1 **preset 6** | −55.0% | ~11 |
   | SVT-AV1 **preset 4** | −57.0% | ~4.5 |
   | SVT-AV1 **preset 2** | −58.0% | ~1.2 |
   | x265 medium | −38.0% | ~22 |
   | x265 slow | −44.0% | ~9 |
   | x265 veryslow | −46.0% | ~3 |

   Headline: "software AV1 (SVT-AV1) reaches the same VMAF at about **55% less bitrate** at a slow preset" vs x264 medium. Note the **diminishing returns**: preset 2 vs 4 vs 6 is only −58% → −57% → −55% BD-rate (~1–2% per step) while speed is 1.2 → 4.5 → 11 fps (~2.5–3.7× per step). **This is the key speed-vs-efficiency insight: at 4K, preset 4–6 is the practical sweet spot; preset ≤2 is rarely worth the 4–10× slowdown for ~1–2% extra efficiency.**

2. **Preset encode-time scaling** (30 s, 1080p24 clip with film-grain synthesis; indicative, not 4K) ([dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)): preset 3 = 781 s, 4 = 340 s, 5 = 231 s, 6 = 146 s, 7 = 115 s, 8 = 109 s → preset 6 ≈ 2.3× faster than 4; preset 8 ≈ 1.3× faster than 6.

3. **10-bit vs 8-bit per-preset slowdown** ([colinmckellar, 2024](https://colinmckellar.com/2024/03/11/svtav1-10-bit/)): 10-bit is ~+40% to +140% slower than 8-bit depending on preset (e.g. preset 3 +120%, 4 +140%, 5 +60%, 6 +60%, 8 +100%). Crucially, **10-bit at a given preset ≈ equal-or-better quality than 8-bit at a much slower preset** (10-bit S3 ≈ better than 8-bit S1; 10-bit S6 ≈ 8-bit S1 at high VMAF). For 10-bit archival this justifies using a moderately fast preset (4–6) rather than chasing preset ≤2.

4. **Official version-to-version speed claims** ([CHANGELOG.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md)): v3.1.0 reports "~15–25% speedup for M1–M5 at the same quality levels" (fast-decode 0); v2.3.0 enabled AVX-512 + LTO by default (~2–4% speedup).

5. **"What CRF hits VMAF ~95–96 (≈ x265 crf 20–21 at 4K)?"** There is no universal table (VMAF/SSIMULACRA2 are content-dependent). Synthesized answer from the cited sources: at 4K with a **transparent target**, use SVT-AV1 **crf ≈ 18–22 at preset 4** (or crf ~1–2 points lower at preset 6–8 to offset lower efficiency). Since SVT `crf 30 ≈ x265 crf 21` at 1080p and 4K archival drops a few points below that, the transparent band crf 18–24 is the community-consistent range; start at **crf 22 / preset 4** and measure SSIMULACRA2 (target ≥ 97–98) and VMAF (target ≥ 95) on your own camera clips. For an independent full-matrix comparison of AV1 encoders at 4K (aom / rav1e / SVT), see the **MSU "High-Quality & 4K Encoding Comparison"** thread ([doom9](https://forum.doom9.net/showthread.php?t=180412)).

---

## D. x265 → SVT-AV1 Translation Table

| x265 concept | SVT-AV1 equivalent | Notes |
|---|---|---|
| `bframes` | **no explicit B-frames**; hierarchical structure via alt-ref frames + overlays. `pred-struct=2` (random access), `hierarchical-levels` (default 6 layers → 32-frame mini-GOP) | AV1 has no "B-frame" flag; hierarchy is the equivalent ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)) |
| `keyint` / `min-keyint` | `--keyint` (10 s ≈ x265 600 @ 60fps); **no `min-keyint`** | use `--force-key-frames` or scene-splitting (Av1an) for mid-GOP keyframes |
| `scenecut` | `--scd` | **does NOT insert keyframes** — only affects bit allocation (§E) |
| `aq-mode` / `aq-strength` | `--aq-mode` (0/1/2, default 2) | **no `aq-strength` knob** (internal deltaq). x265 aq-mode 4 ≈ SVT aq-mode 2 |
| `cutree` | TPL (always on) + `--enable-tf` (alt-ref MCTF) | `enable-tf=0` ≈ "cutree off" (approx.); TPL itself cannot be disabled |
| `psy-rd` | `tune=0` (VQ) + `enable-qm=1:qm-min=0` + `ac-bias` | SVT's perceptual stack ([PSY README](https://github.com/nekotrix/svt-av1-psy), [Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)) |
| `sao` | `--enable-cdef` + `--enable-restoration` | AV1 has no SAO; restoration = Wiener + self-guided (SG) filters |
| `deblock` | `--enable-dlf` (0/1/2) + `--sharpness` | no direct tC/β offsets like x265 |
| `ctu` (64) | superblock size 128 (preset ≤6) / 64 (preset ≥7) — preset-controlled, no user knob | "What presets do" table ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md)) |
| `me` / `merange` | no direct equivalent; preset controls ME (subpel, hierarchical ME) | no `merange` knob |
| `rc-lookahead` | `--lookahead` (-1…120) | 60–120 for quality tiers |
| `qcomp` | no direct equivalent; closest: `--qp-scale-compress-strength` (0–3) | TPL is internal and non-configurable |
| `ref` | max reference frames, preset-controlled (7 for ≤4, 5 for 5–9, 2 for 10) | no user knob |
| `qpmin` / `qpmax` | `--min-qp` / `--max-qp` | direct equivalent |
| `vbv-maxrate` / `vbv-bufsize` | `--mbr` (capped-CRF max bitrate); CBR has `--buf-sz` | **no vbv-bufsize for CRF** (only `--mbr-overshoot-pct`) |
| `level` | `--level` (0=auto, 2.0–7.3) | direct |
| `aud` / `hrd` | AV1 is OBU-based — no AUD needed; HRD not user-exposed for CRF | n/a |
| `colorprim`/`transfer`/`colormatrix` | `--color-primaries`, `--transfer-characteristics`, `--matrix-coefficients` + `--mastering-display`, `--content-light` | HDR10: `bt2020`/`smpte2084`/`bt2020-ncl` |

---

## E. Version Specifics (2.x / 3.x / 4.x)

From [CHANGELOG.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md) (authoritative) — **latest stable = `v4.2.0` (2026-07-14)**:

| Version | Date | Notable changes |
|---|---|---|
| **2.3.0** | 2024-10-28 | Preset shift (M12/M13→M11, M7–M11 down one); `--lp` redefined as levels of parallelism (`logical_processors` deprecated); **AVX-512 + LTO enabled by default**; `fast-decode` range → 0–2 |
| **3.0.0** | 2025-02-18 | **Presets repositioned, one removed → max distinct preset M10**; API break; ported SVT-AV1-PSY features into `tune 0`; added `avif` mode |
| **3.1.0** | 2025-07-24 | `--rtc` flag; `--chroma-qm-min/max`; S-frames; ~15–25% speedup M1–M5 (fast-decode 0) |
| **3.1.2** | 2025-08-24 | version-bump fix |
| **4.0.0** | 2026-01-13 | Added `tune 3` (IQ), `tune 4` (MS-SSIM), **`--ac-bias`**, `--adaptive-film-grain` toggle, `--max-tx-size`, **extended quarter-step CRF**, `--scm 3`, `--hbd-mds`, `--enable-intrabc` |
| **4.1.0** | 2026-03-23 | MD/entropy refactor; still-image efficiency |
| **4.2.0** | 2026-07-14 | **`tune 5` (VMAF)** ~15% VMAF BD-rate gain at minimal PSNR loss; `--cqp`, `--enable-intrabc`, `--hbd-mds`, `--enable-kf-tf`; `initial_display_delay` for A/V sync on seek |

**2.x → 3.x delta (what changes if ffmpeg bundles SVT-AV1 3.x vs 2.x):** (1) preset numbers shifted — a given `-preset N` means a different speed/quality point after v3.0 (do not carry over tuned CRF/preset pairs from 2.x); (2) API/ABI break at v3.0 (ffmpeg built against 2.x must be rebuilt against 3.x); (3) PSY perceptual features folded into `tune 0`; (4) `--lp` replaces `logical_processors`. Defaults of note in current master: `preset 8`, `tune 1` (PSNR), `rc 0`, `aq-mode 2`, `keyint -2` (~5 s), `scd 0`, `enable-overlays 0`, `film-grain 0`, `film-grain-denoise 0`, `adaptive-film-grain 1`.

---

## F. Risks & Caveats (params that silently require conditions)

1. **`--crf` forces `aq-mode=2`.** "If `--crf` is set, then aq-mode will be forced to 2; if `-q`/`--qp` is set, the encoder will use whatever aq-mode is set" ([user guide](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/svt-av1_encoder_user_guide.md)). To use aq-mode 0/1 with a constant-quality target, use `--qp` or `--cqp`, not `--crf`.
2. **`--scd` does not insert scene-cut keyframes.** "SVT-AV1 does not insert key frames at scene changes, regardless of the `scd` parameter" — use **Av1an** (scene-splitting) or `--force-key-frames` if you need mid-GOP keyframes ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md), [Tenets](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)). This is a direct x265→SVT mismatch: **no `min-keyint`/`scenecut` behavior.**
3. **Preset-number semantics changed across versions.** v2.3 shifted M7–M13 down; v3.0 removed a preset (max M10). Preset tables from pre-2025 blogs don't match current builds.
4. **`--mbr` (capped CRF) is unreliable** — community reports it as "largely unreliable" as a hard cap ([Tenets footnote](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)); it's a soft cap via re-encode ([Appendix-Rate-Control.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Appendix-Rate-Control.md)). Also, a known v1.4.1 bug "CRF with maxrate … significantly limited" was fixed; verify cap behavior on your build.
5. **`enable-qm` is a psychovisual feature intended for `tune 0`.** It is default-off upstream (default-on in the PSY fork). Use `enable-qm=1` with `tune=0`; with `tune 1` (PSNR default) it may not help and can hurt metrics ([PSY README](https://github.com/nekotrix/svt-av1-psy), [Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)).
6. **`film-grain-denoise=1` can delete fine texture detail.** Official guidance: prefer `film-grain-denoise=0` (default) and verify levels manually; too-high `film-grain` with denoise off → **noise stacking** ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md)).
7. **`lookahead` matters mostly for VBR/CBR.** For CRF (rc 0) it's `-1` (auto); large values mainly benefit `rc 1/2` ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md), [user guide](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/svt-av1_encoder_user_guide.md)).
8. **Only 4:2:0 is supported.** `--color-format` = "only yuv420 is supported at this time" — 4:2:2 sources must be downsampled before/in ffmpeg (§Q6) ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)).
9. **`keyint -1` ("infinite") is CRF-only**; `--pass 2` is VBR-only; `--tbr` only for rc 1/2; `--buf-sz` only CBR; `--mbr` only CRF ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)).
10. **Presets ≤3 parallelize poorly** — highest-quality presets use dependency-heavy features; thread scaling drops (also, per-frame single-thread mode in v4.2.0). Don't expect preset 0–3 to saturate a 16–32-core machine ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md)).
11. **`tune 5` (VMAF) is video-only; `tune 3` (IQ) is still-image-only** ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)).
12. **No `tune-mbr` parameter exists.** The user's "tune-mbr" is `--mbr` (MaxBitRate, capped CRF). There is no MBR "tune".

---

## Q6. 4:2:2 → 4:2:0 Chroma Handling

- SVT-AV1 accepts **only 4:2:0** ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md)). Downsample chroma **upstream of the encoder** — no SVT flag needed.
- In ffmpeg: `-pix_fmt yuv420p10le` performs the 4:2:2→4:2:0 (and 8→10-bit) conversion via swscale. Use a high-quality scaler flag for chroma if you care (`-vf scale=...:flags=bicubic` or `zscale`), and **verify `-chroma_sample_location`/`-color_range` are carried correctly** (SVT's metadata options are `--chroma-sample-position`, `--color-range`; ffmpeg needs `-chroma_sample_location:v`).
- Community consensus: no additional chroma tuning is required; the 4:2:0 downsample is a source-processing step, and AV1's CfL (chroma-from-luma) prediction handles chroma efficiently on its own ([Tenets](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)).

## Q7. Multi-threading

- **`--lp` (0–6, default 0 = auto)** = Level of Parallelism: higher → more threads + more in-flight pictures → higher fps + higher memory. It is **not** a thread-count pin, and output is **identical** between `--lp 1` and `--lp n` in default CRF (quality-neutral threading) ([Parameters.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md), [CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md)).
- **`--pin N`** pins execution to the first N cores; if `--lp` is unset, default parallelism is sized to those N cores. For multi-encode partitioning use affinity tools (`taskset`/`numactl` on Linux; Windows `start /affinity`).
- **ffmpeg `-threads` with libsvtav1:** SVT-AV1 manages its own thread pool; pass SVT options through `-svtav1-params lp=N` rather than relying on `-threads`. ffmpeg ≥5.1 fully supports `-svtav1-params` ([Ffmpeg.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Ffmpeg.md)).
- **Scaling ceiling:** ~16 cores usable efficiently at 1080p preset 4–6; **higher resolutions scale to more cores**. For >16 cores, split by scene with **Av1an** (runs N encoder instances) rather than a single encode ([CommonQuestions.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md), [Code Calamity "double your encoding speeds"](https://codecalamity.com/double-your-video-encoding-speeds/)).
- **Windows-specific:** SVT-AV1 scales well on Windows; a v2.1.0 fix addressed a "performance regression for systems with multiple processor groups" (i.e. >64 logical cores on Windows — relevant to HEDT/workstation boxes) ([CHANGELOG.md](https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md)). 10-bit is ~40–140% slower than 8-bit per preset, so budget threads/`--lp` accordingly for 10-bit archival ([colinmckellar](https://colinmckellar.com/2024/03/11/svtav1-10-bit/)).

---

## Sources (all URLs relied upon)

**Official SVT-AV1 (canonical GitLab + GitHub mirror raw files):**
- <https://gitlab.com/AOMediaCodec/SVT-AV1> (canonical)
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md>
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/svt-av1_encoder_user_guide.md>
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Appendix-Rate-Control.md>
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md>
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Ffmpeg.md>
- <https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md>
- <https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md> (blame/reference view)

**Community guides & threads:**
- <https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695> ("Tenets of AV1 Encoding")
- <https://gist.github.com/ankushian/a22862c6f92a51574e1720d1d392941d> ("AV1 encoding pocket guide")
- <https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95> ("SVT-AV1 Encoding Guide")
- <https://forum.doom9.net/showthread.php?t=185159> ("What settings do you recommend for SVT-AV1")
- <https://forum.doom9.org/showthread.php?t=185686> ("How do you decide which value for --film-grain")
- <https://forum.doom9.net/showthread.php?t=180412> (MSU High-Quality & 4K Encoding Comparison)
- <https://github.com/nekotrix/svt-av1-psy> (SVT-AV1-PSY, psychovisual fork)
- <https://github.com/psy-ex/svt-av1-psy> (SVT-AV1-PSY, psy-ex fork)
- <https://github.com/juliobbv-p/svt-av1-hdr> (svt-av1-hdr fork)

**Benchmarks:**
- <https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1>
- <https://colinmckellar.com/2024/03/11/svtav1-10-bit/>
- <https://www.ixbt.com/sw/h265-av1-video-encoding-test.html> (4K H.265 vs AV1, Russian)
- <http://web.archive.org/web/20260321004616/https://wiki.x266.mov/blog/svt-av1-fourth-deep-dive-p1> (Codec Wiki "Deep Dive into SVT-AV1's Evolution" presets v2.0→v3.0)

**Code Calamity & misc:**
- <https://codecalamity.com/double-your-video-encoding-speeds/>
- <https://codecalamity.com/encoding-uhd-4k-hdr10-videos-with-ffmpeg/>
- <https://codecalamity.com/encoding-settings-for-hdr-4k-videos-using-10-bit-x265/>
- <https://codecalamity.com/introducing-fastflix-av1-encoder-gui-and-more/>
- <https://www.phoronix.com/news/SVT-AV1-2.3>
- <https://trac.ffmpeg.org/wiki/Encode/AV1>
- <https://ffmpeg.party/guides/av1/>
- <https://lafibre.info/tv-numerique-hd-3d/comparer-h-264-vp9-av1/>
