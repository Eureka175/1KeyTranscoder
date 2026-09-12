# Static analysis: every video-decode site, frame flow, and frame-count gate

> **归档说明（2026-09 文档梳理时入库）。** 本文档原在 `work/hwdecode/notes/static-analysis.md`
> （`work/` 不入库）。转存原因：它是对**生产树全部视频解码点**的逐点静态分析
> （897 行），是"硬件解码在当前代码中不可达"这一判断的原始证据，`docs/` 下
> 没有等价记录。
>
> **时效提示**：分析对象为 2026-08 的代码。此后默认后端改为能力优先自动选择
> （v0.6.2），但**解码路径仍是恒 `--avsw`**，故"硬件解码不可达"这一结论
> 依然成立；具体行号可能已漂移。

Scope: **read-only static analysis** of the production tree only —
`1kt.py`, `core/`, `encoders/`, `preservation/`, `watchfolder.py`.
Excluded as non-production: `dist/`, `work/release_out/`, `work/verify12/`,
`work/release_ref/`, `olddocs/backup/`, `docs/reference/`.

Bug under investigation: "Sony video, when hardware-decoded via QSV/NVDEC,
runs to completion and exits normally, but the resulting frame count differs
from software decode."

---

## 0. Headline

**Hardware decoding is NOT reachable in the current code.** Decode is
hard-coded to rigaya software decode (`--avsw`) at exactly two literals
(`encoders/nvencc.py:107`, `encoders/qsvencc.py:102`), and the FFmpeg paths
pass no `-hwaccel` / decoder-selection flag at all. There is no CLI switch,
no profile key, and no capability probe that can turn hw decode on.

The project has **already documented this exact bug** in
`docs/design/hardware_backend_design.md` (see §8.0 below) with a measured
matrix: hw decode loses 3 frames on A7M5 (357/360) and 2 on A7M4 (193/195),
unaffected by `:noedit`, `--avsync forcecfr/vfr`,
`--offset-video-dts-advance`, B-frames, or strip. `--avsw` is 360/360 and
195/195.

So the *hw-decode* attribution cannot come from the current source tree as
written. What the tree **does** contain are several independent mechanisms
that can change the frame count while exiting 0 — listed and ranked in §8.

---

## 1. Decode entry points (complete inventory)

### 1A. rigaya CLI decode+encode (NVEncC / QSVEncC) — the only external decoder

There are exactly **two** argv builders, and **one** production execution site.

`encoders/nvencc.py:127-145` — the NVEncC argv:

```python
def command(self, source, output, profile, chroma, depth,
            vfr=False, audio_copy=False, color=None):
    args, skipped, notes = self.build_args(profile, chroma, depth, vfr, color)
    cmd = [str(self.tool), "-i", str(source), *args]
    if audio_copy:
        cmd += ["--audio-copy"]
    cmd += ["-f", "mp4", "-o", str(output)]
    return cmd, skipped, notes
```

`encoders/qsvencc.py:124-142` — byte-for-byte the same shape.

`encoders/nvencc.py:106-110` (NVEncC) / `encoders/qsvencc.py:101-105` (QSVEncC)
— the reader/decoder flags:

```python
args = [
    "--avsw", "--video-track", "1", "-c", self.codec,
    "--output-depth", str(depth),
    *args,
]
```

Execution site (`core/batch_hw.py:332-334`) — the only production call:

```python
rc, elapsed = run_hw_tool(
    cmd, raw_log, total_frames, label, progress=show_progress
)
```

`encoders/hw.py:169-217` runs it via `subprocess.Popen(cmd, ...)` with
`stdout=PIPE, stderr=STDOUT`, line-parses the rigaya progress format
(`encoders/hw.py:110-116`), and **returns only `(return_code, elapsed_sec)`**
(`encoders/hw.py:217`). Nothing about the decoded/encoded frames comes back
out (see §2, R10).

`backend.command()` is called in exactly 4 production places:
`core/batch_hw.py:309` (real encode), `:660`, `:909`, `:1112` (dry-run
command printing only).

### 1B. FFmpeg CLI (`-i` + software decode; no hwaccel anywhere)

| Site | argv | Output use |
|---|---|---|
| `encoders/x265.py:168-205` | `ffmpeg -hide_banner -nostdin -stats -y -i <src> -map 0 -map_metadata 0 -map_chapters 0 -c:v:0 libx265 -preset P -crf C -pix_fmt <fmt> [-c:v:N copy] -c:a copy -c:s copy -c:d copy -c:t copy -fps_mode passthrough -x265-params <p> -movflags +use_metadata_tags <part_dst>` | file; `run_ffmpeg` parses `frame=` for the console only |
| `encoders/x265.py:231-250` | same, video-only: `-map 0:v:0 -c:v libx265 ... -an -sn -dn -fps_mode passthrough -tag:v hvc1 <out_mov>` | file (Sony/DJI intermediate) |
| `encoders/svtav1.py:116-153` | `... -i <src> -map 0 ... -c:v:0 libsvtav1 -pix_fmt <fmt> ... -fps_mode passthrough -svtav1-params <p> ... <part_dst>` | file |
| `encoders/svtav1.py:179-198` | video-only variant, `-tag:v av01` | file |
| `preservation/quality.py:310-316` | `ffmpeg -hide_banner -nostdin -y -i <original> -i <final> -filter_complex <psnr/ssim graph> -map [p] -map [s] -f null -` | **decodes BOTH sides simultaneously**; reads PSNR/SSIM from stderr |
| `core/channel_sync.py:230-242` | `ffmpeg -v error -nostdin -y -i <src> -map 0:a:N -vn -sn -dn -f f32le/f64le -ac 1 -ar <rate> <out_raw>` | **audio only** — writes raw mono float file |
| `core/channel_sync.py:275-286` | `ffmpeg ... -map 0:a:N -vn -sn -dn -c:a copy -f mov/mp4 <out>` | **audio only**, stream copy (no decode) |
| `core/channel_sync.py:929-940` | `ffmpeg ... -f f32le -ar <rate> -ac 1 -i <raw> -c:a <codec> -f mov/mp4 <out>` | **audio only**, re-encode from raw |
| `core/versions.py:65-70` | `ffmpeg -f lavfi -i color=size=64x64:rate=1 -c:v libsvtav1 -preset 12 -crf 40 -frames:v 1 -f null -` | version-probe only (synthetic 64x64) |
| `preservation/poc_video.py:65-75` | `ffmpeg -y -hide_banner -i <src> -map 0:v:0 -c:v libx265 -preset ultrafast -crf 28 -tag:v hvc1 -an -sn -dn <out_mov>` | **NOT PRODUCTION** — `FFmpegUltrafastVideoBackend` is referenced only from `olddocs/sony_poc.py:36,121`; production injects `encode_video` as a callback (`preservation/pipeline.py:83`) |

All FFmpeg sites decode in software. Verified: no `-hwaccel`, no
`-c:v h264_cuvid|hevc_cuvid|h264_qsv|hevc_qsv`, no `-hwaccel_output_format`,
no `hwdownload`.

### 1C. ffprobe — metadata/packet counting only, never frame decode

`core/probe.py:154-174` (`probe_source`), `core/probe.py:318-324`
(`count_frames`), `core/batch_hw.py:1348-1355` (`_encoded_ok`),
`preservation/pipeline.py:181-188` (`_encoded_ok`),
`preservation/poc_video.py:22-30`, `preservation/validate.py:26-29`,
`preservation/selfcheck.py:44-54`, `preservation/quality.py:76-83`,
`preservation/dji.py:501-509` and `:566-574`,
`preservation/sony.py:154-158`, `preservation/colour.py:59-69`,
`preservation/dji.py:100-104` (a `subprocess.run` whose argv assembles an
ffprobe invocation for the DJI rebuild path).

None of these pass `-show_frames` or `-count_frames`.

### 1D. MP4Box / GPAC — container only, no elementary-stream decode

`preservation/gpac.py:37-51` `_run()` is the single executor; all methods
(`diso_xml`, `info`, `raw_track`, `nhml_dump`, `dump_meta_xml`,
`dump_meta_item`, `mux_new`, `add_track`, `add_track_ref`, `set_meta`,
`add_meta_item`, `set_meta_xml`, `meta_pass`, `set_brand`, `flatten`) are
box-level or track-copy operations. `nhml_dump` (`gpac.py:214-247`) is
defined but **never called** in the production pipeline.

### 1E. Gyroflow — container/IMU reader

`preservation/gyroflow.py:49-65`: `gyroflow <video> --export-metadata 2:<json>`.
Reads the container for IMU/metadata; not the project's decode path.

### 1F. NOT FOUND

Searched for and found **no** occurrences anywhere in the production tree of:
PyAV (`import av`), `decord`, `imageio`, `cv2`/`opencv`, rawvideo piping
(`-f rawvideo`), `send_packet`, `receive_frame`, `avcodec_*`, `av_frame_*`,
`sws_scale`, `hwdownload`, `d3d11va`, `dxva2`, `vulkan`, `h264_cuvid`,
`hevc_cuvid`, `h264_qsv`, `hevc_qsv`, `avcuvid`, `avqsv`, `--avhw`,
`-hwaccel`.

---

## 2. Frame counting: where, how, and semantics

### 2.1 The three different "frame count" quantities in this codebase

| # | Source | Function | Semantics |
|---|---|---|---|
| F1 | `nb_read_packets` | `core/probe.py:316-333` `count_frames()` | **demuxed PACKETS** of stream `v:0`. Docstring says "decoded packet count" — it is *not* decoded frames and *not* `-count_frames`. |
| F2 | `nb_frames` → else `ceil(duration*fps)` | `core/probe.py:212-214`, surfaced as `summary["total_frames"]` | **container metadata or a duration×fps estimate** |
| F3 | rigaya `N frames` / FFmpeg `frame=N` | parsed but discarded | see R10 |

`core/probe.py:316-333` verbatim:

```python
def count_frames(ffprobe: Path, path: Path) -> tuple[int, str]:
    """(decoded packet count, avg_frame_rate) for the first video stream."""
    cmd = [
        str(ffprobe), "-v", "error", "-count_packets",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=nb_read_packets,nb_frames,avg_frame_rate",
        "-of", "json", str(path),
    ]
    ...
    frames = int(st.get("nb_read_packets") or st.get("nb_frames") or 0)
    return frames, str(st.get("avg_frame_rate") or "")
```

Note the `or` fallback: when `nb_read_packets` is absent/0 the function
silently returns F2 instead of F1 — so the *same function* can return
different metrics for the two files being compared.

`core/probe.py:212-214` (F2):

```python
total_frames = _parse_int(video.get("nb_frames"))
if total_frames <= 0 and duration > 0 and fps > 0:
    total_frames = int(math.ceil(duration * fps))
```

### 2.2 The 1:1 frame-count gates

**G1 — the hardware 1:1 gate (the important one).**
`core/batch_hw.py:336-363` inside `hw_encode_with_fallback`:

```python
ok = False
if rc == 0 and output.is_file() and output.stat().st_size > 0:
    if do_frame_check:
        try:
            frames, fps = count_frames(ffprobe, output)
            src_frames, src_fps = count_frames(ffprobe, source)
            if frames == src_frames and (
                not src_fps or fps == src_fps
            ):
                ok = True
            else:
                emit_warning(..., f"1:1 check failed ({frames}/{src_frames}, "
                    f"{fps}/{src_fps}) — treated as format failure", warnings)
```

Semantics: F1(output) vs F1(source) plus string equality of
`avg_frame_rate`. A mismatch is classified as a **format failure** and drives
the downgrade ladder (`batch_hw.py:395-413`); when the ladder is exhausted it
raises `RuntimeError` (`batch_hw.py:396-400`).

**Gate enablement per path:**

| Path | `do_frame_check` | Evidence |
|---|---|---|
| Sony hw | **True** | `core/batch_hw.py:698` `do_frame_check=True` |
| DJI hw | `not vfr` | `core/batch_hw.py:1141` |
| non-Sony hw ("classic") | **False** | `core/batch_hw.py:930` |

**G2 — the x265/SVT-AV1 Sony gate (raises).** `1kt.py:562-573`:

```python
out_frames, out_fps = count_frames(ffprobe, out_mov)
src_frames = source_summary["total_frames"]
if src_frames and out_frames != src_frames:
    raise RuntimeError(
        f"frame count mismatch: source {src_frames} vs "
        f"encoded {out_frames}"
    )
```

⚠️ **Metric mixing**: `out_frames` is F1 (`nb_read_packets`), `src_frames` is
F2 (`nb_frames` / `ceil(duration*fps)`). These are not the same quantity.

**G3 — the x265/SVT-AV1 DJI gate (raises).** `1kt.py:789-795`, identical
mixing.

**G4 — non-Sony hw path (`encode_one_classic`).** No frame gate at all;
success is `rc == 0` plus a non-empty output (`1kt.py:393-405`,
`core/batch_hw.py:943-947`).

**G5 — POC gate (not production).** `preservation/poc_video.py:87-107`:

```python
# hard 1:1 timeline requirement: same frame count, same frame rate.
src_frames = int(src_v.get("nb_read_packets") or src_v.get("nb_frames") or 0)
out_frames = int(out_v.get("nb_read_packets") or out_v.get("nb_frames") or 0)
if src_frames and out_frames != src_frames:
    raise RuntimeError(f"frame count mismatch: source {src_frames} vs encoded {out_frames}")
```

**G6 — DJI post-hoc gate (critical).** `preservation/dji.py:526-535`:

```python
if not vfr:
    from core.probe import count_frames
    s_frames, s_fps = count_frames(ffprobe, original)
    o_frames, o_fps = count_frames(ffprobe, final)
    eq("dji.video.frame_count", s_frames, o_frames)
    eq("dji.video.frame_rate", s_fps, o_fps)
else:
    note("dji.video.frame_count", UNKNOWN, "VFR source: gate skipped")
```

`dji.video.*` is in `critical_prefixes` (`dji.py:664-665`), so a mismatch
here **does** set `structural_success = False` (`dji.py:666-682`).

**G7 — Sony post-hoc gate (NON-critical).** `preservation/validate.py:213-214`:

```python
eq("video.frame_count",
   sv.get("nb_frames"), ov.get("nb_frames"))
```

but the fatal whitelist `critical_modified` at `validate.py:452-477` contains
`video.timescale`, `video.track_duration`, `video.stts`, `video.elst`,
`video.codec`, the colour items — and **not** `video.frame_count` or
`video.frame_rate`. `structural_success = not critical_missing and not
critical_modified` (`validate.py:482`). A frame-count mismatch is therefore
recorded as a MODIFIED item and **does not fail the Sony run**. (At
`--check full` it is caught indirectly — see G8.)

**G8 — selfcheck (`--check full` only).** `preservation/selfcheck.py:284-285`:

```python
for key in ("width", "height", "avg_frame_rate", "nb_read_packets"):
    note(f"video.ffprobe.{key}", s_v.get(key), o_v.get(key))
```

`note` → `_compare_items` (`selfcheck.py:122-137`) records `FAIL`, and
`overall = summary[FAIL] == 0` (`selfcheck.py:496`). `checker.py:108-118`
then sets `structural_success = False`. So at `--check full` frame count is
fatal; at the default `--check basic` (`1kt.py:991-993`) it is not.

**G9 — quality sample tolerance.** `preservation/quality.py:285-297`:

```python
if (
    src_probe["frames"]
    and out_probe["frames"]
    and abs(src_probe["frames"] - out_probe["frames"]) > 2
):
    rep = result(
        _SKIP,
        f"frame count mismatch {src_probe['frames']} vs "
        f"{out_probe['frames']}", ...)
```

|Δ| ≤ 2 packets passes silently; |Δ| > 2 becomes **SKIP** (never FAIL).

### 2.3 `_encoded_ok` — resume validity checks (≥1 packet only)

`core/batch_hw.py:1342-1363`:

```python
def _encoded_ok(ffprobe: Path, path: Path) -> bool:
    """A reusable intermediate must be a real file with >=1 video
    packet, not a partial artifact of an interrupted encode."""
    ...
    return bool(streams and int(
        streams[0].get("nb_read_packets") or 0
    ) > 0)
```

Identical closure `preservation/pipeline.py:175-196`. **No frame-count
validation** — any file with one packet is accepted as a valid intermediate.

### 2.4 Progress-line frame counts are parsed and discarded

`encoders/hw.py:185-193` sets `last_frame` from the rigaya progress line; the
function returns only `(return_code, elapsed)` at `encoders/hw.py:217`.
`_last_encode_fps` (`core/batch_hw.py:233-242`) extracts **only the fps**
from `encoded N frames, F fps`:

```python
matches = re.findall(r"encoded\s+\d+\s+frames,\s*([\d.]+)\s+fps", text)
return float(matches[-1]) if matches else 0.0
```

`1kt.py:121-129` parses `frame=(\d+)` into `current_frame`, used only for the
console line and the 10-second log line; `run_ffmpeg` returns
`(return_code, elapsed)` at `1kt.py:175`.

---

## 3. Frame feeding / hardware-decode configuration

### 3.1 Verdict: decode is hard-coded to software

**Definitively software-decode-only.** Three independent confirmations:

1. **The literals.** `encoders/nvencc.py:107` and `encoders/qsvencc.py:102`
   both begin the arg list with the unconditional string `"--avsw"`. It is
   the first emitted flag and nothing later in the list re-selects a reader.
2. **No reachable override.** `build_flag_args` (`encoders/hw.py:45-108`)
   iterates **only** over the keys of `PARAM_MAP`
   (`encoders/nvencc.py:25-61`, `encoders/qsvencc.py:21-69`). Neither map
   contains `avhw`, `avcuvid`, `avqsv`, or any decode key, so profile JSON
   cannot inject one.
3. **Documented intent.** `encoders/caps.py:9-13`:
   ```
   The encode-side format matrix is parsed from the text `--check-features`
   output ... Decode-side capabilities are NOT modeled: hardware
   encode paths always decode with `--avsw` (software), so decode
   capability is irrelevant by design.
   ```
   and `core/batch_hw.py:7`: `- hardware encode paths always decode with
   --avsw (software);`, `encoders/nvencc.py:6-8`,
   `encoders/qsvencc.py:8`, `docs/design/hardware_backend_design.md:429-430`.

### 3.2 The `avhw` profile key is dead

`nvenc.json` carries `"avhw": true` at lines 50, 88, 126, 164. It is
**inert**: `_SKIPPED_ALWAYS` in `encoders/nvencc.py:70-73` lists `"avhw"`:

```python
_SKIPPED_ALWAYS = (
    "split_enc", "parallel", "output_buf",
    "cuda_schedule", "avoid_idle_clock", "avhw", "output_depth",
)
```

but grepping `encoders/` for `_SKIPPED_ALWAYS` returns **only the two
definitions** (`nvencc.py:70`, `qsvencc.py:71`) — it is never read. The
actual protection is the absence of `"avhw"` from `PARAM_MAP`; because
`build_flag_args` iterates `PARAM_MAP`, the key is not even reported as
skipped.

### 3.3 Exact emitted reader/decoder flags per backend

| Backend | Reader/decoder flags | Full shape |
|---|---|---|
| `NvencBackend` (HEVC/AV1) | `--avsw --video-track 1` | `<NVEncC64.exe> -i <src> --avsw --video-track 1 -c <hevc\|av1> --output-depth <N> [<profile flags>] [--atc-sei <v>] [<color flags>] [--output-csp yuv422] [--avsync forcecfr] [--audio-copy] -f mp4 -o <out>` (`encoders/nvencc.py:106-110,119-124,141-144`) |
| `QsvBackend` (HEVC/AV1) | `--avsw --video-track 1` | same shape (`encoders/qsvencc.py:101-105,114-121,138-141`) |
| `X265Backend` | **none** (FFmpeg default software decoder) | `encoders/x265.py:168-205` / `:231-250` |
| `SvtAv1Backend` | **none** | `encoders/svtav1.py:116-153` / `:179-198` |
| PSNR/SSIM | **none** | `preservation/quality.py:310-316` |
| channel_sync | `-vn -sn -dn` (audio only) | `core/channel_sync.py:232-238` |

`--video-track 1` (`nvencc.py:107`, `qsvencc.py:102`) is a *positional*
selector for the first video track. `docs/design/hardware_backend_design.md:116-117`
notes its semantics are "第 N 条视频轨（按分辨率排序）", not a container
track id.

### 3.4 No CLI surface for decode

`1kt.py:922-1043` (`main`'s argparse) exposes `--encoder`, `--preset`,
`--config`, `--scaling-config`, `--tool-nvencc`, `--tool-qsvencc`, `--ffmpeg`,
`--ffprobe`, `--gpac-dir`, `--gyroflow`, `--channel-sync`,
`--channel-sync-transparent`, `--check`, `--jobs`, `--experimental-multihw`,
`--headless`, `--retry-list`, `--keep-work`, `--no-downgrade`, `--dry-run`.
**No decode-related option exists.**

---

## 4. Flush / drain / EOS

**NOT FOUND.** Searched the whole production tree for: `send_packet`,
`receive_frame`, `EAGAIN`/`eagain`, `avcodec_flush`, `decode()` (PyAV),
`flush_packets`, `-flush_packets`, `-fflags +genpts`, `drain`.

The project has **no programmatic decode loop of any kind**. Every decode
is delegated to a child process, so drain/EOS is entirely implicit in the
rigaya CLI (`encoders/hw.py:169-217`) or in FFmpeg (`1kt.py:105-175`,
`preservation/quality.py:310-321`).

The only end-of-stream signals the project observes are:

- `proc.wait()` return code — `encoders/hw.py:211`, `1kt.py:170`,
  `preservation/quality.py:349`;
- the rigaya final line `encoded N frames, F fps` — regex at
  `encoders/hw.py:114-116`, of which only fps is retained
  (`core/batch_hw.py:239-242`);
- FFmpeg's `frame=N` progress line — `1kt.py:121`, retained only for display.

Consequence: a short final drain (last few frames lost at EOF) is invisible
to this codebase — the child returns 0, and the packet count of the written
file is whatever the muxer got.

---

## 5. HW frame transfer / pixel format

- **`hwdownload`: NOT FOUND.** No hw frames ever exist in-process.
- **Pixel-format decision points (three, one per backend family):**

  `encoders/base.py:25-40` — `ffmpeg_pix_fmt(src_info)` (x265 path):
  ```python
  if src.bit_depth <= 0 or src.chroma not in _CHROMA_BASE:
      return "yuv420p10le"
  base = _CHROMA_BASE[src.chroma]
  if src.bit_depth > 8:
      return f"{base}{src.bit_depth}le"
  return base
  ```
  with `_CHROMA_BASE = {"4:2:0": "yuv420p", "4:2:2": "yuv422p", "4:4:4": "yuv444p", "mono": "gray"}`
  (`base.py:17-22`). For x265 a 4:2:2 source stays 4:2:2 — **no chroma
  conversion**.

  `encoders/svtav1.py:99-102` — `av1_pix_fmt(src_info)`:
  ```python
  return "yuv420p10le" if src_info.bit_depth > 8 else "yuv420p"
  ```
  SVT-AV1 always converts to 4:2:0; the caller warns
  (`1kt.py:1575-1587`, `encoders/svtav1.py:12-13`).

  `encoders/hw.py:368-395` — `plan_initial_format(caps, backend_kind, chroma, depth, codec)`:
  ```python
  if codec == "av1":
      return ("4:2:0", max(depth, 10)), chroma != "4:2:0"
  if chroma == "4:2:2":
      if backend_kind == "qsvencc":
          return ("4:2:0", max(depth, 10)), True
      if supports(caps, "4:2:2", depth, codec):
          return (chroma, depth), False
      return ("4:2:0", max(depth, 10)), True
  if depth > 8:
      if supports(caps, chroma, depth, codec):
          return (chroma, depth), False
      return ("4:2:0", 10), True
  return (chroma, depth), False
  ```
  Callers: `core/batch_hw.py:277-279`, `:655-657`, `:893-895`, `:1091-1093`.
  The `--output-csp yuv422` emit (`nvencc.py:121-122`, `qsvencc.py:118-119`)
  is effectively NVENC-only, because the QSV branch above already rewrote
  4:2:2 → 4:2:0 (the QSV comment at `qsvencc.py:116-117` acknowledges this).

- **10-bit ↔ 8-bit.** `--output-depth` is always emitted from the planned
  depth (`nvencc.py:108`, `qsvencc.py:103`); the FFmpeg side encodes it into
  the pix_fmt string. The downgrade ladder (`encoders/caps.py:205-212`)
  walks source → (4:2:0,10) → (4:2:0,8).

- **Color conversion.** No matrix/range conversion filter anywhere. Colour
  is passed through as signalling only: rigaya flags
  `--colorprim/--transfer/--colormatrix/--colorrange`
  (`encoders/hw.py:336-341`), and for AV1 a container `colr` box byte patch
  (`preservation/isobmf.py:567-635`, called from
  `preservation/pipeline.py:352-361` via `preservation/colour.py`).

- **Forced format in the quality metric.** `preservation/quality.py:303-308`:
  ```python
  graph = (
      "[0:v]settb=AVTB,setpts=N,format=yuv420p10le,split=2[x1][x2];"
      "[1:v]settb=AVTB,setpts=N,format=yuv420p10le,split=2[y1][y2];"
      "[x1][y1]psnr=stats_file=quality_psnr.csv[p];"
      "[x2][y2]ssim[s]"
  )
  ```
  Both sides are squashed to yuv420p10le and re-timestamped by **frame
  index** (`setpts=N`), which deliberately hides any timing divergence — see
  `quality.py:6-8`.

---

## 6. Timestamp / PTS / DTS handling

### 6.1 In-process PTS/DTS: NOTHING

Grep for `\bpts\b|\bdts\b|setpts` across production returns **only**
`preservation/quality.py:304-305` (the metric filter graph above) and the
word `-dts` inside a docstring at `preservation/gpac.py:8` (an MP4Box quirk
note). **No production code reads, computes, or rewrites a PTS or DTS.**
No `ctts` parsing anywhere (grep for `ctts|stss|sdtp|cslg|sbgp|sidx` in
`preservation/isobmf.py` returns only the words `stco/co64` in comments).

### 6.2 Container-level duration patching

`preservation/isobmf.py:210-237` — `_stts_sum`:

```python
"""Sum of sample durations (stts) for a track, in track units.

stts stays exact through GPAC rewrites (native sample-table copy),
unlike mdhd durations which GPAC can mangle when rescaling a
hardware-intermediate track to a different timescale.
"""
...
        sc, delta = struct.unpack(">II", f.read(8))
        total += sc * delta
```

⚠️ This sums **decode** durations only and ignores any `ctts` composition
offsets. For a stream with B-frames the true presentation span is
`stts_sum + max(ctts_offset)`; the patched `tkhd`/`elst` will be short by the
reorder delay. This is a *timing* defect, not a count defect, but it is the
same ctts-blindness the design doc blames for the reader-level frame loss
(`docs/design/hardware_backend_design.md:214-215`).

`preservation/isobmf.py:240-381` — `patch_track_durations(path,
movie_timescale, from_stts=False)`. Docstring `:245-269`:

```
Fix tkhd/elst durations truncated or mangled by GPAC rewrites.
...
Hardware-backend intermediates (rigaya mp4, mvhd timescale 1000)
hit a second defect: GPAC rescales the imported video track's
timescale but NOT its mdhd duration, so the mdhd value itself is
garbage. With `from_stts=True` the content duration is taken from
the stts sum instead (always exact), ...
Only 4/8-byte duration fields are rewritten in place; no box size
or offset changes. ... Tracks with multi-entry or empty-edit edit lists
are skipped (reported), never guessed.
```

Key branches:
```python
if count != 1:
    if count > 1:
        patched.append(f"trak:{handler}: multi-entry elst skipped")
    elst = None          # isobmf.py:320-325
...
if media_time < 0:
    patched.append(f"trak:{handler}: empty edit skipped")
    continue             # isobmf.py:334-336
...
if from_stts:
    content = _stts_sum(f, trak)
    if content is None:
        patched.append(f"trak:{handler}: no stts, skipped")
        continue
    expected = round(content * movie_timescale / track_ts)   # :338-343
else:
    expected = round(
        (media_dur - media_time) * movie_timescale / track_ts)  # :345-347
```
then `tkhd` duration (`:349-364`) and the first `elst` entry duration
(`:366-380`) are patched.

`preservation/isobmf.py:384-440` — `patch_movie_duration(path)`: sets `mvhd`
duration to `max(tkhd durations)`.

**Call sites (`from_stts=True` on every hardware path):**
- `preservation/pipeline.py:312-320` (Sony, gated by `fix_hw_timing`;
  `True` from `core/batch_hw.py:764`)
- `preservation/audio_sync.py:49-53`
- `core/channel_sync.py:1005` → `repair_remux_timescale`
- `preservation/dji.py` (`fix_hw_timing=True` from `core/batch_hw.py:1238`)
- x265/SVT-AV1 paths pass `fix_hw_timing=False` (`1kt.py:631`, `1kt.py:839`)

### 6.3 Timescale handling

- `mvhd` TimeScale is read: `preservation/sony.py:210-216`,
  `preservation/validate.py:187-193`, `preservation/selfcheck.py:198-207`.
- `video_timescale` read at `sony.py:219-223`; both stored in the bundle
  (`sony.py:371-372`).
- Threaded into every mutating MP4Box call as `-timescale`
  (`preservation/gpac.py:110-113`, `_ts_args()`), with the module docstring
  `gpac.py:11-18` documenting that GPAC resets it on every rewrite.
- `mdhd` timescale is read to derive `expected` in `patch_track_durations`
  (`isobmf.py:300-309`).

### 6.4 Edit list (`elst`) treatment

- Parity check on durations: `validate.py:237-256`, `selfcheck.py:267-274`.
- Encoder priming shift in `MediaTime` is **explicitly tolerated**:
  `validate.py:239-247`:
  ```python
  if (src_v["elst"] and out_v["elst"]
          and src_v["elst"][0][1] != out_v["elst"][0][1]):
      mt_note = (f" (media_time {src_v['elst'][0][1]}->"
                 f"{out_v['elst'][0][1]}, encoder priming, compensated)")
  ```
  and `selfcheck.py:271-273` `"(encoder priming shift allowed)"`.
- `stts` is compared exactly and **is** fatal on the Sony path:
  `validate.py:232` `eq("video.stts", src_v["stts"], out_v["stts"])`, with
  `"video.stts"` in `critical_modified` (`validate.py:460`).

---

## 7. Encoder interface and the narrowest decoder injection point

### 7.1 The declared protocol — `encoders/base.py:43-80`

```python
class EncoderBackend(Protocol):
    """Contract every encoder backend must satisfy."""

    name: str

    # Encoder parameter namespace: profile JSON key -> parameter name,
    # in canonical order. Consumed by core.scaling.ScalingEngine.
    param_order: dict[str, str]

    def format_fixed(
        self,
        key: str,
        value: Any,
        fps: float,
    ) -> str | None:
        """Format a fixed (unscaled) value in this encoder's syntax."""
        ...

    def build_command(
        self,
        ffmpeg: Path,
        src: Path,
        part_dst: Path,
        profile: dict[str, Any],
        effective: EffectiveParams,
        video_stream_count: int,
        src_info: SourceInfo,
    ) -> tuple[list[str], dict[str, Any]]:
        ...
```

There is **no `build_video_command` and no `run_encoder` in the protocol.**
`build_video_command` is an undocumented *de-facto* extra method, implemented
only by the FFmpeg-family backends (`encoders/x265.py:282-300`,
`encoders/svtav1.py:231-249`) and called from `1kt.py:532` and `1kt.py:750`.
The hardware backends expose a **different, un-declared** interface:
`build_args` (`nvencc.py:93-125`, `qsvencc.py:91-122`), `command`
(`nvencc.py:127-145`, `qsvencc.py:124-142`), plus attributes `kind`, `caps`,
`codec`, `known` (`nvencc.py:76-91`, `qsvencc.py:74-89`).

So there are effectively **two disjoint backend protocols** in the tree, and
`EncoderBackend` documents only the FFmpeg one.

### 7.2 The narrowest injection point for a decoder abstraction

**Recommended: the reader-token assembly inside `build_args`.**
`encoders/nvencc.py:107` / `encoders/qsvencc.py:102`. The single literal
`"--avsw"` is the *entire* decode policy of the project. Replacing that one
token (e.g. with a reader chosen from a policy object) changes decode for all
three hardware paths at once, because every one of them funnels through
`backend.command()` → `build_args()`:

- `core/batch_hw.py:309` — `hw_encode_with_fallback`, used by Sony
  (`:692-702`), DJI (`:1135-1145`) and classic (`:924-934`);
- `:660`, `:909`, `:1112` — dry-run only.

**Nothing downstream needs to change.** Mux/preservation never see the flag:
the argv goes `build_args` → `command` → `hw_encode_with_fallback` →
`run_hw_tool`, and the preservation layer only ever receives a finished file
path via the injected `encode_video` callback
(`preservation/pipeline.py:83`, `:204`, `:220`; `preservation/dji.py:1229-1247`).

**Second-narrowest (execution-only) seam:**
`encoders/hw.py:140-146`:

```python
def run_hw_tool(
    cmd: list[str],
    raw_log_path: Path,
    total_frames: int,
    label: str = "hw",
    progress: bool = True,
) -> tuple[int | None, float]:
```

It receives a fully-built argv and does nothing but execute and parse — a
convenient place to *observe* decode behaviour (it could return the
encoder-reported frame count, currently discarded at `:217`) without
touching argv construction.

---

## 8. Risk list, ranked

### 8.0 First: the project's own documented root cause

`docs/design/hardware_backend_design.md` already recorded this exact bug:

- `:35` — "rigaya 硬解 reader（NVEncC avcuvid/avhw、QSVEncC avqsv）对 Sony
  elst+priming 素材**稳定丢帧**（A7M5 360→357，A7M4 195→193），`:noedit`、
  `--avsync forcecfr/vfr`、`--offset-video-dts-advance` 全部无效"
- `:36` — "**`--avsw` 软解在两工具上均帧精确**（360/360、195/195），是保留
  路径的强制默认 reader"
- `:192-208` — frame-accuracy matrix (A7M5, 360-frame baseline):

  | path | result |
  |---|---|
  | x265/FFmpeg baseline (production pipeline) | **360/360 ✅** |
  | NVEncC direct default (avcuvid) | 357/360 ❌ |
  | NVEncC `--avhw` | 357/360 ❌ |
  | NVEncC `-b 0` | 357/360 ❌ |
  | NVEncC `--offset-video-dts-advance` | 357/360 ❌ |
  | NVEncC MP4Box strip then hw decode | 357/360 ❌ |
  | NVEncC strip `:noedit` then hw decode | 357/360 ❌ |
  | NVEncC `--avsync forcecfr` / `vfr` | 357/360 ❌ |
  | NVEncC **`--avsw`** | **360/360 ✅** |
  | QSVEncC default (avqsv) | 357/360 ❌ |
  | QSVEncC strip then avqsv | 357/360 ❌ |
  | QSVEncC avqsv + `--avsync forcecfr` | 357/360 ❌ |
  | QSVEncC **`--avsw`** | **360/360 ✅** |

- `:210-215` — "A7M4（195 帧基准）：NVEncC cuvid 硬解 **193/195 ❌**（丢 2
  帧且不报错）... 结论：丢帧与 B 帧、elst、容器长度均无关，是 rigaya 共享
  reader 层对 Sony 结构（ctts/priming）的时间戳处理缺陷；**avsw 是帧精确的
  唯一路径**。"
- `:388` — risk register: "| 硬解 reader 丢帧 | 无解（工具层） | avsw 强制
  默认规避；NVEncC 升级后重测 |"
- `:123-124` — the pre-authorised optimisation: "可选优化（实施时按需启用）：
  先 avhw，`count_frames` 1:1 校验不过则自动重试 avsw —— 现有
  `encode_video` 的帧数/帧率校验天然是闸门"

**Verdict: NO hardware-decode-reachable path exists in the current source.**
The reported symptom therefore cannot be reproduced by the tree as written
unless either (a) an older/hand-patched build is in use, or (b) the
divergence comes from one of R1–R10 below.

### 8.1 DEFINITELY SOFTWARE-DECODE-ONLY, but can still change frame count

| # | Risk | Evidence | Why it diverges |
|---|---|---|---|
| **R1** | **`--avsync forcecfr` on VFR sources** | emit: `encoders/nvencc.py:123-124`, `encoders/qsvencc.py:120-121`; trigger: `core/batch_hw.py:82-88` `detect_vfr`, flag wired at `:310`, `:661`, `:695`, `:927`, `:1113`, `:1138` | CFR forcing **duplicates or drops** frames. The FFmpeg paths instead emit `-fps_mode passthrough` (`x265.py:201`,`:246`; `svtav1.py:149`,`:194`), which never dup/drops. Any source that trips `detect_vfr` therefore gets a **guaranteed hw-vs-software frame-count difference.** `detect_vfr` is a *string* compare of `r_frame_rate` vs `avg_frame_rate`, so it also trips on metadata-only mismatches. |
| **R2** | **DJI VFR: gate disabled while forcecfr still applies** | `core/batch_hw.py:1141` `do_frame_check=not vfr`; `preservation/dji.py:527-535` skips the post-hoc gate for VFR; but `:1138` still passes `vfr` into `command()` → `--avsync forcecfr` | Unbounded frame-count divergence with **no gate anywhere**, reported as success. The design even acknowledges it: `olddocs/backup/pre_ui_1kt/1keytransc.py:1671` "No 1:1 frame gate (VFR phone material must not...)". |
| **R3** | **Resume reuses a frame-count-rejected intermediate** | `hw_encode_with_fallback` never unlinks `output` on failure (`core/batch_hw.py:336-363`); `preservation/pipeline.py:198` `if not _encoded_ok(): encode_video(...)`, and `_encoded_ok` only needs ≥1 packet (`pipeline.py:175-196`) | Run 1: gate fails → RuntimeError, bad `encoded.mov` left on disk. Run 2: `_encoded_ok` is True → **the bad intermediate is reused, never re-encoded, and the 1:1 gate is never re-run**. Then `validate.compare` treats `video.frame_count` as non-critical (R5) → delivered, exit 0. Same hole in DJI (`core/batch_hw.py:1177-1186`), which at least unlinks when `_encoded_ok` is False. |
| **R4** | **Metric mixing in the raising gates** | `1kt.py:562-573` and `1kt.py:789-795`: `count_frames()` = F1 (`nb_read_packets`) vs `source_summary["total_frames"]` = F2 (`nb_frames`/`ceil(duration*fps)`, `probe.py:212-214`) | Compares two different quantities. Can **falsely raise** (duration×fps rounds up past the packet count) or **falsely pass**, depending on which F2 branch fired. Also affected by the `nb_read_packets or nb_frames` fallback inside `count_frames` (`probe.py:332`). |
| **R5** | **Sony frame count is non-fatal in `validate.compare`** | `preservation/validate.py:213-214` records it; whitelist `:452-477` omits `video.frame_count`/`video.frame_rate`; `structural_success` at `:482` | At the default `--check basic` (`1kt.py:992`), a Sony output with a wrong frame count is **MODIFIED-but-not-critical** → run completes, exit 0. Only `--check full` catches it, and only via the indirect selfcheck path (`selfcheck.py:284-285` → `:496` → `checker.py:108-118`). |
| **R6** | **Quality sample tolerates ±2 packets and SKIPs beyond** | `preservation/quality.py:285-297` | The only site that decodes **both** files is also the only site that tolerates divergence — and it degrades to SKIP, never FAIL, so `checker.py:150` never fires. |
| **R7** | **`_stts_sum` ignores `ctts`** | `preservation/isobmf.py:210-237`, used at `:338-343` | Patched `tkhd`/`elst` presentation duration is short by the composition reorder delay on B-frame streams. Timing defect (can trip `video.track_duration`/`video.elst`, which **are** critical) rather than a count defect — but same ctts-blindness the doc blames for the reader loss. |
| **R8** | **No `ctts` support at all in the byte patcher** | grep `ctts` in `preservation/isobmf.py`: NOT FOUND; only durations/colr/uuid are patched (`isobmf.py:264-267`) | If a whole-file `ctts`-aware repair were needed, the tooling does not exist. |
| **R9** | **`--video-track 1` is positional, not a track id** | `encoders/nvencc.py:107`, `encoders/qsvencc.py:102`; semantics documented `docs/design/hardware_backend_design.md:116-117` | For multi-video-track sources a different track than intended can be selected. Low risk for Sony single-track XAVC; relevant for DJI (cover track). Note `count_frames` also uses `v:0`, so the gate compares the same stream index the encoder used. |
| **R10** | **Encoder-reported frame count is discarded** | `encoders/hw.py:189,192` set `last_frame`; `:217` returns only `(return_code, elapsed)`. `core/batch_hw.py:239-242` keeps only the fps. `1kt.py:129` sets `current_frame`; `:175` returns only `(return_code, elapsed)` | The cheapest direct signal of decode-side drops (the reader/encoder's own frame counter) never reaches any gate. A trivial fix here would make R1–R3 self-detecting in-band. |

### 8.2 HARDWARE-DECODE-REACHABLE

**None.** Confirmed by §3.1: the only reader token emitted is the literal
`--avsw` (nvencc.py:107, qsvencc.py:102); no `--avhw`/`avcuvid`/`avqsv` key
exists in any `PARAM_MAP`; `nvenc.json`'s `avhw: true` is inert; no
`-hwaccel` anywhere; no CLI surface.

The moment §4.3 of the design doc (`hw_backend_design.md:123-124`) is
implemented as written ("先 avhw，`count_frames` 1:1 校验不过则自动重试
avsw"), the project will become hw-decode-reachable, and per the doc's own
matrix it will lose exactly **3 frames (A7M5) / 2 frames (A7M4)** — with the
1:1 gate as the only defence, and R3/R5 as the holes through which a rejected
result can still reach the output tree.

---

## 9. `-vsync` / `-fps_mode` / `-r` / `--avsync` / CFR forcing

**`-fps_mode passthrough`** (explicitly NO duplication/drop) — 4 sites:

- `encoders/x265.py:201` (`build_command`) and `:246` (`build_video_command`)
- `encoders/svtav1.py:149` and `:194`

**`--avsync forcecfr`** (CFR forcing — duplicates/drops frames) — 2 sites,
both gated on the `vfr` argument:

```python
# encoders/nvencc.py:123-124
if vfr:
    args += ["--avsync", "forcecfr"]
```
```python
# encoders/qsvencc.py:120-121
if vfr:
    args += ["--avsync", "forcecfr"]
```

`vfr` originates from `detect_vfr` (`core/batch_hw.py:82-88`):

```python
def detect_vfr(src_info: SourceInfo) -> bool:
    """VFR detection: r_frame_rate != avg_frame_rate (both non-empty)."""
    rfr = (src_info.r_frame_rate or "").strip()
    afr = (src_info.avg_frame_rate or "").strip()
    if not rfr or not afr or rfr in ("0/0", "N/A") or afr in ("0/0", "N/A"):
        return False
    return rfr != afr
```

and is logged at `core/batch_hw.py:446-453`:

```
"WARNING | VFR source detected (r_frame_rate=%s != "
"avg_frame_rate=%s) -> encoding with --avsync forcecfr "
"(nearest rational-rate CFR)",
```

**NOT FOUND:** `-vsync`, plain `-r`, `-async`, `-fps_mode cfr`, `-fps_mode vfr`,
`-drop_ts`, `-vsync drop`. The only `-r` occurrences are substrings of
`r_frame_rate` / `--ref` (`-r` is never emitted as a video-rate flag). The
only `-ar` flags are audio sample rate (`core/channel_sync.py:236`, `:932`).

**Asymmetry summary:** for a source that trips `detect_vfr`, the rigaya hw
paths force CFR while the FFmpeg software paths pass VFR through untouched.
This alone guarantees different frame counts between the two, and it is the
one CFR-forcing decision in the project.

---

## 10. Quick reference: file:line index of every decode-relevant site

| file:line | what |
|---|---|
| `encoders/nvencc.py:107` | `"--avsw"` — NVEncC reader token (decode policy) |
| `encoders/nvencc.py:106-110` | `build_args` flag block |
| `encoders/nvencc.py:123-124` | `--avsync forcecfr` |
| `encoders/nvencc.py:141-144` | full NVEncC argv |
| `encoders/nvencc.py:70-73` | dead `_SKIPPED_ALWAYS` (`avhw`) |
| `encoders/qsvencc.py:102` | `"--avsw"` — QSVEncC reader token |
| `encoders/qsvencc.py:120-121` | `--avsync forcecfr` |
| `encoders/qsvencc.py:138-141` | full QSVEncC argv |
| `encoders/hw.py:140-217` | `run_hw_tool` — execute + progress parse; returns rc/elapsed only |
| `encoders/hw.py:110-116` | rigaya progress/final-line regexes |
| `encoders/hw.py:368-395` | `plan_initial_format` — rigaya pix_fmt/chroma policy |
| `encoders/base.py:25-40` | `ffmpeg_pix_fmt` — FFmpeg pix_fmt policy |
| `encoders/base.py:43-80` | `EncoderBackend` Protocol |
| `encoders/x265.py:168-205`, `:231-250` | FFmpeg x265 argv (`-fps_mode passthrough`) |
| `encoders/svtav1.py:99-102` | `av1_pix_fmt` (forces 4:2:0) |
| `encoders/svtav1.py:116-153`, `:179-198` | FFmpeg libsvtav1 argv |
| `encoders/caps.py:9-13` | "decode capability is irrelevant by design" |
| `core/probe.py:212-214` | F2 `total_frames` |
| `core/probe.py:316-333` | `count_frames` (F1, `nb_read_packets`) |
| `core/batch_hw.py:82-88` | `detect_vfr` |
| `core/batch_hw.py:233-242` | `_last_encode_fps` (frame count discarded) |
| `core/batch_hw.py:277-279` | `plan_initial_format` call |
| `core/batch_hw.py:309-311` | `backend.command()` — production argv build |
| `core/batch_hw.py:332-334` | `run_hw_tool` — production execution |
| `core/batch_hw.py:336-363` | **hw 1:1 frame gate** |
| `core/batch_hw.py:446-453` | VFR / forcecfr warning |
| `core/batch_hw.py:698` | Sony `do_frame_check=True` |
| `core/batch_hw.py:930` | classic `do_frame_check=False` |
| `core/batch_hw.py:1141` | DJI `do_frame_check=not vfr` |
| `core/batch_hw.py:1342-1363` | `_encoded_ok` (≥1 packet) |
| `core/channel_sync.py:230-242` | ffmpeg audio decode (`-vn`) |
| `core/channel_sync.py:929-940` | ffmpeg audio re-encode |
| `1kt.py:88-175` | `run_ffmpeg` (frame= parsed, discarded) |
| `1kt.py:562-573` | Sony x265 frame-count raise gate (metric-mixed) |
| `1kt.py:789-795` | DJI x265 frame-count raise gate (metric-mixed) |
| `preservation/pipeline.py:175-196` | `_encoded_ok` closure |
| `preservation/pipeline.py:198-209` | resume-reuse decision |
| `preservation/pipeline.py:312-320` | `patch_track_durations` / `patch_movie_duration` |
| `preservation/quality.py:285-297` | ±2 tolerance → SKIP |
| `preservation/quality.py:303-308` | PSNR/SSIM graph (decodes both, `setpts=N`) |
| `preservation/quality.py:310-316` | ffmpeg double-decode command |
| `preservation/validate.py:213-214` | `video.frame_count` (non-critical) |
| `preservation/validate.py:452-477` | `critical_modified` whitelist |
| `preservation/validate.py:482` | `structural_success` |
| `preservation/selfcheck.py:284-285` | `nb_read_packets` note (counts as FAIL) |
| `preservation/selfcheck.py:496` | `overall = summary[FAIL] == 0` |
| `preservation/isobmf.py:210-237` | `_stts_sum` (ctts-blind) |
| `preservation/isobmf.py:240-381` | `patch_track_durations` |
| `preservation/isobmf.py:384-440` | `patch_movie_duration` |
| `preservation/gpac.py:110-113` | `_ts_args` — `-timescale` threading |
| `preservation/dji.py:526-535` | DJI frame gate (critical) |
| `preservation/dji.py:664-682` | DJI criticality + `structural_success` |
| `docs/design/hardware_backend_design.md:35-36`, `:119-126`, `:192-215`, `:388` | documented hw-decode frame loss + `--avsw` mandate |
