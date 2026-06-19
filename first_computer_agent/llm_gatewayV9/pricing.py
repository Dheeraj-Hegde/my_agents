"""USD pricing per 1M tokens, keyed by provider name.

V9: added to back the /v1/cost/by_agent endpoint's `dollars` field. Prices
are list rates for the *configured worker model* of each provider as of
mid-2026 — they're rough; the headline number callers should trust is
tokens. Dollars are derived for human readability.

The course's default providers (gemini-flash-lite, github gpt-4.1-mini,
nvidia free-tier, openrouter free-tier, ollama) are all $0 or free-quota
during normal use; the table exists so production deployments that swap in
paid models still get sensible reports.

Pricing source: provider docs as of 2026-05. Updated alongside
`router.LIMITS` when those tiers shift.
"""
from __future__ import annotations


# USD per 1,000,000 tokens (input, output).  None means "ask the user"
# (we treat None as $0 to avoid lying about cost).
#
# 2026-06 update: realistic list rates for `gemini` so the per-session cost
# report reflects the true monetary value of the work even when the
# project is running on AI-Studio's free quota. Free-tier callers are
# still not charged a cent; the figure simply tells you what the same run
# would cost on the paid plan.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # Gemini 2.5 Flash list rates (Google AI list price, 2026-Q2).
    "gemini":     (0.10, 0.40),
    # NVIDIA NIM free tier (build.nvidia.com), text models only here.
    "nvidia":     (0.00, 0.00),
    # Groq's openai/gpt-oss-120b: listed at $0.15/$0.75 per Mtok as of 2026-04.
    "groq":       (0.15, 0.75),
    # Cerebras zai-glm-4.7 list rate (rough; account-dependent).
    "cerebras":   (0.50, 0.50),
    # OpenRouter ":free" tag — $0 by definition.
    "openrouter": (0.00, 0.00),
    # GitHub Models GPT-4.1-mini list rate (outside the free quota).
    "github":     (0.40, 1.60),
    # Local — never charged.
    "ollama":     (0.00, 0.00),
}


def estimate_usd(provider: str, in_tokens: int, out_tokens: int) -> float:
    p_in, p_out = PRICING_USD_PER_MTOK.get(provider, (0.0, 0.0))
    return round((in_tokens / 1e6) * p_in + (out_tokens / 1e6) * p_out, 6)
