"""Three bundled Computer-Use tasks.

Each module exposes a `build()` returning a `TaskSpec` shaped so the
cascade lands on the intended layer:

    calculator    → solved at Layer 2a (hotkeys), zero vision calls
    vscode_editor → solved at Layer 2c (Electron CDP page)
    canvas_sketch → forced to Layer 3 (vision SoM)

Bundle these via `ALL_TASKS` for the runner. Tasks whose module is
empty / missing / failed to import are silently dropped — that lets a
student rip out task 3 while still demoing 1 and 2.
"""

import importlib

ALL_TASKS = []
__all__ = ["ALL_TASKS"]

for _name in ("calculator", "vscode_editor", "canvas_sketch",
              "vscode_create_file", "browser_game"):
    try:
        _mod = importlib.import_module(f"{__name__}.{_name}")
    except Exception:
        continue
    _build = getattr(_mod, "build", None)
    if _build is None:
        continue
    globals()[_name] = _mod
    __all__.append(_name)
    ALL_TASKS.append(_build)
