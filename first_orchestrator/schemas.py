from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: int
    kind: Literal["fact", "preference", "tool_outcome", "scratchpad"]
    keywords: list[str]
    descriptor: str            # one short human-readable line
    value: dict                # structured payload
    artifact_id: Optional[int] = None  # Artifact.id or None
    source: str
    run_id: Union[int, str]
    goal_id: Optional[int] = None
    confidence: float
    created_at: datetime


class Artifact(BaseModel):
    id: int                    # numeric content-address derived from sha256
    content_type: str
    size_bytes: int
    source: str
    descriptor: str


class Goal(BaseModel):
    id: int
    text: str                  # short imperative description
    done: bool = False
    attach_artifact_id: Optional[int] = None


class Hit(BaseModel):
    """A memory match returned by `MemoryStore.read`."""

    handle: str                # "mem:<id>"
    descriptor: str
    kind: Optional[str] = None # source MemoryItem kind (fact, tool_outcome, …)
    artifact_id: Optional[int] = None
    score: float = 0.0
    content: Optional[str] = None  # inlined prior answer / tool result


class Observation(BaseModel):
    goals: list[Goal] = Field(default_factory=list)
    attach: Optional[str] = None        # artifact handle to fetch, if any
    goal_id: Optional[int] = None       # currently focused goal

    def next_pending(self) -> Optional[Goal]:
        for g in self.goals:
            if not g.done:
                return g
        return None

    def next_unfinished(self) -> Optional[Goal]:
        """Alias for next_pending()."""
        return self.next_pending()

    @property
    def all_done(self) -> bool:
        return bool(self.goals) and all(g.done for g in self.goals)


class ToolCall(BaseModel):
    name: str
    arguments: dict


class DecisionOutput(BaseModel):
    """Exactly one of `answer` or `tool_call` is populated."""

    answer: Optional[str] = None
    tool_call: Optional[ToolCall] = None

    @property
    def is_answer(self) -> bool:
        """True when the decision produced an answer (no tool call)."""
        return self.tool_call is None and self.answer is not None


# ------------------------------------------------------------------ state

class QueryStateSnapshot(BaseModel):
    """Schema for QueryState JSON snapshots persisted to ``state/``."""

    id: int
    run_id: Union[int, str]
    created_at: datetime
    raw_query: str
    normalized: str = ""
    tokens: list[str] = Field(default_factory=list)
    goal_id: Optional[int] = None
    goals: list[Goal] = Field(default_factory=list)


class ObservationSnapshot(BaseModel):
    """Schema for Observation JSON snapshots persisted to ``state/``."""

    id: int
    run_id: Union[int, str]
    created_at: datetime
    query: str
    attach: Optional[str] = None
    goal_id: Optional[int] = None
    goals: list[Goal] = Field(default_factory=list)


__all__ = [
    "MemoryItem",
    "Artifact",
    "Goal",
    "Hit",
    "Observation",
    "ToolCall",
    "DecisionOutput",
    "QueryStateSnapshot",
    "ObservationSnapshot",
]