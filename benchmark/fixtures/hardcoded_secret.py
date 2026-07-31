"""Textbook hardcoded credential — should be an obvious, non-ownership finding."""

from __future__ import annotations

import requests

API_KEY = "BENCHMARK_FAKE_SECRET_do_not_use_in_production_xyz789"


def call_billing_api(customer_id: str) -> dict[str, object]:
    # BUG: production secret is hardcoded in source instead of loaded from
    # environment/secret manager, so it ships with every checkout of this repo.
    response = requests.get(
        f"https://api.billing.example.com/customers/{customer_id}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    return response.json()
