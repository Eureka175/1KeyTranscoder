# AV1 MP4 `stts` data track write failure

## Symptom

Classic AV1 (`svtav1`) encoding of a source with a Sony timed-metadata track fails
before the first frame is written:

```text
[mp4 @ ...] Could not find tag for codec none in stream #2,
            codec not currently supported in container
[out#0/mp4 @ ...] Could not write header (incorrect codec parameters ?)
```

The source stream is reported by ffprobe as:

```text
index=2 codec_type=data codec_name=none codec_tag_string=stts
```

## Cause

The classic command maps every input stream and copies data tracks:

```text
-map 0 ... -c:d copy
```

FFmpeg's **MP4 muxer** has no sample entry for a data stream with
`codec_name=none` / `codec_tag_string=stts`, so it cannot write the output
header. This is a container/stream-mapping failure, not a scaling or encoder
failure: it occurs before any AV1 encode work and is independent of
`--resize` / `--scale`.

The classic AV1 intermediate is MP4 because FFmpeg's MOV muxer does not accept
AV1. Sony/DJI preservation paths use a video-only intermediate and re-attach
metadata from the preservation bundle, so they are not affected by this path.

## Fix

Classic software paths now share one stream policy regardless of codec:
primary video + audio + subtitles. Data and attached-picture streams are
dropped for both x265 and SVT-AV1 (`drop_metadata_streams=True`).

x265 previously kept this external-URL data track because its intermediate is
MOV; AV1 cannot, because FFmpeg only supports AV1 in MP4 and the MP4 muxer
cannot represent the track. Keeping codec-specific stream mapping made the two
classic paths behave differently, so both now drop the same streams.

Sony/DJI preservation paths are unaffected: they use a video-only intermediate
and re-attach metadata from the preservation bundle.

Verified on the affected 4K source: x265 and AV1 outputs now both contain
video + audio only, with the same dimensions and frame count.
