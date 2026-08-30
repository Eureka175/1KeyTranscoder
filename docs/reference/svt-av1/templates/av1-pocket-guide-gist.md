# Source: https://gist.github.com/ankushian/a22862c6f92a51574e1720d1d392941d
# Fetched: 2026-08-29 17:10:29 +08:00

# Tenets of AV1 Encoding

AV1 is a next-generation video codec developed by Alliance of Open Media to facilitate VOD, storage and live-streaming. It's usually paired with Opus audio codec, stored in MP4 ([ISOBMFF](https://en.wikipedia.org/wiki/ISO_base_media_file_format)) or streamed using [HLS](https://en.wikipedia.org/wiki/HTTP_Live_Streaming) (HTTP Live Streaming). SVT-AV1 is currently the best production quality encoder available (the matter of discusssion here).

## Presets

Quoting the SVT-AV1 documentation:

> Presets control how many efficiency features are used during the encoding process, and the intensity with which those features are used. Lower presets use more features and produce a more efficient file (smaller file, for a given visual quality). However, lower presets also require more compute time during the encode process. If a file is to be widely distributed, it can be worth it to use very low presets, while high presets allow fast encoding, such as for real-time applications.
>
> Generally speaking, presets **1-3** represent extremely high efficiency, for use when encode time is not important and **quality/size of the resulting video file is critical**. Presets **4-6** are commonly used by home enthusiasts as they represent a **balance of efficiency and reasonable compute time**. Presets between **7 and 13** are used for **fast and real-time encoding**. 
>
> *One should use the lowest preset that is tolerable.*

Presets can be selected using `--preset` option. It's encouraged to choose presets based on the usage. [This table](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md#what-presets-do) could be used as a reference to what preset implies what.

It should be noted, that highest quality presets (0-3) use features that aren't well parellizable, which leads to lower than expected performance on multi-core CPUs, supporting high number of threads.

## Rate Control
AV1 provides three means of rate control—CBR, VBR or CRF; of which the most relevant is CRF, and ocassionally VBR. CBR could be completely ignored.

### CRF

One of the most important parameters (ranging 1-61) to tune is CRF (using `--crf` option), which adapts the quantizer to ascertain a target quality. Higher CRFs use lower bitrates; lower CRFs use higher. Just as in other modern video codecs (e.g. x264), CRF is a logarithmic scale. 

A CRF of 35 is recommended as a starting point for HD video (1080p); can be tuned as per need. And, if needed an upper bound of the bitrate can be set using `--mbr` (e.g. `--mbr=1500k` to limit bitrate to 1.5 Mbit/s).

## GOP Interval 
GOP Interval (tuned by the `--keyint` option) specifies the interval of frames after which an intra-frame (i.e. keyframe) is inserted. Frequent keyframes (e.g. keyframe inserted after each second) lends itself to precise and fast seekability, but at the cost of reduced compression efficiency. It's often recommended to use 5-10 second GOP (through `--keyint=5s` or `--keyint=10s`) interval for the normal usecase; but your mileage may vary. For VOD it's recommended to use frequent keyframes (`--keyint=1s`) for better error resilience and seekability.

It can be longer, if your needs are special (e.g. video content barely changes); however, it's never a good idea to prolong the GOP to stupendous intervals.

## Film Grain Synthesis
Films, especially [celluloid films](https://en.wikipedia.org/wiki/Celluloid), embody in themselves a lot of [film grain](https://en.wikipedia.org/wiki/Film_grain), which partly contributes to its old look-and-feel. This ambient optical effect is present prominently in restored films—in HD or UHD (4K). Video codecs often struggle to encode this non-deterministic noise. AV1 natively (i.e. regardless of encoder used) supports synthesizing this grain. While encoding, the characteristics of the grain are analysed; video is denoised and sent down the encoding pipeline. The extracted deterministic information about grain present in video are encoded into the video bitstream. At the decoding site, this grain is synthesised as a post-processing step. It could be considered the video equivalent of [Comfort Noise Generation](https://en.wikipedia.org/wiki/Comfort_noise) by VoIP codecs like Opus.

Film grain synthesis is by default disabled. And in pratice, most of the times you wouldn't even care about grain preservation; so it's better left disabled.

## Scene Change

Quoting the SVT-AV1 documentation:

> At present, SVT-AV1 does not insert key frames at scene changes, regardless of the `scd` parameter. It is, therefore, advisable to use a **third-party splitting program** to encode videos by chunks if key frame insertion at scene changes is desired.

The documentation further states that, AV1 is smart enough to act upon scene changes. That said, if you want maximal efficiency you should use [AV1AN](https://rust-av.github.io/Av1an/Cli/scene_detection.html#split-method---split-method), which is the *third-party splitting program* that inserts scene changes for you.

## Variance Boost

Videos that we encounter on a daily basis, often contain low-contrast fine textures and scenes. These are poorly handled  by encoders, and as a result yield a blocky mess, especially for dark videos[^1]. In its worst, the textures are heavily quantized resulting in a coalesced superblock of single color (average of the pixels in that region).

Variance is the ratio of high to low constrast for a given 8x8 block of a 64x64 superblock. **Variance boost** (enabled through `--enable-variance-boost=1` option) lowers the strength of quantization for that 8x8 block to encode the fine details. The strength of this boost could be adjusted (using `--variance-boost-strength`) as needed for quality gain in certain circumstances.

| Strength | Usage |
|--------------------------|------------|
| 1 | Line art animation, devoid of texture. |
| 2 (default) | Live-action, and animation with texture. |
| 3 | Live-action with scenes of both high and low contrast. |
| 4[^2] | Live-action with scenes mostly dark. |

The option `--variance-octile` tunes the selectivity of a 8x8 block for variance boost. Lower values than 4 use superfluous bitrate, while higher values than 7 yield visually unappealing results. It's best to leave the option at 6 (default).

## Parallelism

SVT-AV1 is designed to be multithreaded for scalability. The `--lp` option (ranging from 0-6, default 4) is use to notify the encoder the level of parallelism wanted, which generally speeds up encoding at the cost of high memory usage. For exact mechanics, see the [docs](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md#1-thread-management-parameters).

## Pointers

Curious users are advised to read the SVT-AV1 documentation—especially the [FAQ](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md)—to better understand how it functions. The following is a list of topics to peer into to unleash the full potential of the codec.

- [Super resolution](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md#super-resolution) is worth looking into; allows video to be internally (within AV1) encoded at a lower horizontal resolution and reproducing the original resolution at the decoder side.
- [Lookahead](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md#gop-size-and-type-options) is also worth tuning, which increases the effectiveness of several coding optimizations but costing increased memory.
- Enabling **Quantisation Matrices** (through `--enable-qm` option) maybe beneficial as per the SVT-AV1-PSYp project.

[^1]: https://www.youtube.com/watch?v=h9j89L8eQQk
[^2]: This option uses superfluous bitrates for not much perceptual gain.
