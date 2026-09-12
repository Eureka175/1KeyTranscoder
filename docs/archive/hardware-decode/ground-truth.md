# 4. Software Ground Truth

> Deliverable: `docs/hardware-decode/test-results/software-ground-truth.json`
> Tooling: `work/hwdecode/fingerprint.py` (mode `sw`),
> `work/hwdecode/run_batch.py`

## 4.1 Why `ffprobe -count_frames` is *not* the ground truth

The investigation deliberately refuses the shortcut. Three different
quantities are all called "frame count" in this project and in FFmpeg:

| Symbol | Quantity | How obtained | What it actually measures |
|---|---|---|---|
| **F1** | demuxed **packets** | `ffprobe -count_packets` → `nb_read_packets` | how many samples the container holds. No decoder involved. |
| **F2** | container **sample count** | `stsz.sample_count` / `nb_frames` | same as F1 in practice for these files |
| **F3** | **decoded frames** | actually running a decoder to EOF | what a transcoder really gets |

`core/probe.py:316-333` (`count_frames`) returns **F1** while its
docstring calls it "decoded packet count" — a naming defect, not a
computation defect, but it means the project's 1:1 gate
(`core/batch_hw.py:336-363`) is a *packet*-count comparison.

F1 and F3 coincide on this corpus (§4.4), which is exactly why the
distinction is easy to miss and dangerous to rely on: they diverge
precisely in the presence of the defect under investigation.

Ground truth here is therefore **F3, obtained by decoding**.

## 4.2 Method

The exact decode loop, in FFmpeg's own terms:

```
demux
  ↓  (mov demuxer, DEFAULT edit-list handling)
send packets                  -> all packets, no early stop
  ↓
receive frames                -> in presentation order
  ↓
EOF
  ↓
flush / drain decoder         -> CLI sends the NULL packet and drains
  ↓
until no more frames
```

Command actually executed (per file, per mode):

```
tools/ffmpeg.exe -hide_banner -nostdin -loglevel info
    -i <source>
    -map 0:v:0 -an -sn -dn
    -fps_mode passthrough
    -vf scale=64:64:flags=bilinear,format=gray,showinfo
    -f rawvideo -pix_fmt gray -s 64x64 -
```

Design decisions and why:

| Decision | Reason |
|---|---|
| `tools/ffmpeg.exe` (**9.0.1**), never the `PATH` copy (8.0.1) | the project ships and validates against 9.0.1; the user explicitly required the bundled build |
| `-fps_mode passthrough` | **essential.** Without it FFmpeg may insert or drop frames to reach a constant rate, and the measuring instrument would create the frame-count difference it is measuring. The project's `x265.py:201,246` / `svtav1.py:149,194` already use this; the rigaya paths use `--avsync forcecfr` instead, which is a structural asymmetry (see `implementation-plan.md`). |
| `-map 0:v:0 -an -sn -dn` | isolate the decoder. Audio, the `rtmd` data track and any timecode track must not enter the frame pipeline. |
| default edit-list handling (no `-ignore_editlist`) | the ground truth must be the *default* consumer behaviour, not a tuned one |
| drain to EOF | the decoder is not stopped early; the CLI's own flush is what proves whether delayed frames survive |
| `showinfo` **after** the canonical conversion | records `pts`, `pts_time`, `duration`, `pict_type`, `iskey`, `adler32` per frame in output order |
| sha1 of the 4096-byte plane | a deterministic content fingerprint per frame |

Per frame this yields, at minimum: `index`, `pts`, `fingerprint`
(plus `pict_type`, `iskey`, `adler32`, `duration`).

## 4.3 Why the `showinfo` statistics were cross-checked

FFmpeg's own adler32 checksums (`checksum:`/`plane_checksum:`) are
recorded alongside the Python sha1 of the same bytes, and the two are
merged with the frame stream. If `showinfo` and stdout ever desynchronise
the merge is detectable — `frames_without_showinfo` is stored per run.
One run (`20260903_C1084.MP4`, `nvdec_cuvid`) initially raised a
`KeyError` from an over-strict merge; that was a tooling defect and was
fixed rather than reported as a decoder failure. It is recorded here
because "the tool crashed" and "the decoder failed" must never be
confused.

## 4.4 Result

| Metric | Value |
|---|---|
| Files decoded to EOF | **151 / 151** |
| Total decoded frames (F3) | **188,475** |
| Total container samples (F2) | **188,475** |
| `decoded(frame) == container(sample)` | **true for all 151 files** |
| Decode warnings | **every file** emits the same edit-list warning (§4.5) |
| Non-zero exits | 0 |
| First PTS | **0** for every file (edit list applied) |
| Non-monotonic PTS sequences | **0** |
| Picture-type census | B 184,880 · I 3,239 · P 356 (≈98 % B-frames) |
| Total keyframes | 3,239 (≈1.7 % of frames — GOP 60) |
| PTS span (min / median / max) | 0.484 s / 7.491 s / 188.171 s |

**Ground truth for every Sony corpus file is the full container sample
count.** For these clips F1 = F2 = F3, and the software decoder emits
every coded picture including all leading pictures that precede the first
keyframe.

The ≈98 % B-frame share is worth noting on its own: these streams are
sustained almost entirely by bidirectional prediction, so any defect in
reorder handling or in the drain of delayed frames would be severe and
obvious. None was found in the software path.

## 4.5 Ground-truth caveat — the edit-list warning

**All 151 files** cause software decode to print:

```
[in#0] st: 0 edit list: 1 Missing key frame while searching for timestamp: 1001
[in#0] st: 0 edit list 1 Cannot find an index entry before timestamp: 1001.
```

This is the demuxer observing that the edit list's presentation start is
not a keyframe — the RC-3 structure from
[`root-cause.md`](root-cause.md). In this corpus it is **cosmetic**: no
frame is lost. It is recorded because a future hardware path could
legitimately react to it differently, and because it is the clearest
in-band signal that these containers are structurally unusual. It is also
the single most reproducible warning in the whole investigation: it is not
an occasional artefact, it is a property of the format as Sony writes it.

## 4.6 What the ground truth does *not* assert

- It does **not** assert that the software decoder is bit-exact with the
  encoder. It asserts what the software decoder emits, which is what the
  comparison needs.
- It does **not** cover interlace, HDR, All-I, or non-4K Sony material
  (see `corpus.md` §3.5).
- `pts` values are recorded in FFmpeg's **default** edit-list-adjusted
  timeline. `-ignore_editlist 1` was run as a control on
  `20260823_C0886.MP4` and changes only `first_pts` (0 → 2002); the frame
  count stays 360 and the picture sequence is unchanged. This control is
  what makes it safe to say the edit list affects timestamps but not
  frame integrity in the software path.
