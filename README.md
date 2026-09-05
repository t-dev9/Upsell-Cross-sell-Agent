# uplift — the agent that cannot give your money away

An upsell & cross-sell agent for Razorpay merchants that is **structurally incapable of
emitting a discount it wasn't allowed to emit.**

Razorpay AI Buildathon · Track 01 (AI Growth & Agentic Commerce) · test mode only

---

![A prompt-injected product title is blocked by Money Guard](docs/blocked.svg)

```
BLOCKED — never_discount_identical_sku (MM p.97) · order completed with no offer · ledger #0002
```

That line is the whole project. A prompt-injected product title talks a language model into
proposing the identical SKU at 60% off. The model does exactly what the attacker asked. The
offer still never reaches the payment API — because the model's output is a *proposal*, and
proposals go through one deterministic door.

**30/30 adversarial inputs blocked before execution.** Run `uplift redteam` and count them
yourself.

## 60-second quickstart — no API key needed

```bash
git clone <this repo> && cd uplift

python -m venv .venv
.venv\Scripts\activate        # Windows;  source .venv/bin/activate  on macOS/Linux
pip install -e .

uplift demo                    # one order, end to end
uplift demo --inject-discount  # <- the prompt injection, blocked
uplift redteam                 # 30/30 blocked, by invariant
uplift eval --n 100            # policy sweep, real vs simulated
uplift money-model             # every invariant -> its source -> its test
uplift demo --replay           # re-send the same event -> idempotency blocks it
uplift serve                   # <- visual audit trail at localhost:8000
uplift verify-order <id>       # reconcile a ledger row against the live gateway
uplift audit verify            # the append-only ledger
pytest                         # 70 tests
```

<details>
<summary>Prefer <code>uv</code>? <code>uv sync</code> then prefix each command with <code>uv run</code>.</summary>

```bash
pip install uv
uv sync
uv run uplift demo
```

The plain-venv path above is listed first deliberately: it needs nothing beyond the
Python you already have, so a reviewer never has to install a tool to read this project.
</details>

With no credentials, stage [3] uses a recorded fixture and payments use a mock adapter —
everything above runs. Add `GROQ_API_KEY` to `.env` (free tier) and stage [3] calls a real
model; add `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` and execution hits Razorpay test mode.

**`--inject-discount` never touches the network** — it replays a recorded jailbreak, so the
demonstration this project rests on cannot be broken by someone else's API being down.

## How it cannot discount

```
order.paid event
  [1] LEVER ENUMERATION            deterministic   walk all six levers + market basket
  [2] ELIGIBILITY + QUALIFICATION  deterministic   → THE candidate set
  [3] SELECT · SEQUENCE · PITCH    LLM + deterministic fallback
  [4] MONEY GUARD                  deterministic   ← the only door to money
  [5] EXECUTE                      Razorpay test mode — unreachable except via [4]
  [6] LEDGER (append-only)         [7] MEASURE
```

Three properties do the work:

1. **The model touches the money path at exactly one stage.** Enumeration and eligibility are
   deterministic and run *before* any model call, so the model never sees an offer the
   business rules didn't already permit. It picks from a list; it cannot invent.
2. **Money Guard re-derives every claim.** Prices, costs, margins and buyer qualification are
   looked up again from the catalog and recomputed. Nothing carried on a proposal is trusted.
3. **Provenance is irrelevant.** Money Guard never reads whether a proposal came from the
   model or the fallback — there is no branch on it, and a test parses the source to prove it.
   A "safe" fallback pick gets exactly the scrutiny a jailbroken model's pick gets.

JSON validity determines only whether a proposal is *parseable*. It is not what makes an
action *safe*.

## The fifteen rules

Each has a config key, a source citation, and a test named after it. `uplift money-model`
prints the table and **exits non-zero if any invariant lacks its test.** Four Tier 2 rules remain
unbuilt and are printed under a heading saying they are not enforced.

| Invariant | Source | What Money Guard checks |
|---|---|---|
| `kill_switch` | ours | One boolean rejects everything, whatever else passes |
| `idempotency` | ours | A replayed `event_id` returns the recorded entry; no second money action |
| `not_in_eligible_set` | ours | The proposal must come from stage [2]'s set |
| `never_discount_identical_sku` | MM p.97-98 | Same SKU, same quantity, same terms, lower price → rejected |
| `never_downsell_qualified_buyer` | LTV p.19 | Downsells are for unqualified prospects only |
| `anchor_price_multiple_min` | MM p.84-88 | An Anchor Upsell below 5× the baseline is not an anchor |
| `margin_floor` | ours | Post-offer gross margin must clear the floor |
| `discount_ceiling` | ours | Effective discount off list must not exceed the ceiling |
| `daily_budget` | ours | Today's cumulative give-away across executed offers must stay under the cap |
| `rollover_credit` | MM p.92 | Credit ≤ 25% of the anchor, and the offer it unlocks ≥ 4× the credit |
| `continuity_never_standalone` | MM p.146 | Continuity is never the front end — it needs a prior anchor or upsell |
| `sequence_largest_first` | LTV p.15,17 | The larger variant on the same axis must be offered first (quantity/quality only) |
| `fatigue_cap` | ours | Stop after N offers shown to one customer in the window |
| `cancellation_stop_conditions` | MM p.59,144,35 | Stop an offer type whose real cancellation or refund rate has gone bad |
| `auto_approve` | ours | At/above the ₹ threshold: `PENDING_APPROVAL`, which cannot execute |

The action space is Crazy Eight levers 3–8 — a closed set from a published monetization
framework, so the model picks from an enumerated list instead of inventing an offer.

**Legend:** **LTV** = *$100M Lifetime Value* (Hormozi, 2025) · **MM** = *$100M Money Models* ·
**Crazy Eight** = LTV's eight-lever menu. Page numbers are printed pages.

## What is real and what is simulated

This project deliberately claims **no revenue uplift number**, and the reason is in
[ARCHITECTURE.md](ARCHITECTURE.md#eval).

**Real, measured:** 30/30 adversarial inputs blocked by invariant · 6 deterministic stages to
1 LLM stage · p95 latency · ₹0.00 per decision · 0 unhandled exceptions · every fallback logged ·
offered gross profit and 30-day billed GP, computed from executed ledger rows against catalog costs.

**Deliberately absent: any conversion rate or realized revenue.** Orders are *created* in Razorpay
test mode and never captured, so no offer is ever accepted and acceptance cannot be observed.
`RealMetrics` has no field for it, and a test enforces that — `added LTGP = conversion × GP`
(LTV p.14) is exactly the number this project will not invent.

**Simulated, assumed:** every added-LTGP figure and policy ranking in `uplift eval`. Those come
from parameters we chose, not from observed customers. Their only payload is that the winning
policy **flips** — `frequency-first` under price-sensitive assumptions, `anchor-first` under
relationship-driven ones. Which policy wins depends on an assumption nobody can verify without
real conversion data, which is precisely why the guarantees are about containment instead.

## Limitations

- **No real conversion data.** Nothing here measures customer behaviour; the acceptance model
  is assumed, and labelled as such at every appearance.
- **Single merchant, single synthetic catalog** (D2C supplements, 10 SKUs).
- **Offers must be pre-created**, so the action space is bounded by dashboard config.
- **The ledger is append-only but NOT tamper-evident.** `prev_id` is a sequential pointer for
  ordering, not a checksum of prior content. Editing a past row goes undetected. There is no
  hash chain, by deliberate decision, and `audit verify` checks ordering only.
- **Executed offers show as `EXECUTED`, but nobody accepted them.** A Razorpay order is created;
  no customer ever pays it. Every "offered GP" figure is conditional on an acceptance that did
  not happen.
- **No real payment capture**, no multi-merchant, no auth. `uplift serve` is a local demo UI
  (stdlib `http.server`, one HTML file, no CDN), not a production frontend.
- **Razorpay test mode is live.** With keys set, stage [5] creates real test-mode orders and the
  ledger records the gateway id, so an audit row reconciles against Razorpay. Orders are
  *created*, never captured — no payment is collected, which remains out of scope.
- **There is no dashboard screenshot, deliberately.** `uplift verify-order <order_id>` fetches the
  order from Razorpay and reconciles it against the ledger row on amount, lever and SKU
  ([docs/verified-order.svg](docs/verified-order.svg)). That is stronger evidence than an image: a
  judge can re-run it, and it **exits non-zero on a mismatch** — a screenshot can neither be
  re-verified nor fail.
- **Three known, unfixed gaps** — `daily_budget` under-counts multi-quantity offers,
  `downsell_quality` can never actually reach a customer (Money Guard correctly blocks it every
  time, as a raw discount), and a race on the ledger's `prev_id` is possible under concurrent
  requests. None of the three lets an unsafe action through; details and why they're unfixed are in
  [ARCHITECTURE.md](ARCHITECTURE.md#failures).

## Build log

[ARCHITECTURE.md](ARCHITECTURE.md#failures) carries a timestamped entry for every real bug hit
during this build, written as it happened — including a false positive in the highest-stakes
invariant, and a predicted headline result that turned out to be arithmetically impossible.
