"""Build the generated control fixtures and inventory the whole corpus.

Usage::

    python -m tests.hwdecode.inventory [--force] [--full]

``--full`` also counts packets in every real-corpus file (slow); the
default mode reads only container tables and the leading-picture scan,
which is what the matrix needs for routing decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.hwdecode import fixtures as FX  # noqa: E402
from tests.hwdecode.probe import probe_input  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="rebuild generated fixtures even if present")
    ap.add_argument("--full", action="store_true",
                    help="count packets for every fixture (slow)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    FX.WORK.mkdir(parents=True, exist_ok=True)

    print("== building generated controls ==")
    gen = FX.build_generated(force=args.force)
    for fid, facts in sorted(gen.items()):
        print(f"  {fid:24s} {facts.get('container_samples')} samples "
              f"lead={facts.get('leading_pictures')}")

    print("== inventory ==")
    out = {"generated": gen, "fixtures": {}}
    rows = []
    for fx in FX.all_fixtures():
        if fx.generated:
            continue
        p = fx.path
        if not p.is_file():
            out["fixtures"][fx.fid] = {"path": str(p), "exists": False}
            rows.append((fx.fid, "MISSING", "", "", "", ""))
            continue
        facts = probe_input(p, input_id=fx.fid, count_packets=args.full)
        out["fixtures"][fx.fid] = facts.to_json()
        v = facts.video
        rows.append((
            fx.fid,
            f"{v.codec}/{v.profile}" if v else "?",
            f"{v.width}x{v.height}" if v else "?",
            f"{facts.container_samples}" if facts.container_samples else "?",
            f"{facts.leading_pictures}" if facts.leading_pictures is not None else "?",
            fx.group,
        ))

    w = max(len(r[0]) for r in rows) + 2
    print(f"{'fixture'.ljust(w)}{'codec':22s}{'size':11s}{'smpl':>7s}{'lead':>6s}  group")
    for fid, codec, size, smpl, lead, group in rows:
        print(f"{fid.ljust(w)}{codec:22s}{size:11s}{smpl:>7s}{lead:>6s}  {group}")

    dest = Path(args.out) if args.out else (FX.WORK / "matrix-corpus.json")
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {dest}")
    print(f"fixtures: {len(rows)} ({sum(1 for r in rows if r[1] == 'MISSING')} missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
