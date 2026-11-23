# 1KeyTranscoder v0.9.15 发布说明

> 2026-11-20 · 输出格式默认策略变更 + 手动覆盖选项

## 新行为

### 1. 所有输出默认 10-bit

不再跟随源文件色深：8-bit、10-bit、12-bit 源默认都编码为 **10-bit**。
这使六条编码路径（NVENC HEVC / QSV HEVC / NVENC AV1 / QSV AV1 / x265 /
SVT-AV1）的输出位深一致。

- 8-bit → 10-bit 是能力归一化，不按降级告警；
- 12-bit → 10-bit 记录为 `depth_normalized_to_10`，属于有损归一化。

### 2. 色度采样默认跟随输入

- 4:2:0 源 → 默认 4:2:0；
- 4:2:2 源 → 默认 4:2:2（NVENC HEVC / x265 支持时保留）；
- 4:4:4 源 → 默认 4:4:4（x265 支持时保留）；
- AV1 三后端仍然 4:2:0-only，非 4:2:0 源能力降级到 4:2:0/10 并告警；
- QSV 4:2:2 仅在 caps 声明支持时保留，否则降级到 4:2:0/10。

### 3. 新增手动覆盖选项

```powershell
# 强制 8-bit 输出
python 1kt.py --input IN --output OUT --encoder x265 --output-depth 8

# 强制 4:2:0
python 1kt.py --input IN --output OUT --encoder x265 --output-chroma 420

# 强制 10-bit + 4:2:2
python 1kt.py --input IN --output OUT --encoder nvenc --output-depth 10 --output-chroma 422
```

- `--output-depth {8,10,12}`
- `--output-chroma {420,422,444,mono}`

显式覆盖会按用户指定值交给后端；后端不支持时由编码器显式失败，
不再静默改回自动策略。

## 实现位置

- `encoders/format_policy.py`：默认 10-bit、色度跟随输入、手动覆盖；
- `1kt.py`：新增 CLI 选项，启动时配置策略；
- NVENC/QSV/x265/SVT-AV1 全部经同一策略，日志仍是
  `[FORMAT-PLAN]` / `FORMAT_PLAN`。

## 验证

- x265 8-bit 源 → `yuv420p10le`；
- x265 4:2:2 源 → `yuv422p10le`；
- NVENC HEVC 8-bit 源 → `yuv420p10le`；
- QSV HEVC 8-bit 源 → `yuv420p10le`；
- SVT-AV1 8-bit 源 → `yuv420p10le`；
- QSV HEVC 4:2:2 源（本机 caps 422=False）→ `yuv420p10le`；
- NVENC HEVC 4:2:2 源 → `yuv422p10le`；
- 手动 `--output-depth 8` → `yuv420p`；
- 手动 `--output-depth 12` → `yuv420p12le`；
- 手动 `--output-chroma 422` → `yuv422p10le`；
- 单元测试：1131 PASS / 2 环境性 FAIL（testsets 语料缺失）。

## 已知限制

- AV1 后端不支持 4:2:2/4:4:4；手动指定这些色度时编码器会报错；
- 12-bit 输出需要后端支持（x265 支持；硬件 HEVC 12-bit 视 caps）；
- 本机 QSV caps `422=False`，无法验证 QSV 4:2:2 直编路径。
