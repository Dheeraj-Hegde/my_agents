"""HTML report generator for a Session 8 run.

Reads persisted state from state/sessions/<sid>/ and emits a single
self-contained HTML file at state/sessions/<sid>/output/report.html.

Sections:
  1. Original user goal
  2. Planner DAG
  3. Browser path chosen per browser node
  4. Browser actions taken
  5. Screenshots / page-state logs (base64-embedded)
  6. Extracted data
  7. Final comparison table
  8. Turn count and cost summary

Importable:
    from html_replay import generate_html_report
    path = generate_html_report("s8-abc123")

CLI:
    uv run python html_replay.py <session_id>
    uv run python html_replay.py          # lists available sessions
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from persistence import SessionStore, list_sessions
from schemas import NodeState


# ── gateway cost rollup ───────────────────────────────────────────────────────

_GATEWAY_URL = "http://localhost:8109"


def _fetch_session_rollup(session_id: str) -> dict[str, dict] | None:
    """Pull the per-agent token / dollar rollup the V9 gateway logs for
    this session. Returns a `{agent_tag: {in_tok, out_tok, dollars, calls,
    provider}}` map, or None if the gateway is offline / the session has
    no logged calls. Used so the cost summary reflects real logged tokens
    even when individual node JSONs were written with stale pricing."""
    try:
        r = httpx.get(
            f"{_GATEWAY_URL}/v1/cost/by_agent",
            params={"session": session_id},
            timeout=4.0,
        )
        if r.status_code != 200:
            return None
        raw = r.json() or {}
    except Exception:
        return None
    out: dict[str, dict] = {}
    for agent, rows in raw.items():
        # Multiple provider rows per agent (failover). Aggregate them.
        total_in = sum(int(row.get("in_tok") or 0) for row in rows)
        total_out = sum(int(row.get("out_tok") or 0) for row in rows)
        total_dollars = sum(float(row.get("dollars") or 0.0) for row in rows)
        total_calls = sum(int(row.get("calls") or 0) for row in rows)
        # Pick the provider that did the most work for display.
        rows_sorted = sorted(rows, key=lambda r: int(r.get("calls") or 0),
                             reverse=True)
        primary = (rows_sorted[0].get("provider") if rows_sorted else "") or ""
        out[agent] = {
            "in_tok": total_in,
            "out_tok": total_out,
            "dollars": round(total_dollars, 6),
            "calls": total_calls,
            "provider": primary,
        }
    return out

ROOT = Path(__file__).parent


# ── helpers ───────────────────────────────────────────────────────────────────

def _esc(s: Any) -> str:
    """HTML-escape a value for safe insertion."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _pre(s: Any, max_chars: int = 4000) -> str:
    text = str(s or "")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (truncated)"
    return f"<pre class='code'>{_esc(text)}</pre>"


def _badge(text: str, colour: str) -> str:
    return f"<span class='badge' style='background:{colour}'>{_esc(text)}</span>"


_PATH_COLOURS = {
    "extract": "#4caf50",
    "deterministic": "#2196f3",
    "a11y": "#ff9800",
    "vision": "#9c27b0",
    "blocked": "#f44336",
}


# ── section builders ──────────────────────────────────────────────────────────

def _sec(title: str, body: str) -> str:
    return (
        f"<section>\n"
        f"  <h2>{_esc(title)}</h2>\n"
        f"  {body}\n"
        f"</section>\n"
    )


def _section_goal(query: str) -> str:
    return _sec("1. Original User Goal",
                f"<blockquote>{_esc(query)}</blockquote>")


def _section_dag(states: list[NodeState]) -> str:
    rows = []
    for st in states:
        r = st.result
        elapsed = f"{r.elapsed_s:.1f}s" if r and r.elapsed_s else "—"
        provider = (r.provider if r and r.provider else "—")
        # Show real cost (incl. $0.0000 for free-tier providers) whenever a
        # gateway call actually happened; only show — for nodes that never
        # contacted the LLM (no provider attributed).
        cost = f"${r.cost:.4f}" if r and r.provider else "—"
        status_col = {
            "complete": "#4caf50", "failed": "#f44336",
            "skipped": "#9e9e9e", "running": "#ff9800", "pending": "#607d8b",
        }.get(st.status, "#607d8b")
        rows.append(
            f"<tr>"
            f"<td>{_esc(st.node_id)}</td>"
            f"<td>{_esc(st.skill)}</td>"
            f"<td><span style='color:{status_col};font-weight:bold'>{_esc(st.status)}</span></td>"
            f"<td>{_esc(', '.join(st.inputs) or '—')}</td>"
            f"<td>{_esc(elapsed)}</td>"
            f"<td>{_esc(provider)}</td>"
            f"<td>{_esc(cost)}</td>"
            f"</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Node</th><th>Skill</th><th>Status</th>"
        "<th>Inputs</th><th>Elapsed</th><th>Provider</th><th>Cost</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return _sec("2. Planner DAG", table)


def _section_browser_paths(states: list[NodeState]) -> str:
    browser_nodes = [s for s in states if s.skill == "browser"]
    if not browser_nodes:
        return _sec("3. Browser Path Chosen", "<p>No browser nodes ran.</p>")
    rows = []
    for st in browser_nodes:
        out = (st.result.output if st.result else {}) or {}
        path = out.get("path", "—")
        colour = _PATH_COLOURS.get(path, "#9e9e9e")
        url = out.get("url") or out.get("final_url") or "—"
        goal = out.get("goal", "—")
        turns = out.get("turns", 0)
        err = st.result.error if st.result else None
        rows.append(
            f"<tr>"
            f"<td>{_esc(st.node_id)}</td>"
            f"<td>{_badge(path, colour)}</td>"
            f"<td class='url'>{_esc(url)}</td>"
            f"<td>{_esc(goal)}</td>"
            f"<td>{_esc(turns)}</td>"
            f"<td>{_esc(err or '—')}</td>"
            f"</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Node</th><th>Path</th><th>URL</th><th>Goal</th><th>Turns</th><th>Error</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return _sec("3. Browser Path Chosen", table)


def _section_browser_actions(states: list[NodeState]) -> str:
    browser_nodes = [s for s in states if s.skill == "browser"]
    if not browser_nodes:
        return _sec("4. Browser Actions Taken", "<p>No browser nodes ran.</p>")

    parts = []
    for st in browser_nodes:
        out = (st.result.output if st.result else {}) or {}
        actions: list[dict] = out.get("actions") or []
        url = out.get("url") or st.node_id
        goal = out.get("goal", "")

        parts.append(f"<h3>{_esc(st.node_id)} — {_esc(url)}</h3>")
        parts.append(f"<p class='goal-label'>Goal: {_esc(goal)}</p>")
        if not actions:
            parts.append("<p><em>No interactive actions recorded (extract path or blocked).</em></p>")
            continue

        rows = []
        for rec in actions:
            turn = rec.get("turn", "?")
            outcome = _esc(str(rec.get("outcome", ""))[:300])
            # actions field is a list of parsed action objects per turn
            acts = rec.get("actions") or []
            if isinstance(acts, list):
                act_text = "; ".join(
                    str(a) if not isinstance(a, dict)
                    else (a.get("tool") or a.get("action") or json.dumps(a, ensure_ascii=False))
                    for a in acts
                )
            else:
                act_text = str(acts)
            rows.append(
                f"<tr>"
                f"<td>{_esc(turn)}</td>"
                f"<td>{_esc(act_text[:300])}</td>"
                f"<td>{outcome}</td>"
                f"</tr>"
            )
        parts.append(
            "<table><thead><tr>"
            "<th>Turn</th><th>Actions</th><th>Outcome</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    return _sec("4. Browser Actions Taken", "\n".join(parts))


def _section_screenshots(session_id: str, states: list[NodeState]) -> str:
    browser_dir = ROOT / "state" / "sessions" / session_id / "browser"
    if not browser_dir.exists():
        return _sec("5. Screenshots / Page-State Logs",
                    "<p>No browser artifacts directory found.</p>")

    # Prefer annotated screenshots; fall back to raw
    pngs = sorted(browser_dir.rglob("*_marked.png"))
    if not pngs:
        pngs = sorted(browser_dir.rglob("*.png"))
    if not pngs:
        # No screenshots — show content snippet from each browser node
        parts = []
        for st in states:
            if st.skill != "browser":
                continue
            out = (st.result.output if st.result else {}) or {}
            content = out.get("content") or ""
            parts.append(f"<h3>{_esc(st.node_id)}</h3>")
            parts.append(_pre(content[:1000] or "(no content)"))
        return _sec("5. Screenshots / Page-State Logs",
                    "\n".join(parts) or "<p>No screenshots or content captured.</p>")

    imgs = []
    for png in pngs:
        rel = png.relative_to(browser_dir)
        data = base64.b64encode(png.read_bytes()).decode()
        imgs.append(
            f"<figure>"
            f"<img src='data:image/png;base64,{data}' alt='{_esc(str(rel))}'>"
            f"<figcaption>{_esc(str(rel))}</figcaption>"
            f"</figure>"
        )
    return _sec("5. Screenshots / Page-State Logs",
                f"<div class='screenshots'>{''.join(imgs)}</div>")


def _section_extracted(states: list[NodeState]) -> str:
    parts = []
    for st in states:
        if st.skill not in ("browser", "distiller", "researcher"):
            continue
        if not st.result or not st.result.output:
            continue
        out = st.result.output
        parts.append(f"<h3>{_esc(st.node_id)} — {_esc(st.skill)}</h3>")
        if st.skill == "browser":
            content = out.get("content") or ""
            parts.append(_pre(content[:2000] or "(no content extracted)"))
        elif st.skill == "distiller":
            parts.append(_pre(json.dumps(out, indent=2, ensure_ascii=False), max_chars=3000))
        elif st.skill == "researcher":
            findings = out.get("findings") or str(out)
            parts.append(_pre(str(findings)[:2000]))
    if not parts:
        return _sec("6. Extracted Data", "<p>No extraction nodes found.</p>")
    return _sec("6. Extracted Data", "\n".join(parts))


def _md_table_to_html(text: str) -> str | None:
    """Convert a Markdown pipe table to an HTML <table>.
    Returns None if the text doesn't look like a pipe table."""
    lines = [l.rstrip() for l in text.splitlines() if "|" in l]
    if len(lines) < 2:
        return None
    # Strip separator row (e.g., |---|---|)
    data_lines = [l for l in lines if not all(c in "-|: " for c in l)]
    if not data_lines:
        return None

    def parse_row(line: str) -> list[str]:
        cells = line.split("|")
        # Remove empty first/last if table is `| a | b |` form
        if cells and not cells[0].strip():
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        return [c.strip() for c in cells]

    header = parse_row(data_lines[0])
    body_rows = data_lines[1:]
    th = "".join(f"<th>{_esc(h)}</th>" for h in header)
    trs = []
    for row in body_rows:
        cells = parse_row(row)
        # Pad short rows
        while len(cells) < len(header):
            cells.append("")
        trs.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    return (
        "<table class='comparison'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{''.join(trs)}</tbody>"
        "</table>"
    )


def _section_comparison_table(states: list[NodeState]) -> str:
    # Find formatter node — it holds final_answer
    formatter = next((s for s in reversed(states) if s.skill == "formatter"), None)
    if not formatter or not formatter.result:
        return _sec("7. Final Comparison Table", "<p>No formatter output found.</p>")
    answer = formatter.result.output.get("final_answer") or ""
    if not answer:
        return _sec("7. Final Comparison Table", "<p>Formatter produced no answer.</p>")

    html_table = _md_table_to_html(answer)
    if html_table:
        body = html_table
    else:
        body = _pre(answer, max_chars=6000)
    return _sec("7. Final Comparison Table", body)


def _section_summary(states: list[NodeState],
                     rollup: dict[str, dict] | None = None) -> str:
    rows = []
    total_elapsed = 0.0
    total_cost = 0.0
    total_in_tok = 0
    total_out_tok = 0
    rollup = rollup or {}

    # The gateway rolls up tokens by *agent tag* (skill name), not by
    # node id, so we can't precisely split a skill that ran twice. To
    # keep the column totals honest we attribute the full rollup to the
    # last node of each skill that actually emitted LLM calls (i.e. has
    # a recorded provider), and zero out the others. The summary row
    # at the bottom then equals the real session total.
    last_idx_with_llm: dict[str, int] = {}
    for idx, st in enumerate(states):
        r = st.result
        if r and r.provider:
            last_idx_with_llm[st.skill] = idx

    for idx, st in enumerate(states):
        r = st.result
        elapsed = r.elapsed_s if r and r.elapsed_s else 0.0
        live = rollup.get(st.skill) or {}
        is_attributed_row = (idx == last_idx_with_llm.get(st.skill))
        if is_attributed_row and live:
            cost = float(live.get("dollars") or 0.0)
            in_tok = int(live.get("in_tok") or 0)
            out_tok = int(live.get("out_tok") or 0)
            provider = (live.get("provider")
                        or (r.provider if r and r.provider else "")
                        or "—")
        else:
            # Fall back to whatever the node JSON recorded at write time
            # (typically $0 for nodes that didn't talk to the gateway).
            cost = float(r.cost) if r and r.cost else 0.0
            in_tok = 0
            out_tok = 0
            provider = (r.provider if r and r.provider else "—")
        total_elapsed += elapsed
        total_cost += cost
        total_in_tok += in_tok
        total_out_tok += out_tok
        rows.append(
            f"<tr>"
            f"<td>{_esc(st.node_id)}</td>"
            f"<td>{_esc(st.skill)}</td>"
            f"<td>{_esc(f'{elapsed:.1f}s')}</td>"
            f"<td>{_esc(f'{in_tok:,} / {out_tok:,}')}</td>"
            f"<td>{_esc(f'${cost:.4f}')}</td>"
            f"<td>{_esc(provider)}</td>"
            f"</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Node</th><th>Skill</th><th>Elapsed</th>"
        "<th>Tokens (in / out)</th><th>Cost</th><th>Provider</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    total_turns = sum(
        (s.result.output or {}).get("turns", 0)
        for s in states
        if s.skill == "browser" and s.result
    )
    rollup_note = (
        "<p class='cost-note'><em>Cost = (logged tokens) × (provider list "
        "price per million tokens, see <code>llm_gatewayV9/pricing.py</code>). "
        "Token rows are attributed once per skill (to the last node that "
        "called the LLM), since the gateway aggregates by agent tag and "
        "cannot split a skill that ran twice within one session. If you "
        "ran on a free-tier quota you weren't actually charged — the "
        "figure shows what equivalent paid usage would cost.</em></p>"
        if total_cost > 0
        else "<p class='cost-note'><em>All providers used in this run "
             "were on $0/Mtok tiers (or no calls were logged), so total "
             "cost is $0.</em></p>"
    )
    summary = (
        f"<p><strong>Total nodes:</strong> {len(states)} &nbsp;|&nbsp; "
        f"<strong>Browser turns:</strong> {total_turns} &nbsp;|&nbsp; "
        f"<strong>Total elapsed:</strong> {total_elapsed:.1f}s &nbsp;|&nbsp; "
        f"<strong>Total tokens:</strong> "
        f"{total_in_tok:,} in / {total_out_tok:,} out &nbsp;|&nbsp; "
        f"<strong>Total cost:</strong> ${total_cost:.4f}</p>"
        + rollup_note
    )
    return _sec("8. Turn Count and Cost Summary", table + summary)


# ── HTML shell ────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 0;
       background: #f5f5f5; color: #212121; }
header { background: #1565c0; color: white; padding: 1.2rem 2rem; }
header h1 { margin: 0; font-size: 1.4rem; }
header .sid { font-size: 0.85rem; opacity: 0.7; }
main { max-width: 1200px; margin: 0 auto; padding: 1rem 1.5rem 3rem; }
section { background: white; border-radius: 6px; margin-bottom: 1.5rem;
          padding: 1.2rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,.12); }
h2 { margin-top: 0; font-size: 1.05rem; color: #1565c0;
     border-bottom: 1px solid #e0e0e0; padding-bottom: 0.4rem; }
h3 { font-size: 0.95rem; color: #424242; margin: 1rem 0 0.3rem; }
blockquote { background: #e3f2fd; border-left: 4px solid #1565c0;
             margin: 0; padding: 0.8rem 1rem; border-radius: 0 4px 4px 0;
             font-size: 1rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem;
        margin-top: 0.5rem; }
th { background: #e8eaf6; text-align: left; padding: 0.45rem 0.6rem;
     border: 1px solid #c5cae9; }
td { padding: 0.4rem 0.6rem; border: 1px solid #e0e0e0; vertical-align: top; }
tr:nth-child(even) td { background: #fafafa; }
table.comparison th { background: #1565c0; color: white; }
table.comparison tr:nth-child(even) td { background: #e8f5e9; }
pre.code { background: #263238; color: #eceff1; padding: 0.8rem 1rem;
           border-radius: 4px; overflow-x: auto; font-size: 0.78rem;
           white-space: pre-wrap; word-break: break-word; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px;
         color: white; font-weight: bold; font-size: 0.78rem; }
.url { word-break: break-all; font-size: 0.8rem; }
.goal-label { color: #555; font-size: 0.85rem; margin: 0.2rem 0 0.5rem; }
.screenshots { display: flex; flex-wrap: wrap; gap: 1rem; }
figure { margin: 0; }
figure img { max-width: 600px; border: 1px solid #ccc; border-radius: 4px;
             display: block; }
figcaption { font-size: 0.75rem; color: #757575; margin-top: 0.25rem; }
"""


def _html_doc(session_id: str, query: str, sections: list[str]) -> str:
    return (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        f"<title>Agent Replay — {_esc(session_id)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"<header>"
        f"<h1>Agent Replay Report</h1>"
        f"<div class='sid'>session: {_esc(session_id)}</div>"
        f"</header>\n"
        f"<main>\n"
        + "\n".join(sections)
        + "</main>\n</body>\n</html>\n"
    )


# ── public API ────────────────────────────────────────────────────────────────

def generate_html_report(session_id: str) -> Path:
    """Read persisted session state and write report.html.

    Returns the Path to the written file.
    """
    store = SessionStore(session_id)
    states = store.read_all_nodes()
    query = store.read_query() or "(no query recorded)"

    # Pull the live token/cost rollup the gateway has for this session
    # (may be None if the gateway is offline or never logged this session).
    rollup = _fetch_session_rollup(session_id)

    sections = [
        _section_goal(query),
        _section_dag(states),
        _section_browser_paths(states),
        _section_browser_actions(states),
        _section_screenshots(session_id, states),
        _section_extracted(states),
        _section_comparison_table(states),
        _section_summary(states, rollup),
    ]

    html = _html_doc(session_id, query, sections)
    out_path = ROOT / "state" / "sessions" / session_id / "output" / "report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    if not args:
        sessions = list_sessions()
        if not sessions:
            print("html_replay: no sessions under state/sessions/", file=sys.stderr)
            return 2
        print("available sessions:")
        for s in sessions:
            print(f"  {s}")
        print("\nusage: uv run python html_replay.py <session_id>")
        return 0
    sid = args[0]
    path = generate_html_report(sid)
    print(f"report written → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
