"""Demo UI tests.

The UI must not become a second, weaker copy of the audit trail. These assert it shows
the same invariant, citation and counterfactual the CLI prints, and that it stays
offline-safe — a judge on a bad connection must still see the injection blocked.
"""

from __future__ import annotations

import re
from pathlib import Path

from uplift.config import Config
from uplift.ledger import Ledger
from uplift.pipeline import run_injected
from uplift.server import _decision_payload

UI = Path(__file__).resolve().parent.parent / "uplift" / "ui.html"


def test_page_loads_no_external_resources():
    """No CDN, no webfont, no build step — the page works with the network unplugged."""
    html = UI.read_text(encoding="utf-8")
    external = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', html)
    assert not external, f"external resources would break offline use: {external}"


def test_page_states_the_ledger_is_not_tamper_evident():
    """The honesty rule holds in the UI too, not only in the CLI and docs."""
    html = UI.read_text(encoding="utf-8").upper()
    assert "NOT TAMPER-EVIDENT" in html
    assert "NO HASH CHAIN" in html


def test_page_claims_no_uplift_number():
    html = UI.read_text(encoding="utf-8").upper()
    assert "NO UPLIFT NUMBER IS CLAIMED" in html


def test_decision_payload_carries_the_full_cli_contract(tmp_path):
    """invariant + citation + counterfactual + ledger id — all four, or the UI is
    less explainable than the terminal and fails Track 01's bar."""
    from tests.test_money_guard import make_event

    led = Ledger(tmp_path / "ui.db")
    decision = run_injected(make_event(), Config(), led)
    payload = _decision_payload(decision)
    led.close()

    assert payload["verdict"] == "BLOCKED"
    assert payload["invariant"] == "never_discount_identical_sku"
    assert payload["citation"] == "MM p.97"
    assert payload["counterfactual"]
    assert payload["ledger_id"] > 0


def test_payload_marks_which_levers_were_filtered(tmp_path):
    """The struck-through rows and their reasons are what make stage [2] visible."""
    from tests.test_money_guard import make_event

    led = Ledger(tmp_path / "ui2.db")
    decision = run_injected(make_event(qualified=True), Config(), led)
    payload = _decision_payload(decision)
    led.close()

    filtered = [lv for lv in payload["levers"] if not lv["eligible"]]
    assert filtered, "expected some levers filtered for a qualified buyer"
    assert all(lv["filtered_reason"] for lv in filtered), "every filtered row needs its reason"


def test_payload_exposes_proposal_provenance(tmp_path):
    """The UI must show whether the model or the fallback answered — a silent fallback
    hid a dead provider during this build."""
    from tests.test_money_guard import make_event

    led = Ledger(tmp_path / "ui3.db")
    payload = _decision_payload(run_injected(make_event(), Config(), led))
    led.close()
    assert payload["proposal"]["source"] in {"ai", "fallback"}
    assert payload["notes"]


# ------------------------------------------------------ gateway reconciliation


def _row(**kw):
    from decimal import Decimal

    from uplift.models import LedgerEntry

    base = dict(
        id=1, prev_id=None, event_id="evt_1", order_id="order_1", action="EXECUTED",
        lever="cross_sell", sku_code="CREATINE", amount=Decimal("899"), verdict="APPROVED",
        invariant=None, citation=None, source="ai", reference="order_GW1",
        created_at="2026-09-05T00:00:00+00:00",
    )
    base.update(kw)
    return LedgerEntry(**base)


def test_verify_order_reconciles_a_matching_order():
    from uplift.reconcile import reconcile

    gateway = {
        "id": "order_GW1", "amount": 89900, "status": "created",
        "notes": {"lever": "cross_sell", "sku": "CREATINE"},
    }
    result = reconcile("order_1", [_row()], fetch=lambda oid: gateway)
    assert result.ok, result.problems


def test_verify_order_detects_mismatch():
    """The verifier must be able to fail, or it is decoration.

    Three independent disagreements, each of which should be caught on its own.
    """
    from uplift.reconcile import reconcile

    wrong_amount = {"id": "order_GW1", "amount": 12345, "notes": {"lever": "cross_sell", "sku": "CREATINE"}}
    r = reconcile("order_1", [_row()], fetch=lambda oid: wrong_amount)
    assert not r.ok and any("amount mismatch" in p for p in r.problems)

    wrong_lever = {"id": "order_GW1", "amount": 89900, "notes": {"lever": "anchor_upsell", "sku": "CREATINE"}}
    r = reconcile("order_1", [_row()], fetch=lambda oid: wrong_lever)
    assert not r.ok and any("lever mismatch" in p for p in r.problems)

    wrong_sku = {"id": "order_GW1", "amount": 89900, "notes": {"lever": "cross_sell", "sku": "SHAKER"}}
    r = reconcile("order_1", [_row()], fetch=lambda oid: wrong_sku)
    assert not r.ok and any("sku mismatch" in p for p in r.problems)


def test_verify_order_reports_a_missing_order_rather_than_passing():
    from uplift.reconcile import reconcile

    def boom(order_id):
        raise RuntimeError("404 not found")

    r = reconcile("order_1", [_row()], fetch=boom)
    assert not r.ok
    assert any("gateway fetch failed" in p for p in r.problems)


def test_verify_order_requires_an_executed_row():
    from uplift.reconcile import reconcile

    r = reconcile("order_1", [_row(action="NO_OFFER")], fetch=lambda oid: {})
    assert not r.ok
    assert any("no EXECUTED ledger row" in p for p in r.problems)
