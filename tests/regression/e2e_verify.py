#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent verification of a transparent-mode output container.

Deliberately does **not** call the code under test for the facts that matter:

  * container structure  -> a box walker written here, not
    `preservation.isobmf` (that module is what writes the boxes);
  * per-stream integrity -> `ffmpeg -c copy -f streamhash`, an external
    program; a stream that was only stream-copied must hash identically;
  * stream order         -> positional, because downstream tooling addresses
    tracks by index;
  * residual alignment   -> the production estimator re-run on **both** the
    source and the output, then checked against the accounting identity

        residual_out(T, A) == residual_src(T, A) - shift(T) + shift(A)

    which holds for every track whatever the anchor did.  Only `fixed` tracks
    are required to land near zero; a refused (`untouched`) track is *supposed*
    to keep its offset — moving it would be the bug.

Gyroflow is the one exception: it is the *consumer* whose misbehaviour the
metadata work exists to prevent, so asking it directly is the point.

Usage:
    python e2e_verify.py --src IN.MP4 --out OUT.MP4 --json R.json \\
        [--shifts '{"audio[1]": 944}'] [--anchor 'audio[2]']
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
SCRATCH = Path(r"E:\1kt_scratch\e2everify")
SR = 48000


# ---------------------------------------------------------------------------
# minimal, independent ISO-BMFF walker
# ---------------------------------------------------------------------------
class Box:
    __slots__ = ("type", "start", "data_start", "end", "size")

    def __init__(self, typ, start, data_start, end, size):
        self.type, self.start, self.data_start = typ, start, data_start
        self.end, self.size = end, size


def _boxes(f, start: int, end: int) -> list[Box]:
    out = []
    off = start
    while off + 8 <= end:
        f.seek(off)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr)
        data = off + 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            data = off + 16
        elif size == 0:
            size = end - off
        if size < 8 or off + size > end:
            break
        out.append(Box(typ.decode("latin-1"), off, data, off + size, size))
        off += size
    return out


def _find(boxes, typ):
    return next((b for b in boxes if b.type == typ), None)


def _trak_handler(f, trak: Box) -> str | None:
    """handler_type lives in trak/mdia/hdlr (hdlr is a FullBox)."""
    mdia = _find(_boxes(f, trak.data_start, trak.end), "mdia")
    if mdia is None:
        return None
    hdlr = _find(_boxes(f, mdia.data_start, mdia.end), "hdlr")
    if hdlr is None:
        return None
    f.seek(hdlr.data_start + 8)
    return f.read(4).decode("latin-1", "replace")


def _uuid_of(f, b: Box) -> str:
    f.seek(b.data_start)
    h = f.read(16).hex().upper()
    return f"{{{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}"


def _sha_of(f, b: Box) -> str:
    f.seek(b.start)
    return hashlib.sha256(f.read(b.size)).hexdigest()


def structure(path: Path) -> dict:
    """Top-level layout + the container facts the metadata work is about."""
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = _boxes(f, 0, end)
        facts: dict = {"top_level": [b.type for b in top], "uuid": [],
                       "tref_cdsc": [], "nrtm_seen": False, "meta_scopes": []}

        ftyp = _find(top, "ftyp")
        if ftyp is not None:
            f.seek(ftyp.data_start)
            raw = f.read(min(ftyp.end - ftyp.data_start, 64))
            facts["ftyp_major_brand"] = raw[0:4].decode("latin-1")
            facts["ftyp_compatible"] = [
                raw[i:i + 4].decode("latin-1")
                for i in range(8, len(raw) - 3, 4)
            ]

        for b in top:
            if b.type == "uuid":
                facts["uuid"].append({"ctx": "root", "guid": _uuid_of(f, b),
                                      "sha": _sha_of(f, b)})
            elif b.type == "meta":
                # nrtm (lens-profile archive) is an *item* of a top-level
                # `meta` box on Sony XAVC files — not inside `moov`.
                facts["meta_scopes"].append("root")
                if _meta_has_nrtm(f, b):
                    facts["nrtm_seen"] = True

        moov = _find(top, "moov")
        if moov is None:
            facts["uuid"].sort(key=lambda d: (d["ctx"], d["guid"], d["sha"]))
            return facts
        for b in _boxes(f, moov.data_start, moov.end):
            if b.type == "uuid":
                facts["uuid"].append({"ctx": "moov", "guid": _uuid_of(f, b),
                                      "sha": _sha_of(f, b)})
            elif b.type == "meta":
                facts["meta_scopes"].append("moov")
                if _meta_has_nrtm(f, b):
                    facts["nrtm_seen"] = True
            elif b.type == "trak":
                handler = _trak_handler(f, b)
                for sub in _boxes(f, b.data_start, b.end):
                    if sub.type == "uuid":
                        facts["uuid"].append(
                            {"ctx": f"trak:{handler}",
                             "guid": _uuid_of(f, sub), "sha": _sha_of(f, sub)})
                    elif sub.type == "tref":
                        for ref in _boxes(f, sub.data_start, sub.end):
                            if ref.type != "cdsc":
                                continue
                            n = (ref.end - ref.data_start) // 4
                            f.seek(ref.data_start)
                            facts["tref_cdsc"].append(
                                {"handler": handler,
                                 "refs": list(struct.unpack(f">{n}I",
                                                            f.read(4 * n)))})
    facts["uuid"].sort(key=lambda d: (d["ctx"], d["guid"], d["sha"]))
    facts["tref_cdsc"].sort(key=lambda d: (str(d["handler"]), d["refs"]))
    return facts


def _meta_has_nrtm(f, meta: Box) -> bool:
    f.seek(meta.start)
    return b"nrtm" in f.read(meta.end - meta.start)


# ---------------------------------------------------------------------------
# external facts
# ---------------------------------------------------------------------------
def probe(path: Path) -> dict:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    return json.loads(r.stdout or "{}")


def stream_layout(d: dict) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for s in d.get("streams", []):
        t = s.get("codec_type", "?")
        out.append(f"{t}[{seen.get(t, 0)}]:"
                   f"{s.get('codec_tag_string') or s.get('codec_name')}")
        seen[t] = seen.get(t, 0) + 1
    return out


def typed_key(d: dict) -> dict[str, int]:
    seen: dict[str, int] = {}
    m = {}
    for i, s in enumerate(d.get("streams", [])):
        t = s.get("codec_type", "?")
        m[f"{t}[{seen.get(t, 0)}]"] = i
        seen[t] = seen.get(t, 0) + 1
    return m


def streamhash(path: Path, idx: int) -> str:
    r = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
         "-map", f"0:{idx}", "-c", "copy", "-f", "streamhash",
         "-hash", "sha256", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    if r.returncode != 0:
        return "ERR"
    return r.stdout.strip().split(",")[-1]


# ---------------------------------------------------------------------------
# residual measured on a container, with the production estimator
# ---------------------------------------------------------------------------
def residuals_on(path: Path, layout: dict[str, int],
                 anchor_key: str) -> dict:
    from core.channel_sync import effective_opts
    from core.sync_estimate import estimate_pair, open_source

    eff = effective_opts(None)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    a_idx = layout.get(anchor_key)
    if a_idx is None:
        return {"error": f"anchor {anchor_key} missing"}
    res: dict = {}
    try:
        for key in sorted(layout):
            if key == anchor_key or not key.startswith("audio"):
                continue
            ra, rb = SCRATCH / "a.f32", SCRATCH / "b.f32"
            ok = True
            for idx, dst in ((a_idx, ra), (layout[key], rb)):
                r = subprocess.run(
                    [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i",
                     str(path), "-map", f"0:{idx}", "-vn", "-f", "f32le",
                     "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SR),
                     str(dst)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = ok and r.returncode == 0
            if not ok:
                res[key] = {"error": "decode failed"}
                continue
            A, B = open_source(ra, "f32"), open_source(rb, "f32")
            try:
                e = estimate_pair(
                    A, B, sample_rate=SR,
                    search_window_ms=eff["search_window_ms"],
                    frame_ms=eff["frame_ms"], hop_ms=eff["hop_ms"],
                    anchor_segment_seconds=eff["anchor_segment_seconds"],
                    min_confidence=eff["min_confidence"],
                    coarse_rate=eff["coarse_rate"],
                    fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                    min_usable_frames=eff["min_usable_frames"],
                    frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                    ref_index=a_idx, tgt_index=layout[key])
                res[key] = {"delay_ms": round(e.delay_ms, 4),
                            "delay_samples": round(e.delay_samples, 2),
                            "usable_frames": e.usable_frames,
                            "confidence": round(e.confidence, 3)}
            except Exception as exc:                        # noqa: BLE001
                res[key] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                for s in (A, B):
                    c = getattr(s, "close", None)
                    if callable(c):
                        c()
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    return res


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--anchor", default="audio[2]")
    ap.add_argument("--shifts", default="{}",
                    help='JSON map "audio[i]" -> applied integer shift')
    ap.add_argument("--no-residual", action="store_true")
    a = ap.parse_args()

    src, out = Path(a.src), Path(a.out)
    shifts = {k: int(v) for k, v in json.loads(a.shifts).items()}
    ps, po = probe(src), probe(out)
    ks, ko = typed_key(ps), typed_key(po)

    rep: dict = {"src": str(src), "out": str(out), "shifts": shifts}
    fs, fo = ps.get("format") or {}, po.get("format") or {}
    rep["format"] = {
        "duration_src": float(fs.get("duration") or 0),
        "duration_out": float(fo.get("duration") or 0),
        "duration_delta_s": round(float(fo.get("duration") or 0)
                                  - float(fs.get("duration") or 0), 6),
        "nb_streams_src": int(fs.get("nb_streams") or 0),
        "nb_streams_out": int(fo.get("nb_streams") or 0),
    }
    ls, lo = stream_layout(ps), stream_layout(po)
    rep["stream_layout"] = {"src": ls, "out": lo, "identical": ls == lo}
    rep["stream_order_preserved"] = ls == lo

    hashes = {}
    for key in sorted(ks):
        if key not in ko:
            hashes[key] = {"present": False}
            continue
        hashes[key] = {"present": True,
                       "same": streamhash(src, ks[key])
                       == streamhash(out, ko[key])}
    rep["packet_streamhash"] = hashes
    rep["streams_changed"] = sorted(k for k, v in hashes.items()
                                    if v.get("present") and not v["same"])
    rep["streams_lost"] = sorted(k for k, v in hashes.items()
                                 if not v.get("present"))

    st_s, st_o = structure(src), structure(out)
    rep["structure"] = {
        "src": {k: v for k, v in st_s.items() if k != "uuid"},
        "out": {k: v for k, v in st_o.items() if k != "uuid"},
    }
    # multiplicity matters: six per-track USMT uuids are six boxes, not one
    key_s = [(u["ctx"], u["guid"], u["sha"]) for u in st_s["uuid"]]
    key_o = [(u["ctx"], u["guid"], u["sha"]) for u in st_o["uuid"]]
    rep["uuid"] = {
        "src_count": len(key_s), "out_count": len(key_o),
        "missing": sorted(f"{c}/{g}" for c, g, _ in
                          set(key_s) - set(key_o)),
        "added": sorted(f"{c}/{g}" for c, g, _ in set(key_o) - set(key_s)),
        "payload_differs": sorted(
            f"{c}/{g}" for c, g, h in set(key_s)
            if (c, g, h) not in set(key_o) and any(
                c2 == c and g2 == g for c2, g2, _ in set(key_o))),
    }
    rep["structure_match"] = {
        "ftyp_major_brand_same":
            st_s.get("ftyp_major_brand") == st_o.get("ftyp_major_brand"),
        "ftyp_major_brand_src": st_s.get("ftyp_major_brand"),
        "ftyp_major_brand_out": st_o.get("ftyp_major_brand"),
        "tref_cdsc_same": st_s["tref_cdsc"] == st_o["tref_cdsc"],
        "tref_cdsc_src": st_s["tref_cdsc"],
        "tref_cdsc_out": st_o["tref_cdsc"],
        "nrtm_src": st_s["nrtm_seen"], "nrtm_out": st_o["nrtm_seen"],
    }

    if not a.no_residual and any(k.startswith("audio") for k in ko):
        r_src = residuals_on(src, ks, a.anchor)
        r_out = residuals_on(out, ko, a.anchor)
        rep["residual"] = {"src": r_src, "out": r_out}
        sh_a = shifts.get(a.anchor, 0)
        verdict = {}
        for key in sorted(r_src):
            s, o = r_src.get(key) or {}, r_out.get(key) or {}
            if ("delay_samples" not in s or "delay_samples" not in o
                    or s["delay_samples"] != s["delay_samples"]
                    or o["delay_samples"] != o["delay_samples"]):
                # Unmeasurable is NOT a pass and NOT a failure: a silent or
                # decoherent track has no residual to speak of. Recorded as
                # its own verdict so it can never be counted as green.
                verdict[key] = {"ok": None, "verdict": "UNMEASURABLE",
                                "src_usable_frames": s.get("usable_frames"),
                                "out_usable_frames": o.get("usable_frames")}
                continue
            sh_t = shifts.get(key, 0)
            expect = s["delay_samples"] - sh_t + sh_a
            got = o["delay_samples"]
            delta = abs(got - expect)
            moved = sh_t != 0
            ok = delta <= 1.0 and (not moved or abs(got) <= 1.0)
            verdict[key] = {
                "ok": ok,
                "verdict": "PASS" if ok else "FAIL",
                "shift": sh_t,
                "expected_out_samples": round(expect, 2),
                "observed_out_samples": got,
                "accounting_delta_samples": round(delta, 2),
                "accounting_ok": delta <= 1.0,
                "residual_near_zero": abs(got) <= 1.0,
            }
        rep["residual_verdict"] = verdict

    try:
        from preservation.gyroflow import check as gyro_check, find_gyroflow
        gf = find_gyroflow(None)
        if gf is None:
            rep["gyroflow"] = {"status": "UNAVAILABLE"}
        else:
            g = gyro_check(src, out, gf, SCRATCH / "gyro")
            rep["gyroflow"] = {"status": g.get("status"),
                               "detail": str(g.get("detail"))[:400]}
    except Exception as exc:                                # noqa: BLE001
        rep["gyroflow"] = {"status": "ERROR",
                           "detail": f"{type(exc).__name__}: {exc}"}

    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    print(_summary(rep))
    if a.json:
        print(f"-> {a.json}")
    return 0


def _summary(rep: dict) -> str:
    st = rep["structure_match"]
    rv = rep.get("residual_verdict") or {}
    bad = [k for k, v in rv.items() if v.get("ok") is False]
    skipped = [k for k, v in rv.items() if v.get("ok") is None]
    return (
        f"order={'SAME' if rep['stream_order_preserved'] else 'DIFF'} "
        f"ftyp={'SAME' if st['ftyp_major_brand_same'] else 'DIFF'}"
        f"({st['ftyp_major_brand_src']}->{st['ftyp_major_brand_out']}) "
        f"tref={'SAME' if st['tref_cdsc_same'] else 'DIFF'} "
        f"nrtm={st['nrtm_src']}->{st['nrtm_out']} "
        f"uuid={rep['uuid']['src_count']}->{rep['uuid']['out_count']}"
        f"(missing {len(rep['uuid']['missing'])}) "
        f"dur_delta={rep['format']['duration_delta_s']:+.6f}s "
        f"changed={rep['streams_changed']} lost={rep['streams_lost']} "
        f"residual_bad={bad} residual_unmeasurable={skipped} "
        f"gyro={(rep.get('gyroflow') or {}).get('status')}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
