# first_multi_agent — EAG v3 Session 8

A growing-graph multi-agent orchestrator. The agent's loop is a
[NetworkX](https://networkx.org/) `DiGraph` whose nodes are *skills*
(planner, researcher, distiller, critic, coder, translator, etc.) and
whose edges carry typed `AgentResult` payloads between them. The graph
is not fixed ahead of time — it **grows at runtime** as the Planner
decomposes the user query, individual skills emit successors, and the
Critic auto-inserts validation nodes on every outgoing edge from skills
marked `critic: true`.

This is the Session 8 evolution of the earlier four-role architecture
(perception → decision → action → memory) from Session 7, lifted into a
DAG executor with parallel fan-out, critic-fail recovery, sandbox code
execution, and persistent session replay.

---

## Architecture

```
USER_QUERY
    │
    ▼
[planner]──┬──►[researcher]──►[distiller]──►[critic]──►[formatter]──►FINAL
           │                                    │
           │                                    └──fail──►[planner (recovery)]
           ▼
       [coder]──►[sandbox_executor]──►[formatter]
```

Five actors can extend the graph:

1. **Planner seed plan** — the initial decomposition of the user query.
2. **Dynamic successors** — any skill can return `successors=[…]` in its
   `AgentResult` to splice in new downstream work.
3. **Static `internal_successors`** — declared in [agent_config.yaml](agent_config.yaml);
   e.g. every `coder` node is automatically followed by `sandbox_executor`.
4. **Critic auto-insertion** — for skills with `critic: true` (the
   Distiller), a `critic` node is inserted on every outgoing edge.
5. **Recovery Planner** — on critic-fail or hard error,
   [recovery.py](recovery.py) decides whether to replan a subgraph or skip.

A hard cap (`MAX_NODES = 60` in [flow.py](flow.py#L23)) prevents
runaway graph growth.

### Skill catalogue

Each skill is two files: an entry in [agent_config.yaml](agent_config.yaml)
plus a Markdown system prompt under [prompts/](prompts/). There is no
Python class per skill — behaviour is driven entirely by the prompt and
the per-skill yaml settings (`tools_allowed`, `temperature`,
`max_tokens`, `provider_pin`, `critic`, `internal_successors`).

| skill              | role                                                                |
| ------------------ | ------------------------------------------------------------------- |
| `planner`          | Decomposes queries into the initial DAG; also synthesises recovery. |
| `retriever`        | FAISS + Memory lookup for relevant facts.                           |
| `researcher`       | Multi-step web research via `web_search` + `fetch_url`.             |
| `distiller`        | Extracts structured fields from raw text. Wrapped by Critic.        |
| `summariser`       | Condenses long content.                                             |
| `critic`           | Pass/fail verdict on an upstream node (deterministic, temp = 0).    |
| `formatter`        | Renders the final user-facing answer (terminal node).               |
| `coder`            | Emits Python code; orchestrator auto-pipes to `sandbox_executor`.   |
| `sandbox_executor` | Runs code from the `coder` node in [sandbox.py](sandbox.py).        |
| `translator`       | Translates text to a single target language per node.               |
| `browser`          | Reserved (Session 9 stub).                                          |

### Components

| file                        | purpose                                                          |
| --------------------------- | ---------------------------------------------------------------- |
| [flow.py](flow.py)          | Graph + Executor. Async fan-out loop, critic insertion, replan.  |
| [skills.py](skills.py)      | Skill registry, prompt rendering, gateway dispatch.              |
| [mcp_runner.py](mcp_runner.py) | Tool-use loop wrapper for skills with `tools_allowed`.        |
| [mcp_server.py](mcp_server.py) | MCP tools: `web_search`, `fetch_url`, file I/O, `search_knowledge`. |
| [recovery.py](recovery.py)  | Critic-fail and hard-fail recovery policies.                     |
| [sandbox.py](sandbox.py)    | Subprocess-isolated Python runner with CPU/mem/time caps.        |
| [memory.py](memory.py)      | Long-term memory (carried over from S7).                         |
| [vector_index.py](vector_index.py) | FAISS index for `search_knowledge`.                       |
| [persistence.py](persistence.py) | Per-session NodeState/graph snapshots under [state/](state/). |
| [replay.py](replay.py)      | Step through any persisted run, one node at a time.              |
| [gateway.py](gateway.py)    | Launches and talks to the local `llm_gatewayV8` in [gateway/](gateway/). |

---

## Running

```powershell
# from first_multi_agent/
uv sync
uv run python flow.py "your query here"
```

The gateway in [gateway/](gateway/) is started automatically by
`ensure_gateway()` on first call (port 8108).

Replay a previous session:

```powershell
uv run python replay.py <session_id>
```

---

## Run logs

All transcripts below live in [run_logs/](run_logs/). Each shows the
session id, the per-node timing line, and the final answer. Files
written by PowerShell `Tee-Object` are UTF-16; open them in any
modern editor.

### Smoke + research suite (Q1–Q5)

| # | query                                                                                                     | log                                                                                       | result summary                                            |
| - | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| 1 | `hello`                                                                                                   | [q1_hello_20260531-112558.log](run_logs/q1_hello_20260531-112558.log)                     | trivial path: planner → formatter (27.8s).                |
| 2 | Fetch Claude Shannon Wikipedia: birth/death dates + three key contributions.                              | [q2_shannon_20260531-112634.log](run_logs/q2_shannon_20260531-112634.log)                 | researcher + formatter; answers all three facts (62.7s).  |
| 3 | Populations of London/Paris/Berlin — which two are closest?                                               | [q3_cities_20260531-112745.log](run_logs/q3_cities_20260531-112745.log)                   | 3-way parallel researcher fan-out → formatter (66s).      |
| 4 | Read `/nonexistent/path.txt` — error path.                                                                | [q4_nonexistent_20260531-112859.log](run_logs/q4_nonexistent_20260531-112859.log)         | planner refuses the impossible read cleanly (36.5s).      |
| 5 | Populations + growth rates of Lagos/Cairo/Kinshasa — fastest-growing?                                     | [q5_africa_20260531-112943.log](run_logs/q5_africa_20260531-112943.log)                   | 3 parallel researchers; correctly picks Kinshasa (86.6s). |

### Coder + sandbox (Q6, Q8)

| # | query                                            | log                                                                       | result                                              |
| - | ------------------------------------------------ | ------------------------------------------------------------------------- | --------------------------------------------------- |
| 6 | `What is 173! mod (10**9 + 7)?`                  | [q6_coder_20260602-193557.log](run_logs/q6_coder_20260602-193557.log)     | `173! mod (10^9 + 7) = 89,271,551`. Code ran in the sandbox in 0.2s. |
| 8 | `What is the sum of the first 67432 primes?`     | [q8_coder_20260604-212625.log](run_logs/q8_coder_20260604-212625.log)     | `27,332,806,411` (sandbox: 0.5s).                   |

### Translator (Q7)

| # | query                                                                                       | log                                                                                | result                                                 |
| - | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 7 | Translate *"Knowledge is power, but enthusiasm pulls the switch."* into FR / ES / DE.       | [q7_translator_20260604-214230.log](run_logs/q7_translator_20260604-214230.log)    | 3 parallel translator nodes → formatter. FR/ES/DE all rendered. |

### Critic recovery demos

Two paired runs each — RUN A is clean (Critic should `pass`); RUN B
poisons the first Distiller output (Critic should `fail` and trigger a
recovery Planner that re-runs the subgraph).

- [critic_demo_20260531-140656.log](run_logs/critic_demo_20260531-140656.log)
  — Alan Turing: `birth_date`, `death_date`, `doctoral_advisor`,
  `paper_1936_title`. RUN B's poison strips two fields, Critic fails,
  recovery Planner re-runs researcher + distiller, final answer is
  complete.
- [critic_demo_20260531-145309.log](run_logs/critic_demo_20260531-145309.log)
  — same shape but with `birth_place` + `nationality`. Same
  fail → replan → pass flow.

### Parallel fan-out verification

- [verify_parallel_20260531-130239.log](run_logs/verify_parallel_20260531-130239.log)
  — three independent facts (boiling point of water, speed of light,
  height of Everest). The log ends with a `LAYER WALL-CLOCK REPORT`
  proving that layer 2's three researchers ran in parallel:
  `wall = 33.34s` vs serial-sum `72.52s`, max branch `32.28s` →
  **PASS — parallel fan-out verified**.

## Configuration

Per-skill knobs live in [agent_config.yaml](agent_config.yaml). For
example, the Critic is pinned to temperature 0 for determinism and the
Researcher to 0.7 for exploratory phrasing. Add a new skill by appending
a yaml entry and dropping a matching `prompts/<name>.md`.

Routing of providers is layered: [gateway/agent_routing.yaml](gateway/agent_routing.yaml)
takes precedence over any `provider_pin` set in the skill catalogue.
