# `tests/regression` — the release regression matrix harness

These are the drivers behind
[`docs/audio-sync-p4/v0.8.7-regression-matrix.md`](../../docs/audio-sync-p4/v0.8.7-regression-matrix.md).
They live here, not under the gitignored `work/`, for the same reason P4.11
records: tooling that only exists on one machine cannot be audited or re-run,
and a report whose harness is untracked is a report you have to take on trust.

They are **not** part of `tests/selftest` (the standing suite) and are not run
by `python tests/full_autotest.py`. They are one per tier of the matrix:

| script | tier | what it produces |
|---|---|---|
| `compare_arms.py` | T5 / T6 / T7 | field-exact diff of two channel-sync arms (`lost_fix`, `added_fix`, `changed_value`, reclassifications) |
| `e2e_verify.py` | T8 | independent verification of one transparent output: own box walker, `ffmpeg -c copy -f streamhash`, residual accounting identity, Gyroflow |
| `run_e2e.py` | T8 / T9 | the stratified transparent batch (7 runs) driving `1kt.py` and then `e2e_verify.py` |
| `t10_plumbing.py` | T10 | watchfolder three-state forwarding, `start.bat` argument fragment, CLI contract, idempotency, corrupt-input semantics |
| `run_p4_suites.py` | T4 | runs the seven `tests/audio_sync_p4` audit scripts and aggregates them |
| `collate.py` | all | folds every tier's artefact into `out/MATRIX.json` |
| `e2e_inventory.py`, `e2e_strata.py` | T8 prep | what clips exist, by verdict / stream count / size — the sampling frame |
| `e2e_report.py`, `p4_counts.py` | reporting | human-readable views of the above |

## Machine-specific assumptions

These are declared rather than hidden, because they are the reason the harness
cannot simply run anywhere:

* `E:\1kt_media\{speech,long}` — the 226-clip calibration corpus (182 GB).
* `E:\1kt_scratch\pcm` — the decode cache. Without it every arm re-decodes
  183 GB of PCM; with it a full 226-clip arm takes ~4 minutes.
* `E:\1kt_work\reports\baseline.json` — the frozen pre-change baseline
  (`c67e792`), which is what makes A1 a comparison rather than an assertion.
* `I:\compact\20260901` — the held-out corpus.
* `E:\1kt_regress_v087` — scratch space for the transparent remuxes (~4 GB per run).
* `tools\ffmpeg.exe`, `tools\ffprobe.exe`, and a working GPAC install.

`REPO` is derived from `__file__` everywhere; only the data roots are absolute.
Artefacts always land in `<repo>\work\regression_r087\{out,logs}` regardless of
where the script is invoked from, so the document's paths stay valid.

## Nothing here encodes

`run_e2e.py` invokes the real transparent path (video is stream-copied, never
re-encoded) and `t10_plumbing.py` uses `--dry-run` throughout. No video encoder
is invoked by any script in this directory.
