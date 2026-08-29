"""Progress dashboard: shared status registry + JSON writer.

The worker side (main batch) updates a per-file status registry; a
background thread snapshots it to a JSON file every 1.5s. A separate
console window (`python -m core.dashboard_ui <json>`) renders it in
nvidia-smi style. --headless skips the window but still writes the
JSON file (cheap, useful for unattended monitoring).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class DashboardStatus:
    def __init__(self, json_path: Path | None) -> None:
        self.json_path = json_path
        self.rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._finished = False
        self._writer: threading.Thread | None = None
        if json_path is not None:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = threading.Thread(
                target=self._write_loop, daemon=True
            )
            self._writer.start()

    # -- worker side ---------------------------------------------------

    def start(self, src, preset: str, backend: str) -> None:
        with self._lock:
            self.rows[str(src)] = {
                "source": str(src),
                "name": Path(str(src)).name,
                "preset": preset,
                "backend": backend,
                "status": "queued",
                "detail": "",
                "frames": 0,
                "fps": 0.0,
                "started": time.time(),
            }

    def update(
        self,
        src,
        status: str,
        detail: str = "",
        frames: int = 0,
        fps: float = 0.0,
    ) -> None:
        with self._lock:
            row = self.rows.get(str(src))
            if row is None:
                return
            if status:
                row["status"] = status
            if detail:
                row["detail"] = detail
            if frames:
                row["frames"] = frames
            if fps:
                row["fps"] = fps

    def finish(self, src, result: str) -> None:
        with self._lock:
            row = self.rows.get(str(src))
            if row is None:
                return
            row["status"] = result
            row["detail"] = ""
            row["elapsed"] = round(time.time() - row["started"], 1)

    def set_meta(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self._meta = dict(meta)

    def mark_finished(self) -> None:
        with self._lock:
            self._finished = True
        if self._writer is not None:
            self._writer.join(timeout=5)
        self._snapshot()

    # -- writer side ---------------------------------------------------

    def _snapshot(self) -> None:
        if self.json_path is None:
            return
        with self._lock:
            data = {
                "meta": getattr(self, "_meta", {}),
                "finished": self._finished,
                "timestamp": time.strftime("%H:%M:%S"),
                "rows": list(self.rows.values()),
            }
        tmp = self.json_path.with_name(self.json_path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(self.json_path)
        except OSError:
            pass

    def _write_loop(self) -> None:
        while not self._finished:
            self._snapshot()
            time.sleep(1.5)
        self._snapshot()
