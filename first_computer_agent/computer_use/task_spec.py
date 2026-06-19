"""Task specifications consumed by every cascade layer.

A TaskSpec is layered: the same TaskSpec can be tried by Layer 1, then
Layer 2a, then 2b, etc. Each layer's `try_(task, host, recorder)`
decides applicability on its own — the orchestrator does not
pre-filter.

`api_handler` and `hotkey_capture` are async callables that receive the
shared `cua.Localhost` so they can use `host.shell.run`,
`host.clipboard.get`, and friends instead of bringing their own OS
plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class TaskSpec:
    """Single Computer-Use task definition.

    Fields are deliberately optional: a calculator task has
    `hotkey_recipe` but no `electron_target`; a VS Code task has
    `electron_target` but no `hotkey_recipe`; a canvas task may have
    neither and rely solely on Layer 3.
    """

    name: str
    goal: str
    # Layer 1 — async callable invoked with the shared cua Localhost.
    # Returns (success, final_value_or_None, detail). Raise
    # NotImplementedError to mark "not an API-solvable task" so the
    # cascade escalates cleanly without counting it as a failure.
    api_handler: Callable[[Any], Awaitable[tuple[bool, str | None, str]]] | None = None
    # Layer 2a — list of {"action": "hotkey"|"type"|"sleep"|...} dicts.
    # Empty list disables Layer 2a for this task.
    hotkey_recipe: list[dict] = field(default_factory=list)
    # Layer 2a follow-up: after the recipe runs, the layer optionally
    # captures a value back (clipboard contents, active title, ...).
    # Async, takes the host, returns str|None.
    hotkey_capture: Callable[[Any], Awaitable[str | None]] | None = None
    # Layer 2b — UIA target list. Empty disables UIA.
    uia_recipe: list[dict] = field(default_factory=list)
    # Layer 2c — Electron CDP attach config.
    electron_target: dict[str, Any] | None = None
    # Layer 3 — free-text VLM goal.
    vision_goal: str | None = None
    # Region hint for the screenshot {"left","top","width","height"};
    # None means full screen.
    vision_region: dict | None = None
    # Hard cap on vision turns.
    max_vision_turns: int = 3
    # Per-turn post-condition check ("scan → act → verify"). When set,
    # Layer 3 re-screenshots AFTER each non-finish action and asks the
    # VLM whether the post-condition holds. The verify call is a
    # separate VLM round-trip (counted in `vision_calls`) and is
    # recorded as a `verify` event in the trajectory. Leaving this
    # `None` preserves the old behaviour (act-only loop) so existing
    # tasks are unaffected.
    verify_goal: str | None = None
    # How many CONSECUTIVE verify-false verdicts to tolerate before
    # the layer gives up. The default 2 means: one bad turn is a
    # blip (re-scan next turn may show it actually worked); two in
    # a row means the layer is stuck and the cascade should escalate
    # / fail rather than burn the rest of `max_vision_turns`.
    max_verify_failures: int = 2
    # Domain validator: async callable invoked AFTER the cascade has
    # short-circuited on a successful layer, while the shared cua
    # `host` is still open. Receives the partially-built
    # `ComputerUseOutput` and the live `host`; returns
    # `(ok: bool, detail: str)`.  When `ok` is False the cascade's
    # success is downgraded to a task-level failure (the layer's
    # individual outcome stays "success" on the trajectory, but the
    # task ends with `success=False` and the validator's reason is
    # surfaced under `validation_detail`). This lets, e.g., the Paint
    # task accept a Layer-3 `finish` action only when the canvas
    # actually has ink on it, instead of trusting the model's
    # self-report. `None` means "no validation; trust the layer".
    validator: Callable[[Any, Any], Awaitable[tuple[bool, str]]] | None = None
    # Free-form metadata; surfaces in the trajectory meta.
    meta: dict = field(default_factory=dict)
