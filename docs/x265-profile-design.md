# x265 Profile Design Methodology

> **What this document is.** The design rationale behind the four x265 profiles shipped by
> 1KeyTranscoder (`uhq`, `hq`, `small`, `fast`): what each one is for, how each one was
> derived, what resource it is allowed to spend, how the four relate to each other, and how
> a parameter decision is judged.
>
> **What this document is not.** It is not an x265 parameter tutorial, not a recommended
> preset table, and not a general x265 cookbook. Parameters appear only where they explain a
> design decision, and in full only in [Appendix A](#appendix-a-current-profile-parameters),
> transcribed from the configuration actually in the repository.
>
> **Provenance.** Part of the x265 calibration happened before this repository kept a formal
> version history, so no dated experiment log is reconstructed here. The vocabulary used
> throughout is *experimentally calibrated*, *iteratively tuned*, *derived from the design
> objective* — not "first experiment, second experiment". Every number quoted in this
> document is either read from the current configuration or cited to a document in this
> repository.

---

## 1. Introduction

1KeyTranscoder's x265 backend is selected with `--encoder x265` and a profile with
`--preset uhq|hq|small|fast|all` (case-insensitive; default `hq`). Those four names are not
four speeds of one parameter set. They are **four encoding strategies designed for four
different production goals**.

The distinction matters because a profile quietly embeds a choice. Any single parameter set
is forced to decide, in advance and for every clip, how much CPU time is worth spending and
how many bits are worth spending. Making that choice explicit — as four named strategies
that state their own budget and their own objective — is what this design does, and it is
what this document records.

The design questions this document answers are therefore:

- Why can one profile not serve every production goal?
- What is each profile's objective, and which resource is it allowed to consume?
- How was each one derived — from what baseline, by pruning what, against what reference?
- How were they validated, and on what material?
- How do the four relate to each other?

Where the profiles are defined:

| Artefact | Contents |
|---|---|
| `x265.json` | The four base profiles: the parameters each strategy decides. |
| `x265_scaling.json` | The 4K60 reference configuration, the normalisation rules, and the per-profile × per-source-class dynamic VBV table. |
| `core/models.py` | `PRESETS = ("UHQ", "HQ", "SMALL", "FAST")` — the single source of the profile names. |
| `core/scaling.py` | Resolves a base profile into effective parameters for a specific source (values only; no tuning numbers are hardcoded there). |
| `encoders/x265.py` | Serialises the resolved parameters (`PARAM_MAP`) and builds the FFmpeg command. It contains no tuning decisions. |
| `docs/evaluation/` | The per-backend assessment record, including the current calibration status. |

---

## 2. Why a Single Profile Is Not Enough

Encoding has at least three objectives that constrain each other:

- **speed** — how much wall-clock time the encode costs,
- **quality** — how faithfully the result represents the source,
- **file size / compression efficiency** — how many bits the delivered quality costs.

They cannot all be maximised at once, and the frontier between them is not a detail that a
tool can hide. Every expensive analysis in an encoder is a purchase: exhaustive motion
search, deeper recursive transform decisions, more reference frames, more B-frames, a longer
lookahead, finer quantiser rounding — each one buys some quality or some compression, and
each one is paid for in CPU time. Conversely, a bitrate ceiling buys a size guarantee and is
paid for in quality.

The project's response is not to search for the parameter set that is best "in general" —
there is no such set. The response is to **select several operating points on that frontier,
name them, and state what each is for**. A profile is the answer to the question "which
constraint is binding for this job?", and different jobs have different binding constraints:

- a reference-grade master is bound by quality;
- an overnight batch on a laptop is bound by time;
- a long-term archive is bound by storage.

Those are not three settings of one knob. They are three different problems, and the numbers
that solve one of them are the wrong numbers for the others. That is why the profile
structure exists.

There is a second, measurable reason the three axes cannot be collapsed: the size axis is
already configured per profile, not once for the backend. `x265_scaling.json`'s
`dynamic_vbv` table gives every profile its own ceiling ratios against the source's own
bitrate, and those ratios differ by profile and by source class. If the file-size budget
were a global setting, the profiles would be interchangeable on that axis — they are not.

---

## 3. Optimization Dimensions

| Dimension | The question it answers | How it is judged in this project |
|---|---|---|
| **Speed** | Can this job finish within the time the production actually has? | Measured throughput on a known machine, recorded per profile as a calibration annotation in `x265.json` (format `285H@<fps>` — frames per second on an Intel Core Ultra 9 285H). |
| **Quality** | Does the result hold up against the source at the intended viewing standard? | Subjective assessment against the near-transparent region, plus the laboratory comparisons recorded under `docs/evaluation/` and the third-party material archived under `docs/reference/x265/`. PSNR/SSIM sampling exists in `--check full`, but `x265.json` documents it explicitly as a corruption/defect detector with deliberately low thresholds — it is **not** used as a quality gate. |
| **File size / compression efficiency** | What does the delivered quality cost in bits, now and over the life of the archive? | Bitrate and size relative to the source and relative to the alternatives, together with the per-profile VBV ceiling rules that make the size budget explicit. |

The three are measured on purpose at different levels of formality, and that asymmetry is
deliberate: throughput is cheap to measure and is therefore pinned per profile; quality is
expensive to measure and is therefore assessed on the corpus rather than asserted per clip.
This document does not restate per-clip quality or size numbers, because the authoritative
place for them is the evaluation record, not a design document.

Two consequences of taking all three dimensions seriously:

1. **Speed alone never justifies a profile.** A strategy that is fast and delivers
   unacceptable compression efficiency has not solved the production problem — it has moved
   it.
2. **Quality alone never justifies a profile either.** A strategy whose quality advantage
   cannot be reached inside the available production time, or whose size advantage is not
   worth the extra computation, has not earned its cost.

---

## 4. Profile Is a Production Strategy

The four profiles, stated by objective:

| Profile | Objective | Resource it is allowed to spend | Constraint it protects |
|---|---|---|---|
| **UHQ** | Quality ceiling: the high-quality reference baseline and the maximum-quality offline setting. | Effectively unbounded computation. | Quality. Nothing else. |
| **HQ** | High-quality final output with reduced computation: as close to the quality ceiling as the pruning allows. | Substantial but production-viable computation. | Quality, with time as a real secondary constraint. |
| **FAST** | Production throughput: compression efficiency per unit of encoding time. | A deliberately small time budget. | Time. |
| **SMALL** | Storage efficiency: compression efficiency and file size. | More computation than the throughput strategy is allowed, spent on making files smaller. | Storage. |

**Profile ≠ quality level.** The four names are not four rungs of one ladder, and the correct
reading of `uhq → hq → small → fast` is **not** "quality decreasing". SMALL is not a worse HQ;
FAST is not a lower SMALL; HQ is not a cheaper UHQ. Writing them in that order describes
nothing about the design — the actual relationships are in
[§9](#9-fast--small--mutual-calibration) and [§14](#14-profile-lineage), and they are not a
single line.

Two practical consequences of this framing:

- **Selecting a profile is selecting a production goal**, not selecting a quality tier.
  `--preset all` runs the same material through all four strategies; because the outputs are
  different strategies rather than different tiers, they are written to per-profile output
  subdirectories and their per-file logs carry the profile name. That layout exists precisely
  because comparing the strategies side by side is a legitimate workflow.
- **No profile is "the good one".** There is no profile that should be used in all cases,
  and the design does not present one. The default (`hq`) is a default for the common case,
  not a claim that the other three are inferior.

---

## 5. UHQ — Quality Ceiling

UHQ's primary goal is stated directly: **maximise encoded quality, and serve as the
high-quality baseline and the quality-ceiling reference for the whole profile set.**

Its role is therefore two-fold:

1. **A production mode** for work that genuinely justifies extreme offline encode time. The
   repository's own annotation on the profile records both halves of that sentence — it is
   the "slowest" setting, and it is annotated as not practical for bulk production, measured
   at 0.8 fps on the reference machine (a 1-hour 4K60 clip is on the order of days of
   encoding).
2. **The design baseline for every other profile.** UHQ is what makes the other three
   profiles derivable rather than arbitrary:
   - it establishes which analyses *exist* at full strength, so the other profiles have
     something to prune from;
   - it provides the quality reference against which "how much did this pruning cost?" can
     be asked;
   - it supplies the quality boundary that the other profiles are positioned against —
     HQ explicitly aims to stay close to it, FAST and SMALL explicitly accept distance
     from it in exchange for their own budget;
   - it answers "what does the full set of analyses buy?" — which is the question that must
     be answered before a computation can be judged as worth keeping or worth removing.

UHQ is not a recommendation. The design does not claim that any footage should be encoded
with UHQ; it claims that a quality ceiling must exist and be reachable, or the cheaper
profiles have no reference and no measurable cost.

---

## 6. HQ — Selective Computation Pruning

HQ is derived from UHQ. It is not "UHQ with the parameters turned down", and it is not
"UHQ's faster preset". The derivation is:

```
UHQ  (quality ceiling: every analysis at full strength)
  ↓
observe the cost and the benefit of each analysis
  ↓
identify the computations whose marginal benefit is too small to justify their cost
  ↓
remove or relax those computations
  ↓
keep the decisions that genuinely determine quality
  ↓
validate on real material
  ↓
HQ  (high-quality production strategy)
```

The principle: **stay close to the quality ceiling while cutting the computation that does
not pay for itself.**

Three properties of this derivation are worth stating explicitly, because they are what
distinguish it from a preset ladder.

**(a) Not all parameters are lowered together.** HQ keeps, at UHQ's value, the decisions that
shape rate allocation and temporal structure across the whole clip: the keyframe interval and
scene-change detection (`keyint`, `scenecut`, `hist_scenecut`), fade handling, B-pyramid and
weighted prediction, the CTU/CU geometry, and the motion-search range (`merange`). These are
whole-clip decisions whose cost is modest relative to the per-block search work, and dropping
them would damage quality in a way the profile is not willing to accept.

**(b) Where HQ does move, it moves the exhaustiveness of per-block search and the depth of
rate-distortion decision-making** — the analyses that dominate CPU time: the RD level, the
RDOQ level, reference-frame count, merge candidates, transform-depth limits, and the early-exit
family (skip heuristics, reference/mode limits, intra fast paths). The quantiser window is
relaxed slightly so that rate control has room to trade bits where they matter less.

**(c) HQ is not a monotone reduction of UHQ.** It also *enables* something UHQ deliberately
switches off: SAO is disabled in UHQ and enabled in HQ (with its early-exit limiter on). SAO is
a compression-side tool with a fidelity cost, and HQ accepts that cost because the profile is
no longer buying quality at any price — it is buying quality at a production-viable price. A
profile that merely ran every parameter in one direction would not need a corpus to validate;
these two-way moves are exactly the decisions that have to be measured.

The result is a high-quality production strategy: close to the ceiling, at a computation cost
the repository records as roughly 2.5× the throughput of UHQ (2 fps versus 0.8 fps on the
reference machine), which is the difference between an impractical and a practical final
delivery.

---

## 7. FAST — Production Throughput

FAST is not "everything turned down so that it goes fast". Its objective is specific:
**compression efficiency per unit of encoding time** — production throughput at a quality and
size that remain usable.

The problem it solves is a resource-allocation problem, not a speed problem: given a fixed and
small CPU-time budget per frame, which analyses are worth spending it on?

```
a fixed, small computation budget per frame
  ↓
the budget cannot be spread evenly across every analysis
  ↓
identify the analyses that are expensive and buy little
  ↓
cut those first, and cut them hardest
  ↓
preserve as many high-value compression decisions as the budget allows
  ↓
validate repeatedly on real material
  ↓
FAST
```

The method is **marginal-value-driven computation pruning**, and its characteristic question is
not "is a higher setting better?" but:

> Is the quality/compression gain this analysis buys worth the encoding time it costs?

That question is answered under FAST's own objective, which is why FAST ends up structurally
different from "HQ with lower numbers":

- **The search and decision machinery is cut hard** — the motion-estimation algorithm and
  search range, the sub-pixel refinement level, reference count, B-frame count, merge
  candidates, RD level, RDOQ, the dynamic-RD family, transform depths and rectangle
  partitioning, and the lookahead depth. These are the expensive per-block analyses, and they
  are exactly the ones whose cost is highest per unit of gained compression.
- **The cheap, high-value decisions are kept**: the long keyframe interval with scene-change
  detection and histogram-based scene-cut handling, fade detection, B-pyramid, weighted
  prediction, the rate-control shaping family, and the whole-clip VBV discipline. Keeping
  these costs little and protects the compression efficiency that makes the profile worth
  using at all.
- **The profile also moves the FFmpeg-side preset** (`-preset fast` instead of the `slow`
  used by the other three). That is part of the strategy, not a detail: x265's preset supplies
  the defaults for every parameter the profile does not list, and several of those
  preset-governed parameters (lookahead slicing among them) are genuinely part of the
  speed/compression trade-off. Explicit profile values override the preset; the preset still
  decides what is left over.

FAST is the profile whose objective explicitly includes "the batch finishes". The repository's
throughput annotation records 10 fps on the reference machine — five times HQ's recorded
throughput — and FAST is the reason the x265 path is usable for material where UHQ and HQ are
not.

---

## 8. SMALL — Storage Efficiency

SMALL is not a slower FAST, and it must not be documented as one. It optimises a different
quantity:

> **compression efficiency / storage efficiency** — the bits required to store the result.

SMALL is allowed to spend **more** computation than FAST, on purpose, because the constrained
resource is not time but the archive: a smaller file at acceptable quality reduces long-term
storage demand, and computation is the currency used to buy that.

The two profiles therefore sit on different budget axes:

| | Budget it is bounded by | What it spends | What it buys |
|---|---|---|---|
| **FAST** | time / throughput | as little computation as the quality and compression target tolerate | usable quality and size within a production time budget |
| **SMALL** | storage / compression | more computation, deliberately | smaller files, higher compression efficiency, at acceptable quality |

SMALL is a re-optimisation, not an interpolation between its neighbours. Relative to FAST it
buys compression with settings that cost real CPU time — deeper rate-distortion levels, the
fuller motion search, longer lookahead and GOP lookahead, more B-frames with a bias towards
using them, stronger I/P and P/B quantiser ratios (`ipratio`/`pbratio`), pyramid-style temporal
prediction, and the two compression-side tools that the quality-ceiling profiles deliberately
switch off (the CU-tree temporal-masking adaptation in the lookahead, and SAO). It also lowers
the bitrate ceiling substantially — both statically and through its own `dynamic_vbv` ratios —
because a smaller ceiling is the point, not a side effect.

Within the four profiles, SMALL is the one that holds the smallest size budget: against
high-bitrate LongGOP sources, its configured ceiling ratios are the lowest of the set, below
FAST, which is below HQ, which is below UHQ. **That ordering is a size ordering, not a quality
ordering** — it is the clearest demonstration that the profiles occupy distinct budgets:

| Profile | Ceiling ratio range, high-bitrate LongGOP sources | Reading |
|---|---|---|
| UHQ | 0.55 – 0.85 of source bitrate (target 0.70) | quality ceiling, size almost unconstrained |
| HQ | 0.35 – 0.65 (target 0.50) | high quality, moderate size reduction |
| FAST | 0.30 – 0.50 (target 0.40) | throughput, meaningful size control |
| SMALL | 0.20 – 0.40 (target 0.30) | smallest files of the four |

There is no quality ranking implied by that table, and none should be read into it. UHQ is
first because it refuses to constrain size at all; SMALL is last because constraining size is
its entire objective.

---

## 9. FAST ↔ SMALL — Mutual Calibration

FAST and SMALL are not two independently invented parameter sets that happen to coexist. They
are calibrated **against each other**, because they sit on opposite sides of the same trade-off
and are only useful if they are far enough apart to be distinguishable and close enough that
both remain legitimate answers for real production.

The two directions of the question:

- **FAST asks:** for this much speed, which quality and compression sacrifices are
  *reasonable*? A faster setting that has stopped delivering useful compression efficiency is
  not a throughput strategy, it is a quality failure with a fast clock.
- **SMALL asks:** for this much size reduction, how much additional computation is
  *justified*? A smaller setting whose runtime is so far beyond FAST's that it cannot finish a
  batch has not bought storage efficiency, it has bought a different problem.

So the relationship is reciprocal:

```
FAST  ⇄  SMALL
```

Each one's settings constrain the other's. The observable consequence is that the two profiles
are not scaled copies of each other: they share the decisions that neither objective argues
about (keyframe/GOP structure, scene-change handling, fade detection, B-pyramid, weighted
prediction, the rate-control shaping family, the constant-VBV discipline) and diverge sharply
where their budgets point in different directions — effort spent per block, lookahead depth,
temporal structure, and the compression-side tools each is willing to enable.

The relationship with the external anchor is part of the same calibration and is covered next.
What must not be written is a linear chain:

```
UHQ → HQ → FAST → SMALL          ✗  not this
```

The correct shape is:

```
UHQ  ──(prune computation, keep quality)──▶  HQ

UHQ  ──(quality reference)──┐
                             ├──▶  FAST  ⇄  SMALL
NVENC UHQ  ──(external anchor)──┘
```

UHQ supplies the quality foundation; HQ is derived from UHQ by pruning; FAST and SMALL each
re-find their own balance point under a different objective, using UHQ as the quality reference
and NVENC UHQ as the reality check, and using each other as the constraint on how far the
trade-off may be pushed.

---

## 10. NVENC UHQ as an External Production Anchor

NVENC UHQ is not a benchmark curiosity in this design, and it is not an opponent. It is an
**external real-world anchor**: a production hardware encoder configuration that this project
actually ships and runs (`nvenc.json`, `UHQ`: `tune uhq`, QVBR 23, 100 Mbit ceiling, level 6.1),
whose operating region is known, reproducible and far above the software path's throughput.

Its purpose is to keep the x265 profiles from being calibrated against an abstract ideal:

- **Speed reference** — what "fast" means in a real production environment. Hardware HEVC on
  the reference machine is recorded in this repository at 73 fps for 4K60, i.e. real-time-plus,
  while the software profiles are recorded at 0.8–10 fps on the same class of material. That
  gap is the reality the throughput objective is defined against.
- **Quality reference** — is the software profile's quality meaningfully above what hardware
  already delivers at comparable bitrates? If not, the extra time buys nothing.
- **File-size reference** — does the software profile actually deliver the size advantage that
  justifies its cost? This is the axis where the x265 path earns its place; the archived
  third-party measurement kept in this repository records that Ada-generation NVENC HEVC at its
  best preset needs on the order of **+25% bitrate to match x265 `slow`** (a community
  measurement, archived in `docs/reference/x265/x265_archiving_evaluation.md` — quoted here as
  a recorded reference point, not as a target this project set for itself).

The design loop is:

```
NVENC UHQ
  ↓
observe the speed / quality / size region a real hardware encoder occupies
  ↓
use that region as an external anchor
  ↓
position FAST and SMALL, and re-check HQ, against it
  ↓
validate on real material
  ↓
adjust
```

**It is not "x265 must beat NVENC."** The anchor's role is to keep the profiles inside real
production constraints. It is what makes the following conclusions statable rather than
theoretical: UHQ and HQ are not bulk-throughput settings — no amount of parameter tuning makes
a software encoder occupy the hardware encoder's region — while FAST and SMALL have to remain
plausible answers *next to* that region rather than outside it. The throughput strategy has to
stay a usable alternative for the jobs where size and fidelity matter more than raw speed, and
the storage strategy has to deliver a size advantage the hardware path does not. FAST and SMALL
were both repeatedly adjusted with reference to this anchor during calibration.

---

## 11. Corpus-Driven Design

### 11.1 The primary target input

The profiles are calibrated for a specific, stated production input, not for "any video":

> **Sony XAVC HS, 4K60P, 10-bit, 4:2:0, 150 Mbps, LongGOP.**

That is the material the base profiles are written for, and `x265_scaling.json` states the
matching reference configuration explicitly: **3840×2160 at 59.94 fps**, with scaling factors
computed relative to it. A profile is therefore never a claim about arbitrary input; it is a
claim about this target input, plus the adaptation rules that carry it to adjacent ones.

### 11.2 The corpus, not a single sample

Calibration used multiple real clips, deliberately covering conditions that change what an
encoder does with the same bitrate:

| Condition covered | Why it is in the corpus |
|---|---|
| High-noise / low-noise | Noise is the single largest consumer of bits; a strategy that works on clean material can fall apart on grain. |
| Handheld / stable | Motion and shake change how much residual the encoder must code, and how much motion search is worth paying for. |
| Indoor / outdoor | Lighting range, and the banding and flat-area risks that come with it. |
| Long-form | Rate control and lookahead behaviour over hours, not seconds — the regime the archive case actually lives in. |
| Multi-scene brightness transitions | Scene-cut handling, fades and rate-control recovery. |

The repository's own test corpus is organised along these lines (`testsets/adjust/`,
`testsets/validate/`, `testsets/stress/`): night-time indoor handheld with above-moderate
noise, overcast-day handheld with high shake and low noise, daytime handheld near-noise-free
and stable, in-car high-shake moderate-noise, and long-form sunny outdoor handheld tracking
with frequent brightness changes.

The requirement this places on the design is that a profile must be a **stable strategy over a
representative set of real production material**, not a setting that happens to look good on
the clip that inspired it. A profile tuned to one sample is not a profile; it is an
overfit.

### 11.3 What "corpus-driven" does and does not mean in the implementation

The profiles themselves are fixed per 4K60 reference and are **normalised by resolution and
frame rate, not by content**: frame-rate expressions for the lookahead family, a spatial
normalisation for the motion-search range with clamps, and a fixed set of parameters that are
deliberately never scaled at all (quantiser, RD, psychovisual and AQ settings — the design
refuses to let these drift with a resolution rule). What *is* content-dependent is the
file-size side: a metadata-only source classification (bits per pixel-frame against the
container bitrate) selects which VBV ceiling ratios apply, with a separate row per profile.

This is an important part of the design: **corpus-driven does not mean per-clip
improvisation.** The strategy is fixed and predictable; the corpus is what the strategy was
validated against, and what continues to be the standard for changing it.

### 11.4 Cross-corpus stability, not just the average

Because a single average hides the failure mode that matters, evaluation looks at the
*spread* across the corpus, not only the mean:

- minimum and maximum, and the range between them;
- variation from clip to clip, and consistency of behaviour;
- predictability — especially for **file size**, because the archive case needs to be able to
  budget roughly how much space a shoot will take before it is encoded.

The question asked of a profile is therefore not only "how well does it do on average?" but
"does it behave stably and predictably across different material?". This document states the
requirement rather than restating per-clip statistics; the measured record lives in the
evaluation material, and no statistic is asserted here that was not actually measured.

---

## 12. Marginal-Value-Driven Parameter Decisions

This is the core of the method. The judgement applied to a parameter is **not** "is a higher
value better?" and **not** "does theory say this analysis improves quality?". It is:

```
Parameter
  ↓
how much additional computation does it cost?
  ↓
how much quality / compression / size / stability does it buy?
  ↓
is that worth it under THIS profile's objective?
  ↓
keep  /  prune  /  adjust
```

Formally: **a parameter exists in a profile not because it improves quality in theory, but
because its marginal value under that profile's objective justifies its marginal cost.**

What counts as "gain" is deliberately plural:

- **quality gain** — visible fidelity to the source,
- **compression gain** — same delivered quality at fewer bits,
- **size reduction** — a lower ceiling actually reached,
- **stability** — behaviour that stays predictable across the corpus.

Three consequences of this rule are visible in the profiles as they stand.

**(a) The same parameter can have a positive marginal value in one profile and a negative one
in another.** The CU-tree adaptation (`cutree` — lookahead-derived temporal masking that gives
more bits to blocks that later frames reuse) is enabled in the two efficiency-oriented profiles
(FAST, SMALL) and deliberately disabled in the two quality-oriented ones (UHQ, HQ) — even though
x265 enables it by default, so the quality profiles are turning a compression tool *off*. That
is not an inconsistency: it is the same computation priced under two different objectives. Where
the binding constraint is bits (throughput efficiency, storage), the computation pays for
itself; where the binding constraint is fidelity and computation is nearly free, it does not.

**(b) What is kept is chosen by cost-per-unit-of-value, not by tier.** Whole-clip decisions
with a low per-frame cost and a large influence on rate allocation — keyframe interval,
scene-change detection, fade handling, B-pyramid, weighted prediction — survive in all four
profiles including the fastest one. Expensive per-block exhaustiveness is what gets cut first
in FAST (motion search range and algorithm, sub-pixel refinement, reference count, merge
candidates, RD/RDOQ depth) precisely because its cost is highest *and* its incremental value
is the smallest of the set.

**(c) The rule explains why pruning is not monotone.** A profile may drop a parameter in one
family while enabling one in another: SAO is off in UHQ and FAST but on in HQ and SMALL;
several analysis shortcuts are enabled in HQ while the quality-bearing whole-clip decisions
stay untouched. Each of these is a separate marginal-value verdict, not a step on a scale.

This is also why the design does not accept "this parameter is known to improve quality" as a
reason to include it — and why it does not accept "this parameter makes it faster" as a reason
to remove it either. Both halves of the exchange have to be weighed, in the profile's own
terms.

---

## 13. Three-Dimensional Evaluation

Every profile is evaluated on **speed, quality and file size / compression efficiency
together**. None of the three can carry the verdict alone.

| Profile | What would be a wrong reading | The three-dimensional claim it must actually earn |
|---|---|---|
| **FAST** | "It is fast." | Within a practical production speed range, it simultaneously holds reasonable quality **and** reasonable compression efficiency. Speed that costs either of the other two has not solved the problem. |
| **SMALL** | "Its files are small." | Spending more computation than the throughput profile is worth it for the file-size and storage-efficiency gain it produces, at acceptable quality. |
| **HQ** | "It is UHQ, cheaper." | The computation removed really was not paying for itself: the quality distance from the ceiling is small enough to be acceptable in final output. |
| **UHQ** | "It is the best one." | It establishes the ceiling and the reference. Its quality is the maximum this pipeline produces; its speed is recorded as impractical for bulk work, and the design says so. |

How the dimensions are actually observed in this project, honestly stated:

- **Speed** — measured and recorded per profile as the throughput annotation in `x265.json`.
  The published calibrations were made on the reference machine (Intel Core Ultra 9 285H,
  16C/16T). Re-measurement on the same machine under foreground load produces lower-bound
  figures (recorded in the assessment material under `docs/evaluation/`) as expected; those
  bounds are not a contradiction of the calibration, they are what contention looks like.
- **Quality** — assessed against the near-transparent region on real material, supported by the
  laboratory and third-party comparison material archived in this repository. The `--check full`
  PSNR/SSIM sampling is a per-file corruption detector with deliberately permissive thresholds
  and is explicitly documented as such; it is not the quality instrument, and this document does
  not treat it as one.
- **File size / compression efficiency** — judged against both the source and the alternatives,
  with each profile's ceiling rules making its size budget explicit (see §8). The repository
  holds a per-clip quality/size calibration matrix for the AV1 backends
  (`docs/evaluation/av1_calibration.md`); the x265 material is an assessment record
  (`docs/evaluation/x265_production_assessment.md`) rather than an equivalent matrix. That gap
  is stated here rather than papered over: the design decides on three dimensions, and where a
  dimension lacks a formal matrix in the repository, this document says so instead of inventing
  numbers.

---

## 14. Profile Lineage

The design lineage, stated once, unambiguously:

- **UHQ** — the original high-quality baseline and the quality ceiling. Every analysis at full
  strength; no speed objective.
- **HQ** — derived from UHQ by **selectively pruning computation cost**: the analyses that
  could not justify their cost under a high-quality objective were removed or relaxed, the
  quality-determining decisions were kept, and the result was validated on real material.
- **FAST** — re-optimised from the quality foundation towards **production throughput**,
  referencing the real speed/quality/size region occupied by NVENC UHQ, with priority given to
  cutting the analyses whose marginal return per unit of time is lowest.
- **SMALL** — optimised for **compression / storage efficiency**, permitted to invest more
  computation than FAST, and calibrated in reference to FAST so that the two remain a usable
  pair rather than drifting apart.

The relationships, in one diagram:

```
                      UHQ  (quality ceiling / reference baseline)
                       │
        selective computation pruning
                       │
                       ▼
                      HQ   (high-quality production output)

                      UHQ  (quality reference)
                       │
   NVENC UHQ ──────────┼────────── external real-world anchor
   (speed/quality/size region)    │
                       ▼
                 FAST  ⇄  SMALL
        (throughput)   (storage efficiency)
              └── mutual calibration ──┘
```

One implementation fact that must not be confused with the lineage: **at runtime, no profile is
derived from another.** Each of the four is a complete, self-contained parameter set in
`x265.json`, resolved independently for a source and serialised on its own. The lineage above
is a *design* relationship — it explains where the values came from and how they were judged —
and it is not a computation performed by the tool. This is deliberate: it keeps a profile's
behaviour independent of the other three, so changing one strategy cannot silently change
another.

---

## 15. Design Philosophy

Collected, the design stands on five commitments:

1. **Make the trade-off explicit instead of hiding it.** Speed, quality and size constrain
   each other; a tool that exposes one parameter set has silently chosen a point on that
   frontier for every user. Four named strategies, each stating its objective and its budget,
   turn that hidden choice into a decision the operator makes.
2. **Profile is a production strategy, not a quality tier.** UHQ is a quality ceiling, HQ a
   high-quality production strategy, FAST a throughput strategy, SMALL a storage strategy.
   None is a better version of another, and there is no ordering between them.
3. **Spend computation where its marginal value is positive — under this profile's
   objective.** The unit of judgement is the exchange rate between added computation and added
   quality/compression/size/stability, evaluated against the objective. A parameter that pays
   under one objective may be a cost under another; that is a result, not an inconsistency.
4. **Anchor to reality and validate on a corpus.** The primary target is real Sony XAVC HS
   4K60 10-bit 4:2:0 150 Mbps LongGOP material; the calibration corpus spans noise, motion,
   lighting and length; and NVENC UHQ is kept as an external anchor representing what a real
   production hardware encoder already achieves. Design conclusions that do not survive
   contact with those three references are not conclusions.
5. **Judge in three dimensions, and keep the record honest.** Speed, quality and size are
   evaluated together; measurements are cited to where they were made; values that are still
   calibration candidates are labelled as such rather than presented as settled.

What this philosophy does **not** claim:

- that there is a universally optimal x265 parameter set, or that these four profiles are it;
- that UHQ should be used when quality matters, or that FAST should be used when time matters —
  the choice is a production decision, made per job;
- that the parameter values are final. They are measured calibrations: changing one requires a
  test-set regression run (see the repository's contribution rules), and the calibration status
  of the scaling rules is recorded in `x265_scaling.json` itself.

---

## Appendix A. Current Profile Parameters

Everything below is transcribed from the configuration currently in the repository
(`x265.json`, `x265_scaling.json`). It is reference material for the design discussion above —
not a recommendation to copy these values into another encoder.

### A.0 How to read this appendix

- **Where serialisation happens.** `encoders/x265.py` maps each profile key to its x265
  parameter name (`PARAM_MAP`) and joins them into a single `-x265-params` string. FFmpeg-level
  flags (`-preset`, `-crf`, `-pix_fmt`) are built by the same module but are not part of that
  string.
- **How values are written here.** Numbers are shown exactly as they appear in `x265.json`
  (e.g. `0.70`, `1.0`); the serialiser formats floats in `%g` style, so `0.70` reaches x265 as
  `0.7` and `1.0` as `1`. String expressions such as `FR*2` are first resolved against the
  source by the scaling engine (§A.4) and serialised as the resulting integer.
- **`description`.** Each profile carries a one-line annotation. It is documentation carried in
  the configuration; the encoder path does not read it.
- **The `285H@<fps>` notation** means frames per second, measured for 4K60 material on the
  calibration machine (Intel Core Ultra 9 285H, 16C/16T). These are the recorded throughput
  calibrations referenced in §3 and §13.
- **The preset is part of the strategy.** x265's official documentation (archived at
  `docs/reference/x265/`) states that a preset sets a table of parameters and that explicitly
  supplied parameters override it. The profile therefore lists the parameters this project
  decides; the preset still governs the parameters the profile does not list (for example
  `lookahead-slices`: `slow` = 4, `fast` = 8 in the official preset table). This is why FAST
  moves the FFmpeg-side preset as well.
- **Calibration status.** `x265_scaling.json` self-declares its ratio, threshold and clamp
  values as calibration candidates (status `PROVISIONAL`); the per-profile throughput
  annotations in `x265.json` are recorded as measured author calibrations. Both statements are
  the repository's, not this document's.

### A.1 Profiles

| Annotation | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| `description` | UHQ Slowest - Not Practical 285H@0.8fps | HQ Slow - Production Ready 285H@2fps | Small - Production Ready 285H@3fps | Fast - Production Ready 285H@10fps |
| FFmpeg `-preset` | `slow` | `slow` | `slow` | `fast` |
| `-crf` | 20 | 21 | 23 | 22 |

### A.2 Full parameter table

Values are exactly as configured. "—" means the key is absent from that profile (x265's
default then applies).

| Key | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| `no_strong_intra_smoothing` | true | true | true | true |
| `rd` | 4 | 4 | 3 | 2 |
| `rdoq_level` | 2 | 1 | 1 | 0 |
| `ref` | 5 | 4 | 5 | 2 |
| `bframes` | 6 | 6 | 8 | 4 |
| `keyint` | 600 | 600 | 600 | 600 |
| `min_keyint` | `FR*0.15` | `FR*0.15` | `FR*0.25` | `FR*0.15` |
| `scenecut` | 40 | 40 | 40 | 40 |
| `hist_scenecut` | true | true | true | true |
| `fades` | true | true | true | true |
| `b_intra` | true | true | true | false |
| `b_adapt` | 2 | 2 | 2 | 2 |
| `bframe_bias` | −10 | −10 | 10 | 0 |
| `open_gop` | false | false | false | false |
| `qcomp` | 0.75 | 0.70 | 0.60 | 0.65 |
| `qblur` | 0.7 | 0.7 | 0.7 | 0.7 |
| `qpstep` | 2 | 2 | 4 | 3 |
| `ipratio` | 1.2 | 1.2 | 1.4 | 1.3 |
| `pbratio` | 1.1 | 1.1 | 1.3 | 1.2 |
| `const_vbv` | true | true | true | true |
| `vbv_maxrate` | 100000 | 80000 | 40000 | 60000 |
| `vbv_bufsize` | 300000 | 240000 | 120000 | 240000 |
| `qpmin` | 15 | 16 | 16 | 15 |
| `qpmax` | 38 | 40 | 45 | 50 |
| `me` | `star` | `umh` | `star` | `hex` |
| `subme` | 4 | 3 | 4 | 2 |
| `merange` | 57 | 57 | 57 | 16 |
| `max_merge` | 4 | 3 | 4 | 2 |
| `weightb` | true | true | true | true |
| `b_pyramid` | true | true | true | true |
| `aq_mode` | 4 | 4 | 3 | 3 |
| `aq_strength` | 1.0 | 0.9 | 0.8 | 0.8 |
| `qg_size` | 16 | 16 | 16 | 32 |
| `aq_motion` | false | false | false | false |
| `cutree` | false | false | true | true |
| `cbqpoffs` | −2 | −2 | −1 | −1 |
| `crqpoffs` | −2 | −2 | −1 | −1 |
| `ctu` | 64 | 64 | 64 | 64 |
| `min_cu_size` | 8 | 8 | 8 | 8 |
| `rect` | true | true | true | false |
| `amp` | false | false | false | false |
| `limit_tu` | 2 | 3 | 2 | 0 |
| `tu_intra_depth` | 3 | 2 | 2 | 1 |
| `tu_inter_depth` | 3 | 2 | 3 | 1 |
| `tskip` | true | true | true | false |
| `tskip_fast` | false | true | true | false |
| `psy_rd` | 1.5 | 1.5 | 1.3 | 1.5 |
| `psy_rdoq` | 1.0 | 0.8 | 0.5 | 0.0 |
| `dynamic_rd` | true | true | true | false |
| `rskip` | 0 | 1 | 0 | 2 |
| `early_skip` | false | true | true | true |
| `fast_intra` | false | false | false | true |
| `splitrd_skip` | false | false | false | true |
| `limit_modes` | false | true | false | false |
| `limit_refs` | 0 | 1 | 1 | 3 |
| `rc_lookahead` | `FR*2` | `FR*1.5` | `FR*3` | `FR*0.5` |
| `gop_lookahead` | `FR*0.1` | `FR*0.1` | `FR*0.25` | `FR*0.1` |
| `lookahead_threads` | `AUTO` | `AUTO` | `AUTO` | `AUTO` |
| `deblock` | `[-1,-1]` | `[-1,-1]` | `[0,-1]` | `[0,0]` |
| `sao` | false | true | true | false |
| `limit_sao` | false | true | false | false |
| `threaded_me` | false | false | false | false |
| `high_tier` | true | true | true | true |
| `level_idc` | 6.2 | 6.2 | 6.2 | 6.2 |
| `aud` | true | true | true | true |
| `repeat_headers` | true | true | true | true |
| `hrd` | true | true | true | true |
| `info` | false | false | false | false |
| `weightp` | true | — | — | — |

Notes on the table:

- Keys identical across all four profiles (`keyint`, `scenecut`, `hist_scenecut`, `fades`,
  `b_adapt`, `open_gop`, `qblur`, `const_vbv`, `weightb`, `b_pyramid`, `aq_motion`, `ctu`,
  `min_cu_size`, `amp`, `lookahead_threads`, `threaded_me`, `high_tier`, `level_idc`, `aud`,
  `repeat_headers`, `hrd`, `info`, `no_strong_intra_smoothing`) are the shared spine of the
  design: decisions no profile's objective argues about.
- `weightp` is stated explicitly only in UHQ; the other profiles leave it to x265's default.
  The archived official CLI reference documents that default as enabled.
- `rdpenalty` is the one key present in `encoders/x265.py`'s `PARAM_MAP` that no profile sets;
  it exists for parameter coverage, not as a per-profile decision.

### A.3 Dynamic VBV ceilings (per profile × source class)

From `x265_scaling.json`. Ceiling = `clamp(OB × target_ratio, OB × min_ratio, OB × max_ratio)`
where `OB` is the source container bitrate; buffer = `round(ceiling × bufsize_factor)`, with
`bufsize_factor` = 3.0 in every row. CRF remains the primary quality control; this is a local
bitrate/size ceiling.

| Source class | UHQ (min/target/max) | HQ | SMALL | FAST |
|---|---|---|---|---|
| `HIGH_BITRATE_LONG_GOP` | 0.55 / 0.70 / 0.85 | 0.35 / 0.50 / 0.65 | 0.20 / 0.30 / 0.40 | 0.30 / 0.40 / 0.50 |
| `NORMAL_LONG_GOP` | 0.70 / 0.85 / 1.00 | 0.60 / 0.75 / 0.90 | 0.40 / 0.55 / 0.70 | 0.50 / 0.65 / 0.80 |
| `LOW_BITRATE_LONG_GOP` | 0.90 / 1.00 / 1.25 | 0.85 / 1.00 / 1.15 | 0.75 / 0.90 / 1.05 | 0.80 / 0.95 / 1.10 |
| `INTRA_LIKE` | 0.30 / 0.40 / 0.55 | 0.25 / 0.35 / 0.45 | 0.15 / 0.25 / 0.35 | 0.20 / 0.30 / 0.40 |

Every row is marked `PROVISIONAL` in the file (and the `INTRA_LIKE` row
`PROVISIONAL_UNCALIBRATED`, since no genuine All-I source is available). A class with no entry
falls back to the profile's static VBV values from §A.1/A.2.

### A.4 Normalisation rules

From `x265_scaling.json`. Reference: **3840×2160 @ 59.94 fps**. Modes:

| Mode | Meaning | Applied to |
|---|---|---|
| `fixed` | keep the base-profile value | everything not listed below — including `crf`, `rd`, and the psychovisual/AQ settings, which are **intentionally never scaled** |
| `fps` | evaluate `FR*` against the source frame rate, optionally capped | `rc_lookahead` (cap 200), `gop_lookahead` (cap 200), `min_keyint` (intentionally uncapped) |
| `sqrt_pixels` | scale by `sqrt((W×H) / (3840×2160))`, clamped | `merange` (clamp 16–92) |
| `pixel_rate` | scale by `(W×H×fps) / (3840×2160×59.94)` | available to the engine; not currently used by any x265 parameter |

The source classification is metadata-only — normalised bits per pixel-frame against the
container bitrate, with threshold values in the same file marked `PROVISIONAL`. It makes no
claim about GOP structure or frame types.

### A.5 Design decisions visible in this appendix

Reading §A.2–A.4 against the body of this document:

- **The shared spine** (identical across all four) is what the design treats as
  non-negotiable across objectives.
- **The quality-side prunes** in HQ and FAST (RD/RDOQ depth, search range and algorithm,
  reference count, merge candidates, transform depths, early-exit family) are the
  marginal-value verdicts of §6 and §7.
- **`cutree` inverted between the quality profiles and the efficiency profiles** is the
  clearest single artefact of §12: the same computation priced under two objectives.
- **The size ceilings in §A.3** are ordered (UHQ > HQ > FAST > SMALL on high-bitrate
  material) while the profiles themselves are not ranked — the size axis is what each profile
  explicitly budgets.
- **`crf`, `rd`, `psy` and `aq` are never normalised** — the design keeps quality decisions
  out of the resolution/frame-rate adaptation path, so a profile means the same thing on
  different source geometries.
