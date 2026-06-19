"""Layer 2c — Electron CDP page.

Modern Electron apps (VS Code, Slack, Cursor, Discord, Notion) speak
the Chrome DevTools Protocol when launched with
`--remote-debugging-port=<n>`. Playwright can attach to a running
instance over CDP and then drive the renderer process exactly like a
browser tab: `page.evaluate(...)` to query / mutate DOM,
`page.locator` to find ARIA elements, `page.screenshot()` for a frame.

cua does not speak CDP, so we keep Playwright for the CDP attach —
but the *launch* of the Electron binary goes through `host.shell.run`
so the cascade has one consistent "spawn external process" surface.

This layer is the right call for any Electron target. It is
*non-applicable* when:
    - no `electron_target` on the task, OR
    - the configured port is not reachable.

Target schema (`task.electron_target`):
    {
        "port": 9222,                         # remote-debugging-port
        "page_url_contains": "vscode-app",   # pick a CDP page by URL
        "script": "<JS string>",              # executed via page.evaluate
        "expect_substring": "...",            # success check on script return
        "launch_argv": "code --remote-debugging-port=9222",
        "launch_wait_s": 8.0,
    }
"""

from __future__ import annotations

import asyncio
import shlex
import socket
import subprocess
import sys
from typing import Any

from ..recorder import Recorder
from ..schemas import LayerOutcome
from ..task_spec import TaskSpec


def _spawn_detached(cmd: str) -> None:
    """Same as the Layer 2a helper: cua's shell.run(background=True)
    requires a PTY, so we shell out via subprocess. No DETACHED_PROCESS
    flag so Windows still grants foreground to the new GUI window."""
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


def _port_open(port: int, host_ip: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host_ip, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _drive_electron(target: dict[str, Any], recorder: Recorder) -> dict:
    """Attach via CDP, execute the script, capture a screenshot frame,
    return {"value", "url", "title"}."""
    from playwright.async_api import async_playwright  # type: ignore

    port = int(target["port"])
    page_filter = target.get("page_url_contains", "")
    script = target["script"]

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}"
        )
        try:
            # Walk all contexts → pages and pick the first that matches.
            picked = None
            for ctx in browser.contexts:
                for page in ctx.pages:
                    url = page.url or ""
                    if page_filter and page_filter not in url:
                        continue
                    picked = page
                    break
                if picked is not None:
                    break

            if picked is None:
                # Fall back to first available page regardless of filter
                # — Electron sometimes serves chrome-extension:// or
                # devtools:// URLs that don't match the natural filter.
                for ctx in browser.contexts:
                    if ctx.pages:
                        picked = ctx.pages[0]
                        break

            if picked is None:
                raise RuntimeError("CDP connected but found 0 pages")

            recorder.event("action", layer="electron",
                           action="attach", url=picked.url,
                           title=await picked.title())

            value = await picked.evaluate(script)
            recorder.event("action", layer="electron",
                           action="evaluate",
                           returned=str(value)[:500])

            try:
                shot = await picked.screenshot(type="png", full_page=False)
                recorder.frame(shot, label="electron page")
            except Exception:
                pass

            return {"value": value, "url": picked.url,
                    "title": await picked.title()}
        finally:
            try:
                await browser.close()
            except Exception:
                pass


class Layer2cElectron:
    name = "electron"

    async def try_(self, task: TaskSpec, host, recorder: Recorder) -> LayerOutcome:
        target = task.electron_target
        if not target:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="no electron_target on task")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="task has no electron_target")

        port = int(target.get("port", 0))
        if not port:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="electron_target missing port")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="electron_target missing port")

        # If the port is closed and we have a launch_argv, spawn the
        # Electron app via the shared cua shell so the cascade has one
        # consistent "spawn external process" surface.
        if not _port_open(port):
            launch = target.get("launch_argv")
            if launch:
                cmd = (" ".join(str(a) for a in launch)
                       if isinstance(launch, (list, tuple))
                       else str(launch))
                recorder.event("action", layer=self.name,
                               action="launch", cmd=cmd)
                try:
                    await asyncio.to_thread(_spawn_detached, cmd)
                except Exception as exc:
                    recorder.event("layer_result", layer=self.name,
                                   success=False, error=str(exc))
                    return LayerOutcome(
                        layer=self.name, success=False, error=str(exc),
                        detail=f"failed to spawn {cmd!r}",
                    )
                deadline = asyncio.get_event_loop().time() + float(
                    target.get("launch_wait_s", 8.0)
                )
                while asyncio.get_event_loop().time() < deadline:
                    if _port_open(port):
                        break
                    await asyncio.sleep(0.5)

        if not _port_open(port):
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason=f"port {port} not listening")
            return LayerOutcome(
                layer=self.name, applicable=False,
                detail=f"no CDP listener on port {port}; "
                       "start the Electron app with "
                       f"--remote-debugging-port={port}",
            )

        recorder.event("layer_try", layer=self.name, applicable=True,
                       port=port)

        try:
            result = await _drive_electron(target, recorder)
        except Exception as exc:
            recorder.event("layer_result", layer=self.name,
                           success=False, error=str(exc))
            return LayerOutcome(
                layer=self.name, success=False, error=str(exc),
                detail=f"CDP drive failed: {exc}",
            )

        expected = target.get("expect_substring")
        ok = True
        if expected is not None:
            ok = expected in (str(result.get("value", "")))

        recorder.event("layer_result", layer=self.name,
                       success=ok, value=str(result.get("value"))[:500],
                       url=result.get("url"))
        return LayerOutcome(
            layer=self.name, success=ok,
            actions=[{"layer": "electron", "action": "evaluate",
                      "url": result.get("url"),
                      # `value` is the FULL evaluated string, used by
                      # downstream consumers (e.g. task validators that
                      # need to JSON-parse it). `value_excerpt` is the
                      # short display version that goes into summary
                      # printouts. Keep both so the trajectory remains
                      # human-readable while validators stay correct.
                      "value": str(result.get("value")),
                      "value_excerpt": str(result.get("value"))[:200]}],
            detail=f"CDP page {result.get('url')!r}; expected_match={ok}",
        )
