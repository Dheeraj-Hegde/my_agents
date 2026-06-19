"""Computer-Use Dashboard.

Tiny FastAPI app that lets a human:
  1. type a natural-language query (e.g. "open Calculator and compute 7*8")
  2. spawn `flow.py` as a child process,
  3. watch the orchestrator log + captured screenshots stream in,
  4. read the final answer and a play-by-play of what the agent did.

No websockets, no message queues — the UI polls a few JSON endpoints
every ~700 ms and reads frames straight off disk. That keeps the
moving parts to: this file, a single static HTML page, and the
existing trajectory recorder on the computer_use side.

Layout on disk:
  dashboard/static/index.html         the UI
  state/dashboard/sessions.json       session index (sid → meta)
  state/dashboard/<sid>.log           captured stdout of flow.py
  state/trajectories/<sid>/<task>/    per-task frames + events
                                      (written by computer_use.recorder)

Run with:
    .\\.venv\\Scripts\\python.exe dashboard.py
    # then open http://localhost:8200
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent
TRAJ_ROOT = ROOT / "state" / "trajectories"
DASH_DIR = ROOT / "state" / "dashboard"
SESSIONS_FILE = DASH_DIR / "sessions.json"
STATIC_DIR = ROOT / "dashboard" / "static"

DASH_DIR.mkdir(parents=True, exist_ok=True)
TRAJ_ROOT.mkdir(parents=True, exist_ok=True)
if not SESSIONS_FILE.exists():
    SESSIONS_FILE.write_text("{}", encoding="utf-8")


# ── session bookkeeping ─────────────────────────────────────────────────────

def _load_sessions() -> dict[str, dict]:
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sessions(d: dict[str, dict]) -> None:
    SESSIONS_FILE.write_text(json.dumps(d, indent=2, default=str),
                             encoding="utf-8")


def _register(sid: str, query: str, pid: int | None) -> None:
    db = _load_sessions()
    db[sid] = {
        "sid": sid,
        "query": query,
        "started_at": time.time(),
        "pid": pid,
        "status": "running",
        "exit_code": None,
        "final_answer": None,
        "ended_at": None,
    }
    _save_sessions(db)


def _finish(sid: str, *, exit_code: int, final_answer: str | None) -> None:
    db = _load_sessions()
    if sid not in db:
        return
    db[sid].update({
        "status": "done" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "final_answer": final_answer,
        "ended_at": time.time(),
    })
    _save_sessions(db)


# ── subprocess runner ───────────────────────────────────────────────────────

# Track live child processes by sid so the UI can cancel.
_LIVE: dict[str, asyncio.subprocess.Process] = {}


def _python_exe() -> str:
    """Prefer the venv interpreter so the child has the same deps."""
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


async def _spawn_flow(sid: str, query: str) -> None:
    """Spawn `python flow.py "<query>"`, pipe stdout to a per-session
    log. The flow.py CLI accepts an explicit session id via
    `--resume <sid>` semantics but we use a different shape here: pass
    the sid via the `S8_SESSION_ID` env var so flow.py reuses it
    instead of minting a fresh `s8-xxxxxxxx`.

    flow.py today does NOT read S8_SESSION_ID — see the patch below
    in main(). We keep the env-var hand-off so the dashboard's sid
    matches the directory under state/trajectories/<sid>/, which is
    what the UI polls."""
    log_path = DASH_DIR / f"{sid}.log"
    env = dict(os.environ)
    env["S8_SESSION_ID"] = sid
    # Prepend a UTF-8 marker so Windows consoles don't mangle the log,
    # and force-unbuffered stdout so we see flow.py's progress live in
    # the dashboard log panel instead of after the child exits.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUNBUFFERED"] = "1"

    # `-u` is belt-and-braces: PYTHONUNBUFFERED handles most cases but
    # interactive consoles on Windows still line-buffer through io.
    cmd = [_python_exe(), "-u", "flow.py", query]
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write(f"[dashboard] launching: {' '.join(cmd)}\n")
        logf.write(f"[dashboard] session_id={sid}\n")
        logf.flush()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        _LIVE[sid] = proc

        # Persist the real OS pid now that we have it.
        db = _load_sessions()
        if sid in db:
            db[sid]["pid"] = proc.pid
            _save_sessions(db)

        final_answer: str | None = None
        capture_next_lines = 0
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            logf.write(line + "\n")
            logf.flush()
            # The orchestrator prints the final answer between two
            # `═══` rules with the line "FINAL: <text>" right after.
            if line.startswith("FINAL:"):
                final_answer = line[len("FINAL:"):].strip()
                capture_next_lines = 0
            elif capture_next_lines > 0:
                final_answer = (final_answer or "") + "\n" + line
                capture_next_lines -= 1

        rc = await proc.wait()
        _LIVE.pop(sid, None)
        logf.write(f"[dashboard] exit_code={rc}\n")
        _finish(sid, exit_code=rc, final_answer=final_answer)


# ── helpers: trajectories on disk ───────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"kind": "_unparsable", "raw": line[:200]})
    return out


def _list_tasks(sid: str) -> list[dict]:
    """Return the per-task summaries for a session, in stable order."""
    sdir = TRAJ_ROOT / sid
    if not sdir.exists():
        return []
    tasks = []
    for tdir in sorted(p for p in sdir.iterdir() if p.is_dir()):
        meta_path = tdir / "meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {"_meta_error": True}
        frames = sorted((tdir / "frames").glob("frame_*.png"))
        events = _read_jsonl(tdir / "events.jsonl")
        tasks.append({
            "task": tdir.name,
            "dir": str(tdir),
            "meta": meta,
            "frame_count": len(frames),
            "event_count": len(events),
            "last_event_at": (events[-1].get("t") if events else None),
        })
    return tasks


def _resolve_frame(rel_path: str) -> Path:
    """Resolve a frame path the UI requested; refuse anything outside
    state/trajectories/."""
    # Normalise — accept either an absolute path under TRAJ_ROOT or a
    # relative path like "<sid>/<task>/frames/frame_0001.png".
    p = Path(rel_path)
    if not p.is_absolute():
        p = TRAJ_ROOT / p
    try:
        p_resolved = p.resolve()
        p_resolved.relative_to(TRAJ_ROOT.resolve())
    except Exception as exc:
        raise HTTPException(400, f"refusing frame outside TRAJ_ROOT: {exc}")
    if not p_resolved.exists():
        raise HTTPException(404, f"frame not found: {p_resolved}")
    return p_resolved


# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="Computer-Use Dashboard")


class RunRequest(BaseModel):
    query: str


@app.post("/api/run")
async def api_run(req: RunRequest) -> dict:
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(400, "query is empty")
    sid = f"s8-{uuid.uuid4().hex[:8]}"
    _register(sid, q, pid=None)
    # Fire-and-forget the subprocess driver.
    asyncio.create_task(_spawn_flow(sid, q))
    return {"sid": sid, "status": "running", "query": q}


@app.get("/api/sessions")
async def api_sessions() -> dict:
    db = _load_sessions()
    # Newest first.
    items = sorted(db.values(),
                   key=lambda s: s.get("started_at") or 0,
                   reverse=True)
    return {"sessions": items}


@app.get("/api/sessions/{sid}")
async def api_session(sid: str) -> dict:
    db = _load_sessions()
    if sid not in db:
        raise HTTPException(404, f"unknown session {sid!r}")
    meta = db[sid]
    log_path = DASH_DIR / f"{sid}.log"
    log = ""
    if log_path.exists():
        # Tail at most 4 KB; the UI scrolls inside its panel anyway.
        try:
            data = log_path.read_text(encoding="utf-8", errors="replace")
            log = data[-4000:]
        except Exception as exc:
            log = f"[dashboard] log read failed: {exc}"
    tasks = _list_tasks(sid)
    return {"meta": meta, "log": log, "tasks": tasks}


@app.get("/api/sessions/{sid}/tasks/{task}/events")
async def api_events(sid: str, task: str) -> dict:
    p = TRAJ_ROOT / sid / task / "events.jsonl"
    return {"events": _read_jsonl(p)}


@app.get("/api/sessions/{sid}/tasks/{task}/frames")
async def api_frames(sid: str, task: str) -> dict:
    fdir = TRAJ_ROOT / sid / task / "frames"
    if not fdir.exists():
        return {"frames": []}
    items = []
    events = _read_jsonl(TRAJ_ROOT / sid / task / "events.jsonl")
    # Map filename → label (the recorder logs a "frame" event per write
    # with the source file path and a free-text label).
    label_by_name: dict[str, str] = {}
    for ev in events:
        if ev.get("kind") == "frame":
            fp = ev.get("file") or ""
            label_by_name[Path(fp).name] = ev.get("label") or ""
    for png in sorted(fdir.glob("frame_*.png")):
        items.append({
            "name": png.name,
            "rel": f"{sid}/{task}/frames/{png.name}",
            "label": label_by_name.get(png.name, ""),
            "bytes": png.stat().st_size,
        })
    return {"frames": items}


@app.get("/api/frame")
async def api_frame(rel: str = Query(...)) -> FileResponse:
    """Serve a single PNG. `rel` is the trajectory-relative path."""
    p = _resolve_frame(rel)
    return FileResponse(str(p), media_type="image/png")


@app.post("/api/sessions/{sid}/cancel")
async def api_cancel(sid: str) -> dict:
    proc = _LIVE.get(sid)
    if proc is None:
        return {"sid": sid, "status": "not_running"}
    try:
        proc.terminate()
    except Exception:
        pass
    return {"sid": sid, "status": "terminating"}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


# Static mount for any future assets (icons, JS modules, etc.).
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", "8200"))
    print(f"[dashboard] http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
