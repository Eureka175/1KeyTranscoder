> [!WARNING] 
> What has been written here earlier maybe somewhat misleading, and it is encouraged to read [this primer](https://gist.github.com/cynthia2006/24d994350685f3eee1ca014ecd9f5bfb) instead.

# Tenets of AV1 Encoding

AV1 is a next-generation video codec developed by Alliance of Open Media to facilitate VOD, storage and live-streaming. Usually paired with the [Opus](https://en.wikipedia.org/wiki/Opus_(audio_format)) audio codec and stored in WebM or Matroska container, or even MP4 ([ISOBMFF](https://en.wikipedia.org/wiki/ISO_base_media_file_format)) (and streamed using [HLS](https://en.wikipedia.org/wiki/HTTP_Live_Streaming)). As of now, besides libaom and rav1e, [SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1) is currently the best production quality encoder available (the matter of discusssion here). This introductory guide is based on the SVT-AV1 documentation.

## Presets

Presets (can be selected with `--preset` option) are a collection of predefined options that influence the speed vs. quality tradeoff, ranging from 0 to 10. Lower quality presets enable expensive algorithms or tune them for better compression, higher presets do the opposite for a given *rate control* (e.g. CRF or VBR). The uses cases of presets vary as per needs; if better compression is crucial (for VOD) and you have a good hardware, choose a lower preset **0-3**; if compression and speed are to be balanced choose **4-6**; if speed is critical (for streaming) choose **7-10**.

As mentioned by others, there are also negative presets (e.g. *preset -1*) which may serve those who're seeking maximal effeciency, even if in some cases that might turn out to be placebo (SVT-AV1 docs themselves refer presets below zero as debugging). It's worth mentioning that, below *preset 4* encoding speed starts to drop down dramatically. 

[This table](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md#what-presets-do) could be consulted if you need to know exactly which options are enabled at which preset.

## Rate Control

AV1 provides three means of rate control—CBR, VBR (enabled with `--rc 1`) or CRF (enabled with `--rc 0`, which is default); CRF is the most relevant, and occasionally VBR. CBR is recommended against of; don't use it, unless you have very special needs, and are aware of the direction you're steering towards.

### VBR

VBR is not often used when encoding AV1, but if you need to target a certain (average) bitrate, you can choose this mode of rate-control. To ascertain a certain filesize, multi-pass encoding is often recommended; it has to be enabled using `--pass 2` option, which generates a log-file in its first pass, containing information for the encoder to use in its second pass.

Option `--tbr` tunes the average bitrate (e.g. `--tbr=2m` for average 2 Mbit/s bitrate). It should be noted that, bitrate may deviate through the course of encoding, as the encoder attempts to roughly maintain an expected visual fidelity [^1] at that rate. 

### CRF

Whereas VBR tunes the **quantizer** to achieve a certain bitrate, CRF[^2] tunes it to achieve a certain visual quality.

### Quantiser

The quantiser is at the heart of lossy video compression. Largely oversimplifying, its primary job is to strip information (e.g. high frequency components of DCT) that wouldn't be perceptually noticeable to humans, thus saving bits that would otherwise be wasted. In SVT-AV1, quantiser values range from 1-61; higher is more aggressive, lower is the opposite.

Setting a CRF (aka. initial quantizer value[^3]) is done with `--crf` option. As a rule of thumb, higher CRF will likely use lower bitrate; a lower CRF use higher [^4]. Just as with other modern video encoders (e.g. x264), CRF is a logarithmic scale (i.e. lower values use dramatically high bitrates and consume more time to encode).

A CRF of 30-35 is highly recommended as a starting point for HD video, for 4K it can be lowered (but tests must be conducted to evaluate its effectiveness). CRF may be highered if it doesn't harm the desired visual fidelity levels (e.g. for 2D animated content).

## 10-bit Video
People often associate 10-bit with HDR, and worry about superfluous bandwidth usage—this is a misconception. Sure 10-bit is often used to make effective use of HDR, but neither 10-bit itself doesn't imply the use of HDR, nor does it dramatically increase filesize. Many other modern video codecs (e.g. VVC, EVC) use 10-bit internally, even if the input is 8-bit so as to avoid quantisation-related effects (e.g. banding) and rounding errors. Thus, it's almost always highly recommended to use 10-bit even if the video is 8-bit.

## GOP Interval 

GOP Interval (tuned by the `--keyint` option) specifies the interval of frames after which an intra-frame (i.e. keyframe) is inserted. Frequent keyframes lends itself to precise and fast seekability, but at the cost of reduced compression efficiency. It's often recommended to use 5-10 second GOP (0.1-0.2 keyframes/second, through `--keyint=5s` or `--keyint=10s`) interval for the normal use cases, unless your needs are special. For VOD it's recommended to use frequent keyframes (1 keyframe/second, tthrough `--keyint=1s`) for better network error resilience, and of course, seekability.

As per SVT-AV1 recommendation, GOP length over 300 is not recommended; but you may try and see how your mileage varies.

## Tuning

By default, SVT-AV1 tunes for PSNR (Peak Signal-to-Noise Ratio), a purely objective metric in assessment of video quality. This may not effect much, but using `--tune 0` option (tune for *visual quality*) is highly recommended, as it better models human perception of quality.

## Film Grain Synthesis

Films, especially [celluloid films](https://en.wikipedia.org/wiki/Celluloid), embody in themselves a lot of [film grain](https://en.wikipedia.org/wiki/Film_grain), which partly contributes to its old look-and-feel. This ambient optical effect is present prominently in restored films—in HD or UHD (4K). Video codecs often struggle to encode this non-deterministic noise. AV1 natively (i.e. regardless of encoder used) supports isolating and synthesizing this grain. It could be considered the video equivalent of [Comfort Noise Generation](https://en.wikipedia.org/wiki/Comfort_noise) by VoIP codecs like Opus.

At the encoding site, the grain is isolated and the video is denoised (if `--film-grain-denoise` is enabled). The characteristics of the grain is analysed, and deterministic information is encoded into the video bitstream. At the decoding site, this grain is synthesized as a post-processing step. 

In SVT-AV1, film grain synthesis is disabled by default, but it can significantly aid in quality of compression if film-grain is present in video, however at the cost of greatly increased compression overhead. See [the docs](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md#practical-advice-on-grain-synthesis) (and Wikipedia) for more details on this topic. As a general advice, setting the film-grain strength too high or enabling film-grain denoising (through `--film-grain-denoise` option) could **delete fine texture detail**. If you're re-encoding from a source which already has destroyed grain, then enabling this option might not help at all.

## Scene Change

> At present, SVT-AV1 does not insert key frames at scene changes, regardless of the `scd` parameter. It is, therefore, advisable to use a **third-party splitting program** to encode videos by chunks if key frame insertion at scene changes is desired.

The documentation further states that, AV1 is smart enough to act upon **scene changes**. That said, if you want maximal efficiency you should use [AV1AN](https://github.com/rust-av/Av1an), which is a third-party splitting program that inserts scene changes for you.

## Parallelism

SVT-AV1 is designed to be multithreaded for scalability. The `--lp` option (ranging from 0-6, default 4) is use to notify the encoder the level of parallelism wanted, which generally speeds up encoding at the cost of high memory usage. There are also options to tune CPU affinity (`--ss`) and execution pinning (`--pin`). Consult the [docs](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md#1-thread-management-parameters), for a description of its exact mechanics.

For greater degrees of parallelism AV1AN may be used as well, which uses a divide-and-conquer approach of splitting video into multiple scenes, then deploying multiple instances of SVT-AV1 to encode them, and finally gathering them in one place.

## See also

For more information, read the SVT-AV1 [FAQ](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md) to better understand the encoder. The following is a list of topics to peer into to unleash the full potential of the codec.

- [Variance boost](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Appendix-Variance-Boost.md) may help in increasing the quality of video by preserving fine details, that would otherwise be lost. However, this option is unlikely to contribute any significant quality improvements that might result with just lowering the CRF or increasing the VBR bitrate.
- `--luminance-qp-bias` (ranging 0-100) option maybe used to lower CRF as per average luminosity of the current scene, ensuring greater visual fidelity for dark scenes.
- [Temporal filtering](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Appendix-Alt-Refs.md) is recommended to look into, especially if your input is noisy.

For the audio part, take a look at the recommended settings for [Opus](https://wiki.xiph.org/Opus_Recommended_Settings).

[^1]: Deviations in quality maybe be controlled using `--max-qp` (maximum quantizer) and `--min-qp` (minimum quantizer); both parameters ranging, 1-63 (range of the quantiser itself). By default `--min-qp` is 1 and `--max-qp` is 63, which means to achieve a certain bitrate, the encoder may deviate the quantiser as it may like. However, it's not recommended to tune this values without further knowledge and experimentation. 
[^2]: For SVT-AV1, CRF is an alias to `--rc 0 --aq-mode 2 --qp <crf>` option set. `--aq-mode 2` means based on the CRF value, the quantizer continue to adapt. 
[^3]: With `--aq-mode 0` the quantiser value doesn't adapt, and it's then known as the CQP mode. Some claim this increases quality in scenes with high-degrees of motion, but that is largely debatable.
[^4]: A soft upper-bound can be set on the bitrate using the `--mbr`, but it has been pointed out to be largely unreliable.