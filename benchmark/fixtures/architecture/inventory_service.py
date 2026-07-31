"""Inventory service layer — the only sanctioned path for stock changes.

Enforces the business rule (stock may never go negative) before touching the
data layer, so callers are expected to go through reserve_stock rather than
writing to inventory_data_store directly.
"""

from __future__ import annotations

from benchmark.fixtures.architecture.inventory_data_store import read_stock, write_stock


def reserve_stock(sku: str, quantity: int) -> None:
    current = read_stock(sku)
    if current < quantity:
        raise ValueError(f"insufficient stock for {sku}: have {current}, need {quantity}")
    write_stock(sku, current - quantity)
