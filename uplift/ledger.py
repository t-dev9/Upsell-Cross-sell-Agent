"""[6] Append-only audit log.

NOT TAMPER-EVIDENT. Each row carries a `prev_id` pointer to the row before it, and that
pointer is for ORDERING ONLY — it is a sequential integer reference, not a checksum of
prior content. Editing a past row's values leaves neighbouring pointers untouched and
undetected. There is no cryptographic hash chain, by deliberate decision, and
`uplift audit verify` therefore checks sequence and append-only-ness, never integrity.

Nothing in this module should ever be described as proving a record was not altered.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import GuardResult, LedgerEntry, OrderEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_id     INTEGER,
    event_id    TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    customer_id TEXT,
    action      TEXT NOT NULL,
    lever       TEXT,
    sku_code    TEXT,
    amount      TEXT,
    verdict     TEXT NOT NULL,
    invariant   TEXT,
    citation    TEXT,
    source      TEXT,
    reference   TEXT,
    created_at  TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = "ledger.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns absent from ledgers created by an earlier build.

        An append-only log is worthless if upgrading the code orphans the history, so
        widen in place rather than asking anyone to delete their audit trail.
        """
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(ledger)")}
        for column, ddl in (("reference", "TEXT"), ("customer_id", "TEXT")):
            if column not in have:
                self._conn.execute(f"ALTER TABLE ledger ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self._conn.close()

    def _last_id(self) -> int | None:
        row = self._conn.execute("SELECT MAX(id) AS m FROM ledger").fetchone()
        return row["m"] if row and row["m"] is not None else None

    def record(
        self,
        event: OrderEvent,
        result: GuardResult,
        action: str,
        reference: str | None = None,
    ) -> int:
        """Append one row. Returns its id — the number the CLI prints on a block."""
        proposal = result.proposal
        cand = proposal.candidate if proposal else None
        amount = str(cand.offer_price) if cand else None
        cur = self._conn.execute(
            """INSERT INTO ledger
               (prev_id, event_id, order_id, customer_id, action, lever, sku_code, amount,
                verdict, invariant, citation, source, reference, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self._last_id(),
                event.event_id,
                event.order_id,
                event.customer.id,
                action,
                cand.lever.value if cand else None,
                cand.sku_code if cand else None,
                amount,
                result.verdict.value,
                result.invariant,
                result.citation,
                proposal.source if proposal else None,
                reference,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def find_by_event_id(self, event_id: str) -> LedgerEntry | None:
        """The first recorded action for this event, if any.

        Backs the idempotency invariant: a replayed webhook returns what was already
        recorded instead of creating a second money action.
        """
        row = self._conn.execute(
            "SELECT * FROM ledger WHERE event_id = ? ORDER BY id LIMIT 1", (event_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    @staticmethod
    def _row_to_entry(r: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            id=r["id"],
            prev_id=r["prev_id"],
            event_id=r["event_id"],
            order_id=r["order_id"],
            action=r["action"],
            lever=r["lever"],
            sku_code=r["sku_code"],
            amount=Decimal(r["amount"]) if r["amount"] is not None else None,
            verdict=r["verdict"],
            invariant=r["invariant"],
            citation=r["citation"],
            source=r["source"],
            reference=r["reference"] if "reference" in r.keys() else None,
            created_at=r["created_at"],
        )

    def discount_spend_today(self) -> Decimal:
        """Total discount already given away today across EXECUTED offers.

        Backs the daily_budget invariant. Counts executed actions only — a blocked
        proposal cost nothing, so counting it would let a run of blocked attempts
        exhaust the budget for offers that were never made.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self._conn.execute(
            "SELECT sku_code, amount FROM ledger "
            "WHERE action = 'EXECUTED' AND created_at LIKE ?",
            (f"{today}%",),
        ).fetchall()

        from . import catalog

        total = Decimal(0)
        for r in rows:
            if not r["sku_code"] or r["amount"] is None:
                continue
            try:
                sku = catalog.get(r["sku_code"])
            except KeyError:
                continue
            given = sku.list_price - Decimal(r["amount"])
            if given > 0:
                total += given
        return total

    def has_accepted_anchor_or_upsell(self, customer_id: str) -> bool:
        """Did this customer ever accept an anchor or upsell offer?

        Backs continuity_never_standalone (MM p.146). Matched on customer_id equality —
        a row written before this column existed has customer_id NULL and simply never
        matches, which is the correct "no history" answer for it.
        """
        rows = self._conn.execute(
            "SELECT lever FROM ledger WHERE action = 'EXECUTED' AND customer_id = ?",
            (customer_id,),
        ).fetchall()
        return any(
            r["lever"] and (r["lever"].startswith("upsell") or r["lever"] == "anchor_upsell")
            for r in rows
        )

    def offers_shown_since(self, customer_id: str, since_iso: str) -> int:
        """How many offers this customer has been shown since a timestamp.

        Backs fatigue_cap. Counts EXECUTED and PENDING_APPROVAL: both put an offer in
        front of the customer. Blocked proposals never reached them, so they do not fatigue.
        Matched on customer_id equality, not on any substring of the order id.
        """
        rows = self._conn.execute(
            "SELECT COUNT(*) AS n FROM ledger "
            "WHERE customer_id = ? AND created_at >= ? "
            "AND action IN ('EXECUTED','PENDING_APPROVAL')",
            (customer_id, since_iso),
        ).fetchone()
        return int(rows["n"] or 0)

    def outcome_rates(self, lever: str, since_iso: str) -> tuple[float, float, int]:
        """(cancellation rate, refund rate, sample size) for one offer type.

        Backs cancellation_stop_conditions. Computed from real CANCELLED / REFUNDED rows
        written by `uplift cancel` — not from a threshold sitting unused in config. If
        nothing has been recorded the sample is 0 and the monitor abstains rather than
        inventing a rate from no data.
        """
        rows = self._conn.execute(
            "SELECT action, COUNT(*) AS n FROM ledger "
            "WHERE lever = ? AND created_at >= ? GROUP BY action",
            (lever, since_iso),
        ).fetchall()
        counts = {r["action"]: int(r["n"]) for r in rows}
        executed = counts.get("EXECUTED", 0)
        if executed == 0:
            return 0.0, 0.0, 0
        cancelled = counts.get("CANCELLED", 0)
        refunded = counts.get("REFUNDED", 0)
        return cancelled / executed, refunded / executed, executed

    def record_outcome(self, order_id: str, action: str) -> int:
        """Append a CANCELLED or REFUNDED row against an existing executed order.

        Append-only: the original EXECUTED row is never edited, so the history shows both
        that the offer happened and that it later went bad.
        """
        prior = self._conn.execute(
            "SELECT * FROM ledger WHERE order_id = ? AND action = 'EXECUTED' ORDER BY id DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if prior is None:
            raise KeyError(f"no executed order {order_id!r} to {action.lower()}")
        prior_customer_id = prior["customer_id"] if "customer_id" in prior.keys() else None
        cur = self._conn.execute(
            """INSERT INTO ledger
               (prev_id, event_id, order_id, customer_id, action, lever, sku_code, amount,
                verdict, invariant, citation, source, reference, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self._last_id(), prior["event_id"], order_id, prior_customer_id, action,
                prior["lever"], prior["sku_code"], prior["amount"], prior["verdict"], None, None,
                prior["source"], prior["reference"],
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def entries(self) -> list[LedgerEntry]:
        rows = self._conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
        return [self._row_to_entry(r) for r in rows]

    def verify_sequence(self) -> tuple[bool, list[str]]:
        """Check ids are contiguous and each prev_id points at the row before it.

        This proves ORDERING and that no row was deleted from the middle. It does not
        and cannot prove a row's values were never edited — see the module docstring.
        """
        problems: list[str] = []
        expected_prev: int | None = None
        expected_id = 1
        for entry in self.entries():
            if entry.id != expected_id:
                problems.append(f"id gap: expected {expected_id}, found {entry.id}")
            if entry.prev_id != expected_prev:
                problems.append(
                    f"row {entry.id}: prev_id {entry.prev_id}, expected {expected_prev}"
                )
            expected_prev = entry.id
            expected_id = entry.id + 1
        return (not problems), problems
