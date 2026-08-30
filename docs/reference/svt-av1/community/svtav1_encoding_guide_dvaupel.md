# SVT-AV1 Encoding Guide

## Open source encoders
- AOMEnc[^4]. Developed by AOM, reference encoder with most features and highest quality.
- SVT-AV1. Developed by Intel, production ready encoder with high performance and optimized for parallelism.
- Rav1e. Developed by Mozilla/Xiph, used by Vimeo[^1].

Since all these tools are stil being actively developed, you should always use the newest versions and compile the 
standalone encoders yourself if necessary.

I choose SVT-AV1, because it has the best speed/quality tradeoff with my middle-grade CPU.

### Auxiliary tools
- Av1an. Cross-platform wrapper for modern encoders with convenient extras and performance boosts, like enhanced
  multithreading, stopping/resuming encodes, VMAF quality settings.
- StaxRip[^6]. Windows GUI for advanced encoding.
- NEAV1E[^3]. Windows GUI encoder.
- nmkoder[^2]. Windows GUI for encoding and analysis.


## SvtAv1EncApp Parameters

Official parameter documentation:

https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/svt-av1_encoder_user_guide.md

The following rules of thumb come from personal tests and external sources like [the AV1 subreddit](https://www.reddit.com/r/AV1/).

### --crf {1-63}
In the default rate control mode (`--rc 0`), this is the main parameter controlling the visual quality of the output.
The encoder tries to assure a constant quality regardless of the resulting bitrate. A lower CRF means higher quality and
bigger file size.

Rule: A CRF of 30 is a good starting point and is roughly equivalent to x265's CRF of 21.

### --enable-overlays {0,1}
Enabling improves quality of keyframes, leave always on.

Note: Currently (v0.9.0) this leads to flickering in my tests, be careful.

### --enable-restoration {0,1}
todo

### --enable-tf {0,1}
Disable/enable temporal filtering of alt ref frames.

Disabling (`--enable-tf 0`) can preserve slightly more detail, but increases file size.

### --film-grain {0-50}
Enables film grain synthesis. The encoder denoises the source (higher value means stronger denoising) and saves noise
parameters in a look-up table. The decoder can later use that table to recreate the grain during playback. This reduces
the necessary bitrate drastically for grainy footage.

This parameter should match the source material's graininess. But after experimenting with it, I personally came to the 
conclusion that it currently works best with lightly to moderately grainy material. Needs improvement.

### --input-depth {8, 10}
The bit depth in which the video gets processed. Should generally set to 10 bit to reduce banding and other artifacts.

Note that this does not change the bit depth, only tells the encoder what the source is. If you want to create 10 bit 
encodes from 8 bit sources, you must convert the source with ffmpeg (see below).

__Problem__:In my tests, 10 bit lead to stuttering in some scenes. Playback performance is still a problem.

### --keyint {int}
Specifies maximum distance between keyframes. Smaller intervals makes seeking faster, but bigger intervals reduce file size.

General rule: keyint = 10 * framerate, e.g. 240 for 24 fps.

### --preset {0-13}
Speed and efficiency of the encoding. 

Rough estimates: 8 is like x265 medium, 6 is like x265 slow.

Encoding time changes drastically with each step. Here are the results of some quick tests with a 30 second, 1080p, 24 fps movie scene with grain synthesis (not representative). 

| preset | encoding time / s | file size / MB |
|--------|-------------------|----------------|
| 3 | 781 | 10,4 |
| 4 | 340 | 10,6 |
| 5 | 231 | 10,6 |
| 6 | 146 | 10,8 |
| 7 | 115 | 10,9 |
| 8 | 109 | 10,8 |

![svtav1_presets_time](https://user-images.githubusercontent.com/85832165/151469770-156f5da4-47d7-4aa0-8766-c955a49f3623.png)

Rule: Reasonable time/quality ratios are in the range 4-8, with 6 being a good starting point. >=8 is good for real time encoding (livestreaming), <4 is rarely worth it.

### --scd {0,1}
Disable/enable scene change detection. Always good to have (unless you use constant bitrate).

## Other tricks

### Pipe from FFMPEG into SvtAv1EncApp
The SVT encoder only accepts uncompressed Y4MPEG streams (`*.y4m`). If your file is in another format, you can decode it
with FFMPEG.
```
ffmpeg -i input.mp4 -pix_fmt yuv420p10le -f yuv4mpegpipe -strict -1  - | SvtAv1EncApp -i stdin ...
```

### Change framerate
Svt encoder can't change the framerate itself. Its `--fps` flag is only an internal hint for rate control (I think).
Instead, we can use ffmpeg's `fps` filter and pipe the stream with the desired fps into the encoder.
```
ffmpeg -i in.mp4 -vf fps=fps=30 -strict -1 -f yuv4mpegpipe - |
SvtAv1EncApp -i stdin --fps 30 --keyint 300 <other options...> -b out.ivf
```


## Current problems
- 10 bit playback performance is not reliable enough on average consumer hardware.
- AV1 tends to smooth video noticeably, even at high bitrates. This is especially appearant in scenes with rain, snow etc, where it is very hard to conserve details.
- Grainy movies are a weakpoint of AV1. Even with film grain synthesis enabled, it is very hard to achieve satisfying
  results. The following is the best we can do for now[^5]:
  ```
  SvtAv1EncApp --rc 0 --crf 20 --preset 3 --irefresh-type 1 --keyint 240 --input-depth 10 --enable-overlays 1
  --enable-tf 0 --enable-restoration 0 --film-grain <int>
  ```

At the moment (early 2022, SVT-AV1 v0.9.0) it is a fairly promising, but not perfect, codec. It is great for most regular content, achieves great quality at small file sizes and appearantly keeps its promise of being considerably more efficient than HEVC.

The only area where it must still improve is grainy, detailed movie scenes. With such high-bitrate, bluray-quality source material it's hard to achieve visual transparency. If grain synthesis has improved enough and smooth decoding is possible
in most devices, it can be generally recommended. For now, it is still in the late experimental phase.


[^1]: https://investors.vimeo.com/news-releases/news-release-details/vimeo-introduces-support-royalty-free-video-codec-av1
[^2]: https://github.com/n00mkrad/nmkoder
[^3]: https://github.com/Alkl58/NotEnoughAV1Encodes
[^4]: https://aomedia.googlesource.com/aom/
[^5]: https://www.reddit.com/r/AV1/comments/s04lcp/comment/hs10yoa/?utm_source=share&utm_medium=web2x&context=3
[^6]:https://github.com/staxrip/staxrip