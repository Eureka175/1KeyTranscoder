### Current aomenc recommended settings for sharpness and detail retention:

```
--cpu-used=4 --cq-level=16 --end-usage=q --lag-in-frames=48 --enable-fwd-kf=1 --aq-mode=1 --deltaq-mode=0 --enable-chroma-deltaq=1 --quant-b-adapt=1 --enable-qm=1 --min-q=1 --enable-keyframe-filtering=0 --arnr-strength=1 --arnr-maxframes=4 --sharpness=3 --enable-dnl-denoising=0 --disable-trellis-quant=0 --threads=64
```

#### General CQ guidelines:
- 40: Youtube quality
- 30: Watchable, fine for streaming or lower bitrate encodes
- 20: Good quality
- 16: Very good quality
- 12: Visually lossless unless you pause and zoom in 4x

Recommended to subtract 2 from the cq level if encoding HDR content

#### Conditional settings
- Add `--tune-content=screen` if encoding a recording of your PC screen (this includes both presentations and games)
- Change `--enable-keyframe-filtering` to `2` if you're not worried about the fact that it has the potential to break playback or seeking on certain players.
- If using av1an, you probably want to set ` --disable-kf` to disable scenecut detection (since av1an does scenecut for us), unless you have a special use case where you know you want aomenc's extra splits.

#### If using Blue's aom-psy fork:
- Add `--tune-content=psy` for live action or `--tune-content=animation` for anime
  - Personally I find that treating 3D CGI (e.g. Pixar) as "live action" is the better option, it does not have the same patterns as anime or cartoons, has much more motion and detail.
- Add `--tune=butteraugli`
  - The latest version of aom-psy supports butteraugli for 10-bit content. If you cannot use butteraugli for some reason, `ipq` is a fallback option.
- Change `--deltaq-mode` to `5` if encoding HDR content
- Increase `--lag-in-frames` to `64`, `96`, or `120` (higher = more RAM usage, `120` might cause problems for av1an users with a lot of workers. `120` is the max in the forked aomenc.)
  - Multiples of 32 are good because they provide optimal memory efficiency. A group of frames is 32 frames, so aomenc wants to load in a whole group at a time. Going from e.g. 64 to 65 will increase memory usage by considerably more than e.g. 63 to 64.
- `--enable-keyframe-filtering=1` may be used, as it has been fixed in the aom-psy fork. (If you're already using `--enable-keyframe-filtering=2`, then that is still a bit more efficient than `1`.)

### Grain synth advice

It's advised to use grain synth to reduce banding even on content with no grain.

Based on the source material:
- zero grain (cgi, digital cartoons, etc) => grain synth 4-8
- slight grain (digital movies) => grain synth 8-14
- medium grain (film movies) => grain synth 14-26
- heavy grain (old film movies) => grain synth >26

### FAQs

- Why `--cpu-used=4`?
  - My opinion is that `4` is the best balance of speed and quality. If you need to go faster, you can use `6` but understand compression will take about a 15% hit (it's still much better than x264). If you don't mind encodes taking longer, you can go to `3` which does help a bit with detail preservation. Probably not useful unless you have a high quality source. The gains from going to speeds 2 and below are not generally worth the extra time cost.
- Why disable keyframe filtering?
  - The reason for not using `--enable-keyframe-filtering=1` (which is the default) is that it has a long-standing bug which can cause [very bad quality drops](https://bugs.chromium.org/p/aomedia/issues/detail?id=3211) in certain high motion scenes.
  - Note that this has been fixed on the aom-psy fork.
- What do each of the settings do?
  - `lag-in-frames` is equivalent to lookahead in x264/x265, it helps make decisions for temporal bitrate allocation e.g. giving more bits to parts of the video that are visible for longer.
  - `enable-fwd-kf` is the equivalent of Open GOP
  - `aq-mode=1` is variance-based spatial AQ. In the psy fork, it also has a low luma bias to help preserve detail in dark areas.
  - `deltaq-mode` is a form of temporal AQ. chroma-deltaq is intended to be the same for the chroma planes, but it's currently quite naive (equivalent of x264 chroma qp offset).
  - `enable-qm` enables quantization matrices. This gives a huge compression improvement at no speed cost. It should really be the default, but it's not for some reason. The default qm-min and qm-max are ideal, going lower than 5 for qm-min begins to produce worse video quality.
  - `min-q=1` prevents the encoder from going into lossless mode, which is currently Very Buggy, so we want to avoid it.
  - `arnr-strength=1` reduces the strength of Alt-Ref Frame Filtering. The default strength filters quite heavily. We lower this to 1 to retain more detail in the encode. (Some members will advocate for disabling it completely. My personal opinion is that there are still issues with rate distribution when ARNR is disabled that make me prefer to keep it enabled.)
  - `sharpness` is a pseudo-Psy-RD setting that allocates more bits to sharp areas of the video to prevent them from blurring. Either 2 or 3 seem to be the sweet spots. Higher than that begins to reduce quality of flat areas of the video by too much.
  - `--enable-dnl-denoising=0` disables denoising when using grain synthesis. It does nothing if you're not using grain synthesis, but I put it here for those who do use it.
  - `--disable-trellis-quant=0` does exactly what it sounds like and enables trellis-based quantization. This can help with detail retention.
  - `--threads=64` lets aomenc use *up to* 64 threads. The default is 1 thread, so you always want to set this, even if using av1an or even if not using tiles. There are still e.g. lookahead threads that you do not want to be bottlenecked waiting on the one encoder thread, and I've found that this does produce a speedup. Unfortunately aomenc only has one `threads` setting, there is no option currently to control lookahead threads separately.