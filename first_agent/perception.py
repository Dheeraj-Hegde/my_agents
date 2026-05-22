"""Perception layer for agents6.

Perception's responsibility is to convert a raw user query, memory hits,
run history, and prior goals into a structured `Observation` containing
an updated goal list with done/open status.  It uses the LLM to reason
about whether goals are satisfied and to decompose new queries.

Boundary
--------
Perception is read-only and side-effect-free with respect to the
outside world:

* It MUST NOT call MCP tools.
* It MUST NOT read or mutate the memory store.
* It MAY call the LLM gateway for goal decomposition AND for evaluating
  goal completion from history / memory hits.  It must not use the LLM
  to answer the query or summarize memory.

A small import-time guard below enforces the MCP boundary: importing
any MCP client module from inside this file raises ``RuntimeError``.
Tool invocation, memory recall, and answering are the responsibility
of the decision / action layer, not perception.
"""

from __future__ import annotations

import builtins
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from schemas import Goal, Hit, MemoryItem, Observation


# --------------------------------------------------------------------- guard
_FORBIDDEN_MCP_PREFIXES = ("mcp", "mcp_server")
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller_file = (globals or {}).get("__file__", "")
    if caller_file == __file__:
        root = name.split(".", 1)[0]
        if root in _FORBIDDEN_MCP_PREFIXES:
            raise RuntimeError(
                f"perception.py is not allowed to import '{name}'. "
                "Perception is a read-only layer and must not call MCP tools."
            )
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guarded_import


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "for", "and", "or", "but", "if",
    "what", "who", "when", "where", "why", "how", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they", "me", "my", "your",
    "this", "that", "these", "those", "with", "as", "by", "from",
    "can", "could", "would", "should", "will", "shall", "may", "might",
}


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def _content_tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower())
            if t not in _STOPWORDS and len(t) > 2]


def _run_id_from(history: list) -> int:
    for it in reversed(history or []):
        rid = getattr(it, "run_id", None)
        if rid is not None:
            return int(rid)
    return 0


@dataclass
class QueryState:
    """Output of `Perception.perceive`: the raw query and derived goals."""

    raw_query: str
    run_id: int
    id: int = field(default_factory=lambda: uuid.uuid4().int)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    normalized: str = ""
    tokens: list[str] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    goal_id: Optional[int] = None
    def has_goals(self) -> bool:
        return bool(self.goals)


class Perception:
    """Convert a user query into a `QueryState` containing a list of goals."""

    def __init__(
        self,
        *,
        llm: Optional[object] = None,
        decompose: bool = True,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        auto_route: Optional[str] = "perception",
    ) -> None:
        self.llm = llm  # lazy: only constructed when decompose is used
        self.decompose = decompose
        self.provider = provider
        self.model = model
        self.auto_route = auto_route
        self._decomp_cache: dict[str, list[str]] = {}

    # -------------------------------------------------------------- pipeline

    def perceive(
        self,
        query: str,
        *,
        run_id: int,
        goal_id: Optional[int] = None,
    ) -> QueryState:
        state = QueryState(raw_query=query, run_id=run_id, goal_id=goal_id)
        state.normalized = _normalize(query)
        state.tokens = _content_tokens(query)
        state.goals = self._derive_goals(query)
        return state

    # ------------------------------------------------- agent-loop observe

    _OBSERVE_SYSTEM = (
        "You are the perception module of an autonomous agent loop. "
        "Your job is to look at the user query, memory hits, run "
        "history, and the current goal list, then output an updated "
        "JSON goal list with accurate done/open status.\n"
        "\n"
        "# Input you receive\n"
        "- QUERY: the original user request.\n"
        "- MEMORY HITS: facts and prior results recalled from memory.\n"
        "- HISTORY: actions and answers from earlier iterations of "
        "THIS run.\n"
        "- PRIOR GOALS: the goal list from the previous iteration "
        "(may be empty on the first iteration).\n"
        "\n"
        "# How to think (always do this before outputting JSON)\n"
        "Reason step-by-step in a REASONING block, then produce the "
        "JSON. Do NOT skip the REASONING block.\n"
        "  1. REASONING_TYPE: tag the kind of perception needed. "
        "Choose from: DECOMPOSE (new query, split into goals), "
        "EVALUATE (check if existing goals are done from history), "
        "ATTACH (find a relevant artifact for the next goal).\n"
        "  2. EVIDENCE: for each goal, cite the specific history "
        "entry (goal_id + kind) that proves it is done, or state "
        "'no evidence yet' if none exists.\n"
        "  3. SELF_CHECK: verify your assessment. Did you invent any "
        "goals the user didn't ask for? Did you mark a goal done "
        "without concrete evidence? Did you preserve URLs/paths "
        "verbatim? Are the goal ids unchanged from PRIOR GOALS? For "
        "any goal with an explicit quantity (top N, each, all N), "
        "did you actually COUNT the matching tool_outcomes in HISTORY "
        "and confirm count >= N before marking done?\n"
        "\n"
        "# Your task\n"
        "1. If PRIOR GOALS is empty, decompose the QUERY into goals "
        "(same rules as goal decomposition below).\n"
        "2. If PRIOR GOALS is provided, keep the SAME goals and ids. "
        "Do NOT add, remove, or rename goals.\n"
        "3. For each goal, decide whether it is DONE:\n"
        "   - A goal is done if the HISTORY contains an answer entry "
        "for that goal_id, OR a tool_outcome whose result clearly "
        "satisfies the goal.\n"
        "   - A goal is NOT done if no history entry addresses it yet.\n"
        "   - COUNT RULE for multi-item goals: when the goal text "
        "specifies a quantity (e.g. 'top 3 results', 'read 5 pages', "
        "'fetch each of the N urls', 'for all results'), the goal is "
        "done ONLY when HISTORY contains AT LEAST that many successful "
        "tool_outcome entries of the appropriate fetch/read tool under "
        "that goal_id. A single fetch_url tool_outcome does NOT "
        "satisfy a 'fetch top 3' goal — count them. An [answer] entry "
        "alone does NOT satisfy a multi-item goal until the required "
        "number of tool_outcomes are also present; if the count is "
        "short, keep the goal OPEN so the agent issues more "
        "tool calls on the next iteration.\n"
        "4. Identify which artifact (if any) should be attached to the "
        "next unfinished goal. Use the artifact_id from a memory hit "
        "or a prior action result that is relevant.\n"
        "\n"
        "# Goal decomposition rules (when PRIOR GOALS is empty)\n"
        "- Each goal describes WHAT the user wants, not HOW to do it.\n"
        "- IMPORTANT: when the user provides an explicit URL, file "
        "path, or other concrete locator, you MUST preserve it "
        "verbatim in the goal text.\n"
        "- Only split into multiple goals when the user explicitly "
        "asks for multiple INDEPENDENT deliverables (e.g. 'save X "
        "AND create Y AND summarise Z').\n"
        "- ALWAYS split a query into two goals when it requires both "
        "(a) fetching/reading an external resource (URL, file, API), "
        "and (b) extracting, summarising, or answering based on that "
        "fetched content. Goal 1 = the fetch (preserve the URL/path "
        "verbatim); Goal 2 = the answer derived from the fetched "
        "content.\n"
        "- ALWAYS split a 'search-then-read' query into SEPARATE "
        "goals: one goal for the web_search (with NO quantity in its "
        "text, e.g. 'Search the web for X'), and a SECOND goal for "
        "reading the result URLs that carries the quantity verbatim "
        "(e.g. 'Read the top 3 result URLs from the search'). Do NOT "
        "bundle the search and the per-result fetches into a single "
        "goal — they use different tools and have different done "
        "conditions (search is done after 1 web_search; the read "
        "goal is done only after N fetch_url tool_outcomes).\n"
        "- ONLY create a separate 'read the result URLs' goal when "
        "the user EXPLICITLY asks to read, open, follow, fetch, "
        "summarise, or extract details FROM the individual result "
        "pages (e.g. 'read the top 3 results', 'open each link', "
        "'summarise each article', 'visit and compare the pages'). "
        "If the user only asks to 'find N items', 'list N options', "
        "'get N suggestions', 'recommend N places', etc., a SINGLE "
        "web_search with max_results=N is enough — do NOT add a "
        "fetch_url goal. Snippets/titles from web_search already "
        "answer those queries.\n"
        "- A bare quantity in the query (e.g. 'find 3 things', "
        "'suggest 5 restaurants', 'show me 4 options') is NOT a "
        "trigger for the read-URLs goal. The trigger is an explicit "
        "verb of reading/opening/fetching applied to the results.\n"
        "- Never exceed 5 goals. Keep each goal under 140 chars.\n"
        "\n"
        "# Output format (STRICT, machine-parsed)\n"
        "First write the REASONING block, then output the JSON.\n"
        "\n"
        "REASONING:\n"
        "- reasoning_type: <comma-separated tags from the set above>\n"
        "- evidence: <per-goal citation or 'no evidence yet'>\n"
        "- self_check: <one or two sentences>\n"
        "\n"
        "Then output ONLY a single JSON object with these fields:\n"
        "{\n"
        "  \"goals\": [\n"
        "    {\"id\": <int>, \"text\": \"<goal text>\", \"done\": "
        "<true|false>},\n"
        "    ...\n"
        "  ],\n"
        "  \"attach\": \"<artifact_id or null>\"\n"
        "}\n"
        "\n"
        "# Rules\n"
        "- Do NOT answer the query. Only evaluate goal status.\n"
        "- Do NOT invent goals the user did not ask for.\n"
        "- When PRIOR GOALS is provided, output the SAME goals with "
        "the SAME ids and text. Only update the 'done' field.\n"
        "- Mark a goal done ONLY when you see concrete evidence in "
        "HISTORY (an answer or successful tool result for that goal).\n"
        "- Do NOT hallucinate evidence. If HISTORY is empty, all "
        "goals are not done.\n"
        "- Error handling: if the query is empty or unintelligible, "
        "output a single goal: \"Clarify the user's request\". If "
        "you are uncertain whether a goal is done, mark it as NOT "
        "done — the agent will re-evaluate on the next iteration.\n"
        "\n"
        "# Examples\n"
        "\n"
        "## Example A — first iteration, simple query\n"
        "REASONING:\n"
        "- reasoning_type: DECOMPOSE\n"
        "- evidence: no history, first iteration.\n"
        "- self_check: single simple query; 1 goal is correct; no "
        "URLs to preserve.\n"
        "{\"goals\": [{\"id\": 1, \"text\": \"Greet the user\", "
        "\"done\": false}], \"attach\": null}\n"
        "\n"
        "## Example B — second iteration, goal satisfied\n"
        "REASONING:\n"
        "- reasoning_type: EVALUATE\n"
        "- evidence: goal_id=1 has an [answer] entry in HISTORY "
        "with text 'Hello!'.\n"
        "- self_check: answer entry exists for goal 1; marking done. "
        "Same id and text preserved.\n"
        "{\"goals\": [{\"id\": 1, \"text\": \"Greet the user\", "
        "\"done\": true}], \"attach\": null}\n"
        "\n"
        "## Example C — multiple independent deliverables\n"
        "REASONING:\n"
        "- reasoning_type: DECOMPOSE\n"
        "- evidence: no history, first iteration.\n"
        "- self_check: two distinct outputs requested; 2 goals; "
        "no URLs.\n"
        "{\"goals\": [{\"id\": 1, \"text\": \"Save mum's birthday "
        "as a note for 2026-05-17\", \"done\": false}, {\"id\": 2, "
        "\"text\": \"Create a reminder note for 2026-05-01\", "
        "\"done\": false}], \"attach\": null}\n"
        "\n"
        "## Example D — fetch + answer (always 2 goals)\n"
        "Query: 'Fetch https://example.com/page and tell me the "
        "author and publish date.'\n"
        "REASONING:\n"
        "- reasoning_type: DECOMPOSE\n"
        "- evidence: no history, first iteration.\n"
        "- self_check: external fetch + derived answer => split into "
        "2 goals; URL preserved verbatim in goal 1.\n"
        "{\"goals\": [{\"id\": 1, \"text\": \"Fetch "
        "https://example.com/page\", \"done\": false}, {\"id\": 2, "
        "\"text\": \"Identify the author and publish date from the "
        "fetched page\", \"done\": false}], \"attach\": null}\n"
        "\n"
        "## Example E — multi-item goal, count not yet satisfied\n"
        "Query: 'Search for X and read the top 3 results.'\n"
        "PRIOR GOALS: id=1 'Search the web for X' [done], id=2 "
        "'Read the top 3 result URLs from the search' [open].\n"
        "HISTORY shows: 1 successful web_search for goal 1, and only "
        "1 successful fetch_url tool_outcome under goal_id=2.\n"
        "REASONING:\n"
        "- reasoning_type: EVALUATE\n"
        "- evidence: goal_id=2 has 1 fetch_url tool_outcome but the "
        "goal requires 3; count 1 < 3 so keep open.\n"
        "- self_check: COUNT RULE applies (top 3); only 1 of 3 "
        "fetches done; do NOT mark done — agent needs to fetch 2 more.\n"
        "{\"goals\": [{\"id\": 1, \"text\": \"Search the web for X\", "
        "\"done\": true}, {\"id\": 2, \"text\": \"Read the top 3 "
        "result URLs from the search\", \"done\": false}], "
        "\"attach\": null}\n"
    )

    def observe(
        self,
        query: str,
        hits: list[Hit],
        history: list,
        prior_goals: list[Goal],
        run_id: Optional[int | str] = None,
    ) -> Observation:
        """Produce an :class:`Observation` for the agent6 loop using the LLM.

        The LLM evaluates the query, memory hits, run history, and prior
        goals to produce an updated goal list with done/open status.
        Falls back to rule-based logic if the LLM is unavailable.
        """
        # --- Try LLM-based observation ---
        llm_result = self._llm_observe(query, hits, history, prior_goals)
        if llm_result is not None:
            return llm_result

        # --- Fallback: rule-based logic ---
        return self._rule_based_observe(query, hits, history, prior_goals)

    def _llm_observe(
        self,
        query: str,
        hits: list[Hit],
        history: list,
        prior_goals: list[Goal],
    ) -> Optional[Observation]:
        """Ask the LLM to evaluate goals and their done status."""
        llm = self.llm
        if llm is None:
            try:
                from llm_gatewayV3.client import LLM
                llm = LLM()
                self.llm = llm
            except Exception:
                return None

        # Build the prompt with all context
        prompt_parts: list[str] = [f"QUERY: {query}"]

        if hits:
            hit_lines = ["MEMORY HITS:"]
            for h in hits:
                body = (h.content or h.descriptor).strip().replace("\n", " ")
                if len(body) > 300:
                    body = body[:297] + "\u2026"
                aid = f" [artifact={h.artifact_id}]" if h.artifact_id else ""
                hit_lines.append(f"  - ({h.kind or '?'}) {body}{aid}")
            prompt_parts.append("\n".join(hit_lines))
        else:
            prompt_parts.append("MEMORY HITS: none")

        if history:
            hist_lines = ["HISTORY:"]
            for item in history[-10:]:
                if isinstance(item, dict):
                    kind = item.get("kind", "?")
                    if kind == "action":
                        tool = item.get("tool", "?")
                        desc = item.get("result_descriptor", "")[:200]
                        aid = item.get("artifact_id") or ""
                        gid = item.get("goal_id", "?")
                        hist_lines.append(
                            f"  - [action] goal_id={gid} tool={tool} "
                            f"result={desc}"
                            + (f" artifact={aid}" if aid else "")
                        )
                    elif kind == "answer":
                        gid = item.get("goal_id", "?")
                        text = item.get("text", "")[:200]
                        hist_lines.append(
                            f"  - [answer] goal_id={gid} {text}"
                        )
                    else:
                        hist_lines.append(
                            f"  - [{kind}] {json.dumps(item, default=str)[:200]}"
                        )
                else:
                    v = item.value if isinstance(item.value, dict) else {}
                    if item.kind == "tool_outcome":
                        tool = v.get("tool", "?")
                        ok = v.get("ok", True)
                        gid = item.goal_id or "?"
                        desc = (
                            v.get("result") or v.get("result_preview")
                            or v.get("descriptor") or ""
                        )
                        if len(desc) > 300:
                            desc = desc[:297] + "\u2026"
                        aid = f" artifact={item.artifact_id}" if item.artifact_id else ""
                        hist_lines.append(
                            f"  - [tool_outcome] goal_id={gid} tool={tool} "
                            f"ok={ok} result={desc}{aid}"
                        )
                    elif (item.kind == "scratchpad"
                          and item.source == "decision"
                          and v.get("answer")):
                        gid = item.goal_id or "?"
                        ans = str(v["answer"])[:200]
                        hist_lines.append(
                            f"  - [answer] goal_id={gid} {ans}"
                        )
                    else:
                        hist_lines.append(
                            f"  - [{item.kind}] {item.descriptor[:200]}"
                        )
            prompt_parts.append("\n".join(hist_lines))
        else:
            prompt_parts.append("HISTORY: none (first iteration)")

        if prior_goals:
            goal_lines = ["PRIOR GOALS:"]
            for g in prior_goals:
                status = "done" if g.done else "open"
                aid = f" attach_artifact={g.attach_artifact_id}" if g.attach_artifact_id else ""
                goal_lines.append(
                    f"  - id={g.id} [{status}] {g.text}{aid}"
                )
            prompt_parts.append("\n".join(goal_lines))
        else:
            prompt_parts.append(
                "PRIOR GOALS: none (first iteration — decompose the query)"
            )

        prompt = "\n\n".join(prompt_parts)

        try:
            resp = llm.chat(
                prompt=prompt,
                system=self._OBSERVE_SYSTEM,
                provider=self.provider,
                model=self.model,
                auto_route=self.auto_route,
            )
        except Exception:
            return None

        text = (resp.get("text") or "").strip() if isinstance(resp, dict) else ""
        if not text:
            return None

        return self._parse_observe_response(text, prior_goals)

    def _parse_observe_response(
        self,
        text: str,
        prior_goals: list[Goal],
    ) -> Optional[Observation]:
        """Parse the LLM's JSON response into an Observation."""
        # Strip code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)

        # Extract JSON object
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        raw_goals = data.get("goals")
        if not isinstance(raw_goals, list) or not raw_goals:
            return None

        attach = data.get("attach")
        if isinstance(attach, (int, float)):
            attach = str(int(attach))
        elif attach is not None and not isinstance(attach, str):
            attach = None

        # Build Goal objects
        goals: list[Goal] = []
        if prior_goals:
            # Map by id for updating done status
            prior_map = {g.id: g for g in prior_goals}
            for rg in raw_goals:
                if not isinstance(rg, dict):
                    continue
                gid = rg.get("id")
                done = bool(rg.get("done", False))
                if gid in prior_map:
                    pg = prior_map[gid]
                    # Once done, stays done
                    pg.done = pg.done or done
                    goals.append(pg)
                else:
                    # LLM returned an id not in prior_goals — use
                    # prior_goals order instead
                    pass
            # Ensure all prior goals are represented
            seen_ids = {g.id for g in goals}
            for pg in prior_goals:
                if pg.id not in seen_ids:
                    goals.append(pg)
        else:
            # First iteration: create new goals from decomposition
            for rg in raw_goals:
                if isinstance(rg, dict):
                    text_val = str(rg.get("text", "")).strip()
                elif isinstance(rg, str):
                    text_val = rg.strip()
                else:
                    continue
                if not text_val:
                    continue
                gid = rg.get("id") if isinstance(rg, dict) else None
                goals.append(Goal(
                    id=gid if isinstance(gid, int) else uuid.uuid4().int,
                    text=text_val,
                    done=bool(rg.get("done", False)) if isinstance(rg, dict) else False,
                    attach_artifact_id=None,
                ))

        if not goals:
            return None

        goals = goals[:5]

        focus = next((g for g in goals if not g.done), None)
        obs = Observation(
            goals=goals,
            attach=attach,
            goal_id=focus.id if focus is not None else None,
        )
        return obs

    def _rule_based_observe(
        self,
        query: str,
        hits: list[Hit],
        history: list,
        prior_goals: list[Goal],
    ) -> Observation:
        """Fallback: rule-based observe when LLM is unavailable."""
        merged_goals: list[Goal] = []
        if prior_goals:
            merged_goals.extend(prior_goals)
        else:
            for g in self._derive_goals(query, hits=hits):
                merged_goals.append(g)

        answer_goal_ids: set[int] = set()
        for item in (history or []):
            if isinstance(item, dict):
                if item.get("kind") == "answer" and item.get("goal_id") is not None:
                    answer_goal_ids.add(item["goal_id"])
            else:
                if (item.kind == "scratchpad"
                        and item.source == "decision"
                        and item.goal_id is not None):
                    v = item.value if isinstance(item.value, dict) else {}
                    if v.get("answer"):
                        answer_goal_ids.add(item.goal_id)
        for g in merged_goals:
            if not g.done and g.id in answer_goal_ids:
                g.done = True

        focus = next((g for g in merged_goals if not g.done), None)
        attach: Optional[str] = None
        if focus is not None and focus.attach_artifact_id:
            attach = focus.attach_artifact_id
        if attach is None and focus is not None and hits:
            focus_toks = set(_content_tokens(focus.text))
            for h in hits:
                if not h.artifact_id:
                    continue
                h_toks = set(_content_tokens(h.descriptor))
                if focus_toks & h_toks:
                    attach = h.artifact_id
                    break
            if attach is None:
                for h in hits:
                    if h.artifact_id:
                        attach = h.artifact_id
                        break

        return Observation(
            goals=merged_goals,
            attach=attach,
            goal_id=focus.id if focus is not None else None,
        )

    # ------------------------------------------------------------- goal flow

    @staticmethod
    def next_pending(state: QueryState) -> Optional[Goal]:
        """Return the first goal that is not yet done, or `None`."""
        for g in state.goals:
            if not g.done:
                return g
        return None

    def mark_done(self, state: QueryState, goal_id: int) -> Optional[Goal]:
        """Mark `goal_id` as done, persist state, and return the next pending goal."""
        for g in state.goals:
            if g.id == goal_id:
                g.done = True
                break
        return self.next_pending(state)

    @staticmethod
    def all_done(state: QueryState) -> bool:
        return all(g.done for g in state.goals) if state.goals else True

    # ----------------------------------------------------------------- goals

    _DECOMP_SYSTEM = (
        "You decompose a user query into an ordered checklist of "
        "imperative goals for an autonomous agent that uses MCP tools. "
        "Your output drives a multi-turn loop: each goal will be handed "
        "to a sub-agent on a later turn, so goals must be ordered so "
        "that later goals can consume artifacts produced by earlier "
        "ones.\n"
        "\n"
        "# How to think (always do this before outputting the array)\n"
        "Reason step-by-step in a REASONING block, then produce the "
        "JSON array. Do NOT skip the REASONING block.\n"
        "  1. RESTATE: what is the user really asking for?\n"
        "  2. REASONING_TYPE: tag the kind of decomposition. Choose "
        "from: SINGLE (one deliverable), MULTI (multiple independent "
        "deliverables), CHAINED (sequential dependent steps).\n"
        "  3. SPLIT: identify each distinct deliverable the user wants.\n"
        "  4. SELF_CHECK: are any goals redundant or out of order? Does "
        "the count obey the limits below? Did you preserve URLs/paths "
        "verbatim? Did you accidentally split a single-source fetch+"
        "extract into multiple goals?\n"
        "\n"
        "# Output format (STRICT, machine-parsed)\n"
        "First write the REASONING block, then on a new line output "
        "ONLY a single JSON array of short imperative strings.\n"
        "\n"
        "REASONING:\n"
        "- restate: <one sentence>\n"
        "- reasoning_type: <tag from the set above>\n"
        "- split: <list the deliverables>\n"
        "- self_check: <one or two sentences>\n"
        "\n"
        "Then the JSON array. No markdown fences, no comments, no "
        "explanation, and no other '[' or ']' characters anywhere "
        "except in the final array.\n"
        "\n"
        "# Rules\n"
        "- Each goal describes WHAT the user wants, not HOW to do it. "
        "Do NOT prescribe tool calls, file reads, or retrieval steps "
        "— the decision layer will figure out the method.\n"
        "- IMPORTANT: when the user provides an explicit URL, file "
        "path, or other concrete locator, you MUST preserve it "
        "verbatim in the goal text. The decision layer needs the "
        "exact resource identifier to act on it.\n"
        "- Each separate user-visible deliverable is its own goal "
        "(e.g. \"store X\", \"create reminder A\", \"create reminder "
        "B\").\n"
        "- Use exactly 1 goal for simple queries (greetings, single "
        "questions, simple chit-chat, factual lookups).\n"
        "- Never exceed 5 goals. Never restate the same task twice. "
        "Keep each goal under 140 chars and imperative (\"Answer ...\", "
        "\"Save ...\", \"Create ...\").\n"
        "- Error handling / fallback: if the query is empty, "
        "unintelligible, or you are unsure how to split it, output "
        "exactly [\"Clarify the user's request\"]. Do NOT invent "
        "tasks the user did not ask for. Do NOT hallucinate.\n"
        "\n"
        "# Examples\n"
        "\n"
        "Query: hi\n"
        "REASONING:\n"
        "- restate: The user is greeting.\n"
        "- reasoning_type: SINGLE\n"
        "- split: one deliverable — a greeting.\n"
        "- self_check: 1 goal, no URLs, correct.\n"
        "[\"Greet the user\"]\n"
    )

    def _llm_decompose_goals(self, query: str, *, hits: list[Hit] | None = None) -> Optional[list[str]]:
        """Ask the LLM for a JSON list of goals. Returns None on any failure."""
        key = query.strip()
        fact_hits = [h for h in (hits or []) if getattr(h, 'kind', None) == 'fact']
        cache_key = key if not fact_hits else f"{key}__facts:{len(fact_hits)}"
        if cache_key in self._decomp_cache:
            return self._decomp_cache[cache_key]
        llm = self.llm
        if llm is None:
            try:
                from llm_gatewayV3.client import LLM  # local import; allowed
                llm = LLM()
                self.llm = llm
            except Exception:
                return None
        try:
            system = self._DECOMP_SYSTEM
            # Append known facts so the LLM generates appropriate goals
            # (e.g. "Answer X" instead of "Search for X").
            fact_hits = [h for h in (hits or []) if getattr(h, 'kind', None) == 'fact']
            if fact_hits:
                fact_lines = ["Known facts from memory:"]
                for h in fact_hits:
                    body = (h.content or h.descriptor).strip().replace("\n", " ")
                    if len(body) > 300:
                        body = body[:297] + "\u2026"
                    fact_lines.append(f"  - {body}")
                system = system + "\n\n" + "\n".join(fact_lines)
            resp = llm.chat(
                prompt=query,
                system=system,
                provider=self.provider,
                model=self.model,
                auto_route=self.auto_route,
            )
        except Exception:
            return None
        text = (resp.get("text") or "").strip() if isinstance(resp, dict) else ""
        if not text:
            return None
        # Strip code fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        m = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list):
            return None
        goals = [str(x).strip() for x in data if isinstance(x, str) and x.strip()]
        if not goals:
            return None
        goals = goals[:5]
        self._decomp_cache[cache_key] = goals
        return goals

    def _derive_goals(self, query: str, *, hits: list[Hit] | None = None) -> list[Goal]:
        """Decompose ``query`` into one or more imperative goals.

        Uses an LLM (when available and ``decompose=True``) for accurate
        intent splitting. Falls back to a conservative regex splitter on
        any failure or when ``decompose=False``.
        """
        texts: Optional[list[str]] = None
        if self.decompose:
            texts = self._llm_decompose_goals(query, hits=hits)
        if not texts:
            texts = self._regex_split_goals(query)
        return [
            Goal(
                id=uuid.uuid4().int,
                text=t,
                done=False,
                attach_artifact_id=None,
            )
            for t in texts
        ]

    @staticmethod
    def _regex_split_goals(query: str) -> list[str]:
        """Fallback splitter: hard sentence boundaries only.

        Splits on ``?``, ``;``, and ``.`` followed by whitespace + a
        capital letter. Conjunctions like ``and`` are not split.
        Tiny fragments (< 12 chars) are merged into their predecessor.
        """
        chunks = re.split(r"\?+|;|\.\s+(?=[A-Z])", query)
        texts = [c.strip(" ,.?!") for c in chunks if c and c.strip(" ,.?!")]
        merged: list[str] = []
        for t in texts:
            if merged and len(t) < 12:
                merged[-1] = f"{merged[-1]}; {t}"
            else:
                merged.append(t)
        if not merged:
            merged = [query.strip()]
        return merged


__all__ = ["Perception", "QueryState"]
