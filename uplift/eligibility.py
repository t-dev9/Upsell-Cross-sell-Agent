"""[2] Eligibility filters + the downsell qualification gate. Deterministic.

Produces THE eligible set. Both the model and the deterministic fallback must draw from
this same set — the fallback is never handed an ungated candidate, and the model never
sees one. That is why this stage runs before any model call, not after.

The qualification gate mirrors the LTV playbook: downsells are built, then offered ONLY
to unqualified prospects (LTV p.19, checklists "Get them to buy fewer/a worse version").
Offering a downsell to someone who would have paid full price is margin given away for
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import catalog
from .models import DOWNSELL_LEVERS, Candidate, Customer, OrderEvent

# A buyer who just paid at or above this share of list price, or who has accepted an
# upsell before, is qualified — they have demonstrated willingness to pay.
_QUALIFIED_PAID_RATIO = Decimal("0.95")


@dataclass(frozen=True, slots=True)
class Rejected:
    candidate: Candidate
    reason: str


@dataclass(frozen=True, slots=True)
class EligibleSet:
    """The output of stage [2] — and the only pool stage [3] may select from."""

    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejected, ...]
    buyer_qualified: bool

    def contains(self, candidate: Candidate) -> bool:
        return any(c.key == candidate.key for c in self.candidates)


def is_qualified_buyer(customer: Customer, event: OrderEvent) -> bool:
    """Did this buyer just demonstrate willingness to pay?

    money_guard re-derives this independently rather than trusting the flag computed
    here — a qualification that only exists upstream is not a guard.
    """
    if customer.accepted_upsell_before:
        return True
    sku = catalog.get(event.sku_code)
    expected = sku.list_price * event.quantity
    if expected <= 0:
        return False
    return (event.amount_paid / expected) >= _QUALIFIED_PAID_RATIO


def filter_candidates(event: OrderEvent, candidates: list[Candidate]) -> EligibleSet:
    qualified = is_qualified_buyer(event.customer, event)
    owned = set(event.customer.past_order_skus)

    keep: list[Candidate] = []
    dropped: list[Rejected] = []

    for c in candidates:
        # The downsell qualification gate — the reason this stage exists.
        if c.lever in DOWNSELL_LEVERS and qualified:
            dropped.append(
                Rejected(c, "qualified buyer — downsells are for unqualified prospects (LTV p.19)")
            )
            continue

        if c.lever.value.startswith("cross_sell") and c.sku_code in owned:
            dropped.append(Rejected(c, "customer already owns this SKU"))
            continue

        try:
            sku = catalog.get(c.sku_code)
        except KeyError:
            dropped.append(Rejected(c, "SKU not in catalog"))
            continue

        if c.offer_price <= 0:
            dropped.append(Rejected(c, "non-positive offer price"))
            continue

        # Cheap sanity screen. It is NOT the margin guard — money_guard.py re-derives
        # margin independently and is the only thing standing between here and money.
        if c.offer_price < sku.unit_cost:
            dropped.append(Rejected(c, "offer below unit cost"))
            continue

        keep.append(c)

    return EligibleSet(
        candidates=tuple(keep),
        rejected=tuple(dropped),
        buyer_qualified=qualified,
    )
