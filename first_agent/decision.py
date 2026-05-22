"""Decision layer for agents6.

The decision layer executes one `Goal` at a time. For each goal it asks
the LLM whether a tool from `mcp_server` is needed and, if so, delegates
the actual tool invocation to the `Action` layer (`action.py`) and uses
the returned result to produce a short text answer.

Boundary
--------
* Decision does NOT call `mcp_server` directly. Tool execution is the
  sole responsibility of `action.Action`.
* `llm_gatewayV3.client.LLM` is allowed (it is the LLM transport, not a
  tool source).
"""

from __future__ import annotations

import builtins
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from action import Action
from llm_gatewayV3.client import LLM

from perception import QueryState  # noqa: F401  (re-exported for typing convenience)
from schemas import DecisionOutput, Goal, Hit, MemoryItem, ToolCall


# --------------------------------------------------------------------- guard
# decision.py must not import `mcp_server` (that lives in action.py) nor
# any tool/transport library directly.
_TOOL_BLOCKLIST_ROOTS = {
    "mcp_server",
    "httpx", "requests", "urllib3", "selenium", "playwright",
    "crawl4ai", "tavily", "ddgs", "duckduckgo_search", "bs4",
    "subprocess", "openai", "anthropic", "google",
}
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller_file = (globals or {}).get("__file__", "")
    if caller_file == __file__:
        root = name.split(".", 1)[0]
        if root in _TOOL_BLOCKLIST_ROOTS:
            raise RuntimeError(
                f"decision.py is not allowed to import '{name}'. "
                "Tool execution must go through `action.Action`."
            )
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guarded_import


DEFAULT_SYSTEM_PROMPT = (
    "You are a careful sub-agent inside a multi-turn agent loop. You "
    "complete exactly ONE goal per turn. The orchestrator gives you the "
    "current goal, the broader plan, memory hits, recent run history, "
    "and (sometimes) an attached artifact preview. On the next turn "
    "you will see the result of any tool you call, so plan ONE step at "
    "a time.\n"
    "\n"
    "# How to think (always do this before answering)\n"
    "Reason step-by-step in plain prose, then act. Do NOT skip the "
    "REASONING block.\n"
    "  1. RESTATE: one short sentence restating the current goal.\n"
    "  2. REASONING_TYPE: tag the dominant kind(s) of reasoning needed.\n"
    "     Choose from: LOOKUP (use memory/artifact/history), RETRIEVE "
    "(need an external tool to fetch new info), ARITHMETIC, LOGIC, "
    "PLANNING, WRITE (produce/store/save output).\n"
    "  3. PLAN: 2-5 short bullets describing the minimal next action.\n"
    "  4. SELF_CHECK: verify the plan. Are the arguments well-formed and "
    "grounded in known facts (no invented filenames, dates, urls)? Has "
    "this exact tool+arguments been tried for this goal already (see "
    "history)? If yes, pick a different action or finalize the answer.\n"
    "  5. DECIDE: either call exactly one tool OR write the final "
    "answer.\n"
    "\n"
    "# Output format (STRICT, machine-parsed)\n"
    "Reply in this exact shape. Do NOT use the characters '{' or '}' "
    "anywhere except inside the final TOOL_CALL JSON. Do NOT use '[' or "
    "']' except inside TOOL_CALL arguments.\n"
    "\n"
    "REASONING:\n"
    "- restate: <one short sentence>\n"
    "- reasoning_type: <comma-separated tags from the set above>\n"
    "- plan: <2-5 short bullets, separated by '; '>\n"
    "- self_check: <one or two sentences>\n"
    "\n"
    "Then EXACTLY ONE of the two lines below, on its own line, and "
    "NOTHING after it:\n"
    "\n"
    "TOOL_CALL: {\"tool\": \"<tool_name>\", \"arguments\": { ... }}\n"
    "\n"
    "FINAL_ANSWER: <one short paragraph answering the current goal; "
    "do NOT restate the question or goal — give only the answer>\n"
    "\n"
    "# Rules\n"
    "- If a fact hit in memory directly answers the current goal, use "
    "FINAL_ANSWER with that information. Facts are pre-extracted from "
    "the user's own statements and are authoritative.\n"
    "- Otherwise, emit a TOOL_CALL when a listed MCP tool can retrieve "
    "or verify the information needed for the goal (fetch, compute, "
    "persist, read). Use FINAL_ANSWER ONLY when the run history already "
    "contains a tool_outcome for this goal, or when NO listed tool is "
    "applicable to the goal.\n"
    "- MULTI-ITEM CONTINUATION: when the current goal specifies a "
    "quantity (e.g. 'top 3 results', 'each of the N urls', 'read all "
    "5 pages'), you MUST keep emitting TOOL_CALLs until history shows "
    "AT LEAST that many successful tool_outcomes of the appropriate "
    "fetch/read tool for this goal. Do NOT emit FINAL_ANSWER while the "
    "count is short. On each turn, pick the NEXT un-fetched item "
    "(e.g. the next URL from the search-results artifact that does "
    "not yet appear in history) and call the tool on it. Only emit "
    "FINAL_ANSWER for this goal once the required count is reached.\n"
    "- NEVER reissue web_search if HISTORY already contains a "
    "successful web_search tool_outcome for this query. Use the URLs "
    "from the existing search-results artifact and call fetch_url on "
    "those URLs instead. A single web_search with max_results=N is "
    "sufficient — do not call web_search a second time to 'get more "
    "results'.\n"
    "- When the goal or user query contains an explicit URL (http:// or "
    "https://), you MUST call `fetch_url` with that URL to retrieve its "
    "content before answering. Do NOT answer from general knowledge "
    "when a specific URL was provided — the user wants data from that "
    "page.\n"
    "- The TOOL_CALL JSON must be valid JSON on a single line and the "
    "tool name and arguments MUST match a tool listed in the tools "
    "block. Never invent tool names or argument keys.\n"
    "- Never re-issue an identical tool call already shown in history "
    "for this goal. If the previous tool failed or returned nothing "
    "useful, either choose a different tool/arguments or write a "
    "FINAL_ANSWER explaining the limitation.\n"
    "- Error handling / fallback: if the goal is ambiguous, the tools "
    "block is empty, a needed tool is unavailable, or you are uncertain, "
    "write a FINAL_ANSWER that states the uncertainty and gives the best "
    "partial answer grounded in available context. Do NOT hallucinate.\n"
    "- Do not add any text after the TOOL_CALL or FINAL_ANSWER line.\n"
    "- FINAL_ANSWER must NOT restate the user's question or the goal. "
    "Do not begin with phrases like 'You asked...', 'The question is...', "
    "'Regarding your request...', or echo the goal back. Give only the "
    "answer itself, directly and concisely.\n"
    "\n"
    "# Example A - tool use\n"
    "REASONING:\n"
    "- restate: Save a note that mum's birthday is on 17 May 2026.\n"
    "- reasoning_type: WRITE\n"
    "- plan: call create_file with a stable filename (paths are already "
    "rooted at the sandbox; do NOT prefix with 'sandbox/').\n"
    "- self_check: filename is grounded in the goal; no prior identical "
    "call in history.\n"
    "TOOL_CALL: {\"tool\": \"create_file\", \"arguments\": {\"path\": "
    "\"mom_birthday.txt\", \"content\": \"Mum's birthday: "
    "2026-05-17\"}}\n"
    "\n"
    "# Example B - URL retrieval\n"
    "REASONING:\n"
    "- restate: Fetch https://example.com/page and extract the key points.\n"
    "- reasoning_type: RETRIEVE\n"
    "- plan: the goal provides an explicit URL; call fetch_url to "
    "retrieve the page content.\n"
    "- self_check: URL is provided verbatim in the goal; fetch_url is "
    "listed in the tools block; no prior identical call in history.\n"
    "TOOL_CALL: {\"tool\": \"fetch_url\", \"arguments\": {\"url\": "
    "\"https://example.com/page\"}}\n"
    "\n"
    "# Example C - answer from a known fact\n"
    "REASONING:\n"
    "- restate: Tell the user when mum's birthday is.\n"
    "- reasoning_type: LOOKUP\n"
    "- plan: a fact hit states mom's birthday is 2026-05-15; this was "
    "extracted from the user's own statement and is authoritative.\n"
    "- self_check: the fact directly answers the goal; no tool needed.\n"
    "FINAL_ANSWER: 15 May 2026.\n"
    "\n"
    "# Example D - uncertain / fallback\n"
    "REASONING:\n"
    "- restate: Fetch yesterday's stock price for ACME.\n"
    "- reasoning_type: RETRIEVE\n"
    "- plan: no listed tool can fetch market data; cannot proceed.\n"
    "- self_check: tools block contains no market-data tool; do not "
    "fabricate a price.\n"
    "FINAL_ANSWER: No available tool can fetch market prices; please "
    "add a market-data tool or supply the value."
)


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_tool_call(text: str) -> Optional[dict]:
    """Try to find a `{"tool": ..., "arguments": ...}` JSON object in the text."""
    if not text:
        return None
    # Look at the first line first for a strict match.
    candidates: list[str] = []
    first = text.strip().splitlines()[0].strip()
    if first.startswith("{"):
        candidates.append(first)
    m = _JSON_OBJ_RE.search(text)
    if m:
        candidates.append(m.group(0))
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            args = data.get("arguments") or {}
            if isinstance(args, dict):
                return {"tool": data["tool"], "arguments": args}
    return None


def _strip_tool_call(text: str) -> str:
    """Remove the first ``{...}`` JSON object from ``text`` and return the rest."""
    if not text:
        return ""
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return text.strip()
    return (text[: m.start()] + text[m.end() :]).strip()


_FINAL_ANSWER_RE = re.compile(
    r"FINAL[_ ]ANSWER\s*:\s*(.+)\Z", re.IGNORECASE | re.DOTALL
)
_REASONING_BLOCK_RE = re.compile(
    r"^REASONING\s*:.*?(?=\n\S|\Z)", re.IGNORECASE | re.DOTALL
)


def _extract_final_answer(text: str) -> str:
    """Extract the user-facing answer from a structured response.

    The decision system prompt requires answers to be emitted as
    ``FINAL_ANSWER: <text>`` after a ``REASONING:`` block. This helper
    returns just the answer portion. If the model did not follow the
    format, we fall back to stripping a leading ``REASONING:`` block,
    and if all else fails we return the original text.
    """
    if not text:
        return ""
    m = _FINAL_ANSWER_RE.search(text)
    if m:
        return m.group(1).strip()
    stripped = _REASONING_BLOCK_RE.sub("", text, count=1).strip()
    return stripped or text.strip()


@dataclass
class GoalResult:
    goal_id: int
    goal_text: str
    answer: str
    tool_call: Optional[dict] = None
    tool_result: Any = None
    error: Optional[str] = None
    raw_responses: list[dict] = field(default_factory=list)


class Decision:
    """Execute one `Goal` at a time using LLM + MCP tools (via `Action`)."""

    def __init__(
        self,
        *,
        llm: Optional[LLM] = None,
        action: Optional[Action] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        auto_route: Optional[str] = "decision",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.llm = llm or LLM()
        self.action = action or Action()
        self.provider = provider
        self.model = model
        self.auto_route = auto_route
        self.system_prompt = system_prompt

    # ------------------------------------------------------------- helpers

    def _tools_block(self) -> str:
        return self.action.tools_block()

    def build_system(
        self,
        goal: Goal,
        *,
        query: str = "",
        memory_block: str = "",
    ) -> str:
        parts = [self.system_prompt, self._tools_block()]
        if query:
            parts.append(f"User query: {query}")
        parts.append(f"Current goal: {goal.text}")
        if memory_block:
            parts.append(memory_block)
        return "\n\n".join(parts)

    # ---------------------------------------------------------------- core

    def next_step(
        self,
        goal: Goal,
        hits: list[Hit],
        attached: list[tuple] | bytes | None,
        history: list[MemoryItem] | list[dict],
        tools: str,
        all_goals: Optional[list[Goal]] = None,
        query: str = "",
    ) -> DecisionOutput:
        """Single LLM step: return either an ``answer`` or a ``tool_call``.

        Does NOT execute the tool — the orchestrator dispatches Action
        and records the outcome, then calls back into ``next_step`` with
        updated ``history`` on the next iteration.

        ``attached`` may be:
        - a list of ``(artifact_id, bytes)`` tuples (new orchestrator),
        - raw ``bytes`` (legacy), or
        - ``None``.

        ``history`` may be:
        - a ``list[dict]`` with keys like ``kind``, ``tool``, ``goal_id``
          (new orchestrator), or
        - a ``list[MemoryItem]`` (legacy).

        ``all_goals`` is the full plan from perception; it is rendered as
        a checklist so the LLM picks actions consistent with the broader
        plan (e.g. avoids re-doing a goal already marked done, and can
        anticipate which artifacts the next goal will need).
        """
        system_parts = [self.system_prompt, tools or self._tools_block()]
        if query:
            system_parts.append(f"Original user query: {query}")
        if all_goals:
            plan_lines = ["Plan (checklist of all goals for this query):"]
            for i, g in enumerate(all_goals, 1):
                if g.done:
                    mark = "[x]"
                elif g.id == goal.id:
                    mark = "[>]"  # current
                else:
                    mark = "[ ]"
                aid = f"  (artifact={g.attach_artifact_id})" if g.attach_artifact_id else ""
                plan_lines.append(f"  {mark} {i}. {g.text}{aid}")
            plan_lines.append(
                "Work ONLY on the [>] goal this turn. Use [x] goals' "
                "artifacts/results as context; do not redo them."
            )
            system_parts.append("\n".join(plan_lines))
        if hits:
            fact_hits = [h for h in hits if getattr(h, 'kind', None) == 'fact']
            other_hits = [h for h in hits if getattr(h, 'kind', None) != 'fact']
            lines: list[str] = []
            if fact_hits:
                lines.append(
                    "Known facts from previous queries:"
                )
                for h in fact_hits:
                    body = (h.content or h.descriptor).strip().replace("\n", " ")
                    if len(body) > 1200:
                        body = body[:1200] + "\u2026"
                    lines.append(f'  fact: {body}')
            if other_hits:
                lines.append("Other memory hits:")
                for h in other_hits:
                    aid = h.artifact_id or "-"
                    lines.append(f"- {h.handle} | {h.descriptor} | artifact={aid}")
                    if h.content:
                        body = h.content
                        if len(body) > 1200:
                            body = body[:1200] + "\u2026"
                        lines.append(f"    content: {body}")
            system_parts.append("\n".join(lines))
        if history:
            # History may be list[dict] (new) or list[MemoryItem] (legacy).
            recent = history[-8:]
            lines = ["Recent run history:"]
            for it in recent:
                if isinstance(it, dict):
                    # New-style dict history entry
                    kind = it.get("kind", "?")
                    if kind == "action":
                        head = (
                            f"- [action] {it.get('tool')}"
                            f"({json.dumps(it.get('arguments', {}), default=str)[:160]})"
                        )
                        desc = it.get("result_descriptor", "")
                        if desc:
                            lines.append(head)
                            lines.append(f"    result: {desc}")
                        else:
                            lines.append(head)
                    elif kind == "answer":
                        text = it.get("text", "")
                        lines.append(f"- [answer] goal_id={it.get('goal_id')} {text[:200]}")
                    else:
                        lines.append(f"- [{kind}] {json.dumps(it, default=str)[:200]}")
                else:
                    # Legacy MemoryItem path
                    v = it.value if isinstance(it.value, dict) else {}
                    if it.kind == "tool_outcome":
                        head = (
                            f"- [tool_outcome] {v.get('tool')}"
                            f"({json.dumps(v.get('arguments', {}), default=str)[:160]})"
                            f" ok={v.get('ok')}"
                        )
                        if v.get("error"):
                            head += f" error={v.get('error')}"
                        lines.append(head)
                        body = v.get("result") or v.get("result_preview")
                        if body:
                            body_s = str(body)
                            if len(body_s) > 1200:
                                body_s = body_s[:1200] + "\u2026"
                            lines.append(f"    result: {body_s}")
                    else:
                        lines.append(f"- [{it.kind}] {it.descriptor}")
            system_parts.append("\n".join(lines))
        # Anti-repeat: detect last tool call for this goal in history and
        # forbid the LLM from re-issuing the identical call.
        last_call: Optional[tuple[str, str]] = None
        for it in reversed(history or []):
            if isinstance(it, dict):
                if it.get("kind") == "action" and it.get("goal_id") == goal.id:
                    last_call = (
                        str(it.get("tool")),
                        json.dumps(it.get("arguments", {}), default=str, sort_keys=True),
                    )
                    break
            else:
                if it.kind == "tool_outcome" and it.goal_id == goal.id:
                    v = it.value if isinstance(it.value, dict) else {}
                    last_call = (
                        str(v.get("tool")),
                        json.dumps(v.get("arguments", {}), default=str, sort_keys=True),
                    )
                    break
        # Resolve attached bytes from either list[tuple] or raw bytes.
        attached_bytes: Optional[bytes] = None
        if isinstance(attached, list) and attached:
            # New-style: list of (artifact_id, bytes) tuples
            attached_bytes = attached[0][1]
        elif isinstance(attached, (bytes, bytearray)):
            # Legacy raw bytes
            attached_bytes = bytes(attached)
        if attached_bytes is not None:
            preview = attached_bytes[:16384].decode("utf-8", errors="replace")
            system_parts.append(
                "Attached artifact preview (first 16 KB) — USE THIS to answer; "
                "do NOT re-fetch:\n" + preview
            )
        if last_call is not None:
            system_parts.append(
                f"You already called `{last_call[0]}` with arguments "
                f"{last_call[1]} for this goal. Do NOT issue an identical "
                "tool call again. Either call a different tool or write "
                "the final answer using the tool result above."
            )
        system_parts.append(f"Current goal: {goal.text}")
        system = "\n\n".join(system_parts)

        resp = self.llm.chat(
            prompt=goal.text,
            system=system,
            provider=self.provider,
            model=self.model,
            auto_route=self.auto_route,
        )
        text = (resp.get("text") or "").strip()
        call = _extract_tool_call(text)
        if call is not None:
            new_sig = (
                call["tool"],
                json.dumps(call["arguments"], default=str, sort_keys=True),
            )
            if last_call is not None and new_sig == last_call:
                # Loop guard: model re-issued the same call. Force an answer
                # by stripping the JSON and returning the surrounding text,
                # or a fallback message.
                stripped = _extract_final_answer(_strip_tool_call(text))
                return DecisionOutput(
                    answer=stripped
                    or "(unable to make further progress; tool returned no new info)"
                )
            return DecisionOutput(
                tool_call=ToolCall(name=call["tool"], arguments=call["arguments"])
            )
        return DecisionOutput(answer=_extract_final_answer(text))

    # ----------------------------------------------- legacy execute_goal

    def execute_goal(
        self,
        goal: Goal,
        *,
        query: str = "",
        memory_block: str = "",
    ) -> GoalResult:
        """Run one goal end-to-end. Returns a `GoalResult`.

        Note: `Decision` does NOT receive or mutate `QueryState`. The
        orchestrator is responsible for marking the goal done via
        `Perception.mark_done` (and attaching any artifact id from the
        returned `GoalResult` itself) so perception stays free of LLM
        results.
        """
        system = self.build_system(goal, query=query, memory_block=memory_block)
        raw_responses: list[dict] = []

        first = self.llm.chat(
            prompt=goal.text,
            system=system,
            provider=self.provider,
            model=self.model,
            auto_route=self.auto_route,
        )
        raw_responses.append(first)
        text = (first.get("text") or "").strip()

        tool_call = _extract_tool_call(text)
        if tool_call is None:
            # Plain LLM answer.
            return GoalResult(
                goal_id=goal.id,
                goal_text=goal.text,
                answer=_extract_final_answer(text),
                raw_responses=raw_responses,
            )

        name = tool_call["tool"]
        args = tool_call["arguments"]
        action_result = self.action.call(name, args)
        if not action_result.ok:
            return GoalResult(
                goal_id=goal.id,
                goal_text=goal.text,
                answer=_extract_final_answer(text),
                tool_call=tool_call,
                error=action_result.error,
                raw_responses=raw_responses,
            )
        tool_result = action_result.result

        # Second LLM pass: turn the tool result into a final answer for this goal.
        followup_system = (
            f"{self.system_prompt}\n\nYou already called the tool "
            f"`{name}` with arguments {json.dumps(args)} and got the result "
            f"below. Now write a one-paragraph answer for the goal."
        )
        followup_prompt = (
            f"Goal: {goal.text}\n\nTool result (JSON):\n"
            + json.dumps(tool_result, default=str, indent=2)[:4000]
        )
        second = self.llm.chat(
            prompt=followup_prompt,
            system=followup_system,
            provider=self.provider,
            model=self.model,
            auto_route=self.auto_route,
        )
        raw_responses.append(second)
        final = _extract_final_answer((second.get("text") or "").strip())

        return GoalResult(
            goal_id=goal.id,
            goal_text=goal.text,
            answer=final or _extract_final_answer(text),
            tool_call=tool_call,
            tool_result=tool_result,
            raw_responses=raw_responses,
        )


__all__ = ["Decision", "GoalResult", "DEFAULT_SYSTEM_PROMPT"]
