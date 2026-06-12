"""Flight-comparison runner — drives the browser agent on a real
flight-comparison task and emits an HTML replay report and (via
make_demo_video.py) a narrated MP4 with click highlights.

Task
----
Compare flights for **25 Jun 2026 → 30 Jun 2026, Bengaluru (BLR) →
London (LHR)** and produce a comparison of the top three flights on:

  * Price
  * Layover time
  * Total travel time

Like ``booking_runner.py``, flight metasearch sites (Google Flights,
Kayak, Skyscanner, etc.) aggressively block headless browsers. The
runner therefore tries them in order and falls back to the next on a
gateway block / interaction failure. A final Wikipedia fallback (the
BLR airport page lists carriers that fly the BLR–LHR route, plus
typical schedule data) guarantees the distiller has *something*
real to summarise even when every commercial OTA gates the bot.

Output
------
   state/sessions/<session_id>/output/report.html   (8-section replay)
   state/sessions/<session_id>/output/demo.mp4      (run separately)

Usage
-----
   uv run python flight_runner.py
   uv run python flight_runner.py "custom flight query"
   # then:
   uv run python make_demo_video.py            # picks latest session
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).parent


# ── task definition ──────────────────────────────────────────────────────────

DEPART_DATE = "2026-06-25"
RETURN_DATE = "2026-06-30"
ORIGIN_CITY = "Bengaluru"
ORIGIN_IATA = "BLR"
DEST_CITY = "London"
DEST_IATA = "LHR"
ADULTS = 1

_DEFAULT_QUERY = (
    f"Compare flights for {DEPART_DATE} (depart) to {RETURN_DATE} (return) "
    f"from {ORIGIN_CITY} ({ORIGIN_IATA}) to {DEST_CITY} ({DEST_IATA}). "
    f"Extract the top three flights and compare them on price, layover "
    f"time and total travel time.\n\n"
    f"Produce the comparison as a table with columns:\n"
    f"Airline / Flight | Price | Layover | Total Travel Time."
)


# ── source URLs (tried in order) ─────────────────────────────────────────────

# Google Flights deep-link. The `tfs` parameter encodes search params on
# the public Google Flights surface; the human-readable `q=` form below
# triggers Google to redirect into the same SPA so the agent lands on
# results immediately. Heavy bot detection — usually blocked.
GOOGLE_FLIGHTS_URL = (
    "https://www.google.com/travel/flights?"
    f"q=Flights+from+{quote_plus(ORIGIN_CITY)}+to+{quote_plus(DEST_CITY)}"
    f"+on+{DEPART_DATE}+through+{RETURN_DATE}"
)

# Kayak — same parameters as a normal user URL.
KAYAK_URL = (
    f"https://www.kayak.com/flights/{ORIGIN_IATA}-{DEST_IATA}/"
    f"{DEPART_DATE}/{RETURN_DATE}/{ADULTS}adults?sort=price_a"
)

# Skyscanner India route page (covers BLR → LHR round trip).
SKYSCANNER_URL = (
    "https://www.skyscanner.co.in/transport/flights/"
    f"{ORIGIN_IATA.lower()}/{DEST_IATA.lower()}/"
    f"{DEPART_DATE[2:].replace('-', '')}/{RETURN_DATE[2:].replace('-', '')}/"
    f"?adults={ADULTS}&sortby=price"
)

# Momondo — sibling of Kayak, sometimes tolerates headless better.
MOMONDO_URL = (
    f"https://www.momondo.com/flight-search/{ORIGIN_IATA}-{DEST_IATA}/"
    f"{DEPART_DATE}/{RETURN_DATE}?sort=price_a"
)

# Wikipedia airport page for BLR — under "Airlines and destinations" it
# lists every carrier flying out of BLR including the BLR–LHR route
# (British Airways and Air India both operate it). Served as plain HTML
# with no bot gating, so Layer 1 (httpx + trafilatura) extracts it
# cleanly and the cascade stops at the cheap text layer.
WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/Kempegowda_International_Airport"


# ── per-source browser goals ─────────────────────────────────────────────────

# Phrased with interactive verbs (click, scroll, sort) so the cascade
# escalates straight to Layer 2b / 3 (a11y / vision) on the OTA sites.

_GOOGLE_GOAL = (
    f"This page is Google Flights showing round-trip flights from "
    f"{ORIGIN_CITY} ({ORIGIN_IATA}) to {DEST_CITY} ({DEST_IATA}) "
    f"departing {DEPART_DATE} and returning {RETURN_DATE}. If a "
    f"consent / cookies banner appears, dismiss it once. Then sort by "
    f"price (cheapest first) if not already sorted, scroll the "
    f"results list, and read the top three flight cards. For each "
    f"capture: airline / flight number, total price, total travel "
    f"time, and the layover stop (city + duration, or 'Nonstop'). "
    f"Finish when you have read three rows."
)

_KAYAK_GOAL = (
    f"This page is Kayak showing round-trip flights {ORIGIN_IATA} -> "
    f"{DEST_IATA} for {DEPART_DATE} / {RETURN_DATE}, sorted by price "
    f"ascending. If a cookie banner appears, dismiss it once. Then "
    f"scroll through the results and read the top three flight cards. "
    f"For each capture: airline, total price, total travel duration, "
    f"and the layover (city + duration, or 'Nonstop')."
)

_SKYSCANNER_GOAL = (
    f"This page is Skyscanner showing {ORIGIN_CITY} -> {DEST_CITY} "
    f"flights for {DEPART_DATE} / {RETURN_DATE}, sorted cheapest. If "
    f"a cookies / consent banner appears, dismiss it once. Then read "
    f"the top three flight result cards. For each capture: airline, "
    f"total price (in INR or GBP if shown), total travel duration, "
    f"and the stops information (layover city + duration, or 'Direct')."
)

_MOMONDO_GOAL = (
    f"This page is Momondo showing {ORIGIN_IATA} -> {DEST_IATA} "
    f"round-trip flights for {DEPART_DATE} / {RETURN_DATE}, sorted "
    f"cheapest first. If a cookie banner appears, dismiss it once. "
    f"Read the top three flight cards and capture for each: airline, "
    f"total price, total travel time, and layover (city + duration, "
    f"or 'Direct')."
)

# Wikipedia goal is intentionally non-interactive so Layer 1's static
# extract is accepted (no click/sort/filter verbs).
_WIKIPEDIA_GOAL = (
    f"This Wikipedia article about Kempegowda International Airport "
    f"({ORIGIN_IATA}) lists every airline operating from {ORIGIN_CITY} "
    f"with each carrier's destinations. Identify the airlines that "
    f"serve {DEST_CITY} ({DEST_IATA}) — typically British Airways and "
    f"Air India operate nonstop, with several carriers offering "
    f"one-stop connections via the Gulf hubs. Report the three most "
    f"prominent carriers on the {ORIGIN_IATA}-{DEST_IATA} route, "
    f"and for each note: airline name, whether the route is nonstop "
    f"or one-stop (and which hub the typical connection uses), and "
    f"the typical scheduled flight duration (nonstop BLR -> LHR is "
    f"about 11 hours, one-stop via Gulf hubs is about 14-18 hours)."
)


# ── prompt builders ──────────────────────────────────────────────────────────

def _distiller_prompt(content: str, source_url: str, source: str,
                      query: str) -> str:
    tmpl = (ROOT / "prompts" / "distiller.md").read_text(encoding="utf-8")
    inputs = [{
        "id": "n:browser",
        "kind": "upstream",
        "skill": "browser",
        "source": source,
        "page_url": source_url,
        "extracted_page_text": (content or "")[:18000],
        "hint": (
            f"The extracted text comes from a flight search for "
            f"{ORIGIN_CITY} ({ORIGIN_IATA}) -> {DEST_CITY} ({DEST_IATA}) "
            f"on {DEPART_DATE} returning {RETURN_DATE}. Locate the three "
            f"cheapest (or most prominent) flight results listed on the "
            f"page. For each flight pull out: airline name + flight "
            f"number when shown, total round-trip price with currency, "
            f"the total travel duration end-to-end (e.g. '11h 25m' or "
            f"'17h 50m'), and the layover description — if the flight "
            f"is non-stop write 'Nonstop' (or 'Direct'), otherwise "
            f"capture the layover city/airport and stopover duration "
            f"(e.g. 'Doha 2h 10m' or 'Dubai 1h 50m'). Many flight "
            f"pages render durations as 'XXh YYm' near the times. "
            f"Return `fields.flights` as a JSON array of exactly three "
            f"objects with keys: "
            f"airline (string, e.g. 'British Airways BA118'), "
            f"price (string with currency, e.g. 'INR 58,420' or "
            f"'GBP 620'), "
            f"layover (string — either 'Nonstop' or 'Doha 2h 10m'), "
            f"travel_time (string, e.g. '11h 25m'). "
            f"If the source is the Wikipedia airport article and only "
            f"lists carriers / typical durations (not live prices), "
            f"populate price with 'Indicative — see source' and use the "
            f"typical scheduled durations the article mentions. "
            f"Also emit `comparison` with `winner` (the flight id with "
            f"the best price + shortest travel time tradeoff) and "
            f"`reason`."
        ),
    }]
    return (
        tmpl.rstrip()
        + f"\n\nUSER_QUERY: {query}\n\n"
        + "INPUTS:\n"
        + json.dumps(inputs, indent=2, ensure_ascii=False, default=str)[:24000]
    )


def _formatter_prompt(distiller_output: dict, distiller_nid: str,
                      query: str) -> str:
    tmpl = (ROOT / "prompts" / "formatter.md").read_text(encoding="utf-8")
    inputs = [
        {"id": "USER_QUERY", "kind": "query", "value": query},
        {"id": distiller_nid, "kind": "upstream", "skill": "distiller",
         "output": distiller_output},
    ]
    instructions = (
        "Render the final answer as a **GitHub-flavoured Markdown pipe "
        "table** with exactly this header row and column order:\n"
        "`| Rank | Airline / Flight | Price | Layover | Total Travel Time |`\n"
        "Follow with the separator row `|------|...|...|...|...|` and "
        "exactly THREE data rows (the three flights from the "
        "distiller, in price-ascending order, ranked 1 / 2 / 3). Keep "
        "each cell compact (~6 words). After the table add one short "
        "paragraph noting the source site, the route "
        f"({ORIGIN_CITY} -> {DEST_CITY}), and the travel dates "
        f"({DEPART_DATE} / {RETURN_DATE}). End with a single "
        "**Recommendation:** sentence naming the best-value flight "
        "based on price-vs-time tradeoff."
    )
    return (
        tmpl.rstrip()
        + f"\n\nUSER_QUERY: {query}\n\n"
        + "FORMATTING INSTRUCTIONS:\n" + instructions + "\n\n"
        + "INPUTS:\n"
        + json.dumps(inputs, indent=2, ensure_ascii=False, default=str)[:20000]
    )


# ── core runner ──────────────────────────────────────────────────────────────

async def _run(query: str) -> tuple[str, str]:
    from browser.skill import BrowserSkill
    from gateway import LLM, ensure_gateway
    from persistence import SessionStore
    from schemas import AgentResult, NodeSpec, NodeState
    from skills import parse_skill_json

    sid = f"s8-{uuid.uuid4().hex[:8]}"
    print(f"\nSession ID : {sid}")
    print(f"Query      : {query[:120]}{'…' if len(query) > 120 else ''}\n")

    await asyncio.to_thread(ensure_gateway)

    store = SessionStore(sid)
    store.write_query(query)

    _counter = 0

    def nxt() -> str:
        nonlocal _counter
        _counter += 1
        return f"n:{_counter}"

    artifacts_root = str(ROOT / "state" / "sessions" / sid / "browser")

    async def _browser(url: str, goal: str, *, inputs: list[str],
                       label: str,
                       force_path: str = "vision",
                       attempts: int = 1,
                       ) -> tuple[str, AgentResult]:
        nid = nxt()
        t0 = time.time()
        spec_md = {
            "url": url, "goal": goal, "force_path": force_path,
            "node_id": nid,
        }
        spec = NodeSpec(skill="browser", inputs=inputs, metadata=spec_md)
        result: AgentResult | None = None
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            sk = BrowserSkill(
                artifacts_root=artifacts_root, session=sid,
                max_steps_vision=14, max_steps_a11y=10,
                wall_clock_s=180.0,
            )
            try:
                result = await sk.run(spec)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                result = None
                print(f"    └─ attempt {attempt}/{attempts} crashed: "
                      f"{type(exc).__name__}: {str(exc)[:80]}")
                continue
            ok = result.success and bool((result.output or {}).get("content"))
            if ok:
                break
            if attempt < attempts:
                print(f"    └─ attempt {attempt}/{attempts} failed "
                      f"(error_code={result.error_code}); retrying")
        if result is None:
            result = AgentResult(
                success=False, agent_name="browser", output={
                    "url": url, "goal": goal, "path": force_path,
                    "turns": 0, "content": None, "actions": [],
                    "final_url": url,
                },
                error=(f"{type(last_exc).__name__}: {last_exc}"
                       if last_exc else "all attempts failed"),
                error_code="interaction_failed",
                elapsed_s=time.time() - t0,
            )
        if not result.elapsed_s:
            result.elapsed_s = time.time() - t0

        path = (result.output or {}).get("path", "?") if result.output else "error"
        turns = (result.output or {}).get("turns", 0) if result.output else 0
        err = f"  error={(result.error or '')[:80]}" if result.error else ""
        print(f"[{nid}] browser({label})  url={url[:70]}…  "
              f"path={path}  turns={turns}  "
              f"elapsed={result.elapsed_s:.1f}s{err}")

        store.write_node(NodeState(
            node_id=nid, skill="browser",
            status="complete" if result.success else "failed",
            inputs=inputs, result=result,
            prompt_sent=(
                f"BrowserSkill(url={url}, force_path={force_path})\n"
                f"goal: {goal}"
            ),
            started_at=t0, completed_at=time.time(),
        ))
        return nid, result

    # ── synthetic planner node ─────────────────────────────────────────────
    planner_nid = nxt()
    planner_plan = {
        "rationale": (
            f"Flight comparison for {ORIGIN_CITY} ({ORIGIN_IATA}) -> "
            f"{DEST_CITY} ({DEST_IATA}), depart {DEPART_DATE}, return "
            f"{RETURN_DATE}. Try the major OTAs in order — Google "
            f"Flights, Kayak, Skyscanner, Momondo — using the vision "
            f"driver. Each is wrapped by the BrowserSkill four-layer "
            f"cascade (extract / deterministic / a11y / vision). The "
            f"first source that returns real flight rows feeds the "
            f"distiller. If every commercial OTA gates the headless "
            f"browser, fall back to the Wikipedia airport page (BLR), "
            f"which lists every carrier serving the BLR-LHR route and "
            f"is served as plain HTML so Layer 1 extracts it. The "
            f"distiller then pulls the three best flights and the "
            f"formatter renders a Markdown comparison table on price, "
            f"layover, and total travel time."
        ),
        "nodes": [
            {"skill": "browser",
             "metadata": {"label": "google_flights",
                          "url": GOOGLE_FLIGHTS_URL,
                          "goal": _GOOGLE_GOAL}},
            {"skill": "browser",
             "metadata": {"label": "kayak_fallback",
                          "url": KAYAK_URL, "goal": _KAYAK_GOAL,
                          "conditional": "if google blocked"}},
            {"skill": "browser",
             "metadata": {"label": "skyscanner_fallback",
                          "url": SKYSCANNER_URL, "goal": _SKYSCANNER_GOAL,
                          "conditional": "if kayak blocked"}},
            {"skill": "browser",
             "metadata": {"label": "momondo_fallback",
                          "url": MOMONDO_URL, "goal": _MOMONDO_GOAL,
                          "conditional": "if skyscanner blocked"}},
            {"skill": "browser",
             "metadata": {"label": "wikipedia_fallback",
                          "url": WIKIPEDIA_URL, "goal": _WIKIPEDIA_GOAL,
                          "conditional": "if all OTAs blocked"}},
            {"skill": "distiller", "metadata": {"label": "distiller"}},
            {"skill": "formatter", "metadata": {"label": "formatter"}},
        ],
    }
    store.write_node(NodeState(
        node_id=planner_nid, skill="planner", status="complete",
        inputs=["USER_QUERY"],
        result=AgentResult(
            success=True, agent_name="planner", output=planner_plan,
            elapsed_s=0.0,
        ),
        prompt_sent="(flight_runner.py — synthetic planner)",
        started_at=time.time(), completed_at=time.time(),
    ))
    print(f"[{planner_nid}] planner  (synthetic)")

    # ── stage 1: try sources in cost-then-likelihood order ────────────────
    sources: list[tuple[str, str, str, str, str, int]] = [
        # (label_for_print, tag,             url,             goal,            force_path, attempts)
        ("Google Flights",  "google_flights",  GOOGLE_FLIGHTS_URL, _GOOGLE_GOAL,    "vision", 2),
        ("Kayak",           "kayak_fallback",  KAYAK_URL,         _KAYAK_GOAL,     "vision", 2),
        ("Skyscanner",      "skyscanner_fb",   SKYSCANNER_URL,    _SKYSCANNER_GOAL,"vision", 2),
        ("Momondo",         "momondo_fb",      MOMONDO_URL,       _MOMONDO_GOAL,   "vision", 1),
        # Wikipedia: force_path="extract" is implicit — cheap Layer 1 picks it.
        ("Wikipedia (BLR)", "wikipedia_fb",    WIKIPEDIA_URL,     _WIKIPEDIA_GOAL, "a11y",   1),
    ]

    extracted_text = ""
    source_url = sources[0][2]
    source_label = sources[0][0]
    chosen_nid: str | None = None
    last_nid = planner_nid

    for label, tag, url, goal, force_path, attempts in sources:
        nid, result = await _browser(
            url, goal, inputs=[last_nid], label=tag,
            force_path=force_path, attempts=attempts,
        )
        last_nid = nid
        ok = result.success and bool((result.output or {}).get("content"))
        if ok:
            extracted_text = (result.output or {}).get("content") or ""
            source_url = url
            source_label = label
            chosen_nid = nid
            print(f"[{nid}] {label} succeeded — using its content "
                  f"({len(extracted_text)} chars)")
            break
        print(f"[{nid}] {label} unusable "
              f"(error_code={result.error_code}); trying next source")

    if chosen_nid is None:
        chosen_nid = last_nid
        print("[runner] every source failed; distiller will report the gap")

    # ── distiller ──────────────────────────────────────────────────────────
    distiller_nid = nxt()
    d_prompt = _distiller_prompt(extracted_text, source_url,
                                 source_label, query)
    t0 = time.time()
    d_reply = await asyncio.to_thread(
        LLM().chat,
        prompt=d_prompt, agent="distiller", session=sid,
        max_tokens=1500, temperature=0.1,
    )
    d_elapsed = time.time() - t0
    d_output = parse_skill_json(d_reply.get("text", ""))
    print(f"[{distiller_nid}] distiller  elapsed={d_elapsed:.1f}s  "
          f"fields={list((d_output.get('fields') or {}).keys())}")

    store.write_node(NodeState(
        node_id=distiller_nid, skill="distiller", status="complete",
        inputs=[chosen_nid],
        result=AgentResult(
            success=True, agent_name="distiller", output=d_output,
            elapsed_s=d_elapsed,
            provider=d_reply.get("provider", ""),
            cost=float(d_reply.get("cost", 0.0) or 0.0),
        ),
        prompt_sent=d_prompt,
        started_at=t0, completed_at=time.time(),
    ))

    # ── formatter ──────────────────────────────────────────────────────────
    formatter_nid = nxt()
    f_prompt = _formatter_prompt(d_output, distiller_nid, query)
    t0 = time.time()
    f_reply = await asyncio.to_thread(
        LLM().chat,
        prompt=f_prompt, agent="formatter", session=sid,
        max_tokens=1500, temperature=0.3,
    )
    f_elapsed = time.time() - t0
    f_output = parse_skill_json(f_reply.get("text", ""))
    print(f"[{formatter_nid}] formatter  elapsed={f_elapsed:.1f}s")

    store.write_node(NodeState(
        node_id=formatter_nid, skill="formatter", status="complete",
        inputs=[distiller_nid, "USER_QUERY"],
        result=AgentResult(
            success=True, agent_name="formatter", output=f_output,
            elapsed_s=f_elapsed,
            provider=f_reply.get("provider", ""),
            cost=float(f_reply.get("cost", 0.0) or 0.0),
        ),
        prompt_sent=f_prompt,
        started_at=t0, completed_at=time.time(),
    ))

    return sid, f_output.get("final_answer", str(f_output))


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    query = " ".join(args).strip() if args else _DEFAULT_QUERY

    sid, answer = asyncio.run(_run(query))

    print("\n" + "═" * 78)
    print("FINAL ANSWER")
    print("═" * 78)
    print(answer)
    print("═" * 78 + "\n")

    try:
        from html_replay import generate_html_report
        report_path = generate_html_report(sid)
        print(f"HTML report → {report_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[html_replay] report generation failed: {exc}", file=sys.stderr)
    print(f"\nSession ID for video: {sid}")
    print("Next step: uv run python make_demo_video.py")


if __name__ == "__main__":
    main()
