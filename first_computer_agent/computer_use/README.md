# Computer-Use skill (Session 10)

The Computer-Use skill drives the host OS through a **five-layer
cascade**, mirroring the discipline of the Session-9 Browser skill but
extended one rung deeper for native + Electron control. Every layer
shares a single `cua.Localhost` handle for OS primitives
(mouse / keyboard / screen / clipboard / shell / window) — the
`cua-sandbox` package's `Localhost` is the spine; only the two layers
where cua has no native surface (UIA, CDP) pull in extra libs.

| layer | name        | underlying tool                                                | LLM? |
|------:|-------------|----------------------------------------------------------------|------|
| 1     | `api`       | `host.shell.run` / `host.clipboard.*` / task's `api_handler`   | no   |
| 2a    | `hotkeys`   | `host.keyboard.keypress` / `host.keyboard.type` / `host.shell` | no   |
| 2b    | `uia`       | `uiautomation` (cua doesn't expose a Windows a11y tree)        | no   |
| 2c    | `electron`  | Playwright `connect_over_cdp` (cua doesn't speak CDP)          | no   |
| 3     | `vision`    | `host.screen.screenshot` + V9 gateway `/v1/vision`             | yes  |

The cascade rule is identical to Browser's: each layer is *tried* only
after the cheaper one returned `applicable=False` or `success=False`.
The layer that succeeds is the only one credited in
`ComputerUseOutput.path`; trajectory events are tagged with the layer
that emitted them so replay can colour-code the run.

## File layout

```
computer_use/
  __init__.py          # re-exports
  schemas.py           # ComputerUseOutput, LayerOutcome, ComputerUseLayer
  task_spec.py         # TaskSpec dataclass consumed by every layer
  recorder.py          # start_recording / stop_recording + Recorder
  skill.py             # ComputerUseSkill (cascade orchestrator)
  layers/
    layer1_api.py
    layer2a_hotkeys.py
    layer2b_uia.py
    layer2c_electron.py
    layer3_vision.py
  tasks/
    calculator.py      # Layer 2a, zero vision
    vscode_editor.py   # Layer 2c (electron_debugging_port)
    canvas_sketch.py   # Layer 3 (forced vision)
prompts/computer_use.md
run_computer_use_tasks.py
```

The dispatcher wiring in `skills.py` is a single `if skill.name ==
"computer_use":` branch — same shape as the existing `browser` and
`sandbox_executor` branches. No edits to `flow.py`.

## Bundled tasks

1. **`01_calculator_hotkeys`** — Launches `calc.exe`, types
   `12345+67890=`, `Ctrl+C`, reads the clipboard. Lands at Layer 2a
   with **zero vision calls**. Layer 2b carries a UIA fallback recipe
   in case `calc.exe` is missing.
2. **`02_vscode_electron_cdp`** — Attaches to VS Code over CDP on
   `--remote-debugging-port=9222`, runs a small JS probe via
   `page.evaluate()` that enumerates the open editor tabs, returns
   `{count, first, active, tabs_sample}`. Lands at **Layer 2c**.
3. **`03_canvas_vision`** — Opens a bundled HTML page whose
   `<canvas>` draws four random characters with no DOM text node
   anywhere; the goal is to read the four characters. Layers 1, 2a,
   2b, 2c all mark themselves non-applicable; the cascade lands at
   **Layer 3** and posts the screenshot to `/v1/vision`.

Constraints satisfied:

- At least one task uses vision  → `03_canvas_vision`
- At least one task uses the Electron page path → `02_vscode_electron_cdp`
- At least one task completes with zero vision calls → `01_calculator_hotkeys`

## Run it

Install OS-level deps (Windows-only ones are conditional):

```powershell
uv sync
uv run playwright install chromium    # only if you don't already have it
```

Optional: launch VS Code with the debugging port so task 2 can attach
without spawning a fresh instance:

```powershell
code --remote-debugging-port=9222 --user-data-dir $env:TEMP\cua-vscode
```

Then run all three tasks:

```powershell
uv run python run_computer_use_tasks.py
```

Or one at a time:

```powershell
uv run python run_computer_use_tasks.py --only calculator
uv run python run_computer_use_tasks.py --only vscode
uv run python run_computer_use_tasks.py --only canvas
```

> **Heads-up:** tasks 1 and 3 take focus away from the active window
> (Calculator hotkeys, screenshot of the foreground browser). Don't
> type during a run.

## Trajectory layout (the evidence)

Each task gets its own directory under
`state/trajectories/<session>/<task>/`:

```
state/trajectories/cua_20260614_140312/
  01_calculator_hotkeys/
    events.jsonl     # one JSON event per line (layer_try, action, layer_result, ...)
    meta.json        # session, task, duration, success, summary
    frames/          # empty for task 1 — no screenshots needed
  02_vscode_electron_cdp/
    events.jsonl
    meta.json
    frames/
      frame_0001.png # VS Code workbench page (Playwright screenshot)
  03_canvas_vision/
    events.jsonl
    meta.json
    frames/
      frame_0001.png # full-screen screenshot fed to the VLM
      frame_0002.png # screenshot for the second turn if needed
```

Submit `state/trajectories/<session>/` as the evidence directory.

## Calling from the orchestrator

The skill is registered in `agent_config.yaml`. A Planner can emit:

```json
{"skill": "computer_use",
 "metadata": {"task_module": "computer_use.tasks.calculator"}}
```

The dispatcher imports `task_module`, calls its `build()`, runs the
cascade, and returns `AgentResult` with `output` = `ComputerUseOutput`
dict. Replay shows the chosen layer just like it does for Browser.

## Cascade discipline in code

Every layer module is `async def try_(self, task, host, recorder)` and
returns the same `LayerOutcome` shape. No layer calls into another.
The orchestrator (`computer_use/skill.py`) is the only place that knows
the cascade order and the only place that opens a cua connection:

```python
async with cua.Localhost.connect() as host:
    for layer_name in CASCADE_ORDER:
        outcome = await self._layers[layer_name].try_(task, host, recorder)
        ...
```

`CASCADE_ORDER` is `["api", "hotkeys", "uia", "electron", "vision"]`.
A layer that recognises the task but cannot run it returns
`applicable=False` (silent skip, no cost). A layer that *tries* and
fails returns `success=False` (the orchestrator records a
`cascade_escalate` event and moves on). Layer 3's `vision_calls`
counter is incremented per VLM round-trip so the gateway ledger and
the trajectory agree on cost.

`ComputerUseSkill.run()` is a thin sync facade around `run_async()`;
the dispatcher in `skills.py` calls it via `asyncio.to_thread` so the
event-loop boundary is clean.
