# NVEncC `setPocAndFix` — Second-Path Analysis (Hardware Decode Phase 2, follow-up)

`research/rigaya-avhw-source` · base `main` @ `15cf218`

> **Question.** `NVEncCore/rgy_input_avcodec.h:654` `FramePosList::setPocAndFix()`
> also has a rule that removes entries whose PTS precedes the first keyframe.
> Is it a second exit for the same defect that was fixed in
> `PipelineTaskNVDecode::getOutputFrame()`, or something else — and does the
> NVEncC patch need to change it too?
>
> **Answer, up front.** It is **a separate defect in a different layer that does
> not affect the delivered frame count on any measured configuration.** It is
> not a second exit, and it must **not** be included in the patch.
> Recommended action: **`SEPARATE_FIX`** (report it; do not ship it).
>
> No production code was modified in this phase. Everything below is measured
> on real binaries, real media and the reader's own logs.

---

## 0. Verdict summary

| # | Question | Verdict | Basis |
|---|---|---|---|
| 1 | What is `setPocAndFix` for? | **`Confirmed`** — it finalises the reader's frame-position table (`FramePosList`): sorts a stable window by PTS, then assigns `poc` + `duration` and prunes pre-keyframe entries | source read + `--log-framelist` reproduction |
| 2 | Which call paths execute it? | **`Confirmed`** — `add()` (line 327), `checkPtsStatus()` (507), `fin()` (bypasses it, lines 379-384) | source read |
| 3 | Why does fixture E/F still crop leading frames? | **`Rejected` as stated** — `setPocAndFix` is *not* the cause. E/F are cropped by the **container's `AV_PKT_FLAG_DISCARD`** flag, honoured in `getSample()` | packet flags + `flags=4` in `--log-packets` + controlled A/B/E comparison |
| 4 | Does it affect real Sony XAVC? | **`Confirmed` for the table** (3 entries pruned on every XAVC HS file, 2 on H.264 4:2:2) — **`Rejected` for the delivered output** (0 frames lost) | framelist dumps, 3 binaries × 11 fixtures |
| 5 | Same problem / independent bug / seek-only / synthetic-only? | **Independent bug in a different layer** — a metadata-table defect, not a frame-delivery defect. Not seek-only, not synthetic-only | §4, §5 |
| 6 | Do `--avsw` and `--avhw` share the logic? | **`Confirmed` yes, identically** | `--log-framelist` byte-identical for both; `trim=3` in both; same reader object |
| 7 | Should the patch change it? | **`NO_ACTION`** (recommend `SEPARATE_FIX` as an upstream report only) | §8 |

---

## 1. What `setPocAndFix` actually does

`FramePosList` is the reader's own table of the frames it read, one row per
picture: `pts`, `dts`, `duration`, `duration2`, `flags`, `repeat_pict`,
`pic_struct`, and a derived `poc`. It is a **lookup table**, not the frame
queue. `PipelineTaskCheckPTS` consults it through `findpts(pts)` to recover a
frame's *original* duration; `LoadNextFrameInternal` consults it through
`findpts()` for telecine flags.

```cpp
// NVEncCore/rgy_input_avcodec.h:654-681
void setPocAndFix(int nSortedSize) {
    // ソートによりptsが確定している範囲
    int nSortFixedSize = nSortedSize - (int)AV_FRAME_MAX_REORDER - 1;
    m_nextFixNumIndex += m_PAFFRewind;
    for (; m_nextFixNumIndex < nSortFixedSize; m_nextFixNumIndex++) {
        if (m_list[m_nextFixNumIndex].data.pts < m_firstKeyframePts // opengop
            && m_nextFixNumIndex <= 16) { // wrap arroundの場合は除く
            //これはフレームリストから取り除く
            m_list.pop();
            m_nextFixNumIndex--;
            nSortFixedSize--;
        } else {
            adjustDurationAfterSort(m_nextFixNumIndex);
            setPoc(m_nextFixNumIndex);
        }
    }
    m_PAFFRewind = 0;
    ...
}
```

Its design purpose, in three parts:

1. **Finalise the analysed window.** `sortPts()` has just sorted
   `m_list[m_nextFixNumIndex .. end)` by PTS. A frame's PTS is only trustworthy
   once enough *later* frames have arrived to be sure no earlier-sorting frame
   is still in flight, so the code only "fixes" up to
   `nSortedSize - AV_FRAME_MAX_REORDER - 1` (`AV_FRAME_MAX_REORDER` = 16;
   `-1` more so that the *next* frame's PTS is available to compute
   `duration` as a difference — see `adjustDurationAfterSort`).
2. **Assign `poc` (presentation order) and `duration`.** `setPoc()` walks the
   sorted list handing out monotonically increasing `poc` values;
   `adjustDurationAfterSort()` sets `duration = pts[i+1] - pts[i]`. This is
   what makes `findpts()` and `copy(poc)` work at all.
3. **Drop entries that precede the first keyframe.** `m_firstKeyframePts` is
   captured in `add()`:

   ```cpp
   // NVEncCore/rgy_input_avcodec.h:320-322
   if (m_firstKeyframePts == AV_NOPTS_VALUE && (pos.flags & AV_PKT_FLAG_KEY) && nIndex == 0) {
       m_firstKeyframePts = m_list[nIndex].data.pts;
   }
   ```

   Any table entry with `pts < m_firstKeyframePts` **and** index ≤ 16 is
   removed with `m_list.pop()`.

**The `<= 16` clause is the design tell.** The intent is: *an open-GOP stream
can begin with a few pictures that sort before the first keyframe; those
belong to the previous GOP and are not part of this segment, so remove them.*
The index bound exists because on a PTS-wraparound (long TS) stream the sort
can place unrelated high-PTS entries at the front, and those must not be
deleted. It is a bounded heuristic for a bounded artefact — exactly the same
*mistaken equivalence* as the `getOutputFrame()` bug ("PTS of the first
keyframe" = "start of presentation"), applied to a **table** instead of to the
**output**.

One implementation detail matters for what follows: `m_list` is
`RGYQueueMPMP<FramePos, 1>`, whose `pop()` removes the **front** element and
**refuses when `size() <= m_nKeepLength` (=1)** (`rgy_queue.h:320-340`). The
caller ignores the return value. The loop can therefore terminate by having
`pop()` silently refuse, which is why the deletion count is not simply "the
loop iteration count".

## 2. Which call paths execute it

| Call site | Trigger | Role |
|---|---|---|
| `add()` → line 327 (`rgy_input_avcodec.h:324-328`) | every packet, once `m_inputFin` **or** `listSize - nextFixNumIndex > 16` | incremental; the only path that runs during normal streaming |
| `checkPtsStatus()` → line 507 | end of the pre-analysis read (`rgy_input_avcodec.cpp:905`, `:2400`) | bulk fix of the analysed window |
| `fin()` → lines 379-384 | end of input | **does not call it**; instead runs its own `setPoc()` loop over the whole list |

`setPocAndFix` therefore runs during **both** the pre-analysis phase and
steady-state reading, for **both** readers. It is reached from
`RGYInputAvcodec`, which is the demuxer for `--avsw` **and** `--avhw` alike
(`rgy_input.cpp:629-692` constructs `RGYInputAvcodec` for
`RGY_INPUT_FMT_AVHW` *and* `RGY_INPUT_FMT_AVSW`).

## 3. Instrumentation

Three binaries were used, all 9.31:

| Binary | Description |
|---|---|
| **`base`** | shipped `tools\NVEncC_9.31_x64\NVEncC64.exe` (unpatched) |
| **`theirs`** | prior session's build (`research/rigaya-nvencc-avhw`): early-`break` guard, drop filter kept only when `--seek` is active |
| **`mine`** | this session's build: unconditional removal of the discard loop in `getOutputFrame()` |

Fixtures: the three real Sony clips (`C1170` 30 f, `C1083` 330 f, `C0886`
360 f, `C9037` H.264 4:2:2 195 f), fixtures A–F from the prior session, and
the DJI control.

Instruments, per measurement:

* `in` — container samples (`ffprobe -count_frames` on the source)
* `reader frames` — the reader's own `"N frames, End of file"` self-report
* **`setPocAndFix` before/after** — `--log-framelist` row count and PTS list,
  which *is* the post-`setPocAndFix` table; compared against the same table
  with the prune disabled in a model (§6)
* `decoder frames` — NVEncC `--log-level trace` `DecPictureDisplay` /
  `input frame (dev)` counts
* `output frames` — the delivered file (`ffprobe -count_frames`)
* `first output PTS`, per-frame fingerprint sequence — packet PTS list and
  SHA-256 of the delivered file

## 4. Does it affect real Sony XAVC? The table yes, the output no

### 4.1 `setPocAndFix` does prune every Sony file

`--log-framelist` dumps `FramePosList` after decoding. For `20260903_C1170.MP4`:

```
     poc, T,flags,repeat,  pts,         dts,duration,duration2,pic_struct
       0, I, 1, 0,        3003,        -2002,   1001,      0, progressive
       1, B, 0, 0,        4004,         4004,   1001,      0, progressive
       ...
      26, B, 0, 0,       29029,        26026,   1001,      0, progressive
      27, X, 0, 0,       29029,        29029,      0,      0, progressive
      -1, X, 0, 0,       29029,        29029,      0,      0, progressive
```

The container has **30** samples. The ledgers that matter:

| | value |
|---|---|
| samples in container | **30** |
| real rows in the frame-position table | **27** |
| PTS `0, 1001, 2002` present in the table? | **absent** |
| delivered frames (`--avsw`) | **30** |
| delivered frames (`--avhw`, `mine`) | **30** |
| delivered frames (`--avhw`, `base`) | 27 |

**The leading pictures are gone from the table and still delivered.** Both
readers produce this identical table:

```
20260903_C1170.MP4   avsw fpRows=29 (27 real + 2 sentinels)  trim=3
20260903_C1170.MP4   avhw fpRows=29 (27 real + 2 sentinels)  trim=3
identical content: True
```

`trim=3` is the `adjust trim by offset 3` debug line, which is
`m_trimParam.offset` — incremented once per leading picture at
`rgy_input_avcodec.cpp:3438`. It fires **3× for XAVC HS and 2× for H.264
4:2:2**, on **both** readers, on every Sony file. So `setPocAndFix` prunes
Sony's leading pictures on the real corpus — and the output count is
unaffected.

### 4.2 Full matrix (`base`, delivered frames)

| fixture | samples | disc. | leading | `--avsw` | `--avhw` | reader self-report | fpRows |
|---|---|---|---|---|---|---|---|
| C1170 XAVC HS 30 f | 30 | 0 | 3 | **30** | **27** | 28 | 29 |
| C1083 XAVC HS 330 f | 330 | 0 | 3 | **330** | **327** | — | 328 |
| C0886 XAVC HS 360 f | 360 | 0 | 3 | **360** | **357** | — | 358 |
| C9037 H.264 4:2:2 195 f | 195 | 0 | 2 | **195** | **193** | 193 | 194 |
| A sony copy → mp4 | 30 | 0 | 3 | 30 | 27 | 28 | 29 |
| B sony copy → mkv | 30 | 0 | 3 | 30 | 27 | 28 | 30 |
| C x265 re-encode | 30 | 0 | 0 | 30 | 30 | 31 | 32 |
| D synthetic | 60 | 0 | 0 | 60 | 60 | — | 61 |
| E lead-removed | 30 | **3** | 3 | **27** | **27** | 28 | 29 |
| F two-sony concat | 360 | **3** | 3 | **357** | **357** | — | 358 |
| DJI control | 105 | 0 | 0 | 105 | 105 | — | 106 |

Reading it:

* On **every** Sony file the frame-position table is short by exactly the
  leading-picture count — and `--avsw` delivers the full sample count anyway.
  The prune is real and harmless.
* `fpRows` is `samples − 1` for MP4, `samples − 2` for C1083, and
  `samples + 1/2` for MKV/x265/DJI — i.e. the sentinel rows
  (`fin()` appends one, `poc = -1`) plus the prune account for the offset. The
  table is a lookup structure with its own conventions, not a frame counter.
* E and F are the only fixtures where `--avsw` also loses frames — see §5.

## 5. Why fixtures E/F cropped — and why it is not `setPocAndFix`

This is a correction to the working hypothesis, so it is set out explicitly.

**Fixture E and F are stream copies whose leading samples carry
`AV_PKT_FLAG_DISCARD`.** `ffprobe` on the container:

```
E_leadremoved.mp4     total packets 30   D-flagged 3  (PTS 3003, 2002, 4004)  K-flagged 1 (PTS 5005)
F_two_sony_concat.mp4 total packets 360  D-flagged 3  (PTS 57998, 56997, 58999) K-flagged 7 (PTS 60000, ...)
```

rigaya sees the same flag. `--log-packets` on E, **both** readers, all 30
packets returned, flags column shows `4` (= `AV_PKT_FLAG_DISCARD`) on the
three leading ones:

```
stream  0, hevc,  5005,     0, 1001, 1,      44      <- IDR, flags=1 (KEY), no DISCARD
stream  0, hevc,  3003,  1001, 1001, 4, 2418402      <- leading, flags=4 (DISCARD)
stream  0, hevc,  2002,  2002, 1001, 4, 2540863      <- leading, flags=4 (DISCARD)
stream  0, hevc,  4004,  3003, 1001, 4, 2681676      <- leading, flags=4 (DISCARD)
stream  0, hevc,  9009,  4004, 1001, 0, 2830076
```

`getSample()` honours that flag:

```cpp
// NVEncCore/rgy_input_avcodec.cpp:3410-3415
if (!m_Demux.video.gotFirstKeyframe) {
    if (pkt->flags & AV_PKT_FLAG_DISCARD) {
        //timestampが正常に設定されておらず、移乗動作の原因となるので、
        //AV_PKT_FLAG_DISCARDがついている最初のフレームは無視する
        continue;
    }
```

### The controlled A/B/E comparison that settles it

| fixture | leading pics | DISCARD-flagged | `--avsw` | `--avhw` base | `--avhw` patched |
|---|---|---|---|---|---|
| **A** sony copy → mp4 | 3 | **0** | 30 | 27 | 30 |
| **B** sony copy → **mkv** | 3 | **0** | 30 | 27 | 30 |
| **E** lead-removed | 3 | **3** | **27** | **27** | 30 |

A and B have the **same three leading pictures** as E and deliver all 30
frames on `--avsw`. The only difference is the DISCARD flag. **The crop
tracks the flag, not the leading pictures, and not `setPocAndFix`** — whose
prune is identical in all three rows.

The flag is not rigaya's invention: FFmpeg's own `mov` demuxer sets it, and
FFmpeg's own decode agrees — `ffmpeg -fps_mode passthrough -f null -` on E
reports **27** frames. E/F are therefore *not* a frame-loss defect at all:
they are FFmpeg marking pre-edit-list samples as outside the presentation
window, and rigaya (correctly) ignoring those samples on `--avsw`.

On `--avhw` with the patch, the frames come back (27 → 30, 357 → 360) because
the patched output stage no longer applies the timestamp filter — the decoder
emits them and nothing downstream drops them. That is a *side effect of
removing the filter*, not a `setPocAndFix` fix, and it means a patched
`--avhw` no longer honours `AV_PKT_FLAG_DISCARD` the way `--avsw` and FFmpeg
do. **Recorded here as a real semantic difference of the patch, and as such a
separate concern from the question asked.**

## 6. Minimal `setPocAndFix` model, validated against the real table

`work/model_setpocandfix.py` re-implements `add()`, `sortPts()`,
`setPocAndFix()`, `setPoc()`/`adjustDurationAfterSort()` and the
`RGYQueueMPMP::pop()` semantics (front removal, refuses at `size() <= 1`), and
is driven by the **real** per-packet `(pts, dts, flags)` stream read with
`ffprobe`. It is calibrated against the real `--log-framelist`.

```
C1170 (30 samples, first keyframe pts=3003)
  m_firstKeyframePts = 3003
    prune idx=0 pts=0     (< firstKeyframePts=3003)
    prune idx=0 pts=1001  (< firstKeyframePts=3003)
    prune idx=0 pts=2002  (< firstKeyframePts=3003)
  -> table after setPocAndFix        : 27 entries   (real --log-framelist: 27 rows)  ✓
  -> with the prune disabled         : 30 entries
     first 6 pts: [0, 1001, 2002, 3003, 4004, 5005]
```

Full model output against measured `--log-framelist`:

| fixture | samples | model entries | prune removes | real fpRows (incl. sentinels) |
|---|---|---|---|---|
| C1170 XAVC HS | 30 | 27 | **3** | 29 = 27 + 2 ✓ |
| C9037 H.264 4:2:2 | 195 | 193 | **2** | 194 = 193 + 1 ✓ |
| A sony copy | 30 | 27 | **3** | 29 ✓ |
| E lead-removed | 30 | 27 | **3** | 29 ✓ |
| F two-sony concat | 360 | 357 | **3** | 358 ✓ |
| C x265 re-encode | 30 | 30 | 0 | 32 (0 pruned) ✓ |
| DJI control | 105 | 105 | 0 | 106 ✓ |
| D synthetic | 60 | 60 | 0 | 61 ✓ |

So, precisely: **`setPocAndFix` deletes the leading pictures from
`FramePosList` — 3 entries for XAVC HS, 2 for XAVC S H.264 4:2:2, 0 for every
control — and the model reproduces the real table exactly.** What it does
*not* delete is a delivered frame (§4).

### What the prune actually costs

Because `findpts()` misses the leading pictures' PTS values, three things
degrade for those frames:

1. `PipelineTaskCheckPTS` cannot adopt the *original* duration
   (`NVEncPipeline.h:2399-2410`) and falls back to the nominal one;
2. `LoadNextFrameInternal` gets `FRAMEPOS_POC_INVALID` for them, so
   telecine flags are not applied;
3. the reader's own `"N frames, End of file"` and `--log-framelist`
   under-report — which is exactly why the prior session called it "a
   verification hazard rather than a data-loss hazard", and that judgement is
   confirmed here.

On this corpus (constant `stts`, CFR) none of the three is observable. The
delivered timing is exact: the patched `--avhw` and stock `--avsw` outputs are
**byte-identical** (checked independently: SHA-256 `6336165c…` for both on
C1170), and the recovered frames carry a uniform delta on the 1001-tick
source grid:

```
C1170 delivered packets (patched --avhw): 30
sorted PTS: 0, 4004, 8008, 12012, 16016, 20020, ... max 116116
distinct inter-frame deltas: {4004}          # exactly 1001 source ticks
```

## 7. Do `--avsw` and `--avhw` share it? Yes — and identically

| Evidence | Result |
|---|---|
| Same reader object | `rgy_input.cpp:629-692` builds `RGYInputAvcodec` for both `RGY_INPUT_FMT_AVHW` and `RGY_INPUT_FMT_AVSW` |
| `found first key frame` | identical (`timestamp 3003, offset 0` for both) |
| `adjust trim by offset` | identical (`3` for both, `2` for both on H.264) |
| `--log-framelist` bytes | **identical content** (`Compare-Object` empty) on every fixture |
| fpRows | identical for every fixture (`29/29`, `328/328`, `194/194`, …) |

**`setPocAndFix` is not reader-specific.** Whatever it does, it does to both
paths — so it cannot be the hidden `--avhw`-only defect, and it cannot
explain the `--avhw`/`--avsw` divergence that the `getOutputFrame()` fix
removed.

## 8. Should the patch change `setPocAndFix`? No

Four independent reasons, in order of weight:

1. **No data-loss justification.** Measured: the prune does not change the
   delivered frame count in any configuration, including on `--avhw` with the
   patch applied (A/B/E all deliver 30). There is nothing to recover.
2. **It is shared code with the widest blast radius of any file in the tree.**
   `rgy_input_avcodec.h` is the reader for **NVEncC, QSVEncC and VCEEncC**. A
   change there is a three-product change, not an NVEncC change.
3. **The behaviour it implements is load-bearing for cases with no fixture
   here.** The `<= 16` window exists for open-GOP / TS-stream heads and the
   original comment cites two specific samples. Removing the prune without
   those samples risks an untested regression in the name of a cosmetic fix.
4. **The honest fix is different and cheaper.** The defect is "the reader's
   self-report is not a frame count". The robust answer is the one Phase 1
   already recommends: **never use `N frames, End of file` as truth; count the
   delivered container** (four-way reconciliation: container samples, reader
   estimate, encoder `encoded N`, independent count of the output file). That
   is a *consumer* fix with zero risk to three products.

If it is ever fixed anyway, the minimal shape is to **not delete the entry** —
keep the row, leave `poc` invalid, and let `findpts()` skip it — rather than
to invent a new window predicate. That preserves the `<= 16` anti-wraparound
bound and the "don't delete" intent while removing the data loss from the
table. It should be a **separate upstream patch**, never folded into the
`getOutputFrame()` fix.

## 9. Separate finding: the two patch variants diverge on `--seek`

Not part of the question asked, but found while exercising the fixture set,
and material to which patch to prefer. Both variants are **byte-identical for
every non-seek case** (SHA-256 equal on C1170, C1083, C9037, E, C_x265, DJI).
They differ on `--seek`:

C1083 (330 frames, 3 leading pictures):

| `--seek` | `avhw base` | `avhw theirs` | `avhw mine` | `avsw base` |
|---|---|---|---|---|
| none | 327 | 330 | **330** | 330 |
| 1.0 s | 207 | 207 | **210** | 210 |
| 4.0 s | 27 | 27 | **30** | 210 |

* **`theirs` preserves the original seek behaviour exactly** (207/27 unchanged
  from base) because its guard keeps the drop filter active whenever
  `--seek` is set. That was a deliberate, measured design choice.
* **`mine` changes seek behaviour**: `--avhw` now loses nothing on seek either
  and matches `--avsw` (210/30). Arguably better — but it is a **behaviour
  change on the seek path that has not been validated for content
  correctness**.

The seek offset is the same mistaken equivalence as everywhere else in this
file: the filter assumes "frames before the first packet's PTS" means "frames
before the requested seek point", but on an open-GOP stream the first packet
is the IRAP while the leading pictures present *before* it and can still be
inside the requested window.

**Consequence for the patch:** the `theirs` shape (guard on `m_seek`) is the
more conservative choice and the one whose seek delta is measured as zero.
If the unconditional shape is preferred, it must be re-validated with a
content check on seek output (first output PTS, and that the first frames
correspond to the requested position), not just a frame count.

## 10. Confidence summary

| Statement | Confidence |
|---|---|
| `setPocAndFix` finalises the frame-position table (sort → `poc`/`duration` → prune) | `Confirmed` |
| It executes on `--avsw` and `--avhw` identically, via the same reader | `Confirmed` |
| It deletes 3 entries on XAVC HS, 2 on H.264 4:2:2, 0 on all controls | `Confirmed` (model reproduces the real `--log-framelist` exactly) |
| It does **not** change the delivered frame count on any measured configuration | `Confirmed` (3 binaries × 11 fixtures) |
| It is not the cause of the fixture E/F crop | `Confirmed` (controlled A/B/E: crop tracks `AV_PKT_FLAG_DISCARD`, not the prune) |
| Fixture E/F crop is caused by `AV_PKT_FLAG_DISCARD` in `getSample()` | `Confirmed` (`flags=4` in `--log-packets`; FFmpeg's own decode also reports 27) |
| E/F are not a data-loss defect (FFmpeg also treats them as 27 frames) | `Confirmed` |
| The prune has no observable cost on this corpus (constant `stts`, CFR) | `Confirmed` for these fixtures; `Likely` in general |
| The prune would cost duration/timing accuracy on VFR material | `Unknown` — no VFR fixture available |
| A patched `--avhw` no longer honours `AV_PKT_FLAG_DISCARD` (E/F 27→30) | `Confirmed` (measured); whether that is desirable is a separate question |
| `--avsw` still delivers 27 on E/F with the patch | `Confirmed` — the patch does not touch the `--avsw` path |
| The two patch variants are byte-identical off the seek path | `Confirmed` (SHA-256 equal on 6 fixtures) |
| Which seek behaviour is correct | `Unknown` — frame counts differ (27 vs 30 at `--seek 4.0`); content correctness not validated |

## 11. Recommendation

```text
SEPARATE_FIX
```

* **Do not include `setPocAndFix` in the current NVEncC patch.**
  (`NO_ACTION` for the patch itself.) There is no delivered-frame benefit, the
  file is shared by three encoders, and the behaviour it guards has no fixture
  here.
* **Report it upstream as a separate, low-severity defect:** "the reader's
  frame-position table silently drops open-GOP leading pictures, so
  `N frames, End of file` and `--log-framelist` under-report on XAVC".
  Suggested minimal shape in §8; must ship with the open-GOP/TS samples the
  original comment cites.
* **Fix the consumer instead, now:** treat the reader's self-report as an
  estimate, and reconcile frame counts from the delivered container. This is
  already the Phase 1 recommendation and it needs no third-party patch.
* **Before adopting either `getOutputFrame()` patch variant, decide the
  `--seek` question** (§9). `theirs` is measured no-change on seek; `mine`
  changes it and matches `--avsw` frame-for-frame. Either way, validate seek
  output *content*, not just counts.
* **Record the `AV_PKT_FLAG_DISCARD` semantic difference** (§5): patched
  `--avhw` bypasses a container signal that `--avsw` and FFmpeg both honour.
  On this corpus the frames are real pictures and recovering them is right;
  but it is a behaviour change and belongs in the adoption decision.

## 12. Artifacts

`work/` is gitignored by project convention, so these stay local by design.

| Path | Role |
|---|---|
| `work/model_setpocandfix.py` | minimal `setPocAndFix` model, validated against real `--log-framelist` |
| `work/measure_setpocandfix.py` | per-fixture reader/decoder/table/output measurement |
| `work/measure_second_path.py` | reader self-report vs delivered count |
| `work/compare_three.py` | base / theirs / mine across fixtures, incl. `--seek` |
| `work/check_recovered_timing.py` | delivered-PTS check for the recovered frames |
| `work/tabulate_seek.py` | seek comparison table |
| `work/raw/setpoc-baseline.json`, `setpoc-patched.json` | measurement matrices |
| `work/raw/tri-compare2.json` | three-binary comparison |
| `work/raw/secpath/fp-*.csv` | the real `--log-framelist` dumps analysed above |
| `work/raw/byteid/` | delivered MP4s used for the SHA-256 identity check |
| `work/raw/sp/pkt-E-*.txt` | `--log-packets` showing `flags=4` on E's leading samples |
| `work/raw/backup-nvenc/`, `work/raw/bin-theirs/` | prior-session patched binary + header |
| `work/raw/bin-mine/` | this session's build (unconditional variant), runnable with its DLLs |

> **Worktree hygiene.** The prior session's build tree lives in a *different*
> worktree (`F:\1KT-avhw`, branch `research/rigaya-nvencc-avhw`). It was used
> read-only, and the header and both copies of its `NVEncC64.exe` were restored
> and SHA-256-verified afterwards. This session's binary is kept inside this
> worktree at `work/raw/bin-mine/`, so nothing here depends on that worktree
> remaining in place.

Upstream source: `rigaya/NVEnc` tag `9.31`, commit
`2cb9d810c045202548b98ff130b12bc764eb39ea`.

## Reproduction

```powershell
$base   = 'F:\1KeyTranscoder\tools\NVEncC_9.31_x64\NVEncC64.exe'
$mine   = 'F:\1KeyTranscoder-rigaya-research\work\raw\bin-mine\NVEncC64.exe'
$theirs = 'F:\1KeyTranscoder-rigaya-research\work\raw\bin-theirs\NVEncC64.exe'
$E      = 'F:\1KT-avhw\work\hwdecode2\fixtures\E_leadremoved.mp4'
$C1170  = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'

# 1. what setPocAndFix removes, and that both readers agree byte-for-byte
& $base -i $C1170 --avsw -c raw --output-res 64x64 --log-framelist fp-avsw.csv -o NUL
& $base -i $C1170 --avhw -c raw --output-res 64x64 --log-framelist fp-avhw.csv -o NUL
#   -> both: 27 real rows, first pts 3003 (leading 0/1001/2002 pruned)

# 2. the prune does not change the delivered count
& $base -i $C1170 --avsw -c raw --output-res 64x64 -o NUL   # 30 / 30 samples
& $mine -i $C1170 --avhw -c raw --output-res 64x64 -o NUL   # 30 / 30 samples

# 3. fixture E: the crop is AV_PKT_FLAG_DISCARD, not the prune
& $base -i $E --avsw -c raw --output-res 64x64 --log-packets pkt.txt -o NUL
#   -> 3 leading packets with flags=4; --avsw delivers 27 (FFmpeg also reports 27)
& $mine -i $E --avhw -c raw --output-res 64x64 -o NUL        # 30

# 4. model vs reality
python work\model_setpocandfix.py

# 5. three-binary comparison incl. seek
python work\compare_three.py work\raw\tri-compare2.json
python work\tabulate_seek.py
```
