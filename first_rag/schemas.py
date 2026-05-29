"""Typed contracts every layer in the S7 agent talks in.

One small file, read top-to-bottom. Every other module imports from here, so
the boundary between layers is a Pydantic model rather than a free-form dict.

Session 7 adds one optional field on `MemoryItem`: `embedding`. Items of
kind `fact`, `preference`, and `tool_outcome` carry a vector embedding
written by Memory at insert time. The embedding underlies FAISS vector
search. Items of kind `scratchpad` are run-scoped and skip embedding.

Ids are random 63-bit positive integers (fit in a signed 64-bit slot and
in JSON without precision loss). When an artifact id needs to be embedded
in a free-form string (descriptors, MCP tool path arguments), use the
textual form `"artifact:<int>"`.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def new_id(prefix: str = "id") -> int:
    """Return a random positive 63-bit integer id.

    The `prefix` argument is accepted for call-site readability only and
    has no effect on the returned value. Collision probability across the
    lifetime of this project is negligible (~1 in 2**63)."""
    return secrets.randbits(63) or 1


# ── Memory ──────────────────────────────────────────────────────────────────

MemoryKind = Literal["fact", "preference", "tool_outcome", "scratchpad"]


class MemoryItem(BaseModel):
    """One record in memory. Reads happen by vector similarity first
    (FAISS over the `embedding` field) with keyword overlap as the
    fallback when vector search returns nothing. Bytes never live here;
    they live in the artifact store."""

    id: int
    kind: MemoryKind
    keywords: list[str] = Field(default_factory=list)
    descriptor: str                              # one short human-readable line
    value: dict = Field(default_factory=dict)    # structured payload
    artifact_id: int | None = None
    embedding: list[float] | None = None         # set by Memory at write time
    source: str
    run_id: str
    goal_id: int | None = None
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Artifacts ───────────────────────────────────────────────────────────────

class Artifact(BaseModel):
    id: int
    content_type: str
    size_bytes: int
    source: str
    descriptor: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Goals & Observations ────────────────────────────────────────────────────

class Goal(BaseModel):
    id: int
    text: str
    done: bool = False
    attach_artifact_id: int | None = None        # Perception sets this when the goal needs raw bytes


class Observation(BaseModel):
    goals: list[Goal]

    @property
    def all_done(self) -> bool:
        return bool(self.goals) and all(g.done for g in self.goals)

    def next_unfinished(self) -> Goal | None:
        return next((g for g in self.goals if not g.done), None)


# ── Decision output ─────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    name: str
    arguments: dict


class DecisionOutput(BaseModel):
    """Decision emits exactly one of these two. `answer` carries arbitrary
    semantic work (summarise, extract, compare, translate) inside its text."""

    answer: str | None = None
    tool_call: ToolCall | None = None

    @property
    def is_answer(self) -> bool:
        return self.answer is not None
