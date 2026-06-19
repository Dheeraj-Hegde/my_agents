"""Layer 1 — system / app API.

Cheapest layer. The task's `api_handler` is an async callable that
receives the shared `cua.Localhost` so it can use `host.shell.run`,
`host.clipboard.get/set`, etc. If the task spec has no `api_handler`
the layer marks itself non-applicable and the cascade escalates.

Handler signatures supported (auto-detected via :mod:`inspect`):

    async def handler(host) -> (bool, value, detail)
    async def handler(host, recorder) -> (bool, value, detail)

The 2-arg form lets a handler that runs its own per-turn loop
(e.g. a headless Playwright vision player) write screenshots and
events straight into the shared trajectory recorder so the
dashboard sees them just like a Layer-3 run.
"""

from __future__ import annotations

import inspect

from ..schemas import LayerOutcome
from ..recorder import Recorder
from ..task_spec import TaskSpec


class Layer1Api:
    name = "api"

    async def try_(self, task: TaskSpec, host, recorder: Recorder) -> LayerOutcome:
        if task.api_handler is None:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="no api_handler on task")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="task has no api_handler")

        recorder.event("layer_try", layer=self.name, applicable=True)
        try:
            # Forward the recorder when the handler asks for it.
            # Backward-compatible: legacy single-arg handlers keep
            # working unchanged.
            try:
                sig = inspect.signature(task.api_handler)
                want_recorder = len(sig.parameters) >= 2
            except (TypeError, ValueError):
                want_recorder = False
            if want_recorder:
                success, value, detail = await task.api_handler(host, recorder)
            else:
                success, value, detail = await task.api_handler(host)
        except NotImplementedError as exc:
            recorder.event("layer_result", layer=self.name,
                           applicable=False, reason=str(exc))
            return LayerOutcome(layer=self.name, applicable=False,
                                detail=f"NotImplemented: {exc}")
        except Exception as exc:
            recorder.event("layer_result", layer=self.name,
                           success=False, error=str(exc))
            return LayerOutcome(layer=self.name, success=False,
                                error=str(exc), detail="api_handler raised")

        recorder.event("layer_result", layer=self.name,
                       success=success, detail=detail, value=value)
        return LayerOutcome(layer=self.name, success=success,
                            detail=detail,
                            actions=[{"layer": "api",
                                      "action": "finish",
                                      "value": value}])
