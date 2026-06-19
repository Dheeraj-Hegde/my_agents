"""Task 2 — VS Code (Electron) via CDP page.

Goal: enumerate the names of files open in the VS Code window connected
on `--remote-debugging-port=9222`, and return the count + first file
name. The script runs in the VS Code renderer process via Playwright's
`page.evaluate`.

How the cascade lands here:
  - Layer 1: no shell command returns the list of currently-open editors
    for a running VS Code instance from the outside, so the API handler
    is intentionally absent.
  - Layer 2a (hotkeys) is empty for this task: blind keystrokes cannot
    introspect the editor's open-files list.
  - Layer 2b (UIA): VS Code's renderer is a Chromium webview; UIA sees
    only the outer Electron shell, so we leave the UIA recipe empty and
    the layer marks itself non-applicable.
  - Layer 2c (Electron CDP): the natural fit. The script queries the
    DOM for `.tab` ARIA labels and the editor area's text-model file
    name, which works on every modern VS Code build.

Prerequisite: start VS Code once with
    code --remote-debugging-port=9222 --user-data-dir <some-dir>
or set CUA_VSCODE_PORT to a different port + launch with that flag.

The script is conservative: it falls back through three selectors so a
theme/extension that hides the natural `.tab[role="tab"]` nodes does not
cause a spurious cascade failure.
"""

from __future__ import annotations

import json
import os
import shutil

from ..task_spec import TaskSpec


# Same script we'd type into the VS Code DevTools console.
_VSCODE_PROBE_JS = r"""
(() => {
  const out = { tabs: [], active: null, source: null };
  // Preferred: ARIA role=tab nodes in the editor group.
  let tabs = Array.from(document.querySelectorAll(
    '.tabs-and-actions-container .tab[role="tab"]'));
  if (!tabs.length) {
    tabs = Array.from(document.querySelectorAll('.tab'));
  }
  if (tabs.length) {
    out.source = 'dom-tabs';
    out.tabs = tabs.map(t => (t.getAttribute('aria-label')
                               || t.innerText || '').trim())
                   .filter(Boolean);
    const active = tabs.find(t => t.classList.contains('active'));
    out.active = active
        ? (active.getAttribute('aria-label') || active.innerText || '').trim()
        : null;
  }
  // Last-ditch: window title.
  if (!out.tabs.length) {
    out.source = 'title';
    out.tabs = [document.title];
    out.active = document.title;
  }
  return JSON.stringify({
    cwd: window.location && window.location.href,
    count: out.tabs.length,
    first: out.tabs[0] || null,
    active: out.active,
    source: out.source,
    tabs_sample: out.tabs.slice(0, 8),
  });
})()
"""


def build() -> TaskSpec:
    port = int(os.environ.get("CUA_VSCODE_PORT", "9222"))

    # Resolve VS Code launch command. The runner only spawns when the
    # port is closed; if VS Code is already attached, this is unused.
    code_exe = (
        os.environ.get("CUA_VSCODE_EXE")
        or shutil.which("code")
        or shutil.which("Code")
        or "code"
    )
    launch_argv = [
        code_exe,
        f"--remote-debugging-port={port}",
        "--new-window",
    ]

    async def _validate(out, host) -> tuple[bool, str]:
        """Confirm the CDP probe really returned a JSON object with
        `count >= 1`. The layer's "success" only means the JS
        evaluated without throwing; an empty Welcome window or a
        renderer that returned `null` would still pass that bar."""
        raw = out.final_value or ""
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            return False, f"final_value is not JSON: {exc}; raw={raw[:120]!r}"
        if not isinstance(parsed, dict):
            return False, f"parsed JSON is not an object: type={type(parsed).__name__}"
        count = parsed.get("count")
        if not isinstance(count, int) or count < 1:
            return False, (
                f"expected count >= 1 in CDP response, got count={count!r}, "
                f"first={parsed.get('first')!r}"
            )
        return True, (
            f"CDP returned count={count}, first={parsed.get('first')!r}, "
            f"source={parsed.get('source')!r}"
        )

    return TaskSpec(
        name="02_vscode_electron_cdp",
        goal=f"Attach to VS Code over CDP on port {port} and report the "
             "list of open editor tabs.",
        api_handler=None,
        hotkey_recipe=[],
        uia_recipe=[],
        electron_target={
            "port": port,
            "page_url_contains": "workbench.html",  # VS Code workbench frame
            "script": _VSCODE_PROBE_JS,
            "expect_substring": '"count":',  # JSON always has this key
            "launch_argv": launch_argv,
            "launch_wait_s": 12.0,
        },
        # Domain validator: parse the JSON probe result, demand
        # count>=1. Prevents a "successful" CDP attach to a window
        # with no open editors from masquerading as a real success.
        validator=_validate,
        meta={"port": port, "exe": code_exe},
    )
