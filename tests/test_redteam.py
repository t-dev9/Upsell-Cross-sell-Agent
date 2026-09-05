"""The red-team suite as a regression gate.

`uplift redteam` is the demo; this is the guarantee. If a future change lets any
adversarial case reach execution, CI fails here rather than on someone's screen.
"""

from __future__ import annotations

from uplift.config import Config
from uplift.eval.redteam import build_cases, run_cases, summary


def test_suite_is_exactly_thirty_cases():
    """A fixed, non-negotiable set — the denominator in 'N/30' must not drift."""
    assert len(build_cases()) == 30


def test_every_adversarial_case_is_blocked():
    """The core claim. No case reaches execution, whatever produced it."""
    results = run_cases(Config())
    escaped = [r.case.name for r in results if not r.blocked]
    assert not escaped, f"reached execution: {escaped}"


def test_every_case_is_blocked_by_the_expected_invariant():
    """Stronger than 'something blocked it' — the right rule fires for the right reason.

    Without this, a single over-broad check could mask every other invariant being
    broken and the scoreboard would still read 30/30.
    """
    results = run_cases(Config())
    wrong = [
        (r.case.name, r.case.expect, r.fired) for r in results if not r.matched_expectation
    ]
    assert not wrong, f"blocked by an unexpected invariant: {wrong}"


def test_both_provenances_are_represented():
    """Provenance must be exercised on both sides, or the single-door claim is untested."""
    sources = {c.proposal.source for c in build_cases()}
    assert sources == {"ai", "fallback"}

    fallback_cases = [c for c in build_cases() if c.proposal.source == "fallback"]
    assert len(fallback_cases) >= 8, "too few fallback cases to prove provenance is irrelevant"


def test_all_six_categories_are_covered():
    categories = {c.category for c in build_cases()}
    assert len(categories) == 6, categories


def test_scoreboard_totals_match_the_suite():
    results = run_cases(Config())
    assert sum(row["total"] for row in summary(results).values()) == 30
    assert sum(row["blocked"] for row in summary(results).values()) == 30
