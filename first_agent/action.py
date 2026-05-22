"""Action layer for agents6.

The action layer is the *only* module allowed to execute tools exposed by
``mcp_server``. The decision layer reasons about which tool to call and
with what arguments, then delegates the actual invocation to an
``Action`` instance defined here.

Boundary
--------
* Only tools listed in ``MCP_TOOL_NAMES`` may be executed.
* The list of tool sources is restricted to ``mcp_server`` (no
  ``httpx``, ``requests``, ``subprocess``, browser drivers, etc.). An
  import-time guard raises ``RuntimeError`` if such a module is pulled
  in from inside this file.
* If ``mcp_server`` cannot be imported (e.g. its native dependencies are
  missing in the current venv), ``Action`` still constructs but reports
  no available tools and refuses calls with a clean error.
"""

from __future__ import annotations

import asyncio
import builtins
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from artifacts import Artifacts
    from schemas import ToolCall


# --------------------------------------------------------------------- guard
_ALLOWED_TOOL_PROVIDERS = {"mcp_server"}
_TOOL_BLOCKLIST_ROOTS = {
    "httpx", "requests", "urllib3", "selenium", "playwright",
    "crawl4ai", "tavily", "ddgs", "duckduckgo_search", "bs4",
    "subprocess", "openai", "anthropic", "google",
}
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller_file = (globals or {}).get("__file__", "")
    if caller_file == __file__:
        root = name.split(".", 1)[0]
        if root in _TOOL_BLOCKLIST_ROOTS:
            raise RuntimeError(
                f"action.py is not allowed to import '{name}'. "
                f"Tools may only come from: {sorted(_ALLOWED_TOOL_PROVIDERS)}."
            )
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guarded_import


# Allowed tool source. Imported lazily-tolerantly: if `mcp_server` cannot
# be loaded the action layer simply reports no tools rather than crashing.
try:
    import mcp_server  # noqa: E402  (must be imported AFTER the guard above)
    _MCP_IMPORT_ERROR: Optional[str] = None
except Exception as _e:  # pragma: no cover - environment-dependent
    mcp_server = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


# Curated list of tools exposed by mcp_server (kept in sync with
# mcp_server.py).
MCP_TOOL_NAMES: tuple[str, ...] = (
    "web_search", "fetch_url", "get_time", "currency_convert",
    "read_file", "list_dir", "create_file", "update_file", "edit_file",
)


def _load_mcp_tools() -> dict[str, Callable[..., Any]]:
    tools: dict[str, Callable[..., Any]] = {}
    if mcp_server is None:
        return tools
    for name in MCP_TOOL_NAMES:
        fn = getattr(mcp_server, name, None)
        if fn is None:
            continue
        # FastMCP's @mcp.tool() may wrap the function; try to get the inner.
        fn = getattr(fn, "fn", fn)
        if callable(fn):
            tools[name] = fn
    return tools


def _tool_signature(fn: Callable[..., Any]) -> str:
    try:
        sig = inspect.signature(fn)
        return f"{fn.__name__}{sig}"
    except (TypeError, ValueError):
        return getattr(fn, "__name__", "tool") + "(...)"


@dataclass
class ActionResult:
    """Outcome of a single tool invocation."""

    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: Optional[str] = None
    payload: Optional[str] = None  # JSON-serialized result, set by execute()

    @property
    def ok(self) -> bool:
        return self.error is None


class Action:
    """Executes tools exposed by ``mcp_server`` on behalf of decision."""

    def __init__(
        self,
        *,
        tools: Optional[dict[str, Callable[..., Any]]] = None,
        allowed: Optional[set[str]] = None,
    ) -> None:
        self.tools: dict[str, Callable[..., Any]] = (
            tools if tools is not None else _load_mcp_tools()
        )
        self.allowed: set[str] = (
            set(allowed) if allowed is not None else set(self.tools.keys())
        )
        self.import_error: Optional[str] = _MCP_IMPORT_ERROR

    # ----------------------------------------------------------- inspection

    def has_tools(self) -> bool:
        return bool(self.tools)

    def list_tools(self) -> list[str]:
        return sorted(self.tools.keys())

    def signature(self, name: str) -> Optional[str]:
        fn = self.tools.get(name)
        return _tool_signature(fn) if fn else None

    def tools_block(self) -> str:
        """Human/LLM-readable description of available tools."""
        if not self.tools:
            if self.import_error:
                return (
                    "No MCP tools available "
                    f"(mcp_server import failed: {self.import_error}); "
                    "answer directly."
                )
            return "No MCP tools are available; answer directly."
        lines = ["Available MCP tools:"]
        for name, fn in self.tools.items():
            doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            lines.append(f"- {_tool_signature(fn)}  -- {doc}")
        return "\n".join(lines)

    # -------------------------------------------------------------- execute

    def call(self, name: str, arguments: dict[str, Any]) -> ActionResult:
        """Invoke ``name`` with ``arguments`` and capture the outcome."""
        args = dict(arguments or {})
        if name not in self.allowed:
            return ActionResult(
                tool=name,
                arguments=args,
                error=f"tool '{name}' is not allowed",
            )
        fn = self.tools.get(name)
        if fn is None:
            return ActionResult(
                tool=name,
                arguments=args,
                error=f"unknown MCP tool: {name}",
            )
        try:
            result = fn(**args)
            if inspect.iscoroutine(result):
                result = asyncio.run(result)
        except Exception as e:
            return ActionResult(
                tool=name,
                arguments=args,
                error=f"{type(e).__name__}: {e}",
            )
        return ActionResult(tool=name, arguments=args, result=result)

    # ----------------------------------------------- agent-loop execute

    def execute(
        self,
        tool_call: "ToolCall",
        artifacts: "Artifacts",
        *,
        inline_limit: int = 4096,
    ) -> tuple[str, Optional[int], "ActionResult"]:
        """Run a :class:`ToolCall` and return ``(descriptor, artifact_id, result)``.

        - Always runs the tool via :meth:`call`.
        - Builds a short human-readable descriptor.
        - If the serialized result exceeds ``inline_limit`` bytes, stores
          the full payload as an artifact and returns its handle.
        - The full payload is always written to ``result.payload`` so the
          orchestrator can persist it on the resulting MemoryItem.
        """
        result = self.call(tool_call.name, dict(tool_call.arguments or {}))
        if not result.ok:
            desc = f"{tool_call.name}: error: {result.error}"
            return desc, None, result

        try:
            payload = json.dumps(result.result, default=str, ensure_ascii=False)
        except Exception:
            payload = str(result.result)
        size = len(payload.encode("utf-8"))

        short = _summarize_result(tool_call.name, result.result)
        descriptor = f"{tool_call.name}({_short_args(tool_call.arguments)}) -> {short}"
        result.payload = payload

        artifact_id: Optional[int] = None
        if size > inline_limit:
            art = artifacts.put(
                payload.encode("utf-8"),
                content_type="application/json",
                source=f"tool:{tool_call.name}",
                descriptor=descriptor[:200],
            )
            artifact_id = art.id
        return descriptor, artifact_id, result


def _short_args(arguments: dict) -> str:
    if not arguments:
        return ""
    parts = []
    for k, v in arguments.items():
        s = repr(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _summarize_result(tool: str, result: Any) -> str:
    """Build a one-line descriptor of ``result``."""
    if result is None:
        return "ok (no result)"
    if isinstance(result, list):
        return f"list[{len(result)}]"
    if isinstance(result, dict):
        # Prefer telling keys for known tool shapes.
        for key in ("answer", "summary", "title", "datetime", "result", "rate", "path"):
            v = result.get(key)
            if isinstance(v, (str, int, float)):
                s = str(v)
                return s if len(s) <= 120 else s[:117] + "..."
        keys = ",".join(list(result.keys())[:6])
        return f"dict{{{keys}}}"
    s = str(result)
    return s if len(s) <= 120 else s[:117] + "..."


__all__ = ["Action", "ActionResult", "MCP_TOOL_NAMES", "execute"]


# ---------------------------------------------------------- MCP-session execute

async def execute(
    session,
    tool_call: "ToolCall",
    artifacts_store: "Artifacts | None" = None,
    *,
    inline_limit: int = 4096,
) -> tuple[str, "int | None"]:
    """Execute *tool_call* over an MCP ``ClientSession``.

    Returns ``(result_text, artifact_id)``.  If the serialised result
    exceeds *inline_limit* bytes and an *artifacts_store* is provided,
    the full payload is stored as an artifact and its id is returned
    as ``artifact_id``; otherwise ``artifact_id`` is ``None``.
    """
    from mcp import ClientSession  # type: ignore

    result = await session.call_tool(tool_call.name, tool_call.arguments)

    # Flatten MCP content objects to a plain string.
    parts: list[str] = []
    for block in (result.content or []):
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    result_text = "\n".join(parts) if parts else str(result)

    artifact_id: "int | None" = None
    if artifacts_store is not None:
        size = len(result_text.encode("utf-8"))
        if size > inline_limit:
            art = artifacts_store.put(
                result_text.encode("utf-8"),
                content_type="application/json",
                source=f"tool:{tool_call.name}",
                descriptor=result_text[:200],
            )
            artifact_id = art.id

    return result_text, artifact_id
