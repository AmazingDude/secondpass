"""High-level order workflow: coordinates order-fulfillment steps."""

from __future__ import annotations


def finalize_order(order_id: str) -> None:
    print(f"Order {order_id} finalized: notifying downstream systems")
