"""The rejection line is a contract, not a print statement.

cli.py's module docstring promises this file exists and asserts the format. A promise
about a test is the same failure as an invariant with no test behind it, so the promise
is kept here rather than quietly deleted.

The contract, as stated in cli.py:

    BLOCKED — <invariant> (<citation>) · <what it did instead> · ledger #<id>

Every field carries weight. A bare ``REJECTED: guard_violation`` would mean the action is
not explainable, which fails Track 01's bar ("every money action must be explainable,
bounded and gated") at the one moment it most needs to hold — and it is the single frame
the demo rests on. These tests render real pipeline output and read it back, so the
format cannot regress into something unreadable without a test going red.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from rich.console import Console

from uplift import catalog, cli, money_guard
from uplift.config import Config
from uplift.ledger import Ledger
from uplift.models import GuardResult, Verdict
from uplift.pipeline import run, run_injected
from uplift.razorpay_adapter import MockAdapter

# Each capture group is a field a human needs in order to audit the decision:
# which rule fired, where that rule came from, what happened instead, and where to
# find the record.
BLOCKED_LINE = re.compile(
    r"BLOCKED — (?P<invariant>\S+) \((?P<citation>[^)]+)\) · "
    r"(?P<counterfactual>.+?) · ledger #(?P<ledger>\d{4})"
)
PENDING_LINE = re.compile(
    r"PENDING_APPROVAL — (?P<invariant>\S+) \((?P<citation>[^)]+)\) · "
    r"(?P<counterfactual>.+?) · ledger #(?P<ledger>\d{4})"
)
EXECUTED_LINE = re.compile(
    r"EXECUTED — (?P<lever>\S+) (?P<sku>\S+) ₹(?P<price>[\d.]+) · "
    r"(?P<reference>\S+) · ledger #(?P<ledger>\d{4})"
)


@pytest.fixture
def rendered(monkeypatch):
    """Capture what the terminal actually shows.

    Width is pinned wide because Rich wraps to the console width, and a wrapped
    rejection line would fail these patterns for a reason that has nothing to do with
    the contract. The test asserts the format, not the terminal size.
    """
    console = Console(width=240, record=True, legacy_windows=False, force_terminal=False)
    monkeypatch.setattr(cli, "console", console)

    def _render(decision, *, injected=False):
        cli._render(decision, injected=injected)
        return console.export_text()

    return _render


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "contract.db")
    yield led
    led.close()


def test_cli_contract_blocked_line_carries_every_audit_field(rendered, ledger):
    """The frame the whole demo rests on. Rendered from a real injected run."""
    decision = run_injected(cli._demo_event(qualified=True), Config(), ledger)
    assert decision.result.verdict is Verdict.BLOCKED

    match = BLOCKED_LINE.search(rendered(decision, injected=True))
    assert match is not None, "BLOCKED line does not match the documented contract"

    assert match["invariant"] == decision.result.invariant
    assert match["citation"] == decision.result.citation
    assert match["counterfactual"] == decision.result.counterfactual
    assert int(match["ledger"]) == decision.ledger_id


def test_cli_contract_rejection_names_a_real_rule_and_a_real_source(rendered, ledger):
    """The invariant must be one the guard actually knows, and it must be citable.

    Asserting against money_guard.CITATIONS rather than a hardcoded string means a rule
    renamed in the guard but not in the renderer fails here instead of shipping a
    rejection that names a rule nobody can look up.
    """
    decision = run_injected(cli._demo_event(qualified=True), Config(), ledger)
    match = BLOCKED_LINE.search(rendered(decision, injected=True))

    assert match["invariant"] in money_guard.CITATIONS
    assert money_guard.CITATIONS[match["invariant"]] == match["citation"]
    assert match["counterfactual"].strip(), "a rejection must say what happened instead"


def test_cli_contract_rejection_is_never_a_generic_code(rendered, ledger):
    """The failure mode this contract exists to prevent."""
    decision = run_injected(cli._demo_event(qualified=True), Config(), ledger)
    text = rendered(decision, injected=True)

    for generic in ("guard_violation", "REJECTED:", "policy_error", "error"):
        assert f"BLOCKED — {generic}" not in text


def test_cli_contract_executed_line_ties_back_to_the_gateway(rendered, ledger):
    """An executed offer must render its gateway reference and its ledger row.

    An audit trail that cannot be tied back to the payment gateway is only half an
    audit trail, so the reference is part of the contract rather than a nicety.
    """
    decision = run(cli._demo_event(qualified=True), Config(), ledger, MockAdapter())
    assert decision.result.verdict is Verdict.APPROVED
    assert decision.receipt is not None

    match = EXECUTED_LINE.search(rendered(decision))
    assert match is not None, "EXECUTED line does not match the documented contract"
    assert match["reference"] == decision.receipt.reference
    assert match["sku"] == decision.proposal.candidate.sku_code
    assert int(match["ledger"]) == decision.ledger_id


def test_cli_contract_pending_approval_explains_itself_like_a_block(rendered, ledger):
    """PENDING_APPROVAL cannot reach stage [5] either, so it owes the same explanation.

    Built by swapping the verdict on a real decision: the auto-approve threshold is a
    config value, and a test that depended on a catalog price crossing it would break
    the next time a price moved.
    """
    decision = run(cli._demo_event(qualified=True), Config(), ledger, MockAdapter())
    held = GuardResult(
        verdict=Verdict.PENDING_APPROVAL,
        proposal=decision.result.proposal,
        invariant="auto_approve",
        citation=money_guard.CITATIONS["auto_approve"],
        counterfactual="held for a human decision",
    )
    decision = dataclasses.replace(decision, result=held, receipt=None)

    match = PENDING_LINE.search(rendered(decision))
    assert match is not None, "PENDING_APPROVAL must be as explainable as BLOCKED"
    assert match["invariant"] == "auto_approve"
    assert not held.approved, "PENDING_APPROVAL must never be executable"


def test_cli_contract_ledger_id_is_zero_padded(rendered, ledger):
    """`ledger #2` and `ledger #0002` sort differently by eye. The width is the contract."""
    decision = run_injected(cli._demo_event(qualified=True), Config(), ledger)
    match = BLOCKED_LINE.search(rendered(decision, injected=True))
    assert len(match["ledger"]) == 4


def test_cli_contract_injected_run_shows_the_attack_that_was_blocked(rendered, ledger):
    """A rejection the viewer cannot trace to an input proves nothing.

    The injected title is what makes the demo legible: without it on screen, the block
    is an assertion rather than a demonstration.
    """
    decision = run_injected(cli._demo_event(qualified=True), Config(), ledger)
    text = rendered(decision, injected=True)

    assert "injected product title" in text.lower()
    assert "60%" in text
    sku = catalog.get(decision.event.sku_code)
    assert sku.code in text
