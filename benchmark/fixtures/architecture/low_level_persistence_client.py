"""Low-level persistence client.

BUG (dependency-direction violation): a persistence client is meant to sit at
the bottom of the dependency graph — nothing here should need to know about
higher-level orchestration. Instead it imports and calls back into the
high-level order workflow after every write, so the "low-level" module now
depends on business logic that is supposed to depend on it.
"""

from __future__ import annotations

from benchmark.fixtures.architecture.high_level_order_workflow import finalize_order

_STORE: dict[str, dict[str, object]] = {}


def write_record(record_id: str, payload: dict[str, object]) -> None:
    _STORE[record_id] = payload
    finalize_order(record_id)


def read_record(record_id: str) -> dict[str, object] | None:
    return _STORE.get(record_id)
