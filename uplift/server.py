"""Local demo UI. Standard library only — no FastAPI, no build step, no CDN.

This is not the cut `server.py`: that cut was a webhook receiver plus an approval queue,
i.e. a production ingestion path. This serves one page on localhost so the audit trail
can be read visually instead of in scrollback. It adds no runtime dependency.

Routes:
    GET  /              the page
    GET  /api/catalog   the full product catalog, for the inventory grid
    POST /api/decide    runs the real pipeline; body {sku_code, qualified, inject, replay}
    GET  /api/redteam   the 30-case scoreboard
    GET  /api/ledger    the append-only log
"""

from __future__ import annotations

import json
import uuid
import webbrowser
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import catalog
from .config import load_config
from .ledger import Ledger
from .models import Customer, OrderEvent, Verdict
from .pipeline import INJECTED_TITLE, Decision, run, run_injected
from .razorpay_adapter import build_adapter

_PAGE = Path(__file__).parent / "ui.html"


def _json_default(o: object) -> object:
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"not serialisable: {type(o)}")


def _build_event(sku_code: str, *, qualified: bool, event_id: str | None = None) -> OrderEvent:
    """Parameterized version of cli.py's `_demo_event` — any catalog SKU, not just WHEY_2KG.

    Same customer/paid-ratio logic as the CLI demo: a qualified buyer has already accepted
    an upsell before and pays full price; an unqualified one has no history and pays 70%
    of list, which is what keeps the downsell levers eligible (eligibility.py).
    """
    sku = catalog.get(sku_code)  # raises KeyError on a bad code — caller turns that into a 400
    if qualified:
        customer = Customer(
            id="cust_1042",
            past_order_skus=("WHEY_1KG",),
            total_spend=Decimal("1999"),
            accepted_upsell_before=True,
        )
        paid = sku.list_price
    else:
        customer = Customer(id="cust_2071", past_order_skus=(), total_spend=Decimal("0"))
        paid = sku.list_price * Decimal("0.7")
    eid = event_id or f"evt_demo_{uuid.uuid4().hex[:8]}"
    return OrderEvent(
        event_id=eid,
        order_id=f"order_{eid.removeprefix('evt_')}",
        customer=customer,
        sku_code=sku.code,
        quantity=1,
        amount_paid=paid,
    )


def _decision_payload(d: Decision) -> dict:
    eligible_keys = {c.key for c in d.eligible.candidates}
    reasons = {r.candidate.key: r.reason for r in d.eligible.rejected}
    sku = catalog.get(d.event.sku_code)

    return {
        "event": {
            "event_id": d.event.event_id,
            "order_id": d.event.order_id,
            "customer": d.event.customer.id,
            "past_order_skus": list(d.event.customer.past_order_skus),
            "accepted_upsell_before": d.event.customer.accepted_upsell_before,
            "sku": sku.name,
            "sku_code": sku.code,
            "amount_paid": str(d.event.amount_paid),
        },
        "levers": [
            {
                "lever": c.lever.value,
                "sku": c.sku_code,
                "qty": c.quantity,
                "price": str(c.offer_price),
                "rationale": c.rationale,
                "eligible": c.key in eligible_keys,
                "filtered_reason": reasons.get(c.key),
            }
            for c in d.enumerated
        ],
        "eligible_count": len(d.eligible.candidates),
        "buyer_qualified": d.eligible.buyer_qualified,
        "proposal": {
            "lever": d.proposal.candidate.lever.value,
            "sku": d.proposal.candidate.sku_code,
            "price": str(d.proposal.candidate.offer_price),
            "pitch": d.proposal.pitch,
            "source": d.proposal.source,
            "repaired": d.proposal.repaired,
        },
        "notes": d.notes,
        "verdict": d.result.verdict.value,
        "invariant": d.result.invariant,
        "citation": d.result.citation,
        "counterfactual": d.result.counterfactual,
        "ledger_id": d.ledger_id,
        "receipt": d.receipt.reference if d.receipt else None,
        "gateway": d.receipt.mode if d.receipt else None,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "uplift"

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # keep the terminal clean; the page is the output

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/catalog":
            self._json(
                {
                    "skus": [
                        {
                            "code": s.code,
                            "name": s.name,
                            "price": s.list_price,
                            "features": list(s.features),
                            "consumable": s.is_consumable,
                        }
                        for s in catalog.all_skus()
                    ]
                }
            )
        elif self.path == "/api/redteam":
            from .eval.redteam import run_cases, summary

            results = run_cases(load_config())
            self._json(
                {
                    "total": len(results),
                    "blocked": sum(1 for r in results if r.blocked),
                    "matched": sum(1 for r in results if r.matched_expectation),
                    "by_invariant": summary(results),
                    "cases": [
                        {
                            "name": r.case.name,
                            "category": r.case.category,
                            "source": r.case.proposal.source,
                            "blocked": r.blocked,
                            "invariant": r.fired,
                            "citation": r.citation,
                            "expected": r.case.expect,
                            "matched": r.matched_expectation,
                        }
                        for r in results
                    ],
                }
            )
        elif self.path == "/api/ledger":
            config = load_config()
            led = Ledger(config.ledger_path)
            ok, problems = led.verify_sequence()
            entries = [
                {
                    "id": e.id,
                    "prev_id": e.prev_id,
                    "order_id": e.order_id,
                    "action": e.action,
                    "verdict": e.verdict,
                    "invariant": e.invariant,
                    "source": e.source,
                    "reference": e.reference,
                    "amount": str(e.amount) if e.amount is not None else None,
                    "created_at": e.created_at,
                }
                for e in led.entries()
            ]
            led.close()
            self._json({"sequential": ok, "problems": problems, "entries": entries})
        elif self.path == "/api/mode":
            adapter = build_adapter()
            config = load_config()
            self._json({"adapter": adapter.name, "llm": config.llm_provider})
        elif self.path == "/api/injected-title":
            self._json({"title": INJECTED_TITLE})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/decide":
            self._json({"error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        config = load_config()
        led = Ledger(config.ledger_path)
        adapter = build_adapter()

        try:
            reuse = None
            if body.get("replay"):
                prior = led.entries()
                if not prior:
                    self._json({"error": "ledger is empty — run a decision first"}, 400)
                    return
                reuse = prior[-1].event_id

            try:
                event = _build_event(
                    body.get("sku_code", "WHEY_2KG"),
                    qualified=bool(body.get("qualified", True)),
                    event_id=reuse,
                )
            except KeyError as exc:
                self._json({"error": str(exc)}, 400)
                return

            if body.get("inject"):
                decision = run_injected(event, config, led)
            else:
                decision = run(event, config, led, adapter)

            payload = _decision_payload(decision)
            payload["injected_title"] = INJECTED_TITLE if body.get("inject") else None
            self._json(payload)
        except Exception as exc:  # noqa: BLE001 — surface it in the UI, never 500 silently
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            led.close()


def serve(port: int = 8000, open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"uplift ui  {url}   (ctrl-c to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
