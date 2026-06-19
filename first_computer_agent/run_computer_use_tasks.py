"""Computer-Use evidence runner.

Runs the three bundled tasks back-to-back, each wrapped in its own
trajectory recording. Prints a one-line summary per task and the
absolute paths of the trajectory directories at the end — those
directories are the deliverable for the assignment.

Usage:
    uv run python run_computer_use_tasks.py
    uv run python run_computer_use_tasks.py --only calculator
    uv run python run_computer_use_tasks.py --session demo01

The runner does not touch the gateway directly for tasks 1 and 2; it
only attempts a `/v1/vision` POST for task 3, which is the constraint
"at least one task uses vision". If your gateway is offline, task 3 is
still recorded — the trajectory captures the screenshot, the VLM
request that failed, and the cascade decision to give up cleanly.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from computer_use import ComputerUseSkill
from computer_use.recorder import start_recording, stop_recording
from computer_use import tasks as _tasks


# Map runner CLI keys → task `build()` callables. Tasks whose module
# is empty / failed to import are silently omitted so the runner stays
# usable while one task is being edited.
TASKS: dict = {}
for _key, _modname in (
    ("calculator", "calculator"),
    ("vscode",     "vscode_editor"),
    ("canvas",     "canvas_sketch"),
    ("vscode_create_file", "vscode_create_file"),
    ("browser_game", "browser_game"),
):
    _mod = getattr(_tasks, _modname, None)
    if _mod is None:
        continue
    _build = getattr(_mod, "build", None)
    if _build is None:
        continue
    TASKS[_key] = _build


def _run_one(skill: ComputerUseSkill, task_key: str) -> dict:
    """Run one task; return a summary dict for the final report."""
    task = TASKS[task_key]()
    print(f"\n[task] {task.name}  — {task.goal}")

    # The skill's `run` will start_recording/stop_recording on its own
    # when we don't pass a recorder. We pre-create one so we can print
    # the directory before the cascade starts (helpful when a task is
    # going to hang the screen for a few seconds).
    rec = start_recording(skill.session, task.name)
    print(f"        trajectory: {rec.dir}")
    started = time.time()
    try:
        out = skill.run(task, recorder=rec)
        success = out.success
    except Exception as exc:  # pragma: no cover - top-level safety net
        rec.event("task_crash", error=str(exc))
        out = None
        success = False
        print(f"        CRASH: {exc!r}")
    finally:
        stop_recording(rec, success=success, summary={
            "path": getattr(out, "path", None),
            "layers_tried": getattr(out, "layers_tried", []),
            "vision_calls": getattr(out, "vision_calls", 0),
            "final_value": getattr(out, "final_value", None),
            # New: validator verdict. None = no validator was attached
            # (task didn't define one); True/False = validator ran and
            # passed/failed. The empty-string default for
            # validation_detail mirrors ComputerUseOutput's default.
            "validated": getattr(out, "validated", None),
            "validation_detail": getattr(out, "validation_detail", ""),
            "detail": getattr(out, "detail", ""),
        })
    elapsed = round(time.time() - started, 2)

    if out is not None:
        print(f"        path={out.path}  layers_tried={out.layers_tried}  "
              f"vision_calls={out.vision_calls}  "
              f"final_value={out.final_value!r}  ({elapsed}s)")
    return {
        "task": task.name,
        "success": success,
        "path": getattr(out, "path", None),
        "layers_tried": getattr(out, "layers_tried", []),
        "vision_calls": getattr(out, "vision_calls", 0),
        "final_value": getattr(out, "final_value", None),
        "trajectory_dir": str(rec.dir),
        "elapsed_s": elapsed,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", default=time.strftime("cua_%Y%m%d_%H%M%S"))
    p.add_argument("--only", choices=list(TASKS), action="append",
                   help="Restrict to these tasks (may be repeated). "
                        "Default: all three.")
    args = p.parse_args(argv)

    selected = args.only or list(TASKS)
    skill = ComputerUseSkill(session=args.session)
    print(f"\n=== Computer-Use evidence run — session {args.session} ===")
    print(f"Tasks: {selected}\n")

    summaries: list[dict] = []
    for key in selected:
        summaries.append(_run_one(skill, key))

    # Constraint check: ≥1 vision call, ≥1 electron, ≥1 zero-vision.
    any_vision    = any(s["vision_calls"] > 0          for s in summaries)
    any_electron  = any(s["path"] == "electron"        for s in summaries)
    any_zero_vis  = any(s["vision_calls"] == 0
                        and s["path"] != "vision"
                        for s in summaries)
    print("\n=== Summary ===")
    print(json.dumps(summaries, indent=2, default=str))
    print("\nConstraint check:")
    print(f"  ≥1 vision-using task     : {any_vision}")
    print(f"  ≥1 Electron-page task    : {any_electron}")
    print(f"  ≥1 zero-vision task      : {any_zero_vis}")
    print("\nTrajectory directories (submit these):")
    for s in summaries:
        print(f"  {s['task']}: {s['trajectory_dir']}")

    return 0 if all(s["success"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
