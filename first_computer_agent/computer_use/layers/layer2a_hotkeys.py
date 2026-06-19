"""Layer 2a — deterministic hotkeys (cua-driven).

Blind keyboard-driven control through `cua.Localhost`. No introspection
of the UI: we trust that the target app's keyboard contract is stable
(Win+R opens Run; Calculator accepts digit/operator keys; Ctrl+A
selects all; Ctrl+C copies). The recipe is a list of dicts evaluated
top-to-bottom.

Recipe action schema:
    {"action": "launch",   "argv": "calc.exe"}      # one shell line
    {"action": "sleep",    "seconds": 0.5}
    {"action": "hotkey",   "keys": ["ctrl", "a"]}    # → keyboard.keypress
    {"action": "type",     "text": "12*9="}
    {"action": "press",    "key": "enter"}           # → keyboard.keypress(["enter"])
    {"action": "focus",    "title_contains": "Calculator"}

`focus` does NOT bring a window to the foreground (cua's `host.window`
is read-only — `get_active_title()` only) — it asserts the active
title contains the expected substring, retrying for ~2 s, and fails
the layer if the title never matches. The cascade then escalates to
UIA, which knows how to set windows active.

This layer is the "zero vision" path. The Calculator task is the
canonical demonstration: launch → blind keystrokes → clipboard capture
→ done, no screenshots required.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys

from ..recorder import Recorder
from ..schemas import LayerOutcome
from ..task_spec import TaskSpec


def _spawn_detached(cmd: str) -> None:
    """Fire-and-forget launch of a GUI process. cua's `shell.run(
    background=True)` requires a PTY (LocalTransport raises on
    Windows), so we use subprocess directly. Important: on Windows we
    deliberately do NOT pass DETACHED_PROCESS or CREATE_NO_WINDOW,
    because those flags suppress the foreground-activation grant and
    the launched GUI app ends up behind the current window. A plain
    Popen via cmd.exe behaves like running `calc.exe` from Run, which
    grants foreground."""
    if sys.platform == "win32":
        subprocess.Popen(cmd, shell=True,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(shlex.split(cmd),
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)


async def _await_active_title(host, title_contains: str,
                              timeout_s: float = 2.5) -> str | None:
    """Poll `host.window.get_active_title` until it contains the substring
    (case-insensitive) or the timeout elapses. Returns the matched title
    or None."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    needle = title_contains.lower()
    last = ""
    while asyncio.get_event_loop().time() < deadline:
        try:
            last = await host.window.get_active_title()
        except Exception:
            last = ""
        if last and needle in last.lower():
            return last
        await asyncio.sleep(0.15)
    return None


class Layer2aHotkeys:
    name = "hotkeys"

    async def _maybe_frame(self, host, recorder: Recorder, label: str) -> None:
        """Best-effort screenshot for the dashboard.

        Layer 2a is deterministic and does not need screenshots to
        function, but the dashboard wants something visual to show.
        We capture after each non-sleep step and swallow any error so a
        flaky screen grab cannot break the recipe."""
        try:
            png = await host.screen.screenshot()
        except Exception as exc:
            recorder.event("frame_skipped", layer=self.name,
                           label=label, error=str(exc))
            return
        try:
            recorder.frame(png, label=label)
        except Exception as exc:
            recorder.event("frame_skipped", layer=self.name,
                           label=label, error=str(exc))

    async def try_(self, task: TaskSpec, host, recorder: Recorder) -> LayerOutcome:
        if not task.hotkey_recipe:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="no hotkey_recipe on task")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="task has no hotkey_recipe")

        recorder.event("layer_try", layer=self.name, applicable=True,
                       steps=len(task.hotkey_recipe))

        actions: list[dict] = []

        try:
            for step_idx, step in enumerate(task.hotkey_recipe, start=1):
                action = step.get("action")

                if action == "launch":
                    argv = step.get("argv")
                    if not argv:
                        raise ValueError("launch step missing argv")
                    if isinstance(argv, (list, tuple)):
                        cmd = " ".join(str(a) for a in argv)
                    else:
                        cmd = str(argv)
                    # cua's shell.run(background=True) needs a PTY,
                    # which LocalTransport on Windows does not provide.
                    # Use a detached subprocess so the GUI app launches
                    # without blocking the cascade.
                    await asyncio.to_thread(_spawn_detached, cmd)

                elif action == "sleep":
                    await asyncio.sleep(float(step.get("seconds", 0.2)))

                elif action == "hotkey":
                    keys = step.get("keys") or []
                    if not keys:
                        raise ValueError("hotkey step missing keys")
                    await host.keyboard.keypress(list(keys))

                elif action == "type":
                    await host.keyboard.type(str(step.get("text", "")))

                elif action == "press":
                    key = step.get("key", "")
                    if not key:
                        raise ValueError("press step missing key")
                    await host.keyboard.keypress([key])

                elif action == "focus":
                    matched = await _await_active_title(
                        host, step.get("title_contains", ""),
                    )
                    if matched is None:
                        raise RuntimeError(
                            "active window title never matched "
                            f"{step.get('title_contains')!r} "
                            "(escalate to UIA / vision)"
                        )
                    recorder.event("action", layer=self.name,
                                   action="focus", matched=matched)
                    actions.append({"layer": "hotkeys", "action": "focus",
                                    "matched": matched})
                    await self._maybe_frame(
                        host, recorder,
                        label=f"step {step_idx}: focus → {matched}",
                    )
                    continue  # we already recorded; skip the generic record below

                else:
                    raise ValueError(f"unknown hotkey action: {action!r}")

                actions.append({"layer": "hotkeys", **step})
                recorder.event("action", layer=self.name, **step)
                # Capture a screenshot after every state-changing step
                # so the dashboard has visuals. Sleep is a no-op for
                # the UI, so skip those.
                if action != "sleep":
                    await self._maybe_frame(
                        host, recorder,
                        label=f"step {step_idx}: {action}",
                    )

            # Optional value capture (e.g. clipboard read).
            value = None
            if task.hotkey_capture is not None:
                value = await task.hotkey_capture(host)
                recorder.event("action", layer=self.name,
                               action="capture", value=value)
                actions.append({"layer": "hotkeys", "action": "capture",
                                "value": value})
                await self._maybe_frame(host, recorder, label="final state")

            recorder.event("layer_result", layer=self.name,
                           success=True, value=value)
            return LayerOutcome(layer=self.name, success=True,
                                actions=actions,
                                detail=f"recipe of {len(task.hotkey_recipe)} "
                                       f"steps ran clean; captured={value!r}")

        except Exception as exc:
            recorder.event("layer_result", layer=self.name,
                           success=False, error=str(exc))
            return LayerOutcome(layer=self.name, success=False,
                                actions=actions, error=str(exc),
                                detail=f"hotkey recipe failed: {exc}")
