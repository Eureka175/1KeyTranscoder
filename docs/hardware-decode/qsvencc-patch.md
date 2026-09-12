# QSVEncC `--avhw` Patch — Provenance and Integration Reference

`research/rigaya-qsvencc-avhw` · base `main` @ `15cf218` · worktree `F:\1KeyTranscoder\work\_worktrees\1KT-qsv`

> **Path note.** This worktree was originally created at `F:\1KT-qsv` and was
> later collected into the project folder under `work\_worktrees\`. Paths in this
> document have been updated accordingly; nothing else changed.

> **What this document is.** The single auditable record for the QSVEncC patch:
> its exact scope, the revision it was built from, the proof that the patch file
> reproduces the built sources, the tested binary, and every measurement that
> supports the claim.
>
> **The claim, scoped to exactly what was measured:**
>
> > **Runtime-proven on QSVEncC 8.26 pinned revision; not yet a general claim for
> > later releases.**
>
> No new experiment was run for this close-out document. The one *verification*
> added at close-out (a freshly cloned pristine checkout, a strict `git apply`,
> and a file-hash comparison against the built tree) is dated and labelled.
> Production code in `main` is untouched and hardware decode remains non-default.

---

## 1. Identity

| Field | Value |
|---|---|
| Tool under patch | **QSVEncC 8.26 (r4504)**, shipped baseline `tools/QSVEncC_8.26_x64/QSVEncC64.exe` (built 2026-08-08) |
| Upstream repository | `https://github.com/rigaya/QSVEnc` |
| **Baseline revision (pinned)** | commit **`b14c9652b34b998351516cc63c86eb990c01c7c8`** (2026-08-08 21:14:16 +0900) |
| Version declaration at that revision | `VER_STR_FILEVERSION "8.26"` (`QSVPipeline/rgy_version.h:35`) |
| Patch file | `work/qsvbuild/0001-avhw-keep-leading-pictures.patch` (2 285 bytes, gitignored `work/`) |
| **Patch sha256** | **`5621ebb0f0277743a3bb5f18176134a134857d72e8a67f7f452c63dd08e53d98`** |
| Scope | **15 added lines, 1 line replaced, 2 files** — `QSVPipeline/qsv_pipeline_ctrl.h` (+10/−1), `QSVPipeline/rgy_input.h` (+6) |
| Nature of change | one admission condition gained a `!bSeeked ||` guard, plus a read-only accessor exposing the existing `--seek` parameter to the pipeline. **No API/ABI change, no new state, no threading change, no encoder parameter change.** |
| Also in the worktree | `work/qsvbuild/poc_patch.py` — the byte-level editor that produced the tested build's sources |

### 1.1 Relationship to the NVEncC patch — same rule, different code

| | NVEncC 9.31 | QSVEncC 8.26 |
|---|---|---|
| File | `NVEncCore/NVEncPipeline.h` | `QSVPipeline/qsv_pipeline_ctrl.h` |
| Class / function | `PipelineTaskNVDecode::getOutputFrame()` | `PipelineTaskMFXDecode::sendBitstream()` |
| Anchor state | `m_hwDecFirstPts` | `m_firstPts` |
| "already done" flag | `m_gotFrameAfterFirstPts` | `m_decFrameOutCount > 0` |
| Mechanism | bulk list-trim inside a seek-adjust loop | per-output admission test |
| Shared file with the other tool? | **No.** Each pipeline header is vendor-specific. | **No.** |
| Patch sha256 | `53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc` | `5621ebb0f0277743a3bb5f18176134a134857d72e8a67f7f452c63dd08e53d98` |

The two tools independently implement the same incorrect rule ("treat the first
fed packet's PTS as the hardware-decode output-start boundary"), which is why
the symptom is identical. **The fixes are not interchangeable** and neither
patch should be described as a port of the other. Only the *reader*
(`rgy_input_avcodec.cpp/.h`) is byte-identical between the two trees; the defect
is one layer further in.

---

## 2. Provenance chain

### 2.1 The exact defect

`QSVPipeline/qsv_pipeline_ctrl.h`, `PipelineTaskMFXDecode::sendBitstream()`,
original 8.26 condition:

```cpp
if (surfDecOut != nullptr && lastSyncP != nullptr
    // 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する
    && (!m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

`m_firstPts` is set at `:1066-1069` from the **first bitstream fed to MFX** (the
IDR, `pts 3003` after libavcodec's "Offset DTS by 2002 to make first pts zero").
Sony XAVC presents 3 pictures before that IDR (`pts 0 / 1001 / 2002`); MFX emits
them in display order and each is rejected. `m_decFrameOutCount` is incremented
only on **emit** (`:1157`), so it stays `0` while rejections are happening and
the `|| m_decFrameOutCount > 0` escape clause never fires. **MFX decoded all 30
pictures; the pipeline discarded 3.** The patch adds `!bSeeked ||` so the filter
runs only when a seek was actually requested — which is the only purpose the
comparison ever legitimately served.

### 2.2 Source ↔ patch

**Method (performed at close-out, against a freshly cloned pristine checkout).**

1. `git clone https://github.com/rigaya/QSVEnc.git` into a clean directory.
2. `git checkout b14c9652b34b998351516cc63c86eb990c01c7c8`.
3. Regenerate the patch from a second pristine checkout by applying the two
   behaviour edits byte-for-byte (ASCII inserts into CRLF files, BOM preserved),
   so the patch contains **only** the behaviour change — no instrumentation, no
   build variation.
4. `git apply --check` and `git apply` against step 2's checkout.

| Step | Result |
|---|---|
| `git apply --check --verbose 0001-avhw-keep-leading-pictures.patch` | **exit 0** — both files checked; `qsv_pipeline_ctrl.h` hunk succeeded, no fuzz |
| `git apply …` | **exit 0**; `git diff --stat` = `qsv_pipeline_ctrl.h \| 10 +++++++++-`, `rgy_input.h \| 6 ++++++`, **15 insertions, 1 deletion** |

**Equality with the tested build's sources.** The tested tree carries
instrumentation in addition to the patch, so the comparison is made after
excluding (a) instrumentation-only lines added by `work/qsvbuild/instrument.py`,
`instrument_pipeline.py` and the reader trace, and (b) blank lines, because the
PoC editor left **one stray blank line** inside the patched condition block.
With those excluded, every statement and comment is identical:

| File | tested tree (instrumentation stripped) | pinned revision + standalone patch | match |
|---|---|---|---|
| `QSVPipeline/qsv_pipeline_ctrl.h` | `f46a106a37ce61154bee7484…` | `f46a106a37ce61154bee7484…` | ✅ |
| `QSVPipeline/rgy_input.h` | `9e07de4c2d93f08f8ccc10f2…` | `9e07de4c2d93f08f8ccc10f2…` | ✅ |

The single excluded difference is a blank line, which vanishes at compile time,
so the standalone patch compiles to the same code path as the tested binary. It
is **not** part of the patch file, and it is not counted as a behaviour change.

### 2.3 Tested binary

| Field | Value |
|---|---|
| Path | `third_party/QSVEnc/_build/x64/ReleaseStatic/QSVEncC64.exe` |
| sha256 | **`f5df83f12911d99d69b07e8270d6cdef4a8e4b62adb06711599c8cce62f8804d`** |
| Build | MSVC 14.51, `ReleaseStatic|x64` |
| Faithfulness check | the **unpatched** local build reproduces the shipped behaviour exactly: `--avsw 30, --avhw 27` on the same clip as the shipped 8.26 |

Instrumentation is gated behind the environment variable `RGY_AVHW_TRACE`, so the
binary is inert unless it is set — no debug output on normal runs.

### 2.4 Build inputs and deviations

Recipe: `qsvencc-root-cause.md` §1 and §10 (`work/qsvbuild/setup_build.ps1`,
`build.ps1`). Three local deviations, **none of which touches the code under
study**:

| Deviation | Reason | Reverts to upstream |
|---|---|---|
| `ENABLE_AVISYNTH_READER 0`, `ENABLE_VAPOURSYNTH_READER 0` | SDKs not installed; both readers are `#if`-guarded and unused on this path | yes (a two-line change in `QSVPipeline/rgy_version.h`) |
| `ENABLE_OPENVINO 0` | runtime not installed; backs AI VPP filters only | yes (same file) |
| `BuildVPL.bat` prebuild bypassed | hardcodes the VS2022 CMake generator; OneVPL built separately from the vendored `libvpl` submodule with Ninja | n/a (build-only) |

Two further build-only adjustments: the `ffmpeg_lgpl` package was reused from
the NVEncC experiment line, and a UTF-8 BOM was dropped from
`QSVPipeline/rgy_version.h` by the editor that inserted the comments above
(purely a file-encoding artefact of the local edit, not a code change).

**A fully faithful build was not produced.** §5 states the consequence.

### 2.5 Accidental / unrelated change check

```
git status --short   (branch content before close-out)
 ?? docs/hardware-decode/qsvencc-avhw-experiment.md
 ?? docs/hardware-decode/qsvencc-root-cause.md
 ?? third_party/
```

* **No production source file in the 1KeyTranscoder tree is modified** by this
  branch. Its only content is documentation.
* `third_party/` is a ~1.5 GB untracked upstream checkout plus SDK stubs used as
  build inputs. It is now covered by a `.gitignore` rule (added at close-out,
  with a comment stating why) so it cannot be committed by accident.
* The patch lives in gitignored `work/` by project convention. Its durable
  record is this document, `qsvencc-root-cause.md` §5 (which quotes the two
  edits), and `work/qsvbuild/poc_patch.py`.

---

## 3. Runtime test record

Raw records: `work/qsvavhw/ab/ab.json` (frame counts and refusal),
`work/qsvavhw/ab/fp_seek_trim.json` (fingerprint, seek, trim),
`work/qsvavhw/trace/*.trace.txt` (instrumented ledger),
`work/qsvavhw/matrix/full.matrix.json` (Phase 1 behaviour matrix, 13 clips).

### 3.1 Frame counts, shipped vs patched (A/B, 10 cases)

| Case | Container | `--avsw` | shipped `--avhw` | patched `--avhw` | Reader (patched) | rc | Verdict |
|---|---|---|---|---|---|---|---|
| Sony container copy | 30 | 30 | **27** | **30** | `avqsv` | 0 | restored |
| Sony C1170 4K60 | 30 | 30 | **27** | **30** | `avqsv` | 0 | restored |
| Sony C0886 4K60 360 f | 360 | 360 | **357** | **360** | `avqsv` | 0 | restored |
| Sony C1158 4K60 150 f | 150 | 150 | **147** | **150** | `avqsv` | 0 | restored |
| Sony H.264 4:2:2 10-bit | 195 | 195 | refusal | **refusal** | **none** | −31 | unchanged |
| Sony H.264 4:2:2 10-bit #2 | 195 | 195 | refusal | **refusal** | **none** | −31 | unchanged |
| DJI control 105 f | 105 | 105 | 105 | **105** | `avqsv` | 0 | unchanged |
| DJI control 297 f | 297 | 297 | 297 | **297** | `avqsv` | 0 | unchanged |
| x265 re-encode control | 30 | 30 | 30 | **30** | `avqsv` | 0 | unchanged |
| synthetic `testsrc2` control | 60 | 60 | 60 | **60** | `avqsv` | 0 | unchanged |

**Reader identity stays `avqsv` on every patched run** — the fix does not
silently route to software. `--avsw` counts are unchanged in every row.

### 3.2 Exactness — per-frame identity, not counts

| Comparison | Frames | Identical |
|---|---|---|
| **patched `--avhw` vs `--avsw`** | 30 vs 30 | **true** (`test0_matches_ref_index = 0`) |
| shipped `--avhw` vs `--avsw` | 27 vs 30 | false — `test[0]` maps to `ref[3]` |

`encoded` frame counts for the same three runs: 30 (patched `--avhw`), 27
(shipped `--avhw`), 30 (`--avsw`). No PTS rewriting is introduced: the patch only
stops discarding outputs, and `PipelineTaskCheckPTS` — the stage that rewrites
timestamps — is untouched.

At frame-hash level the Phase 1 investigation had already established that the
shipped loss is a **strict contiguous head truncation** (`--avhw[i] ==
--avsw[i+3]`, extra = ∅, tail identical), i.e. not a flush loss, not a reorder,
not a PTS error.

### 3.3 Seek regression — 3/3 unchanged

The added gate exists to protect `--seek`, so this is the critical regression.

| `--seek` | shipped `--avhw` | patched `--avhw` | same |
|---|---|---|---|
| 1.0 s | 237 | 237 | ✅ |
| 2.5 s | 177 | 177 | ✅ |
| 4.0 s | 57 | 57 | ✅ |

Pictures before a requested seek position are still dropped exactly as before.

### 3.4 Trim regression — unchanged

| `--trim` | `--avsw` | shipped `--avhw` | patched `--avhw` |
|---|---|---|---|
| 0:29 | 27 | 27 | 27 |
| 3:29 | 27 | 27 | 27 |
| 10:20 | 11 | 11 | 11 |

(`--trim` on its own drops the leading pictures in *both* readers. That is a
separate pre-existing behaviour, identical before and after the patch, and out of
scope here.)

### 3.5 Capability refusal — still a refusal, still not a frame drop

H.264 High 4:2:2 10-bit remains a **pre-flight capability refusal**: `avqsv:
codec h264(yuv422p10le) unable to decode by qsv.`, no reader constructed, no
output file, `rc = −31`. The patch cannot and does not turn a refusal into a
decode. This is the distinction the investigation exists to preserve:
**`unsupported` is not `frame drop`** — and the quiet failure (3 frames lost,
exit 0) is the more dangerous one.

### 3.6 GPU pipeline preserved

The patch removes *rejections*; it adds no download. Decode surfaces still flow
`MFXDEC → MFXVPP` under `--avhw`, the debug log still shows `avqsv`, and the
encoder still consumes video-memory surfaces. No CPU round-trip is introduced.
The allocation chain is the evidence: `--avhw` starts at `MFXDEC-MFXVPP` while
`--avsw` starts at `INPUT-MFXVPP` and needs `staging textures`.

---

## 4. Known limitations and caveats

| # | Limitation | Impact |
|---|---|---|
| Q1 | **Version scope.** Only QSVEncC **8.26** at `b14c965` was built, patched and measured. | **Not a general claim for later releases.** Releases **8.27–8.30 exist and were not examined**; `qsv_pipeline_ctrl.h` may have changed. Anyone adopting this patch on another revision must re-locate the condition and re-run §3. |
| Q2 | **`--seek 0` is indistinguishable from no seek.** The guard predicate is `first > 0.0f`, so `--seek 0` leaves the filter disabled. | Same convention as the NVEncC patch. **Not observable in any validation performed here** (no test requested an explicit `--seek 0`), and therefore recorded as an unhandled edge case rather than a measured one. |
| Q3 | **Single machine.** Intel Arc 140T, driver `32.0.101.8974`, QuickSyncVideo API v2.17. | Decoder-driver interaction is not portable evidence; the *rule* is in rigaya's code, but re-validate on other Intel hardware. |
| Q4 | **Build not fully faithful.** Three documented deviations (§2.4) plus a dropped BOM. The **unpatched** build reproduces shipped behaviour exactly (`30 / 27`), which is the faithfulness check obtained. | A byte-faithful build was not produced. |
| Q5 | **PoC scope.** No long-run or multi-stream soak was performed, and `--frames` semantics were not characterised for QSVEncC the way they were for NVEncC. | Do not infer long-run stability from this record. |
| Q6 | **`frame count == container` is not on its own a correctness test.** The load-bearing evidence is the per-frame SHA-256 identity in §3.2. | Any future re-validation must repeat the fingerprint comparison, not just count frames. |
| Q7 | **`FramePosList::setPocAndFix` is a separate latent defect** (also present in NVEncC). It removes the same three pictures from the frame-position **table** in *both* readers, so it does not cause the `--avhw`-only loss. It is **not** patched here and must not be bundled. | Fixing it alone would not have changed the `--avhw` frame count. See `qsvencc-root-cause.md` §7 and `docs/hardware-decode/research-conclusion.md`. |
| Q8 | **Measurement traps** recorded in `qsvencc-avhw-experiment.md` §11 (Y4M instead of rawvideo, aspect-ratio-preserving `--output-res`, `--log-level error` hiding the reader identity, `dxvadec` not meaning "GPU decoded", …). | These produced wrong numbers before being caught. Anyone re-running must read that list first. |

---

## 5. Status

```text
ROOT CAUSE:  Confirmed      (instrumented, exact file / class / function / condition)
PATCH:       Runtime Proven (2-edit PoC, built from the pinned 8.26 revision, A/B measured)

BRANCH STATUS: READY TO MERGE / READY TO ARCHIVE

  baseline        QSVEncC 8.26 (r4504), rigaya/QSVEnc b14c9652b34b998351516cc63c86eb990c01c7c8
  scope           15 added lines, 1 line replaced, 2 files
  patch sha256    5621ebb0f0277743a3bb5f18176134a134857d72e8a67f7f452c63dd08e53d98
  applies         clean (strict git apply, exit 0, no fuzz) to a freshly cloned
                  pristine baseline -- verified at branch close-out
  provenance      with instrumentation-only lines and one stray blank line
                  excluded, the patched files are byte-identical to the built
                  tree's (f46a106a... / 9e07de4c...)
  binary          f5df83f12911d99d69b07e8270d6cdef4a8e4b62adb06711599c8cce62f8804d
  correctness     Sony n-3 -> n (4/4 cases); patched --avhw per-frame identical
                  to --avsw (30/30); reader still avqsv on every run
  regression      --seek 3/3 unchanged; --trim 3/3 unchanged; controls 4/4
                  unchanged; H.264 4:2:2 refusal unchanged (rc -31)
  pipeline        MFXDEC -> MFXVPP preserved; no host copy introduced

  ★ CLAIM BOUNDARY (state this verbatim with any citation):
      Runtime-proven on QSVEncC 8.26 pinned revision;
      not yet a general claim for later releases.

  open before adoption
    - re-locate the condition and re-validate on any revision other than b14c965
    - decide the --seek 0 edge case (Q2)
    - long-run / multi-stream soak (Q5)
    - keep FramePosList::setPocAndFix out of this patch (Q7)

  This is a READY-to-integrate PATCH, not a READY-to-ship binary. The build is a
  research build (avs/vpy readers and OpenVINO disabled, OneVPL built separately)
  and must not be distributed.
```

Cross-branch conclusion and the integration prerequisites that apply to all four
research branches: `docs/hardware-decode/research-conclusion.md`.
