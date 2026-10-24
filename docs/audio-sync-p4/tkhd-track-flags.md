# tkhd 轨道启用标志 / alternate group（生产容器契约）

> 状态：已合入生产路径（2026-10-25）。触发问题：交付 MP4 在 Premiere 中只显示
> **一条音轨**，MediaInfo 却正常列出四条。

## 1. 现象与根因

| 检查项 | Sony 源 | ffmpeg 多音轨输出（修复前） |
|---|---|---|
| 每轨 `tkhd` flags | **7**（enabled \| in_movie \| in_preview） | 视频/首音轨 3，后续音轨 **2（未启用）** |
| `alternate_group` | **0** | **1 .. N**（所有音轨互相"备选"） |

ISO/IEC 14496-12 把 `alternate_group` 定义为**同一轨的备选版本**（典型用法是
多语言音轨）；播放器/编辑器按规范**只选其中一条**。Premiere 严格执行这条语义，
因此把四条单声道 PCM 当成"一条可切换音轨"；MediaInfo 不解释该语义，仍按物理轨
列四条 —— 缺陷只在 NLE 里可见。

同一个 `alternate_group=1` 也出现在 GPAC（MP4Box `-new`）的多音轨输出上（实测
`[(7,1),(7,1)]`），所以不是 ffmpeg 独有的问题；任何"多音轨单声道 PCM"成品都需要
这一步。

## 2. 修复

`preservation/isobmf.py`：

```python
track_flags(path)              # 读取每轨 tkhd flags / alternate_group
normalize_track_flags(path)    # 原地归一: flags |= 0x7, alternate_group = 0
```

- **流式**：只按 box header seek，读到的 `moov` 子头即可，`mdat` 从不读入内存；
- **只改 4 个字节/轨**：`tkhd` 的 version/flags（24 bit）与 `alternate_group`
  （16 bit），文件大小不变；
- **幂等**：已归一文件返回空列表、不写盘；
- 未知 `tkhd` version 时跳过并给出描述（不猜布局）。

## 3. 生产接入点

最终成品写出后调用（全部覆盖，顺序在容器结构完全落定之后）：

| 路径 | 位置 |
|---|---|
| Sony 保留管线 | `preservation/pipeline.py` step 5e |
| 透明模式（Sony 元数据恢复） | `preservation/transparent_restore.py` 末尾（dst） |
| channel-sync 透明重封装 | `core/channel_sync.py` 最终 dst（含无 restore 分支） |
| classic 路径音频替换 | `preservation/audio_sync.py` `remux_replace_audio` 末尾 |
| DJI 重建 | `preservation/dji.py` 最终 output.mov |
| 硬件路径容器重封装 | `core/batch_hw.py` `remux_container_lossless`（返回描述含 `tkhd: N track flag(s) normalized`） |

说明：`ffmpeg` 仍然只承担**编码器（x265）/解码 / wrapper** 角色；软件不实现切片
功能，切片属于外部工序。此修复只是容器事实恢复，不改变任何媒体 payload。

## 4. 回归

`tests/selftest/suites/container_flags.py`：

- **L1**（纯 Python，无外部工具）：构造 `moov` 在 `mdat` 前/后两种布局、`tkhd`
  v0/v1，验证解析、归一、幂等、文件大小与 `mdat` payload 字节不变；
- **L2**（toolchain）：真实 ffmpeg / MP4Box 生成多音轨文件，验证归一后
  `flags=7 / alt=0`、音轨数不变、音频 streamhash 不变。

注册于 `tests/selftest/runner/engine.py`：
`unit: "tkhd 轨道标志/alternate group (PR 多音轨)"`、
`toolchain: "tkhd 轨道标志 (真实 muxer 输出)"`。

实测结果：

| 输入 | 归一前 | 归一后 |
|---|---|---|
| ffmpeg 多音轨 | `[(1,3,1),(2,2,1)]` | `[(1,7,0),(2,7,0)]` |
| MP4Box 多音轨 | `[(7,1),(7,1)]` | `[(7,0),(7,0)]` |
| `remux_replace_audio` 输出 | — | 全部 7/0 |
| `remux_container_lossless` 输出 | `[(3,0),(3,1)]` | `[(7,0),(7,0)]`，返回描述含 `tkhd: 2 track flag(s) normalized` |

回归基线：`--level unit` **930 PASS / 0 FAIL**；`--level toolchain`
**953 PASS / 1 FAIL**（唯一失败为 `caps.nvenc 实机探测`，本机 N 卡未接，属环境
性、与本次改动无关）。

## 5. 手工工具（外部产物）

对软件之外产生的 MP4/MOV（例如手工 ffmpeg 切片），仍可用
`tests/regression/long_align_tools/fix_tkhd.py <file>...` 做同样修补，并可用
`--revert` 回退（原值保存在 `<file>.tkhd.json`）。
