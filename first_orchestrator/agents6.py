"""agents6 – orchestrator that wires Memory, Perception, Artifacts,
Decision and Action (MCP) into the single loop shown in the architecture
diagram:

    loop:
      hits       = memory.read(query, history)
      obs        = perception.observe(query, hits, history, prior_goals)
      if obs.all_done: break
      goal       = obs.next_unfinished()
      attached   = artifacts.get_bytes(goal.attach) if attachment
      out        = decision.next_step(goal, hits, attached, history, tools)
      if out.is_answer: record answer; continue
      desc, aid  = action.execute(tool_call)
      memory.record_outcome(tool_call, result, artifact_id)
      append to history; iterate

Run directly for an interactive REPL:
    python agents6.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from action import Action, execute as action_execute
from artifacts import Artifacts
from decision import DEFAULT_SYSTEM_PROMPT, Decision
from llm_gatewayV3.client import LLM
from memory import MemoryStore
from perception import Perception
from schemas import DecisionOutput, Goal, Hit, MemoryItem, Observation


SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

MAX_ITERATIONS = 8
MEMORY_TOP_K = 5
MEMORY_MIN_SCORE = 0.3


# Multi-item goal detection.
#
# Goals with an explicit READ/FETCH verb + quantity (e.g. "read top 3
# results", "fetch each url", "open all the pages") require several
# tool calls before the goal can be answered. The orchestrator drives
# an inner action loop for these so a single iteration can issue all
# N tool calls (keeping the outer iteration count small) and Decision
# is not allowed to emit a FINAL_ANSWER until the count is satisfied.
#
# Bare counts like "find 3 things", "suggest 5 places", "list 4
# options" do NOT match — a single web_search with max_results=N
# already satisfies those, and Decision should just answer from the
# search snippets.
_QUANT_RE = re.compile(
    r"\b(?:read|fetch|open|visit|follow|summari[sz]e|extract)\s+"
    r"(?:the\s+)?(?:top|first|next)?\s*(\d+)\b",
    re.IGNORECASE,
)
_ALL_EACH_RE = re.compile(
    r"\b(?:read|fetch|open|visit|follow|summari[sz]e|extract)\s+"
    r"(?:all\s+(?:of\s+)?(?:the\s+)?(?:result|url|link|page)s?|"
    r"each\s+(?:of\s+(?:the\s+)?)?(?:result|url|link|page)s?|"
    r"every\s+(?:result|url|link|page))\b",
    re.IGNORECASE,
)
# Tools that count toward a multi-item fetch/read goal.
_FETCH_TOOLS = {"fetch_url", "read_file"}
# Safety cap on inner action loop iterations per outer iteration.
_INNER_MAX = 6


def _required_count(goal_text: str, default_all: int = 3) -> Optional[int]:
    """Return N if the goal text specifies a quantity, else None.

    'top 3 results' -> 3, 'read 5 pages' -> 5, 'all results' -> default_all,
    'fetch each url' -> default_all. Returns None when no quantity is
    expressed (single-item goal).
    """
    if not goal_text:
        return None
    m = _QUANT_RE.search(goal_text)
    if m:
        try:
            n = int(m.group(1))
            return n if n > 1 else None
        except ValueError:
            pass
    if _ALL_EACH_RE.search(goal_text):
        return default_all
    return None


def _count_fetches(history: list, goal_id: int, tools: set[str]) -> int:
    """Count successful tool_outcome entries for goal_id whose tool is in `tools`."""
    n = 0
    for it in history or []:
        # MemoryItem-style entries
        gid = getattr(it, "goal_id", None)
        kind = getattr(it, "kind", None)
        if gid == goal_id and kind == "tool_outcome":
            v = it.value if isinstance(getattr(it, "value", None), dict) else {}
            if v.get("tool") in tools and v.get("ok", True):
                n += 1
    return n


# ---------------------------------------------------------- iteration record

@dataclass
class IterationRecord:
    """One pass through the loop, kept for diagnostics."""

    step: int
    goal: Optional[Goal] = None
    hits: list[Hit] = field(default_factory=list)
    observation: Optional[Observation] = None
    decision: Optional[DecisionOutput] = None
    action_descriptor: Optional[str] = None
    artifact_id: Optional[int] = None
    error: Optional[str] = None


# --------------------------------------------------------------- MCP session

@asynccontextmanager
async def mcp_session():
    """Yield an MCP ``ClientSession`` connected to the local MCP server
    over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_script = str(Path(__file__).resolve().parent / "mcp_server.py")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session) -> list:
    """Return the list of tool descriptors from the MCP session."""
    result = await session.list_tools()
    return result.tools


def mcp_tools_for_decision(mcp_tools: list) -> str:
    """Format MCP tool descriptors into a text block for the Decision prompt."""
    if not mcp_tools:
        return "No MCP tools available; answer directly."
    lines = ["Available MCP tools:"]
    for tool in mcp_tools:
        desc = getattr(tool, "description", "") or ""
        first_line = desc.strip().splitlines()[0] if desc.strip() else ""
        sig_parts = [tool.name]
        schema = getattr(tool, "inputSchema", None) or {}
        props = schema.get("properties", {})
        if props:
            params = ", ".join(
                f"{k}: {v.get('type', '?')}" for k, v in props.items()
            )
            sig_parts.append(f"({params})")
        else:
            sig_parts.append("()")
        lines.append(f"- {''.join(sig_parts)}  -- {first_line}")
    return "\n".join(lines)


def final_answer_from(history: list[dict]) -> str:
    """Extract the final user-facing answer from the run history."""
    answers: list[str] = []
    for entry in history:
        if entry.get("kind") == "answer":
            text = entry.get("text", "").strip()
            if text:
                answers.append(text)
    if not answers:
        # Fallback: summarise the last action result
        for entry in reversed(history):
            if entry.get("kind") == "action":
                desc = entry.get("result_descriptor", "")
                if desc:
                    return desc
        return ""
    return "\n\n".join(answers)


# --------------------------------------------------------------- core run()

async def run(query: str) -> str:
    """Async orchestrator loop over an MCP session."""
    run_id = uuid.uuid4().hex[:8]
    history: list[dict] = []
    prior_goals: list[Goal] = []

    memory = MemoryStore()
    perception = Perception()
    decision = Decision(system_prompt=SYSTEM_PROMPT)
    artifacts = Artifacts()

    # Durable memory: classify the user's query so facts/preferences
    # in it survive into future runs.
    memory.remember(query, source="user_query", run_id=run_id)

    async with mcp_session() as session:
        mcp_tools = await load_tools(session)
        tools = mcp_tools_for_decision(mcp_tools)

        for it in range(1, MAX_ITERATIONS + 1):
            hits = memory.read(query, history)
            obs = perception.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            if obs.all_done:
                break

            goal = obs.next_unfinished()
            if goal is None:
                break

            attached: list[tuple] = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                attached.append((
                    goal.attach_artifact_id,
                    artifacts.get_bytes(goal.attach_artifact_id),
                ))

            out = decision.next_step(
                goal, hits, attached, history, tools,
                all_goals=obs.goals, query=query,
            )

            if out.is_answer:
                history.append({"iter": it, "kind": "answer",
                                "goal_id": goal.id, "text": out.answer})
                continue

            result_text, art_id = await action_execute(
                session, out.tool_call, artifacts
            )
            memory.record_outcome(
                tool_call=out.tool_call,
                result_text=result_text,
                artifact_id=art_id,
                run_id=run_id,
                goal_id=goal.id,
            )
            history.append({"iter": it, "kind": "action",
                            "goal_id": goal.id, "tool": out.tool_call.name,
                            "arguments": out.tool_call.arguments,
                            "result_descriptor": result_text[:300],
                            "artifact_id": art_id})

    return final_answer_from(history)


class Agent6:
    """Orchestrator: Memory → Perception → Artifacts → Decision → Action.

    Implements the agent6 loop from the architecture diagram.
    """

    def __init__(
        self,
        *,
        llm: Optional[LLM] = None,
        decision: Optional[Decision] = None,
        action: Optional[Action] = None,
        artifacts: Optional[Artifacts] = None,
        store: Optional[MemoryStore] = None,
        store_path: Union[str, Path, None] = None,
        run_id: Optional[int] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        auto_route: Optional[str] = "decision",
        max_iters: int = MAX_ITERATIONS,
        memory_min_score: float = MEMORY_MIN_SCORE,
        memory_top_k: int = MEMORY_TOP_K,
    ) -> None:
        self.action = action or Action()
        self.artifacts = artifacts or Artifacts()
        if decision is not None:
            self.decision = decision
        else:
            self.decision = Decision(
                llm=llm,
                action=self.action,
                provider=provider,
                model=model,
                auto_route=auto_route,
                system_prompt=SYSTEM_PROMPT,
            )
        if store is not None:
            self.store = store
        elif store_path is not None:
            self.store = MemoryStore(path=store_path)
        else:
            self.store = MemoryStore()
        self.perception = Perception()
        self.max_iters = max_iters
        self.memory_min_score = memory_min_score
        self.memory_top_k = memory_top_k

        # Per-run state (reset on each .run() call)
        self.run_id = run_id if run_id is not None else uuid.uuid4().int
        self.last_observation: Optional[Observation] = None
        self.trace: list[IterationRecord] = []

    # ────────────────────────────────────────────────────── public API

    def run(
        self,
        query: str,
        *,
        verbose: bool = False,
        on_step: Optional[Callable[[IterationRecord], None]] = None,
    ) -> str:
        """Execute the agent6 loop and return the final answer.

        Each iteration follows the architecture diagram exactly:
          1. Memory.read(query, history) → hits[]
          2. Perception.observe(query, hits, history, prior_goals)
                → Observation(goals, attach?)
          3. [goal has attachment] → Artifacts.get_bytes(art:…) → bytes
          4. Decision.next_step(goal, hits, attached, history, tools)
                → answer | tool_call
          5. [Decision picks a tool] → Action.execute(tool_call)
                → descriptor, artifact_id?
          6. Memory.record_outcome(tool_call, result, artifact_id)
          7. Append to history, iterate
        """
        self._reset(query)
        tools_block = self.action.tools_block()

        # Pre-loop: extract & persist any user-declared facts
        extracted = self.store.remember(
            query,
            run_id=self.run_id,
            llm=self.decision.llm,
            provider=self.decision.provider,
            model=self.decision.model,
        )

        prior_goals: list[Goal] = []
        answers: dict[int, str] = {}

        for step in range(1, self.max_iters + 1):
            rec = IterationRecord(step=step)

            # ── 1. Memory: read(query, history) → hits[] ──
            history = self.store.by_run(self.run_id)
            rec.hits = self.store.read(
                query,
                history,
                top_k=self.memory_top_k,
                min_score=self.memory_min_score,
            )

            # ── 2. Perception: observe(...) → Observation(goals, attach?) ──
            obs = self.perception.observe(query, rec.hits, history, prior_goals)
            self.store.observe(query, rec.hits, obs.goals, run_id=self.run_id)
            rec.observation = obs
            self.last_observation = obs

            if verbose:
                _emit_iter_header(step)
                if step == 1 and extracted:
                    for item in extracted:
                        v = item.value if isinstance(item.value, dict) else {}
                        print(
                            f"{_label('memory.remember')} "
                            f"fact: {v.get('entity', '?')} = "
                            f"{v.get('value', '?')}"
                        )
                _emit_memory(rec.hits)
                _emit_perception(obs)

            if obs.all_done:
                self.trace.append(rec)
                break

            goal = obs.next_pending()
            if goal is None:
                self.trace.append(rec)
                break
            rec.goal = goal

            # ── 3. Artifacts: get_bytes (conditional on attachment) ──
            attached: Optional[bytes] = None
            attach_handle = goal.attach_artifact_id or obs.attach
            # obs.attach may be a string representation of an int id
            if isinstance(attach_handle, str) and attach_handle.isdigit():
                attach_handle = int(attach_handle)
            if attach_handle and self.artifacts.exists(attach_handle):
                try:
                    attached = self.artifacts.get_bytes(attach_handle)
                except Exception:
                    attached = None
            if verbose and attached is not None:
                _emit_attach(attach_handle, len(attached))

            # ── 4. Decision: next_step(goal, hits, attached, history, tools) ──
            out = self.decision.next_step(
                goal=goal,
                hits=rec.hits,
                attached=attached,
                history=history,
                tools=tools_block,
                all_goals=obs.goals,
                query=query,
            )
            rec.decision = out
            if verbose:
                _emit_decision(out)

            # ── alt: Decision answers (plain text) ──
            # Decision only writes user-facing text once, on the
            # final synthesis goal. Record the answer, mark the
            # goal done locally, and exit the loop if no other
            # goals remain open (avoids a wasted perception pass).
            if out.is_answer:
                answer = (out.answer or "").strip()
                answers[goal.id] = answer
                self.store.record_answer_event(
                    query, goal.text, answer,
                    run_id=self.run_id, goal_id=goal.id,
                )
                goal.done = True
                self.trace.append(rec)
                if on_step:
                    on_step(rec)
                if all(g.done for g in obs.goals):
                    break
                continue

            # ── alt: Decision picks a tool ──
            assert out.tool_call is not None

            # ── Action: execute(tool_call) → descriptor, artifact_id? ──
            desc, art_id, result = self.action.execute(
                out.tool_call, self.artifacts
            )
            rec.action_descriptor = desc
            rec.artifact_id = art_id
            if verbose:
                _emit_action(desc, art_id, result)

            # ── Memory: record_outcome(tool_call, result, artifact_id) ──
            self.store.record_outcome(
                out.tool_call,
                desc,
                art_id,
                run_id=self.run_id,
                goal_id=goal.id,
                ok=result.ok,
                error=result.error,
                payload=result.payload,
            )
            if art_id:
                goal.attach_artifact_id = art_id

            # ── inner action loop for multi-item goals ──
            # If the current goal (or the original user query) specifies
            # a quantity (e.g. "top 3"), keep asking Decision for the
            # next tool call within this same iteration until the
            # required count of successful matching tool_outcomes is
            # reached. Any FINAL_ANSWER from Decision during this inner
            # loop is rejected (we must finish the multi-fetch before
            # answering).
            required = _required_count(goal.text) or _required_count(query)
            if required and result.ok and out.tool_call.name in _FETCH_TOOLS:
                inner = 0
                while inner < _INNER_MAX:
                    inner += 1
                    inner_history = self.store.by_run(self.run_id)
                    have = _count_fetches(
                        inner_history, goal.id, _FETCH_TOOLS
                    )
                    if have >= required:
                        break
                    inner_attached: Optional[bytes] = None
                    if goal.attach_artifact_id and self.artifacts.exists(
                        goal.attach_artifact_id
                    ):
                        try:
                            inner_attached = self.artifacts.get_bytes(
                                goal.attach_artifact_id
                            )
                        except Exception:
                            inner_attached = None
                    inner_out = self.decision.next_step(
                        goal=goal,
                        hits=rec.hits,
                        attached=inner_attached,
                        history=inner_history,
                        tools=tools_block,
                        all_goals=obs.goals,
                        query=query,
                    )
                    if verbose:
                        _emit_decision(inner_out)
                    # Reject premature FINAL_ANSWER on a multi-item goal.
                    if inner_out.tool_call is None:
                        break
                    if inner_out.tool_call.name not in _FETCH_TOOLS:
                        break
                    i_desc, i_aid, i_result = self.action.execute(
                        inner_out.tool_call, self.artifacts
                    )
                    if verbose:
                        _emit_action(i_desc, i_aid, i_result)
                    self.store.record_outcome(
                        inner_out.tool_call,
                        i_desc,
                        i_aid,
                        run_id=self.run_id,
                        goal_id=goal.id,
                        ok=i_result.ok,
                        error=i_result.error,
                        payload=i_result.payload,
                    )
                    if not i_result.ok:
                        # Stop the inner loop on a tool failure; the next
                        # outer iteration's perception/decision can react.
                        break

            # ── append to history, iterate ──
            # Per-goal answers are no longer produced inline; the
            # next iteration runs perception (which can mark this
            # goal done from the tool_outcome) and Decision picks
            # the next goal's tool call. A single FINAL_ANSWER is
            # produced once, on the final synthesis goal.

            prior_goals = obs.goals
            self.trace.append(rec)
            if on_step:
                on_step(rec)

        # ── assemble final answer ──
        if verbose and self.last_observation and self.last_observation.all_done:
            done = len(self.last_observation.goals)
            print(f"\n[done] all {done} goals satisfied")

        final = self._assemble_answer(answers)
        if verbose and final:
            print()
            _emit_final(final)
        return final

    # Backward-compatible alias
    ask = run

    # ──────────────────────────────────────────────── private helpers

    def _reset(self, query: str) -> None:
        self.run_id = uuid.uuid4().int
        self.last_observation = None
        self.trace = []
        self.store.add_query(query, run_id=self.run_id, source="user")

    def _assemble_answer(self, answers: dict[int, str]) -> str:
        """Return the single final answer Decision produced.

        Decision now writes user-facing text only once (on the final
        synthesis goal), so there is at most one entry in ``answers``
        worth returning. If multiple are present (legacy / fallback
        paths), return the last one in goal order — it is the most
        synthesized.
        """
        goals = self.last_observation.goals if self.last_observation else []
        final = ""
        for g in goals:
            a = answers.get(g.id)
            if a:
                final = a
        if not final and answers:
            # No observation goals (shouldn't happen) — fall back to
            # the last recorded answer by insertion order.
            final = list(answers.values())[-1]
        return final

    def history(self) -> list[MemoryItem]:
        return self.store.by_run(self.run_id)


def _truncate(text: str, n: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "\u2026"


# --------------------------------------------------------------- pretty print
_LABEL_W = 16


def _label(tag: str) -> str:
    return f"[{tag}]".ljust(_LABEL_W)


def _emit_iter_header(step: int) -> None:
    print(f"\n--- iter {step} ---")


def _emit_memory(hits: list) -> None:
    print(f"{_label('memory.read')} {len(hits)} hits")
    indent = " " * _LABEL_W
    for h in hits:
        kind = getattr(h, 'kind', None) or '?'
        content = getattr(h, 'content', None) or getattr(h, 'descriptor', '')
        content = content.strip().replace('\n', ' ')
        if len(content) > 100:
            content = content[:97] + '…'
        print(f"{indent} {kind}: \"{content}\"")


def _emit_perception(obs: Observation) -> None:
    indent = " " * _LABEL_W
    lines: list[str] = []
    for g in obs.goals:
        mark = "[done]" if g.done else "[open]"
        lines.append(f"{mark} {g.text}")
    if not lines:
        lines.append("(no goals)")
    print(f"{_label('perception')} {lines[0]}")
    for ln in lines[1:]:
        print(f"{indent} {ln}")
    if obs.attach:
        print(f"{indent}   attach={obs.attach}")


def _emit_attach(handle: str, size: int) -> None:
    print(f"{_label('attach')} {handle} ({size} bytes)")


def _emit_decision(out: DecisionOutput) -> None:
    if out.tool_call is not None:
        import json as _json
        args = _json.dumps(out.tool_call.arguments, default=str, ensure_ascii=False)
        if len(args) > 200:
            args = args[:197] + "..."
        print(f"{_label('decision')} TOOL_CALL: {out.tool_call.name}({args})")
    else:
        ans = (out.answer or "").strip().replace("\n", " ")
        if len(ans) > 140:
            ans = ans[:137] + "..."
        print(f"{_label('decision')} ANSWER: {ans}")


def _emit_action(descriptor: str, art_id: Optional[str], result) -> None:
    if not result.ok:
        print(f"{_label('action')} ERROR: {result.error}")
        return
    if art_id and result.payload:
        size = len(result.payload.encode("utf-8"))
        preview = result.payload.replace("\n", " ").strip()
        if len(preview) > 60:
            preview = preview[:60] + "..."
        print(
            f"{_label('action')} -> [artifact {art_id}, {size} bytes]"
            f" preview: {preview}"
        )
    else:
        short = descriptor.split("->", 1)[-1].strip()
        if len(short) > 140:
            short = short[:137] + "..."
        print(f"{_label('action')} -> {short}")


def _emit_final(text: str) -> None:
    indent = "       "
    lines = text.splitlines() or [text]
    print(f"FINAL: {lines[0]}")
    for ln in lines[1:]:
        print(f"{indent}{ln}")


def _emit_memory_remember(answer: str) -> None:
    short = answer.strip().replace("\n", " ")
    if len(short) > 100:
        short = short[:97] + "\u2026"
    print(f"{_label('memory.remember')} fact: \"{short}\"")


def _silence_http_logs() -> None:
    """Mute chatty HTTP client logs (httpx/httpcore/urllib3) in the REPL."""
    for name in (
        "httpx",
        "httpcore",
        "httpcore.http11",
        "httpcore.connection",
        "urllib3",
        "openai",
        "anthropic",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    # llm_gatewayV3 client occasionally prints raw debug lines via env flag.
    os.environ.setdefault("LLM_GATEWAY_DEBUG", "0")


def repl() -> None:
    _silence_http_logs()
    agent = Agent6()
    print(
        f"agents6 ready (run_id={agent.run_id}, store={agent.store.path}, "
        f"items={len(agent.store)}, tools={agent.action.list_tools() or '[]'}). "
        f"Type 'exit' to quit, 'history' to dump memory, 'goals' to show "
        f"the last observation's goals."
    )

    def _show_goals(obs: Optional[Observation]) -> None:
        if not obs or not obs.goals:
            print("(no goals)")
            return
        for i, g in enumerate(obs.goals, 1):
            mark = "[done]" if g.done else "[open]"
            print(f"  {mark} {i}. {g.text}")

    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            break
        if q.lower() == "history":
            for it in agent.store.all():
                print(f"  [{it.kind}] ({it.run_id}) {it.descriptor}")
            continue
        if q.lower() == "goals":
            _show_goals(agent.last_observation)
            continue
        try:
            agent.run(q, verbose=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"!! error: {e}", file=sys.stderr)


if __name__ == "__main__":
    repl()
