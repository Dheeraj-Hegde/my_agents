"""FastAPI RAG service on top of agent7.

Thin HTTP wrapper around `agent7.run()`. Every endpoint that mutates state
(ingest, ask) holds a single module-level asyncio lock because the
underlying VectorIndex and `state/memory.json` writers are not
concurrency-safe. Read-only endpoints (`/status`, `/health`) skip the lock.

Endpoints:
  POST /ingest   — walk `sandbox/<root>` (default `.`) for files matching
                   the given extensions, then drive agent7 to call the MCP
                   `index_document` tool once per file. Embeddings go
                   through the gateway's /v1/embed endpoint (768-dim).
  POST /ask      — `agent7.run(question)`; decision layer already prefers
                   `search_knowledge` when memory hits exist, so retrieval
                   happens through the same gateway embedding pipeline.
  GET  /status   — read-only snapshot of `state/index_ids.json` and
                   `state/memory.json` (no agent, no embed).
  GET  /health   — ping gateway `/v1/routers`.

Run:
  uv run python rag_app.py
  uv run uvicorn rag_app:app --host 127.0.0.1 --port 8200
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent7
import gateway
from mcp_server import SANDBOX, index_document

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATIC_DIR = ROOT / "static"
INDEX_IDS_PATH = STATE_DIR / "index_ids.json"
MEMORY_PATH = STATE_DIR / "memory.json"

# Serialize agent runs: FAISS, memory.json, and per-provider rate state
# in the gateway are single-writer designs.
_run_lock = asyncio.Lock()


# ── request models ──────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


class IngestRequest(BaseModel):
    root: str = Field(
        default="earnings_call",
        description="Subdirectory under sandbox/ to walk. Use '.' for the whole sandbox. Ignored when `files` is set.",
    )
    extensions: list[str] = Field(
        default_factory=lambda: [".md", ".txt"],
        description="File extensions (with leading dot) to index. Ignored when `files` is set.",
    )
    files: list[str] | None = Field(
        default=None,
        description=(
            "Optional explicit list of sandbox-relative file paths to index. "
            "When provided, `root`/`extensions` are ignored."
        ),
    )


# ── helpers ─────────────────────────────────────────────────────────────────

def _index_snapshot() -> dict[str, Any]:
    if not INDEX_IDS_PATH.exists():
        return {"size": 0, "unique": 0, "ids_sample": []}
    ids = json.loads(INDEX_IDS_PATH.read_text(encoding="utf-8"))
    return {
        "size": len(ids),
        "unique": len(set(ids)),
        "ids_sample": ids[:10],
    }


def _memory_fact_count() -> int:
    if not MEMORY_PATH.exists():
        return 0
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return 0
    return sum(1 for it in items if isinstance(it, dict) and it.get("kind") == "fact")


def _walk_sandbox(root: str, extensions: list[str]) -> list[str]:
    """Return paths relative to SANDBOX, suitable for `index_document(path=...)`."""
    base = (SANDBOX / root).resolve()
    sandbox_root = SANDBOX.resolve()
    if not str(base).startswith(str(sandbox_root)):
        raise HTTPException(status_code=400, detail=f"root '{root}' escapes sandbox")
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"sandbox path '{root}' not found")

    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        rel = p.resolve().relative_to(sandbox_root).as_posix()
        out.append(rel)
    return out


def _validate_files(files: list[str]) -> list[str]:
    """Confirm each user-supplied path is a real file under SANDBOX."""
    sandbox_root = SANDBOX.resolve()
    out: list[str] = []
    for f in files:
        p = (SANDBOX / f).resolve()
        if not str(p).startswith(str(sandbox_root)):
            raise HTTPException(status_code=400, detail=f"path '{f}' escapes sandbox")
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"file '{f}' not found in sandbox")
        out.append(p.relative_to(sandbox_root).as_posix())
    # dedup while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _indexed_paths() -> set[str]:
    """Sandbox-relative paths that already have at least one chunk indexed."""
    if not MEMORY_PATH.exists():
        return set()
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return set()
    return {
        it["descriptor"].split(" ", 1)[0][len("[sandbox:"):]
        for it in items
        if isinstance(it, dict)
        and isinstance(it.get("descriptor"), str)
        and it["descriptor"].startswith("[sandbox:")
    }


# ── app ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Earnings Call Analyst",
    description=(
        "RAG dashboard for analyzing earnings call transcripts via agent7 + MCP "
        "with gateway-backed embeddings."
    ),
    version="0.1.0",
)


@app.on_event("startup")
async def _startup() -> None:
    # Warm the gateway so the first request doesn't pay cold-start cost.
    # Run in a thread because ensure_gateway() is synchronous and may block
    # up to ~45s while the V7 process boots.
    await asyncio.to_thread(gateway.ensure_gateway)


PRELOAD_ROOT = "earnings_call"
PRELOAD_EXTS = [".md", ".txt"]


def _already_indexed(files: list[str]) -> bool:
    """True when every preload file already has at least one chunk in
    memory.json (descriptor begins with `[sandbox:<path> chunk`)."""
    if not files:
        return False
    indexed = _indexed_paths()
    return all(f in indexed for f in files)


@app.on_event("startup")
async def _preload_index() -> None:
    """Auto-ingest the default earnings_call corpus on first boot. Opt-in:
    only runs when `RAG_PRELOAD=1` is set. Otherwise indexing is driven
    on demand from the dashboard / `POST /ingest`. Skipped when every
    preload file is already present in the index."""
    if os.environ.get("RAG_PRELOAD", "0") != "1":
        print("[preload] disabled (set RAG_PRELOAD=1 to enable startup ingest)")
        return
    try:
        files = _walk_sandbox(PRELOAD_ROOT, PRELOAD_EXTS)
    except HTTPException:
        print(f"[preload] sandbox/{PRELOAD_ROOT} not found — skipping")
        return
    if not files:
        print(f"[preload] no {PRELOAD_EXTS} files under sandbox/{PRELOAD_ROOT} — skipping")
        return
    if _already_indexed(files):
        print(f"[preload] all {len(files)} files already indexed — skipping")
        return
    print(f"[preload] indexing {len(files)} file(s) from sandbox/{PRELOAD_ROOT}…")
    async with _run_lock:
        for f in files:
            try:
                await asyncio.to_thread(index_document, f)
            except Exception as exc:
                print(f"[preload] {f}: {exc}")
    after = _index_snapshot()
    print(f"[preload] done — index size now {after['size']}")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    up = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{gateway.GATEWAY_URL}/v1/routers")
            up = r.status_code == 200
    except Exception:
        up = False
    return {"gateway_up": up, "gateway_url": gateway.GATEWAY_URL}


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "index": _index_snapshot(),
        "memory_facts": _memory_fact_count(),
        "sandbox": str(SANDBOX),
    }


@app.post("/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    # RAG-only: answer strictly from the indexed knowledge base. Disable
    # web tools so the agent cannot fall back to fetching/searching the
    # internet when the index has no relevant chunks.
    rag_prompt = (
        "Answer the following question using ONLY the indexed knowledge "
        "base via `search_knowledge`. Do not browse the web or fetch URLs.\n\n"
        "Rules for using `search_knowledge` results:\n"
        "- Each returned chunk includes a `similarity` score (cosine, 0-1) "
        "and the full `chunk` text.\n"
        "- A chunk being returned does NOT mean it answers the question. "
        "Read the `chunk` text and check that it explicitly contains "
        "information about the asked topic.\n"
        "- If `search_knowledge` returns an empty list, OR none of the "
        "returned chunks explicitly mention the specific subject of the "
        "question (e.g. for 'is X planning layoffs?' you need a chunk "
        "that actually discusses layoffs / headcount / workforce / "
        "severance at X — not just a chunk that mentions X), reply with "
        "EXACTLY: \"I could not find any details about that in the "
        "knowledge base.\" and stop. Do not synthesize an answer from "
        "tangentially-related chunks.\n\n"
        f"Question: {req.question}"
    )
    async with _run_lock:
        try:
            answer = await agent7.run(
                rag_prompt,
                exclude_tools={
                    "web_search",
                    "fetch_url",
                    "read_file",
                    "list_dir",
                    "create_file",
                    "update_file",
                    "edit_file",
                    "index_document",
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"agent7.run failed: {exc}")
    return {"answer": answer}


@app.post("/ingest")
async def ingest(req: IngestRequest) -> dict[str, Any]:
    if req.files:
        files = _validate_files(req.files)
    else:
        files = _walk_sandbox(req.root, req.extensions)
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"no files matching {req.extensions} under sandbox/{req.root}",
        )
    before = _index_snapshot()

    # Call index_document directly (bypassing agent7) so we index EXACTLY
    # the requested files. The LLM agent is unreliable for "index this
    # specific list" — it can call list_dir and pick up extras.
    results: list[dict[str, Any]] = []
    async with _run_lock:
        for f in files:
            try:
                r = await asyncio.to_thread(index_document, f)
                results.append({"path": f, **{k: r[k] for k in ("chunks_indexed", "source") if k in r}})
            except Exception as exc:
                results.append({"path": f, "error": str(exc)})

    after = _index_snapshot()
    total_chunks = sum(r.get("chunks_indexed", 0) for r in results)
    errors = [r for r in results if "error" in r]
    return {
        "files": files,
        "file_count": len(files),
        "chunks_indexed": total_chunks,
        "results": results,
        "errors": errors,
        "index_before": before,
        "index_after": after,
        "new_vectors": after["size"] - before["size"],
    }


@app.get("/sandbox/files")
async def sandbox_files(
    root: str = ".",
    extensions: str = ".md,.txt",
) -> dict[str, Any]:
    """List files under sandbox/<root> matching the given extensions, and
    flag which ones are already indexed."""
    exts = [e.strip() for e in extensions.split(",") if e.strip()]
    files = _walk_sandbox(root, exts)
    indexed = _indexed_paths()
    sandbox_root = SANDBOX.resolve()
    out: list[dict[str, Any]] = []
    for f in files:
        p = sandbox_root / f
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({"path": f, "indexed": f in indexed, "size": size})
    return {
        "root": root,
        "extensions": exts,
        "files": out,
    }


@app.get("/sandbox/file")
async def sandbox_file(path: str, max_bytes: int = 8000) -> dict[str, Any]:
    """Return a preview of a single sandbox file's contents (truncated)."""
    sandbox_root = SANDBOX.resolve()
    p = (SANDBOX / path).resolve()
    if not str(p).startswith(str(sandbox_root)):
        raise HTTPException(status_code=400, detail=f"path '{path}' escapes sandbox")
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file '{path}' not found")
    full_size = p.stat().st_size
    raw = p.read_bytes()[: max(0, max_bytes)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return {
        "path": path,
        "size": full_size,
        "bytes_returned": len(raw),
        "truncated": len(raw) < full_size,
        "content": text,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_app:app", host="127.0.0.1", port=8200, reload=False)
