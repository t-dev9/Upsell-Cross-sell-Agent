"""Every threshold and invariant switch, each carrying its source citation.

The citation lives in the Pydantic field description so ARCHITECTURE.md's MONEY_MODEL
table can be generated from this module rather than hand-maintained and drifting.

A number without a config key and a cite is not an invariant. Never inline a bound in
money_guard.py.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# Citation strings. Kept here so an invariant, its bound and its source cannot drift apart.
MM = "MM"  # $100M Money Models
LTV = "LTV"  # $100M Lifetime Value


class Config(BaseModel):
    """Frozen runtime configuration. Every Tier 1 invariant reads its bound from here."""

    model_config = {"frozen": True}

    # ---------------------------------------------------------------- Tier 1
    # These six are enforced in money_guard.py and each has a test in
    # tests/test_money_guard.py named test_<invariant_name>.

    anchor_price_multiple_min: float = Field(
        default=5.0,
        description=f"{MM} p.84-88 — an Anchor Upsell is a distinct named offer at 5-10x the "
        "anchor baseline. Reject any anchor priced below this multiple. Separate rule "
        "from sequencing.",
    )
    margin_floor_pct: float = Field(
        default=0.25,
        description="ours — reject if post-offer gross margin % on the SKU falls below this floor.",
    )
    discount_ceiling_pct: float = Field(
        default=0.15,
        description="ours — reject if the effective discount % (list price vs. offer price) "
        "exceeds this ceiling.",
    )
    kill_switch: bool = Field(
        default=False,
        description="ours — when True, Money Guard rejects every action regardless of any "
        "other rule passing. Evaluated first and short-circuits everything.",
    )

    # idempotency (Tier 1, promoted once Tier 1 was complete and tested) and
    # never_discount_identical_sku and never_downsell_qualified_buyer take no numeric
    # bound — they are structural checks, always on, and cannot be configured off.
    # MM p.97-98 and LTV p.19 respectively.

    # --- promoted from Tier 2 once Tier 1 was complete and tested (CLAUDE.md section 3)
    daily_budget_inr: Decimal = Field(
        default=Decimal("5000"),
        description="ours — cap on cumulative discount/credit given away in one day, "
        "across all executed offers.",
    )
    rollover_credit_max_pct: float = Field(
        default=0.25,
        description=f"{MM} p.92 — a rollover credit may not exceed this share of the "
        "anchor price.",
    )
    rollover_next_offer_multiple_min: float = Field(
        default=4.0,
        description=f"{MM} p.92 — the offer a credit unlocks must be at least this "
        "multiple of the credit, or the credit is just a discount wearing a hat.",
    )

    fatigue_cap_per_window: int = Field(
        default=3,
        description="ours — max offers shown to one customer within the trailing window.",
    )
    fatigue_window_days: int = Field(default=7, description="ours — the trailing window.")
    auto_approve_threshold_inr: Decimal = Field(
        default=Decimal("20000"),
        description="ours — at or above this offer price, write PENDING_APPROVAL and "
        "never auto-execute. Replaces the cut FastAPI approval queue with a ledger state.",
    )
    cancellation_rate_max: float = Field(
        default=0.10,
        description=f"{MM} p.59 — stop an offer type once its cancellation rate exceeds this.",
    )
    refund_rate_max: float = Field(
        default=0.05,
        description=f"{MM} p.35 — stop an offer type once its refund rate exceeds this.",
    )
    cancellation_window_days: int = Field(
        default=30, description=f"{MM} p.144 — rolling window for the cancellation monitor."
    )

    # ------------------------------------------------ India regulatory posture
    # Trial-with-penalty and negative-option billing are regulated differently in
    # India than in the books' US context, so both ship disabled.

    enable_trial_with_penalty: bool = Field(
        default=False,
        description=f"{MM} p.130 — disabled by default: regulated differently in India.",
    )
    enable_negative_option_billing: bool = Field(
        default=False,
        description="ours — disabled by default: regulated differently in India.",
    )

    # ------------------------------------------------------ Stage [3] selector
    llm_provider: Literal["groq", "fixture", "anthropic"] = Field(
        default="fixture",
        description="Which LLMProvider backs stage [3]. 'fixture' is the no-key default so "
        "the quickstart runs with no credentials.",
    )
    llm_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Free-tier open-weights model. The deterministic layer does the "
        "constraining, not the model — that is the point being demonstrated.",
    )
    llm_timeout_s: float = Field(default=20.0, description="Per-call timeout before fallback.")

    # --------------------------------------------------------------- Ledger
    ledger_path: str = Field(default="ledger.db", description="SQLite append-only audit log.")


# --------------------------------------------------------------------- Tier 2
# NOT ENFORCED. These keys exist so the priority order is legible, but no check in
# money_guard.py reads them and no MONEY_MODEL row may cite them.
#
# CLAUDE.md section 3: "a threshold sitting unused in config.py is not enforcement and
# must not be presented as one." They stay inert until their check and test ship.

TIER_2_UNUSED: dict[str, str] = {}
"""Empty: every rule the plan named is now enforced with a check and a test."""


def load_config(**overrides: object) -> Config:
    """Build config, letting the environment set the selector provider.

    Presence of GROQ_API_KEY flips the default provider to groq; absence leaves the
    fixture in place so `uplift demo` runs for someone who just cloned the repo.
    """
    if "llm_provider" not in overrides:
        overrides["llm_provider"] = "groq" if os.environ.get("GROQ_API_KEY") else "fixture"
    return Config(**overrides)  # type: ignore[arg-type]
