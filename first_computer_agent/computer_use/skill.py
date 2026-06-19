"""Computer-Use skill orchestrator.

Owns the five-layer cascade. The dispatcher in `skills.py` constructs
this with `(session, enabled_layers)` and calls `run(task)` per task.
Layers consume the shared `cua.Localhost` handle the orchestrator
opens at the top of each run — that handle gives the layers a
uniform OS surface (`host.mouse`, `host.keyboard`, `host.screen`,
`host.clipboard`, `host.shell`, `host.window`) instead of each layer
importing its own pyautogui / mss / subprocess stack.

The skill never goes through the gateway's chat channel directly;
Layer 3 is the only layer that talks to the gateway, via its own
typed `/v1/vision` POST.

Cascade rule (identical to Session-9 Browser):

  async with cua.Localhost.connect() as host:
      for each layer in [api, hotkeys, uia, electron, vision]:
          outcome = await layer.try_(task, host, recorder)
          if not outcome.applicable:    continue
          record outcome
          if outcome.success:           short-circuit, return path=layer
      return path=last_attempted (failed) or "vision" if every layer skipped

`ComputerUseSkill.run()` is a sync facade so the existing dispatcher
in `skills.py` (which already calls us via `asyncio.to_thread`) needs
no changes. `run_async()` is exposed for callers already inside an
event loop.
"""

from __future__ import annotations

import asyncio
import time

from .layers import (
    Layer1Api,
    Layer2aHotkeys,
    Layer2bUia,
    Layer2cElectron,
    Layer3Vision,
)
from .recorder import Recorder, start_recording, stop_recording
from .schemas import ComputerUseLayer, ComputerUseOutput, LayerOutcome
from .task_spec import TaskSpec


# Cascade order. Single source of truth.
CASCADE_ORDER: list[ComputerUseLayer] = [
    "api", "hotkeys", "uia", "electron", "vision",
]


class ComputerUseSkill:
    def __init__(self, *, session: str = "default",
                 trajectories_root: str | None = None,
                 enabled_layers: list[ComputerUseLayer] | None = None):
        self.session = session
        self.trajectories_root = trajectories_root  # informational only;
                                                    # recorder.py owns layout
        self.enabled_layers = enabled_layers or list(CASCADE_ORDER)

        # Instantiate once per skill so per-layer state is bound at
        # construction. All layer.try_() methods are async and accept
        # the shared `host`.
        self._layers = {
            "api":      Layer1Api(),
            "hotkeys":  Layer2aHotkeys(),
            "uia":      Layer2bUia(),
            "electron": Layer2cElectron(),
            "vision":   Layer3Vision(agent="computer_use", session=session),
        }

    # ── public API ─────────────────────────────────────────────────────

    def run(self, task: TaskSpec, *, recorder: Recorder | None = None
            ) -> ComputerUseOutput:
        """Sync entry point. Opens a fresh event loop and runs
        `run_async`. Safe to call from threads (which is what the
        skills.py dispatcher does via `asyncio.to_thread`)."""
        return asyncio.run(self.run_async(task, recorder=recorder))

    async def run_async(self, task: TaskSpec, *,
                        recorder: Recorder | None = None
                        ) -> ComputerUseOutput:
        """Async entry point. Opens one cua.Localhost connection for
        the full cascade and tears it down on the way out."""
        import cua  # local import: cua_auto sub-process is heavy

        own_recorder = recorder is None
        if recorder is None:
            recorder = start_recording(self.session, task.name)
        recorder.event("task_start", task=task.name, goal=task.goal)
        started = time.time()

        chosen_path: ComputerUseLayer | None = None
        layers_tried: list[ComputerUseLayer] = []
        all_actions: list[dict] = []
        final_value: str | None = None
        last_outcome: LayerOutcome | None = None
        vision_calls = 0
        success = False
        detail = ""
        validated: bool | None = None
        validation_detail: str = ""

        try:
            async with cua.Localhost.connect() as host:
                # Pin the screen size at the top of the run for replay /
                # debugging — coordinate-bearing actions are only
                # interpretable next to the dimensions they were
                # captured against.
                try:
                    w, h = await host.screen.size()
                    recorder.event("host_ready",
                                   screen_width=w, screen_height=h,
                                   env=await host.get_environment())
                except Exception:
                    pass

                for layer_name in CASCADE_ORDER:
                    if layer_name not in self.enabled_layers:
                        continue
                    layer = self._layers[layer_name]
                    outcome = await layer.try_(task, host, recorder)

                    if not outcome.applicable:
                        # Skipped — cheap, not counted as an attempt.
                        continue

                    layers_tried.append(layer_name)
                    last_outcome = outcome
                    all_actions.extend(outcome.actions)

                    if layer_name == "vision":
                        # Each VLM round-trip counts; Layer 3's actions
                        # carry a 'turn' key for each call.
                        vision_calls += sum(
                            1 for a in outcome.actions if "turn" in a
                        )

                    if outcome.success:
                        chosen_path = layer_name
                        success = True
                        # Pull a captured value out of the outcome.
                        for a in outcome.actions:
                            if a.get("action") == "capture":
                                final_value = str(a.get("value"))
                            if a.get("action") == "finish":
                                final_value = str(a.get("value"))
                            if a.get("action") == "evaluate":
                                # Prefer the full `value` (used by task
                                # validators that need to parse it),
                                # fall back to the truncated excerpt
                                # for layers that still only carry it.
                                final_value = (a.get("value")
                                               or a.get("value_excerpt")
                                               or final_value)
                        detail = outcome.detail
                        break
                    else:
                        detail = outcome.detail
                        recorder.event(
                            "cascade_escalate", from_layer=layer_name,
                            reason=outcome.error or "layer failed",
                        )

                # ── Validation step ────────────────────────────────────
                # Runs INSIDE the host context so the validator can take
                # fresh screenshots / read the clipboard / etc. against
                # the same `cua.Localhost` the cascade used. The
                # provisional `success` is what the cascade reported;
                # the validator's job is to re-confirm with domain logic
                # (e.g. "Paint canvas actually has ink") and downgrade
                # `success` to False if the cascade was fooled.
                if success and task.validator is not None:
                    provisional = ComputerUseOutput(
                        task=task.name, goal=task.goal,
                        path=chosen_path or "vision",
                        success=success,
                        layers_tried=layers_tried,
                        vision_calls=vision_calls,
                        turns=len(all_actions),
                        actions=all_actions,
                        final_value=final_value,
                        trajectory_dir=str(recorder.dir),
                        detail=detail,
                    )
                    recorder.event("validate_start",
                                   layer=chosen_path,
                                   final_value=final_value)
                    try:
                        ok, vdetail = await task.validator(provisional, host)
                    except Exception as exc:
                        ok, vdetail = False, (
                            f"validator raised "
                            f"{type(exc).__name__}: {exc}"
                        )
                    validated = bool(ok)
                    validation_detail = vdetail
                    recorder.event("validate_result",
                                   validated=validated,
                                   detail=vdetail)
                    if not ok:
                        # Cascade thought it succeeded; validator
                        # disagrees. Final task verdict: failure. We
                        # leave the per-layer outcome untouched on the
                        # trajectory — replay can see "vision succeeded
                        # but validation rejected".
                        success = False
                        detail = f"{detail} | validation_failed: {vdetail}"
        finally:
            if chosen_path is None:
                chosen_path = layers_tried[-1] if layers_tried else "vision"
                success = success and chosen_path is not None

        out = ComputerUseOutput(
            task=task.name,
            goal=task.goal,
            path=chosen_path,
            success=success,
            layers_tried=layers_tried,
            vision_calls=vision_calls,
            turns=len(all_actions),
            actions=all_actions,
            final_value=final_value,
            trajectory_dir=str(recorder.dir),
            detail=detail,
            validated=validated,
            validation_detail=validation_detail,
        )

        recorder.event("task_end", success=success, path=chosen_path,
                       vision_calls=vision_calls,
                       elapsed_s=round(time.time() - started, 3),
                       final_value=final_value,
                       validated=validated)

        if own_recorder:
            stop_recording(recorder, success=success, summary={
                "path": chosen_path,
                "layers_tried": layers_tried,
                "vision_calls": vision_calls,
                "final_value": final_value,
                "detail": detail,
                "validated": validated,
                "validation_detail": validation_detail,
            })
        return out


__all__ = ["ComputerUseSkill", "CASCADE_ORDER"]
