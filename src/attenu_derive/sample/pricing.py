"""
One price table for every sampling harness (USD per 1M tokens: input, output, cache read, cache write).
Public list prices as known on 2026-08-18; unknown models are priced CONSERVATIVELY and the manifest says so
(`price_basis`). Estimates are cache-aware when the harness can see cached tokens; `list` = every input token
at the input price (an upper bound — always labelled as such, never reported as the price).
"""
from __future__ import annotations

_PRICES = {
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25), "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75), "claude-sonnet-4": (3.0, 15.0, 0.30, 3.75),
    "claude-opus": (15.0, 75.0, 1.50, 18.75),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.025, 0.10), "gemini-2.5-flash": (0.30, 2.50, 0.075, 0.30), "gemini-2.0-flash": (0.10, 0.40, 0.025, 0.10),
    "gemini-2.5-pro": (1.25, 10.0, 0.31, 1.25),
}
_CONSERVATIVE = (3.0, 15.0, 0.30, 3.75)          # Sonnet-class / Gemini-Pro-class


def _key(model: str) -> str:
    return model.split("/", 1)[-1]                 # "anthropic/claude-haiku-4-5-..." -> "claude-haiku-4-5-..."


def price_basis(model: str) -> str:
    m = _key(model)
    return "list" if any(m.startswith(k) for k in _PRICES) else "conservative (unknown list price; Sonnet/Pro-class rate)"


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_creation: int = 0) -> float:
    m = _key(model)
    pin, pout, pread, pwrite = next((v for k, v in sorted(_PRICES.items(), key=lambda kv: -len(kv[0])) if m.startswith(k)), _CONSERVATIVE)
    fresh = max(0, input_tokens - cache_read - cache_creation)
    return fresh / 1e6 * pin + cache_read / 1e6 * pread + cache_creation / 1e6 * pwrite + output_tokens / 1e6 * pout
