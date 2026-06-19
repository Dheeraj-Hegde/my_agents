"""Task 1 — Calculator via deterministic hotkeys (cua-driven).

Goal: launch the Windows Calculator, compute `a + b`, copy the result
to the clipboard, return it. Defaults to `12345 + 67890` so existing
runs continue to behave the same; pass `a=`, `b=` (and optional
`op="+"|"-"|"*"|"/"`) to `build()` to compute something else — the
Planner can do this via `metadata.a` / `metadata.b` on a `computer_use`
node.

Why this task lives at Layer 2a:
  - `calc.exe` accepts every digit and operator key directly.
  - Ctrl+C copies the result regardless of UI version (Calc app,
    classic calc, even most third-party calculators on Windows).
  - There is no need to look at the screen: `host.clipboard.get()`
    confirms the result.

This is the cascade's "zero vision" demonstration. The Layer 1 API
handler is intentionally absent — there is no `calc.exe --compute`
flag, so Layer 1 has nothing to offer and marks itself non-applicable;
the cascade lands cleanly on Layer 2a.

All OS access (launch, keystrokes, clipboard read) goes through the
shared `cua.Localhost`; this file contains no direct subprocess /
pyautogui / pyperclip imports.
"""

from __future__ import annotations

from ..task_spec import TaskSpec


# Operator → Python evaluator. Kept tiny on purpose; the Calculator app
# accepts these same glyphs as keystrokes, so the keypress recipe and
# the validator stay in lockstep.
_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b if b and a % b == 0 else None,
}


async def _read_clipboard(host) -> str | None:
    """Read the clipboard via cua. None on failure."""
    try:
        return await host.clipboard.get()
    except Exception:
        return None


def _make_validator(expected: str):
    async def _validate(out, host) -> tuple[bool, str]:
        """Confirm Calculator actually produced the expected result.

        The clipboard read happens inside the cascade as the Layer 2a
        capture step; the validator just re-asserts the final value is
        the expected integer string. We avoid re-reading the clipboard
        here because Windows can race the layer's capture with the
        validator's read and return either the result or empty on a
        fresh open.
        """
        got = (out.final_value or "").strip()
        if got == expected:
            return True, f"clipboard=={expected}"
        return False, (
            f"expected clipboard {expected!r}, got {got!r} "
            f"(layer={out.path}, layers_tried={out.layers_tried})"
        )
    return _validate


def build(a: int = 12345, b: int = 67890, op: str = "+", **_ignored) -> TaskSpec:
    a = int(a)
    b = int(b)
    if op not in _OPS:
        raise ValueError(f"unsupported op {op!r}; expected one of {list(_OPS)}")
    result = _OPS[op](a, b)
    if result is None:
        raise ValueError(f"operation {a}{op}{b} has no clean integer result")
    expected = str(result)
    expression = f"{a}{op}{b}="
    return TaskSpec(
        name="01_calculator_hotkeys",
        goal=f"Compute {a} {op} {b} in the Windows Calculator and "
             "read the result from the clipboard.",
        # Layer 1: no API exit. Falls through.
        api_handler=None,
        # Layer 2a recipe — fully deterministic, no introspection.
        # `launch.argv` may be a string (passed to host.shell.run as-is)
        # or a list (joined with spaces).
        hotkey_recipe=[
            {"action": "launch", "argv": "calc.exe"},
            {"action": "sleep",  "seconds": 1.8},
            {"action": "focus",  "title_contains": "Calculator"},
            {"action": "sleep",  "seconds": 0.3},
            {"action": "press",  "key": "escape"},     # clear any prior state
            {"action": "type",   "text": expression},
            {"action": "sleep",  "seconds": 0.4},
            {"action": "hotkey", "keys": ["ctrl", "c"]},
            {"action": "sleep",  "seconds": 0.4},
        ],
        hotkey_capture=_read_clipboard,
        # Layer 2b fallback: if hotkeys fail to land on Calculator
        # (e.g. focus race lost), drive Calculator via UIA control
        # names. Present here as evidence of cascade discipline; in
        # normal runs Layer 2a already succeeded.
        uia_recipe=[
            {"step": "find_window", "title_contains": "Calculator"},
            {"step": "click",       "control": {"Name": "Clear"}},
            {"step": "type",        "text": expression},
            {"step": "sleep",       "seconds": 0.3},
            {"step": "read",        "control": {"AutomationId": "CalculatorResults"},
                                    "into": "result"},
        ],
        # Domain validator: confirm the cascade really wrote the
        # expected value to the clipboard (the layer's "success" alone
        # could mean the recipe ran clean while keystrokes hit the
        # wrong window).
        validator=_make_validator(expected),
        meta={"expected": expected, "a": a, "b": b, "op": op},
    )
