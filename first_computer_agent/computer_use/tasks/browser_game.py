"""Task — play a vision-only browser mini-game.

Two run modes, selected by ``CUA_BROWSER_GAME_HEADLESS``:

  - **headless (default)** — no on-screen window. The task's Layer-1
    ``api_handler`` launches Playwright Chromium in ``headless=True``,
    navigates to the bundled HTML, then runs a per-turn vision loop:
    grab the page viewport via ``page.screenshot()``, POST it to the
    V9 gateway's ``/v1/vision`` endpoint, and execute the model's
    single emitted click via ``page.mouse.click()``. The cascade
    therefore lands at **Layer 1 (api)** but the work is still
    100 % vision-driven (same gateway, same prompt shape, same one
    action per turn). All frames + events go through the shared
    Recorder, so the dashboard and the demo video see the trajectory
    exactly as they would for a Layer-3 run.

  - **visible** (``CUA_BROWSER_GAME_HEADLESS=0``) — pre-launches a
    Chrome / Edge ``--app`` window at a fixed screen position and
    lets the cascade fall through to **Layer 3 (vision)**, which uses
    ``host.screen.screenshot()`` + ``host.mouse.click()`` to play the
    game in the OS desktop the user can watch.

In both modes:
  - The model receives a goal phrased entirely in "what you see"
    terms (red dot on a pale-red cell among three grey cells).
  - The validator / DOM check guarantees the cascade does not report
    success until the JS has actually counted three good hits and
    painted the green ``YOU WIN`` panel.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from ..task_spec import TaskSpec


# ── bundled assets ─────────────────────────────────────────────────────────
_ASSETS_DIR = Path(__file__).resolve().parent / "browser_game_assets"
_GAME_HTML  = _ASSETS_DIR / "game.html"

# Visible-mode window geometry — the Layer-3 prompt references these
# absolute screen coordinates so the model only has to pick which of
# four known cell centres holds the red target.
_WIN_X, _WIN_Y = 80, 60
_WIN_W, _WIN_H = 1280, 820

# Headless viewport size. Cell centres below are computed from this
# size (a 2x2 grid with the score bar, gap and footer subtracted),
# and the headless prompt feeds those centres to the VLM.
_HL_VIEW_W, _HL_VIEW_H = 1280, 820

# Gateway endpoint — same one Layer 3 uses.
_GATEWAY_URL    = "http://localhost:8109"
_VISION_ENDPOINT = f"{_GATEWAY_URL}/v1/vision"

# Strict JSON schema accepted by the V9 gateway's /v1/vision endpoint.
# Mirrors layer3_vision._ACTION_SCHEMA: strict mode means EVERY
# property is required, the unused fields get empty strings / zeros.
_ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string",
                   "enum": ["click", "finish"]},
        "x":      {"type": "integer"},
        "y":      {"type": "integer"},
        "value":  {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "x", "y", "value", "reason"],
}


# ── headless: cell centres in viewport pixel coords ────────────────────────
#
# The HTML is: 90 px score bar, 28 px padding, then a 2x2 grid with
# 28 px gap, then a 56 px footer. We pre-compute the centre of each
# cell so the headless prompt can list them explicitly.
def _headless_cell_centres() -> dict[str, tuple[int, int]]:
    score_h, foot_h, pad, gap = 90, 56, 28, 28
    grid_top    = score_h + pad
    grid_left   = pad
    grid_right  = _HL_VIEW_W - pad
    grid_bottom = _HL_VIEW_H - foot_h - pad
    cell_w = (grid_right - grid_left - gap) // 2
    cell_h = (grid_bottom - grid_top - gap) // 2
    cx_left   = grid_left + cell_w // 2
    cx_right  = grid_left + cell_w + gap + cell_w // 2
    cy_top    = grid_top + cell_h // 2
    cy_bottom = grid_top + cell_h + gap + cell_h // 2
    return {
        "top-left":     (cx_left,  cy_top),
        "top-right":    (cx_right, cy_top),
        "bottom-left":  (cx_left,  cy_bottom),
        "bottom-right": (cx_right, cy_bottom),
    }


_HL_CENTRES = _headless_cell_centres()
_HL_CENTRE_LINES = "\n".join(
    f"  - {name}: ({x}, {y})" for name, (x, y) in _HL_CENTRES.items()
)


# ── visible-mode browser launch ────────────────────────────────────────────

def _resolve_browser() -> tuple[str, str]:
    """Find a Chrome / Edge executable (visible mode only)."""
    env = os.environ.get("CUA_BROWSER_EXE")
    if env and Path(env).exists():
        return env, "custom"
    candidates: list[tuple[str, str]] = []
    for name, kind in (("chrome", "chrome"), ("chrome.exe", "chrome"),
                       ("msedge", "edge"),   ("msedge.exe", "edge")):
        located = shutil.which(name)
        if located:
            candidates.append((located, kind))
    pf      = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86    = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_a = os.environ.get("LocalAppData", "")
    for cand, kind in (
        (Path(pf)      / "Google/Chrome/Application/chrome.exe", "chrome"),
        (Path(pf86)    / "Google/Chrome/Application/chrome.exe", "chrome"),
        (Path(local_a) / "Google/Chrome/Application/chrome.exe", "chrome"),
        (Path(pf)      / "Microsoft/Edge/Application/msedge.exe", "edge"),
        (Path(pf86)    / "Microsoft/Edge/Application/msedge.exe", "edge"),
    ):
        if cand.exists():
            candidates.append((str(cand), kind))
    if not candidates:
        raise RuntimeError(
            "No Chrome or Edge install found. Set CUA_BROWSER_EXE to "
            "an absolute path to a Chromium-family browser executable."
        )
    return candidates[0]


def _launch_game_window() -> subprocess.Popen:
    """Spawn the browser in app mode pointed at the bundled HTML.
    Visible mode only. Foreground-grant rules match layer2a_hotkeys."""
    exe, _kind = _resolve_browser()
    user_data_dir = Path(tempfile.gettempdir()) / "cua-browser-game-profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    url = _GAME_HTML.resolve().as_uri()
    args = [
        exe,
        f"--app={url}",
        f"--window-position={_WIN_X},{_WIN_Y}",
        f"--window-size={_WIN_W},{_WIN_H}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,InfobarUI",
    ]
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ── visible-mode Layer-3 prompt (used when CUA_BROWSER_GAME_HEADLESS=0) ────

_CELL_CENTRES = {
    "top-left":     (_WIN_X + int(_WIN_W * 0.27), _WIN_Y + 90 + int((_WIN_H - 90 - 56) * 0.27)),
    "top-right":    (_WIN_X + int(_WIN_W * 0.73), _WIN_Y + 90 + int((_WIN_H - 90 - 56) * 0.27)),
    "bottom-left":  (_WIN_X + int(_WIN_W * 0.27), _WIN_Y + 90 + int((_WIN_H - 90 - 56) * 0.73)),
    "bottom-right": (_WIN_X + int(_WIN_W * 0.73), _WIN_Y + 90 + int((_WIN_H - 90 - 56) * 0.73)),
}
_CENTRE_LINES = "\n".join(
    f"  - {name}: ({x}, {y})" for name, (x, y) in _CELL_CENTRES.items()
)
_VISION_GOAL = (
    "You are playing a small in-browser game. The browser window is "
    "in the foreground at roughly the top-left of the screen. The "
    "window contains:\n"
    "  - a dark navy SCORE bar across the top reading 'SCORE n / 3',\n"
    "  - a 2-by-2 grid of large rounded cells filling the middle,\n"
    "  - a thin footer at the bottom.\n"
    "Exactly ONE cell at a time is the TARGET. The target cell has "
    "a bright pale-red background, a big solid RED circle in its "
    "centre, and the word 'CLICK' under the circle. The other three "
    "cells are pale grey and EMPTY.\n"
    "Your job each turn: pick the cell that contains the red circle "
    "and emit ONE click at its centre. Use these screen coordinates "
    "for the four cell centres:\n"
    f"{_CENTRE_LINES}\n"
    "After a successful click the score increments and a NEW cell "
    "becomes the target (the old one goes empty). Keep clicking the "
    "current target until you see a SOLID GREEN full-window panel "
    "with the giant white words 'YOU WIN' (and 'SCORE 3 OF 3'). "
    "As soon as that green WIN screen is visible, emit "
    "{\"action\":\"finish\",\"value\":\"GAME WON\","
    "\"reason\":\"green YOU WIN panel visible\"}. "
    "Rules: ONE action per turn. NEVER type, NEVER press keys, "
    "NEVER click outside the browser window, NEVER click on a "
    "grey empty cell. If no red target is visible (the page is "
    "still loading), click the centre of the window at "
    f"({_WIN_X + _WIN_W // 2}, {_WIN_Y + _WIN_H // 2}) once to "
    "give it focus and try again next turn."
)

_VERIFY_GOAL = (
    "Look at this screenshot of the same browser game. Did the LAST "
    "click either (a) increase the score number in the dark navy "
    "bar at the top, or (b) replace the grid with a SOLID GREEN "
    "panel reading 'YOU WIN'? If yes (score advanced OR win panel "
    "is visible) reply ok=true. If the score number is unchanged "
    "AND no green win panel is visible, reply ok=false."
)


# ── headless prompt + vision call ──────────────────────────────────────────

_HL_VISION_GOAL = (
    "You are playing a small in-browser game. This screenshot is the "
    f"FULL {_HL_VIEW_W}x{_HL_VIEW_H} page viewport (coordinates "
    f"0..{_HL_VIEW_W} left-right, 0..{_HL_VIEW_H} top-bottom). It "
    "contains:\n"
    "  - a dark navy SCORE bar across the top reading 'SCORE n / 3',\n"
    "  - a 2-by-2 grid of large rounded cells filling the middle,\n"
    "  - a thin footer at the bottom reading 'CLICK THE RED TARGET'.\n"
    "Exactly ONE cell at a time is the TARGET. The target cell has "
    "a bright pale-red background, a big solid RED circle in its "
    "centre, and the word 'CLICK' under the circle. The other three "
    "cells are pale grey and EMPTY.\n"
    "Your job each turn: pick the cell that contains the red circle "
    "and emit ONE click at its centre. Use these VIEWPORT pixel "
    "coordinates for the four cell centres:\n"
    f"{_HL_CENTRE_LINES}\n"
    "After a successful click the score increments and a NEW cell "
    "becomes the target. Keep clicking the current target until you "
    "see a SOLID GREEN full-viewport panel with the giant white "
    "words 'YOU WIN' (and 'SCORE 3 OF 3'). As soon as that green "
    "WIN screen is visible, emit "
    "{\"action\":\"finish\",\"value\":\"GAME WON\","
    "\"reason\":\"green YOU WIN panel visible\"}. "
    "Rules: ONE action per turn. NEVER click on a grey empty cell."
)


async def _call_vlm(png: bytes, prompt: str, *,
                    agent: str, session: str) -> dict:
    """POST a single page-viewport PNG to the V9 gateway's vision
    endpoint and return the parsed action dict.

    Same wire shape as layer3_vision._call_vlm (data: URL image,
    strict response schema, three-attempt 503 cooldown loop)."""
    b64 = base64.b64encode(png).decode("ascii")
    payload = {
        "prompt": prompt,
        "image": f"data:image/png;base64,{b64}",
        "agent": agent,
        "session": session,
        "max_tokens": 300,
        "temperature": 0.0,
        "schema": _ACTION_SCHEMA,
        "schema_name": "BrowserGameAction",
    }
    last_status: int | None = None
    last_body: str = ""
    async with httpx.AsyncClient(timeout=45.0) as cli:
        for attempt in range(3):
            r = await cli.post(_VISION_ENDPOINT, json=payload)
            last_status, last_body = r.status_code, r.text[:300]
            if r.status_code == 200:
                break
            if r.status_code == 503 and "cooldown" in r.text.lower():
                await asyncio.sleep(8.0 * (attempt + 1))
                continue
            break
    if last_status != 200:
        raise RuntimeError(f"/v1/vision returned {last_status}: {last_body}")
    body = r.json()
    parsed = body.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    text = body.get("text") or body.get("content") or ""
    return json.loads(text)


# ── headless: the api_handler the cascade lands on ────────────────────────

async def _headless_play(host, recorder) -> tuple[bool, str | None, str]:
    """Play the game vision-only inside a headless Chromium.

    Receives the cua ``host`` (unused — we don't touch the OS desktop
    in headless mode) and the shared ``Recorder`` so frames and
    events show up in the trajectory exactly like a Layer-3 run.
    """
    # Lazy import so machines that don't have Playwright wheels can
    # still load the task module for visible-mode runs.
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        # `NotImplementedError` makes Layer 1 mark itself
        # non-applicable instead of failing, so the cascade can fall
        # through to Layer 3 / visible mode if the user installed
        # Chrome but not Playwright.
        raise NotImplementedError(
            f"Playwright not available — cannot run headless: {exc}"
        )

    max_turns = int(os.environ.get("CUA_BROWSER_GAME_MAX_TURNS", "8"))
    session = getattr(recorder, "session", "default")
    url = _GAME_HTML.resolve().as_uri()
    turns_played = 0
    captured: str | None = None
    win_seen_via_dom = False

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as exc:
            # Same fall-through trick: if the Playwright chromium
            # binary isn't installed, escalate to visible mode.
            raise NotImplementedError(
                f"playwright chromium binary missing — run "
                f"`playwright install chromium`: {exc}"
            )

        ctx = await browser.new_context(
            viewport={"width": _HL_VIEW_W, "height": _HL_VIEW_H},
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="load")
        # Tiny settle so the JS that paints the first target has run.
        await asyncio.sleep(0.4)
        recorder.event("headless_browser", url=url,
                       viewport=[_HL_VIEW_W, _HL_VIEW_H])
        try:
            for turn in range(1, max_turns + 1):
                turns_played = turn
                # SCAN — viewport-only screenshot (no chrome UI, no
                # desktop wallpaper) keeps the prompt's "FULL viewport"
                # claim true.
                png = await page.screenshot(type="png", full_page=False)
                recorder.frame(png, label=f"headless vision turn {turn}")

                # ACT — one VLM round-trip per turn.
                t0 = time.time()
                try:
                    decision = await _call_vlm(
                        png, _HL_VISION_GOAL,
                        agent="computer_use", session=session,
                    )
                except Exception as exc:
                    recorder.event("vlm_call", layer="api", turn=turn,
                                   error=str(exc))
                    return False, None, (
                        f"VLM call failed on turn {turn}: {exc}"
                    )
                recorder.event("vlm_call", layer="api", turn=turn,
                               elapsed_s=round(time.time() - t0, 3),
                               decision=decision)

                op = decision.get("action")
                if op == "finish":
                    captured = decision.get("value") or "GAME WON"
                    # DOM-verify: only honour `finish` when the WIN
                    # panel is actually showing. Catches the model
                    # hallucinating success.
                    try:
                        win_seen_via_dom = bool(await page.evaluate(
                            "document.getElementById('win')"
                            ".classList.contains('show')"
                        ))
                    except Exception:
                        win_seen_via_dom = False
                    recorder.event("action", layer="api",
                                   action="finish", value=captured,
                                   dom_win_visible=win_seen_via_dom,
                                   reason=decision.get("reason"))
                    # Snap a final frame so the trajectory always ends
                    # on the green WIN panel (or whatever the model
                    # mistakenly called WIN, for debugging).
                    final_png = await page.screenshot(type="png",
                                                      full_page=False)
                    recorder.frame(final_png, label="final state")
                    break
                if op == "click":
                    x = int(decision.get("x", 0))
                    y = int(decision.get("y", 0))
                    # Clip to viewport so a wild guess doesn't raise.
                    x = max(1, min(_HL_VIEW_W - 1, x))
                    y = max(1, min(_HL_VIEW_H - 1, y))
                    await page.mouse.click(x, y)
                    recorder.event("action", layer="api",
                                   action="click", x=x, y=y,
                                   reason=decision.get("reason"))
                    # Settle so the next screenshot reliably shows the
                    # new target (the JS waits 450 ms before redraw).
                    await asyncio.sleep(0.7)
                else:
                    recorder.event("action", layer="api",
                                   action="ignored", op=op,
                                   reason=decision.get("reason"))
                if turn == max_turns:
                    final_png = await page.screenshot(type="png",
                                                      full_page=False)
                    recorder.frame(final_png, label="final state")
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    # Final verdict.
    if captured and win_seen_via_dom:
        return True, captured, (
            f"headless win after {turns_played} turn(s); "
            f"DOM #win.show=true; captured={captured!r}"
        )
    if captured and not win_seen_via_dom:
        return False, captured, (
            f"model emitted finish={captured!r} but DOM shows the "
            f"WIN panel is NOT active — agent claimed a win it did "
            f"not actually achieve"
        )
    return False, None, (
        f"exhausted {turns_played} turn(s) without a finish action"
    )


# ── mode selection ────────────────────────────────────────────────────────

def _is_headless() -> bool:
    """Headless is the default. Set CUA_BROWSER_GAME_HEADLESS=0 to
    opt back into the visible Chrome window."""
    return os.environ.get("CUA_BROWSER_GAME_HEADLESS", "1").strip() != "0"


# ── visible-mode validator ────────────────────────────────────────────────

async def _validate_visible(out, host) -> tuple[bool, str]:
    """Confirm the on-screen WIN panel is actually visible.

    Visible mode only. The headless api_handler does its own DOM-level
    verification, so headless runs don't attach a validator at all.
    """
    try:
        from PIL import Image  # type: ignore
        import io
    except Exception as exc:
        return True, f"trust-only validation (Pillow missing): {exc}"
    try:
        png = await host.screen.screenshot()
        w, h = await host.screen.size()
    except Exception as exc:
        return True, f"trust-only validation (screenshot failed): {exc}"
    img = Image.open(io.BytesIO(png)).convert("RGB")
    cx = _WIN_X + _WIN_W // 2
    cy = _WIN_Y + _WIN_H // 2
    if not (0 <= cx < w and 0 <= cy < h):
        return True, "trust-only validation (centre off-screen)"
    patch = img.crop((cx - 20, cy - 20, cx + 20, cy + 20))
    rs, gs, bs = 0, 0, 0
    for r, g, b in patch.getdata():
        rs += r; gs += g; bs += b
    n = patch.size[0] * patch.size[1]
    r_avg, g_avg, b_avg = rs // n, gs // n, bs // n
    msg = f"centre_rgb=({r_avg},{g_avg},{b_avg}) at ({cx},{cy})"
    is_green = g_avg > 110 and g_avg > r_avg + 30 and g_avg > b_avg + 30
    if is_green:
        return True, f"WIN panel visible: {msg}"
    return False, (
        f"WIN panel NOT visible at game-window centre — agent may "
        f"have given up before the third hit, or the window lost "
        f"focus: {msg}"
    )


# ── build ─────────────────────────────────────────────────────────────────

def build(**_ignored) -> TaskSpec:
    """Return the TaskSpec for the requested mode.

    Headless (default): cascade lands at Layer 1 (api). Frames written
    by the headless player are page-viewport screenshots, not desktop
    captures, so nothing visible happens on the user's screen.

    Visible (``CUA_BROWSER_GAME_HEADLESS=0``): cascade lands at
    Layer 3 (vision). Chrome / Edge opens on the desktop at a fixed
    position; ``host.screen.screenshot()`` feeds the gateway.
    """
    if not _GAME_HTML.exists():
        raise RuntimeError(f"bundled game asset missing: {_GAME_HTML}")

    if _is_headless():
        return TaskSpec(
            name="05_browser_game_vision",
            goal="Play a small in-browser click-the-red-target game "
                 "headlessly using vision only and report the win.",
            # Layer 1: the headless vision player. The 2-arg handler
            # opts into the new Layer-1 recorder-forwarding API.
            api_handler=_headless_play,
            # Cascade fallbacks intentionally absent.
            hotkey_recipe=[],
            uia_recipe=[],
            electron_target=None,
            vision_goal=None,
            validator=None,
            meta={
                "mode": "headless",
                "expected_layer": "api",
                "expected_finish_substring": "WIN",
                "game_html": str(_GAME_HTML),
                "viewport": {"w": _HL_VIEW_W, "h": _HL_VIEW_H},
                "cell_centres": {k: list(v) for k, v in _HL_CENTRES.items()},
            },
        )

    # Visible mode: pre-launch the browser so Layer 3 has something
    # to screenshot.
    _launch_game_window()
    boot_s = float(os.environ.get("CUA_BROWSER_GAME_BOOT_S", "2.5"))
    time.sleep(boot_s)

    return TaskSpec(
        name="05_browser_game_vision",
        goal="Play a small in-browser click-the-red-target game by "
             "vision only and report the win.",
        api_handler=None,
        hotkey_recipe=[],
        uia_recipe=[],
        electron_target=None,
        vision_goal=_VISION_GOAL,
        vision_region=None,
        max_vision_turns=8,
        verify_goal=_VERIFY_GOAL,
        max_verify_failures=2,
        validator=_validate_visible,
        meta={
            "mode": "visible",
            "target_app": "chrome|edge --app",
            "expected_layer": "vision",
            "expected_finish_substring": "WIN",
            "game_html": str(_GAME_HTML),
            "window": {"x": _WIN_X, "y": _WIN_Y, "w": _WIN_W, "h": _WIN_H},
        },
    )
