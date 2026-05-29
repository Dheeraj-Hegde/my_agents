# Earnings Call Analyst — RAG over agent7

A FastAPI dashboard and HTTP service that turns the Session 7 cognitive
agent (`agent7`) into a Retrieval‑Augmented‑Generation app. Documents are
ingested from a sandboxed corpus, chunked, embedded via the local
**llm_gatewayV7** embedding endpoint, and stored in a FAISS index. The
agent answers questions strictly from that index using the MCP
`search_knowledge` tool — web search and file tools are disabled on the
`/ask` path so answers are grounded in the indexed corpus only.

The default corpus is a collection of company earnings‑call transcripts
under [sandbox/earnings_call/](sandbox/earnings_call), but you can point
it at any subdirectory of [sandbox/](sandbox) (e.g. [sandbox/papers/](sandbox/papers)).

---

## Architecture

```
┌─────────────────┐    HTTP     ┌──────────────────────┐
│  Browser UI     │ ──────────▶ │  rag_app.py (FastAPI)│
│  dashboard.html │             │  /ingest /ask /...   │
└─────────────────┘             └──────────┬───────────┘
                                           │ in‑proc
                                           ▼
                                ┌──────────────────────┐
                                │  agent7.run()        │
                                │  memory → perception │
                                │  → decision → action │
                                └──────────┬───────────┘
                                           │ stdio MCP
                                           ▼
                                ┌──────────────────────┐
                                │  mcp_server.py       │
                                │  index_document      │
                                │  search_knowledge    │
                                │  read_file, ...      │
                                └──────────┬───────────┘
                                           │
                       ┌───────────────────┼───────────────────┐
                       ▼                   ▼                   ▼
              ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
              │ memory.py      │  │ vector_index   │  │ llm_gatewayV7  │
              │ memory.json    │  │ FAISS IndexFlat│  │ /v1/embed      │
              │ (facts)        │  │ state/*.faiss  │  │ /v1/chat       │
              └────────────────┘  └────────────────┘  └────────────────┘
```

Key pieces:

- [rag_app.py](rag_app.py) — FastAPI app + the dashboard. Serializes
  agent runs with an asyncio lock because FAISS and `memory.json` are
  single‑writer. On `/ask` it injects an instruction that forbids the
  agent from using web/file tools and forces it through
  `search_knowledge`.
- [agent7.py](agent7.py) — Cognitive loop:
  `memory.read → perception.observe → decision.next_step → action.execute → memory.record_outcome`.
- [mcp_server.py](mcp_server.py) — MCP tool server (stdio). Exposes
  `index_document`, `search_knowledge`, plus the standard file/web
  tools.
- [memory.py](memory.py) — Typed memory service. Embeddable kinds
  (`fact`, `preference`, `tool_outcome`) are vectorised at write time.
  `vector_search_scored()` returns cosine‑similarity scores so callers
  can apply a relevance threshold.
- [vector_index.py](vector_index.py) — `faiss.IndexFlatIP` on
  L2‑normalised vectors (cosine similarity), persisted to
  [state/index.faiss](state/index.faiss) and
  [state/index_ids.json](state/index_ids.json).
- [gateway.py](gateway.py) — Auto‑starts
  [llm_gatewayV7/main.py](llm_gatewayV7/main.py) on port 8101 and
  re‑exports `embed()` / `embed_many()` / the `LLM` chat client.

### Indexing pipeline

1. `POST /ingest` walks `sandbox/<root>` (or uses an explicit `files`
   list) and calls `mcp_server.index_document(path)` directly per file.
2. `index_document` reads the file, splits it with sliding‑window
   chunking (default `chunk_size=400` words, `overlap=80`), then calls
   `memory.add_facts_batch(...)`.
3. `add_facts_batch` sends every chunk to `/v1/embed_batch` in **one**
   gateway round‑trip (important — per‑chunk calls would hit
   Gemini free‑tier cooldowns), gets back 768‑dim vectors, appends them
   to FAISS, and writes the fact records to
   [state/memory.json](state/memory.json).

### Retrieval pipeline

1. `POST /ask` wraps the question in a strict RAG prompt and calls
   `agent7.run(question, exclude_tools={web_search, fetch_url, read_file, ...})`.
2. The agent calls the MCP tool `search_knowledge(query, k)`.
3. `search_knowledge` embeds the query with `task_type="retrieval_query"`,
   does FAISS top‑K, filters by `SEARCH_MIN_SCORE` (default `0.5`
   cosine), and returns ranked chunks with `similarity`, `descriptor`,
   `source`, and `chunk` text.
4. The agent answers only from those chunks. If nothing passes the
   threshold, it returns the exact string
   `"I could not find any details about that in the knowledge base."`.

---

## Prerequisites

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (the project uses `uv run`)
- A built `llm_gatewayV7/` sibling directory (already present here).
  The gateway needs API keys for whichever provider it routes to.
- Optional: `TAVILY_API_KEY` for the (unused on `/ask`) web search tool.

Place provider keys (e.g. `GEMINI_API_KEY`) in
`llm_gatewayV7/.env` per that gateway's README, and any agent‑side
keys (Tavily etc.) in `first_rag/.env`.

Install dependencies:

```powershell
uv sync
```

---

## How to use

### 1. Start the service

```powershell
uv run python rag_app.py
```

This boots FastAPI on `http://127.0.0.1:8200` and warms the gateway
(launches `llm_gatewayV7` on port 8101 if it isn't already up — first
boot can take ~30–45 s while uv resolves the gateway venv).

For autoreload during development:

```powershell
uv run uvicorn rag_app:app --host 127.0.0.1 --port 8200 --reload
```

Optional: auto‑ingest the default `earnings_call` corpus on startup
(skipped when every file is already indexed):

```powershell
$env:RAG_PRELOAD = "1"; uv run python rag_app.py
```

### 2. Open the dashboard

Browse to <http://127.0.0.1:8200>. The UI ([static/dashboard.html](static/dashboard.html))
shows:

- Gateway health and current index size.
- A sandbox file browser with per‑file indexed/un‑indexed badges,
  inline previews, and a multi‑select "Index selected" action.
- An **Ask** box that hits `POST /ask` and renders the agent's answer
  plus a live log of each call.

Typical first‑run flow:

1. In the file browser, pick `earnings_call` from the root selector
   (or any other subdirectory of `sandbox/`).
2. Tick the files you want, click **Index selected**, and wait for the
   "indexed N chunks" toast.
3. Type a question in the Ask box, e.g.
   *"What did NVIDIA say about data‑centre revenue in Q4 FY26?"*.

### 3. Use the HTTP API directly

All endpoints accept/return JSON.

```powershell
# Service status
curl http://127.0.0.1:8200/status

# Gateway health
curl http://127.0.0.1:8200/health

# List indexable files
curl "http://127.0.0.1:8200/sandbox/files?root=earnings_call&extensions=.md,.txt"

# Preview a single file (first 8000 bytes)
curl "http://127.0.0.1:8200/sandbox/file?path=earnings_call/nvdia_q426.md"

# Ingest a whole subdirectory
curl -X POST http://127.0.0.1:8200/ingest `
     -H "Content-Type: application/json" `
     -d '{"root":"earnings_call","extensions":[".md",".txt"]}'

# Ingest an explicit file list
curl -X POST http://127.0.0.1:8200/ingest `
     -H "Content-Type: application/json" `
     -d '{"files":["earnings_call/nvdia_q426.md","earnings_call/meta_q126.md"]}'

# Ask a question (RAG-only — web tools are disabled here)
curl -X POST http://127.0.0.1:8200/ask `
     -H "Content-Type: application/json" `
     -d '{"question":"How did Microsoft describe Azure AI demand?"}'
```

`/ask` responds with `{"answer": "..."}`. If the indexed corpus does
not cover the topic, the answer is the literal
`"I could not find any details about that in the knowledge base."`.

### 4. Driving the agent directly (no HTTP)

You can run a single query through the same agent that powers `/ask`:

```powershell
uv run python agent7.py "Summarise Tesla's Q4 2025 capex commentary"
```

This bypasses the FastAPI lock and the RAG‑only prompt wrapper, so the
agent is free to use web search / file tools as well.

### 5. Indexing your own corpus

1. Drop files into `sandbox/<your_folder>/` (any `.md` or `.txt`;
   extensions are configurable per‑request).
2. POST to `/ingest` with `{"root": "<your_folder>"}` or pass an
   explicit `files` list.
3. Confirm with `GET /status` — `index.size` should grow by the number
   of chunks.

Paths are validated to stay inside `sandbox/`; any attempt to escape
returns HTTP 400.

---

## Configuration

Environment variables read at runtime:

| Variable            | Default | Effect |
|---------------------|---------|--------|
| `RAG_PRELOAD`       | `0`     | When `1`, ingest the default `earnings_call` corpus on startup. Skipped if every file is already indexed. |
| `SEARCH_MIN_SCORE`  | `0.5`   | Cosine threshold for `search_knowledge`. Chunks below this score are dropped before the agent sees them. Raise if you see tangential answers, lower if relevant chunks are being filtered out. |
| `TAVILY_API_KEY`    | unset   | Enables the `web_search` MCP tool (not used by `/ask`). |
| `GEMINI_API_KEY` / other provider keys | unset | Consumed by `llm_gatewayV7`. See [llm_gatewayV7/README.md](llm_gatewayV7/README.md). |

The chunker defaults (`chunk_size=400`, `overlap=80` words) can be
overridden per call if you invoke `index_document` directly via the MCP
tool — `/ingest` uses the defaults.

The embedding model is **fixed at the gateway level**. Changing it
invalidates every vector in [state/index.faiss](state/index.faiss);
delete `state/index.faiss` and `state/index_ids.json` and re‑ingest if
you swap models.

---

## State and reset

Everything mutable lives under [state/](state):

- `state/index.faiss` — binary FAISS index.
- `state/index_ids.json` — parallel id list mapping FAISS rows to
  `MemoryItem.id`.
- `state/memory.json` — the full Memory service store (facts,
  preferences, tool outcomes, scratchpad).
- `state/artifacts/` — opaque byte blobs referenced by id.

To wipe the index and start over:

```powershell
Remove-Item state\index.faiss, state\index_ids.json, state\memory.json -ErrorAction SilentlyContinue
```

(`memory.json.bak` is a manual backup — leave or delete as you wish.)

---

## Tests

```powershell
uv run pytest
```

Markers in [pyproject.toml](pyproject.toml):

- `network` — requires internet (Tavily/DDG/crawl4ai).
- `embed` — requires the gateway `/v1/embed` endpoint to be reachable.

Skip them with `-m "not network and not embed"` for an offline run.

---

## Troubleshooting

- **`Gateway V7 failed to start within 45s`** — run
  `uv run python llm_gatewayV7/main.py` manually in another terminal to
  see provider/auth errors. The agent only retries the boot once per
  process.
- **Answer is always *"I could not find any details..."*** — check
  `GET /status`; if `index.size == 0` you haven't ingested. If it's
  populated, try lowering `SEARCH_MIN_SCORE` (e.g. `0.4`).
- **Mid‑run `'charmap' codec` error on Windows** — both `agent7.py` and
  `mcp_server.py` already force UTF‑8 on stdout/stderr. If you see it
  anyway, you're running a wrapper that captured the streams before
  those reconfigure calls; launch via `uv run` (or `python -X utf8`).
- **`Embedding dim X does not match index dim Y`** — the embedding
  model changed under a populated index. Delete the `state/index.*`
  files and re‑ingest (see *State and reset*).
- **Stale chunks after editing a sandbox file** — re‑ingesting appends
  new chunks; it does **not** evict old ones. For a clean re‑index of
  one corpus, wipe state and re‑run `/ingest`.

---

## Corpus manifest

Everything ingestible lives under [sandbox/](sandbox). Path validation in
both `mcp_server._safe()` and `rag_app._walk_sandbox()` blocks any access
outside this root.

### `sandbox/` (top-level scratch files)

| File | Purpose |
|------|---------|
| [moms_birthday.txt](sandbox/moms_birthday.txt) | Seed fact used by the persistence traces (`03a` / `03b`). |
| [reminder_1_may_2026.txt](sandbox/reminder_1_may_2026.txt) | Calendar reminder written by trace `03a`. |
| [reminder_15_may_2026.txt](sandbox/reminder_15_may_2026.txt) | Calendar reminder written by trace `03a`. |

### `sandbox/papers/` — research-paper corpus (5 docs)

Used by traces `06a`, `06b`, `07`, `08`. Each file produces 3 chunks at
the default `chunk_size=400 / overlap=80`, for **15 vectors total**.

| File | Topic |
|------|-------|
| [papers/attention.md](sandbox/papers/attention.md) | Vaswani et al., *Attention Is All You Need* (Transformer architecture). |
| [papers/cot.md](sandbox/papers/cot.md) | Wei et al., *Chain-of-Thought Prompting Elicits Reasoning in LLMs*. |
| [papers/react.md](sandbox/papers/react.md) | Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*. |
| [papers/lora.md](sandbox/papers/lora.md) | Hu et al., *LoRA: Low-Rank Adaptation of Large Language Models*. |
| [papers/dpo.md](sandbox/papers/dpo.md) | Rafailov et al., *Direct Preference Optimization*. |

### `sandbox/earnings_call/` — earnings-call corpus (51 transcripts)

The default corpus for the RAG dashboard and the five `rag_*` traces.

| Sector | Files |
|--------|-------|
| Semis / AI infra | `nvdia_q226.md`, `nvdia_q326.md`, `nvdia_q426.md`, `nvdia_q127.md` |
| Cloud / hyperscalers | `alphabet_q325.md`, `amazon_q425.md`, `amazon_q126.md`, `microsoft_q226.md`, `microsoft_q326.md`, `meta_q126.md`, `oracle_q425.md`, `oracle_q126.md`, `oracle_q226.md`, `oracle_q326.md`, `servicenow_q126.md`, `infosys_q326.md` |
| Energy | `bp_q126.md`, `bp_q225.md`, `bp_q325.md`, `bp_q425.md`, `shell_q126.md`, `shell_q325.md`, `shell_q425.md`, `phillips_q126.md`, `phillips_q425.md` |
| Financials | `jpmc_q126.md`, `goldmansachs_q126.md`, `goldmansachs_q425.md`, `barclays_q126.md`, `barclays_q425.md`, `hsbc_q126.md`, `hsbc_q225.md`, `hsbc_q325.md`, `hsbc_q425.md`, `standardchartard_q225.md`, `standardchartard_q325.md`, `standardchartard_q425.md` |
| Consumer / staples | `unilever_q225.md`, `unilever_q325.md`, `unilever_q425.md`, `flutter_q126.md`, `flutter_q425.md` |
| Telecom | `at&t_q126.md`, `at&t_q325.md`, `at&t_q425.md` |
| Auto / industrials | `tesla_q325.md`, `tesla_q425.md`, `rollsroyce_q425.md` |
| Aerospace | `virgingalactic_q325.md`, `virgingalactic_q425.md` |

---

## Base traces (8) — full agent7, all tools enabled

Captured under [screenshot/captures/](screenshot/captures) (`01.txt`–`08.txt`).
These exercise the unconstrained agent loop (web search, fetch_url, file
tools, MCP indexing, plus the new vector tools). They are the
provenance for the RAG-only behaviour the dashboard adds on top.

| # | Capture | Query (verbatim) | What it exercises |
|---|---------|------------------|-------------------|
| 1 | [01.txt](screenshot/captures/01.txt) | "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory." | `fetch_url` (crawl4ai) → 1-shot synthesis from an artifact. |
| 2 | [02.txt](screenshot/captures/02.txt) | "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate." | Multi-step `web_search` chained with conditional reasoning. |
| 3a | [03a.txt](screenshot/captures/03a.txt) | "My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder for two weeks before and on the day." | Memory write + `create_file` (idempotent — second run hits "already exists"). |
| 3b | [03b.txt](screenshot/captures/03b.txt) | "When is mom's birthday?" | Cross-run memory recall — answered entirely from vector hits, no tool calls. |
| 4 | [04.txt](screenshot/captures/04.txt) | "Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on." | `web_search` → 3× `fetch_url` → cross-source synthesis (5 goals). |
| 5 | — | (folded into 06a / 06b) | — |
| 6a | [06a.txt](screenshot/captures/06a.txt) | "Index the file papers/attention.md and tell me what the three key contributions of the Transformer architecture are according to this paper." | `index_document` (single file, 3 chunks) → `search_knowledge` → answer. |
| 6b | [06b.txt](screenshot/captures/06b.txt) | "Across the papers I have indexed, what do they say about chain-of-thought reasoning?" | Pure vector recall against the freshly-indexed papers corpus (no new tool calls). |
| 7 | [07.txt](screenshot/captures/07.txt) | "Index every .md file under papers/. Confirm how many chunks were indexed in total." | `list_dir` → 5× `index_document` (15 chunks). |
| 8 | [08.txt](screenshot/captures/08.txt) | "Across these papers, how do they handle the credit assignment problem?" | Cross-document synthesis: 5 sequential `search_knowledge` calls, one per paper, then a compare-and-contrast answer. |

(Capture `05.txt` exists for sequence parity but the substantive
indexing demo is in `06a`/`06b`; treat the indexing pair as trace 5+6.)

---

## Custom traces (5) — RAG-only over `earnings_call`, with no-corpus comparison

Captured under [screenshot/captures/rag_*.txt](screenshot/captures). Each
was run via `agent7.run(…, exclude_tools={web_search, fetch_url, read_file, list_dir, create_file, update_file, edit_file, index_document})`
— exactly the tool set the `/ask` endpoint exposes. Only 3 MCP tools are
visible to the agent: `get_time`, `currency_convert`, `search_knowledge`.

The **no-corpus comparison** is the contractual fallback enforced by the
RAG prompt wrapper in [rag_app.py](rag_app.py): when
`search_knowledge` returns no chunks above `SEARCH_MIN_SCORE` (default
`0.5`), the agent is instructed to reply with the literal string
`"I could not find any details about that in the knowledge base."` and
stop. Wipe `state/index.faiss` + `state/index_ids.json` (or skip the
ingest step) to reproduce the no-corpus column for any of these
queries.

| # | Capture | Question | Answer with corpus indexed | Answer with **no** corpus indexed |
|---|---------|----------|----------------------------|-----------------------------------|
| 1 | [rag_01.txt](screenshot/captures/rag_01.txt) | "How much Blackwell and Rubin revenue does NVIDIA have visibility to through the end of calendar year 2026?" | "Visibility into **half a trillion dollars** in combined Blackwell + Rubin revenue from the start of the current year through the duration of these product cycles." (sourced from `nvdia_q326.md` / `nvdia_q426.md`) | `"I could not find any details about that in the knowledge base."` |
| 2 | [rag_02.txt](screenshot/captures/rag_02.txt) | "Which oil major suspended its share buyback program to strengthen its balance sheet, and what is its new net debt target range?" | **BP** suspended buybacks to redirect cash to the balance sheet; new net-debt target **$14B–$18B** by end of 2027 (from `bp_q425.md`). | `"I could not find any details about that in the knowledge base."` |
| 3 | [rag_03.txt](screenshot/captures/rag_03.txt) | "Which NVIDIA accelerator is designed for long-context workloads where the model must absorb PDFs, video, or 3D images before generation?" | **GB300** — top chunk at cosine `0.6338` from `nvdia_q326.md`; ~⅔ of Blackwell revenue this quarter. | `"I could not find any details about that in the knowledge base."` |
| 4 | [rag_04.txt](screenshot/captures/rag_04.txt) | "How big is the new Rolls-Royce multi-year share buyback program for the period 2026 to 2028?" | First multi-year buyback: **£7B–£9B** over 2026–2028; £2.5B in 2026 (incl. £200M interim tranche already complete). Top chunk cosine `0.7032` from `rollsroyce_q425.md`. | `"I could not find any details about that in the knowledge base."` |
| 5 | [rag_05.txt](screenshot/captures/rag_05.txt) | "Why is Tesla converting the Model S and X production space at its Fremont factory into an Optimus robot factory?" | To scale Optimus toward **1M units/year at Fremont**; Musk frames Optimus as a general-purpose robot learning from human observation/video, with claimed GDP impact (from `tesla_q425.md`). | `"I could not find any details about that in the knowledge base."` |

Why the no-corpus column is uniformly the fallback string (rather than
hallucinated guesses): the `/ask` prompt explicitly instructs the agent
that "a chunk being returned does NOT mean it answers the question",
that it must read the `chunk` text and check it explicitly mentions the
subject, and that on empty/irrelevant retrieval it must emit the exact
fallback string and stop. Without the index populated,
`search_knowledge` returns `[]` on every call, triggering that branch
deterministically. This is by design — the RAG path trades fluency on
out-of-corpus questions for groundedness on in-corpus ones.
