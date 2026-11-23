# 1KeyTranscoder v0.9.18 发布说明

> 2026-11-21 · 修复高码率 4K60 ALL-I 素材 NVENC 初始化失败

## 问题

Media Encoder 导出的 4K 59.94fps HEVC Main10 4:2:0 ALL-I 素材（约 500 Mbps）转码时，
NVENC 在初始化阶段失败：

```text
Max B frames are 5 frames.
nvenc : Error on nvEncInitializeEncoder: 8 (Invalid Level.)
Failed to create encoder
```

同一错误在 `--avhw` 和 `--avsw` 两种读端都会出现，VLC 能正常打开源文件，
因此不是源文件损坏或解码失败。

## 根因

`nvenc.json` 四个挡位都静态写了 `"level": "6.1"`。同时，NVENC 动态 VBV
按源码率推导出：

- `max_bitrate = 352624 kbps`
- `vbv_bufsize = 528936 kbps`

HEVC Level 6.1 high tier 的最大码率上限是 240 Mbps，352 Mbps 超出上限，
所以 NVENC 判定 Level 非法并拒绝初始化。普通相机素材码率较低，未触发；
这批 ALL-I 外录素材码率异常高，必然触发。

## 修复

移除 `nvenc.json` 中静态的 `"level": "6.1"`，让 NVENC 根据分辨率、帧率和
动态 VBV 自动选择合法 Level（本场景自动选择 6.2）。不改变编码质量参数、
不改变码率目标，只修复非法 Level 组合。

验证：同一 `C1727.mp4`（4K 59.94，50.55s）在修复后：

- `--preset fast`：成功；
- `--preset uhq`：成功，输出 159.5 MB，耗时约 290s。

## 影响

- 高码率 4K60/ALL-I 素材不再因 Level 6.1 失败；
- 普通素材仍由 NVENC 自动选择合适 Level；
- QSV / AV1 / x265 配置未改。
