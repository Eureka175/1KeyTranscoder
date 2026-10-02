# Audio Sync P3.8 — Provenance

> **GENERATED FILE — do not edit by hand.**
> Regenerate with `python work/p3/p38/coordinator/pin_provenance.py`.
>
> It exists because `work/` is gitignored: every line of P3.8 is untracked, so
> `git log` cannot say whether the semantics engine changed. A conclusion bound to
> "the engine" is bound to nothing. These hashes are the binding.

Generated: `2026-10-02T10:12:32Z`  
Branch: `research/audio-sync-p3-production-semantics`  
HEAD: `2e96f7399fd3545fdd08f5315183db7aef7faac5` — docs(audio-sync): close the P3 research line and prepare the production handoff  
Upstream: `(none)`

`work/` is gitignored, so **none of the implementation files below are committed.**
The hashes in this file are the only durable record of what was actually tested.

## P3.8 implementation surface

| artifact | path | bytes | SHA-256 |
|---|---|---:|---|
| P3.8 engine | `work/p3/p38/semantics/gap_semantics.py` | 47,836 | `CE10F3FB68484CFE95B4C5D184A01EC351FDCE78C0DCFBCCA1209B7F0931283D` |
| P3.8 semantics selftest | `work/p3/p38/semantics/selftest.py` | 26,765 | `42E879400B05275C0EDF39BA603F3B340A9D195C14117C561E6D5FA8A4246EA7` |
| P3.8 failure injection | `work/p3/p38/failure_injection.py` | 16,363 | `9C8896FA3E90ACE86D9F0A43DBE1226EA69C923DF375CACAB97D19F88006697A` |
| P3.8 frozen-plan gate | `work/p3/p38/reproduce_frozen.py` | 9,199 | `6E837471D1304E1A28C587F557BC69815690B296C103F099B58A74A1696B7D09` |
| P3.8 overlapping-window geometry | `work/p3/p38/overlap_geometry.py` | 11,111 | `4494E9AED7175F4ECA218D087CAE4D2DEB1DC1AA37AC52041919C384BE8F1425` |
| P3.8 window-grid seam | `work/p3/p38/window_grid_seam.py` | 6,018 | `C75AE758205DB7AAE42DB07627AEDF20572D074C1C66B68EB1AF295D8575DEC3` |
| P3.8 memory check (attempt 1, SUPERSEDED -- quarantined) | `work/archive/pending-delete/p38-closure/superseded/work/p3/p38/memory_check.py` | 9,734 | `D227F44A85B0838397046901CD8CC2BF278F6CD35AB375D7B53E1C48EB7D3B6A` |
| P3.8 memory check (with working controls) | `work/p3/p38/memory_check2.py` | 11,787 | `5067465869E3D6C8F7B8AD2186B543F1787ECBDCDF96E3F50426AB856D55EFDA` |
| P3.8 RSS helper consumed by the memory check | `work/p3/p38/memprobe.py` | 1,637 | `D9A4A5BE85E585E0383F124D7069D2BE26A8D58647095B1852283361187C3CE8` |
| P3.8 memory probe | `work/p3/p38/memory_probe.py` | 9,115 | `A1CE2E1FDA833684ABCA5003A8DB13AD4E5503722181A97B982DE247443E4073` |
| P3.8 offset across delay models | `work/p3/p38/offset_across_models.py` | 6,725 | `3A04671573D1E0E4FC88D522D58741EDB8F24FB99E2D3FB926D6E2C2ABD950A0` |
| P3.8 archive/quarantine record | `work/p3/p38/coordinator/archive_quarantine.py` | 12,504 | `FD33B1C565A625443225A8C284CDFE8364E2D2CD66C1CAB9ADCC564EF382CEED` |
| P3.8 evidence freeze | `work/p3/p38/coordinator/freeze.py` | 7,251 | `ED93A545180C41D7917C9CBAB1F7A771D65DBD5ACAE8681E1B1E82496D06BE3B` |
| P3.8 provenance pinner | `work/p3/p38/coordinator/pin_provenance.py` | 17,138 | `E16C8AD3F2CF602AE3D1B311E2CA1ADA4EC488C5CAB2E8E7686613D548A8FF36` |
| P3.8 final git/regression audit | `work/p3/p38/coordinator/final_audit.py` | 9,134 | `06EB399FA5DCD463A9165A8DDCECCAF016A6D039A77722867BF88F805A0056F9` |
| Critical A/C window-centre audit | `work/p3/p38/coordinator/a_window_center_audit.py` | 16,592 | `AD39A7C3601C63B80919932FAAEEA8418C066A8C7ECBA4BDD83A2076817D44E9` |
| Critical A/C confirmatory (real measure_window) | `work/p3/p38/coordinator/a_confirmatory_real_measure.py` | 9,188 | `81A9D5E99B95CFB0CE195F397C32A8A0B6703D22DFCAC6E634DF89A6FDEC5E88` |
| Critical C mapping fixture | `work/p3/p38/coordinator/c_mapping_fixture.py` | 1,735 | `4FD2E1BE1AEC9BB4AD57E31537DD3124E0349D850C32D5E308B0A52834F9F618` |
| P3.9 quarantine register generator | `work/p3/p38/coordinator/quarantine_register.py` | 9,503 | `67E27D5A9A1E0188B8F2410EA489820020C9167A47C470C6FCD86C40383D545A` |
| P3.9 corpus integrity check | `work/p3/p39/corpus_integrity.py` | 3,953 | `E39B0AA5CC967307E59BAB49B585477A49E6BF82FB26F58AC30B302AFA64DA78` |
| P3.9 prototype design options (A/B/C, unranked) | `work/p3/p39/prototype-design-options.md` | 22,092 | `2B2875AAABAA79E1F1209F1338A8C3F2B6E7A653CFA5BC29CDA21DFD385CA4CA` |
| P3.9 real-apply validation design (DESIGNED, NOT RUN) | `work/p3/p39/P3.9-real-apply-validation.md` | 76,634 | `8F79ED9197F60A7931DBCD9F7641929937D250627BC4DA618CA3552E00E04DAF` |
| P3.9 window coordinate contract (A/B/C, unchosen) | `work/p3/p39/window-coordinate-contract.md` | 45,638 | `8561D054B8EB39D24CB9E966122A263930CAE802EC531AFB44803C440B9052AE` |
| P3.9 production readiness matrix | `work/p3/p39/production-readiness-matrix.md` | 6,925 | `379B722E38D9A2C1E1C9280F9F51865F3A74E46CC067A5220A33C442C078DFF6` |
| P3.9 acceptance report | `work/p3/p39/P3.9-ACCEPTANCE-REPORT.md` | 17,958 | `2924AC3446524C9A58FD2C654EA438439D2DBEE48392AD55237F4F5620721770` |

## Validated-against surface (measurement, planning, correction)

| artifact | path | bytes | SHA-256 |
|---|---|---:|---|
| P3 measurement layer | `work/p3/measure.py` | 48,680 | `037A00953E64CD10295E87E9814F4F8236D6BE9ABD4D5F4453D161BA260F58FF` |
| P3.2 planner | `work/p3/correct.py` | 36,527 | `300707AD2B48478E3145522D57E2536A10956EC78D04BE28DAFBCE7DBE2FFE57` |
| P3 long-run driver | `work/p3/run_long.py` | 10,754 | `0867E05FEF499FF2FF81284C12C9618F97EDC4EE957B68F11A6CF49C9E6BF25D` |
| P1 correction path (production) | `core/sync_fix.py` | 5,912 | `6D3F75B403C56CAF78190BFCFEA3954ABC7CD15E11B6737B833E6080CF8F79C2` |
| P1 channel integration (production) | `core/channel_sync.py` | 47,060 | `14283AB7215FF592E8ADD766963195DB303408F5BF0A70E3526B20D28ADE1BAB` |
| P1/P2 estimator (production) | `core/sync_estimate.py` | 31,622 | `773C96A746DFFFE7A2A1852B059AF51B0FD51A84CAEA182E5A22403D31BDDF3E` |

## Frozen historical artifacts

| artifact | path | bytes | SHA-256 |
|---|---|---:|---|
| P3.2 validation manifest (READ-ONLY) | `work/p3/p32/validation_manifest.json` | 6,666 | `C77338D0D3CAC48BDFC4757317CC57D3DF0D73B7DEBB33C2D4CD93ED4A16375D` |
| P3.3 frozen plan C1305 hop10 | `work/p3/p33/c1305/plan_20260919_C1305_hop10.json` | 61,369 | `71398FA52A90A9B49B8342AB941EEBB9CFB2217A6210BCE340962834C333904E` |
| P3.3 frozen plan C1305 hop2.5 | `work/p3/p33/c1305/plan_20260919_C1305_hop2p5_s3000.json` | 150,829 | `C55103C2449918DE5630F5639C89D6988A8751A44898819F940645FB430864BC` |
| P3.3 frozen plan C1306 hop10 | `work/p3/p33/c1306/plan_20260919_C1306_hop10.json` | 67,472 | `B1D8489CFA4370CAD94EE97656004775226818C17AFCEBFA332F881C306DFC0A` |
| P3.3 frozen plan C1306 hop2.5 | `work/p3/p33/c1306/plan_20260919_C1306_hop2p5.json` | 166,430 | `D7EF2D38E6C47C4D7B723DCEBD30F8D749FEAA0DA359814BBAC0D41206AE77A9` |

## Identity of every key semantic: version + hash + commit

A hash alone says *which bytes*; it does not say *which semantics*. The version
string lives inside the hashed file, so it is only meaningful together with the
hash. The commit says which tree the tracked part of the evidence came from.

| semantic | version string | source of the version | file SHA-256 (short) | git commit |
|---|---|---|---|---|
| P3 gap semantics engine | `p38-semantics-1` | `work/p3/p38/semantics/gap_semantics.py` | `CE10F3FB68484CFE…` | `2e96f7399fd3` |
| P3 measurement layer | `3.0.0-dev` | `work/p3/measure.py` | `037A00953E64CD10…` | `2e96f7399fd3` |
| P3.2 planner / plan format | `p3-correction-1` | `work/p3/correct.py` | `300707AD2B48478E…` | `2e96f7399fd3` |
| P3.8 failure-injection record | `p38-failure-injection-1` | `work/p3/p38/failure_injection.py` | `9C8896FA3E90ACE8…` | `2e96f7399fd3` |
| P3.8 frozen-plan reproduction record | `p38-real-reproduction-1` | `work/p3/p38/reproduce_frozen.py` | `6E837471D1304E1A…` | `2e96f7399fd3` |
| P3.8 evidence freeze record | `p38-evidence-freeze-1` | `work/p3/p38/coordinator/freeze.py` | `ED93A545180C41D7…` | `2e96f7399fd3` |
| P3.8 memory re-measurement record | `p38-memory-2` | `work/p3/p38/memory_check2.py` | `5067465869E3D6C8…` | `2e96f7399fd3` |
| P3.8 window/centre mapping fixture | `p38-a-confirmatory-1` | `work/p3/p38/coordinator/a_confirmatory_real_measure.py` | `81A9D5E99B95CFB0…` | `2e96f7399fd3` |
| P3.8 window-grid seam record | `p38-window-seam-1` | `work/p3/p38/window_grid_seam.py` | `C75AE758205DB7AA…` | `2e96f7399fd3` |
| P3.8 offset-across-models record | `p38-offset-models-1` | `work/p3/p38/offset_across_models.py` | `3A04671573D1E0E4…` | `2e96f7399fd3` |
| P3.8 final git/regression audit | `p38-final-audit-1` | `work/p3/p38/coordinator/final_audit.py` | `06EB399FA5DCD463…` | `2e96f7399fd3` |
| P3 engine self-test | `100 PASS / 0 FAIL` | `work/p3/p38/semantics/selftest.py` | `42E879400B05275C…` | `2e96f7399fd3` |
| P3.8 overlapping-window geometry | `26 PASS / 0 FAIL` | `work/p3/p38/overlap_geometry.py` | `4494E9AED7175F4E…` | `2e96f7399fd3` |

The frozen P3.3 plans carry `version = p3-correction-1`; the P3.2 freeze carries
`manifest_version = p32-validation-freeze-1` **and** its own `git_commit`
(`b4582b6`), which is what makes that manifest auditable against a tree rather
than only against a hash.

## Real corpus

| audio | path | bytes | SHA-256 |
|---|---|---:|---|
| C1305 ch1 | `work/p2/out/raw/20260919_C1305_a1.f32` | 923,002,080 | `A1BEFEFCBE9A1AFC6621DC941C4914E1E7A66D10FC065ABA397353D38CD6221F` |
| C1305 ch2 | `work/p2/out/raw/20260919_C1305_a2.f32` | 923,002,080 | `31DF1572AEEB4020D68AF6DE092146A496434991ABB542D8BD11EE2E55860E6B` |
| C1305 ch3 (reference) | `work/p2/out/raw/20260919_C1305_a3.f32` | 923,002,080 | `360A9F4D61A37E68DFF67CEFDF9ABBF74D3D694C15272CABDC7DCCC9A814F462` |
| C1305 ch4 | `work/p2/out/raw/20260919_C1305_a4.f32` | 923,002,080 | `E70BBCBFFF4904B4C25B24FEC23D554B804CC913166EC3AD5D72BC76A19A46ED` |
| C1306 ch1 | `work/p2/out/raw/20260919_C1306_a1.f32` | 799,614,816 | `313EB8D37744CBFAC5950F86484A539068B9BFFE9E323ECC80EE5CB53A5D0F47` |
| C1306 ch2 | `work/p2/out/raw/20260919_C1306_a2.f32` | 799,614,816 | `8B11A078AAD73EF8DB93DC57416864BC765502CB039264EA6D5A1A4745C0F266` |
| C1306 ch3 (reference) | `work/p2/out/raw/20260919_C1306_a3.f32` | 799,614,816 | `3970DD19B83DDD2D68B2D1F51D833EA9B686DEF85139C5697ABBD9BCF3B629A6` |
| C1306 ch4 | `work/p2/out/raw/20260919_C1306_a4.f32` | 799,614,816 | `3E468FAC4BC0349856A100FD01C82825E74E4E876ABDDD4A0E4948BBFD65312E` |

## Measurement runs — the only source of real per-window delays

`window_id` is **not unique across hops**; a P3.6 fidelity gate failed on exactly
that (477/478 windows disagreed). Every run is therefore identified by `(stem, hop tag)`.

### `20260919_C1305` / `hop10` — `work/p3/experiments/20260919_C1305`

- `run_meta.json` — 1,240 B — `3DD930084B0F2581EB3F31EF4DD0990F1710824698B4E23647F97A00669B8E03`
- `raw_measurements_ch3_to_ch1.jsonl` — 380,150 B — `57707147B39B1B7190669FEA8CCAB54B6690F27F1900815C8A4476A9E1C645D1`
- `raw_measurements_ch3_to_ch2.jsonl` — 379,242 B — `8646E61E8B9FF2B420756E40483415E37C2901B3A56D6F914617D51D63FD65EA`
- `raw_measurements_ch3_to_ch4.jsonl` — 372,742 B — `77D5F0E29296D09A3BBC1549455887D7AEB934FFF63565EC56E0A594E15D00B0`

### `20260919_C1305` / `hop2p5_s3000` — `work/p3/p32/experiments_mid/20260919_C1305`

- `run_meta.json` — 1,277 B — `796F9699D30295714054FAEA1E4A14234642CF48184BC89D1C350156DBC777DC`
- `raw_measurements_ch3_to_ch4.jsonl` — 1,491,205 B — `02030803781A7F5C49A4B3040BBFE7BEF367780994938973C67BCFAC04DD500A`

### `20260919_C1306` / `hop10` — `work/p3/p32/experiments/20260919_C1306`

- `run_meta.json` — 1,310 B — `3BCDA97D6340B9911C9C4881FE6EFA4CAC88C5182E5EEF6BEE057DAEC0844D8B`
- `raw_measurements_ch3_to_ch1.jsonl` — 326,459 B — `2290A829B52E89E2024D3DFDCAFC927842F1F55952EB272101A908D2C9281181`
- `raw_measurements_ch3_to_ch2.jsonl` — 326,623 B — `FAD6EF332C8D3AD6E99E199B3CD7071A9B912AC43EF4CA188812FAFB99734453`
- `raw_measurements_ch3_to_ch4.jsonl` — 317,677 B — `3C5662D83AFD836D1B537C1346684DE3691E7AC8C89DAE2AD004B521A66A1709`

### `20260919_C1306` / `hop2p5` — `work/p3/p32/experiments_fine/20260919_C1306`

- `run_meta.json` — 1,277 B — `7A6357DA7EE1975806EF57D629F3253856583DB07D5DEF5C259637379199786A`
- `raw_measurements_ch3_to_ch4.jsonl` — 1,270,613 B — `2FC085A681E0F7A990DDE49E835E3E8C1B7A7DED9138FB5B6B27E4AD4325CC57`

## Agent artifacts

Each agent bound its own numbers to input hashes. The hashes below are the
agents' manifests as read back from disk, not a summary of them.

### Agent A (estimator E2E, Critical B)

- root: `work/p3/p38/agent_a_closure`
- files: 17, total 500,462 B
- `manifest.json` SHA-256: `6BDB6A141B364F39D471F1948F22E9FA8C8CA884E6ECB3E171F409C263F4A302`

### Agent B (adversarial review)

- root: `work/p3/p38/agent_b_closure`
- files: 24, total 161,534,337 B
- `manifest.json`: **absent**

### P3.8 first-pass Agent A

- root: `work/p3/p38/agent_a`
- files: 18, total 900,168 B
- `manifest.json` SHA-256: `995BC72DDEEAFFBF788EF41E2231800614BBACF7C6276646DDBFA6C008CF0E87`

### P3.8 first-pass Agent B

- root: `work/p3/p38/agent_b`
- files: 27, total 488,346 B
- `manifest.json` SHA-256: `8F451AAD0F548039A607AAECB6F7EB2073E6C46996EF0765988A2D1A4A51F54B`

## What this file does NOT establish

- It does not say the hashed code is *correct*; it says which code a result came
  from. Correctness is the job of the suites and the independent review.
- It does not make the implementation durable. A hash of a gitignored file cannot
  restore that file. If `work/` is deleted, the evidence is gone and these hashes
  are a tombstone, not a backup. This is an accepted, recorded weakness of the
  research layout, not a solved problem.
- Corpus hashes prove the *bytes* were unchanged; they do not prove the corpus is
  the same recording as any other artifact elsewhere in the repository.
