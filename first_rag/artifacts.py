"""Content-addressable artifact store.

Raw bytes the agent fetches or produces live here. Each blob gets a
monotonic integer artifact id allocated via `schemas.new_id("art")`.
Files are named by that id (`{id}.bin` / `{id}.json`); a small sidecar
index `_digest_index.json` maps sha256 digests to ids so identical bytes
re-use the same id (content dedup) without exposing the digest in the
public handle. Memory holds the int handle + a short descriptor; this
module owns the bytes. Perception sees handles, Decision sees bytes only
when Perception attaches them. The 50 KB of HTML touches exactly one LLM
call across a run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from schemas import Artifact, new_id

STORE = Path(__file__).parent / "state" / "artifacts"
STORE.mkdir(parents=True, exist_ok=True)
_DIGEST_INDEX = STORE / "_digest_index.json"


def _load_digest_index() -> dict[str, int]:
    if not _DIGEST_INDEX.exists():
        return {}
    try:
        return json.loads(_DIGEST_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_digest_index(idx: dict[str, int]) -> None:
    tmp = _DIGEST_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx), encoding="utf-8")
    tmp.replace(_DIGEST_INDEX)


def put(blob: bytes, *, content_type: str, source: str, descriptor: str) -> int:
    """Write blob (deduped by content hash) and return its integer handle."""
    digest = hashlib.sha256(blob).hexdigest()
    digest_idx = _load_digest_index()
    existing = digest_idx.get(digest)
    if existing is not None and (STORE / f"{existing}.bin").exists():
        return existing

    art_id = new_id("art")
    bin_path = STORE / f"{art_id}.bin"
    meta_path = STORE / f"{art_id}.json"
    bin_path.write_bytes(blob)
    meta = Artifact(
        id=art_id,
        content_type=content_type,
        size_bytes=len(blob),
        source=source,
        descriptor=descriptor,
    )
    meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    digest_idx[digest] = art_id
    _save_digest_index(digest_idx)
    return art_id


def get_bytes(artifact_id: int) -> bytes:
    return (STORE / f"{artifact_id}.bin").read_bytes()


def get_meta(artifact_id: int) -> Artifact:
    raw = json.loads((STORE / f"{artifact_id}.json").read_text(encoding="utf-8"))
    return Artifact.model_validate(raw)


def exists(artifact_id: int) -> bool:
    return (STORE / f"{artifact_id}.bin").exists()
