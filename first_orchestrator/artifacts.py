"""Artifacts layer for agents6.

A simple content-addressed blob store. Tool outputs that are large or
opaque (long markdown, file dumps, binary payloads) are stashed here
instead of being shoved through the LLM. The store hands back a small
handle (``art:<sha256-prefix>``) that other layers can pass around;
bytes are only materialized on demand via :meth:`Artifacts.get_bytes`.

Persistence
-----------
Blobs are mirrored to ``root`` on disk (default ``./artifacts/``) so
that ``MemoryItem.artifact_id`` references survive process restarts.
For each artifact two files are written, both named by the same
sha256-prefix digest used in the handle:

* ``<digest>.bin``  – raw bytes
* ``<digest>.json`` – :class:`schemas.Artifact` metadata

On construction the directory is scanned and any existing pairs are
loaded back into the in-memory index.

Boundary
--------
* Artifacts MUST NOT call MCP tools, the LLM, or memory.
"""

from __future__ import annotations

import builtins
import hashlib
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from schemas import Artifact


# --------------------------------------------------------------------- guard
_FORBIDDEN_ROOTS = {"mcp", "mcp_server", "llm_gatewayV3"}
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller_file = (globals or {}).get("__file__", "")
    if caller_file == __file__:
        root = name.split(".", 1)[0]
        if root in _FORBIDDEN_ROOTS:
            raise RuntimeError(
                f"artifacts.py is not allowed to import '{name}'. "
                "Artifacts is a passive blob store."
            )
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guarded_import


DEFAULT_ROOT = Path(__file__).resolve().parent / "artifacts"
_HANDLE_PREFIX = "art:"
_HASH_LEN = 16  # hex chars from sha256


def _make_handle(digest_hex: str) -> str:
    return f"{_HANDLE_PREFIX}{digest_hex[:_HASH_LEN]}"


def _digest_from_handle(handle: str) -> str:
    if not handle.startswith(_HANDLE_PREFIX):
        raise ValueError(f"not an artifact handle: {handle!r}")
    return handle[len(_HANDLE_PREFIX) :]


class ArtifactNotFound(KeyError):
    pass


class Artifacts:
    """Content-addressed blob store mirrored to disk under ``root``."""

    def __init__(self, root: Union[str, Path, None] = DEFAULT_ROOT) -> None:
        self.root: Path = Path(root) if root is not None else DEFAULT_ROOT
        self._blobs: Dict[str, bytes] = {}
        self._metas: Dict[str, Artifact] = {}
        if self.root is not None:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Disk unavailable — fall back to in-memory only.
                self.root = None  # type: ignore[assignment]
            else:
                self._load()

    # ------------------------------------------------------------ disk I/O

    def _bin_path(self, digest: str) -> Path:
        return self.root / f"{digest}.bin"

    def _meta_path(self, digest: str) -> Path:
        return self.root / f"{digest}.json"

    def _load(self) -> None:
        """Rebuild the in-memory index from any ``<digest>.json`` files."""
        if not self.root or not self.root.exists():
            return
        for meta_file in self.root.glob("*.json"):
            digest = meta_file.stem
            bin_file = self._bin_path(digest)
            if not bin_file.exists():
                continue
            try:
                meta = Artifact.model_validate_json(
                    meta_file.read_text(encoding="utf-8")
                )
                data = bin_file.read_bytes()
            except Exception:
                continue
            self._blobs[digest] = data
            self._metas[digest] = meta

    def _persist(self, digest: str, data: bytes, meta: Artifact) -> None:
        if not self.root:
            return
        try:
            self._bin_path(digest).write_bytes(data)
            self._meta_path(digest).write_text(
                meta.model_dump_json(), encoding="utf-8"
            )
        except OSError:
            # Best-effort: if disk write fails we keep the in-memory copy.
            pass

    # -------------------------------------------------------------- public

    def put(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        source: str = "",
        descriptor: str = "",
    ) -> Artifact:
        """Store ``data`` and return the resulting :class:`Artifact`."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Artifacts.put expects bytes")
        full_digest = hashlib.sha256(bytes(data)).hexdigest()
        handle = _make_handle(full_digest)
        digest = _digest_from_handle(handle)

        blob = bytes(data)
        self._blobs[digest] = blob
        art_id = int(full_digest[:16], 16)  # stable int from first 16 hex chars
        meta = Artifact(
            id=art_id,
            content_type=content_type,
            size_bytes=len(blob),
            source=source,
            descriptor=descriptor,
        )
        self._metas[digest] = meta
        self._persist(digest, blob, meta)
        return meta

    def _resolve(self, ref: Union[int, str]) -> str:
        """Return the digest key for an int id or a string handle."""
        if isinstance(ref, int):
            for digest, meta in self._metas.items():
                if meta.id == ref:
                    return digest
            raise ArtifactNotFound(ref)
        return _digest_from_handle(ref)

    def exists(self, ref: Union[int, str]) -> bool:
        try:
            digest = self._resolve(ref)
        except (ValueError, ArtifactNotFound):
            return False
        return digest in self._blobs

    def get_bytes(self, ref: Union[int, str]) -> bytes:
        digest = self._resolve(ref)
        if digest not in self._blobs:
            raise ArtifactNotFound(ref)
        return self._blobs[digest]

    def get_meta(self, ref: Union[int, str]) -> Artifact:
        digest = self._resolve(ref)
        if digest not in self._metas:
            raise ArtifactNotFound(ref)
        return self._metas[digest]

    # ------------------------------------------------------------- helpers

    def preview(self, ref: Union[int, str], *, max_bytes: int = 4096) -> str:
        """Return a UTF-8 preview of an artifact (replacement-decoded)."""
        raw = self.get_bytes(ref)[:max_bytes]
        return raw.decode("utf-8", errors="replace")


__all__ = ["Artifacts", "ArtifactNotFound", "DEFAULT_ROOT"]
