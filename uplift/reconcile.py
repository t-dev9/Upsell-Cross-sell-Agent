"""Reconcile a ledger row against the live payment gateway.

An audit trail nobody can check against the gateway is a log, not an audit trail. This
fetches the order Razorpay actually holds and compares it to what we recorded.

Fetching is injectable so the comparison logic is testable without a network, and so the
same code path serves the live command and the test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Protocol

from .models import LedgerEntry

RAZORPAY_ORDER_URL = "https://api.razorpay.com/v1/orders/{order_id}"


class OrderFetcher(Protocol):
    def __call__(self, order_id: str) -> dict: ...


@dataclass(frozen=True, slots=True)
class Reconciliation:
    order_id: str
    ledger: LedgerEntry | None
    gateway: dict | None
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems and self.ledger is not None and self.gateway is not None


def live_fetcher(order_id: str) -> dict:
    """Fetch one order by id.

    BY ID, never by listing: `GET /v1/orders` returns count 0 for test orders with no
    payment attempts even when they exist (ARCHITECTURE.md FAILURES, 2026-09-05 12:18).
    """
    import httpx

    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not (key_id and key_secret):
        raise RuntimeError("Razorpay credentials not set")

    resp = httpx.get(
        RAZORPAY_ORDER_URL.format(order_id=order_id), auth=(key_id, key_secret), timeout=20.0
    )
    resp.raise_for_status()
    return resp.json()


def reconcile(
    order_id: str,
    entries: list[LedgerEntry],
    fetch: Callable[[str], dict] | None = None,
) -> Reconciliation:
    """Compare what we recorded against what the gateway holds.

    This must be able to FAIL. A verifier that always reports success is decoration, so
    every disagreement below is collected rather than smoothed over.
    """
    fetch = fetch or live_fetcher

    executed = [e for e in entries if e.order_id == order_id and e.action == "EXECUTED"]
    ledger = executed[-1] if executed else None
    problems: list[str] = []

    if ledger is None:
        return Reconciliation(order_id, None, None, ("no EXECUTED ledger row for this order",))
    if not ledger.reference:
        problems.append("ledger row has no gateway reference — nothing to reconcile against")

    try:
        gateway = fetch(ledger.reference or order_id)
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed into a pass
        return Reconciliation(order_id, ledger, None, (f"gateway fetch failed: {exc}",))

    # Amount: gateway holds paise, the ledger holds rupees.
    gw_amount = Decimal(str(gateway.get("amount", 0))) / 100
    if ledger.amount is None or gw_amount != ledger.amount:
        problems.append(f"amount mismatch: ledger INR {ledger.amount}, gateway INR {gw_amount}")

    # Notes: the lever and SKU we stamped at execution must still be on the order.
    notes = gateway.get("notes") or {}
    if ledger.lever and notes.get("lever") != ledger.lever:
        problems.append(f"lever mismatch: ledger {ledger.lever!r}, gateway {notes.get('lever')!r}")
    if ledger.sku_code and notes.get("sku") != ledger.sku_code:
        problems.append(f"sku mismatch: ledger {ledger.sku_code!r}, gateway {notes.get('sku')!r}")

    return Reconciliation(order_id, ledger, gateway, tuple(problems))
