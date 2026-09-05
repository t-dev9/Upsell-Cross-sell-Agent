"""One test per Tier 1 invariant, named test_<invariant_name>.

That naming is load-bearing: `pytest -k <invariant>` must resolve for every row in
ARCHITECTURE.md's MONEY_MODEL table. A row whose invariant name returns no tests is not
enforced and must be deleted from the table.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from uplift import catalog, money_guard
from uplift.config import Config
from uplift.eligibility import EligibleSet, filter_candidates
from uplift.levers import enumerate_all
from uplift.models import Candidate, Customer, Lever, OrderEvent, Proposal, Verdict


def make_event(*, qualified: bool = False, sku_code: str = "WHEY_2KG", qty: int = 1) -> OrderEvent:
    sku = catalog.get(sku_code)
    customer = Customer(
        id="cust_test",
        past_order_skus=(),
        total_spend=Decimal(0),
        accepted_upsell_before=qualified,
    )
    paid = sku.list_price * qty if qualified else sku.list_price * qty * Decimal("0.7")
    return OrderEvent(
        event_id="evt_test",
        order_id="order_test",
        customer=customer,
        sku_code=sku_code,
        quantity=qty,
        amount_paid=paid,
    )


def eligible_with(candidate: Candidate, event: OrderEvent) -> EligibleSet:
    """An eligible set that contains the candidate, so provenance passes and the
    invariant under test is what actually fires."""
    base = filter_candidates(event, enumerate_all(event))
    return EligibleSet(
        candidates=(candidate, *base.candidates),
        rejected=base.rejected,
        buyer_qualified=base.buyer_qualified,
    )


def evaluate(candidate: Candidate, event: OrderEvent, config: Config, source="ai"):
    proposal = Proposal(candidate=candidate, pitch="", source=source)
    return money_guard.evaluate(proposal, event, eligible_with(candidate, event), config)


# --------------------------------------------------------------- Tier 1 invariants


def test_kill_switch():
    """A single boolean rejects everything, whatever else would have passed."""
    event = make_event()
    sku = catalog.get("CREATINE")
    good = Candidate(lever=Lever.CROSS_SELL, sku_code=sku.code, offer_price=sku.list_price)

    assert evaluate(good, event, Config(kill_switch=False)).approved

    result = evaluate(good, event, Config(kill_switch=True))
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "kill_switch"
    assert result.counterfactual


def test_never_discount_identical_sku():
    """MM p.97-98 — same SKU, same quantity, lower price is a raw discount."""
    event = make_event()
    sku = catalog.get(event.sku_code)
    rogue = Candidate(
        lever=Lever.UPSELL_QUALITY,
        sku_code=sku.code,
        offer_price=(sku.list_price * Decimal("0.4")).quantize(Decimal("1")),
        quantity=1,
    )
    result = evaluate(rogue, event, Config())
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "never_discount_identical_sku"
    assert result.citation == "MM p.97"


def test_never_discount_identical_sku_allows_different_quantity():
    """Buying more at a better unit rate is a different offer, not a discount."""
    event = make_event()
    sku = catalog.get(event.sku_code)
    bulk = Candidate(
        lever=Lever.UPSELL_QUANTITY,
        sku_code=sku.code,
        offer_price=(sku.list_price * 2 * Decimal("0.9")).quantize(Decimal("1")),
        quantity=2,
    )
    result = evaluate(bulk, event, Config())
    assert result.invariant != "never_discount_identical_sku"


def test_never_discount_identical_sku_allows_subscription_terms():
    """Lever 3 changes HOW they pay, which is what MM p.97-98 tells you to do instead
    of discounting. Subscribe-and-save must not read as a raw discount."""
    event = make_event()
    sku = catalog.get(event.sku_code)
    subscription = Candidate(
        lever=Lever.UPSELL_FREQUENCY,
        sku_code=sku.code,
        offer_price=(sku.list_price * Decimal("0.92")).quantize(Decimal("1")),
        quantity=1,
    )
    result = evaluate(subscription, event, Config())
    assert result.approved, f"blocked by {result.invariant}"


def test_frequency_lever_cannot_smuggle_a_steep_discount():
    """The lever-3 exemption is not a bypass — discount_ceiling still binds it.

    Relabelling a 60%-off proposal as a subscription must not get it through.
    """
    event = make_event()
    sku = catalog.get(event.sku_code)
    smuggled = Candidate(
        lever=Lever.UPSELL_FREQUENCY,
        sku_code=sku.code,
        offer_price=(sku.list_price * Decimal("0.4")).quantize(Decimal("1")),
        quantity=1,
    )
    result = evaluate(smuggled, event, Config())
    assert result.verdict is Verdict.BLOCKED
    # Which check catches it depends on the price: here 60% off lands below unit cost,
    # so margin_floor fires first. Either is correct — the property under test is that
    # relabelling the lever does not open a path to execution.
    assert result.invariant in {"margin_floor", "discount_ceiling"}


def test_never_downsell_qualified_buyer():
    """LTV p.19 — downsells are for unqualified prospects only."""
    event = make_event(qualified=True)
    smaller = catalog.get("WHEY_1KG")
    downsell = Candidate(
        lever=Lever.DOWNSELL_QUANTITY,
        sku_code=smaller.code,
        offer_price=smaller.list_price,
    )
    result = evaluate(downsell, event, Config())
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "never_downsell_qualified_buyer"
    assert result.citation == "LTV p.19"


def test_never_downsell_qualified_buyer_is_rederived_not_trusted():
    """The guard recomputes qualification instead of reading the upstream flag.

    Hand it an EligibleSet that lies (buyer_qualified=False) about a buyer who is in
    fact qualified. The block must still fire.
    """
    event = make_event(qualified=True)
    smaller = catalog.get("WHEY_1KG")
    downsell = Candidate(
        lever=Lever.DOWNSELL_QUANTITY, sku_code=smaller.code, offer_price=smaller.list_price
    )
    lying = EligibleSet(candidates=(downsell,), rejected=(), buyer_qualified=False)
    result = money_guard.evaluate(
        Proposal(candidate=downsell, pitch="", source="ai"), event, lying, Config()
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "never_downsell_qualified_buyer"


def test_anchor_price_multiple_min():
    """MM p.84-88 — an 'anchor' below 5x the baseline is not an anchor."""
    event = make_event()
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    baseline = catalog.get(event.sku_code).list_price
    too_cheap = Candidate(
        lever=Lever.ANCHOR_UPSELL,
        sku_code=anchor.code,
        offer_price=(baseline * 2).quantize(Decimal("1")),
    )
    result = evaluate(too_cheap, event, Config())
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "anchor_price_multiple_min"
    assert result.citation == "MM p.84-88"

    # At list the anchor clears this rule. It comes back PENDING_APPROVAL rather than
    # APPROVED because INR 24999 is above the auto-approve threshold — a different rule,
    # so assert on the invariant rather than on approval.
    at_list = Candidate(
        lever=Lever.ANCHOR_UPSELL, sku_code=anchor.code, offer_price=anchor.list_price
    )
    assert evaluate(at_list, event, Config()).invariant != "anchor_price_multiple_min"


def test_margin_floor():
    """Cost comes from the catalog, so a below-cost bundle cannot pass."""
    event = make_event()
    sku = catalog.get("CREATINE")
    below_cost = Candidate(
        lever=Lever.CROSS_SELL,
        sku_code=sku.code,
        offer_price=(sku.unit_cost * Decimal("0.9")).quantize(Decimal("1")),
    )
    result = evaluate(below_cost, event, Config(discount_ceiling_pct=1.0))
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "margin_floor"


def test_discount_ceiling():
    """Effective discount off list must not exceed the configured ceiling."""
    event = make_event()
    sku = catalog.get("CREATINE")
    steep = Candidate(
        lever=Lever.CROSS_SELL,
        sku_code=sku.code,
        offer_price=(sku.list_price * Decimal("0.6")).quantize(Decimal("1")),
    )
    result = evaluate(steep, event, Config(margin_floor_pct=0.0))
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "discount_ceiling"


def test_idempotency():
    """A replayed event returns the recorded action instead of creating a second one.

    Promoted to Tier 1 because it is the enforcement behind a whole red-team category.
    """

    SeenBefore = lambda: _LedgerStub(seen=True)  # noqa: E731
    NeverSeen = _LedgerStub

    event = make_event()
    sku = catalog.get("CREATINE")
    good = Candidate(lever=Lever.CROSS_SELL, sku_code=sku.code, offer_price=sku.list_price)
    proposal = Proposal(candidate=good, pitch="", source="ai")
    eligible = eligible_with(good, event)

    first = money_guard.evaluate(proposal, event, eligible, Config(), replay=NeverSeen())
    assert first.approved

    replay = money_guard.evaluate(proposal, event, eligible, Config(), replay=SeenBefore())
    assert replay.verdict is Verdict.BLOCKED
    assert replay.invariant == "idempotency"
    assert "no second money action" in (replay.counterfactual or "")


def test_idempotency_blocks_replay_end_to_end(tmp_path):
    """Through the real pipeline and a real ledger: the same event twice executes once."""
    from uplift.ledger import Ledger
    from uplift.pipeline import run
    from uplift.razorpay_adapter import MockAdapter
    from uplift.selector import FixtureProvider

    led = Ledger(tmp_path / "replay.db")
    adapter = MockAdapter()
    event = make_event()
    provider = FixtureProvider('{"choice": 1, "pitch": "first"}')

    first = run(event, Config(), led, adapter, provider=provider)
    second = run(event, Config(), led, adapter, provider=provider)

    assert first.result.approved
    assert second.result.verdict is Verdict.BLOCKED
    assert second.result.invariant == "idempotency"
    assert len(adapter.receipts) == 1, "the money action must happen exactly once"
    led.close()


def test_not_in_eligible_set():
    """A candidate stage [2] never produced cannot be executed, however it arrived."""
    event = make_event()
    sku = catalog.get("CREATINE")
    invented = Candidate(
        lever=Lever.CROSS_SELL, sku_code=sku.code, offer_price=sku.list_price
    )
    empty = EligibleSet(candidates=(), rejected=(), buyer_qualified=False)
    result = money_guard.evaluate(
        Proposal(candidate=invented, pitch="", source="ai"), event, empty, Config()
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "not_in_eligible_set"


# ------------------------------------------------------------- structural claims


@pytest.mark.parametrize(
    "candidate_factory,expected",
    [
        (
            lambda e: Candidate(
                lever=Lever.UPSELL_QUALITY,
                sku_code=e.sku_code,
                offer_price=(catalog.get(e.sku_code).list_price * Decimal("0.4")).quantize(
                    Decimal("1")
                ),
                quantity=1,
            ),
            "never_discount_identical_sku",
        ),
        (
            lambda e: Candidate(
                lever=Lever.ANCHOR_UPSELL,
                sku_code=catalog.ANCHOR_SKU_CODE,
                offer_price=(catalog.get(e.sku_code).list_price * 2).quantize(Decimal("1")),
            ),
            "anchor_price_multiple_min",
        ),
    ],
)
def test_identical_treatment_of_ai_and_fallback(candidate_factory, expected):
    """The single-door claim, as an executable assertion.

    The same violating proposal must be blocked identically whether it is tagged
    source='ai' or source='fallback'. If anyone ever adds a branch keyed on provenance,
    this test fails.
    """
    event = make_event()
    candidate = candidate_factory(event)
    config = Config()

    as_ai = evaluate(candidate, event, config, source="ai")
    as_fallback = evaluate(candidate, event, config, source="fallback")

    assert as_ai.verdict is as_fallback.verdict is Verdict.BLOCKED
    assert as_ai.invariant == as_fallback.invariant == expected
    assert as_ai.citation == as_fallback.citation
    assert as_ai.counterfactual == as_fallback.counterfactual


def test_money_guard_never_reads_proposal_source():
    """Static check: no branch in money_guard.py keys on proposal provenance."""
    from pathlib import Path

    src = Path(money_guard.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    body = code.split('"""', 2)[-1]  # drop the module docstring, which discusses source
    assert ".source" not in body, "money_guard must not read proposal.source"


def test_every_tier_1_invariant_has_a_test():
    """CLAUDE.md section 3: no enforcing test => delete the row.

    Enforced mechanically here rather than by hand-checking the table.
    """
    from pathlib import Path

    tests_src = Path(__file__).read_text(encoding="utf-8")
    for invariant in money_guard.TIER_1_INVARIANTS:
        assert f"def test_{invariant}" in tests_src, f"{invariant} has no test named after it"


# ------------------------------------------- Tier 2, promoted and enforced
# Each of these governs an offer type that was added so the rule has something real to
# refuse. A check that can never fire is not enforcement, whatever the config says.


class _LedgerStub:
    """Minimal ReplayLookup. Money Guard talks to a Protocol, so no database is needed."""

    def __init__(self, *, spend=Decimal(0), accepted=False, seen=False, shown=0,
                 rates=(0.0, 0.0, 0)):
        self._spend, self._accepted, self._seen = spend, accepted, seen
        self._shown, self._rates = shown, rates

    def find_by_event_id(self, event_id):
        return object() if self._seen else None

    def discount_spend_today(self):
        return self._spend

    def has_accepted_anchor_or_upsell(self, customer_id):
        return self._accepted

    def offers_shown_since(self, customer_id, since_iso):
        return self._shown

    def outcome_rates(self, lever, since_iso):
        return self._rates


def test_daily_budget():
    """Individually-legal offers can still add up to an unacceptable day."""
    event = make_event()
    sku = catalog.get("CREATINE")
    # A small, otherwise-legal discount.
    cand = Candidate(
        lever=Lever.CROSS_SELL,
        sku_code=sku.code,
        offer_price=(sku.list_price * Decimal("0.9")).quantize(Decimal("1")),
    )
    proposal = Proposal(candidate=cand, pitch="", source="ai")
    eligible = eligible_with(cand, event)
    cfg = Config(daily_budget_inr=Decimal("100"))

    fresh = money_guard.evaluate(proposal, event, eligible, cfg, replay=_LedgerStub())
    assert fresh.approved, f"blocked by {fresh.invariant}"

    spent = money_guard.evaluate(
        proposal, event, eligible, cfg, replay=_LedgerStub(spend=Decimal("95"))
    )
    assert spent.verdict is Verdict.BLOCKED
    assert spent.invariant == "daily_budget"


def test_rollover_credit():
    """MM p.92 — credit <= 25% of the anchor, and the offer must be >= 4x the credit."""
    event = make_event()
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)

    oversized = Candidate(
        lever=Lever.ROLLOVER,
        sku_code=anchor.code,
        offer_price=anchor.list_price,
        credit_amount=(anchor.list_price * Decimal("0.5")).quantize(Decimal("1")),
    )
    result = evaluate(oversized, event, Config())
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "rollover_credit"
    assert result.citation == "MM p.92"


def test_rollover_credit_rejects_a_credit_that_unlocks_too_little():
    """The second half of MM p.92: a credit unlocking a barely-bigger offer is a discount."""
    event = make_event()
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    # Chosen to clear margin_floor and discount_ceiling, which sit earlier in the
    # registry, so the 4x rule is provably what fires rather than a cheaper check.
    credit = Decimal("6000")  # within the 25% cap on a 24999 anchor
    weak = Candidate(
        lever=Lever.ROLLOVER,
        sku_code=anchor.code,
        offer_price=Decimal("22000"),  # < 4 x 6000, but only 12% off list
        credit_amount=credit,
    )
    result = evaluate(weak, event, Config())
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "rollover_credit"


def test_rollover_credit_allows_a_well_formed_credit():
    event = make_event()
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    credit = (anchor.list_price * Decimal("0.1")).quantize(Decimal("1"))
    good = Candidate(
        lever=Lever.ROLLOVER,
        sku_code=anchor.code,
        offer_price=anchor.list_price - credit,
        credit_amount=credit,
    )
    result = evaluate(good, event, Config())
    assert result.invariant != "rollover_credit"


def test_continuity_never_standalone():
    """MM p.146 — continuity sits on top of a relationship, never at the front end."""
    club = catalog.get("CLUB_MONTHLY")
    cand = Candidate(lever=Lever.CONTINUITY, sku_code=club.code, offer_price=club.list_price)
    proposal = Proposal(candidate=cand, pitch="", source="ai")

    cold = make_event(qualified=False)
    result = money_guard.evaluate(
        proposal, cold, eligible_with(cand, cold), Config(), replay=_LedgerStub()
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.invariant == "continuity_never_standalone"
    assert result.citation == "MM p.146"

    # Same offer, same code path — allowed once the ledger shows a prior upsell.
    warm = money_guard.evaluate(
        proposal, cold, eligible_with(cand, cold), Config(), replay=_LedgerStub(accepted=True)
    )
    assert warm.invariant != "continuity_never_standalone"


def test_continuity_is_offered_by_the_generator_so_the_guard_can_refuse_it():
    """The generator must NOT pre-filter continuity.

    If levers.py quietly avoided offering it to cold customers, the invariant would never
    be exercised by anything and would prove nothing. The rule has to be what refuses it.
    """
    from uplift.levers import enumerate_all

    cold = make_event(qualified=False)
    levers_offered = {c.lever for c in enumerate_all(cold)}
    assert Lever.CONTINUITY in levers_offered


def test_sequence_largest_first():
    """LTV p.15,17 — the larger variant on the same axis must be offered first."""
    event = make_event()
    # Both differ from the purchased SKU, so never_discount_identical_sku (earlier in
    # the registry) cannot fire and the sequencing rule is provably what blocks.
    small = catalog.get("ISOLATE_2KG")
    big = catalog.get("WHEY_5KG")
    smaller_first = Candidate(
        lever=Lever.UPSELL_QUANTITY, sku_code=small.code, offer_price=small.list_price
    )
    bigger = Candidate(
        lever=Lever.UPSELL_QUANTITY, sku_code=big.code, offer_price=big.list_price
    )
    pool = EligibleSet(candidates=(smaller_first, bigger), rejected=(), buyer_qualified=False)

    blocked = money_guard.evaluate(
        Proposal(candidate=smaller_first, pitch="", source="ai"), event, pool, Config()
    )
    assert blocked.verdict is Verdict.BLOCKED
    assert blocked.invariant == "sequence_largest_first"
    assert blocked.citation == "LTV p.15,17"

    ok = money_guard.evaluate(
        Proposal(candidate=bigger, pitch="", source="ai"), event, pool, Config()
    )
    assert ok.invariant != "sequence_largest_first"


def test_sequence_largest_first_does_not_apply_to_cross_sell():
    """Complements are ranked by lift, not price.

    Forcing the priciest complement would override the market-basket signal with a price
    sort — selling the wrong product more expensively.
    """
    event = make_event()
    cheap = catalog.get("SHAKER")
    dear = catalog.get("PREWORKOUT")
    a = Candidate(lever=Lever.CROSS_SELL, sku_code=cheap.code, offer_price=cheap.list_price)
    b = Candidate(lever=Lever.CROSS_SELL, sku_code=dear.code, offer_price=dear.list_price)
    pool = EligibleSet(candidates=(a, b), rejected=(), buyer_qualified=False)

    result = money_guard.evaluate(
        Proposal(candidate=a, pitch="", source="ai"), event, pool, Config()
    )
    assert result.invariant != "sequence_largest_first"


def test_fatigue_cap():
    """Offers already shown count; offers the guard itself blocked do not."""
    event = make_event()
    sku = catalog.get("CREATINE")
    cand = Candidate(lever=Lever.CROSS_SELL, sku_code=sku.code, offer_price=sku.list_price)
    proposal = Proposal(candidate=cand, pitch="", source="ai")
    pool = eligible_with(cand, event)
    cfg = Config(fatigue_cap_per_window=3)

    assert money_guard.evaluate(
        proposal, event, pool, cfg, replay=_LedgerStub(shown=2)
    ).invariant != "fatigue_cap"

    tired = money_guard.evaluate(proposal, event, pool, cfg, replay=_LedgerStub(shown=3))
    assert tired.verdict is Verdict.BLOCKED
    assert tired.invariant == "fatigue_cap"


def test_cancellation_stop_conditions():
    """MM p.59/144/35 — stop an offer type whose real outcomes have gone bad."""
    event = make_event()
    sku = catalog.get("CREATINE")
    cand = Candidate(lever=Lever.CROSS_SELL, sku_code=sku.code, offer_price=sku.list_price)
    proposal = Proposal(candidate=cand, pitch="", source="ai")
    pool = eligible_with(cand, event)

    # No history: the monitor abstains rather than inventing a rate from no data.
    quiet = money_guard.evaluate(
        proposal, event, pool, Config(), replay=_LedgerStub(rates=(0.0, 0.0, 0))
    )
    assert quiet.invariant != "cancellation_stop_conditions"

    bad = money_guard.evaluate(
        proposal, event, pool, Config(), replay=_LedgerStub(rates=(0.4, 0.0, 20))
    )
    assert bad.verdict is Verdict.BLOCKED
    assert bad.invariant == "cancellation_stop_conditions"
    assert bad.citation == "MM p.59,144,35"


def test_auto_approve():
    """Above the threshold: PENDING_APPROVAL, and it must NOT be executable."""
    from uplift.razorpay_adapter import ExecutionRefused, MockAdapter

    event = make_event()
    anchor = catalog.get(catalog.ANCHOR_SKU_CODE)
    big = Candidate(
        lever=Lever.ANCHOR_UPSELL, sku_code=anchor.code, offer_price=anchor.list_price
    )
    result = evaluate(big, event, Config(auto_approve_threshold_inr=Decimal("20000")))
    assert result.verdict is Verdict.PENDING_APPROVAL
    assert result.invariant == "auto_approve"
    assert result.counterfactual

    # The load-bearing half: pending is as unable to reach stage [5] as blocked is.
    assert not result.approved
    adapter = MockAdapter()
    with pytest.raises(ExecutionRefused):
        adapter.execute(result, "order_x")
    assert adapter.receipts == []

    below = evaluate(big, event, Config(auto_approve_threshold_inr=Decimal("99999")))
    assert below.approved
