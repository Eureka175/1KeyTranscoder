"""DJI (Osmo Action / drone) metadata-preservation checks.

DJI files carry per-frame motion data (quaternions) in the djmd meta
track (handler meta, sample entry djmd), plus dbgi (debug info) and a
tmcd timecode track. Key facts:

- MP4Box -diso XML parsing FAILS on DJI files (the hidden mjpeg cover
  track's sample entry breaks ElementTree) — all track enumeration
  here goes through MP4Box -info text parsing (gpac.info/parse_info).
- The mjpeg cover track and movie-level udta/mdta are NOT addressable
  by GPAC 26.02 (-add src#5/#6 fails, no udta copy option) — they are
  dropped by policy and logged, never silently.
- Gyroflow officially supports DJI Action 4/5/6, Avata, Neo etc. DJI
  gyro has the lens profile built in and needs no sync: quaternions
  map to frames by index. Gyroflow's type-3 export ("camera data")
  carries the per-frame org_quat; type-2 ("parsed metadata") carries
  detected_source / lens profile / timestamp accuracy.
- In-camera stabilization (Rocksteady/EIS) footage has NO gyro data:
  the djmd track exists but the quaternion lists are empty. Both sides
  must compare equal either way (empty == empty passes).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from .gpac import GpacContainerBackend

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
PRESERVED = "PRESERVED"
MODIFIED = "MODIFIED"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"

# data-track sample entries preserved verbatim from DJI sources
DJI_DATA_ENTRIES = ("djmd", "dbgi", "tmcd")

# per-sample float comparison tolerance (identical source bytes ->
# identical exports in practice; tolerate serialization jitter)
QUAT_EPS = 1e-4
READOUT_EPS = 0.05  # ms


def track_manifest(
    gpac: GpacContainerBackend, source: Path
) -> tuple[int, list[dict]]:
    """(movie_timescale, tracks) via MP4Box -info (diso-free)."""
    return gpac.parse_info(gpac.info(source))


def dji_track_specs(tracks: list[dict]) -> dict[str, Any]:
    """Split -info tracks into video id / audio ids / data (id, entry)."""
    video_id = None
    audio_ids: list[int] = []
    data_ids: list[tuple[int, str]] = []
    for t in tracks:
        if t["handler"] == "vide" and video_id is None:
            video_id = t["id"]
        elif t["handler"] == "soun":
            audio_ids.append(t["id"])
        elif t["entry"] in DJI_DATA_ENTRIES:
            data_ids.append((t["id"], t["entry"]))
    return {
        "video_id": video_id,
        "audio_ids": audio_ids,
        "data_ids": data_ids,
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _export(
    gyroflow: Path, video: Path, out_json: Path, export_type: int
) -> Any:
    """Gyroflow headless metadata export (type 2 parsed / 3 camera)."""
    video = video.resolve()
    out_json = out_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if out_json.exists():
        out_json.unlink()
    cmd = [
        str(gyroflow),
        str(video),
        "--export-metadata",
        f"{export_type}:{out_json}",
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
    )
    if not out_json.is_file():
        raise RuntimeError(
            f"Gyroflow produced no export (type {export_type}) for "
            f"{video.name} (rc={proc.returncode}):\n"
            f"{(proc.stdout or '')[-800:]}"
        )
    return json.loads(out_json.read_text(encoding="utf-8"))


def parsed_facts(parsed: dict) -> dict[str, Any]:
    """DJI-relevant facts from a type-2 (parsed metadata) export."""
    return {
        "detected_source": str(parsed.get("detected_source") or ""),
        "has_lens_profile": parsed.get("lens_profile") is not None,
        "has_accurate_timestamps": bool(
            parsed.get("has_accurate_timestamps")
        ),
        "readout_ms": round(
            float(parsed.get("frame_readout_time") or 0.0), 3
        ),
    }


def _cam_summary(cam: Any) -> dict[str, Any]:
    """Comparable summary of a type-3 (camera data) export.

    The type-3 export is a per-frame list; DJI entries carry org_quat
    (original quaternion orientation), stab_quat (Gyroflow-computed
    stabilization), frame index and timestamp_ms. org_gyro/org_acc are
    zero-filled for DJI (quaternion-only source).
    """
    if not isinstance(cam, list):
        return {"count": 0, "frames": [], "org_quat": [], "stab_quat": []}
    return {
        "count": len(cam),
        "frames": [s.get("frame") for s in cam],
        "org_quat": [s.get("org_quat") for s in cam],
        "stab_quat": [s.get("stab_quat") for s in cam],
    }


def _quat_close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= QUAT_EPS for x, y in zip(a, b))


def gyroflow_dji_check(
    original: Path,
    final: Path,
    gyroflow: Path,
    scratch: Path,
) -> dict[str, Any]:
    """Consumer validation: Gyroflow must see identical DJI facts and
    identical per-frame motion data in original vs final."""
    try:
        p_src = _export(gyroflow, original, scratch / "dji_p_src.json", 2)
        p_out = _export(gyroflow, final, scratch / "dji_p_out.json", 2)
        c_src = _export(gyroflow, original, scratch / "dji_c_src.json", 3)
        c_out = _export(gyroflow, final, scratch / "dji_c_out.json", 3)
    except Exception as exc:
        return {"status": SKIP, "detail": f"not run: {exc}"}

    sf, of_ = parsed_facts(p_src), parsed_facts(p_out)
    ss, os_ = _cam_summary(c_src), _cam_summary(c_out)

    problems: list[str] = []
    if sf["detected_source"] != of_["detected_source"]:
        problems.append(
            f"detected_source {sf['detected_source']!r} vs "
            f"{of_['detected_source']!r}"
        )
    if sf["has_lens_profile"] != of_["has_lens_profile"]:
        problems.append("lens profile presence differs")
    if sf["has_accurate_timestamps"] != of_["has_accurate_timestamps"]:
        problems.append("timestamp accuracy differs")
    if abs(sf["readout_ms"] - of_["readout_ms"]) > READOUT_EPS:
        problems.append(
            f"readout {sf['readout_ms']} vs {of_['readout_ms']} ms"
        )
    if ss["count"] != os_["count"]:
        problems.append(
            f"motion samples {ss['count']} vs {os_['count']}"
        )
    elif ss["frames"] != os_["frames"]:
        problems.append("frame index sequence differs")
    else:
        bad = [
            i
            for i, (a, b) in enumerate(zip(ss["org_quat"], os_["org_quat"]))
            if not _quat_close(a, b)
        ]
        if bad:
            problems.append(
                f"{len(bad)} org_quat sample(s) differ (first={bad[0]})"
            )
        bad_stab = [
            i
            for i, (a, b) in enumerate(
                zip(ss["stab_quat"], os_["stab_quat"])
            )
            if not _quat_close(a, b)
        ]
        if bad_stab:
            problems.append(
                f"{len(bad_stab)} stab_quat sample(s) differ "
                f"(first={bad_stab[0]})"
            )

    return {
        "status": PASS if not problems else FAIL,
        "detail": "; ".join(problems)
        if problems
        else (
            f"{sf['detected_source']}, {ss['count']} motion samples, "
            "quaternions + lens profile identical"
        ),
        "original": {"parsed": sf, "camera": ss},
        "final": {"parsed": of_, "camera": os_},
    }


def run_dji_check(
    *,
    original: Path,
    final: Path,
    gpac: GpacContainerBackend,
    ffprobe: Path,
    gyroflow: Path | None,
    scratch: Path,
    vfr: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """ORIGINAL vs FINAL structural + payload + consumer check for DJI.

    Critical items: data-track payloads (djmd/dbgi/tmcd) byte-identical,
    track inventory, audio streams, video frame count/fps (CFR only).
    Movie timescale is informational (DJI quaternions align by frame
    index; GPAC -new sets it to the first track's media timescale).
    """
    scratch.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, str]] = []

    def note(name: str, status: str, detail: str = "") -> None:
        items.append({"item": name, "status": status, "detail": detail})

    def eq(
        name: str,
        a: Any,
        b: Any,
        ok_detail: str = "",
    ) -> None:
        if a == b:
            note(name, PRESERVED, ok_detail or f"{a!r}")
        else:
            note(name, MODIFIED, f"original={a!r} final={b!r}")

    src_ts, src_tracks = track_manifest(gpac, original)
    out_ts, out_tracks = track_manifest(gpac, final)

    s_specs = dji_track_specs(src_tracks)
    o_specs = dji_track_specs(out_tracks)
    if not s_specs["data_ids"]:
        raise RuntimeError(f"{original.name}: no DJI data track found")
    if s_specs["video_id"] is None:
        raise RuntimeError(f"{original.name}: no video track found")

    # --- inventory ---
    s_inv = sorted(
        (t["handler"], t["entry"]) for t in src_tracks
        if t["handler"] != "vide"
    )
    o_inv = sorted(
        (t["handler"], t["entry"]) for t in out_tracks
        if t["handler"] != "vide"
    )
    eq("dji.track_inventory", s_inv, o_inv)

    # video: re-encoded HEVC expected
    o_video = next(
        (t for t in out_tracks if t["handler"] == "vide"), None
    )
    if o_video is None:
        note("dji.video.track", MISSING, "no video track in final")
    elif o_video["entry"] != "hvc1":
        note(
            "dji.video.track", MODIFIED,
            f"final video entry {o_video['entry']!r} (hvc1 expected)",
        )
    else:
        note("dji.video.track", PRESERVED, "vide:hvc1")

    # --- data-track payloads (byte-identical) ---
    for track_id, entry in s_specs["data_ids"]:
        s_raw = scratch / f"src_{entry}.bin"
        o_raw = scratch / f"out_{entry}.bin"
        try:
            gpac.raw_track(original, track_id, s_raw)
            s_hash, s_size = _sha256_file(s_raw), s_raw.stat().st_size
        except Exception as exc:
            note(f"dji.{entry}.sha256", UNKNOWN, f"src dump failed: {exc}")
            continue
        o_match = next(
            (t for t in out_tracks if t["entry"] == entry), None
        )
        if o_match is None:
            note(f"dji.{entry}.sha256", MISSING, "track not in final")
            continue
        try:
            gpac.raw_track(final, o_match["id"], o_raw)
            o_hash, o_size = _sha256_file(o_raw), o_raw.stat().st_size
        except Exception as exc:
            note(f"dji.{entry}.sha256", MODIFIED, f"final dump failed: {exc}")
            continue
        eq(f"dji.{entry}.payload_sha256", s_hash, o_hash)
        eq(f"dji.{entry}.payload_size", s_size, o_size)
        s_smp = next(
            (t["sample_count"] for t in src_tracks if t["id"] == track_id),
            0,
        )
        o_smp = o_match["sample_count"]
        eq(f"dji.{entry}.sample_count", s_smp, o_smp)
        for p in (s_raw, o_raw):
            try:
                p.unlink()
            except OSError:
                pass

    # --- audio ---
    def audio_facts(path: Path) -> list[tuple]:
        cmd = [
            str(ffprobe), "-v", "error", "-show_streams", "-of", "json",
            str(path),
        ]
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", check=False,
        )
        if proc.returncode != 0:
            return []
        streams = json.loads(proc.stdout).get("streams", [])
        return sorted(
            (
                st.get("codec_name"), st.get("sample_rate"),
                st.get("channels"),
            )
            for st in streams
            if st.get("codec_type") == "audio"
        )

    eq(
        "dji.audio.streams", audio_facts(original), audio_facts(final),
    )

    # --- video frames / fps (CFR only) ---
    if not vfr:
        from core.probe import count_frames

        s_frames, s_fps = count_frames(ffprobe, original)
        o_frames, o_fps = count_frames(ffprobe, final)
        eq("dji.video.frame_count", s_frames, o_frames)
        eq("dji.video.frame_rate", s_fps, o_fps)
    else:
        note("dji.video.frame_count", UNKNOWN, "VFR source: gate skipped")

    # --- movie timescale (informational; quaternions are frame-indexed) ---
    eq("dji.movie_timescale", src_ts, out_ts)

    # --- Gyroflow consumer check ---
    gyro: dict[str, Any] | None = None
    if gyroflow is not None:
        log("DJI Gyroflow consumer validation...")
        gyro = gyroflow_dji_check(
            original, final, gyroflow, scratch / "gyroflow"
        )
        log(f"dji gyroflow: {gyro['status']} ({gyro['detail']})")
    else:
        gyro = {"status": SKIP, "detail": "Gyroflow not installed"}

    summary = {
        PRESERVED: sum(1 for i in items if i["status"] == PRESERVED),
        MODIFIED: sum(1 for i in items if i["status"] == MODIFIED),
        MISSING: sum(1 for i in items if i["status"] == MISSING),
        UNKNOWN: sum(1 for i in items if i["status"] == UNKNOWN),
    }
    critical_prefixes = ("dji.video", "dji.track", "dji.djmd",
                         "dji.dbgi", "dji.tmcd", "dji.audio")
    critical_missing = [
        i for i in items
        if i["status"] == MISSING
        and i["item"].startswith(critical_prefixes)
    ]
    critical_modified = [
        i for i in items
        if i["status"] == MODIFIED
        and i["item"].startswith(critical_prefixes)
    ]
    structural_success = (
        not critical_missing
        and not critical_modified
        and (gyro is None or gyro["status"] != FAIL)
    )
    return {
        "original": str(original),
        "final": str(final),
        "summary": summary,
        "structural_success": structural_success,
        "critical_missing": critical_missing,
        "critical_modified": critical_modified,
        "items": items,
        "gyroflow": gyro,
    }
