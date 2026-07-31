"""Low-level in-memory inventory store."""

from __future__ import annotations

STOCK: dict[str, int] = {"widget": 40, "gadget": 5}


def read_stock(sku: str) -> int:
    return STOCK.get(sku, 0)


def write_stock(sku: str, quantity: int) -> None:
    STOCK[sku] = quantity
