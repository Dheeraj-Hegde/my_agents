"""Layer 2b — Windows UI Automation (accessibility tree).

UIA is the right tool whenever the target is a native Win32 / WPF /
WinUI / WinForms app: it exposes a labelled control tree, and clicking
a control by name is more robust than blind keystrokes (Layer 2a) and
infinitely cheaper than vision (Layer 3).

cua does not expose an a11y tree, so this layer uses the
`uiautomation` Python package directly (Windows-only, ctypes wrapper
around UIAutomationCore.dll). The blocking calls run inside
`asyncio.to_thread` so the cascade event loop stays responsive. On
non-Windows hosts the layer marks itself non-applicable and the
cascade escalates.

Recipe schema:
    {"step": "find_window", "title_contains": "Notepad"}
    {"step": "click",       "control": {"Name": "OK"}}
    {"step": "type",        "text": "hello"}
    {"step": "read",        "control": {"AutomationId": "ResultBox"},
                              "into": "value"}

This layer's primary job in the bundled tasks is the *fallback* for
the calculator path — if hotkeys fail to land on Calculator, UIA can
still drive it by control name.
"""

from __future__ import annotations

import asyncio
import platform

from ..recorder import Recorder
from ..schemas import LayerOutcome
from ..task_spec import TaskSpec


def _import_uia():
    try:
        import uiautomation as auto  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "uiautomation is required for Layer 2b. "
            "Install with `uv add uiautomation` on Windows."
        ) from exc
    return auto


def _run_recipe_sync(recipe: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Synchronous body of the UIA recipe; runs inside asyncio.to_thread."""
    import time
    auto = _import_uia()

    actions: list[dict] = []
    captured: dict[str, str] = {}
    window = None

    for step in recipe:
        op = step.get("step")
        if op == "find_window":
            title = step.get("title_contains", "")
            window = auto.WindowControl(searchDepth=1, SubName=title)
            if not window.Exists(maxSearchSeconds=5):
                raise RuntimeError(
                    f"no top-level window matches {title!r}")
            window.SetActive()
        elif op == "click":
            if window is None:
                raise RuntimeError("click before find_window")
            ctrl = window.Control(**step.get("control", {}))
            if not ctrl.Exists(maxSearchSeconds=2):
                raise RuntimeError(
                    f"control not found: {step.get('control')}")
            ctrl.Click()
        elif op == "type":
            auto.SendKeys(step.get("text", ""))
        elif op == "read":
            if window is None:
                raise RuntimeError("read before find_window")
            ctrl = window.Control(**step.get("control", {}))
            if not ctrl.Exists(maxSearchSeconds=2):
                raise RuntimeError(
                    f"control not found: {step.get('control')}")
            val = ctrl.Name or getattr(ctrl, "Value", "") or ""
            captured[step.get("into", "value")] = val
        elif op == "sleep":
            time.sleep(float(step.get("seconds", 0.2)))
        else:
            raise ValueError(f"unknown UIA step: {op!r}")

        actions.append({"layer": "uia", **step})

    return actions, captured


class Layer2bUia:
    name = "uia"

    async def try_(self, task: TaskSpec, host, recorder: Recorder) -> LayerOutcome:
        if platform.system() != "Windows":
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason=f"not Windows ({platform.system()})")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="UIA requires Windows")

        if not task.uia_recipe:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="no uia_recipe on task")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="task has no uia_recipe")

        try:
            _import_uia()
        except RuntimeError as exc:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason=str(exc))
            return LayerOutcome(layer=self.name, applicable=False,
                                detail=str(exc))

        recorder.event("layer_try", layer=self.name, applicable=True,
                       steps=len(task.uia_recipe))

        try:
            actions, captured = await asyncio.to_thread(
                _run_recipe_sync, task.uia_recipe,
            )
            for a in actions:
                # `a` already carries its own "layer" key from
                # _run_recipe_sync; splatting it directly lets the
                # recorder consume the kwarg without a duplicate.
                recorder.event("action", **a)
            recorder.event("layer_result", layer=self.name,
                           success=True, captured=captured)
            return LayerOutcome(
                layer=self.name, success=True, actions=actions,
                detail=f"UIA recipe ran; captured={captured!r}",
            )
        except Exception as exc:
            recorder.event("layer_result", layer=self.name,
                           success=False, error=str(exc))
            return LayerOutcome(
                layer=self.name, success=False, error=str(exc),
                detail=f"UIA recipe failed: {exc}",
            )
