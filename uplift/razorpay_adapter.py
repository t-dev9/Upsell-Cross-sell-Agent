"""[5] EXECUTE — unreachable except through stage [4].

execute() takes a GuardResult, not a Proposal. A caller cannot reach this module holding
only something the model suggested; they must be holding a verdict Money Guard produced.
And execute() re-checks that verdict before doing anything, so even a hand-constructed
BLOCKED result cannot be pushed through. The type signature carries the gate, and the
runtime check backs it up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from .models import GuardResult


class ExecutionRefused(RuntimeError):
    """Raised when something tries to execute without an APPROVED GuardResult."""


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    reference: str
    amount: str
    sku_code: str
    mode: str


class PaymentAdapter(Protocol):
    name: str

    def execute(self, result: GuardResult, order_id: str) -> ExecutionReceipt: ...


def _require_approved(result: GuardResult) -> None:
    """The runtime half of the gate. PENDING_APPROVAL is refused exactly like BLOCKED.

    Checked against `approved` rather than by listing forbidden verdicts, so a verdict
    added later is refused by default instead of silently becoming executable.
    """
    if not result.approved or result.proposal is None:
        raise ExecutionRefused(
            f"execution requires an APPROVED GuardResult, got {result.verdict.value}"
        )


@dataclass
class MockAdapter:
    """Used whenever Razorpay test-mode credentials are absent — including a fresh clone."""

    name: str = "mock"
    receipts: list[ExecutionReceipt] = field(default_factory=list)

    def execute(self, result: GuardResult, order_id: str) -> ExecutionReceipt:
        _require_approved(result)
        assert result.proposal is not None
        cand = result.proposal.candidate
        receipt = ExecutionReceipt(
            reference=f"mock_{order_id}_{len(self.receipts) + 1}",
            amount=str(cand.offer_price),
            sku_code=cand.sku_code,
            mode="mock",
        )
        self.receipts.append(receipt)
        return receipt


class LiveAdapter:
    """Razorpay TEST MODE only. Wired when RAZORPAY_KEY_ID / _SECRET are present.

    Creates a test-mode order for the offer amount. Never captures a real payment —
    real payment capture is explicitly out of scope.
    """

    name = "razorpay-test"

    def __init__(self) -> None:
        self.key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not (self.key_id and self.key_secret):
            raise RuntimeError("Razorpay test-mode credentials not set")

    def execute(self, result: GuardResult, order_id: str) -> ExecutionReceipt:
        _require_approved(result)
        assert result.proposal is not None
        import httpx

        cand = result.proposal.candidate
        paise = int(cand.offer_price * 100)
        resp = httpx.post(
            "https://api.razorpay.com/v1/orders",
            auth=(self.key_id, self.key_secret),
            json={
                "amount": paise,
                "currency": "INR",
                "receipt": f"uplift_{order_id}",
                "notes": {"lever": cand.lever.value, "sku": cand.sku_code},
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        return ExecutionReceipt(
            reference=resp.json()["id"],
            amount=str(cand.offer_price),
            sku_code=cand.sku_code,
            mode="razorpay-test",
        )


def build_adapter() -> PaymentAdapter:
    """Live test-mode adapter when credentials exist, mock otherwise."""
    if os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"):
        try:
            return LiveAdapter()
        except RuntimeError:
            pass
    return MockAdapter()
