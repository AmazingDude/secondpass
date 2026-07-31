"""Well-scoped price formatting helper — no architecture issues expected."""

from __future__ import annotations


def format_price(cents: int) -> str:
    dollars, remainder = divmod(cents, 100)
    return f"${dollars}.{remainder:02d}"


def apply_discount(cents: int, percent_off: float) -> int:
    if not 0 <= percent_off <= 100:
        raise ValueError("percent_off must be between 0 and 100")
    discounted = cents * (1 - percent_off / 100)
    return round(discounted)
