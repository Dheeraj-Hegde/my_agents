"""In-memory store for user queries and related artifacts.

Provides a `MemoryItem` Pydantic model and a simple `MemoryStore` for
adding, retrieving, and searching items captured during an agent run.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional, Union

from schemas import Goal, Hit, MemoryItem, ToolCall


MemoryKind = Literal["fact", "preference", "tool_outcome", "scratchpad"]

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "for", "and", "or", "but", "if",
    "what", "who", "when", "where", "why", "how", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they", "me", "my", "your",
    "this", "that", "these", "those", "with", "as", "by", "from",
    "can", "could", "would", "should", "will", "shall", "may", "might",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower())
            if t not in _STOPWORDS and len(t) > 2]

DEFAULT_STORE_PATH = Path(__file__).resolve().parent / "state" / "memory.jsonl"


class MemoryStore:
    """A `MemoryItem` store backed by an append-only JSONL file.

    Items are kept in-memory for fast querying and also appended to
    ``path`` so they survive process restarts. On construction the file is
    replayed to rebuild the in-memory index.
    """

    def __init__(
        self,
        path: Union[str, Path, None] = DEFAULT_STORE_PATH,
    ) -> None:
        self._items: dict[int, MemoryItem] = {}
        self.path: Optional[Path] = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    # --------------------------------------------------------- persistence

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = MemoryItem.model_validate_json(line)
                except Exception:
                    continue
                self._items[item.id] = item

    def _append(self, item: MemoryItem) -> None:
        if not self.path:
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(item.model_dump_json())
            f.write("\n")

    def _rewrite(self) -> None:
        """Rewrite the full JSONL file from the in-memory index."""
        if not self.path:
            return
        with self.path.open("w", encoding="utf-8") as f:
            for item in self._items.values():
                f.write(item.model_dump_json())
                f.write("\n")

    def add(self, item: MemoryItem) -> MemoryItem:
        self._items[item.id] = item
        self._append(item)
        return item

    def add_query(
        self,
        query: str,
        *,
        run_id: int | str,
        source: str = "user",
        goal_id: int | None = None,
        keywords: list[str] | None = None,
        artifact_id: int | None = None,
        confidence: float = 1.0,
        kind: MemoryKind = "scratchpad",
    ) -> MemoryItem:
        """Convenience helper to record a raw user query as a `MemoryItem`."""
        item = MemoryItem(
            id=uuid.uuid4().int,
            kind=kind,
            keywords=keywords or _default_keywords(query),
            descriptor=_truncate(query, 120),
            value={"query": query},
            artifact_id=artifact_id,
            source=source,
            run_id=run_id,
            goal_id=goal_id,
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )
        return self.add(item)

    def get(self, item_id: int) -> Optional[MemoryItem]:
        return self._items.get(item_id)

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def by_run(self, run_id: int | str) -> list[MemoryItem]:
        return [i for i in self._items.values() if i.run_id == run_id]

    def by_kind(self, kind: MemoryKind) -> list[MemoryItem]:
        return [i for i in self._items.values() if i.kind == kind]

    def search(self, keyword: str) -> list[MemoryItem]:
        kw = keyword.lower()
        return [
            i for i in self._items.values()
            if kw in i.descriptor.lower()
            or any(kw in k.lower() for k in i.keywords)
        ]

    def clear(self) -> None:
        self._items.clear()
        if self.path and self.path.exists():
            self.path.unlink()

    def __len__(self) -> int:
        return len(self._items)

    # ----------------------------------------------------- agent-loop API

    def read(
        self,
        query: str,
        history: Optional[Iterable[MemoryItem]] = None,
        *,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[Hit]:
        """Token-score memory items against ``query`` and return top hits.

        ``history`` is accepted for parity with the agent-loop signature;
        it is used to exclude very recent items from being recalled (they
        are already in the prompt by other means). Returns up to
        ``top_k`` :class:`Hit` objects ordered by descending score.
        """
        toks = _tokens(query)
        if not toks:
            return []
        token_set = set(toks)
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(t) for t in toks) + r")\b",
            re.IGNORECASE,
        )
        recent_ids = set()
        for it in (history or []):
            if isinstance(it, dict):
                # Plain dict history (new orchestrator) — no id to exclude
                pass
            else:
                recent_ids.add(it.id)
        scored: list[tuple[float, MemoryItem]] = []
        for item in self._items.values():
            # Skip items already present in the prompt context, but never
            # skip ``fact`` items — they exist specifically to be recalled.
            if item.id in recent_ids and item.kind != "fact":
                continue
            # Scratchpads and tool_outcomes are internal bookkeeping
            # (query logs, observe notes, answer events, tool results).
            # They are already surfaced through the ``history`` parameter;
            # recalling them here would drown out the genuinely useful
            # fact items.
            if item.kind in ("scratchpad", "tool_outcome"):
                continue
            v = item.value if isinstance(item.value, dict) else {}
            # For facts, match against the specific goal text rather
            # than the broad original query so that unrelated facts
            # from the same multi-goal run don't pollute results.
            query_field = (
                str(v.get("goal", "")) if item.kind == "fact"
                else str(v.get("query", ""))
            )
            blob_parts = [
                item.descriptor or "",
                " ".join(item.keywords or []),
                query_field,
                str(v.get("answer", "")),
                str(v.get("descriptor", "")),
                str(v.get("result", "")),
                str(v.get("result_preview", "")),
            ]
            blob = " \n ".join(p for p in blob_parts if p)
            if not blob:
                continue
            matches = pattern.findall(blob)
            if not matches:
                continue
            score = len({h.lower() for h in matches}) / max(1, len(token_set))
            # Facts are authoritative user-declared data — boost them.
            if item.kind == "fact":
                score += 0.35
            elif isinstance(v, dict) and v.get("answer"):
                score += 0.1
            if item.artifact_id:
                score += 0.05
            if score >= min_score:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits: list[Hit] = []
        for s, it in scored[:top_k]:
            v = it.value if isinstance(it.value, dict) else {}
            content = (
                v.get("answer")
                or v.get("result")
                or v.get("result_preview")
                or v.get("query")
            )
            hits.append(
                Hit(
                    handle=f"mem:{it.id}",
                    descriptor=it.descriptor,
                    kind=it.kind,
                    artifact_id=it.artifact_id,
                    score=round(s, 4),
                    content=str(content) if content is not None else None,
                )
            )
        return hits

    def observe(
        self,
        query: str,
        hits: list[Hit],
        goals: list[Goal],
        *,
        run_id: int | str,
    ) -> MemoryItem:
        """Persist a scratchpad note about a single perception step.

        If an observe scratchpad for the same query and run already
        exists, update it in place instead of creating a duplicate.
        """
        descriptor = _truncate(
            f"observe[{len(goals)} goals, {len(hits)} hits]: {query}",
            160,
        )
        value = {
            "query": query,
            "hits": [h.model_dump() for h in hits],
            "goals": [g.model_dump() for g in goals],
        }
        # Look for an existing observe scratchpad for this query + run.
        query_lower = query.strip().lower()
        for existing in self._items.values():
            if (existing.kind == "scratchpad"
                    and existing.source == "perception"
                    and existing.run_id == run_id):
                ev = existing.value if isinstance(existing.value, dict) else {}
                if ev.get("query", "").strip().lower() == query_lower:
                    existing.descriptor = descriptor
                    existing.value = value
                    existing.created_at = datetime.now(timezone.utc)
                    self._rewrite()
                    return existing
        item = MemoryItem(
            id=uuid.uuid4().int,
            kind="scratchpad",
            keywords=_default_keywords(query),
            descriptor=descriptor,
            value=value,
            artifact_id=None,
            source="perception",
            run_id=run_id,
            goal_id=None,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        return self.add(item)

    # --------------------------------------------------- fact extraction

    _REMEMBER_SYSTEM = (
        "You extract factual statements that the user is TELLING you "
        "to remember. Only extract facts the user explicitly states "
        "in their own words. Do NOT use your general knowledge to "
        "infer, fill in, or fabricate any facts.\n"
        "\n"
        "# Critical rules\n"
        "- ONLY extract facts the user is DECLARING (e.g. \"My mom's "
        "birthday is 15 May 2026\").\n"
        "- Return [] for questions, commands, requests, fetch/lookup "
        "tasks, greetings, and chit-chat — even if you know the "
        "answer from general knowledge.\n"
        "- NEVER invent facts about people, dates, or topics that the "
        "user did not explicitly state.\n"
        "\n"
        "# Output format (STRICT, machine-parsed)\n"
        "Output ONLY a JSON array of objects. Each object has:\n"
        "  - \"entity\": short noun phrase identifying the subject "
        "(e.g. \"mom's birthday\", \"dentist appointment\")\n"
        "  - \"value\": the specific datum (e.g. \"2026-05-15\", "
        "\"3pm on Tuesday\")\n"
        "  - \"summary\": one plain sentence stating the fact "
        "(e.g. \"Mom's birthday is 2026-05-15.\")\n"
        "\n"
        "If the message contains NO user-declared facts, output "
        "exactly: []\n"
        "\n"
        "Do NOT add prose, markdown fences, or explanation.\n"
        "\n"
        "# Examples\n"
        "Input: My mom's birthday is 15 May 2026.\n"
        "Output: [{\"entity\": \"mom's birthday\", \"value\": "
        "\"2026-05-15\", \"summary\": \"Mom's birthday is "
        "2026-05-15.\"}]\n"
        "\n"
        "Input: when is moms birthday?\n"
        "Output: []\n"
        "\n"
        "Input: Fetch https://en.wikipedia.org/wiki/Claude_Shannon "
        "and tell me his birth date and contributions.\n"
        "Output: []\n"
        "\n"
        "Input: What is the capital of France?\n"
        "Output: []\n"
        "\n"
        "Input: My dentist is Dr. Patel at 555-1234 and my next "
        "appointment is June 3 2026 at 2pm.\n"
        "Output: [{\"entity\": \"dentist\", \"value\": "
        "\"Dr. Patel, 555-1234\", \"summary\": \"Dentist is "
        "Dr. Patel at 555-1234.\"}, {\"entity\": \"dentist "
        "appointment\", \"value\": \"2026-06-03 14:00\", "
        "\"summary\": \"Next dentist appointment is 2026-06-03 "
        "at 2pm.\"}]"
    )

    def remember(
        self,
        query: str,
        *,
        run_id: int | str,
        source: str = "user_query",
        llm: object | None = None,
        provider: str | None = None,
        model: str | None = None,
        auto_route: str | None = "memory",
    ) -> list[MemoryItem]:
        """Extract and store facts from the user's statement.

        Uses the LLM to classify the input and pull out entity/value
        pairs.  Each extracted fact is stored (with dedup) so that
        future ``read()`` calls can surface it.  Returns the list of
        newly stored items (empty if nothing was extracted or if the
        LLM is unavailable).
        """
        if llm is None:
            try:
                from llm_gatewayV3.client import LLM
                llm = LLM()
            except Exception:
                return []
        try:
            resp = llm.chat(
                prompt=query,
                system=self._REMEMBER_SYSTEM,
                provider=provider,
                model=model,
                auto_route=auto_route,
            )
        except Exception:
            return []
        text = (resp.get("text") or "").strip() if isinstance(resp, dict) else ""
        if not text:
            return []
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        # Models sometimes emit reasoning prose before/after the JSON
        # array. Scan all `[...]` candidates (non-nested, sufficient for
        # our flat schema) and pick the last one that parses as a list.
        data = None
        for cand in reversed(re.findall(r"\[[^\[\]]*\]", text, flags=re.DOTALL)):
            try:
                parsed = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                data = parsed
                break
        if data is None:
            return []

        stored: list[MemoryItem] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entity = str(entry.get("entity", "")).strip()
            value = str(entry.get("value", "")).strip()
            summary = str(entry.get("summary", "")).strip()
            if not entity or not summary:
                continue
            # Dedup: check keyword overlap with existing facts.
            fact_toks = set(_tokens(entity))
            duplicate = False
            for existing in self._items.values():
                if existing.kind != "fact":
                    continue
                ev = existing.value if isinstance(existing.value, dict) else {}
                existing_toks = set(_tokens(
                    ev.get("goal", "") or ev.get("entity", "")
                ))
                if fact_toks and existing_toks:
                    jaccard = (len(fact_toks & existing_toks)
                               / len(fact_toks | existing_toks))
                    if jaccard >= 0.6:
                        existing.value = {
                            "entity": entity, "value": value,
                            "goal": entity, "answer": summary,
                        }
                        existing.keywords = _default_keywords(
                            f"{entity} {value} {summary}"
                        )
                        existing.descriptor = _truncate(
                            f"fact: {entity} = {value}", 160
                        )
                        existing.run_id = run_id
                        existing.created_at = datetime.now(timezone.utc)
                        self._rewrite()
                        stored.append(existing)
                        duplicate = True
                        break
            if duplicate:
                continue
            item = MemoryItem(
                id=uuid.uuid4().int,
                kind="fact",
                keywords=_default_keywords(f"{entity} {value} {summary}"),
                descriptor=_truncate(f"fact: {entity} = {value}", 160),
                value={
                    "entity": entity, "value": value,
                    "goal": entity, "answer": summary,
                },
                artifact_id=None,
                source="user",
                run_id=run_id,
                goal_id=None,
                confidence=1.0,
                created_at=datetime.now(timezone.utc),
            )
            self.add(item)
            stored.append(item)
        return stored

    # --------------------------------------------------- answer events

    def record_answer_event(
        self,
        query: str,
        goal_text: str,
        answer: str,
        *,
        run_id: int | str,
        goal_id: int | None = None,
    ) -> MemoryItem:
        """Append an ``answer`` scratchpad event to run history.

        This is NOT a durable fact; it is a transient event visible to
        Perception so it can decide whether the goal is satisfied.
        Durable facts are created by ``remember()`` at the start of
        the run.
        """
        kw = _default_keywords(f"{goal_text} {answer}")
        item = MemoryItem(
            id=uuid.uuid4().int,
            kind="scratchpad",
            keywords=kw,
            descriptor=_truncate(f"answer: {goal_text}", 160),
            value={"query": query, "goal": goal_text, "answer": answer},
            artifact_id=None,
            source="decision",
            run_id=run_id,
            goal_id=goal_id,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        return self.add(item)

    def record_answer(
        self,
        query: str,
        goal_text: str,
        answer: str,
        *,
        run_id: int | str,
        goal_id: int | None = None,
        dedup_threshold: float = 0.6,
    ) -> MemoryItem | None:
        """Persist a completed goal answer as a ``fact`` so future queries
        for the same information can be answered from memory.

        If an existing fact has goal keywords that overlap above
        ``dedup_threshold`` (Jaccard similarity), the existing fact is
        **updated** with the new answer instead of creating a duplicate.
        Returns ``None`` only when the existing fact already has the
        exact same answer text.
        """
        goal_toks = set(_tokens(goal_text))
        best_match: MemoryItem | None = None
        best_score: float = 0.0
        for existing in self._items.values():
            if existing.kind != "fact":
                continue
            ev = existing.value if isinstance(existing.value, dict) else {}
            existing_toks = set(_tokens(ev.get("goal", "")))
            if not goal_toks or not existing_toks:
                continue
            jaccard = len(goal_toks & existing_toks) / len(goal_toks | existing_toks)
            if jaccard > best_score:
                best_score = jaccard
                best_match = existing

        if best_match is not None and best_score >= dedup_threshold:
            ev = best_match.value if isinstance(best_match.value, dict) else {}
            if ev.get("answer", "").strip().lower() == answer.strip().lower():
                return None  # identical answer already stored
            # Update the existing fact in place.
            best_match.value = {"query": query, "goal": goal_text, "answer": answer}
            best_match.keywords = _default_keywords(f"{goal_text} {answer}")
            best_match.descriptor = _truncate(f"answer: {goal_text}", 160)
            best_match.run_id = run_id
            best_match.goal_id = goal_id
            best_match.created_at = datetime.now(timezone.utc)
            self._rewrite()
            return best_match

        kw = _default_keywords(f"{goal_text} {answer}")
        item = MemoryItem(
            id=uuid.uuid4().int,
            kind="fact",
            keywords=kw,
            descriptor=_truncate(f"answer: {goal_text}", 160),
            value={"query": query, "goal": goal_text, "answer": answer},
            artifact_id=None,
            source="decision",
            run_id=run_id,
            goal_id=goal_id,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        return self.add(item)

    def record_outcome(
        self,
        tool_call: ToolCall,
        result_text: str,
        artifact_id: int | None,
        *,
        run_id: int | str,
        goal_id: int | None = None,
        ok: bool = True,
        error: str | None = None,
        payload: str | None = None,
        payload_inline_limit: int = 4096,
    ) -> MemoryItem:
        """Persist a ``tool_outcome`` memory item from an Action result."""
        descriptor = result_text  # backward compat alias
        value: dict = {
            "tool": tool_call.name,
            "arguments": tool_call.arguments,
            "descriptor": descriptor,
            "ok": ok,
        }
        if error:
            value["error"] = error
        if payload is not None and artifact_id is None:
            # Small payload: embed full content so Decision can see it later.
            value["result"] = _truncate(payload, payload_inline_limit)
        elif payload is not None:
            # Large payload lives in artifacts; keep a meaningful head.
            value["result_preview"] = _truncate(payload, 4096)
        kw = _default_keywords(f"{tool_call.name} {descriptor}")
        item = MemoryItem(
            id=uuid.uuid4().int,
            kind="tool_outcome",
            keywords=kw,
            descriptor=_truncate(f"{tool_call.name}: {descriptor}", 160),
            value=value,
            artifact_id=artifact_id,
            source="action",
            run_id=run_id,
            goal_id=goal_id,
            confidence=1.0 if ok else 0.5,
            created_at=datetime.now(timezone.utc),
        )
        return self.add(item)


def _truncate(text: str, n: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "\u2026"


def _default_keywords(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    # Split on whitespace first, then further split each piece on
    # underscores, dots, hyphens and other non-alphanumeric separators
    # so that e.g. "mom_birthday.txt" yields ["mom", "birthday", "txt"]
    # instead of the concatenated "mombirthdaytxt".
    _SEP_RE = re.compile(r"[^a-z0-9]+")
    for raw in text.split():
        parts = _SEP_RE.split(raw.lower())
        for token in parts:
            if len(token) >= 3 and token not in seen:
                seen.add(token)
                out.append(token)
            if len(out) >= 10:
                break
        if len(out) >= 10:
            break
    return out


__all__ = ["MemoryItem", "MemoryKind", "MemoryStore"]
