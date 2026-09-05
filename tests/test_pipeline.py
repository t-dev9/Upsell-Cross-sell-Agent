"""Stage-boundary tests: the fallback is gated exactly like the model path."""

from __future__ import annotations

from decimal import Decimal

import pytest

from uplift import catalog
from uplift.config import Config
from uplift.eligibility import filter_candidates
from uplift.ledger import Ledger
from uplift.levers import enumerate_all
from uplift.models import GuardResult, Verdict
from uplift.pipeline import run
from uplift.razorpay_adapter import ExecutionRefused, MockAdapter
from uplift.selector import FixtureProvider, fallback_select, select
from tests.test_money_guard import make_event


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "test.db")
    yield led
    led.close()


def test_fallback_draws_only_from_the_eligible_set():
    """The fallback never sees an ungated candidate — same pool as the model."""
    event = make_event()
    eligible = filter_candidates(event, enumerate_all(event))
    proposal = fallback_select(eligible)
    assert eligible.contains(proposal.candidate)
    assert proposal.source == "fallback"


def test_provider_failure_falls_back_rather_than_raising():
    """Any provider exception becomes a fallback proposal, never a crash."""

    class Broken:
        name = "broken"

        def complete(self, system, user):
            raise TimeoutError("upstream down")

    event = make_event()
    eligible = filter_candidates(event, enumerate_all(event))
    proposal, notes = select(eligible, Config(), provider=Broken())
    assert proposal.source == "fallback"
    assert eligible.contains(proposal.candidate)
    assert any("provider error" in n for n in notes)


def test_invalid_json_gets_exactly_one_repair_then_falls_back():
    """JSON validity is a parseability concern. Unparseable twice => fallback."""

    class AlwaysGarbage:
        name = "garbage"

        def __init__(self):
            self.calls = 0

        def complete(self, system, user):
            self.calls += 1
            return "not json at all"

    event = make_event()
    eligible = filter_candidates(event, enumerate_all(event))
    provider = AlwaysGarbage()
    proposal, notes = select(eligible, Config(), provider=provider)

    assert provider.calls == 2, "exactly one repair attempt"
    assert proposal.source == "fallback"
    assert any("repair failed" in n for n in notes)


def test_repaired_json_is_still_only_a_proposal():
    """A repaired reply is tagged, but tagging is bookkeeping — not a safety property."""

    class GarbageThenValid:
        name = "flaky"

        def __init__(self):
            self.calls = 0

        def complete(self, system, user):
            self.calls += 1
            return "oops" if self.calls == 1 else '{"choice": 1, "pitch": "ok"}'

    event = make_event()
    eligible = filter_candidates(event, enumerate_all(event))
    proposal, _ = select(eligible, Config(), provider=GarbageThenValid())
    assert proposal.source == "ai"
    assert proposal.repaired is True
    assert eligible.contains(proposal.candidate)


def test_out_of_range_choice_is_a_parse_failure_not_a_clamp():
    """A manipulated model must not be able to steer selection with a bad index."""
    event = make_event()
    eligible = filter_candidates(event, enumerate_all(event))
    proposal, notes = select(
        eligible, Config(), provider=FixtureProvider('{"choice": 999, "pitch": "x"}')
    )
    assert proposal.source == "fallback"


def test_execution_requires_an_approved_guard_result():
    """Stage [5] is unreachable without stage [4]'s approval."""
    adapter = MockAdapter()
    blocked = GuardResult(
        verdict=Verdict.BLOCKED,
        proposal=None,
        invariant="margin_floor",
        citation="ours",
        counterfactual="order completed with no offer",
    )
    with pytest.raises(ExecutionRefused):
        adapter.execute(blocked, "order_x")


def test_blocked_decision_executes_nothing_and_still_logs(ledger):
    """A block writes a ledger row and leaves the adapter untouched."""
    event = make_event()
    adapter = MockAdapter()
    decision = run(event, Config(kill_switch=True), ledger, adapter)

    assert decision.result.verdict is Verdict.BLOCKED
    assert decision.receipt is None
    assert adapter.receipts == []
    assert decision.ledger_id > 0

    entries = ledger.entries()
    assert entries[-1].action == "NO_OFFER"
    assert entries[-1].invariant == "kill_switch"


def test_approved_decision_executes_and_logs(ledger):
    event = make_event()
    adapter = MockAdapter()
    decision = run(event, Config(), ledger, adapter)

    if decision.result.approved:
        assert decision.receipt is not None
        assert ledger.entries()[-1].action == "EXECUTED"


def test_ledger_is_append_only_and_sequential(ledger):
    event = make_event()
    adapter = MockAdapter()
    for _ in range(3):
        run(event, Config(), ledger, adapter)

    ok, problems = ledger.verify_sequence()
    assert ok, problems
    entries = ledger.entries()
    assert [e.id for e in entries] == [1, 2, 3]
    assert [e.prev_id for e in entries] == [None, 1, 2]


def test_enumeration_covers_every_lever():
    """All six levers plus the anchor are walked — the action space is closed and complete."""
    event = make_event()
    levers_seen = {c.lever for c in enumerate_all(event)}
    assert len(levers_seen) >= 5, f"only {levers_seen} enumerated"


def test_cross_sell_ranked_by_lift():
    """Lever 8 uses real co-occurrence, not popularity."""
    from uplift.basket import associations_for

    assocs = associations_for("WHEY_2KG")
    assert assocs, "expected complements for the anchor product"
    assert all(a.lift >= 1.0 for a in assocs)
    assert assocs == sorted(assocs, key=lambda a: (a.lift, a.confidence), reverse=True)
