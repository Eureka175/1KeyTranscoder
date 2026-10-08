# P4 — provenance

**Identity of everything P4 produced, and of everything P4 relied on.**

The P3 line's reason for pinning provenance applies with more force to P4: the
implementation P3 relied on lives under gitignored `work/`, so a hash of it proves
*which bytes* produced a result and **does not preserve them**. P4's answer is
structural rather than documentary:

```text
P4's implementation lives in TRACKED code (core/audio_sync_tv.py)
P4's tests live in TRACKED code (tests/audio_sync_p4/)
P4 imports NOTHING from work/
```

Everything under `work/p4/` is **re-derivable evidence**, not a dependency. If
`work/` were deleted, P4's module, its regression suite and its reproduction
targets (the P3.11 `dst_sha256` values) would survive — the latter because they
are quoted in `evidence-freeze.md`, a tracked document.

---

## 1. Repository identity

| item | value |
|---|---|
| repo | `F:\1KeyTranscoder` |
| branch | `research/audio-sync-p3-production-semantics` |
| **P4 base commit (HEAD at P4 start)** | `8bb229764f551955db2c392e4b2bee6bfd7e9418` |
| base commit subject | `docs(audio): consolidate and freeze the p3 research documentation` |
| upstream | **none** — the branch is in no remote ref |
| tags | `v0.8.0` only; **unmoved** |
| `VERSION` on disk | `0.8.1` (uncommitted; not changed by P4) |
| pushed / merged / tagged by P4 | **NO / NO / NO** |

### 1.1 P3 phase identity P4 inherits

| phase | HEAD at phase start | provenance doc |
|---|---|---|
| P3 base | `2e96f7399fd3545fdd08f5315183db7aef7faac5` | `docs/audio-sync-p3/provenance.md` |
| P3.10 | `0e3000abca26dc77a24b097435893433a5bdd888` | `p3.10-provenance.md` |
| P3.11 | `48ef2aee7ca8e4a690f14213fdf9b0d2de1b7eed` | `p3.11-provenance.md` |
| P3.12 | `d9182b4b8107c4129a6a8bba6b8a328604a13f2a` | `p3.12-provenance.md` |

---

## 2. P4 implementation files (tracked)

| file | bytes | SHA-256 |
|---|---:|---|
| `core/audio_sync_tv.py` | 44 909 | `2B66AE4EB4396384A6BB57742E35BB9E98D62B7457AA71AC055A485563D501A0` |
| `tests/audio_sync_p4/test_production_sync.py` | 58 351 | `78523D57801F91F798D4139E720F93F560B09CF048B555B5057842F98D41A952` |

**Algorithm version:** `2.4.0-p4` (`tv.ALGO_VERSION`).

**Policy-contract version:** the gap-policy set is
`{"hold", "no_correction_gap", "explicit_state", "current", "linear", "next"}`
with `GAP_POLICIES` (offline/online) and `GAP_POLICY_NEEDS_ENDPOINTS`.
Changing either mapping changes the contract and must bump the algorithm version.

**Coordinate-contract version:** `COORDINATES = ("latest-start",
"nearest-centre", "sample-domain")`, dispatched only in `window_index()`.
`sample-domain` is an alias of `latest-start` by construction.

---

## 3. P4 evidence files (under gitignored `work/p4/`)

Re-derivable, **not** dependencies.

| file | bytes | SHA-256 | produces |
|---|---:|---|---|
| `current-state-audit.md` | — | — | Stage 0 audit |
| `verify_p311_reproduction.py` | 15 964 | `691C8F8B32A44900CA1D5DE69EE3257045A48D330853BA83043F246CB0203181` | V2/V3 |
| `memory_and_performance.py` | 25 322 | `879520DCD636FE17847C43AA9872FECD8BA5CDF147E05B15CD698FE24E0DD278` | V6–V8 |
| `p311-reproduction.json` | — | — | 30 per-arm results |
| `memory-performance.json` | — | — | memory/perf report |
| `hash_verify.py` | — | — | V10–V12 |

---

## 4. Frozen configuration — asserted, not assumed

```text
sample rate    48 000 Hz, float32, 1 channel
window          1 440 000 samples = 30.0 s     (tv.DEFAULT_WINDOW_SAMPLES)
hop               480 000 samples = 10.0 s     (tv.DEFAULT_HOP_SAMPLES)
search                   ±3 000 samples = 62.5 ms (tv.DEFAULT_SEARCH_SAMPLES)
residual grid     5 s window, 10 s hop (aligned to the correction block boundaries)
ratio             W = 3*hop
```

P4 asserts these as **named constants with a test** (`T10.8`) rather than passing
them around as literals, because P3.8 recorded that conflating `window_samples`
and `hop_samples` caused an out-of-bounds read.

`P4-HANDOFF.md` §2 and `HANDOFF-CHECKLIST.md` §3.2 both forbid changing the window
or the hop inside an integration. **P4 changed neither.**

Two independent confirmations of the geometry:

```text
P3 asserted: measure_resumable.py asserts n_win == 1_440_000, hop == 480_000,
             search == 3000
P4 asserts:  test_production_sync.py T10.1-T10.5, T10.8
seam rule:   P3.11 states "2 when n mod hop == 0, 3 otherwise"
             P4 derives the same rule from ceil(n/hop) - (floor((n-win)/hop)+1)
             and reproduces 3 / 3 for the two corpora (T10.6) and 2 for a
             multiple of hop (T10.7)
```

---

## 5. Corpora

Read-only in every P4 run. Hash-verified **before each comparison** in V2.

| corpus | channel role | bytes | SHA-256 |
|---|---|---:|---|
| C1305 | ch1 (target) | 923 002 080 | `A1BEFEFCBE9A1AFC6621DC941C4914E1E7A66D10FC065ABA397353D38CD6221F` |
| C1305 | ch2 (target) | 923 002 080 | `31DF1572AEEB4020D68AF6DE092146A496434991ABB542D8BD11EE2E55860E6B` |
| C1305 | **ch3 (reference)** | 923 002 080 | `360A9F4D61A37E68DFF67CEFDF9ABBF74D3D694C15272CABDC7DCCC9A814F462` |
| C1305 | ch4 (target) | 923 002 080 | `E70BBCBFFF4904B4C25B24FEC23D554B804CC913166EC3AD5D72BC76A19A46ED` |
| C1306 | ch1 (target) | 799 614 816 | `313EB8D37744CBFAC5950F86484A539068B9BFFE9E323ECC80EE5CB53A5D0F47` |
| C1306 | ch2 (target) | 799 614 816 | `8B11A078AAD73EF8DB93DC57416864BC765502CB039264EA6D5A1A4745C0F266` |
| C1306 | **ch3 (reference)** | 799 614 816 | `3970DD19B83DDD2D68B2D1F51D833EA9B686DEF85139C5697ABBD9BCF3B629A6` |
| C1306 | ch4 (target) | 799 614 816 | `3E468FAC4BC0349856A100FD01C82825E74E4E876ABDDD4A0E4948BBFD65312E` |

`n mod hop`: C1305 → **350 520**; C1306 → **223 704**. Both non-zero, so both take
the seam = 3 case.

---

## 6. The reproduction anchors

The six `latest-start` × `hold` corrected-PCM hashes, recorded by P3.11 and
**reproduced byte-for-byte by P4** (V2). These are the strongest P4 evidence,
because the expected value was produced by **different code, in an earlier
session**.

| Corpus | Pair | bytes | SHA-256 (P3.11 recorded, P4 reproduced) |
|---|---|---:|---|
| C1305 | ch3→ch1 | 923 002 080 | `6E4D5A22D9E05CCD35352C41A7D6538BB22ECBBC7EC1933D75D9ACC8A3726E2E` |
| C1305 | ch3→ch2 | 923 002 080 | `30259ACDD7957A2E28860BB0D85599DC0A635D8DA3421CE0C7AB59019C1D25C4` |
| C1305 | ch3→ch4 | 923 002 080 | `413E0205BB5622EA838F98339BD6E0FDAE1235AFCE5B6BBE93C60747B5D9B20C` |
| C1306 | ch3→ch1 | 799 614 816 | `03F63C2388366CD4A2A640E0C655222805ADBB56B8DAE78F48B677934462FDDF` |
| C1306 | ch3→ch2 | 799 614 816 | `569B7D706686CE34110ABD321FE04B19938C9343610A7C3C8A4781F57B900ADC` |
| C1306 | ch3→ch4 | 799 614 816 | `642B1B2197F99DF4F6AEEB2569437E1F12D61E727D606E518E111A4553A3A4F4` |

`413E0205…` for C1305 ch3→ch4 is **the same hash P3.10 recorded**. P4 reproduces
it a third time, from different code — P3.10 → P3.11 → P4.

### 6.1 The coordinate-contract anchors

`nearest-centre` × `hold`, all six pairs, **also reproduced byte-for-byte**:

| Corpus | Pair | SHA-256 |
|---|---|---|
| C1305 | ch3→ch1 | `7FF130893DCCB1BEC75BE8802622BC8B3309DCCB10653DC2DAE1B999260935F9` |
| C1305 | ch3→ch2 | `32AF26A90454144994187DDBEFC278DFAC591B02D666CBA02ABBF30DD1B0E90D` |
| C1305 | ch3→ch4 | `7C27E252B6D093E27CBDA19246AA91A76DDE4A6474FF835BBD34DA72310C5601` |
| C1306 | ch3→ch1 | `04D8E1EFEF71705FBC43C7779ACF9A87FE69BD4B67C920E7A2BE7B72AF36E648` |
| C1306 | ch3→ch2 | `BBCD8FC33FE6FBA2E3DE2CE946199DD5D288934607C7FBE9509E1DED6FCAD2B3` |
| C1306 | ch3→ch4 | `925EB432803AAEB1596AE893DC9209B2C3F6F671F4126EC05B9419470C484ACF` |

**Both coordinate contracts reproduce.** That is what makes D2 a *declared
parameter* rather than a rewrite.

### 6.2 The gap-policy anchors

`current`, `explicit_state`, `no_correction_gap` × `latest-start`, 18 further
arms — all byte-exact. Notably:

```text
explicit_state reproduces hold's hash exactly on every pair
    -> P3.11's "bit-identical PCM, only the claim differs" is confirmed by P4
       from a different implementation
```

---

## 7. Files P4 must NOT have changed — hash re-verification

Verified at **P4 end** (V10/V11). All 12 match the values P3 recorded.

### 7.1 The nine user-owned tracked modifications

| file | SHA-256 | at P4 end |
|---|---|---|
| `1kt.py` | `4CAB92CA9509C163A1F213898D060AE7CD42BCB4611A4EC9B941E55DC54AD857` | **MATCH** |
| `VERSION` | `AAF6721DC37C40B4233ACCFC081F25776FF3BD1D2A4B9D5CEA760D1C2CF86665` | **MATCH** |
| `core/batch_hw.py` | `C40A51BE1A8F12A366FF7F01300892D62E3D96AEE20C409D2B1AA54FE1DCB43B` | **MATCH** |
| `preservation/checker.py` | `E54A012675FD66BBE0760A769D8F862565B168EC1B6B9D8B2229412089428C42` | **MATCH** |
| `preservation/gpac.py` | `C9EE74764E05A1AC00C6D0FE7103934384EF0CBD311DFDD5B4D9E261770825E1` | **MATCH** |
| `preservation/validate.py` | `6F7B8F9E82CD8F16CA8D46C06C24B48869C3DF6795F40FE165EB532A875A5EFF` | **MATCH** |
| `tests/selftest/runner/engine.py` | `DBD8D5775983D3623C9DAC1199BBE4F9A780F8DF3D491231776288E8EE045CD8` | **MATCH** |
| `tests/selftest/suites/core.py` | `DEE050C35B3CDFE9AE9750783D792ECEDB853755E57A81DAE9AA65AE3ACD1824` | **MATCH** |
| `tests/selftest/suites/toolchain.py` | `BD765F60F61E60A339A502FCDC5F09D1907D8F6664EAD38C6E082844DB917205` | **MATCH** |

### 7.2 The three P1 production files

| file | SHA-256 | at P4 end |
|---|---|---|
| `core/sync_fix.py` | `6D3F75B403C56CAF78190BFCFEA3954ABC7CD15E11B6737B833E6080CF8F79C2` | **MATCH** |
| `core/channel_sync.py` | `14283AB7215FF592E8ADD766963195DB303408F5BF0A70E3526B20D28ADE1BAB` | **MATCH** |
| `core/sync_estimate.py` | `773C96A746DFFFE7A2A1852B059AF51B0FD51A84CAEA182E5A22403D31BDDF3E` | **MATCH** |

**A match here is the default-path guarantee in its strongest form:** the bytes
that implement P1's validated correction are unchanged, so P1's behaviour cannot
have moved.

### 7.3 Scoped claim, re-verified rather than quoted

`HANDOFF-CHECKLIST.md` §3.3.3 retires *"`core/` is unmodified"* as an
unconditional statement. P4 uses the scoped form and **re-ran the command**:

```text
git status --porcelain core/
 M core/batch_hw.py          <- user-owned, not P4's, not touched
```

So the correct P4 statement is: **`core/batch_hw.py` is modified (user-owned);
every other file P4 touched or depended on in `core/` is either new and tracked
(`audio_sync_tv.py`) or byte-identical to HEAD.**

---

## 8. What P4 added, and what it did not touch

### 8.1 Added (all tracked)

```text
core/audio_sync_tv.py
tests/audio_sync_p4/test_production_sync.py
docs/audio-sync-p4/README.md
docs/audio-sync-p4/evidence-freeze.md
docs/audio-sync-p4/policy-matrix.md
docs/audio-sync-p4/coordinate-decision.md
docs/audio-sync-p4/gap-policy-decision.md
docs/audio-sync-p4/residual-acceptance.md
docs/audio-sync-p4/implementation.md
docs/audio-sync-p4/validation.md
docs/audio-sync-p4/production-readiness.md
docs/audio-sync-p4/policy-recommendation.md
docs/audio-sync-p4/provenance.md
docs/audio-sync-p4/P4-FINAL-REPORT.md
docs/design/architecture.md        (additive section only)
README.md, docs/README.md          (status lines only)
```

### 8.2 Added (under gitignored `work/p4/`)

```text
current-state-audit.md
verify_p311_reproduction.py
memory_and_performance.py
hash_verify.py
p311-reproduction.json
memory-performance.json
memory-performance-quick.json
```

### 8.3 NOT touched

```text
core/channel_sync.py, core/sync_fix.py, core/sync_estimate.py,
core/audio_plan.py, 1kt.py
every file under docs/audio-sync-p3/   (frozen history)
every file under work/p3/, work/p2/, work/archive/
every historical P3 report
any file P3 recorded as hashed evidence
any fixture, to make a test pass
any threshold
VERSION, and every git tag
```

---

## 9. Reproducing P4

From a checkout **with the real corpus present** (`work/p2/out/raw/*.f32`):

```powershell
# the regression suite -- runs from TRACKED CODE ONLY, no corpus needed
python tests/audio_sync_p4/test_production_sync.py

# the P3.11 byte-exact reproduction -- needs the corpus and work/p3/p311
python work/p4/verify_p311_reproduction.py --json work/p4/p311-reproduction.json

# memory, performance, long run
python work/p4/memory_and_performance.py --json work/p4/memory-performance.json

# hash re-verification of everything P4 must not have changed
python work/p4/hash_verify.py

# the regressions P4 must not have broken
python tests/full_autotest.py --level unit
python work/p2/scripts/p1_regression_guard.py
python tests/audio_sync_p311/test_coordinates.py    # needs work/, see note
```

**Note the asymmetry, because it is the point:** the first, second, third and
fourth commands run from tracked code. The fifth depends on `work/`, which is
gitignored — that is the D7 limitation, recorded and not closed by P4.

---

## 10. Provenance limitations, stated

```text
these hashes prove WHICH BYTES produced a result
they do not PRESERVE those bytes

P4's module and tests are TRACKED, so they survive a work/ deletion
P4's evidence scripts under work/p4/ do NOT
P3's implementation does NOT (OPEN-DECISIONS.md D7, still open)

the corpus is not tracked (3.4 GiB); V2 cannot run without it
no P4 result was produced from a clean clone of this repository

P4 did not push, merge or tag
P4 did not move or modify a tag
P4 did not change VERSION
```

### 10.1 The one structural improvement P4 made

`P4-HANDOFF.md` §3 warned: *"If P4 needs the P3 implementation to survive, moving
it somewhere tracked is the first task, not a later one."*

P4 did not move `work/` — that is **D7**, a repository-layout decision owned by the
repository owner, and moving ~25 GiB of research artifacts is not P4's call.
Instead P4 removed the *dependency*:

```text
P4 re-implemented the apply path in tracked code
P4's tests import nothing from work/
P4's reproduction check treats the P3.11 hashes as DATA, and those hashes
    are quoted in a tracked document (evidence-freeze.md, provenance.md)
```

**So if `work/` were deleted tomorrow: P4's production module, its 180-case suite
and its reproduction anchors would all survive. P3's own implementation would
not — and that remains D7, open.**
