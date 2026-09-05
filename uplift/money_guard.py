"""[4] MONEY GUARD — the only door to a money action.

Every path that could reach stage [5] converges here first: the original model choice,
the JSON-repaired model choice, and the deterministic fallback choice. There is no
exception handler that skips it and no shortcut branch around it.

Two properties make this a security boundary rather than a formality:

1. It RE-DERIVES every claim. Prices, costs, margins and buyer qualification are looked
   up again from catalog.py and recomputed here. Nothing carried on the proposal is
   trusted, because a proposal is an assertion by an upstream stage — including one that
   may have been manipulated through a prompt-injected product title.

2. It NEVER reads proposal.source. No branch, no parameter, no early return keyed on
   whether the model or the fallback produced the proposal. A "safe" fallback pick gets
   exactly the checks a jailbroken model's pick gets. JSON validity determines only
   whether a proposal was parseable; it is not what makes an action safe.

Every bound comes from config.py with its citation. No magic numbers in this file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Protocol

from . import catalog
from .config import Config
from .eligibility import EligibleSet, is_qualified_buyer
from .models import (
    DOWNSELL_LEVERS,
    GuardResult,
    Lever,
    OrderEvent,
    Proposal,
    Verdict,
)

# invariant name -> source citation, printed verbatim by the CLI on every block.
CITATIONS: dict[str, str] = {
    "kill_switch": "ours",
    "idempotency": "ours",
    "never_discount_identical_sku": "MM p.97",
    "never_downsell_qualified_buyer": "LTV p.19",
    "anchor_price_multiple_min": "MM p.84-88",
    "margin_floor": "ours",
    "discount_ceiling": "ours",
    "not_in_eligible_set": "ours",
    "daily_budget": "ours",
    "rollover_credit": "MM p.92",
    "continuity_never_standalone": "MM p.146",
    "sequence_largest_first": "LTV p.15,17",
    "fatigue_cap": "ours",
    "cancellation_stop_conditions": "MM p.59,144,35",
    "auto_approve": "ours",
}


class ReplayLookup(Protocol):
    """The ledger reads this module needs, as a Protocol.

    A Protocol rather than a Ledger import so money_guard stays independent of storage —
    and so tests can hand it a small stub instead of a database. Every method is
    optional in practice: a caller with no ledger passes None, and the checks that need
    history skip rather than guess. A guard that invents history it cannot read would be
    worse than one that abstains.
    """

    def find_by_event_id(self, event_id: str) -> object | None: ...

    def discount_spend_today(self) -> Decimal: ...

    def has_accepted_anchor_or_upsell(self, customer_id: str) -> bool: ...

    def offers_shown_since(self, customer_id: str, since_iso: str) -> int: ...

    def outcome_rates(self, lever: str, since_iso: str) -> tuple[float, float, int]: ...


class _Block(Exception):
    """Raised by a check to reject. Carries what the CLI must print."""

    def __init__(self, invariant: str, counterfactual: str) -> None:
        self.invariant = invariant
        self.counterfactual = counterfactual
        super().__init__(invariant)


# --------------------------------------------------------------------- checks
# Each takes (proposal, event, eligible, config) and raises _Block to reject.
# None of them reads proposal.source.


def _check_kill_switch(p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None) -> None:
    """A single boolean that rejects everything, whatever else passes. Runs first."""
    if c.kill_switch:
        raise _Block("kill_switch", "kill switch engaged — order completed with no offer")


def _check_idempotency(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """The same event_id must never produce a second money action.

    Promoted from Tier 2 once Tier 1 was complete and tested — it is
    the enforcement behind the repeated-webhook red-team category, so leaving it unbuilt
    would have meant claiming a category the code did not cover.

    Runs before the money rules: a replay is refused on provenance, not re-litigated on
    price. Without a lookup (a caller with no ledger) there is nothing to check, and the
    remaining invariants still apply.
    """
    if r is None:
        return
    if r.find_by_event_id(e.event_id) is not None:
        raise _Block(
            "idempotency",
            f"event {e.event_id} already actioned — returned the recorded ledger entry, "
            "no second money action created",
        )


def _check_in_eligible_set(p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None) -> None:
    """The proposal must come from stage [2]'s set — for AI and fallback alike.

    This is what stops a manipulated model inventing an offer that was never enumerated.
    """
    if not s.contains(p.candidate):
        raise _Block(
            "not_in_eligible_set",
            "proposal was not in the stage-[2] eligible set — order completed with no offer",
        )


def _check_never_discount_identical_sku(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """MM p.97-98 — change how they pay or what they get, never the price for the same thing.

    Same SKU, same quantity, same terms, lower price is a raw discount with nothing given
    in return. The agent cannot emit one.

    Three things are NOT discounts, because each changes what is being sold:
      - a different SKU entirely
      - a larger quantity (buying more, priced per unit)
      - a recurring commitment (lever 3) — subscribe-and-save changes HOW they pay,
        which is the exact move the cited rule tells you to make instead of discounting

    The frequency exemption is not a loophole: discount_ceiling still binds every
    proposal, so a "subscription" at 60% off is blocked by that check instead. No single
    rule can be bypassed by relabelling a lever.
    """
    cand = p.candidate
    if cand.sku_code != e.sku_code:
        return
    if cand.quantity != e.quantity:
        return  # buying more is a different offer, priced per unit
    if cand.lever is Lever.UPSELL_FREQUENCY:
        return  # recurring terms, not the same one-off purchase — still discount-capped
    sku = catalog.get(cand.sku_code)
    baseline = sku.list_price * cand.quantity
    if cand.offer_price < baseline:
        raise _Block(
            "never_discount_identical_sku",
            "order completed with no offer",
        )


def _check_never_downsell_qualified_buyer(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """LTV p.19 — downsells exist for unqualified prospects only.

    Re-derived here from the event rather than read off EligibleSet.buyer_qualified: a
    qualification computed only in stage [2] would be a filter, not a guard.
    """
    if p.candidate.lever not in DOWNSELL_LEVERS:
        return
    if is_qualified_buyer(e.customer, e):
        raise _Block(
            "never_downsell_qualified_buyer",
            "buyer already demonstrated willingness to pay — order completed with no offer",
        )


def _check_anchor_price_multiple_min(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """MM p.84-88 — the Anchor Upsell is a distinct named offer at 5-10x the baseline.

    Separate rule from sequencing. An 'anchor' priced at 2x is not an anchor.
    """
    if p.candidate.lever is not Lever.ANCHOR_UPSELL:
        return
    baseline = catalog.get(e.sku_code).list_price * e.quantity
    if baseline <= 0:
        return
    multiple = p.candidate.offer_price / baseline
    if multiple < Decimal(str(c.anchor_price_multiple_min)):
        raise _Block(
            "anchor_price_multiple_min",
            f"anchor at {multiple:.1f}x is below the {c.anchor_price_multiple_min:.0f}x "
            "floor — order completed with no offer",
        )


def _check_margin_floor(p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None) -> None:
    """Post-offer gross margin on the SKU must clear the configured floor.

    Cost comes from the catalog, not from the proposal — a corrupted catalog value or a
    model-supplied 'cost' cannot talk its way past this.
    """
    cand = p.candidate
    sku = catalog.get(cand.sku_code)
    total_cost = sku.unit_cost * cand.quantity
    if cand.offer_price <= 0:
        raise _Block("margin_floor", "non-positive price — order completed with no offer")
    margin = (cand.offer_price - total_cost) / cand.offer_price
    floor = Decimal(str(c.margin_floor_pct))
    if margin < floor:
        raise _Block(
            "margin_floor",
            f"post-offer margin {margin:.1%} below the {floor:.0%} floor — "
            "order completed with no offer",
        )


def _check_discount_ceiling(p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None) -> None:
    """Effective discount off list must not exceed the ceiling."""
    cand = p.candidate
    sku = catalog.get(cand.sku_code)
    list_total = sku.list_price * cand.quantity
    if list_total <= 0:
        return
    discount = (list_total - cand.offer_price) / list_total
    ceiling = Decimal(str(c.discount_ceiling_pct))
    if discount > ceiling:
        raise _Block(
            "discount_ceiling",
            f"effective discount {discount:.1%} exceeds the {ceiling:.0%} ceiling — "
            "order completed with no offer",
        )


def _check_daily_budget(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """Cap how much margin can be given away in a single day, across all offers.

    Individually-legal offers can still add up to an unacceptable day, which is the
    whole reason this is a separate rule rather than a tighter discount ceiling.

    Counts executed spend only. Without a ledger there is no history to read, so the
    check abstains rather than assuming zero and waving everything through — abstaining
    is visible in the other invariants that still run; a fabricated zero would not be.
    """
    if r is None:
        return
    cand = p.candidate
    sku = catalog.get(cand.sku_code)
    list_total = sku.list_price * cand.quantity
    give_away = max(Decimal(0), list_total - cand.offer_price) + cand.credit_amount
    if give_away <= 0:
        return

    already = r.discount_spend_today()
    cap = Decimal(str(c.daily_budget_inr))
    if already + give_away > cap:
        raise _Block(
            "daily_budget",
            f"today's give-away would reach INR {already + give_away} against a "
            f"INR {cap} cap — order completed with no offer",
        )


def _check_rollover_credit(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """MM p.92 — a credit must be small relative to the anchor, and must unlock
    something substantially larger than itself.

    Both halves matter. A credit that is too large is a discount; a credit that unlocks
    a barely-bigger offer is also a discount. Either way the merchant paid for nothing.
    """
    cand = p.candidate
    credit = cand.credit_amount
    if credit <= 0:
        return

    anchor_price = catalog.get(cand.sku_code).list_price
    max_pct = Decimal(str(c.rollover_credit_max_pct))
    if anchor_price > 0 and credit > anchor_price * max_pct:
        raise _Block(
            "rollover_credit",
            f"credit INR {credit} exceeds {max_pct:.0%} of the INR {anchor_price} "
            "anchor — order completed with no offer",
        )

    min_multiple = Decimal(str(c.rollover_next_offer_multiple_min))
    if cand.offer_price < credit * min_multiple:
        raise _Block(
            "rollover_credit",
            f"offer INR {cand.offer_price} is below {min_multiple:.0f}x the INR {credit} "
            "credit — order completed with no offer",
        )


def _check_continuity_never_standalone(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """MM p.146 — continuity is sold on top of a relationship, never as the front end.

    Qualification is read from the ledger's executed history, plus the customer record
    on the event. The generator in levers.py deliberately offers continuity to everyone
    so that this rule is what actually refuses it: a rule the generator quietly avoids
    breaking has never been tested by anything.
    """
    if p.candidate.lever is not Lever.CONTINUITY:
        return

    if e.customer.accepted_upsell_before:
        return
    if r is not None and r.has_accepted_anchor_or_upsell(e.customer.id):
        return

    raise _Block(
        "continuity_never_standalone",
        "customer has no prior accepted anchor or upsell — order completed with no offer",
    )


# The axes where a "larger variant of the same thing" exists. Cross-sell and downsell
# levers are excluded by design — see the docstring below.
_LARGEST_FIRST_LEVERS = frozenset({Lever.UPSELL_QUANTITY, Lever.UPSELL_QUALITY})


def _check_sequence_largest_first(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """LTV p.15,17 — offer the largest eligible variant first, then downsell.

    Offering the small one first leaves money on the table you can never go back for: a
    customer who accepted the cheaper version will not be re-asked for the dearer one.

    Applies ONLY to the quantity and quality axes, where "larger variant of the same
    thing" is meaningful. It deliberately does not apply to cross-sell: complements are
    ranked by market-basket lift, and forcing the priciest one would override the
    complementarity signal with a price sort — selling the wrong product more expensively.
    Nor to downsells, whose entire purpose is to be smaller.
    """
    if p.candidate.lever not in _LARGEST_FIRST_LEVERS:
        return
    cand = p.candidate
    larger = [
        x for x in s.candidates
        if x.lever is cand.lever and x.offer_price > cand.offer_price
    ]
    if larger:
        biggest = max(larger, key=lambda x: x.offer_price)
        raise _Block(
            "sequence_largest_first",
            f"{biggest.sku_code} at INR {biggest.offer_price} was eligible on the same "
            "lever and should be offered first — order completed with no offer",
        )


def _check_fatigue_cap(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """Stop pestering. Counts offers actually put in front of this customer.

    Blocked proposals never reached them, so they do not count toward fatigue — a guard
    that punished the customer for offers the guard itself refused would be incoherent.
    """
    if r is None:
        return
    since = (
        datetime.now(timezone.utc) - timedelta(days=c.fatigue_window_days)
    ).isoformat(timespec="seconds")
    shown = r.offers_shown_since(e.customer.id, since)
    if shown >= c.fatigue_cap_per_window:
        raise _Block(
            "fatigue_cap",
            f"{shown} offers already shown in {c.fatigue_window_days}d against a cap of "
            f"{c.fatigue_cap_per_window} — order completed with no offer",
        )


def _check_cancellation_stop_conditions(
    p: Proposal, e: OrderEvent, s: EligibleSet, c: Config, r: ReplayLookup | None
) -> None:
    """MM p.59/144/35 — stop selling an offer type that is going bad.

    Rates come from real CANCELLED / REFUNDED ledger rows, so this is a monitor over
    outcomes rather than a threshold sitting unused in config.py. With no executed
    history for the lever the sample is zero and the check abstains: a rate computed
    from no data is not evidence.
    """
    if r is None:
        return
    since = (
        datetime.now(timezone.utc) - timedelta(days=c.cancellation_window_days)
    ).isoformat(timespec="seconds")
    cancel_rate, refund_rate, sample = r.outcome_rates(p.candidate.lever.value, since)
    if sample == 0:
        return
    if cancel_rate > c.cancellation_rate_max:
        raise _Block(
            "cancellation_stop_conditions",
            f"{p.candidate.lever.value} cancellation rate {cancel_rate:.0%} over {sample} "
            f"offers exceeds {c.cancellation_rate_max:.0%} — offer type stopped",
        )
    if refund_rate > c.refund_rate_max:
        raise _Block(
            "cancellation_stop_conditions",
            f"{p.candidate.lever.value} refund rate {refund_rate:.0%} over {sample} "
            f"offers exceeds {c.refund_rate_max:.0%} — offer type stopped",
        )


# Order matters: kill switch short-circuits, then replay, then provenance, then money.
_Check = Callable[[Proposal, OrderEvent, EligibleSet, Config, "ReplayLookup | None"], None]

CHECKS: tuple[tuple[str, _Check], ...] = (
    ("kill_switch", _check_kill_switch),
    ("idempotency", _check_idempotency),
    ("not_in_eligible_set", _check_in_eligible_set),
    ("never_discount_identical_sku", _check_never_discount_identical_sku),
    ("never_downsell_qualified_buyer", _check_never_downsell_qualified_buyer),
    ("anchor_price_multiple_min", _check_anchor_price_multiple_min),
    ("margin_floor", _check_margin_floor),
    ("discount_ceiling", _check_discount_ceiling),
    ("rollover_credit", _check_rollover_credit),
    ("continuity_never_standalone", _check_continuity_never_standalone),
    ("daily_budget", _check_daily_budget),
    ("sequence_largest_first", _check_sequence_largest_first),
    ("fatigue_cap", _check_fatigue_cap),
    ("cancellation_stop_conditions", _check_cancellation_stop_conditions),
)

TIER_1_INVARIANTS: tuple[str, ...] = tuple(name for name, _ in CHECKS)


def evaluate(
    proposal: Proposal,
    event: OrderEvent,
    eligible: EligibleSet,
    config: Config,
    replay: ReplayLookup | None = None,
) -> GuardResult:
    """The single entry point. Nothing reaches stage [5] without an APPROVED result.

    Returns rather than raises, so the caller cannot accidentally swallow a block in an
    exception handler and proceed.
    """
    for _name, check in CHECKS:
        try:
            check(proposal, event, eligible, config, replay)
        except _Block as blocked:
            return GuardResult(
                verdict=Verdict.BLOCKED,
                proposal=proposal,
                invariant=blocked.invariant,
                citation=CITATIONS.get(blocked.invariant, "ours"),
                counterfactual=blocked.counterfactual,
            )
    # auto_approve is the one rule that does not reject. Everything above passed, so the
    # action is legal — the only question left is whether it is large enough to need a
    # human. PENDING_APPROVAL is not an approval: GuardResult.approved stays False, so
    # the adapter refuses it on exactly the path it refuses a block.
    if proposal.candidate.offer_price >= config.auto_approve_threshold_inr:
        return GuardResult(
            verdict=Verdict.PENDING_APPROVAL,
            proposal=proposal,
            invariant="auto_approve",
            citation=CITATIONS["auto_approve"],
            counterfactual=(
                f"INR {proposal.candidate.offer_price} is at or above the "
                f"INR {config.auto_approve_threshold_inr} auto-approve threshold — "
                "written as PENDING_APPROVAL, not executed"
            ),
        )
    return GuardResult(verdict=Verdict.APPROVED, proposal=proposal)
