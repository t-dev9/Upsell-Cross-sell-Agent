"""SIMULATED acceptance model. Read this docstring before quoting any number from it.

Nothing here measures customer behaviour. This module encodes assumptions we chose about
how buyers respond to price and to complementarity, and then reports what those
assumptions imply. Any "winning policy" it produces is a property of the inputs below,
not a finding about real customers, this merchant, or any merchant.

That is exactly why the submission claims no uplift number. What the simulator is for is
narrower and honest: showing that the answer to "which policy is best?" FLIPS depending
on assumptions you cannot verify without real data — which is the argument for building
containment guarantees rather than optimising a number.

Every figure this module produces must be labelled simulated wherever it is displayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .. import catalog
from ..models import Candidate, Lever


@dataclass(frozen=True, slots=True)
class AssumptionSet:
    """One set of chosen parameters. Named so results can never be quoted context-free."""

    name: str
    label: str
    price_elasticity: float  # more negative = take-rate falls faster as the ask grows
    complementarity_weight: float  # how much cross-sell affinity lifts acceptance

    def describe(self) -> str:
        return (
            f"{self.name} ({self.label}): elasticity {self.price_elasticity}, "
            f"complementarity {self.complementarity_weight}"
        )


# The three sets, fixed up front so results cannot be tuned after the fact.
SET_A = AssumptionSet("A", "price-sensitive", -1.5, 0.1)
SET_B = AssumptionSet("B", "baseline", -0.8, 0.4)
SET_C = AssumptionSet("C", "relationship-driven", -0.3, 0.8)
ASSUMPTION_SETS = (SET_A, SET_B, SET_C)

# Cross-sell affinity, standing in for "this solves their next problem". Derived from the
# same order history basket.py uses, so the two are at least consistent with each other.
_AFFINITY: dict[str, float] = {
    "SHAKER": 0.9,
    "CREATINE": 0.85,
    "MULTIVITAMIN": 0.5,
    "PREWORKOUT": 0.45,
    "OMEGA3": 0.4,
}


def accept_probability(
    candidate: Candidate, anchor_price: Decimal, assumptions: AssumptionSet
) -> float:
    """SIMULATED probability a buyer accepts this offer.

    Two forces, both assumed: the ask relative to what they just paid (damped by
    elasticity), and complementarity (weighted by the set). No empirical basis.
    """
    if anchor_price <= 0:
        return 0.0

    ratio = float(candidate.offer_price) / float(anchor_price)
    # Elasticity applied to the size of the ask: a bigger ask converts worse, and how
    # much worse is the whole point of varying this parameter.
    base = max(0.02, min(0.9, 0.55 * (ratio ** assumptions.price_elasticity)))

    if candidate.lever is Lever.CROSS_SELL:
        affinity = _AFFINITY.get(candidate.sku_code, 0.3)
        base *= 1.0 + assumptions.complementarity_weight * affinity

    return max(0.0, min(0.95, base))


def gross_profit(candidate: Candidate) -> Decimal:
    """Real arithmetic on real catalog costs — this part is not simulated."""
    sku = catalog.get(candidate.sku_code)
    return candidate.offer_price - (sku.unit_cost * candidate.quantity)


def expected_added_ltgp(
    candidate: Candidate, anchor_price: Decimal, assumptions: AssumptionSet
) -> float:
    """SIMULATED added LTGP = conversion x upsell gross profit (LTV p.14).

    The gross-profit half is real arithmetic; the conversion half is assumed. Their
    product is therefore simulated, and is labelled as such everywhere it appears.
    """
    return accept_probability(candidate, anchor_price, assumptions) * float(gross_profit(candidate))
