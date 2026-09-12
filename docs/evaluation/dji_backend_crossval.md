# DJI 双后端多轮对正报告

> **归档说明（2026-09 文档梳理时入库）。** 本文档原在 `work/dji_xval/crossval_report.md`
> （`work/` 不入库）。之所以转存入库：其中的**逐轨载荷 SHA256 对正证据**
> （djmd/dbgi/tmcd 在 源 / NVENC / QSV / NVENC-重跑 四方一致）与 Gyroflow
> 四元数跨后端一致性数据，在 `docs/` 下没有等价记录。
>
> 报告引用的逐轮产物（`work/dji_xval/{nvenc_hq,qsv_hq,nvenc_hq_r2}/`）与驱动
> 脚本 `crossval.py` **未入库**——它们是编码产物与工作脚本。得出结论的**输入**
> 是 `testsets` 中 DJI 素材的字节级副本，可重建；复核本报告需按原流程重跑。

- 轮次: R1 nvenc_hq / R2 qsv_hq / R3 nvenc_hq_r2（均 --check full --keep-work，输入 dji_xval_in 副本）
- 结论前置: 见各 clip 小节; 数据轨三方 sha256 一致、Gyroflow 四元数跨后端一致、视频层 SSIM/PSNR 量化差异。

## DJI_20260830095031_0009_D.MP4

### 1. 数据轨载荷 sha256（源 vs 三份成品，须全部一致）

| 轨 | 源 | NVENC | QSV | NVENC-r2 | 一致 |
|---|---|---|---|---|---|
| djmd | 7de441fb33be… | 7de441fb33be… | 7de441fb33be… | 7de441fb33be… | ✅ |
| dbgi | afba0f768fe8… | afba0f768fe8… | afba0f768fe8… | afba0f768fe8… | ✅ |
| tmcd | 2694a91356d8… | 2694a91356d8… | 2694a91356d8… | 2694a91356d8… | ✅ |

### 2. Gyroflow type-3 逐帧四元数（NVENC vs QSV vs 源）

- `org_quat`: 样本数 105 | NVENC==源 ✅ | QSV==源 ✅ | NVENC==QSV ✅
- `stab_quat`: 样本数 105 | NVENC==源 ✅ | QSV==源 ✅ | NVENC==QSV ✅

### 3. 视频层客观指标（源 vs 各后端，10bit 对齐）

| 对比 | SSIM All | PSNR dB |
|---|---|---|
| 源 vs nvenc_hq | 0.98189 | 43.49 |
| 源 vs qsv_hq | 0.97925 | 42.27 |
| NVENC vs QSV | 0.98675 | 43.18 |

### 4. 成品非视频轨清单（三份成品一致）

```
{
  "nvenc_hq": [
    [
      "meta",
      "dbgi",
      105
    ],
    [
      "meta",
      "djmd",
      105
    ],
    [
      "soun",
      "mp4a",
      164
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ],
  "qsv_hq": [
    [
      "meta",
      "dbgi",
      105
    ],
    [
      "meta",
      "djmd",
      105
    ],
    [
      "soun",
      "mp4a",
      164
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ],
  "nvenc_hq_r2": [
    [
      "meta",
      "dbgi",
      105
    ],
    [
      "meta",
      "djmd",
      105
    ],
    [
      "soun",
      "mp4a",
      164
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ]
}
```

## DJI_20260830095040_0010_D.MP4

### 1. 数据轨载荷 sha256（源 vs 三份成品，须全部一致）

| 轨 | 源 | NVENC | QSV | NVENC-r2 | 一致 |
|---|---|---|---|---|---|
| djmd | e905b49aa61d… | e905b49aa61d… | e905b49aa61d… | e905b49aa61d… | ✅ |
| dbgi | 7df62b33ce63… | 7df62b33ce63… | 7df62b33ce63… | 7df62b33ce63… | ✅ |
| tmcd | b00b50b7ee01… | b00b50b7ee01… | b00b50b7ee01… | b00b50b7ee01… | ✅ |

### 2. Gyroflow type-3 逐帧四元数（NVENC vs QSV vs 源）

- `org_quat`: 样本数 330 | NVENC==源 ✅ | QSV==源 ✅ | NVENC==QSV ✅
- `stab_quat`: 样本数 330 | NVENC==源 ✅ | QSV==源 ✅ | NVENC==QSV ✅

### 3. 视频层客观指标（源 vs 各后端，10bit 对齐）

| 对比 | SSIM All | PSNR dB |
|---|---|---|
| 源 vs nvenc_hq | 0.99396 | 47.73 |
| 源 vs qsv_hq | 0.99175 | 45.42 |
| NVENC vs QSV | 0.99405 | 46.30 |

### 4. 成品非视频轨清单（三份成品一致）

```
{
  "nvenc_hq": [
    [
      "meta",
      "dbgi",
      330
    ],
    [
      "meta",
      "djmd",
      330
    ],
    [
      "soun",
      "mp4a",
      258
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ],
  "qsv_hq": [
    [
      "meta",
      "dbgi",
      330
    ],
    [
      "meta",
      "djmd",
      330
    ],
    [
      "soun",
      "mp4a",
      258
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ],
  "nvenc_hq_r2": [
    [
      "meta",
      "dbgi",
      330
    ],
    [
      "meta",
      "djmd",
      330
    ],
    [
      "soun",
      "mp4a",
      258
    ],
    [
      "tmcd",
      "tmcd",
      1
    ]
  ]
}
```

## 轮次汇总

| 轮次 | 范围 | 结果 |
|---|---|---|
| R1 | NVENC HQ, check=full | 2/2 PRESERVED=26 MODIFIED=0 MISSING=0, Gyroflow PASS |
| R2 | QSV HQ, check=full | 2/2 同上 |
| R3 | NVENC 确定性重跑, check=full | 2/2 同上（数据轨 sha256 与 R1 一致） |
