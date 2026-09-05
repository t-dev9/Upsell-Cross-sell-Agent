"""Synthetic order events for the eval sweep.

Deterministic given a seed, so a run is reproducible and a reviewer can regenerate the
exact inputs behind any figure.
"""

from __future__ import annotations

import random
from decimal import Decimal

from .. import catalog
from ..models import Customer, OrderEvent

# Anchor SKUs a customer might have just bought. The 12-month stack is excluded — it is
# the anchor upsell target, not a starting purchase.
_ENTRY_SKUS = ("WHEY_2KG", "WHEY_1KG", "ISOLATE_2KG", "CREATINE", "MULTIVITAMIN")


def generate_events(n: int = 100, seed: int = 20260905) -> list[OrderEvent]:
    rng = random.Random(seed)
    events: list[OrderEvent] = []

    for i in range(n):
        sku_code = rng.choice(_ENTRY_SKUS)
        sku = catalog.get(sku_code)
        qualified = rng.random() < 0.45
        repeat = rng.random() < 0.35

        past: tuple[str, ...] = ()
        if repeat:
            past = tuple(
                rng.sample([s for s in _ENTRY_SKUS if s != sku_code], rng.randint(1, 2))
            )

        # A qualified buyer paid at or near list; an unqualified one came in on a
        # discount or a partial basket.
        paid = sku.list_price if qualified else (
            sku.list_price * Decimal(str(round(rng.uniform(0.55, 0.9), 2)))
        ).quantize(Decimal("1"))

        events.append(
            OrderEvent(
                event_id=f"evt_{i:04d}",
                order_id=f"order_{i:04d}",
                customer=Customer(
                    id=f"cust_{i:04d}",
                    past_order_skus=past,
                    total_spend=Decimal(str(rng.randint(0, 40000))),
                    accepted_upsell_before=qualified and rng.random() < 0.6,
                ),
                sku_code=sku_code,
                quantity=1,
                amount_paid=paid,
            )
        )

    return events
