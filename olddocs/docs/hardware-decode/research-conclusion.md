# Hardware Decode Research Conclusion

**Cross-branch close-out of the P0-A hardware-decode research line.**

| | |
|---|---|
| Branches concluded | `research/rigaya-avhw-source` · `research/rigaya-nvencc-avhw` · `research/rigaya-qsvencc-avhw` · `research/hwdecode-e2e-benchmark` |
| Base | `main` @ `15cf218` (all four branches) |
| Documented on | `research/hwdecode-e2e-benchmark` (this file) |
| `main` | **not modified.** No production transcoder logic, no default-backend behaviour, no hardware-decode default. |
| Production integration | **not started.** It is the next session's scope (`feature/hardware-decode-integration`). |

> **How to read this document.** It reports only what the four branches measured.
> Every row that could be over-read carries its measurement boundary. Where a
> statement is an inference rather than a measurement, it says so. Where nothing
> was tested, it says **untested** rather than "expected to work".
>
> **The single most important correction to the project's prior position:**
> the old conclusion *"rigaya is unusable, only FFmpeg works"* is **wrong**.
> The correct statement is that **the shipped rigaya binaries carry
> Sony-specific hardware-decode pipeline bugs, those bugs are now located and
> fixed at source, and the fixes are runtime-proven** — with FFmpeg retained as
> the independent correctness path.

---

## 1. Current verified state

Every cell below is backed by a measurement recorded on one of the four
branches. **The table covers only the corpus actually exercised**: Sony XAVC HS
(HEVC Main10 4:2:0) and Sony XAVC S (H.264 High 4:2:2 10-bit) in MP4 and MKV,
plus DJI HEVC Main10 4:2:0 controls. It is **not** a claim about "all Sony" or
"all DJI" material — see §5.

| Backend | Sony | DJI | Status |
|---|---|---|---|
| **NVEncC 9.31 stock `--avhw`** | ❌ **N−3** (HEVC) / **N−2** (H.264 4:2:2) | ✅ N | **patched required** |
| **NVEncC 9.31 patched `--avhw`** | ✅ **N** | ✅ N | **integration candidate** |
| **QSVEncC 8.26 stock `--avhw`** | ❌ **N−3** (HEVC) / capability refusal (H.264 4:2:2) | ✅ N | **patched required** |
| **QSVEncC 8.26 patched `--avhw`** | ✅ **N** | ✅ N | **integration candidate** (version-scoped, §3.3) |
| **FFmpeg decode** (`-hwaccel cuda` / `qsv`) | ✅ N | ✅ N | **reference / fallback** |

Notes that belong with that table:

* **NVEncC patched** was validated on 8 cases × 2 readers with 13 assertions
  (`failures: []`), a 17/17 seek+trim matrix, and long runs to the full
  10 170-frame clip; on every leading-picture case the patched `--avhw` output is
  **byte-identical** to `--avsw`.
* **QSVEncC patched** was validated on 10 A/B cases, per-frame SHA-256 identity
  with `--avsw` (30/30), a 3/3 seek and 3/3 trim regression, and unchanged
  controls. **H.264 4:2:2 10-bit remains a capability refusal** on QSV — no
  reader, no output, `rc = −31` — and the patch does not change that.
* **FFmpeg** was frame-exact on the whole Sony corpus (NVDEC 151/151, QSV
  146/146 bit-identical to software decode; the remaining 5 are loud capability
  refusals, not frame loss). It is the independent evidence that made the patched
  rigaya result credible, and it stays.
* **DJI** was frame-exact on stock *and* patched rigaya `--avhw`, on both
  rigaya tools and on FFmpeg — zero leading pictures, so the defect has nothing
  to discard. **Container-side predictors were not used as evidence here**: DJI's
  status rests on measured delivered frame counts and per-frame signatures, not
  on an `mp4` box analysis.

---

## 2. Root causes

Three separate defects were found. They are independent: different files,
different layers, different blast radii, and only the first two are patched.

### RC-1 — NVEncC: output-stage filtering (patched)

| | |
|---|---|
| Site | `NVEncCore/NVEncPipeline.h`, `PipelineTaskNVDecode::getOutputFrame()` |
| Upstream | `rigaya/NVEnc` tag `9.31`, commit `2cb9d810c045202548b98ff130b12bc764eb39ea` |
| Defect | `m_hwDecFirstPts` is the PTS of the first packet **fed to the decoder** (in decode order, the first IRAP). The output loop treats it as the first PTS that should be **presented** and releases every buffered picture below it, so the emission loop never emits them. |
| Why Sony | XAVC codes 3 (HEVC) / 2 (H.264 4:2:2) pictures *before* its first IRAP in presentation order. Those pictures have lower PTS than the IRAP, so they are exactly what gets discarded. |
| Patch | 18 added lines, 2 files, 0 deletions. The filter now runs only when a seek was actually requested. |
| Patch sha256 | `53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc` |
| Evidence | decoder callback log shows every picture is produced (30/30, 195/195, 360/360, 105/105); the downstream `CheckPTS` stage's first frame is the IRAP; the patch restores N with output byte-identical to `--avsw` |
| Status | **RUNTIME-PROVEN — integration candidate.** Provenance: `docs/hardware-decode/nvencc-patch.md` on `research/rigaya-nvencc-avhw`. |

### RC-2 — QSVEncC: pipeline admission filtering (patched)

| | |
|---|---|
| Site | `QSVPipeline/qsv_pipeline_ctrl.h`, `PipelineTaskMFXDecode::sendBitstream()` |
| Upstream | `rigaya/QSVEnc` commit `b14c9652b34b998351516cc63c86eb990c01c7c8` (= 8.26) |
| Defect | Each MFX output whose presentation timestamp precedes `m_firstPts` — the PTS of the first fed packet — is rejected, and `m_decFrameOutCount` cannot advance while rejections are ongoing, so **all** such pictures are rejected, not one. |
| Why Sony | Same stream shape as RC-1. MFX decoded all 30 pictures; the pipeline discarded the 3 leading ones. |
| Patch | 15 added lines, 1 line replaced, 2 files. The rejection is gated on a requested seek. |
| Patch sha256 | `5621ebb0f0277743a3bb5f18176134a134857d72e8a67f7f452c63dd08e53d98` |
| Evidence | instrumented from-source build: `container 30 → packets 30 → MFX outputs 30 → accepted 27 → encoded 27`, with accept/reject traced per frame |
| Status | **RUNTIME-PROVEN on the pinned 8.26 revision only** — see §3.3. Provenance: `docs/hardware-decode/qsvencc-patch.md` on `research/rigaya-qsvencc-avhw`. |
| Relationship to RC-1 | **Same rule, different code.** The two pipeline headers are vendor-specific and the fixes are **not** interchangeable. Only the shared *reader* (`rgy_input_avcodec.cpp/.h`) is byte-identical between the two tools — and the reader is **not** the defect. |

### RC-3 — `FramePosList::setPocAndFix`: an independent metadata-table issue (**not patched, and must stay separate**)

| | |
|---|---|
| Site | `rgy_input_avcodec.h`, `FramePosList::setPocAndFix()` (the file is shared by NVEncC, QSVEncC and VCEEncC) |
| Defect | The reader's frame-position **table** prunes entries whose PTS precedes `m_firstKeyframePts` (bounded by `<= 16` for PTS wraparound). On Sony material it removes exactly the leading pictures — 3 entries for XAVC HS, 2 for H.264 4:2:2, 0 on every control. |
| Effect | It degrades three things — the original-duration lookup for those frames, telecine flags, and the reader's own `N frames, End of file` / `--log-framelist` self-report — but it **changes no delivered frame**. |
| Why it is not the frame-loss cause | It fires **identically** on `--avsw` and `--avhw` (`--log-framelist` byte-identical on every fixture; `trim=3` on both), and `--avsw` delivers every frame. It is a *table* defect, not a delivery defect. |
| Action | **No code change.** Regression-test the patch without it, and report it **upstream as a separate, low-severity issue**: "the reader's frame-position table silently drops open-GOP leading pictures, so `N frames, End of file` and `--log-framelist` under-report on XAVC." |
| Consumer-side mitigation (already recommended, no third-party patch needed) | Never treat the reader's self-report as a frame count. Reconcile from the delivered container. |

**Explicitly not claimed:** that `setPocAndFix` caused the observed frame loss
(refuted by measurement); that all rigaya versions or all rigaya tools carry the
same bug (only two pinned revisions were examined); that all codecs, containers
or Sony material are affected (the measured corpus is §5).

---

## 3. Engineering recommendation

### 3.1 For the integration session

1. **Use the patched rigaya path for hardware decode.** The patched route is
   correct (byte-identical to software decode) and cheap (2.5–2.7× less CPU per
   frame). Stock rigaya `--avhw` must **not** be used on Sony material.
2. **Keep the software decode fallback.** Unchanged, loud, never a silent
   switch. Capability refusal, init failure, count mismatch and sequence
   mismatch must each be distinguishable in a log.
3. **Retain FFmpeg as an independent correctness / reference / fallback path.**
   It is a genuinely independent implementation, frame-exact corpus-wide, and it
   is what made the patched reader's result credible.
4. **Do not use CPU raw-pipe FFmpeg as the default architecture.** NVDEC → 
   `hwdownload` → format conversion → y4m → re-upload is frame-exact but gives
   back **~80 % of the CPU saving** and costs ~6 % wall clock. It forfeits the
   only benefit hardware decode has.
5. **Do not make hardware decode the default** until integration validation
   completes. Nothing in this research line changed a default.
6. **Reconcile frame counts against the delivered container**, never against a
   decoder's or reader's self-report: container samples · reader estimate ·
   `encoded N frames` · independent count of the delivered file.
7. **Validate with picture identity, not counts.** A count check alone passed a
   truncated stream on this project; per-frame signatures and output hashes are
   what caught it.

### 3.2 The value proposition, stated honestly

> **Hardware decode's main benefit is CPU headroom, not a guaranteed single-job
> speed-up.**

| | |
|---|---|
| **Observed, reproducible** | **2.5–2.7× less CPU per frame** (Sony 95.8 → 37.1, DJI 98.4 → 35.9 CPU-s/1000 frames; 2–3 % spread over repeats). The patch adds no measurable CPU of its own. |
| **Observed, negative** | Single-job throughput does **not** improve — the same-binary pairing is **~11 % slower** (140.9 s `--avsw` vs 158.4 s patched `--avhw`). |
| **Observed, weak** | 2-way aggregate throughput is higher (Sony +20 %, DJI +4 %) but **within this host's run-to-run drift**; the defensible claim is "at least as fast, with half the CPU". |
| **Inferred, not proved** | The pipeline is **NVENC-bound** (VE mean 80–84 %, VD mean ~5 %; 2-way `--avhw` uses only 6–7 % of the machine and still reaches only ~15–20 fps aggregate). Strong and consistent, but not isolated by a controlled encoder-capacity experiment. |
| **Measured limit** | **4 concurrent 4K jobs collapse to 8.01 fps aggregate** on this 8 GB laptop GPU — below the 1-way 22.7 fps. Frame-exact, `rc=0`, so it is a throughput result, not a crash. |
| **Inferred, not proved** | The 4-way collapse's mechanism. VRAM exhaustion and NVENC session contention both fit; that run's VRAM sampling failed, so **no VRAM telemetry exists for it**. Do not quote either as the confirmed root cause. |

### 3.3 Claim boundaries — carry these with any citation

| Claim | Boundary that must accompany it |
|---|---|
| QSVEncC patch | **Runtime-proven on QSVEncC 8.26 pinned revision; not yet a general claim for later releases.** Releases 8.27–8.30 exist and were not examined. |
| NVEncC patch | Validated on NVEncC 9.31 (`2cb9d81`) only. The patch is small and re-applies to a 9.31-lineage tree, but any other revision must be re-located and re-validated. |
| Corpus coverage | Sony XAVC HS (HEVC Main10 4:2:0) and XAVC S (H.264 High 4:2:2 10-bit), MP4 and MKV, plus DJI HEVC and x265/synthetic controls. **No All-I, no 8-bit, no 1080p material exists in this environment.** |
| Performance | Single-host, single-GPU (NVIDIA RTX 5070 Laptop 8 GB; Intel Arc 140T for QSV), laptop, with documented host drift. Absolute fps across sessions is not comparable. |
| Builds | Both patched binaries are **research builds** (readers/SDKs disabled, generated CUDA MSBuild shim for NVEncC, OneVPL built separately for QSVEncC) and must **not** be distributed. The NVEncC baseline also uses a different toolchain from the shipped binary, so cross-binary timing is not a controlled measurement. |
| "Fixed" | Means **fixed at source with a tracked patch file**, not fixed in any shipped rigaya release. Upstreaming has not happened. |

---

## 4. Integration prerequisites

**A. Patch-level (per tool)**

1. Re-run the correctness suite after **any** rebase (NVEncC: `run_regression.py`
   + `check_regression.py`, `run_seektrim.py`, `run_longrun.py`; QSVEncC:
   `ab_validate.ps1`, `ab_fingerprint.ps1`).
2. Confirm the patch still applies cleanly to the pinned revision and reproduces
   the built sources (both branches record the exact procedure).
3. Re-locate the condition on any revision other than the pinned one before
   claiming the fix — QSVEncC has four unreleased-to-us later versions.

**B. Corpus-level**

4. Run the full **151-file Sony corpus + DJI** with four-way count
   reconciliation. After the patch the invariant becomes the simple
   `avhw == container`; it has not been checked corpus-wide.
5. Include per-frame picture identity, not only counts.

**C. Environment-level**

6. Re-test on a **multi-GPU host** before enabling device selection (all
   validation here was single-GPU).
7. Re-test **parallel / `--split-enc`** if the integration uses it (argued from
   source only, never measured).
8. Watch for the one non-reproducible `0xC0000005` recorded on a 10 170-frame
   NVEncC run (1 in 6 attempts, never reproduced; every completed run was
   frame-exact).

**D. Product-level**

9. Distinguish capability refusal from frame loss **everywhere** in logs and
   metrics. On this machine they coexist in the same tool, on adjacent files:
   QSV refuses H.264 4:2:2 10-bit loudly (`rc = −31`, no output) while silently
   losing 3 frames on every supported Sony file. **`unsupported` is not
   `frame drop`.**
10. Keep the swap invisible in behaviour: a hardware path that produces a
    different frame count than the software path must fail loudly, never
    silently succeed.

---

## 5. Open items

Listed as work for later sessions. **None of these was executed here.**

| # | Open item | Why it is open | Suggested owner session |
|---|---|---|---|
| 1 | **QSVEncC later-version compatibility** (8.27–8.30) | Only 8.26 (`b14c965`) was examined. `qsv_pipeline_ctrl.h` may have changed. | integration / follow-up |
| 2 | **`--frames N` leading-picture semantics** | On Sony, `--frames N` delivers `N − leading_pictures` on **both** readers and both binaries — pre-existing, identical before and after the patches. The same shortfall appears with `--trim`. | integration (must compensate or drive by container count) |
| 3 | **`--seek 0` semantics** | Both patches use the predicate `seek > 0`, so an explicit `--seek 0` is indistinguishable from no seek. Not observable in any validation performed. | follow-up |
| 4 | **`AV_PKT_FLAG_DISCARD` behaviour change** | A patched NVEncC `--avhw` no longer honours the container flag the way `--avsw` and FFmpeg do (synthetic fixtures E/F go 27 → 30). On the measured corpus those are real pictures and recovering them is correct — but it is a behaviour change and belongs in the adoption decision. | integration decision |
| 5 | **Multi-GPU / multi-encoder validation** | Single-GPU host throughout; `--device 1` fails with `Invalid Device Id = 1`. Also untested: parallel / `--split-enc` encode. | follow-up |
| 6 | **Broader codec / profile matrix** | No All-I, no 8-bit, no 1080p, no VFR material in the environment. The mechanism *predicts* that any stream whose first IRAP is not first in presentation order is affected, and no measurement contradicts it — but that is an inference, not a measurement. | follow-up |
| 7 | **4-way GPU-memory / concurrency behaviour** | The 4-way 4K collapse is measured; its **mechanism is not** (VRAM sampling failed on that run). Needs working per-pid VRAM telemetry, and 1080p material to re-evaluate the concurrency ceiling. | follow-up |
| 8 | **Hardware-decode default decision** | Deliberately deferred. Requires integration validation first. | **after** `feature/hardware-decode-integration` |
| 9 | **`FramePosList::setPocAndFix` upstream report** | Independent metadata-table defect; no delivered-frame benefit to fixing it here, and the file is shared by three encoders. Report upstream with the open-GOP/TS samples the original comment cites. | follow-up (report only) |
| 10 | **Upstreaming both pipeline patches to rigaya** | Neither patch has been offered upstream. Upstreaming is cheaper and more durable than carrying a fork, on a code path where a rebase mistake is a silent frame loss. | follow-up |
| 11 | **H.264 4:2:2 10-bit QSV capability** | Refusal confirmed on this machine and three other Intel platforms on record; whether the limit is silicon or the VPL/MSDK stack is **not separable** with this evidence. Cross-checked, not resolved. | follow-up |
| 12 | **QSV long-run / multi-stream soak** | Not performed. NVEncC has a long-run sweep; QSVEncC does not. | follow-up |

---

## 6. Branch-level result and archive index

| # | Branch | Deliverable | Status |
|---|---|---|---|
| A | `research/rigaya-avhw-source` | Source archaeology: drop-site attribution, reader exoneration, `setPocAndFix` separation, predictor derived from source | READY TO ARCHIVE |
| B | `research/rigaya-nvencc-avhw` | NVEncC patch + runtime/regression proof + provenance record | READY TO MERGE / integration source reference |
| C | `research/rigaya-qsvencc-avhw` | QSVEncC root cause + runtime-proven patch + provenance record (version-scoped) | READY TO MERGE / integration source reference |
| D | `research/hwdecode-e2e-benchmark` | End-to-end benchmark, observed/inferred separation, integration guidance, and this document | READY TO ARCHIVE |

Per-branch provenance, in the order an integrator needs it:

1. `docs/hardware-decode/rigaya-avhw-analysis.md` — why the drop happens (branch A)
2. `docs/hardware-decode/nvencc-patch.md` — the NVEncC patch, proven (branch B)
3. `docs/hardware-decode/qsvencc-patch.md` — the QSVEncC patch, proven and version-scoped (branch C)
4. `docs/hardware-decode/e2e-benchmark.md` — what the fixed path costs and buys (branch D)
5. this document — the single place to read before starting integration

---

## 7. What this research line did **not** do

Stated so the boundary of the result is unambiguous:

* **No production code was modified** on any of the four branches, and `main`
  was untouched.
* **No default was changed.** Hardware decode remains non-default.
* **No hardware decode was wired into the transcoder**, and no integration
  branch was started.
* **No shipped binary was produced.** Both patched binaries are research builds
  and must not be distributed.
* **`FramePosList::setPocAndFix` was not merged, patched or bundled** with
  either fix.
* **No new experiment was run during close-out** beyond re-verifying that both
  patch files apply cleanly to freshly cloned pristine upstream revisions and
  reproduce the built sources.
* **No claim is made** for rigaya revisions other than the two pinned ones, for
  codecs/profiles outside the measured corpus, for multi-GPU hosts, or for
  parallel-encode modes.

Next session: `feature/hardware-decode-integration`.
