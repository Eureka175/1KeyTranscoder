# HEVC 4:2:2 输出与 Range Extensions (Rext) 播放兼容性

> 定位：归档优先（保真 > 兼容）。本文档明确 4:2:2 源在 HEVC 编码后的
> 容器形态、播放边界与验证手段。2026-08-29 更新，实测数据来自
> A7M4 XAVC-S 4:2:2 10bit (C9037) 全流程验证。

## 1. 为什么输出是 Rext

HEVC 的 Main / Main 10 profile 只定义 4:2:0 色度采样。**4:2:2 与 4:4:4
编码必须走 Range Extensions（Rext）profile**，这不是降级或异常，而是
规范上的唯一形态：

| 源 | 后端策略 | 输出形态 |
|---|---|---|
| XAVC-S / XAVC S-I 4:2:2 10bit | NVENC 保 4:2:2 | `hvc1, profile=Rext, yuv422p10le` |
| 同上 | QSV 政策转 4:2:0 | `hvc1, profile=Main 10, yuv420p10le` |
| 4:2:0 源（XAVC-HS 等） | 两端一致 | `hvc1, profile=Main 10, yuv420p10le` |

实现细节：`nvenc.json` 声明 `profile: main10`；当命令带
`--output-csp yuv422` 时 NVENC 自动把 main10 提升为 Rext（实测确认，
无报错）。`--experimental-multihw` 的静态路由把 4:2:2 源固定发给
NVENC，QSV 机器（无 NVENC）则按政策转 4:2:0 并在日志中显式 WARNING。

## 2. 播放兼容边界（实测 + 公开资料，需按目标设备复核）

| 环境 | Rext 4:2:2 10bit HEVC | 说明 |
|---|---|---|
| FFmpeg / VLC / mpv | ✅ 完整支持 | 本工具 `--check full` 的 Gyroflow 消费端校验即走此路，已 PASS |
| NVIDIA 50 系 (Blackwell) 硬解 | ✅ | Blackwell NVDEC 新增 4:2:2 HEVC 解码 |
| Windows 系统播放器 | ⚠️ 多数不行 | HEVC 扩展主要覆盖 Main/Main10 4:2:0；需实测 |
| macOS / QuickTime | ⚠️ 需实测 | VideoToolbox 4:2:2 支持随芯片/系统版本而异 |
| 智能电视 / 手机 / 平板 | ❌ 大概率不支持 | 移动 SoC HEVC 硬解基本止步 Main10 4:2:0 |
| 剪辑软件 (DaVinci/Premiere) | ⚠️ 需实测 | 取决于内置解码后端；Premiere 在 NVIDIA 上可走 NVDEC |

**结论**：Rext 成品是"归档母本"，不承诺跨设备直放。需要分发时，
用 QSV 后端（4:2:0）或对成品做一次按需转码即可。

## 3. 程序侧保障

- 静态路由：`--experimental-multihw` 下 4:2:2 源 → NVENC（保真路径）；
  纯 QSV 环境走 4:2:0 并打 WARNING（`capability-driven downgrade:
  4:2:2/10 -> 4:2:0/10`）。
- 验证：`--check` 的 validate/selfcheck 断言成品为 HEVC（hvc1/hev1），
  对 Rext 与 Main10 一视同仁；`--check full` 的 Gyroflow 消费端校验
  用 FFmpeg 软解复核整条时间线，等价于一次独立解码验证。
- 审计：postprobe CSV 与 `SOURCE_FORMAT` 日志记录 profile/pix_fmt，
  可事后核对任一成品的实际形态。

## 4. 归档建议（推荐操作）

1. 4:2:2 素材归档：NVENC 后端（默认行为，保留 4:2:2 Rext）。
2. 交付前抽验：对 Rext 成品跑 `ffprobe -select_streams v:0` 确认
   `profile=Rext` 与 `pix_fmt=yuv422p10le`，再用 VLC/ffmpeg 各解码
   验证一次。
3. 需要给"看片端"（电视/手机）一份时：另出一份 QSV 4:2:0 副本，
   不要动归档母本。

## 5. 与色彩元数据的关系（同批修复）

4:2:2 Rext 成品同样写全 colr（color_primaries/transfer/matrix/range，
HLG/HDR10 时含 atc-sei/mdcv/clli），与 Main10 路径共用同一套色彩
信令逻辑（`core/color.py`）。
