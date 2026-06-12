# Browser-Capable Comparison Agent

An autonomous agent that drives a real browser to complete comparison
tasks on the live web and produces, for every run, a structured
comparison table plus a self-contained replay viewer.

All eight sections are emitted by [html_replay.py](html_replay.py) into a
single offline-viewable file at
[output/s8-58cb175c/output/report.html](output/s8-58cb175c/output/report.html),
and narrated by [make_demo_video.py](make_demo_video.py) into
[output/s8-58cb175c/output/demo.mp4](output/s8-58cb175c/output/demo.mp4).

---

## Reference task

> Compare flights from **Bengaluru (BLR) → London (LHR)**, depart
> **25 June 2026**, return **30 June 2026**. Extract the top three
> options and compare them on **price, layover and total travel time**.

Example final comparison table (session `s8-58cb175c`):

| Rank | Airline | Price | Layover | Total Travel Time |
|---|---|---|---|---|
| 1 | Etihad | £536 | AUH | 12 hr 40 min |
| 2 | Etihad | £539 | AUH | 13 hr 15 min |
| 3 | British Airways | £706 | Nonstop | 10 hr 55 min |

---

## The eight report sections

Values below are from the reference run **`s8-58cb175c`** (12 June 2026).
Every link points to the actual file on disk.

| # | Section | Value (from `s8-58cb175c`) | Source file |
|---|---|---|---|
| 1 | **Original user goal** | "Compare flights from Bengaluru (BLR) to London (LHR), departing 25 June 2026 and returning 30 June 2026 … Read the top three flight cards … Flight numbers are NOT required." | [output/s8-58cb175c/query.txt](output/s8-58cb175c/query.txt) |
| 2 | **Planner DAG** | 9 nodes / 7 edges — `n:1 planner → n:2 browser` (failed, Playwright context destroyed) → recovery → `n:5 planner → n:6 browser → n:7 distiller → n:9 critic (pass) → n:8 formatter` | [output/s8-58cb175c/graph.json](output/s8-58cb175c/graph.json) |
| 3 | **Browser path chosen** | `vision` (L3 — Playwright + V9 `/v1/vision` with SoM markers, on `www.google.com/travel/flights`) | [output/s8-58cb175c/nodes/n_006.json](output/s8-58cb175c/nodes/n_006.json) |
| 4 | **Browser actions taken** | 4 turns — `turn 1: scroll down [3]` → `turn 2: click [2]` → `turn 3: scroll down [0]` → `turn 4: done` (outcome `done(True)`) | [output/s8-58cb175c/nodes/n_006.json](output/s8-58cb175c/nodes/n_006.json) |
| 5 | **Screenshots / page-state logs** | 4 marked frames + 4 raw frames + 4 SoM snapshots | [output/s8-58cb175c/browser/browser_1781259339/vision/](output/s8-58cb175c/browser/browser_1781259339/vision/) ([turn_01_marked.png](output/s8-58cb175c/browser/browser_1781259339/vision/turn_01_marked.png), [turn_04_marked.png](output/s8-58cb175c/browser/browser_1781259339/vision/turn_04_marked.png)) |
| 6 | **Extracted data** | `flight_1` Etihad £536 / 1 stop AUH / 12h 40m  ·  `flight_2` Etihad £539 / 1 stop AUH / 13h 15m  ·  `flight_3` British Airways £706 / Nonstop / 10h 55m | [output/s8-58cb175c/nodes/n_007.json](output/s8-58cb175c/nodes/n_007.json) |
| 7 | **Final comparison table** | See table below (`final_answer` from formatter `n:8`) | [output/s8-58cb175c/nodes/n_008.json](output/s8-58cb175c/nodes/n_008.json) |
| 8 | **Turn count and cost summary** | 4 browser turns  ·  total LLM cost **$0.001078** (browser/vision only; planner, distiller, critic, formatter all $0 on Groq) | [output/s8-58cb175c/output/report.html](output/s8-58cb175c/output/report.html) |

**Final comparison table (§ 7) verbatim:**

| Rank | Airline | Price | Layover | Total Travel Time |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Etihad | £536 | AUH | 12 hr 40 min |
| 2 | Etihad | £539 | AUH | 13 hr 15 min |
| 3 | British Airways | £706 | Nonstop | 10 hr 55 min |

> **Recommendation:** If you prioritize convenience and speed over cost, the British Airways flight is the best choice as it is the only nonstop option and offers the shortest travel time.

Full replay viewer: [output/s8-58cb175c/output/report.html](output/s8-58cb175c/output/report.html).

---

## Architecture

```
USER_QUERY -> Planner -> Browser -> Distiller -> Critic -> Formatter
                ^                                   |
                +-------- fail -> recovery ---------+
```

The orchestrator ([flow.py](flow.py)) builds a NetworkX DiGraph of skill
nodes. When a `critic: true` skill (Distiller) completes, every outgoing
edge is automatically gated by a **Critic node** — `pass` lets the child
run, `fail` triggers a recovery planner that adds an alternate branch.

Browser nodes run a **4-layer cascade** ([browser/skill.py](browser/skill.py)):

| Layer | Engine | Cost | Use case |
|---|---|---|---|
| L1 **extract**       | `httpx` + `trafilatura`            | free   | static pages, article bodies |
| L2a **deterministic** | Playwright + caller-supplied CSS  | free   | known forms |
| L2b **a11y**         | Playwright + V9 `/v1/chat`         | text   | dynamic but a11y-tree friendly |
| L3 **vision**        | Playwright + V9 `/v1/vision` (SoM) | vision | JS-heavy SPAs (Google Flights) |

A layer escalates only when the prior one returns nothing useful, the
goal requires interaction (`click`, `fill`, `select`, …), or the page is
a known interstitial (Google consent, cookie wall, sign-in redirect).
When a CAPTCHA / Cloudflare / login-wall marker is detected, the cascade
returns `path: blocked` instead of escalating further.

---

## Repository layout

```
flow.py                # orchestrator (Executor.run)
skills.py              # skill registry + per-skill dispatcher
agent_config.yaml      # skill defs, critic flags, internal_successors
prompts/*.md           # one prompt per skill
browser/               # 4-layer browser skill
  client.py            # V9 gateway client
  skill.py             # cascade entry point
  driver.py            # Playwright drivers (a11y + vision)
  dom.py, highlight.py # SoM marker injection
llm_gatewayV9/         # auto-launched LLM gateway (port 8109)
recovery.py            # plan_recovery + handle_critic_verdict
persistence.py         # per-node JSON + screenshot writers
html_replay.py         # 8-section self-contained HTML report
make_demo_video.py     # narrated MP4 with click overlays
state/sessions/<sid>/  # one folder per run
  query.txt
  graph.json
  nodes/n_*.json
  browser/browser_n_<nid>/{a11y,vision}/turn_NN_{raw,marked}.png
  output/{report.html,demo.mp4}
```

---

## Running

```powershell
# Install deps once
uv sync

# Set encoding so the box-drawing log lines don't crash on Windows
$env:PYTHONIOENCODING = "utf-8"

# End-to-end orchestrator run (gateway auto-launches on port 8109)
uv run python flow.py "Today is 12 June 2026. Compare flights from Bengaluru (BLR) to London (LHR), depart 25 June 2026, return 30 June 2026. Use deep-link https://www.google.com/travel/flights?q=Flights+from+Bengaluru+to+London+on+2026-06-25+through+2026-06-30 . Read the top 3 results: airline, price, layover, total travel time. Flight numbers are NOT required. Return a Markdown table sorted cheapest first."

# Generate the 8-section report + narrated video for the reference run
uv run python html_replay.py     s8-58cb175c
uv run python make_demo_video.py s8-58cb175c
```

The orchestrator prints the session id on the first line. Reports and
videos land in `state/sessions/<that-id>/output/`.

---

## Replay viewer (`report.html`)

`html_replay.py` emits a **single self-contained HTML file** with all
eight sections inlined (base64 screenshots, no external assets, opens
offline). Each section in the file uses the exact wording above so the
mapping back to the spec is direct.

---

## Narrated demo video

**▶ Watch the demo:** [output/s8-58cb175c/output/demo.mp4](output/s8-58cb175c/output/demo.mp4)

---

## Validation

`tests/` contains:

- `test_critic_autoinsert.py` — proves the orchestrator auto-inserts a critic on every edge out of a `critic: true` skill.
- `test_recovery.py` / `test_recovery_amnesia.py` — verify `plan_recovery` rewires the DAG correctly on failure.
- `test_natural_vision_search.py` — end-to-end vision-driver smoke.

Manual deliverables checklist is in [VALIDATION.md](VALIDATION.md).
