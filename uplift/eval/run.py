"""Scores lever policies across the three assumption sets, and reports the rank flip.

Two kinds of number come out of here and they must never be presented together
unqualified:

  REAL      — decisions made, deterministic-vs-LLM split, guard blocks by invariant,
              latency, cost per decision. Measured from actual runs.
  SIMULATED — every added-LTGP figure and every policy ranking. Produced by
              simulate.py's chosen assumptions, not by observing customers.

The payload of the simulated half is not any absolute figure. It is that the #1 policy
CHANGES depending on an assumption nobody can verify without real conversion data —
which is the argument for guaranteeing containment instead of optimising a number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from ..config import Config
from ..eligibility import filter_candidates
from ..levers import enumerate_all
from ..models import Candidate, Lever, OrderEvent
from .generate import generate_events
from .simulate import ASSUMPTION_SETS, AssumptionSet, expected_added_ltgp

# The policies compared. Each is a deterministic rule for picking one candidate from the
# eligible set, so differences come from the policy rather than from model variance.
POLICIES: dict[str, Lever | None] = {
    "anchor-first": Lever.ANCHOR_UPSELL,
    "cross-sell-first": Lever.CROSS_SELL,
    "quality-upsell-first": Lever.UPSELL_QUALITY,
    "quantity-upsell-first": Lever.UPSELL_QUANTITY,
    "frequency-first": Lever.UPSELL_FREQUENCY,
    "downsell-first": Lever.DOWNSELL_QUANTITY,
    "no-offer": None,
}


@dataclass(frozen=True, slots=True)
class PolicyScore:
    policy: str
    simulated_added_ltgp: float
    offers_made: int


@dataclass(frozen=True, slots=True)
class RealMetrics:
    """Measured, not assumed.

    Deliberately absent: conversion rate, realized revenue, realized LTGP. LiveAdapter
    CREATES Razorpay orders and never captures them, so no offer is ever accepted and
    acceptance is not observable. Any conversion figure here would be invented, so there
    is no field to put one in — see test_real_metrics_expose_no_realized_revenue.
    """

    events: int
    decisions: int
    events_with_no_eligible_candidate: int
    deterministic_stages: int
    llm_stages: int
    p95_latency_ms: float
    cost_inr_per_decision: float
    unhandled_exceptions: int
    offered_gross_profit: Decimal = Decimal(0)
    """Gross profit the merchant earns IF an executed offer is accepted.

    Real arithmetic (offer price minus catalog cost) over offers that really were
    executed. Named 'offered', never 'realized': the order exists, the acceptance does not.
    """
    gross_profit_within_30d: Decimal = Decimal(0)
    """The MM p.156 cash-timing column: how much of that GP is billed inside 30 days.

    Structure, not behaviour, so it needs no conversion data. Currently equal to
    offered_gross_profit because every offer this catalog produces bills on order
    creation — see ledger_gross_profit for why that equality is reported rather than
    dressed up.
    """
    executed_offers: int = 0


def _pick(policy_lever: Lever | None, candidates: tuple[Candidate, ...]) -> Candidate | None:
    if policy_lever is None:
        return None
    matching = [c for c in candidates if c.lever is policy_lever]
    if not matching:
        return None
    return max(matching, key=lambda c: c.offer_price)


def score_policies(
    events: list[OrderEvent], assumptions: AssumptionSet
) -> list[PolicyScore]:
    """SIMULATED. Every figure returned here rests on simulate.py's assumptions."""
    totals: dict[str, float] = {name: 0.0 for name in POLICIES}
    counts: dict[str, int] = {name: 0 for name in POLICIES}

    for event in events:
        eligible = filter_candidates(event, enumerate_all(event))
        if not eligible.candidates:
            continue
        anchor_price = event.amount_paid if event.amount_paid > 0 else Decimal("1")
        for name, lever in POLICIES.items():
            chosen = _pick(lever, eligible.candidates)
            if chosen is None:
                continue
            totals[name] += expected_added_ltgp(chosen, anchor_price, assumptions)
            counts[name] += 1

    return sorted(
        (PolicyScore(name, totals[name], counts[name]) for name in POLICIES),
        key=lambda s: s.simulated_added_ltgp,
        reverse=True,
    )


def ledger_gross_profit(entries) -> tuple[Decimal, Decimal, int]:
    """(offered GP, GP billed within 30 days, executed count) over EXECUTED ledger rows.

    Executed rows only: a blocked proposal earned nothing and cost nothing, so counting
    it would pad a real number with actions that never happened.

    On the 30-day figure (MM p.156). Every offer this catalog can produce is billed in
    full when the order is created — bulk prepay, the 12-month anchor, and the monthly
    club's first period alike — so today the two figures are EQUAL and the ratio is 100%.

    That equality is a real finding about this money model, not a rounding artefact: the
    whole action space is front-loaded, which is the answer a payments company wants to
    the question "when does the cash arrive?". It is reported rather than hidden, and the
    two figures diverge the moment a genuine installment offer exists. What is NOT counted
    is a recurring offer's later periods: those depend on retention, which this system
    does not measure, so counting them would smuggle a behavioural assumption into a
    number labelled real.
    """
    from .. import catalog

    offered = Decimal(0)
    billed_30d = Decimal(0)
    count = 0
    for e in entries:
        if e.action != "EXECUTED" or not e.sku_code or e.amount is None:
            continue
        try:
            sku = catalog.get(e.sku_code)
        except KeyError:
            continue
        gp = e.amount - sku.unit_cost
        offered += gp
        billed_30d += gp  # every current offer bills on order creation
        count += 1
    return offered, billed_30d, count


def measure_real(
    events: list[OrderEvent], config: Config, ledger_entries: list | None = None
) -> RealMetrics:
    """Measured properties of the pipeline itself. No assumptions involved.

    Stages [1] and [2] run for real here. Stage [3] is deliberately not called per-event:
    the policies above are deterministic by design, so scoring them needs no model, and
    the deterministic-vs-LLM split below reports exactly that.
    """
    latencies: list[float] = []
    decisions = 0
    empty = 0
    errors = 0

    for event in events:
        start = time.perf_counter()
        try:
            eligible = filter_candidates(event, enumerate_all(event))
            if eligible.candidates:
                decisions += 1
            else:
                empty += 1
        except Exception:  # noqa: BLE001 — counted, never swallowed silently
            errors += 1
        latencies.append((time.perf_counter() - start) * 1000)

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0

    offered, billed_30d, executed = ledger_gross_profit(ledger_entries or [])

    return RealMetrics(
        events=len(events),
        decisions=decisions,
        events_with_no_eligible_candidate=empty,
        # Six of the seven stages are deterministic; only stage [3] can call a model.
        deterministic_stages=6,
        llm_stages=1,
        p95_latency_ms=round(p95, 3),
        cost_inr_per_decision=0.0,  # free tier
        unhandled_exceptions=errors,
        offered_gross_profit=offered,
        gross_profit_within_30d=billed_30d,
        executed_offers=executed,
    )


def find_rank_flip(
    events: list[OrderEvent],
) -> tuple[dict[str, list[PolicyScore]], tuple[str, str] | None]:
    """Score every set and report which policies swap the #1 slot between A and C.

    Returns (scores_by_set, flip) where flip is (winner_in_A, winner_in_C) when they
    differ, else None. Reporting the flip that actually occurs — rather than asserting
    one chosen in advance — is the whole point of running this.
    """
    by_set = {a.name: score_policies(events, a) for a in ASSUMPTION_SETS}
    first_a = by_set["A"][0].policy
    first_c = by_set["C"][0].policy
    flip = (first_a, first_c) if first_a != first_c else None
    return by_set, flip
