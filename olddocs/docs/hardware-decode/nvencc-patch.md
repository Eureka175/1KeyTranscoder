# NVEncC `--avhw` Patch — Provenance and Integration Reference

`research/rigaya-nvencc-avhw` · base `main` @ `15cf218` · worktree `F:\1KeyTranscoder\work\_worktrees\1KT-avhw`

> **Path note.** This worktree was originally created at `F:\1KT-avhw` and was
> later collected into the project folder under `work\_worktrees\`. Paths in this
> document have been updated accordingly; nothing else changed.

> **What this document is.** A single, auditable record of *what the patch is,
> what built it, what was measured, and what is still open* — written when the
> branch was closed out. It adds **no new experiment**: every number below is
> taken from the runtime work recorded in
> [`nvencc-avhw-experiment.md`](nvencc-avhw-experiment.md), and the two
> verification results dated *close-out* were re-run against **freshly cloned
> pristine upstream checkouts**.
>
> **What it is not.** It is not a distribution plan and not a production change.
> Nothing in `main` is touched by this branch, and hardware decode remains
> non-default.

---

## 1. Identity

| Field | Value |
|---|---|
| Tool under patch | **NVEncC 9.31 (r4047) by rigaya** (shipped baseline `tools/NVEncC_9.31_x64/NVEncC64.exe`) |
| Upstream repository | `https://github.com/rigaya/NVEnc` |
| **Baseline revision (pinned)** | tag `9.31`, commit **`2cb9d810c045202548b98ff130b12bc764eb39ea`** |
| Version declaration at that revision | `VER_STR_FILEVERSION "9.31"` (`NVEncCore/rgy_version.h:33`) |
| Patch file | `work/hwdecode2/patch/0001-avhw-keep-leading-pictures.patch` (2 832 bytes, gitignored `work/`) |
| **Patch sha256** | **`53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc`** |
| Scope | **18 added lines, 2 files, 0 deletions** — `NVEncCore/NVEncPipeline.h` (+11), `NVEncCore/rgy_input.h` (+7) |
| Nature of change | one condition around `m_dec->frameQueue()->releaseFrame(...)`, plus a read-only accessor that exposes the existing `--seek` parameter to the pipeline. **No API/ABI change, no new state, no threading change, no encoder parameter change.** |

### 1.1 Why the patch is two files

The defect is a filter in NVEncC's hardware output stage that treats the first
*fed packet's* PTS as the presentation start. The filter's only legitimate
purpose is to honour `--seek`. So the fix needs one piece of information the
pipeline did not previously have — *was a seek requested?* — and that is what
the 7-line accessor provides. The behavioural change itself is a single
condition.

---

## 2. Provenance chain

### 2.1 Source ↔ patch

**Method.** Clone upstream at the pinned commit into a fresh directory, apply the
patch with `git apply` (strict, no `--ignore-whitespace`, no fuzz options), then
hash the two resulting files and compare with the files in the build tree that
produced the tested binary.

| Step | Command | Result |
|---|---|---|
| Fresh pristine checkout | `git clone https://github.com/rigaya/NVEnc.git && git checkout 2cb9d810c045202548b98ff130b12bc764eb39ea` | `HEAD` = `2cb9d810`, working tree clean |
| Apply check (strict) | `git apply --check --verbose 0001-avhw-keep-leading-pictures.patch` | **exit 0** — both files checked, no offset, no fuzz |
| Apply | `git apply …` | **exit 0**; `git diff --stat` = `NVEncPipeline.h \| 11 +++++++++++`, `rgy_input.h \| 7 +++++++`, **18 insertions, 0 deletions** |

| File | pristine + patch (re-verified at close-out) | built tree | match |
|---|---|---|---|
| `NVEncCore/NVEncPipeline.h` | `daed88a78fbd091a1db159fd3cc9a1cdead46f56c0891aae6b3dd1e37aa0b00d` | same | ✅ |
| `NVEncCore/rgy_input.h` | `e03e828e06b44f90ef95578866e7fb67cec602b1b6bf5e04f374a53004641627` | same | ✅ |

Both hashes also match the values recorded at build time in
`work/hwdecode2/raw/provenance.json` (`patched_sources.*.pristine_plus_patch_sha256`,
`match: true`). The independent re-check performed at close-out reproduced the
build-time record exactly.

### 2.2 Tested binary

| Field | Value |
|---|---|
| Path | `third_party/NVEncC/_build/x64/RelStatic/rt/NVEncC64.exe` |
| sha256 | **`dcf6d7a63143c777e54a749281bef7ee1dd50d61c44b14d128e66a1217c8be4b`** |
| Size | 77 757 440 bytes |
| Version string | `NVEncC (x64) 9.31 (r1) by rigaya, Sep 12 2026 (VC 1951/Win)` · `[NVENC API v13.1, CUDA 13.1]` |
| Built from | the two files in §2.1, after a deliberate revert/re-apply cycle that caught a half-patched tree (recorded in `nvencc-avhw-experiment.md` §5.1.1) |

> **✅ 二进制现存位置（2026-09 补注）。** 上面那个 build-tree 路径已随研究分支
> 工作树的移除而消失（`git worktree remove` 会连同未跟踪文件一起删除）。
> 该被测二进制已保存在项目内：
> **`tools/avhw/NVEncC_9.31_avhw/NVEncC64.exe`**，2026-09 实测 sha256 仍为
> `dcf6d7a63143c777…7c8be4b`（与上文一致），`--version` 报
> `9.31 (r1) ... CUDA 13.1`，reader 列表含 `avhw` —— 可直接用于复核本补丁。
> QSVEncC 对应物在 `tools/avhw/QSVEncC_8.26_avhw/`。
> 注意 `tools/` 是 gitignored：**该二进制不受版本控制保护**。

The tested binary is a **research build, not a distribution candidate**: `avs`
and `vpy` readers disabled, a generated CUDA 13.0 MSBuild shim, FFmpeg linked
dynamically rather than statically. Consequences are listed in §6.

### 2.3 Build inputs and environment deviations

Full recipe: `nvencc-avhw-experiment.md` §9 (`work/hwdecode2/build_nvencc.ps1`).

| Component | Source |
|---|---|
| MSVC 14.51 (19.51) | Visual Studio Community 2026 |
| CUDA 13.1 headers / `nvcc` 13.1.115 / import libs | conda `cuda131` (`nvidia` channel) — matches the NVENC API 13.1 headers already in the repo |
| CUDA MSBuild customizations (13.0 props/targets) | generated shim over the CUDA 12.9.1 installer files |
| NPP headers + import libraries | `libnpp\npp_dev\` inside the same installer |
| FFmpeg dev (headers + import libs), `libvmaf`, `libplacebo`, `libdovi`, `hdr10plus-rs` | rigaya `ffmpeg_dlls_for_hwenc` release `20250830` |
| ONNX Runtime dev | NuGet `Microsoft.ML.OnnxRuntime` 1.23.2 |

Environment files changed **outside the candidate patch** (all recorded in
`provenance.json` under `build_env_changes`):

| File | Change | Reverts to upstream |
|---|---|---|
| `NVEncCore/rgy_version.h` | `ENABLE_AVISYNTH_READER` / `ENABLE_VAPOURSYNTH_READER` = 0 | yes (`work/hwdecode2/disable_reader_sdks.py --revert`) |
| `cudaver.props` | BuildCustomizations import paths pointed at the extracted `third_party/` copy | n/a (build-only) |
| `Directory.Build.props` (new) | sets `CudaToolkitCustomDir`, adds CUDA includes | n/a (build-only) |
| vendored Vship header | adds `Vship_PRIMARIES_DisplayP3` (absent upstream at v4.1.0 but referenced by `NVEncFilterSsim.cpp`) | n/a (build-only) |

None of these files is part of the patch, and none touches the `--avhw` decode,
timestamp or muxing path.

### 2.4 Accidental / unrelated diff check — clean

At close-out the branch's own git diff was inspected:

```
git diff main...HEAD --stat
 .gitignore                                     |    7 +-
 docs/hardware-decode/README.md                 |   11 +-
 docs/hardware-decode/nvencc-avhw-experiment.md | 1176 ++++++++++++++++++++++++
```

* **No production source file is modified.** The three paths are documentation
  and a `.gitignore` addition.
* The `.gitignore` change appends `third_party/` (third-party source checkouts
  and toolchains used as build inputs) under a comment explaining why. It is
  scoped to research build inputs and does not affect any tracked production
  artefact.
* The patch itself lives in gitignored `work/` by project convention, so its
  durable record is this document plus the quoted diff in
  `nvencc-avhw-experiment.md` §5.2 and `rigaya-avhw-analysis.md`.

---

## 3. What the patch changes, in one paragraph

`NVEncCore/NVEncPipeline.h`, `PipelineTaskNVDecode::getOutputFrame()`: the
original code buffered decoded pictures until one satisfied
`dispInfo.timestamp >= m_hwDecFirstPts`, then advanced a running cursor `istart`
past the earlier entries and called `m_dec->frameQueue()->releaseFrame()` on
them, so the emission loop below never emitted them. `m_hwDecFirstPts` is the
PTS of the first packet **fed to the decoder** — in decode order, the first IRAP
— not the first PTS that should be **presented**. On Sony XAVC those differ by
the leading-picture count, so the loop silently discarded exactly those
pictures. The patch adds an early branch: **when no seek was requested**
(`m_input->getSeekParam().first <= 0.0f`), set `m_gotFrameAfterFirstPts` and
`break` before the filter, leaving the buffered pictures to be emitted in
display order by the existing loop. When a seek *was* requested, the original
filter runs **verbatim**, which is what the filter was written for and why the
seek regression is byte-for-byte unchanged.

---

## 4. Runtime test record

Harnesses (gitignored, `work/hwdecode2/`): `run_regression.py` +
`check_regression.py` (correctness matrix and assertions), `run_seektrim.py`,
`run_longrun.py`, `fingerprint.py`, `verify_frames_semantics.py`,
`record_provenance.py`.

### 4.1 Correctness matrix — 13 assertions × 16 records: PASS

Raw: `work/hwdecode2/raw/regression/regression-{patched,baseline}.json`,
verdict: `regression-verdict.json` — `failures: []`, `limits:` nine entries
(the section 13 limitations, all declared, none hidden).

| Case | Reader | Container | Leading pics | Baseline `encoded` | Patched `encoded` | Identical to `--avsw` |
|---|---|---|---|---|---|---|
| Sony XAVC HS 30 f | `--avhw` | 30 | 3 | **27** | **30** | bytes, PTS, keyframes, fingerprints |
| Sony XAVC HS 330 f | `--avhw` | 330 | 3 | **327** | **330** | bytes, PTS, kf, fp |
| Sony XAVC HS long (10 170 f) | `--avhw` | 10 170 | 3 | **10 167** | **10 170** | bytes, PTS, kf, fp |
| Sony H.264 4:2:2 (195 f) | `--avhw` | 195 | 2 | **193** | **195** | bytes, PTS, kf, fp |
| DJI control | `--avhw` | 105 | 0 | 105 | **105** | bytes, PTS, kf, fp |
| x265 re-encode | `--avhw` | 30 | 0 | 30 | **30** | bytes, PTS, kf, fp |
| synthetic `testsrc2` | `--avhw` | 60 | 0 | 60 | **60** | bytes, PTS, kf, fp |
| Sony in MKV, no edit list | `--avhw` | — | 3 | **27** | **30** | bytes, PTS, kf, fp |
| *(the same eight cases)* | `--avsw` | | | *unchanged on every row* | | |

Each record captures five independent things about the delivered file: frame
count (`ffprobe -count_frames`), the ordered `(pts, dts, flags, size)` packet
sequence, the keyframe index sequence, a per-frame picture fingerprint, and the
output sha256 — plus the 13 assertions, including C6 (the reader really was
`avcuvid`; no silent software fallback) and C12 (the two readers fingerprint the
same **source** pictures position-for-position, with no encoder involved).

### 4.2 Frame fingerprint and output byte-identity

Raw decode per-frame mean Y/U/V over raw `yuv420p10le`:

| Clip | `--avhw` frames | `--avsw` frames | Compared | Identical means | Max deviation |
|---|---|---|---|---|---|
| Sony 30 f | 30 | 30 | 30 | **30 / 30** | 0.0000 |
| Sony 330 f | 330 | 330 | 330 | **330 / 330** | 0.0000 |

Encoded MP4, same source and same encoder settings
(`-c hevc --output-depth 10 --cqp 23`):

```
sha256 avhw 6336165c02a8a294b0fd497b5a43759a321ba748a27d2478327ee6206926fc04
sha256 avsw 6336165c02a8a294b0fd497b5a43759a321ba748a27d2478327ee6206926fc04
identical bytes: True ; packet sequence: 30 vs 30, identical (pts, dts, flags, size)
```

The e2e benchmark independently reproduced the same byte-identity on a
3 599-frame Sony clip **and on a 3 597-frame DJI clip** (`e2e-benchmark.md` §3).

### 4.3 Seek / trim regression — 17/17 identical

Raw: `work/hwdecode2/raw/seektrim/seektrim-patched.json`.

| Clip | Cases | Result |
|---|---|---|
| C1083 (330 f, 5.5 s) | `--seek` 0.5 / 2 / 3 / 4.5, `--frames 300`, `--trim 0:59`, `--trim 100:199` | identical on both binaries |
| C1169 (10 170 f, ~169 s) | `--seek` 0.5 / 10 / 30 / 120 / 165, `--frames 300`, `--trim 0:59`, `--trim 100:199`, `--seek 30 --trim 0:29` | identical on both binaries |

`--seek 5.2` on the 5.5 s clip fails on **both** binaries and both readers
(`No video packets found!`, rc = 1) — pre-existing, loud, not a patch effect.

### 4.4 Long-run — 3 000 / 6 000 / 10 000 / ≥10 170 (clamped) / full 10 170

Raw: `work/hwdecode2/raw/longrun/longrun-{patched2,baseline}.json` and
`longrun-patched-final.json` (confirmation on the rebuilt binary).

| Requested | Reader | Binary | `encoded` | Output | Packets | Wall s | fps |
|---|---|---|---|---|---|---|---|
| 3 000 | `--avhw` | patched | 2 997 | 2 997 | 2 997 | 24.0 | 147.4 |
| 3 000 | `--avsw` | patched | 2 997 | 2 997 | 2 997 | 42.7 | 77.0 |
| 6 000 | `--avhw` | patched | 5 997 | 5 997 | 5 997 | 43.7 | 150.1 |
| 10 000 | `--avhw` | patched | 9 997 | 9 997 | 9 997 | 69.0 | 151.1 |
| 10 000 | `--avhw` | baseline | 9 997 | 9 997 | 9 997 | 78.5 | 130.1 |
| ≥10 170 (clamped) | `--avhw` | baseline | **10 167** | 10 167 | 10 167 | 108.5 | 97.1 |
| ≥10 170 (clamped) | `--avsw` | baseline | 10 170 | 10 170 | 10 170 | 132.3 | 79.0 |
| **full 10 170** | `--avhw` | **patched** | **10 170** | **10 170** | **10 170** | 110.4 | 93.8 |
| full 10 170 | `--avsw` | patched | 10 167 | 10 167 | 10 167 | 155.4 | 66.9 |

Confirmed on the rebuilt binary: 2 997 / 2 997 / 9 997 / 9 997 frames,
`--avhw` 147.3 and 151.4 fps against `--avsw` 81.5 and 88.8 fps, NVEncC peak
working set 949–956 MiB (`--avhw`) versus 1 817–1 822 MiB (`--avsw`), VRAM
1 593–1 682 MiB versus 1 099–1 102 MiB, and **every output hash identical to the
first pass**.

No run in the sweep produced a crash, a short read, a container with gaps, or a
mismatch between `encoded`, the container count and the packet count. The
`18 000` request exceeds the clip and is clamped to EOF; it completed with
`rc=0` — the earlier `STATUS_STACK_BUFFER_OVERRUN` did not recur, and nothing in
the patch was changed to make it pass.

### 4.5 GPU-resident proof

| Evidence | Requirement | Patched build | Baseline |
|---|---|---|---|
| `Input Info` | `avcuvid:` — never `avsw:` | `avcuvid: H.265/HEVC, 3840x2160, 60000/1001 fps` | same |
| `Vpp Filters` | GPU-only | `copyDtoD` | `copyDtoD` |
| Engine counters | VE (encoder) **and** VD (decoder) busy | `VE: 66.3`, `VD: 35.0` | `VE: 34.1`, `VD: 13.1` |
| Host round-trip | none introduced | `copyDtoD` is device-to-device only; no `hwdownload`, no software decoder | — |

`--avsw` on the same patched binary still reports `avsw: hevc(...)->p010 [AVX2]`
with no `VD` figure, so the two paths remain distinct.

---

## 5. Integration prerequisites

Required before this patch is wired into anything:

1. **Re-run the three suites after any rebase**: `run_regression.py` +
   `check_regression.py`, `run_seektrim.py`, `run_longrun.py`. The regression
   gate is the fixture set in §4.1 — any change to the three control counts is a
   regression.
2. **Reconcile frame counts against the delivered container**, never against the
   reader's self-report: container samples · reader estimate · `encoded N frames` ·
   an independent count of the delivered file. `FramePosList::setPocAndFix`
   remains unpatched (§6), so the reader's estimate can still under-report.
3. **Never use `--frames N` as an exact frame budget** — it delivers
   `N − leading_pictures` on Sony for *both* readers (§6).
4. **Extend to the full corpus**: the 151 Sony files plus DJI, with the
   four-way count reconciliation. Phase 1 validated the *prediction*
   `avhw = container − leading` on 151/151; after the patch the invariant is the
   simpler `avhw == container` and has not yet been checked corpus-wide.
5. **Re-test on a multi-GPU host** before enabling device selection.
6. **Keep software decode as the default** until an integration session
   validates the hardware path end to end.

---

## 6. Known limitations (unchanged from the experiment record)

The full list is `nvencc-avhw-experiment.md` §13 (16 entries, `L1`–`L16`). The
ones that could bite an integrator:

| # | Limitation | Impact |
|---|---|---|
| L1 | **Single-GPU host** — one CUDA device; `--device 1` → `Invalid Device Id = 1` | Multi-GPU is **untested**. Do not claim multi-GPU readiness. |
| L2 | **Toolchain differs from the shipped binary** (CUDA 13.1 / MSVC 14.51 vs CUDA 11.8 / MSVC 14.44) | Cross-binary wall clock and fps are **not a controlled benchmark**. Every correctness claim is toolchain-independent (counts, PTS, keyframes, fingerprints, bytes). |
| L9 | **`--frames N` yields `N − leading_pictures`** (pre-existing, identical on both binaries) | Integration must request `N + leading_pictures` or drive by container count. |
| L11 | **`--trim` + `--avhw` is short by the leading-picture offset** (pre-existing) | Same compensation. |
| L12 | **One non-reproducible `0xC0000005`** on a 10 170-frame run (1 in 6 attempts, never reproduced; every completed run frame-exact) | A stability risk for long unattended runs. |
| L13 | **Parallel encode / `--split-enc` untested** — argued from source only | Do not claim parallel-encode safety. |
| L14 | **Corpus scale: 8 cases**, not the whole 151-file Sony corpus | Full-corpus sweep is prerequisite 4. |
| L15 | **`avs` / `vpy` readers disabled** in the research build | Unreachable from 1KeyTranscoder, which passes a file path. |
| L16 | **`FramePosList::setPocAndFix` is not fixed** | The reader's `N frames` / `--log-framelist` can under-report on OpenGOP-ish inputs. Verification hazard, not a data-loss hazard on the measured corpus. Deliberately not bundled — see `rigaya-avhw-analysis.md` (branch `research/rigaya-avhw-source`) and `nvencc-second-path-analysis.md`. |

Additional semantic difference recorded at close-out: a patched `--avhw` no
longer honours `AV_PKT_FLAG_DISCARD` the way `--avsw` and FFmpeg do (synthetic
fixtures E/F go 27 → 30). On the measured corpus those are real pictures and
recovering them is correct, but it **is** a behaviour change and belongs in the
adoption decision.

---

## 7. Status

```text
PATCH STATUS: READY FOR PROJECT INTEGRATION
BRANCH STATUS: READY TO MERGE / READY TO ARCHIVE

  baseline        NVEncC 9.31, rigaya/NVEnc 2cb9d810c045202548b98ff130b12bc764eb39ea
  scope           18 added lines, 2 files, 0 deletions
  patch sha256    53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc
  applies         clean (strict git apply, exit 0, no fuzz) to a freshly cloned
                  pristine baseline -- re-verified at branch close-out
  provenance      pristine+patch file hashes == built-tree hashes, byte for byte
                  (daed88a7... / e03e828e...), matching the build-time record
  binary          dcf6d7a63143c777e54a749281bef7ee1dd50d61c44b14d128e66a1217c8be4b
  correctness     13/13 assertions x 16 records PASS; failures: []
  identity        patched --avhw output byte-identical to --avsw on every
                  leading-picture case (MP4 sha256 6336165c...)
  regression      controls (DJI / x265 / synthetic) unchanged; seek+trim 17/17
                  identical before/after
  pipeline        avcuvid + copyDtoD; VE and VD engines busy; no host copy
  cost            patch adds no measurable CPU; e2e benchmark: 2.6x less CPU per
                  frame, but NOT faster (single-way ~11% slower on the same binary)

  open before production adoption
    - full 151-file corpus sweep with four-way count reconciliation
    - multi-GPU and parallel/split-encode validation (single-GPU host today)
    - decide the AV_PKT_FLAG_DISCARD semantic difference
    - keep FramePosList::setPocAndFix out of this patch (separate upstream report)

  This is a READY-to-integrate PATCH, not a READY-to-ship binary. The research
  build (avs/vpy readers disabled, generated CUDA MSBuild shim, dynamically
  linked FFmpeg) must not be distributed.
```

Cross-branch conclusion and the integration prerequisites that apply to all four
research branches: `docs/hardware-decode/research-conclusion.md`.
