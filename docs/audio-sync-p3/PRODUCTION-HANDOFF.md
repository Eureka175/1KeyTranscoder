# Audio Sync P3 — PRODUCTION-HANDOFF

**Audience: whoever is asked to turn any of P3 on.** This file says what is
already safe, what research validated, and what is **not approved** — with the
blocking condition named for each.

`core/`, `production/`, `encoders/`, `1kt.py` were **not modified by any P3
phase**. The entire P3 implementation lives under gitignored `work/`, behind no
feature flag, unreachable from the shipped entry point.

---

## 1. Already safe — shipped, unchanged

```text
P1 fixed-offset channel sync (--channel-sync)
```

P1 keeps its existing behaviour, including the fail-closed rule: when
measurement cannot meet its validity requirements, it leaves the track
`untouched`. **P3 changed nothing here.** `docs/design/channel_sync_p1.md` and
`core/channel_sync.py::DEFAULTS` remain the authority.

Do not read the P3 work as an upgrade path for P1. It is not one.

## 2. Research-validated — evidence exists, integration not approved

| capability | status | what validated it |
|---|---|---|
| P3 measurement layer | `VALIDATED` | ≤ 0.00003 samples on a constructed fractional fixture |
| P3 state semantics | `VALIDATED` | `TRUSTED` / `UNRESOLVED_GAP` / `REENTRY` produced explicitly; no window is `TRUSTED` without a measurement (P3.10 §7) |
| P3 real PCM apply | `VALIDATED` | contract-exact; excess below 0.5 sample on 480/481 and 415/416 points |
| P3 independent verification | `VALIDATED` | separate estimator, separate process, reads only PCM; 5 of 6 pairs within ±3 samples |
| Full real channel-pair E2E | `VALIDATED` | 6/6 pairs, 962/962 and 832/832 verify points, 0 contract disagreements |

**"Validated" does not mean "approved to ship".** Each of the above was validated
*as a research instrument on this corpus*. None has been validated as a product
feature.

## 3. NOT production-approved

| item | status | blocking condition |
|---|---|---|
| Gap policy | `DEFERRED` | Nobody has decided whether uncorrected alignment inside a gap is worse than invented structure. The metric that would have ranked the candidates is **policy-invariant** (0 of 8 136 pairs), so the research cannot offer a ranking even in principle. |
| Coordinate policy | `DEFERRED` | `latest-start` is what the engine does. Whether it is *right* is undecided; changing it silently alters **every historical plan**, therefore every P3.3–P3.8 real-corpus number. |
| Residual acceptance threshold | `NOT ESTABLISHED` | No threshold exists and none may be invented. "Is this good enough" is a product question and must be answered by the product owner, not derived from the data. |
| Default enablement | `NOT ENABLED` | No flag exists. Enabling by default is a decision nobody has made. |

## 4. The five reasons production correction is NOT READY

Reproduced from `work/p3/p311/FINAL-REPORT.md` §15. These are the blocking
conditions, not a summary:

1. **The tail is unsolved.** Where the plan did not measure, residual p95 is
   177–1121 samples (3.7–23 ms) against 9–30 samples where it did.
2. **Coverage is 19.5–74.4 %.** On three of six pairs the estimator refuses ~80 %
   of windows, so most of the corrected stream is policy-filled rather than
   measured, **while 100 % of samples are rewritten**.
3. **`current` — the shipped policy — over-claims.** It reports 0 unresolved
   windows where the mask has 388.
4. **No acceptance threshold exists**, so "good enough" cannot be answered.
5. **`no_correction_gap` is nearly a no-op** on the low-coverage pairs, so the
   policy choice decides whether most of the stream is corrected at all.

## 5. Why it is nonetheless READY FOR A POLICY DECISION

The mechanism is now *measured* rather than argued: the correction is essentially
exact where it measured, the apply reproduces the plan bit-exactly, the tail is
fully explained by gap carries, every candidate's real PCM behaviour is recorded,
and the comparison is reproducible. That is enough to decide **what to do about
gaps**. That decision is a product decision, not a research one.

## 6. Standing prohibitions for whoever integrates

Carried forward from `HANDOFF-CHECKLIST.md` §4 and re-affirmed:

```text
do not invent a residual threshold
do not raise a threshold so that a named corpus passes
do not select a gap policy or a coordinate contract on the research's behalf
do not enable anything by default without the policy decision above
do not treat "the reference agrees" as ground truth — the reference is the same
    estimator's exhaust, so a shared bias is invisible in it
do not rewrite a historical P3 report to match a later result
```

## 7. Before relying on any P3 number

1. Read `HANDOFF-CHECKLIST.md` §3.3: **"`core/` is unmodified" is not a valid
   unconditional statement.** It is true of P3's own changes; it is false of the
   working tree, because `core/batch_hw.py` carries unrelated user modifications.
   Re-verify rather than assume.
2. Read the phase's provenance document. Every headline number has a file, a hash
   and a command behind it.
3. Check `work/archive/pending-delete/` before assuming a stream is present.
   Some generated PCM has been quarantined; every quarantined file has an exact
   restore command in the manifest beside it.
