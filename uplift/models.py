"""Pydantic types crossing every stage boundary.

One note carries the whole architecture: Proposal.source records whether a proposal came
from the model or the deterministic fallback. It is reported and logged. It is NEVER read
by money_guard.py — no branch, no parameter, no early return. That is the single-door
claim expressed in code, and tests/test_money_guard.py asserts it.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Lever(StrEnum):
    """Crazy Eight levers 3-8. Levers 1-2 are merchant-level, not per-transaction."""

    UPSELL_FREQUENCY = "upsell_frequency"  # 3
    UPSELL_QUANTITY = "upsell_quantity"  # 4
    UPSELL_QUALITY = "upsell_quality"  # 5
    DOWNSELL_QUANTITY = "downsell_quantity"  # 6
    DOWNSELL_QUALITY = "downsell_quality"  # 7
    CROSS_SELL = "cross_sell"  # 8
    ANCHOR_UPSELL = "anchor_upsell"  # the distinct 5-10x named offer, MM p.84-88
    CONTINUITY = "continuity"  # recurring membership, MM p.146 — never standalone
    ROLLOVER = "rollover"  # prior spend credited toward a larger offer, MM p.92


DOWNSELL_LEVERS = frozenset({Lever.DOWNSELL_QUANTITY, Lever.DOWNSELL_QUALITY})


class SKU(BaseModel):
    model_config = {"frozen": True}

    code: str
    name: str
    list_price: Decimal
    unit_cost: Decimal
    is_consumable: bool = False
    quality_up: str | None = None
    quality_down: str | None = None
    features: tuple[str, ...] = ()

    @property
    def gross_margin_pct(self) -> Decimal:
        if self.list_price <= 0:
            return Decimal(0)
        return (self.list_price - self.unit_cost) / self.list_price


class Customer(BaseModel):
    model_config = {"frozen": True}

    id: str
    past_order_skus: tuple[str, ...] = ()
    total_spend: Decimal = Decimal(0)
    accepted_upsell_before: bool = False


class OrderEvent(BaseModel):
    """The `order.paid` event that starts the pipeline."""

    model_config = {"frozen": True}

    event_id: str
    order_id: str
    customer: Customer
    sku_code: str
    quantity: int = 1
    amount_paid: Decimal


class Candidate(BaseModel):
    """One enumerated offer from stage [1]. Deterministically generated, never invented."""

    model_config = {"frozen": True}

    lever: Lever
    sku_code: str
    offer_price: Decimal
    quantity: int = 1
    rationale: str = ""
    credit_amount: Decimal = Decimal(0)
    """Prior spend credited toward this offer (MM p.92). Zero for ordinary offers.

    Money Guard bounds it independently: a credit is a way of giving money away, so it
    is capped relative to the anchor and the offer it unlocks.
    """

    @property
    def key(self) -> str:
        return f"{self.lever}:{self.sku_code}:{self.quantity}:{self.offer_price}"


class Proposal(BaseModel):
    """Stage [3] output. Identical shape whether the model or the fallback produced it."""

    model_config = {"frozen": True}

    candidate: Candidate
    pitch: str = ""
    source: Literal["ai", "fallback"]
    repaired: bool = Field(
        default=False,
        description="True if the model's first JSON was invalid and the single repair "
        "attempt succeeded. Recorded for the failure log — it has no bearing on safety.",
    )


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    """Above the auto-approve threshold: a human decides.

    This is the ledger-state replacement for the cut FastAPI approval queue. It must be
    exactly as unable to reach stage [5] as BLOCKED is — `approved` stays False, so the
    adapter refuses it on the same code path.
    """


class GuardResult(BaseModel):
    """Stage [4] output — and the only key that opens stage [5].

    razorpay_adapter.execute() takes a GuardResult, so execution is unreachable without
    one. A BLOCKED result carries the invariant that fired, its citation, and what the
    system did instead, because CLAUDE.md section 7 requires all three in CLI output.
    """

    model_config = {"frozen": True}

    verdict: Verdict
    proposal: Proposal | None = None
    invariant: str | None = None
    citation: str | None = None
    counterfactual: str | None = None

    @property
    def approved(self) -> bool:
        """Only APPROVED opens stage [5]. PENDING_APPROVAL and BLOCKED both stay shut.

        Written as an identity check against APPROVED rather than `is not BLOCKED`, so
        adding a future verdict cannot accidentally make it executable.
        """
        return self.verdict is Verdict.APPROVED


class LedgerEntry(BaseModel):
    id: int
    prev_id: int | None
    event_id: str
    order_id: str
    action: str
    lever: str | None
    sku_code: str | None
    amount: Decimal | None
    verdict: str
    invariant: str | None
    citation: str | None
    source: str | None
    reference: str | None = None  # gateway order id, so a row reconciles to Razorpay
    created_at: str
