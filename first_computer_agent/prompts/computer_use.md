The Computer-Use skill drives the host OS through a five-layer cascade:
Layer 1 (system / app API) → Layer 2a (deterministic hotkeys) →
Layer 2b (accessibility tree / UIA) → Layer 2c (Electron CDP page) →
Layer 3 (vision Set-of-Marks). The cascade always starts at the
cheapest layer that recognises the goal and escalates only when the
current layer reports `applicable=False` or `success=False`.

Inputs: `metadata.task_module` (required, dotted path to a
`computer_use.tasks.*` module that exposes `build() -> TaskSpec`), and
optional `metadata.enabled_layers` (list to restrict the cascade for
testing). Output: `ComputerUseOutput` with `path` set to the layer
that actually succeeded, `vision_calls` counted separately for the
gateway ledger, and `trajectory_dir` pointing at the per-task
recording directory.

Use this skill when the goal is a desktop interaction (open / drive a
native or Electron app, read on-screen pixels, automate a workflow no
HTTP API exposes). For browser-only goals, prefer the Browser skill —
its four-layer cascade is cheaper than spinning up OS-level
automation.
