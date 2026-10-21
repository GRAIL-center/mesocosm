"""Currency conversion against a small static table. Rates are refreshed by the nightly job."""
from __future__ import annotations

RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "KRW": 1380.0}


def convert(amount: float, src: str, dst: str) -> float:
    if src not in RATES or dst not in RATES:
        raise KeyError(f"unknown currency: {src!r} or {dst!r}")
    return amount / RATES[src] * RATES[dst]
