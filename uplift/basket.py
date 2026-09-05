"""[1] Market basket — support, confidence, lift over the order history.

Feeds lever 8 (cross-sell) with ranked candidate pairs. Deterministic: no model call
here, and the ranking is reproducible from catalog.ORDER_HISTORY alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import ORDER_HISTORY


@dataclass(frozen=True, slots=True)
class Association:
    """antecedent -> consequent, with the three standard measures."""

    antecedent: str
    consequent: str
    support: float  # P(A and B)
    confidence: float  # P(B|A)
    lift: float  # confidence / P(B); >1 means genuinely complementary

    def __str__(self) -> str:
        return (
            f"{self.antecedent}->{self.consequent} "
            f"supp={self.support:.2f} conf={self.confidence:.2f} lift={self.lift:.2f}"
        )


def _counts(
    history: tuple[tuple[str, ...], ...],
) -> tuple[dict[str, int], dict[tuple[str, str], int], int]:
    singles: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    for order in history:
        unique = set(order)
        for sku in unique:
            singles[sku] = singles.get(sku, 0) + 1
        for a in unique:
            for b in unique:
                if a != b:
                    pairs[(a, b)] = pairs.get((a, b), 0) + 1
    return singles, pairs, len(history)


def associations_for(
    sku_code: str,
    *,
    history: tuple[tuple[str, ...], ...] = ORDER_HISTORY,
    min_support: float = 0.05,
    min_lift: float = 1.0,
) -> list[Association]:
    """Complements of `sku_code`, best first.

    min_lift=1.0 keeps only pairs that co-occur more than chance would predict — a pair
    below that is popularity, not complementarity, and cross-selling it is noise.
    """
    singles, pairs, n = _counts(history)
    if n == 0 or sku_code not in singles:
        return []

    out: list[Association] = []
    antecedent_count = singles[sku_code]
    for (a, b), count in pairs.items():
        if a != sku_code:
            continue
        support = count / n
        confidence = count / antecedent_count
        consequent_support = singles[b] / n
        lift = confidence / consequent_support if consequent_support else 0.0
        if support >= min_support and lift >= min_lift:
            out.append(Association(a, b, support, confidence, lift))

    out.sort(key=lambda x: (x.lift, x.confidence), reverse=True)
    return out
