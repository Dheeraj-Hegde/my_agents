"""Typed contracts for the Computer-Use skill.

Kept in a sibling module instead of `schemas.py` at the repo root so the
S8 runtime stays untouched. The runtime only sees `ComputerUseOutput`
because the dispatcher in `skills.py` casts it into the existing
`AgentResult.output` dict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# The five cascade layers, in escalation order. Used in
# ComputerUseOutput.path and in every trajectory event so replay can
# colour-code the layer that produced each action.
ComputerUseLayer = Literal[
    "api",        # Layer 1
    "hotkeys",    # Layer 2a
    "uia",        # Layer 2b
    "electron",   # Layer 2c
    "vision",     # Layer 3
]


class LayerOutcome(BaseModel):
    """What every layer returns to the cascade orchestrator.

    `applicable` is False when the layer recognised the goal but cannot
    handle it (e.g. UIA on a canvas with no accessible children) — the
    orchestrator escalates without counting the call as a failure.
    `success` is False only when the layer *tried* and the attempt did
    not satisfy the goal — that counts as a real failure for that layer.
    """

    layer: ComputerUseLayer
    applicable: bool = True
    success: bool = False
    detail: str = ""
    actions: list[dict] = Field(default_factory=list)
    frames: list[str] = Field(default_factory=list)  # paths to PNGs
    error: str | None = None


class ComputerUseOutput(BaseModel):
    """What the Computer-Use skill writes into AgentResult.output."""

    task: str
    goal: str
    path: ComputerUseLayer
    success: bool = False               # cascade succeeded at `path`
                                        # AND the task validator (if any)
                                        # accepted the result.
    layers_tried: list[ComputerUseLayer] = Field(default_factory=list)
    vision_calls: int = 0
    turns: int = 0
    actions: list[dict] = Field(default_factory=list)
    final_value: str | None = None     # e.g. calculator result, file path
    trajectory_dir: str | None = None  # absolute path on disk
    detail: str = ""
    # Domain validation result (None ⇒ task defined no validator;
    # True ⇒ validator accepted; False ⇒ validator rejected and
    # `success` was downgraded). The reason string is always populated
    # when `validated is False`.
    validated: bool | None = None
    validation_detail: str = ""
