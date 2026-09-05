"""D2C supplements catalog: SKUs, costs, margins, quality-tier links.

Chosen because all six levers have a natural instance here — consumables give a real
frequency lever, the tiers give quality up and down, and the cross-sell pairs
(protein -> shaker -> creatine) need no explanation to a reader.

Prices in INR. unit_cost is cost-to-deliver, so gross margin is revenue minus that —
not net profit (LTV p.4).
"""

from __future__ import annotations

from decimal import Decimal

from .models import SKU

_SKUS: tuple[SKU, ...] = (
    # --- whey line: the anchor product, with a tier above and below
    SKU(
        code="WHEY_2KG",
        name="Whey Protein 2kg",
        list_price=Decimal("3499"),
        unit_cost=Decimal("1900"),
        is_consumable=True,
        quality_up="ISOLATE_2KG",
        quality_down="WHEY_1KG",
        features=("24g protein/scoop", "60 servings", "third-party tested", "free shaker"),
    ),
    SKU(
        code="ISOLATE_2KG",
        name="Whey Isolate 2kg",
        list_price=Decimal("4999"),
        unit_cost=Decimal("2600"),
        is_consumable=True,
        quality_down="WHEY_2KG",
        features=(
            "27g protein/scoop",
            "60 servings",
            "third-party tested",
            "lactose-free",
            "free shaker",
        ),
    ),
    SKU(
        code="WHEY_1KG",
        name="Whey Protein 1kg",
        list_price=Decimal("1999"),
        unit_cost=Decimal("1050"),
        is_consumable=True,
        quality_up="WHEY_2KG",
        features=("24g protein/scoop", "30 servings"),
    ),
    SKU(
        code="WHEY_5KG",
        name="Whey Protein 5kg (bulk)",
        list_price=Decimal("7999"),
        unit_cost=Decimal("4400"),
        is_consumable=True,
        quality_down="WHEY_2KG",
        features=("24g protein/scoop", "150 servings", "third-party tested"),
    ),
    # --- the anchor upsell: a distinct named offer at 5-10x (MM p.84-88)
    SKU(
        code="TRANSFORM_12M",
        name="12-Month Transformation Stack",
        list_price=Decimal("24999"),
        unit_cost=Decimal("11000"),
        is_consumable=False,
        features=(
            "12 months of protein",
            "creatine + multivitamin",
            "quarterly coach check-in",
            "training programme",
        ),
    ),
    # --- continuity: a recurring membership. MM p.146 — never sold standalone, which
    # money_guard enforces rather than the generator merely avoiding it.
    SKU(
        code="CLUB_MONTHLY",
        name="Athlete Club (monthly)",
        list_price=Decimal("499"),
        unit_cost=Decimal("150"),
        is_consumable=False,
        features=("free delivery", "10% member pricing", "coach Q&A"),
    ),
    # --- cross-sell set
    SKU(
        code="CREATINE",
        name="Creatine Monohydrate 250g",
        list_price=Decimal("899"),
        unit_cost=Decimal("380"),
        is_consumable=True,
        features=("micronised", "50 servings"),
    ),
    SKU(
        code="SHAKER",
        name="Steel Shaker 700ml",
        list_price=Decimal("599"),
        unit_cost=Decimal("240"),
        features=("leak-proof", "steel"),
    ),
    SKU(
        code="MULTIVITAMIN",
        name="Daily Multivitamin 60ct",
        list_price=Decimal("749"),
        unit_cost=Decimal("310"),
        is_consumable=True,
        features=("60 servings", "24 micronutrients"),
    ),
    SKU(
        code="PREWORKOUT",
        name="Pre-Workout 300g",
        list_price=Decimal("1299"),
        unit_cost=Decimal("560"),
        is_consumable=True,
        features=("30 servings", "200mg caffeine"),
    ),
    SKU(
        code="OMEGA3",
        name="Omega-3 90ct",
        list_price=Decimal("999"),
        unit_cost=Decimal("430"),
        is_consumable=True,
        features=("90 servings", "1000mg EPA/DHA"),
    ),
)

BY_CODE: dict[str, SKU] = {s.code: s for s in _SKUS}

# The named 5-10x offer. Kept explicit so money_guard can identify anchor proposals
# without inferring intent from price alone.
ANCHOR_SKU_CODE = "TRANSFORM_12M"


def get(code: str) -> SKU:
    """Look up a SKU. Money Guard re-derives prices and costs through this, never
    trusting figures carried on a proposal."""
    try:
        return BY_CODE[code]
    except KeyError:
        raise KeyError(f"unknown SKU {code!r}") from None


def all_skus() -> tuple[SKU, ...]:
    return _SKUS


# Synthetic order history — real co-occurrence for basket.py to compute support,
# confidence and lift over. Each tuple is one past order's SKU set.
ORDER_HISTORY: tuple[tuple[str, ...], ...] = (
    ("WHEY_2KG", "SHAKER"),
    ("WHEY_2KG", "CREATINE", "SHAKER"),
    ("WHEY_2KG", "CREATINE"),
    ("WHEY_2KG", "SHAKER", "MULTIVITAMIN"),
    ("WHEY_2KG", "CREATINE", "PREWORKOUT"),
    ("WHEY_1KG", "SHAKER"),
    ("WHEY_2KG", "CREATINE", "SHAKER"),
    ("ISOLATE_2KG", "CREATINE", "SHAKER"),
    ("WHEY_2KG", "MULTIVITAMIN"),
    ("WHEY_2KG", "CREATINE"),
    ("CREATINE", "PREWORKOUT"),
    ("WHEY_2KG", "SHAKER", "CREATINE", "OMEGA3"),
    ("MULTIVITAMIN", "OMEGA3"),
    ("WHEY_1KG", "CREATINE"),
    ("ISOLATE_2KG", "SHAKER"),
    ("WHEY_2KG", "PREWORKOUT"),
    ("WHEY_2KG", "SHAKER"),
    ("WHEY_5KG", "CREATINE", "SHAKER"),
    ("MULTIVITAMIN", "OMEGA3", "WHEY_2KG"),
    ("WHEY_2KG", "CREATINE", "SHAKER"),
)
