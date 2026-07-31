"""Checkout handler.

BUG (layering violation): this handler is supposed to go through
inventory_service.reserve_stock for any stock change. Instead it imports the
data-store module directly and mutates the raw dict itself, bypassing the
service layer's business rule entirely (stock can go negative here, and any
future rule added to reserve_stock silently does not apply to checkout).
"""

from __future__ import annotations

from benchmark.fixtures.architecture.inventory_data_store import STOCK


def checkout(sku: str, quantity: int) -> None:
    STOCK[sku] -= quantity
