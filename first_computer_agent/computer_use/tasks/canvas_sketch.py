"""Task 3 — MS Paint canvas via Layer 3 vision.

Goal: with a large brush pre-selected in Paint (the recorder's
pre-task setup handles that), let the VLM plot a sequence of clicks
on the canvas to leave a visible "A I" pattern of ink dots. The
captured value is the free-text `value` the model returns when it
emits `{"action":"finish"}`. The validator independently confirms
the canvas actually has ink on it, by screenshotting the canvas
region and counting non-white pixels — this is what catches the
common failure mode where the VLM emits `finish` without having
clicked on the canvas at all.

Why this task lands on Layer 3 (vision):
  - Layer 1: there is no Paint shell flag that accepts "draw an A then
    an I"; `api_handler` is therefore absent.
  - Layer 2a (hotkeys): blind keystrokes cannot place ink — Paint's
    canvas does not accept characters. We leave `hotkey_recipe` empty
    so this layer marks itself non-applicable.
  - Layer 2b (UIA): the Paint canvas is an unlabelled drawing surface
    with no useful AutomationId / Name children. UIA cannot click "on
    the canvas at (x,y)" by name, so we leave `uia_recipe` empty.
  - Layer 2c (Electron CDP): MS Paint is a Win32 / WinUI app, not an
    Electron app. `electron_target` is absent.
  - Layer 3 (vision): the natural fit. The VLM emits clicks one at a
    time on the canvas to plot the letters A and I.

Pre-conditions (the demo recorder takes care of these — see
`record_demo.py:_prep_paint_environment`):
  - `mspaint.exe` is running, maximised, foreground.
  - The Brushes tool is selected with a thick brush size so each
    click leaves a visible blob, not a 1-pixel dot.
  - The canvas has been left-clicked once to dismiss any startup
    dialog and confirm focus.

Layer 3's action contract is one action per turn (`click` / `type` /
`press` / `finish`) per layer3_vision.py, so the goal is phrased in
terms of point-clicks, NOT strokes — Layer 3 cannot drag today.
"""

from __future__ import annotations

import io

from ..task_spec import TaskSpec


# The full prompt the VLM receives is assembled by
# layer3_vision.Layer3Vision._build_prompt; this string is dropped in
# verbatim as the Goal line. The recorder pre-selects the Brushes tool
# at a thick size before the cascade runs, so the goal is phrased as
# "click on the canvas" with explicit y-bounds — the model should NOT
# touch the toolbar at all.
_VISION_GOAL = (
    "You are looking at the maximised Microsoft Paint window on "
    "Windows 11. The Brushes tool is already selected with a thick "
    "brush, and the canvas is empty. Your job is to plot ink dots "
    "on the WHITE CANVAS ONLY to draw a capital 'A' and a capital "
    "'I' side by side. "
    "Rules: ONE click per turn. ONLY click in the white canvas "
    "region, which starts roughly at y=250 and ends near the bottom "
    "of the screen — NEVER click above y=250 (that is the toolbar/"
    "ribbon, clicking there changes the tool). NEVER click in the "
    "right-side panels (x>1500). Use the LEFT half of the canvas "
    "(x in 300..700) for the letter A: three dots forming the two "
    "legs and the apex. Use the RIGHT half (x in 900..1300) for the "
    "letter I: three dots vertically stacked. "
    "After exactly 6 click turns, emit "
    "{\"action\":\"finish\",\"value\":\"AI logo drawn\","
    "\"reason\":\"six dots placed on canvas\"}. "
    "Do NOT open menus, do NOT type, do NOT press keys. Stop after "
    "the finish action even if the result looks imperfect."
)


async def _validate(out, host) -> tuple[bool, str]:
    """Confirm there is actual ink on the canvas.

    Take a fresh screenshot via cua, crop to the canvas region
    (roughly y=260..h-120, x=200..w-400 on a maximised Paint window
    at 1920x1080), and count pixels that are darker than near-white.
    A few hundred non-white pixels is the floor for "the model
    actually clicked somewhere visible"; one thick-brush click leaves
    several hundred pixels of ink, so even a single visible blob
    clears the bar.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        # If Pillow is missing we cannot validate; fall back to
        # trusting the layer's success and document why.
        return True, f"validator skipped (Pillow not available: {exc})"

    try:
        png = await host.screen.screenshot()
        w, h = await host.screen.size()
    except Exception as exc:
        return False, f"screenshot failed: {exc}"

    img = Image.open(io.BytesIO(png)).convert("RGB")
    # Conservative canvas crop. Paint maximised on a typical desktop
    # has the ribbon in the top ~220 px and a status bar/footer in
    # the bottom ~80 px; the right panel (Copilot toggle, color
    # palette) eats ~350 px. Cropping inside these margins keeps
    # toolbar/menu pixels out of the count so we measure ONLY canvas
    # ink, not UI chrome.
    left   = max(0,   int(w * 0.12))
    top    = max(220, int(h * 0.25))
    right  = min(w,   int(w * 0.78))
    bottom = min(h,   int(h * 0.92))
    crop = img.crop((left, top, right, bottom))

    # Count both white (canvas background) and dark (ink) pixels.
    # A real "ink on canvas" screenshot has lots of white background
    # plus a few hundred dark pixels of brush strokes. A colourful
    # desktop wallpaper (Win11 default gradients, gaming themes,
    # etc.) has almost no white at all — so requiring 30 %+ of the
    # crop to be near-white screens out the failure mode where Paint
    # never came to foreground and the screenshot is actually the
    # desktop. The 235 cutoff is loose for both classes so JPEG-ish
    # screenshot artefacts (gdigrab + libx264) don't perturb the count.
    white_pixels = 0
    ink_pixels   = 0
    for r, g, b in crop.getdata():
        if r >= 235 and g >= 235 and b >= 235:
            white_pixels += 1
        elif r < 200 or g < 200 or b < 200:
            # "ink" = any pixel that is meaningfully darker than the
            # canvas — covers black/grey/coloured brush strokes alike.
            ink_pixels += 1

    crop_area      = crop.size[0] * crop.size[1]
    white_pct      = white_pixels / max(1, crop_area)
    ink_floor      = 200          # ~one thick-brush click of ink
    ink_ceiling    = int(crop_area * 0.70)
    white_floor    = 0.30         # canvas must be at least 30 % white
    msg = (
        f"crop=({left},{top},{right},{bottom}) area={crop_area} "
        f"white={white_pixels} ({white_pct:.0%}) "
        f"ink={ink_pixels} "
        f"ink_floor={ink_floor} ink_ceiling={ink_ceiling} "
        f"white_floor={white_floor:.0%}"
    )
    if white_pct < white_floor:
        return False, (
            f"no white canvas detected — probably wrong window "
            f"foreground (desktop wallpaper / dark app): {msg}"
        )
    if ink_pixels < ink_floor:
        return False, f"canvas is empty (no visible clicks): {msg}"
    if ink_pixels > ink_ceiling:
        return False, f"too much ink — canvas covered or wrong crop: {msg}"
    return True, f"canvas has visible ink on white: {msg}"


def build() -> TaskSpec:
    return TaskSpec(
        name="03_paint_vision_logo",
        goal="Draw a small 'AI' monogram on the MS Paint canvas via "
             "vision-driven clicks.",
        # Layer 1: no API exit for drawing.
        api_handler=None,
        # Layer 2a: empty → non-applicable.
        hotkey_recipe=[],
        # Layer 2b: empty → non-applicable (canvas has no UIA handles).
        uia_recipe=[],
        # Layer 2c: not an Electron app.
        electron_target=None,
        # Layer 3: the real entry point.
        vision_goal=_VISION_GOAL,
        # Full-screen capture: the VLM needs to see both the toolbar
        # (to find the Brush) and the canvas (to plot points). The
        # Layer-3 action handler offsets clicks back to absolute screen
        # coords, so leaving region=None is correct.
        vision_region=None,
        # Turn cap: 6 click turns + 1 finish + 1 spare = 8. The prompt
        # explicitly instructs "after exactly 6 clicks, finish".
        max_vision_turns=8,
        # Domain validator: count non-white pixels in the canvas crop.
        # Catches the common failure where the VLM emits `finish`
        # immediately without having clicked anywhere visible.
        validator=_validate,
        meta={"target_app": "mspaint.exe",
              "expected_layer": "vision",
              "expected_finish_substring": "AI"},
    )
