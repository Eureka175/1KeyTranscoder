# -*- coding: utf-8 -*-
"""DJI Action split-recording detection and lossless staging.

DJI Action cameras start a new file when a recording reaches roughly 16 GB
(``DJI_YYYYMMDDHHMMSS_NNNN_D.MP4``).  This module implements the detection
and lossless joining shared by the ``1kt.py --dji-join`` switch and the
standalone ``dji_join.py`` wrapper.

Design boundary: **no second MP4 writer and no second encoder pipeline**.
Concatenation uses MP4Box through the existing
:class:`preservation.gpac.GpacContainerBackend`; probing uses
:class:`core.probe.probe_streams`.  Callers feed the staged files back into
the normal 1KeyTranscoder pipeline.

Join strategy (important): a plain ``MP4Box -cat`` of camera files aligns
each next segment at the previous *movie* duration.  When the AAC track is a
few milliseconds longer than the video track, that leaves a one-frame gap at
every boundary and makes the joined video look VFR; the hardware integrity
gate then rejects hardware decode.  ``-unalign-cat`` concatenates every
track's samples continuously instead: measured on the user's Action 4
segments the joined video stays exactly CFR ``30000/1001`` with
``time_base=1/30000`` and frame count = sum of segments, while
``djmd``/``dbgi``/``tmcd`` are carried along by the same pass.

``-flat`` is used as well.  Without it MP4Box 26.02 writes a full-size
temporary file beside the destination: peak disk = 2x the joined size.
With ``-flat`` it writes the destination directly (1x), which is what makes
one 60 GB split group joinable on a volume that has ~118 GB free.

A single ``-cat`` pass also replaces the older per-track extraction +
video concat + audio concat + final mux sequence (peak ~3x the joined size).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.paths import VIDEO_EXTS
from core.probe import probe_streams
from preservation.dji import (
    camera_tool,
    is_action_series,
)
from preservation.gpac import GpacContainerBackend

#: DJI Action file names are ``DJI_YYYYMMDDHHMMSS_NNNN_D.MP4``.
ACTION_NAME_RE = re.compile(r"^DJI_(\d{14})_(\d{4})_D\.", re.IGNORECASE)
#: A non-final segment is expected to be near the camera's split threshold.
SPLIT_BYTES_MIN = 15 * 1024**3
#: File-name start times and container durations differ by at most a few
#: frames/seconds at a real split boundary.
BOUNDARY_TOLERANCE_SEC = 3.0
#: Suffix appended to the first segment's stem for the joined staging file.
JOINED_SUFFIX = "__joined"
#: Encode progress at which the next group's join is started in the
#: background.  Joining at ~85% keeps at most two joined groups on disk
#: (the one being encoded plus one being produced) instead of all of them,
#: while still hiding most of the join time behind the encode.
PREFETCH_FRACTION = 0.85
#: Free space kept unused on top of the joined file's on-disk size.
SPACE_RESERVE_BYTES = 8 * 1024**3
#: Projected encode output (intermediate + final) as a fraction of the
#: previous group's source size.  While the next group's joined file exists,
#: the previous group's encode is still writing its output; without this the
#: join could fill the volume and the encoder would fail after it.
OUTPUT_RESERVE_FRACTION = 0.30
#: How often a waiting join re-checks free space while the previous group is
#: still being encoded (and will free its joined source when it finishes).
SPACE_POLL_SEC = 10.0
GB = 1024**3

LogFn = Callable[[str], None]


class DjiJoinError(RuntimeError):
    """A split group was detected but could not be joined."""


@dataclass
class Segment:
    path: Path
    start: datetime
    index: int
    duration: float
    size: int
    video_signature: tuple[Any, ...]
    audio_signature: tuple[Any, ...]
    data_tags: tuple[str, ...]
    probe: dict[str, Any] = field(default_factory=dict)


@dataclass
class SegmentGroup:
    name: str
    segments: list[Segment]

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "segments": [str(s.path) for s in self.segments],
            "segment_count": len(self.segments),
            "total_duration_sec": round(self.duration, 3),
            "total_size_bytes": sum(s.size for s in self.segments),
            "reason": "contiguous DJI Action split recording",
        }


@dataclass
class JoinPlan:
    """Detection result: what to join and what to carry through unchanged."""

    input_root: Path
    sources: list[Path]
    groups: list[SegmentGroup]
    singles: list[Segment]

    @property
    def joined_count(self) -> int:
        return len(self.groups)

    @property
    def grouped_source_count(self) -> int:
        return sum(len(g.segments) for g in self.groups)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_root": str(self.input_root),
            "sources": len(self.sources),
            "groups": [g.as_dict() for g in self.groups],
            "ungrouped_dji_segments": [str(s.path) for s in self.singles],
        }


@dataclass
class StagingResult:
    """A staging tree ready to be handed to the normal 1kt pipeline."""

    root: Path
    sources: list[Path]
    joined: list[dict[str, Any]]


def parse_action_name(path: Path) -> tuple[datetime, int] | None:
    """Return (start time from the file name, sequence index) or None."""
    m = ACTION_NAME_RE.match(path.name)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return start, int(m.group(2))


def _stream_signature(streams: list[dict[str, Any]]) -> tuple:
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return (
        video.get("codec_name"),
        video.get("width"),
        video.get("height"),
        video.get("pix_fmt"),
        video.get("avg_frame_rate") or video.get("r_frame_rate"),
        audio.get("codec_name"),
        audio.get("channels"),
        audio.get("sample_rate"),
    )


def _audio_signature(streams: list[dict[str, Any]]) -> tuple:
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return (
        audio.get("codec_name"),
        audio.get("channels"),
        audio.get("sample_rate"),
    )


def data_tags(streams: list[dict[str, Any]]) -> tuple[str, ...]:
    """Sorted ``codec_tag_string`` values of all data streams."""
    return tuple(sorted(
        str(s.get("codec_tag_string") or "").strip()
        for s in streams if s.get("codec_type") == "data"
        and s.get("codec_tag_string")
    ))


def probe_segment(ffprobe: Path, path: Path) -> Segment | None:
    """Probe one file; return None when it is not a DJI Action segment."""
    parsed = parse_action_name(path)
    if parsed is None:
        return None
    start, index = parsed
    fmt, streams = probe_streams(ffprobe, path)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return None
    tags = data_tags(streams)
    # A DJI Action split must carry at least one of the action data tracks;
    # this prevents ordinary files that merely share the name pattern from
    # being grouped.
    if not ({"djmd", "dbgi"} & set(tags)):
        return None
    size = path.stat().st_size
    return Segment(
        path=path,
        start=start,
        index=index,
        duration=duration,
        size=size,
        video_signature=_stream_signature(streams),
        audio_signature=_audio_signature(streams),
        data_tags=tags,
        probe={"format": fmt, "streams": streams},
    )


def can_follow(prev: Segment, seg: Segment) -> bool:
    """True when ``seg`` is the immediate continuation of ``prev``."""
    boundary = (
        prev.start + timedelta(seconds=prev.duration) - seg.start
    ).total_seconds()
    return (
        seg.index == prev.index + 1
        and prev.size >= SPLIT_BYTES_MIN
        and abs(boundary) <= BOUNDARY_TOLERANCE_SEC
        and prev.video_signature == seg.video_signature
        and prev.audio_signature == seg.audio_signature
        and bool({"djmd", "dbgi"} & set(seg.data_tags))
    )


def group_segments(
    segments: list[Segment],
) -> tuple[list[SegmentGroup], list[Segment]]:
    """Group contiguous segments per directory; pure (no probing, no I/O).

    Returns ``(groups, ungrouped_segments)``.  A group only exists when at
    least two segments are chained, so a lone segment is never "joined" into
    itself.
    """
    by_parent: dict[Path, list[Segment]] = {}
    for seg in segments:
        by_parent.setdefault(seg.path.parent, []).append(seg)

    groups: list[SegmentGroup] = []
    singles: list[Segment] = []
    for parent in sorted(by_parent, key=lambda p: str(p).lower()):
        segments_for_parent = sorted(
            by_parent[parent], key=lambda s: (s.start, s.index)
        )
        chain: list[Segment] = []
        for seg in segments_for_parent:
            if chain and can_follow(chain[-1], seg):
                chain.append(seg)
                continue
            if len(chain) >= 2:
                groups.append(_make_group(chain))
            elif chain:
                singles.append(chain[0])
            chain = [seg]
        if len(chain) >= 2:
            groups.append(_make_group(chain))
        elif chain:
            singles.append(chain[0])
    groups.sort(key=lambda g: (str(g.segments[0].path.parent).lower(),
                               g.segments[0].path.name.lower()))
    singles.sort(key=lambda s: str(s.path).lower())
    return groups, singles


def _make_group(segments: list[Segment]) -> SegmentGroup:
    first = segments[0]
    last = segments[-1]
    name = f"{first.path.stem}__{last.path.stem.split('_')[-2]}_joined"
    return SegmentGroup(name=name, segments=segments)


def discover_groups(
    sources: list[Path],
    ffprobe: Path,
    log: LogFn | None = None,
) -> tuple[list[SegmentGroup], list[Segment]]:
    """Probe ``sources`` and return ``(groups, ungrouped DJI segments)``.

    A source that cannot be probed is treated as "not a DJI segment" and
    reported through ``log``: one unreadable/corrupt file must not abort the
    whole batch before the ordinary pipeline gets its own chance to report
    the same file.
    """
    segments: list[Segment] = []
    for src in sources:
        try:
            seg = probe_segment(ffprobe, src)
        except Exception as exc:                   # noqa: BLE001
            if log is not None:
                log(f"[DJI-JOIN] probe skipped | {src.name} | "
                    f"{type(exc).__name__}: {exc}")
            continue
        if seg is not None:
            segments.append(seg)
    return group_segments(segments)


def detect_plan(input_root: Path, sources: list[Path],
                ffprobe: Path, log: LogFn | None = None) -> JoinPlan:
    """Probe every source once and build the join plan."""
    groups, singles = discover_groups(sources, ffprobe, log=log)
    return JoinPlan(
        input_root=input_root,
        sources=list(sources),
        groups=groups,
        singles=singles,
    )


# ---------------------------------------------------------------------------
# Lossless join
# ---------------------------------------------------------------------------

def normalize_join(
    gpac: GpacContainerBackend,
    group: SegmentGroup,
    dest: Path,
) -> None:
    """Join the segments in one MP4Box pass (1x disk, no temp file).

    ``-cat`` carries every track, ``-unalign-cat`` keeps each track
    sample-continuous across split boundaries (exact CFR video; see the
    module docstring), and ``-flat`` makes MP4Box write the destination
    directly instead of a full-size temporary file.  This replaced the
    per-track extraction / concat / remux sequence, which needed roughly
    three times the joined size in scratch space.
    """
    if dest.exists():
        dest.unlink()
    gpac.concat(
        [seg.path for seg in group.segments],
        dest,
        unalign=True,
        flat=True,
    )
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise DjiJoinError(
            f"MP4Box concat produced no output for {group.name}"
        )



def join_group(
    gpac: GpacContainerBackend,
    group: SegmentGroup,
    dest: Path,
    ffprobe: Path,
) -> dict[str, Any]:
    """Losslessly join the group and validate the result with ffprobe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    normalize_join(gpac, group, dest)
    fmt, streams = probe_streams(ffprobe, dest)
    tags = data_tags(streams)
    joined_duration = float(fmt.get("duration") or 0.0)
    expected = group.duration
    tolerance = max(2.0, expected * 0.005)
    errors: list[str] = []
    if abs(joined_duration - expected) > tolerance:
        errors.append(
            f"duration mismatch: joined={joined_duration:.3f} "
            f"expected={expected:.3f}"
        )
    if not ({"djmd", "dbgi"} & set(tags)):
        errors.append(f"DJI data track missing after join: {tags}")
    # The camera model tag (``tool:``) lives on the movie, not on a track, so
    # MP4Box's track-wise rewrite drops it and the joined file no longer looks
    # like an Action-series source.  Capture the first segment's model here
    # so the pipeline can still apply the Action metadata policy by default.
    tool = camera_tool(gpac, group.segments[0].path)
    return {
        "group": group.as_dict(),
        "joined": str(dest),
        "joined_duration_sec": joined_duration,
        "joined_size_bytes": dest.stat().st_size,
        "camera_tool": tool,
        "keep_metadata": is_action_series(tool),
        "streams": [
            {
                "index": s.get("index"),
                "type": s.get("codec_type"),
                "codec": s.get("codec_name"),
                "tag": s.get("codec_tag_string"),
                "width": s.get("width"),
                "height": s.get("height"),
                "avg_frame_rate": s.get("avg_frame_rate"),
            }
            for s in streams
        ],
        "data_tags": list(tags),
        "errors": errors,
        "ok": not errors,
    }


# ---------------------------------------------------------------------------
# Staging tree for the normal pipeline
# ---------------------------------------------------------------------------

def staged_dest(segment_path: Path, input_root: Path,
                staging_root: Path) -> Path:
    """Destination for a joined group: same relative dir, ``__joined`` stem."""
    rel = segment_path.relative_to(input_root)
    return staging_root / rel.parent / (
        f"{segment_path.stem}{JOINED_SUFFIX}{segment_path.suffix}"
    )


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return
    except OSError as exc:
        raise DjiJoinError(
            f"cannot link {src} into staging tree without copying source: {exc}"
        ) from exc


def stage_plan(
    *,
    plan: JoinPlan,
    staging_root: Path,
    gpac: GpacContainerBackend,
    ffprobe: Path,
    log: LogFn | None = None,
) -> StagingResult:
    """Materialise the join plan under ``staging_root``.

    Joined groups become one file per recording; ungrouped sources are
    hardlinked (symlink fallback) so the source tree is never modified or
    copied.  Raises :class:`DjiJoinError` after keeping the partial staging
    tree for inspection.
    """
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)

    joined: list[dict[str, Any]] = []
    grouped = {s.path.resolve() for g in plan.groups for s in g.segments}
    for i, group in enumerate(plan.groups, 1):
        first = group.segments[0]
        dest = staged_dest(first.path, plan.input_root, staging_root)
        result = join_group(gpac, group, dest, ffprobe)
        joined.append(result)
        if log is not None:
            log(
                f"[DJI-JOIN] {i}/{len(plan.groups)} {group.name} | "
                f"{len(group.segments)} segments -> {dest.name} | "
                f"{result['joined_duration_sec']:.1f}s | "
                f"tool={result['camera_tool']!r} | "
                f"keep_metadata={result['keep_metadata']} | ok={result['ok']}"
            )
        if not result["ok"]:
            # Keep the partial tree: the caller logs its path so a failed
            # join can be inspected instead of silently retried from scratch.
            raise DjiJoinError(
                f"joined validation failed for {group.name}: "
                f"{'; '.join(result['errors'])}"
            )
    for src in plan.sources:
        if src.resolve() in grouped:
            continue
        rel = src.relative_to(plan.input_root)
        _link_or_copy(src, staging_root / rel)

    staged_sources = sorted(
        (p for p in staging_root.rglob("*")
         if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
        key=lambda p: str(p).lower(),
    )
    return StagingResult(
        root=staging_root,
        sources=staged_sources,
        joined=joined,
    )


def write_reports(
    *,
    report_dir: Path,
    plan: JoinPlan,
    joined: list[dict[str, Any]] | None = None,
    staging_root: Path | None = None,
) -> dict[str, Path]:
    """Write the plan/joined JSON reports; returns the written paths."""
    report_dir.mkdir(parents=True, exist_ok=True)
    plan_path = report_dir / "dji_join_plan.json"
    payload = plan.as_dict()
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    if staging_root is not None:
        payload["staging_root"] = str(staging_root)
    plan_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths: dict[str, Path] = {"plan": plan_path}
    if joined is not None:
        joined_path = report_dir / "dji_join_joined.json"
        joined_path.write_text(
            json.dumps(joined, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        paths["joined"] = joined_path
    return paths


# ---------------------------------------------------------------------------
# Progressive staging: one joined group per encode, one group ahead
# ---------------------------------------------------------------------------

class ProgressiveStager:
    """Stage split groups as the encode pipeline consumes them.

    The former staging path joined *every* group before encoding started.
    That needs the sum of all joined groups on disk at once (149 GB for the
    user's 2026-11-15 folder, on a volume with 118 GB free) and makes the
    first encode wait for the whole staging phase.  This class joins the
    first group, then:

    * the normal pipeline encodes that group;
    * when that encode starts, a background thread starts joining the next
      group (an ~85% progress callback is kept as an additional early
      trigger where the encoder reports it);
    * a finished group's joined source is deleted immediately after its
      encode is delivered (unless ``--dji-join-keep-temp``).

    So at most two joined groups exist at any moment and the join of group
    N+1 is hidden behind the encode of group N.

    Encoding order: ``sources`` lists the joined groups first, in plan
    order, then the ungrouped files.  ``ensure_ready`` additionally waits
    until every earlier group has finished encoding, so even ``--jobs N``
    cannot start group N+1 while group N is still encoding (which would
    put three joined groups on disk or interleave the join with the wrong
    file).
    """

    _SUCCESS_RESULTS = ("done", "skipped", "dry-run")

    def __init__(
        self,
        *,
        plan: JoinPlan,
        staging_root: Path,
        gpac: GpacContainerBackend,
        ffprobe: Path,
        log: LogFn | None = None,
        keep_temp: bool = False,
        prefetch_fraction: float = PREFETCH_FRACTION,
        reserve_bytes: int = SPACE_RESERVE_BYTES,
        space_poll_sec: float = SPACE_POLL_SEC,
        parallel_groups: bool = False,
    ) -> None:
        self.plan = plan
        self.root = staging_root
        self.report_dir = staging_root.parent / "logs" / "dji_join"
        self.gpac = gpac
        self.ffprobe = ffprobe
        self.log = log or (lambda _msg: None)
        self.keep_temp = keep_temp
        self.prefetch_fraction = prefetch_fraction
        self.reserve_bytes = int(reserve_bytes)
        self.space_poll_sec = space_poll_sec
        #: Remux mode may rebuild several already-joined groups concurrently;
        #: the encode pipeline keeps the strict one-at-a-time order.
        self.parallel_groups = bool(parallel_groups)

        self.groups = list(plan.groups)
        self.group_dest = [
            staged_dest(g.segments[0].path, plan.input_root, staging_root)
            for g in self.groups
        ]
        self._index = {p.resolve(): i for i, p in enumerate(self.group_dest)}
        #: Files handed to the normal pipeline, joined groups first.
        self.sources: list[Path] = []
        #: Joined source -> camera model (for the DJI Action policy).
        self.preserve_sources: dict[Path, str | None] = {}

        self._cond = threading.Condition()
        self._ready: set[int] = set()
        self._finished: set[int] = set()
        #: Groups whose encoder has produced its complete intermediate but
        #: whose verifier/finalisation is still running (async-integrity).
        #: They may gate the next group's *encode*, but their joined source
        #: is retained until note_finished().
        self._released: set[int] = set()
        self._results: dict[int, dict[str, Any]] = {}
        self._next_join = 0
        #: Exclusive upper bound of group indices the worker may join.
        self._allowed = 0
        self._join_error: BaseException | None = None
        self._closed = False
        self._materialised = False
        self._worker: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------

    @property
    def error(self) -> BaseException | None:
        return self._join_error

    @property
    def joined(self) -> list[dict[str, Any]]:
        return [self._results[i] for i in sorted(self._results)]

    def prepare(self) -> list[Path]:
        """Materialise the staging tree, join group 0, start the worker."""
        self.materialise()
        if self.groups:
            # The first group is needed before the first encode starts, so
            # this one is synchronous (and fails the run early/clearly).
            self._join_one(0)
            with self._cond:
                self._allowed = 1
                self._next_join = 1
                if len(self.groups) > 1:
                    self._worker = threading.Thread(
                        target=self._worker_loop,
                        name="dji-join-stager",
                        daemon=True,
                    )
                    self._worker.start()
        return self.sources

    def materialise(self) -> list[Path]:
        """Create the staging tree: hardlinks for ungrouped files only."""
        if self._materialised:
            return self.sources
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        grouped = {
            s.path.resolve() for g in self.groups for s in g.segments
        }
        for src in self.plan.sources:
            if src.resolve() in grouped:
                continue
            rel = src.relative_to(self.plan.input_root)
            _link_or_copy(src, self.root / rel)
        singles = sorted(
            (p for p in self.root.rglob("*")
             if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
            key=lambda p: str(p).lower(),
        )
        self.sources = [*self.group_dest, *singles]
        self._materialised = True
        return self.sources

    def close(self, *, keep: bool) -> None:
        """Stop the join worker and, when ``keep`` is false, remove staging."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=10.0 if not keep else 2.0)
        if keep:
            self.log(f"[DJI-JOIN] progressive staging kept | {self.root}")
            return
        if worker is not None and worker.is_alive():
            self.log(
                "[DJI-JOIN] join worker still running; staging kept | "
                f"{self.root}"
            )
            return
        cleanup_staging(self.root)

    # -- called by the normal pipeline ---------------------------------

    def ensure_ready(self, src: Path) -> None:
        """Block until ``src`` may be encoded.

        For a joined group this waits for (a) all earlier groups to finish
        encoding and (b) this group's join to complete.  Ungrouped files
        return immediately.
        """
        idx = self._index.get(src.resolve())
        if idx is None:
            return
        with self._cond:
            while True:
                if self._join_error is not None:
                    raise DjiJoinError(
                        f"join failed before {src.name}: {self._join_error}"
                    ) from self._join_error
                if self._closed:
                    raise DjiJoinError(
                        f"staging closed before {src.name} was encoded"
                    )
                earlier_done = self.parallel_groups or all(
                    (i in self._finished or i in self._released)
                    for i in range(idx)
                )
                if earlier_done and idx in self._ready:
                    return
                self._cond.wait(timeout=1.0)

    def note_progress(self, src: Path, frames: int, total_frames: int) -> None:
        """Encoder progress hook: arm the next group's join near the end."""
        idx = self._index.get(src.resolve())
        if idx is None or total_frames <= 0 or frames <= 0:
            return
        if frames / total_frames < self.prefetch_fraction:
            return
        self._allow(idx + 1, reason="progress")

    def note_encode_started(self, src: Path) -> None:
        """Arm the next group's join when this group's encode starts.

        The deployed rigaya builds only emit a few progress lines for short
        clips (measured: a 120-frame encode reported frames 30 and 44, then
        nothing until the final summary), so a percentage-only trigger could
        leave the join until after the encode.  Starting it at encode start
        keeps the same hard bound -- at most two joined groups on disk,
        because ``ensure_ready`` does not let the next group encode before
        this one finishes -- and hides the whole join behind the encode.
        ``_wait_for_space`` still delays the join when the volume cannot
        hold both joined files plus the projected encode output.
        """
        idx = self._index.get(src.resolve())
        if idx is None:
            return
        self._allow(idx + 1, reason="encode-start")

    def note_encode_complete(self, src: Path) -> None:
        """Async-integrity: encoder finished, verifier still running.

        This releases the *next group's encode gate* but deliberately does
        **not** delete this group's joined source: the verifier still needs
        the source container for its expected sample count, and a later
        finalisation may still need it.  ``note_finished`` performs the
        actual reclamation once verification + delivery are done.
        """
        idx = self._index.get(src.resolve())
        if idx is None:
            return
        with self._cond:
            if idx in self._finished or idx in self._released:
                return
            self._released.add(idx)
            if idx + 1 < len(self.groups):
                self._allowed = max(self._allowed, idx + 2)
            self.log(
                f"[DJI-JOIN] encode-complete release | group {idx + 1}/"
                f"{len(self.groups)} | next encode may start while "
                f"integrity/finalisation runs | source retained"
            )
            self._cond.notify_all()

    def note_finished(self, src: Path, result: str) -> None:
        """A source finished encoding; release its joined file and arm next."""
        idx = self._index.get(src.resolve())
        if idx is None:
            return
        with self._cond:
            self._finished.add(idx)
            if (
                result in self._SUCCESS_RESULTS
                and not self.keep_temp
            ):
                self._delete_joined(idx)
            if idx + 1 < len(self.groups):
                self._allowed = max(self._allowed, idx + 2)
            self._cond.notify_all()

    # -- worker --------------------------------------------------------

    def _allow(self, k: int, *, reason: str) -> None:
        if k >= len(self.groups):
            return
        with self._cond:
            if k < self._allowed:
                return
            self._allowed = k + 1
            self.log(
                f"[DJI-JOIN] prefetch armed | group {k + 1}/"
                f"{len(self.groups)} ({reason}) | free="
                f"{_fmt_gb(self._free_bytes())} GB"
            )
            self._cond.notify_all()

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while (
                    not self._closed
                    and self._join_error is None
                    and self._next_join >= self._allowed
                ):
                    self._cond.wait(timeout=1.0)
                if self._closed or self._join_error is not None:
                    return
                idx = self._next_join
            try:
                self._join_one(idx)
            except BaseException as exc:            # noqa: BLE001
                with self._cond:
                    self._join_error = exc
                    self._cond.notify_all()
                self.log(
                    f"[DJI-JOIN] FATAL: background join failed | group "
                    f"{idx + 1}/{len(self.groups)} | "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            with self._cond:
                self._next_join = idx + 1
                self._cond.notify_all()

    def _join_one(self, idx: int) -> None:
        group = self.groups[idx]
        dest = self.group_dest[idx]
        t0 = time.monotonic()
        self._reclaim_finished()
        self._wait_for_space(idx)
        self.log(
            f"[DJI-JOIN] join start | group {idx + 1}/{len(self.groups)} | "
            f"{group.name} | {len(group.segments)} segment(s) | "
            f"~{_fmt_gb(sum(s.size for s in group.segments))} GB | "
            f"free={_fmt_gb(self._free_bytes())} GB"
        )
        result = join_group(self.gpac, group, dest, self.ffprobe)
        if not result["ok"]:
            raise DjiJoinError(
                f"joined validation failed for {group.name}: "
                f"{'; '.join(result['errors'])}"
            )
        elapsed = time.monotonic() - t0
        with self._cond:
            self._results[idx] = result
            self._ready.add(idx)
            if result.get("keep_metadata"):
                self.preserve_sources[dest.resolve()] = result.get(
                    "camera_tool"
                )
            self._cond.notify_all()
        self.log(
            f"[DJI-JOIN] join done | group {idx + 1}/{len(self.groups)} | "
            f"{result['joined_duration_sec']:.1f}s | "
            f"{_fmt_gb(result['joined_size_bytes'])} GB | "
            f"{elapsed:.1f}s | tool={result['camera_tool']!r} | "
            f"keep_metadata={result['keep_metadata']} | "
            f"free={_fmt_gb(self._free_bytes())} GB"
        )

    # -- disk handling -------------------------------------------------

    def _free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.root).free
        except OSError:
            return 0

    def _delete_joined(self, idx: int) -> None:
        dest = self.group_dest[idx]
        try:
            if dest.is_file():
                size = dest.stat().st_size
                dest.unlink()
                self.log(
                    f"[DJI-JOIN] reclaimed | group {idx + 1}/"
                    f"{len(self.groups)} | {dest.name} | "
                    f"{_fmt_gb(size)} GB | "
                    f"free={_fmt_gb(self._free_bytes())} GB"
                )
        except OSError as exc:
            self.log(
                f"[DJI-JOIN] WARNING: cannot reclaim {dest.name} | {exc}"
            )

    def _reclaim_finished(self) -> None:
        if self.keep_temp:
            return
        with self._cond:
            finished = sorted(self._finished)
        for idx in finished:
            if self.group_dest[idx].is_file():
                self._delete_joined(idx)

    def _wait_for_space(self, idx: int) -> None:
        """Wait until the group's joined output fits on the volume.

        The next join is usually armed while the previous group is still
        encoding; if free space is not enough yet, this waits for that
        encode to finish and release its joined source.  It fails with the
        exact numbers instead of letting MP4Box hit a full disk midway.
        """
        need = sum(s.size for s in self.groups[idx].segments)
        reserve = self.reserve_bytes
        if idx > 0:
            with self._cond:
                prev_done = (idx - 1) in self._finished
            if not prev_done:
                # The previous group is still encoding into this volume.
                # Reserve a conservative share of its projected output so a
                # full disk cannot hit the encoder mid-write.
                prev_size = sum(
                    s.size for s in self.groups[idx - 1].segments
                )
                reserve += int(prev_size * OUTPUT_RESERVE_FRACTION)
        required = need + reserve
        warned = False
        while True:
            with self._cond:
                if self._join_error is not None:
                    raise DjiJoinError(
                        f"join failed before group {idx + 1}: "
                        f"{self._join_error}"
                    )
                if self._closed:
                    raise DjiJoinError("staging closed")
            free = self._free_bytes()
            if free >= required:
                return
            if self.keep_temp:
                raise DjiJoinError(
                    f"not enough free space for group {idx + 1}/"
                    f"{len(self.groups)} with --dji-join-keep-temp: "
                    f"need ~{_fmt_gb(required)} GB + reserve, "
                    f"free {_fmt_gb(free)} GB"
                )
            if not warned:
                warned = True
                self.log(
                    f"[DJI-JOIN] waiting for space | group {idx + 1}/"
                    f"{len(self.groups)} needs ~{_fmt_gb(required)} GB, "
                    f"free {_fmt_gb(free)} GB; waiting for the current "
                    f"encode to release its joined source"
                )
            with self._cond:
                if all(i in self._finished for i in range(idx)):
                    raise DjiJoinError(
                        f"not enough free space to join group {idx + 1}/"
                        f"{len(self.groups)} ({self.groups[idx].name}): "
                        f"need ~{_fmt_gb(required)} GB, free "
                        f"{_fmt_gb(free)} GB; no earlier group is left to "
                        f"release"
                    )
                self._cond.wait(timeout=self.space_poll_sec)

    # -- reporting -----------------------------------------------------

    def write_report(self) -> dict[str, Path]:
        """Write the joined-groups report for the groups joined so far."""
        return write_reports(
            report_dir=self.report_dir,
            plan=self.plan,
            joined=self.joined,
            staging_root=self.root,
        )


def _fmt_gb(value: float) -> str:
    return f"{value / GB:.1f}"


def cleanup_staging(staging_root: Path | None) -> None:
    """Remove a staging tree, ignoring absent/non-removable entries."""
    if staging_root is None:
        return
    shutil.rmtree(staging_root, ignore_errors=True)
