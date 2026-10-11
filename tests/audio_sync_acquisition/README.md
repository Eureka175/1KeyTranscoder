# tests/audio_sync_acquisition — real-take acquisition toolchain

This is the executable half of
[`docs/audio-sync-p4/p4.10-acquisition-protocol.md`](../../docs/audio-sync-p4/p4.10-acquisition-protocol.md).
P4.10 published the protocol and reported that the analysis after recording was
"automated and already specified"; the code it referred to lived under the
gitignored `work/` tree, so a clean clone could not run any of it. These tools are
tracked code so that promise is checkable.

**No real wireless take exists yet.** `REAL_GROUND_TRUTH = BLOCKED` and
`D3 = NOT ESTABLISHED` (see
[`docs/audio-sync-p4/p4.10-real-source-qualification.md`](../../docs/audio-sync-p4/p4.10-real-source-qualification.md)).
Everything here has been exercised only on deterministic fixtures whose delays were
injected digitally, so what is proven is the toolchain and nothing about a link.

## The four commands, in order

```powershell
# 1. generate the signal the operator plays into BOTH paths
python tests/audio_sync_acquisition/generate_signal.py
python tests/audio_sync_acquisition/generate_signal.py --verify

# 2. check the delivery before any production code touches it
python tests/audio_sync_acquisition/preflight.py --input takes\

# 3. analyse what passed
python tests/audio_sync_acquisition/ingest.py --takes takes\

# 4. prove the toolchain still behaves (fixtures only)
python work/p4.11-acquisition/make_fixtures.py
python tests/audio_sync_acquisition/test_e2e.py
```

| file | role |
|---|---|
| `generate_signal.py` | the deterministic test signal and its manifest |
| `preflight.py` | is this take usable at all, and what kind of data is it |
| `ingest.py` | take it through the production estimator / gate / plan / apply / verifier |
| `test_e2e.py` | the fixture-based end-to-end test, with negative controls |
| `acquire_common.py` | signal-layout reader, operator-note parser, reason codes |
| `schema.json` | the operator note's required fields |

## What the tools refuse to do

Each of these is a deliberate refusal, not a gap:

* **No accuracy number without a truth.** With no reference-path calibration the
  result is labelled `CROSS_METHOD_AGREEMENT`, never `MEASURED_ACCURACY`. Two
  methods agreeing on the same real signal is a different claim from a measured
  error, and the label says which one it is.
* **No correction the gate refused.** `ingest.py` reads every threshold from
  `core.channel_sync.effective_opts` at run time and applies nothing when the
  evidence gate refuses.
* **No silent bias subtraction.** The raw measurement, the calibration measurement
  and the derived value are all stored, with the formula and the sign direction
  next to them. A missing calibration is `null`, never `0`.
* **No physical input guessed from stream order.** Four mono streams do not say
  which physical input feeds each of them; the operator note must, and an
  incomplete mapping stops the analysis rather than being filled in.
* **No input modified.** Preflight and ingest decode to a scratch file and never
  write to, trim or re-encode a take.
* **No reimplementation.** The estimator, the evidence gate, the plan and the apply
  are the production functions. The only measurement written here is the
  independent verifier, which is *supposed* to share no code with the estimator.

## Sign conventions, measured not remembered

Proved on a ramp in `work/p4.11-acquisition/sign-chain.json`:

```text
fixture material      tgt[k] = ref[k - d]            d > 0 => tgt LATE
estimate_pair(ref,tgt) returns +d                    (matches the fixture)
sync_fix.shift_stream(delay_samples=s) does out[k] = in[k+s]
so correcting a target that is LATE by d means shift_stream(delay_samples=+d)
```

For a take, the reported quantity is:

```text
raw_wireless_vs_reference  = estimate_pair(reference_channel, wireless_channel)
                             positive  =>  the WIRELESS channel arrives LATE
cable_calibrated_offset    = raw_wireless_vs_reference
                             - reference_path_bias
```

The bias is the offset measured by the session's `CABLE_CALIBRATION` take, which is
two electrical paths and should read zero. It is **measured**, so it carries
sub-sample error, and the derived value carries that error too — reported, not
rounded away.

## Status vocabulary

Execution status and data status are separate, because a take that is unusable is
not a software failure:

```text
execution : PASS / FAIL / BLOCKED / INSUFFICIENT_DATA / NOT RUN
data      : READY_FOR_ANALYSIS / CONSTANT_DELAY_CANDIDATE /
            TIME_VARYING_CANDIDATE / DATA_INELIGIBLE / IDENTITY_UNCONFIRMED
```

Every rejection carries a stable code from the closed set in
`acquire_common.REASON_CODES`. A bare "invalid file" is not a possible output.
