"""Token prices used for cost figures. Source: MGT 409 lecture 11 slide "Cached Token Cost" (course price list for the
Portkey models). USD per 1M tokens. Override with AII_PRICE_INPUT / AII_PRICE_CACHED / AII_PRICE_OUTPUT if prices change."""
from __future__ import annotations

import os

PRICES = {  # model: (input, cached input, output) USD per 1M tokens
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-astra": (10.00, 1.00, 50.00),
}
CACHE_WRITE = {  # model: cache-write price, USD per 1M tokens (first time a prefix is written to the cache)
    "gpt-6-luna": 0.125,
    "gpt-6.1-sol": 2.50,
    "gpt-6-astra": 12.50,
}
SOURCE = ("MGT 409 lecture 11, 'Cached Token Cost' slide: gpt-6-luna $0.10 input, $0.01 cached, $0.125 cache write, "
          "$0.50 output per 1M tokens")


def price(model: str = "gpt-6-luna") -> tuple[float, float, float]:
    base = PRICES.get(model, PRICES["gpt-6-luna"])
    env = [os.environ.get(k) for k in ("AII_PRICE_INPUT", "AII_PRICE_CACHED", "AII_PRICE_OUTPUT")]
    return tuple(float(e) if e else b for e, b in zip(env, base))


def cache_write_price(model: str = "gpt-6-luna") -> float:
    env = os.environ.get("AII_PRICE_CACHE_WRITE")
    return float(env) if env else CACHE_WRITE.get(model, price(model)[0] * 1.25)


def cost_usd(prompt: int, cached: int, completion: int, model: str = "gpt-6-luna", cache_write: int = 0) -> float:
    """prompt includes cached and cache-written tokens (OpenAI convention). Uncached input = prompt - cached - cache_write,
    billed at the input price; cache_write is billed at the cache-write price; cached at the cached price."""
    pi, pc, po = price(model)
    plain = max(prompt - cached - cache_write, 0)
    return (plain * pi + cached * pc + cache_write * cache_write_price(model) + completion * po) / 1e6
