"""Task — drive VS Code to author ``readme_demo.txt``.

User goal: open VS Code, create ``readme_demo.txt`` containing the
sentence "hello this is a demo for computer agent", save it, then
close the VS Code window.

Cascade landing zone: **Layer 2a (hotkeys)**.
  - Layer 1 (api): we deliberately do NOT define an ``api_handler``;
    writing the file with ``Path.write_text`` would trivially win the
    cascade but defeats the user's intent of "access vscode to create"
    the file. Layer 1 marks itself non-applicable.
  - Layer 2a (hotkeys): launch ``code --new-window <path>``, type the
    sentence into the empty editor, ``Ctrl+S`` to save, ``Alt+F4`` to
    close. Fully deterministic, no introspection required.
  - Layer 2b/2c/3 are fallbacks that only run if 2a fails.

The task uses ``--new-window`` and an isolated ``--user-data-dir`` so
the user's primary VS Code instance (where they're chatting with the
agent) is never disturbed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from ..task_spec import TaskSpec


# Repo root — the task file lives at computer_use/tasks/<this>.py, so
# parents[2] is the workspace folder.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Where the produced file lands. Kept inside the repo so reviewers can
# inspect it easily after a run; pre-cleaned on every build() call.
_DEFAULT_FILE_NAME = "readme_demo.txt"
_DEFAULT_CONTENT = "hello this is a demo for computer agent"


def _resolve_code_exe() -> str:
    """Locate the VS Code launcher.

    Order: ``CUA_VSCODE_EXE`` env override → ``code`` on PATH →
    ``code.cmd`` on PATH → bare ``code`` (lets shell=True resolve
    via PATHEXT on Windows).
    """
    env_override = os.environ.get("CUA_VSCODE_EXE")
    if env_override:
        return env_override
    for cand in ("code", "code.cmd", "Code"):
        located = shutil.which(cand)
        if located:
            return located
    return "code"


def _quote_for_shell(s: str) -> str:
    """Wrap a path in double quotes for shell=True. Windows ``code.cmd``
    is happy with double-quoted paths containing spaces."""
    return f'"{s}"' if " " in s else s


def build(*,
          file_path: str | None = None,
          content: str | None = None,
          **_ignored) -> TaskSpec:
    """Build the TaskSpec.

    Parameters
    ----------
    file_path:
        Absolute or repo-relative path of the file to create. Defaults
        to ``<repo>/readme_demo.txt``.
    content:
        Body to type into the editor. Defaults to the user's requested
        sentence.
    """
    target_path = Path(file_path) if file_path else (_REPO_ROOT / _DEFAULT_FILE_NAME)
    target_path = target_path.resolve()
    body = _DEFAULT_CONTENT if content is None else str(content)

    # Pre-clean: if a previous run left the file behind we want VS
    # Code to open an empty editor, otherwise our typed text would be
    # appended to existing content. We don't trust the user's typed
    # delete chain because focus race conditions can swallow keys.
    try:
        if target_path.exists():
            target_path.unlink()
    except Exception:
        # Non-fatal: the recipe still selects-all+deletes before typing.
        pass

    # Use an isolated user-data-dir so we don't merge into the user's
    # primary VS Code window. Same trick the vscode_editor task uses.
    user_data_dir = Path(tempfile.gettempdir()) / "cua-vscode-create"

    code_exe = _resolve_code_exe()

    # On Windows, shell=True (used by Layer 2a's spawner) will route
    # ``code`` through ``cmd.exe`` and resolve the .cmd shim. Quoting
    # the file path keeps spaces safe.
    launch_cmd = (
        f'{_quote_for_shell(code_exe)} '
        f'--new-window '
        f'--user-data-dir {_quote_for_shell(str(user_data_dir))} '
        f'{_quote_for_shell(str(target_path))}'
    )

    # The `focus` step asserts the active title contains the file
    # name. VS Code's title format on Windows is typically
    # "<filename> - Visual Studio Code" for single-file mode, so the
    # filename substring is a reliable check.
    title_needle = target_path.name

    recipe: list[dict] = [
        {"action": "launch", "argv": launch_cmd},
        # VS Code cold start on Windows can take a few seconds; the
        # focus step below polls for ~2.5s, but we still want the
        # editor surface to be ready to accept keys.
        {"action": "sleep",  "seconds": 6.0},
        {"action": "focus",  "title_contains": title_needle},
        {"action": "sleep",  "seconds": 0.4},
        # If VS Code popped a "Get Started" or "Welcome" tab into focus,
        # Ctrl+1 puts focus into the first editor group's primary
        # editor (which is our target file).
        {"action": "hotkey", "keys": ["ctrl", "1"]},
        {"action": "sleep",  "seconds": 0.2},
        # Belt-and-braces: if the editor already had stale content (a
        # previous run that the unlink above somehow missed), wipe it.
        {"action": "hotkey", "keys": ["ctrl", "a"]},
        {"action": "press",  "key": "delete"},
        # Type the body. cua's keyboard.type goes character-by-character
        # so Unicode and punctuation are safe.
        {"action": "type",   "text": body},
        {"action": "sleep",  "seconds": 0.3},
        # Save.
        {"action": "hotkey", "keys": ["ctrl", "s"]},
        {"action": "sleep",  "seconds": 1.0},
        # Close the window. Alt+F4 sends WM_CLOSE; with the file just
        # saved there's no "unsaved changes" dialog to negotiate.
        {"action": "hotkey", "keys": ["alt", "f4"]},
        {"action": "sleep",  "seconds": 0.8},
    ]

    async def _validate(out, host) -> tuple[bool, str]:
        """Confirm VS Code actually wrote the file with the right body."""
        if not target_path.exists():
            return False, f"file not created at {target_path}"
        try:
            actual = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            return False, f"could not read {target_path}: {exc}"
        # VS Code may add a trailing newline depending on the user's
        # "files.insertFinalNewline" setting; accept either form.
        if actual.rstrip("\r\n") != body:
            return False, (
                f"file content mismatch. expected {body!r}, "
                f"got {actual!r}"
            )
        return True, f"file at {target_path} has expected body ({len(actual)} bytes)"

    return TaskSpec(
        name="04_vscode_create_file",
        goal=f"Open VS Code, create {target_path.name} containing the "
             f"sentence {body!r}, save and close VS Code.",
        api_handler=None,
        hotkey_recipe=recipe,
        # No clipboard capture needed — the validator reads the file.
        hotkey_capture=None,
        uia_recipe=[],
        electron_target=None,
        validator=_validate,
        meta={
            "file_path": str(target_path),
            "content": body,
            "user_data_dir": str(user_data_dir),
            "code_exe": code_exe,
            "platform": sys.platform,
        },
    )
