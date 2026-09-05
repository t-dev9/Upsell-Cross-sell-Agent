"""[1] Crazy Eight enumeration — levers 3-8, plus the Anchor Upsell.

Deterministic. No model call in this file. The systematic walk beats inspiration
(LTV p.22), which is why enumeration is code rather than a prompt: the model picks from
an enumerated list instead of inventing an offer.

Levers 1-2 (raise price, cut delivery cost) are merchant-level, not per-transaction,
so they are absent by design.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from . import catalog
from .basket import associations_for
from .models import Candidate, Lever, OrderEvent, SKU

# Discounts on *different* configurations are legitimate: buying more, or committing
# for longer, earns a better unit rate. Discounting the identical SKU never is
# (MM p.97-98) — that rule is enforced independently in money_guard.py.
_BULK_DISCOUNT = Decimal("0.10")
_SUBSCRIPTION_DISCOUNT = Decimal("0.08")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def upsell_frequency(sku: SKU) -> list[Candidate]:
    """[3] Buy the same thing more often — subscribe-and-save on a consumable."""
    if not sku.is_consumable:
        return []
    price = _money(sku.list_price * (1 - _SUBSCRIPTION_DISCOUNT))
    return [
        Candidate(
            lever=Lever.UPSELL_FREQUENCY,
            sku_code=sku.code,
            offer_price=price,
            rationale=(
                f"subscribe-and-save: monthly {sku.name} at "
                f"{int(_SUBSCRIPTION_DISCOUNT * 100)}% off list, cancel anytime"
            ),
        )
    ]


def upsell_quantity(sku: SKU) -> list[Candidate]:
    """[4] Buy more now — bulk prepay at a better unit rate."""
    out: list[Candidate] = []
    bulk_price = _money(sku.list_price * 2 * (1 - _BULK_DISCOUNT))
    out.append(
        Candidate(
            lever=Lever.UPSELL_QUANTITY,
            sku_code=sku.code,
            offer_price=bulk_price,
            quantity=2,
            rationale=f"2x {sku.name} prepaid at {int(_BULK_DISCOUNT * 100)}% off unit price",
        )
    )
    # A larger pack SKU is a bigger version of the same purchase, not a discount.
    if sku.code == "WHEY_2KG":
        bigger = catalog.get("WHEY_5KG")
        out.append(
            Candidate(
                lever=Lever.UPSELL_QUANTITY,
                sku_code=bigger.code,
                offer_price=bigger.list_price,
                rationale=f"step up to {bigger.name} — lower cost per serving",
            )
        )
    return out


def upsell_quality(sku: SKU) -> list[Candidate]:
    """[5] Buy a better version — the tier above."""
    if not sku.quality_up:
        return []
    better = catalog.get(sku.quality_up)
    return [
        Candidate(
            lever=Lever.UPSELL_QUALITY,
            sku_code=better.code,
            offer_price=better.list_price,
            rationale=f"upgrade to {better.name}",
        )
    ]


def anchor_upsell(sku: SKU) -> list[Candidate]:
    """The distinct named offer at 5-10x the anchor baseline (MM p.84-88).

    Priced at list. money_guard independently rejects any anchor proposal below
    config.anchor_price_multiple_min x the baseline, whatever price arrives here.
    """
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    if sku.code == anchor.code:
        return []
    return [
        Candidate(
            lever=Lever.ANCHOR_UPSELL,
            sku_code=anchor.code,
            offer_price=anchor.list_price,
            rationale=f"{anchor.name} — the 12-month commitment, not a bigger tub",
        )
    ]


CONTINUITY_SKU_CODE = "CLUB_MONTHLY"


def continuity(sku: SKU) -> list[Candidate]:
    """Recurring membership (MM p.146).

    The generator emits it for anyone; money_guard is what refuses it to a customer with
    no prior anchor or upsell. Filtering here instead would make the rule a generator
    convention rather than an enforced invariant — and a convention cannot be violated
    by a manipulated model, so it would prove nothing.
    """
    club = catalog.get(CONTINUITY_SKU_CODE)
    if sku.code == club.code:
        return []
    return [
        Candidate(
            lever=Lever.CONTINUITY,
            sku_code=club.code,
            offer_price=club.list_price,
            rationale=f"{club.name} — free delivery and member pricing, monthly",
        )
    ]


def rollover(sku: SKU, event_amount: Decimal) -> list[Candidate]:
    """Credit what they just paid toward a larger commitment (MM p.92).

    Credit is capped at 25% of the anchor and the offer must be at least 4x the credit.
    Those bounds are re-derived in money_guard: this generator proposes, it does not
    get to decide what is allowed.
    """
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    if sku.code == anchor.code or event_amount <= 0:
        return []
    credit = _money(event_amount * Decimal("0.25"))
    return [
        Candidate(
            lever=Lever.ROLLOVER,
            sku_code=anchor.code,
            offer_price=anchor.list_price - credit,
            credit_amount=credit,
            rationale=(
                f"roll {_money(credit)} of today's order into {anchor.name}"
            ),
        )
    ]


def downsell_quantity(sku: SKU) -> list[Candidate]:
    """[6] Buy fewer rather than nothing."""
    if not sku.quality_down:
        return []
    smaller = catalog.get(sku.quality_down)
    return [
        Candidate(
            lever=Lever.DOWNSELL_QUANTITY,
            sku_code=smaller.code,
            offer_price=smaller.list_price,
            rationale=f"smaller pack: {smaller.name}",
        )
    ]


def downsell_quality(sku: SKU) -> list[Candidate]:
    """[7] Buy a worse version — the quality levers read backwards.

    Feature-downsell ordering (MM p.115-121): strip the highest-value feature first,
    because customers re-upsell themselves to get it back.
    """
    if not sku.features:
        return []
    kept = feature_downsell_order(sku)[1:]
    if not kept:
        return []
    stripped = sku.features[0]
    price = _money(sku.list_price * Decimal("0.75"))
    return [
        Candidate(
            lever=Lever.DOWNSELL_QUALITY,
            sku_code=sku.code,
            offer_price=price,
            rationale=f"basic tier — without {stripped}; keeps {', '.join(kept)}",
        )
    ]


def feature_downsell_order(sku: SKU) -> list[str]:
    """Features ranked highest-value first — the order they get removed in (MM p.115-121).

    catalog lists features in descending value, so this is the identity ordering; the
    function exists so the rule is named, testable, and not an accident of list order.
    """
    return list(sku.features)


def cross_sell(sku: SKU, *, exclude: frozenset[str] = frozenset()) -> list[Candidate]:
    """[8] The product solving the customer's next problem, ranked by lift."""
    out: list[Candidate] = []
    for assoc in associations_for(sku.code):
        if assoc.consequent in exclude or assoc.consequent == sku.code:
            continue
        partner = catalog.get(assoc.consequent)
        out.append(
            Candidate(
                lever=Lever.CROSS_SELL,
                sku_code=partner.code,
                offer_price=partner.list_price,
                rationale=(
                    f"{partner.name} — bought together in "
                    f"{assoc.confidence:.0%} of {sku.name} orders (lift {assoc.lift:.2f})"
                ),
            )
        )
    return out


def enumerate_all(event: OrderEvent) -> list[Candidate]:
    """Walk every lever. This is the complete action space for one order event."""
    sku = catalog.get(event.sku_code)
    already_owned = frozenset(event.customer.past_order_skus) | {event.sku_code}

    candidates: list[Candidate] = []
    candidates += upsell_frequency(sku)
    candidates += upsell_quantity(sku)
    candidates += upsell_quality(sku)
    candidates += anchor_upsell(sku)
    candidates += continuity(sku)
    candidates += rollover(sku, event.amount_paid)
    candidates += downsell_quantity(sku)
    candidates += downsell_quality(sku)
    candidates += cross_sell(sku, exclude=already_owned)

    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in candidates:
        if c.key not in seen:
            seen.add(c.key)
            unique.append(c)
    return unique
