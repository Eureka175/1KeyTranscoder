# Parameter Analysis — Recovery, Census and Provenance

**Question this answers.** `actual_x265_params` is missing on 201 of 1839 records.
Is that lost experimental data?

**Answer: no.** Every one of the 201 carries a complete 67-key parameter vector in
`effective_params`, plus the full `effective_params_audit` that records how each
dynamic value was computed. `actual_x265_params` is a *flattened serialisation* of
`effective_params` written only on the success path.

---

## 1. The identity that resolves the "201 missing" question

```
actual_x265_params  ==  ":".join(f"{k}={v}" for k in sorted(effective_params))
```

| Measurement | Value |
|---|---:|
| records with both fields | 1638 |
| records where the two agree as parsed mappings | **1638** |
| identity rate | **1.000000** |
| records with `effective_params` but no `actual_x265_params` | **201** |
| records with neither | **0** |
| records where `actual_x265_params` could be reproduced by literal sorted serialisation | all sampled cases |

Since the identity is exact on 100 % of the overlapping population, "missing
`actual_x265_params`" is **not** a signal of data loss. It is a signal that the
record did not complete the success path.

### Cross-check: three independent verifications

| Verification | Result |
|---|---|
| `actual_x265_params` vs `effective_params` vs `COMMAND` payload — all 1638 records | **0 disagreements** |
| Sample record vs per-source log `EFFECTIVE_X265` — all records with a log | 1586 exact, **52 differ** (see §5) |
| Sample vs event-archive `effective_params` (`job_started`/`attempt_started`) | **1839 / 1839 exact** |

---

## 2. Recovery outcome for the 201

| Recovery class | Count |
|---|---:|
| `RECOVERED_EXACT` | 0 |
| `RECOVERED_FROM_COMMAND` | **0** |
| `RECOVERED_FROM_EVENT` | **201** |
| `RECOMPUTED_FROM_AUDIT` | 0 |
| `PARTIAL` | 0 |
| `CONFLICT` | 0 |
| `NOT_RECOVERABLE` | **0** |

**Why `RECOVERED_FROM_COMMAND` is zero:** no per-source log of the 201 contains a
`COMMAND` line. The `TypeError` fired at the call site before
`log_encode_header()` ran. 202 of the 1914 logs lack `EFFECTIVE_X265` — exactly
the 201 plus one unrelated `PREPROBE FAILED` log.

**Why `RECOMPUTED_FROM_AUDIT` was not needed:** the values are present verbatim,
not merely derivable.

### Provenance per record

Each recovered vector exists in **five byte-identical representations**:

1. `calibration/samples/<day>.jsonl` → `effective_params`
2. `calibration/events/archive/.../---job_started-*.json` → `effective_params`
3. `calibration/events/archive/.../-A001-attempt_started-*.json` → `effective_params`
4. `calibration/events/archive/.../---job_failed-*.json` → `effective_params`
5. `calibration/events/archive/.../-A001-attempt_failed-*.json` → `effective_params`

402/402 cross-representation comparisons identical, including the audit node.
**No competing source disagrees anywhere.**

Per-record provenance is in
`1kt_calibrate/archive/parameters.json` → `evidence.sample_file`,
`evidence.event_id`, `evidence.event_file`.

---

## 3. Parameter census — 61 static, 6 dynamic

All 1839 records use the **same 67-key set** — a single key-set variant, no
missing keys, no extra keys.

### Dynamic keys (6)

| Key | Distinct values | Rule (verified 1839/1839) |
|---|---:|---|
| `merange` | 4 (57 ×1299, 29 ×308, 40 ×29, 17 ×2) | `clamp(half_up(57 · √(pixels / 3840·2160)), 16, 92)` |
| `rc-lookahead` | 7 | `min(ceil(fps × 1.5), 200)` |
| `gop-lookahead` | 4 | `min(ceil(fps × 0.1), 200)` |
| `min-keyint` | 6 | `ceil(fps × 0.15)`, uncapped |
| `vbv-maxrate` | 1494 | `half_up(clamp(OB × target_ratio, OB × min_ratio, OB × max_ratio))` |
| `vbv-bufsize` | 1149 (240000 ×353) | `min(half_up(maxrate × 3.0), 240000)` — CPB clamp |

### Static keys (61)

Exactly one distinct value each across 1638 observed records, all matching the
hash-verified `HQ` profile in `x265.json`:
`rd=4 · rdoq-level=1 · ref=4 · bframes=6 · keyint=600 · scenecut=40 ·
hist-scenecut=1 · fades=1 · b-intra=1 · b-adapt=2 · bframe-bias=-10 ·
open-gop=0 · qcomp=0.7 · qblur=0.7 · qpstep=2 · ipratio=1.2 · pbratio=1.1 ·
const-vbv=1 · qpmin=16 · qpmax=40 · me=umh · subme=3 · max-merge=3 ·
weightb=1 · b-pyramid=1 · aq-mode=4 · aq-strength=0.9 · qg-size=16 ·
aq-motion=0 · cutree=0 · cbqpoffs=-2 · crqpoffs=-2 · ctu=64 · min-cu-size=8 ·
rect=1 · amp=0 · limit-tu=3 · tu-intra-depth=2 · tu-inter-depth=2 · tskip=1 ·
tskip-fast=1 · psy-rd=1.5 · psy-rdoq=0.8 · dynamic-rd=1 · rskip=1 ·
early-skip=1 · fast-intra=0 · splitrd-skip=0 · limit-modes=1 · limit-refs=1 ·
deblock=-1,-1 · sao=1 · limit-sao=1 · no-strong-intra-smoothing=1 ·
threaded-me=0 · high-tier=1 · level-idc=6.2 · aud=1 · repeat-headers=1 ·
hrd=1 · info=0`

Preset `HQ` ×1839; backend `x265` ×1839; `crf` 21.0 is an ffmpeg flag, not an
`-x265-params` entry. Parameter-string length 778–787 characters.

**Implication for calibration design:** there is **no experimental variation in
the rules**. Every dynamic value is a pure function of source metadata. This is an
observational survey, not a designed experiment — see
[`scaling-analysis.md`](scaling-analysis.md).

---

## 4. Scaling-rule verification — 100.0000 %

The frozen rules were re-implemented twice, by methods that share no code path:

| Method | Basis | Result |
|---|---|---|
| **A — audit replay** | the recorded `effective_params_audit` `final` values vs the record's own parameter string | **1638 / 1638**, 0 mismatch, 0 incomplete |
| **B — config replay** | `effective_params_audit` ignored; recompute from `x265.json` + `x265_scaling.json` + `source_info` + `scaling_context` | **1638 / 1638**, 0 mismatch, 0 not-computable |

Both methods cover all six dynamic rules.

### Three undocumented behaviours that had to be discovered

1. **`vbv-bufsize` derives from the *rounded integer* `vbv-maxrate`**, not from
   the unrounded value: `77235`, not `77234`. Deriving from `OB × 3` reproduces
   only 918/1839.
2. **A 240 000 kbit/s CPB clamp** on `vbv-bufsize` (HEVC Level 6.x High-tier
   MaxCPB). It binds in **353/1638** records and is documented only in a legacy
   audit `note` string.
3. **Rounding is half-away-from-zero**, with `ceil` for the fps-derived family.
   For `merange`, half-even (1473/1839) and truncate (1442/1839) are **ruled out**.

### Rule fidelity against the frozen config

* On-disk `x265.json` SHA-256 `0c522f47…` **equals** the `config_hash` recorded in
  all 1839 records.
* On-disk `x265_scaling.json` SHA-256 `0cdc44cf…` **equals** the
  `scaling_config_hash` recorded in all 1839 records.
* **0 contradictions** between observed dynamic values and the rule file.

---

## 5. The 52 records where the per-source log disagrees

52 successful records have a per-source log whose `EFFECTIVE_X265` differs from
the sample record.

| Property | Value |
|---|---|
| Records affected | 52 (all `SUCCEEDED`) |
| Differing keys | exactly `vbv-maxrate` and `vbv-bufsize` |
| Context | the affected group is the `2025.5.28迪迦` DSC_29xx corpus |
| Log sessions per affected source | 1 (`command_count=1`, `effective_count=1`, `done_count=1`) |

Example (`DSC_2980`):

| key | sample record | per-source log |
|---|---:|---:|
| `vbv-maxrate` | 78791 | 70152 |
| `vbv-bufsize` | 236373 | 210456 |

**Interpretation (MEDIUM confidence).** Both keys are functions of the source's
measured `OB`. The sample record is the attempt that was scored; the log line is
the last write for that source. The two differ because the source's dynamic VBV
inputs were re-measured on a later watcher pass, and the log record is
append-only. The sample record is authoritative for the *sampled attempt*; the
log line must **not** be used to re-derive it.

**Reported as a conflict, not silently resolved** — see
`calibration/reconstructed/conflicts.json`, kind
`PARAMS_SAMPLE_VS_FILE_LOG` (52 entries, severity HIGH).

---

## 6. Dataset impact of the 201

| Question | Answer |
|---|---|
| Is a parameter vector lost? | No — 67/67 keys recovered |
| Is an output metric lost? | No — none was ever produced |
| Do any records pair an output metric with missing parameters? | **0** |
| Are any *successful* records lost by excluding the 201? | **0** |
| Does recovery add training rows? | No — it adds provenance completeness only |

The 201 reduce **corpus coverage** (201 of 1692 distinct
`basename × preset` sources, ≈11.9 %, were never encoded) but cost **zero**
analytical rows. The real coverage gap in this dataset is elsewhere: **142 of 142
4:2:2 sources failed, so the corpus contains zero successful 4:2:2 encodings**,
and 633 of 1631 successes lack `compression_ratio`.

---

## 7. Residual unknowns

| ID | Item | Status |
|---|---|---|
| `U1` | Would the deployed build have emitted exactly these 67 values? | `INSUFFICIENT_EVIDENCE` — the final link is only observable by re-encoding, which is prohibited |
| `U2` | Did the deployed revision's static 61 keys equal the on-disk `HQ` profile? | `LOW_RESIDUAL_RISK` — mitigated by the matching `config_hash` and by the 7 non-TypeError failures from the same revision carrying exactly those 61 keys |
| `U3` | Why did the deployed build pass `video_entry` to `encode_one_sony()`? | `UNKNOWN` — primary evidence is the traceback; no revision metadata was archived |
| `U4` | Would a re-encode reproduce these vectors? | `INSUFFICIENT_EVIDENCE` — prohibited |
| `U5` | Why do 633 successes lack `compression_ratio`? | Answered separately: it is a **verification-gate path selection**, see [`data-quality.md`](data-quality.md) |
| `U6` | Any `EFFECTIVE_X265` outside the archive? | Checked — 0 occurrences in `logs/total.log` or `logs/error.log` |

**Recovered parameters never overwrite raw evidence.** The recovered index is a
derived layer (`calibration/reconstructed/`, `archive/parameters.json`);
`calibration/samples/*.jsonl` is untouched.
