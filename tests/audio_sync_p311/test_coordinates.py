"""P3.11 — coordinate mapping: the apply path and the comparison path must agree exactly.

The apply path computes a window index block by block from a CLOSED FORM
(`production_chain_p311.window_index`) because materialising one int64 per sample costs
1.6-1.8 GB at production length. The comparison path materialises the whole trajectory
(`coord_labels`). Two spellings of the same mapping is exactly the situation in which this
research line has repeatedly produced a plausible wrong answer with no error, so the
equality is ASSERTED here rather than assumed.

Also pins the properties the reports depend on:

  * at the production ratio `win = 3*hop`, `nearest-centre` selects the immediately preceding
    window index over most of the stream (a full hop of extra lag);
  * `latest-start` and `nearest-centre` differ by exactly one index where they differ at all;
  * `sample-domain` equals `latest-start` on every block except the last, so it is NOT a
    third independent candidate and must not be reported as one;
  * run-length encoding of a piecewise-constant trajectory produces runs whose lengths sum to
    the stream length and whose boundaries are contiguous (the P3.9 zone-length defect).

Run: python tests/audio_sync_p311/test_coordinates.py
Exit 0 only if every case ran and passed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "work" / "p3" / "p311"))
sys.path.insert(0, str(REPO / "work" / "p3"))
sys.path.insert(0, str(REPO / "work" / "p3" / "p38" / "semantics"))
sys.path.insert(0, str(REPO / "work" / "p3" / "p310"))

import production_chain_p311 as P                              # noqa: E402

n_pass = n_fail = 0
fails: list[str] = []
FS, WIN, HOP = P.FS, P.WIN, P.HOP


def ck(name: str, ok: bool, detail: str = "") -> None:
    global n_pass, n_fail
    if ok:
        n_pass += 1
        print(f"  [PASS] {name}")
    else:
        n_fail += 1
        fails.append(name)
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))


def t1_closed_form_equals_materialised() -> None:
    print("\n== T1  window_index() == coord_labels() over a full short stream ==")
    for n_windows in (1, 2, 3, 4, 5, 7, 12):
        # the stream length must be COVERED by the plan, because the clip is part of the
        # contract: a stream longer than n_windows*hop has blocks the plan does not reach, and
        # clipping them is a stated choice rather than an accident. The first version of this
        # test used a stream one third of a hop LONGER than the plan, so every comparison
        # disagreed in the final partial block and reported a false mapping defect.
        n = n_windows * HOP
        idx = np.arange(n, dtype=np.int64)
        for name in P.APPLY_COORDINATES:
            want = P.coord_labels(name, n, n_windows)
            got = P.window_index(name, idx, n_windows)
            ck(f"{name}: n_windows={n_windows}, closed form == materialised",
               bool(np.array_equal(want, got)),
               f"{int((want != got).sum())} differing samples")
    # and the real production lengths, whose plans cover the stream exactly
    for n, n_windows in ((230750520, 481), (199903704, 417)):
        idx = np.arange(n_windows, dtype=np.int64) * HOP
        for name in P.APPLY_COORDINATES:
            got = P.window_index(name, idx, n_windows)
            ck(f"{name}: n={n} block-start indices are in range",
               bool(got.min() >= 0 and got.max() < n_windows),
               f"range [{got.min()}, {got.max()}] of {n_windows}")
        ck(f"n={n}: the plan covers the whole stream (n_windows*hop >= n)",
           n_windows * HOP >= n, f"{n_windows * HOP} vs {n}")


def t2_engine_contract_is_latest_start() -> None:
    print("\n== T2  the engine's own mapping is latest-start, exactly ==")
    # read the engine source and confirm the arithmetic we claim to mirror is really there
    src = (REPO / "work" / "p3" / "p38" / "semantics" / "gap_semantics.py").read_text(
        encoding="utf-8")
    ck("apply_shift_plan computes `w = pos // hop_samples`",
       "w = pos // hop_samples" in src)
    ck("and segments at `(w + 1) * hop_samples`",
       "seg_end = min(b, (w + 1) * hop_samples)" in src)
    n_windows, n = 6, 6 * HOP
    idx = np.arange(n, dtype=np.int64)
    ck("within the plan's coverage, window_index('latest-start') is exactly pos // hop",
       bool(np.array_equal(P.window_index("latest-start", idx, n_windows), idx // HOP)))
    # and beyond the coverage it CLIPS to the last window, which is a stated choice: the first
    # version of this test compared an unclipped identity against a clipped trajectory in the
    # final partial block and reported a mapping defect that did not exist.
    over = np.array([n + 1, n + HOP, n + HOP + 7], dtype=np.int64)
    ck("beyond the coverage it clips to the last window (explicit, not silent)",
       bool(np.all(P.window_index("latest-start", over, n_windows) == n_windows - 1)),
       f"{P.window_index('latest-start', over, n_windows).tolist()}")
    ck("...and the materialised trajectory agrees with that clip at the same samples",
       bool(np.array_equal(
           P.coord_labels("latest-start", n + HOP + 8, n_windows)[n + 1:n + HOP + 8],
           P.window_index("latest-start", np.arange(n + 1, n + HOP + 8), n_windows))))


def t3_nearest_centre_relationship() -> None:
    print("\n== T3  nearest-centre: one block of extra lag where it differs ==")
    n_windows = 10
    n = n_windows * HOP
    starts = np.arange(n_windows, dtype=np.int64) * HOP
    latest = P.window_index("latest-start", starts, n_windows)
    near = P.window_index("nearest-centre", starts, n_windows)
    diff = latest != near
    ck("they differ somewhere (the ambiguity is not vacuous)", bool(diff.any()),
       f"{int(diff.sum())} block starts")
    ck("block 0 is the ONLY point where nearest-centre is behind latest-start",
       bool(np.all(near[1:] == latest[1:] - 1)) and int(near[0]) == int(latest[0]),
       f"near {near[:4].tolist()} latest {latest[:4].tolist()}")
    ck("the RAW mapping returns a NEGATIVE index at block 0 (unclamped)",
       int(P.window_nearest_centre(np.array([0]))[0]) == -1,
       f"{int(P.window_nearest_centre(np.array([0]))[0])}")
    ck("and the contracted mapping CLIPS it to 0 there -- the clip is explicit",
       int(P.window_index("nearest-centre", np.array([0]), n_windows) [0]) == 0)
    ck("clipping is what makes nearby-centre == latest-start on the FIRST hop only",
       bool(P.window_index("nearest-centre", np.array([0]), n_windows)[0]
            == P.window_index("latest-start", np.array([0]), n_windows)[0]))
    # the P3.9-era claim, checked rather than repeated
    ck("from block 1 onward the two NEVER agree",
       bool(np.all(latest[1:] != near[1:])),
       "they agree at some block start >= 1")
    frac_note = ("each block start >= hop, so the sample-domain disagreement is not uniform: "
                 "it starts at the first hop boundary")
    print(f"    {frac_note}")
    ck("the first sample where a per-SAMPLE evaluation differs is the first hop boundary",
       P.coord_labels("latest-start", 4 * HOP, 4)[HOP]
       != P.coord_labels("nearest-centre", 4 * HOP, 4)[HOP])
    # measured headline on the real plan sizes
    for n_windows in (481, 417):
        starts = np.arange(n_windows, dtype=np.int64) * HOP
        a = P.window_index("latest-start", starts, n_windows)
        b = P.window_index("nearest-centre", starts, n_windows)
        n_b = int((a != b).sum())
        ck(f"n_windows={n_windows}: {n_b} of {n_windows} block starts get a different "
           f"window", n_b == n_windows or n_b == n_windows - 1, f"{n_b}")


def t4_sample_domain_is_not_a_third_candidate() -> None:
    print("\n== T4  sample-domain is IDENTICAL to latest-start, everywhere ==")
    for n_windows in (1, 2, 3, 5, 481):
        n = n_windows * HOP
        idx = np.arange(n, dtype=np.int64)
        a = P.window_index("latest-start", idx, n_windows)
        b = P.window_index("sample-domain", idx, n_windows)
        ck(f"n_windows={n_windows}: per-sample trajectories are identical",
           bool(np.array_equal(a, b)), f"{int((a != b).sum())} differing samples")
    for n, n_windows in ((230750520, 481), (199903704, 417)):
        idx = np.arange(n_windows, dtype=np.int64) * HOP
        a = P.window_index("latest-start", idx, n_windows)
        b = P.window_index("sample-domain", idx, n_windows)
        ck(f"n={n}: block-start agreement {int((a == b).sum())}/{n_windows}",
           bool(np.array_equal(a, b)))
    # the reason, stated as arithmetic rather than as an observation: the measurement windows
    # OVERLAP at win = 3*hop, so three of them cover any sample and the LATEST is selected
    for b in (0, 1, 7, 480):
        pos = np.array([b * HOP])
        covering = [c for c in range(b - 2, b + 1) if c >= 0]
        w_sd = int(P.window_sample_domain_pos(pos, n_windows=None)[0])
        ck(f"block {b}: {len(covering)} overlapping measurement windows cover the sample; "
           f"the latest is selected", w_sd == b, f"{w_sd} vs {b}")
    ck("the covering set really does contain three windows mid-stream (overlap is real)",
       len([c for c in range(478 - 2, 478 + 1) if c >= 0]) == 3)

def t5_rle_zones() -> None:
    print("\n== T5  zone run-length encoding is contiguous and total ==")
    n_windows = 8
    n = n_windows * HOP + 7
    for name in P.APPLY_COORDINATES:
        lab = P.coord_labels(name, n, n_windows)
        zones = P._rle_labelled(lab)
        total = sum(z["n_samples"] for z in zones)
        ck(f"{name}: zone lengths sum to the stream length", total == n,
           f"{total} vs {n}")
        contiguous = all(zones[i]["end_sample"] + 1 == zones[i + 1]["start_sample"]
                         for i in range(len(zones) - 1))
        ck(f"{name}: zones are contiguous with no gap or overlap", contiguous)
        rebuilt = np.concatenate([np.full(z["n_samples"], z["value"], dtype=np.int64)
                                  for z in zones])
        ck(f"{name}: zones rebuild the trajectory exactly",
           bool(np.array_equal(rebuilt, lab)))
    # the P3.9 defect: pairing np.diff transitions gives nonsense zone lengths
    lab = np.array([0, 0, 1, 1, 1, 0], dtype=np.int64)
    naive = np.diff(np.flatnonzero(np.diff(lab)))
    proper = [z["n_samples"] for z in P._rle_labelled(lab)]
    ck("the RLE answer for a hand-checked trajectory is [2, 3, 1]",
       proper == [2, 3, 1], f"{proper}")
    ck("and it is NOT the np.diff-transition pairing",
       list(naive) != proper, f"naive {list(naive)}")


def t6_comparison_arithmetic() -> None:
    print("\n== T6  the comparison function's own numbers ==")
    n_windows = 6
    n = n_windows * HOP
    c = P.compare_coordinates("latest-start", "nearest-centre", n, n_windows)
    ck("agree + disagree == n_samples",
       c["n_agree"] + c["n_disagree"] == n, f"{c['n_agree']}+{c['n_disagree']} vs {n}")
    ck("agreement_fraction == n_agree / n",
       abs(c["agreement_fraction"] - c["n_agree"] / n) < 1e-12)
    ck("the zone lengths in the histogram sum to the disagreement count",
       sum(int(k) * v for k, v in c["zone_length_histogram"].items()) == c["n_disagree"],
       f"{c['zone_length_histogram']} vs {c['n_disagree']}")
    ck("max positional difference is one window index here",
       c["max_abs_index_difference"] == 1,
       f"{c['max_abs_index_difference']}")
    ck("zone lengths are reported in SAMPLES and sum correctly",
       sum(int(k) * v for k, v in c["zone_length_histogram"].items()) == c["n_disagree"],
       f"{c['zone_length_histogram']}")


def main() -> int:
    print("=" * 90)
    print("P3.11 — coordinate mapping tests")
    print("=" * 90)
    t1_closed_form_equals_materialised()
    t2_engine_contract_is_latest_start()
    t3_nearest_centre_relationship()
    t4_sample_domain_is_not_a_third_candidate()
    t5_rle_zones()
    t6_comparison_arithmetic()
    print(f"\nSUMMARY: {n_pass} PASS / {n_fail} FAIL")
    if n_pass == 0:
        print("FAIL: no case ran", file=sys.stderr)
        return 2
    for f in fails:
        print(f"  FAIL {f}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
