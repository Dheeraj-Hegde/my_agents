"""Shared async cua.Localhost handle.

Layers consume `Host` via `try_(task, host, recorder)`. The skill opens
one `cua.Localhost.connect()` per `run()` so the transport, screen
dimensions, and clipboard state are stable across the cascade. We do
not cache a Localhost across `run()` calls — cua's contract is that
`connect()` is cheap and `disconnect()` cleans up the cua_auto
sub-process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imported lazily at call time
    from cua_sandbox.localhost import Localhost as Host  # type: ignore
else:
    Host = object  # runtime placeholder so type hints don't import cua at startup
