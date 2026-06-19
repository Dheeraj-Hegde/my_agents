"""Trajectory recorder.

`start_recording(session, task)` returns a `Recorder` whose `event(...)`
and `frame(...)` methods append to a per-task directory:

    state/trajectories/<session>/<task>/
        events.jsonl    one JSON line per event
        frames/         numbered PNGs (screenshots + SoM overlays)
        meta.json       written by stop_recording()

Recorder is intentionally process-local and synchronous — no async, no
queues. The Computer-Use cascade calls `event()` from each layer; the
runner calls `stop_recording()` once the task is finished.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRAJ_ROOT = ROOT / "state" / "trajectories"


@dataclass
class Recorder:
    session: str
    task: str
    dir: Path
    started_at: float = field(default_factory=time.time)
    frame_index: int = 0
    event_count: int = 0
    _events_fh: Any = None

    def __post_init__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "frames").mkdir(exist_ok=True)
        self._events_fh = open(self.dir / "events.jsonl", "a", encoding="utf-8")

    # ── public API ─────────────────────────────────────────────────────

    def event(self, kind: str, layer: str | None = None, **payload) -> None:
        """Append one event line. `kind` is free-form ("layer_try",
        "action", "frame", "layer_result", "task_done", ...). `layer`
        is the cascade layer attribution, if applicable."""
        rec = {
            "t": round(time.time() - self.started_at, 3),
            "kind": kind,
            "layer": layer,
            **payload,
        }
        self._events_fh.write(json.dumps(rec, default=str) + "\n")
        self._events_fh.flush()
        self.event_count += 1

    def frame(self, png_bytes: bytes, label: str = "") -> str:
        """Write a screenshot/SoM frame; return its file path."""
        self.frame_index += 1
        name = f"frame_{self.frame_index:04d}.png"
        path = self.dir / "frames" / name
        path.write_bytes(png_bytes)
        self.event("frame", layer=None, file=str(path), label=label)
        return str(path)

    def stop(self, *, success: bool, summary: dict | None = None) -> Path:
        """Finalise the recording. Writes meta.json and closes the
        events file. Returns the trajectory directory path."""
        meta = {
            "session": self.session,
            "task": self.task,
            "started_at": self.started_at,
            "ended_at": time.time(),
            "duration_s": round(time.time() - self.started_at, 3),
            "event_count": self.event_count,
            "frame_count": self.frame_index,
            "success": success,
            "summary": summary or {},
        }
        (self.dir / "meta.json").write_text(
            json.dumps(meta, indent=2, default=str)
        )
        try:
            self._events_fh.close()
        except Exception:
            pass
        return self.dir


def start_recording(session: str, task: str) -> Recorder:
    """Open a new trajectory directory and return a `Recorder`.

    Calling this twice for the same (session, task) appends to the same
    events.jsonl — that is intentional: re-running a task in the same
    session keeps the history. Frame numbering continues from disk so
    nothing is overwritten.
    """
    tdir = TRAJ_ROOT / session / task
    rec = Recorder(session=session, task=task, dir=tdir)
    # Honour existing frame count so re-runs do not stomp earlier frames.
    existing = sorted((tdir / "frames").glob("frame_*.png"))
    if existing:
        last = existing[-1].stem.split("_")[-1]
        try:
            rec.frame_index = int(last)
        except ValueError:
            pass
    rec.event("recording_started", session=session, task=task)
    return rec


def stop_recording(rec: Recorder, *, success: bool, summary: dict | None = None) -> Path:
    rec.event("recording_stopped", success=success)
    return rec.stop(success=success, summary=summary)
