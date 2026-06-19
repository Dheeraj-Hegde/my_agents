"""Layer 3 — vision Set-of-Marks (cua-driven).

The expensive last resort. Uses `host.screen.screenshot()` for capture
and `host.mouse.click / host.keyboard.type / host.keyboard.keypress`
for the actions the VLM emits — no direct pyautogui / mss anywhere.
The VLM itself is the V9 gateway's `/v1/vision` endpoint; that's the
single OS-external call this layer makes.

Single-call design: each turn produces *one* action (click, type,
press, or finish). The cascade keeps calling Layer 3 until the task is
finished or `task.max_vision_turns` is hit. This mirrors the Browser
skill's Layer-3 turn loop.

Per-turn loop (scan → act → verify): every turn takes a fresh
screenshot, asks the VLM for one action, runs it, and (when
`task.verify_goal` is set) re-screenshots and asks the VLM whether
the post-condition holds. A streak of `task.max_verify_failures`
verify=False verdicts aborts Layer 3 so the cascade can escalate —
preventing the layer from burning its full turn budget on a stuck
state. Verify is a separate VLM round-trip and is counted in
`vision_calls`. When `verify_goal` is None, the loop degrades to the
historical act-only behaviour and existing tasks are unaffected.

Expected VLM JSON contract:
    {"action": "click",  "x": 540, "y": 320, "reason": "..."}
    {"action": "type",   "text": "...",       "reason": "..."}
    {"action": "press",  "key":  "enter",     "reason": "..."}
    {"action": "finish", "value": "...",      "reason": "..."}

Verify response contract (post-condition gate):
    {"ok": true|false, "reason": "..."}

The contract is enforced by JSON parse-and-keys check; anything else
is treated as an interaction failure and the cascade gives up on
Layer 3 rather than thrashing.
"""

from __future__ import annotations

import asyncio
import ast
import base64
import io
import json
import re
import time

import httpx

from ..recorder import Recorder
from ..schemas import LayerOutcome
from ..task_spec import TaskSpec


GATEWAY_URL = "http://localhost:8109"
VISION_ENDPOINT = f"{GATEWAY_URL}/v1/vision"


# Strict JSON schema for the single-action-per-turn contract. Every
# property is `required` because the V9 gateway hardcodes strict=True
# on `response_format` (see schemas.py: VisionRequest → ResponseFormat),
# and OpenAI-style strict mode rejects schemas with unlisted properties.
# Action-specific fields the model doesn't need get filled with empty
# strings / zeros, and _do_action ignores any field it isn't using.
_ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string",
                   "enum": ["click", "type", "press", "finish"]},
        "x":      {"type": "integer"},
        "y":      {"type": "integer"},
        "text":   {"type": "string"},
        "key":    {"type": "string"},
        "value":  {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "x", "y", "text", "key", "value", "reason"],
}


# Strict JSON schema for the per-turn verify call. The verify VLM only
# answers "did the post-condition hold after the last action?" so the
# shape is intentionally tiny — `ok` (bool) plus a short reason. Same
# strict-mode rules as _ACTION_SCHEMA apply.
_VERIFY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ok":     {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["ok", "reason"],
}


def _crop_png(png: bytes, region: dict) -> bytes:
    """Crop a PNG to the requested region. Lazy-imports Pillow so
    machines without Pillow can still run zero-vision tasks."""
    from PIL import Image  # type: ignore
    img = Image.open(io.BytesIO(png))
    left = int(region.get("left", 0))
    top = int(region.get("top", 0))
    right = left + int(region.get("width", img.width))
    bottom = top + int(region.get("height", img.height))
    crop = img.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


async def _grab(host, region: dict | None) -> tuple[bytes, dict]:
    """Take a screenshot via cua; optionally crop to `region`. Returns
    (png_bytes, region_used). The returned `region` has absolute
    left/top so the Layer-3 action handler can offset clicks."""
    raw = await host.screen.screenshot()
    if region is None:
        w, h = await host.screen.size()
        return raw, {"left": 0, "top": 0, "width": w, "height": h}
    return _crop_png(raw, region), {
        "left": int(region.get("left", 0)),
        "top": int(region.get("top", 0)),
        "width": int(region.get("width", 0)),
        "height": int(region.get("height", 0)),
    }


async def _call_vlm(png: bytes, prompt: str, *, agent: str,
                    session: str,
                    schema: dict | None = None,
                    schema_name: str = "VisionAction") -> dict:
    """POST to V9's /v1/vision. Returns the parsed JSON object the
    model emitted. Raises on transport / non-JSON response.

    `schema` defaults to the action schema; verify calls override with
    _VERIFY_SCHEMA so the provider is forced into the right shape."""
    b64 = base64.b64encode(png).decode("ascii")
    # V9 /v1/vision expects `image` as a data: URL or http(s) URL —
    # NOT a raw base64 string under `image_b64`. The earlier shape
    # silently returned HTTP 422 ("Field required: body.image") and
    # the cascade aborted before any VLM turn could fire.
    #
    # We also send a strict JSON schema so the provider is forced into
    # structured-output mode and cannot return malformed JSON (the
    # symptom that killed the first re-record: unquoted keys like
    # `y:125`). All fields are required because the gateway hardcodes
    # `strict=True` for response_format; the model fills empty strings
    # / zeros for fields irrelevant to its chosen action and the
    # downstream _do_action ignores them.
    payload = {
        "prompt": prompt,
        "image": f"data:image/png;base64,{b64}",
        "agent": agent,
        "session": session,
        "max_tokens": 400,
        "temperature": 0.0,
        "schema": schema if schema is not None else _ACTION_SCHEMA,
        "schema_name": schema_name,
    }
    async with httpx.AsyncClient(timeout=45.0) as cli:
        # Provider cooldown handling: the V9 gateway routes vision
        # exclusively to Gemini in our free-tier setup. When the
        # per-minute RPM bucket is empty Gemini reports a short
        # cooldown and the gateway returns 503 with
        # `last_error: None`. A single re-try after a generous sleep
        # almost always succeeds because the bucket refills in <60s.
        for attempt in range(3):
            r = await cli.post(VISION_ENDPOINT, json=payload)
            if r.status_code == 200:
                break
            if r.status_code == 503 and "cooldown" in r.text.lower():
                await asyncio.sleep(8.0 * (attempt + 1))
                continue
            break
    if r.status_code != 200:
        raise RuntimeError(
            f"/v1/vision returned {r.status_code}: {r.text[:300]}"
        )
    body = r.json()
    text = body.get("text") or body.get("content") or ""
    # Some providers return the structured object in `.parsed` directly
    # when a JSON schema was sent. We don't send a schema here (the union
    # of action shapes is awkward to express in strict mode), so we
    # always reach the text path — but if a future caller adds a schema,
    # honour the parsed object first.
    parsed = body.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    return _extract_action_json(text)


# Matches the FIRST balanced-looking JSON object in a chunk of model
# text. The model often wraps its answer in prose ("Here is the next
# action: { ... } Reason: ...") and the strict json.loads on the raw
# string fails. This regex grabs the outermost {...} run.
_FIRST_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Quote bare-identifier keys: `{"x":1,y:2}` → `{"x":1,"y":2}`. The
# model occasionally drops a key's quotes even under structured-output
# mode for providers that ignore response_format. The pattern requires
# `{` or `,` immediately before the bareword so we don't touch
# identifiers inside string values.
_UNQUOTED_KEY_RE = re.compile(
    r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)'
)


def _fix_unquoted_keys(s: str) -> str:
    return _UNQUOTED_KEY_RE.sub(r'\1"\2"\3', s)


def _extract_action_json(text: str) -> dict:
    """Pull a single action dict out of a noisy VLM response.

    The Layer 3 contract is one JSON object per turn. Real models
    routinely violate the strict shape: they wrap in markdown fences,
    prepend prose, use single quotes, drop key quotes, or trail commas.
    We try, in order:
      1. json.loads on the raw stripped text
      2. strip ``` fences (markdown / 'json' tag) and retry
      3. extract the first {...} run and retry json.loads
      4. fix unquoted bare-identifier keys, retry json.loads
      5. ast.literal_eval (handles single-quoted dicts)
    Raise RuntimeError with the raw text on total failure so the
    cascade's error message is actionable instead of opaque.
    """
    raw = text.strip()
    candidates: list[str] = []

    # 1. raw, as-is
    candidates.append(raw)

    # 2. de-fence markdown
    if "```" in raw:
        parts = raw.split("```")
        # Code-fenced blocks alternate text/code/text/...; take any
        # part that *looks* like a JSON object.
        for p in parts:
            p_s = p.strip()
            if p_s.lower().startswith("json"):
                p_s = p_s[4:].strip()
            if p_s.startswith("{"):
                candidates.append(p_s)

    # 3. first {...} run inside whatever's left
    for c in list(candidates):
        m = _FIRST_OBJECT_RE.search(c)
        if m:
            candidates.append(m.group(0))

    # 4. quote-the-bare-keys variant of each candidate
    for c in list(candidates):
        fixed = _fix_unquoted_keys(c)
        if fixed != c:
            candidates.append(fixed)

    last_err: Exception | None = None
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception as e:
            last_err = e
        else:
            if isinstance(obj, dict):
                return obj
        # ast fallback handles Python-style {'action': 'click', ...}
        try:
            obj = ast.literal_eval(c)
        except Exception as e:
            last_err = e
            continue
        if isinstance(obj, dict):
            return obj

    raise RuntimeError(
        f"VLM response is not a JSON object: "
        f"last_err={type(last_err).__name__}: {last_err}; "
        f"raw_text={text[:400]!r}"
    )


async def _do_action(action: dict, host, recorder: Recorder,
                     region: dict) -> str | None:
    """Execute one VLM-emitted action via cua. Returns captured value
    when the action is `finish`."""
    op = action.get("action")
    if op == "click":
        # Coordinates are relative to the screenshotted region; offset
        # back into absolute screen coords for the click.
        x = int(action["x"]) + int(region.get("left", 0))
        y = int(action["y"]) + int(region.get("top", 0))
        await host.mouse.click(x, y)
        recorder.event("action", layer="vision", action="click",
                       x=x, y=y, reason=action.get("reason"))
    elif op == "type":
        await host.keyboard.type(str(action.get("text", "")))
        recorder.event("action", layer="vision", action="type",
                       text=action.get("text"))
    elif op == "press":
        key = action.get("key", "")
        if not key:
            raise RuntimeError("vision press action missing key")
        await host.keyboard.keypress([key])
        recorder.event("action", layer="vision", action="press",
                       key=key)
    elif op == "finish":
        recorder.event("action", layer="vision", action="finish",
                       value=action.get("value"))
        return action.get("value")
    else:
        raise RuntimeError(f"unknown VLM action: {op!r}")
    return None


class Layer3Vision:
    name = "vision"

    def __init__(self, *, agent: str = "computer_use",
                 session: str = "default"):
        self.agent = agent
        self.session = session

    async def try_(self, task: TaskSpec, host, recorder: Recorder) -> LayerOutcome:
        if not task.vision_goal:
            recorder.event("layer_try", layer=self.name, applicable=False,
                           reason="no vision_goal on task")
            return LayerOutcome(layer=self.name, applicable=False,
                                detail="task has no vision_goal")

        recorder.event("layer_try", layer=self.name, applicable=True,
                       max_turns=task.max_vision_turns)

        actions: list[dict] = []
        captured: str | None = None
        turn = 0
        # Per-turn "scan → act → verify" loop (see docstring + the
        # cascade diagram). `consecutive_verify_failures` counts only
        # back-to-back verify=False verdicts; one bad turn is a blip,
        # `max_verify_failures` in a row means the layer is stuck.
        consecutive_verify_failures = 0

        try:
            for turn in range(1, task.max_vision_turns + 1):
                # ── SCAN ───────────────────────────────────────────────
                png, region = await _grab(host, task.vision_region)
                frame_path = recorder.frame(png, label=f"vision turn {turn}")

                prompt = self._build_prompt(task, turn)
                t0 = time.time()
                try:
                    decision = await _call_vlm(
                        png, prompt,
                        agent=self.agent, session=self.session,
                    )
                except Exception as exc:
                    recorder.event(
                        "layer_result", layer=self.name, success=False,
                        error=str(exc), reason="VLM call failed",
                    )
                    return LayerOutcome(
                        layer=self.name, success=False, error=str(exc),
                        actions=actions, frames=[frame_path],
                        detail=f"VLM unreachable / non-JSON: {exc}",
                    )

                recorder.event(
                    "vlm_call", layer=self.name, turn=turn,
                    elapsed_s=round(time.time() - t0, 3),
                    decision=decision,
                )
                actions.append({"layer": "vision", "turn": turn,
                                "decision": decision})

                # ── ACT ────────────────────────────────────────────────
                value = await _do_action(decision, host, recorder, region)
                if decision.get("action") == "finish":
                    captured = value
                    break

                # ── VERIFY (post-condition) ────────────────────────────
                # Skipped when the task didn't define a post-condition;
                # the loop then degrades to the old act-only behaviour.
                if task.verify_goal:
                    # Brief settle pause before the verify screenshot so
                    # the UI has time to repaint the post-action state.
                    await asyncio.sleep(0.6)
                    v_png, _ = await _grab(host, task.vision_region)
                    recorder.frame(v_png, label=f"vision turn {turn} verify")
                    v_prompt = self._build_verify_prompt(
                        task, turn, last_action=decision,
                    )
                    try:
                        verdict = await _call_vlm(
                            v_png, v_prompt,
                            agent=self.agent, session=self.session,
                            schema=_VERIFY_SCHEMA,
                            schema_name="VisionVerify",
                        )
                    except Exception as exc:
                        # Verify-call transport failure is treated as
                        # "unknown" — record it, but do NOT fail the
                        # layer outright; the next scan may recover.
                        recorder.event(
                            "verify", layer=self.name, turn=turn,
                            ok=None, error=str(exc),
                        )
                        verdict = {"ok": True,
                                   "reason": f"verify call failed: {exc}"}
                    ok = bool(verdict.get("ok"))
                    recorder.event(
                        "verify", layer=self.name, turn=turn,
                        ok=ok, reason=verdict.get("reason"),
                    )
                    # Record verify as its own action with a `turn` key
                    # so the skill-level `vision_calls` counter picks
                    # it up (one VLM round-trip = one count).
                    actions.append({"layer": "vision", "turn": turn,
                                    "action": "verify",
                                    "ok": ok,
                                    "reason": verdict.get("reason")})
                    if ok:
                        consecutive_verify_failures = 0
                    else:
                        consecutive_verify_failures += 1
                        if consecutive_verify_failures >= task.max_verify_failures:
                            detail = (
                                f"verify failed {consecutive_verify_failures}"
                                f" turn(s) in a row; giving up Layer 3 so "
                                f"the cascade can escalate. Last reason: "
                                f"{verdict.get('reason')!r}"
                            )
                            recorder.event(
                                "layer_result", layer=self.name,
                                success=False, turns=turn,
                                reason="verify_streak_exceeded",
                            )
                            return LayerOutcome(
                                layer=self.name, success=False,
                                actions=actions, detail=detail,
                            )
                # Pause to let the UI settle before the next screenshot,
                # AND to keep the free-tier vision provider's per-minute
                # RPM bucket from running dry inside a single task run.
                # Each turn is therefore at least ~2.5s of wall clock
                # plus ~3–6s of model latency, which empirically keeps
                # Gemini happy across 10 consecutive turns.
                await asyncio.sleep(2.5)

            recorder.event("layer_result", layer=self.name,
                           success=captured is not None,
                           turns=turn, captured=captured)
            return LayerOutcome(
                layer=self.name, success=captured is not None,
                actions=actions,
                detail=f"vision loop ran {turn} turn(s); captured={captured!r}",
            )
        except Exception as exc:
            recorder.event("layer_result", layer=self.name,
                           success=False, error=str(exc))
            return LayerOutcome(
                layer=self.name, success=False, error=str(exc),
                actions=actions, detail=f"vision loop crashed: {exc}",
            )

    # ── helpers ────────────────────────────────────────────────────────

    def _build_prompt(self, task: TaskSpec, turn: int) -> str:
        return (
            "You control a desktop via a single action per turn.\n"
            f"Goal: {task.vision_goal}\n"
            f"Turn: {turn} of {task.max_vision_turns}\n"
            "Inspect the screenshot, then return ONE JSON object on a "
            "single line. Allowed shapes:\n"
            '  {"action":"click","x":<int>,"y":<int>,"reason":"..."}\n'
            '  {"action":"type","text":"...","reason":"..."}\n'
            '  {"action":"press","key":"enter","reason":"..."}\n'
            '  {"action":"finish","value":"...","reason":"..."}\n'
            "Coordinates are in pixels relative to the top-left of the "
            "screenshot. Use `finish` when the goal is met."
        )

    def _build_verify_prompt(self, task: TaskSpec, turn: int,
                             *, last_action: dict) -> str:
        """Prompt for the per-turn post-condition check.

        The model sees a FRESH screenshot taken AFTER the last action
        ran, plus a one-line summary of that action, and answers
        whether the post-condition (`task.verify_goal`) now holds.
        Kept deliberately small — verify is a sanity gate, not a
        second planner."""
        op = last_action.get("action", "?")
        # Compact echo of the action so the verifier knows what was
        # supposed to happen without re-deriving it from pixels.
        if op == "click":
            summary = (f"clicked at "
                       f"({last_action.get('x')}, {last_action.get('y')})")
        elif op == "type":
            summary = f"typed {last_action.get('text')!r}"
        elif op == "press":
            summary = f"pressed key {last_action.get('key')!r}"
        else:
            summary = op
        return (
            "You are verifying a desktop automation step.\n"
            f"Original goal: {task.vision_goal}\n"
            f"Post-condition to check: {task.verify_goal}\n"
            f"Turn: {turn} of {task.max_vision_turns}\n"
            f"Last action: {summary}\n"
            "Inspect the screenshot (taken AFTER the action) and "
            "return ONE JSON object on a single line:\n"
            '  {"ok": true,  "reason": "..."}  when the post-condition holds\n'
            '  {"ok": false, "reason": "..."}  when it does not\n'
            "Answer only about the post-condition, not the full goal."
        )
