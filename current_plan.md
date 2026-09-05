# The Agent That Cannot Give Your Money Away

**An upsell & cross-sell agent that is structurally incapable of discounting.**
Razorpay AI Buildathon — Track 01 (AI Growth & Agentic Commerce)

## Context

Tarun is submitting to the Razorpay AI Buildathon, a hiring program for AI Builder Interns
(₹75k/month, Bangalore, in-person). Deliverables: public GitHub repo and architecture write-up —
plus a pitch video, which is handled outside this document. **This document covers the build only.**
Shortlisted builders go straight to a panel.

Track 01, upsell & cross-sell agent. The **business logic is Alex Hormozi's** (`$100M Lifetime
Value`, `$100M Money Models`); the architecture, code and engineering are ours.

### The core claim — one sentence

> Every other upsell agent in this track can emit an arbitrary discount. This one cannot.

That is the product thesis and the Track 01 bar simultaneously. Everything below serves it.

### What the judges score

Problem taste · build quality · **AI judgment** (deterministic where AI is unnecessary) ·
**failure recovery** (show what broke and how you fixed it). Track 01's bar: *"every money action
must be explainable, bounded and gated. Show the audit trail. Show one failure handled gracefully."*

*Criteria come from third-party coverage, not Razorpay's own page — verify before relying on them.*

---

## The decision that shapes everything: prove containment, not uplift

**We will not claim a revenue uplift number.** Not in the README, not in ARCHITECTURE.md, not in
CLI output, not in a chart title.

Any uplift figure would come from an acceptance simulator we wrote ourselves — so the agent would
be graded against assumptions we chose. Worse, whether "the LLM beats market-basket" is decided
entirely by how `generate.py` encodes complementarity, not by the agent. That experiment is
circular, a payments engineer sees it immediately, and once they do, the guardrail numbers — which
are *real* — get discarded alongside the fake ones. A disclaimer does not repair a circular
experiment; it documents that we knew and shipped anyway.

So the headline evidence is **containment**, which we can measure honestly:

| Evidence | Real or simulated |
|---|---|
| N/30 adversarial inputs blocked, by invariant | **Real** |
| Deterministic vs LLM decision split | **Real** |
| Actions proposed → blocked → executed, by invariant | **Real** |
| p95 latency, ₹ cost per decision | **Real** |
| 0 unhandled exceptions; every fallback logged | **Real** |
| Which policy "wins" on added LTGP | *Simulated — assumed parameters, not measured customer behavior; shown only as a sensitivity sweep* |

The simulator survives as **one chart showing the ranking flip** across three named, **explicitly
simulated** assumption sets — this is not a measurement of real customer behavior and not a claim
about what this or any actual merchant's customers actually do — defined now rather than
improvised later, on two parameters: **price-sensitivity** (elasticity of take-rate to the size of
the ask) and **complementarity weight** (how much cross-sell lift the market-basket step assigns to
a candidate pair):

| Set | Price-sensitivity | Complementarity weight | Simulated #1 policy (under this assumption only) |
|---|---|---|---|
| **A — price-sensitive** | high (elasticity ≈ −1.5: small price increases sharply cut take-rate) | low (≈0.1) | Anchor Upsell (lever 5) — a cheap, single large commitment beats a bundle few will click |
| **B — baseline** | medium (≈−0.8, the corpus's fitted default) | medium (≈0.4) | Anchor Upsell, but by a narrower margin |
| **C — relationship-driven** | low (≈−0.3: buyers care less about price, more about fit) | high (≈0.8) | Cross-sell (lever 8) — the "next problem" pitch overtakes the anchor once complementarity is weighted heavily |

**The flip the sweep must actually produce: Anchor Upsell and Cross-sell swap rank #1 between Set A
and Set C.**
That swap, not any absolute LTGP number, is the chart's entire payload — it is a property of the
simulator's chosen inputs, not a finding about real customers or real conversion behavior. Confirm
this flip actually appears in the simulator's output (not that it reflects real-world behavior)
with an early spike in `simulate.py` — if the flip doesn't actually appear with these two
parameters, swap in a different pair (e.g., fatigue/decay rate instead of complementarity weight)
during that same spike, while there is still room to redesign it before the chart is finalized.

**Stated verbatim in ARCHITECTURE.md, next to the sweep chart:** *"Which policy wins depends on which
assumption you make, so this submission claims no uplift number. What it can prove: nothing illegal
reached the money path."*

---

## The business logic, as enforced code

### Action space — Crazy Eight levers 3–8

Levers 1–2 (raise price, cut delivery cost) are merchant-level, not per-transaction.

| # | Lever | Deterministic generator |
|---|---|---|
| 3 | Upsell frequency | subscribe-and-save; recurring version of a consumable |
| 4 | Upsell quantity | bulk (prepay) · more often · bigger |
| 5 | Upsell quality | premium tier via the quality-lever list |
| 6 | Downsell quantity | fewer units |
| 7 | Downsell quality | quality levers read backwards |
| 8 | Cross-sell | the product solving the customer's **next problem** |

Hormozi argues the systematic walk beats inspiration (LTV p.22) — that is the sourced justification
for why enumeration is code, not a model. Judging criterion #3, argued rather than asserted.

**Feature-downsell ordering** (MM p.115–121): remove highest-value features first, because
customers re-upsell themselves. Ten lines in `levers.py`, and a better demo than the anchor mechanic.

### Invariants — business rules that are also money guards

Enforced in `money_guard.py`. Each has a config key **and a test**. These tests are the primary
evidence for "bounded and gated." This is the highest-stakes component in the submission — the core
claim stands or falls on these firing correctly — so it is tiered up front, not decided ad hoc
during the build:

- **Tier 1 — must ship, each with a test; these are the invariants the core claim rests on:**
  never-discount-identical-SKU,
  never-downsell-qualified-buyer, `anchor_price_multiple_min`, margin floor, discount ceiling,
  kill switch.
- **Tier 2 — implement only after all Tier 1 invariants are done and tested, in this priority
  order, and only if there is remaining capacity:** daily budget (cheapest, do first) → rollover
  credit → continuity-never-standalone → `sequence_largest_first` → cancellation stop-conditions →
  fatigue cap → idempotency → auto-approve. If Tier 1 isn't finished, **all of Tier 2 is cut
  without exception** — do not partially implement one Tier 2 rule instead of finishing Tier 1
  cleanly.

Every row below states the actual check `money_guard.py` runs, not just the rule it's derived
from — per the MONEY_MODEL section rule further down: if a cited rule has no enforcing mechanism
and no test, it does not belong in this table.

| Invariant | Source | Tier | Enforcement (the actual check Money Guard runs) |
|---|---|---|---|
| **Never discount an identical SKU** — change how they pay or what they get, never the price for the same thing | MM p.97–98 | **1** | Money Guard rejects any proposal — AI or fallback — whose bundle equals the anchor SKU at a lower price. The agent cannot emit a raw discount |
| **Never downsell a qualified buyer** | LTV p.19 | **1** | Qualification gate mirrors `checklists.md`'s downsell gate line-for-line in `eligibility.py`; Money Guard independently re-checks buyer qualification before allowing any downsell lever through, regardless of what the AI or fallback proposed |
| `sequence_largest_first` | LTV p.15,17 | 2 | Money Guard rejects a proposed offer if a larger/higher-tier variant on the same lever axis was present in the eligible set and wasn't offered first |
| `anchor_price_multiple_min: 5` | MM p.84–88 | **1** | *Separate rule.* Money Guard rejects any Anchor Upsell proposal priced below 5× the anchor baseline. The Anchor Upsell is a distinct named offer at 5–10×. **Do not merge with the row above** — that was an error in a previous draft |
| Rollover credit ≤ 25% (next offer ≥ 4× credit) | MM p.92 | 2 | Money Guard rejects if `credit_amount > 0.25 × anchor_price`, or if the next offer's price `< 4 × credit_amount` |
| Continuity never standalone | MM p.146 | 2 | Money Guard rejects a Continuity-lever proposal for any customer whose order history shows no prior anchor or upsell lever ever accepted |
| Cancellation stop-conditions: >10% pay-later cancellation (p.59), >5% early cancellation under Waived Fee (p.144), refund rate <5% (p.35) | MM | 2 | A `cancellation_monitor` aggregates outcomes from the ledger/order history per offer-type on a rolling window, computes the three rates, and sets a per-offer-type stop-signal in runtime state. Money Guard checks that signal before allowing any further offer of that type and blocks exactly like any other invariant if tripped, with the same name/cite/counterfactual output. **If this monitor doesn't get built, this row is deleted from the shipped table entirely — a threshold sitting unused in `config.py` is not enforcement and must not be presented as one** |
| Margin floor | ours | **1** | Money Guard rejects if post-offer gross margin % on the SKU falls below the configured floor |
| Discount ceiling | ours | **1** | Money Guard rejects if the effective discount % (list price vs. offer price) exceeds the configured ceiling |
| Daily budget | ours | 2 (first in priority order) | Money Guard rejects if today's cumulative discount/credit spend across all executed offers would exceed the configured daily cap |
| Fatigue cap | ours | 2 | Money Guard rejects if this customer has already been shown ≥N offers (configured) in the trailing window |
| Idempotency | ours | 2 | The same `event_id`/`order_id` processed twice returns the already-recorded ledger action instead of re-executing; no duplicate money action is ever created |
| Auto-approve | ours | 2 | Actions below the configured ₹ threshold execute immediately; actions at/above it are written as `PENDING_APPROVAL` and never auto-execute |
| Kill switch | ours | **1** | A single config boolean that, when set, makes Money Guard reject every action regardless of any other rule passing |

**India regulatory note** (ARCHITECTURE.md, one line): trial-with-penalty and negative-option
billing are regulated differently in India than in the book's US context, so those levers ship
**disabled by default** in `config.py`. Costs almost nothing to add; nobody else in the track will
do it.

### Metrics reported

`added LTGP = conversion × upsell gross profit` (LTV p.14), **plus a 30-day gross profit per
acquired customer column** (MM p.156) — a payments company thinks in cash timing, and the model's
job is to move profit *earlier*. One extra column, most domain-native thing in the submission.

---

## Architecture

**Core architecture principle — true everywhere in this document:** AI recommends → eligibility
filters → AI/fallback selects → Money Guard independently verifies → only then can execution
happen. **It doesn't matter whether the decision came from the AI or the fallback — there is only
one door to the money action: Money Guard.**

```
order.paid event  /  cart state  /  customer history
        │
 [1] LEVER ENUMERATION        ── deterministic ──  walk all six levers;
     market-basket (support/confidence/lift) for cross-sell candidates
        │
 [2] ELIGIBILITY + QUALIFICATION GATE   ── deterministic ──  produces the one
     eligible/qualified candidate set that everything downstream must draw from
        │
 [3] SELECT · SEQUENCE · PITCH          ── LLM, with a deterministic fallback ──
     AI proposes from the eligible set ONLY, as strict structured output, with one
     repair attempt on invalid JSON. If the AI call fails, times out, or still can't
     produce valid JSON after repair, the fallback selects from that SAME
     eligible/qualified set produced by stage [2] — never an ungated candidate —
     and is treated by every later stage as an ordinary stage-3 output,
     indistinguishable from an AI pick. JSON validity only determines whether a
     proposal is parseable; it is NOT what makes an action safe.
        │
 [4] MONEY GUARD              ── deterministic chokepoint, the ONLY door to money ──
     Every path that could reach Execute — the original AI choice, the
     JSON-repaired AI choice, and the fallback choice — converges here first, with
     no exception handler and no shortcut branch that skips it. Money Guard is the
     only security boundary in this system: it independently re-derives every claim
     the proposal makes, regardless of whether the JSON was well-formed, whether it
     was repaired, or whether it came from the "safe" fallback path. It blocks,
     names the invariant, cites the source, and records the counterfactual.
        │
 [5] EXECUTE                  ── Razorpay test mode ──  unreachable except via [4]
        │
 [6] LEDGER (append-only log; not tamper-evident — no cryptographic chaining)     [7] MEASURE
```

**Load-bearing idea:** it doesn't matter whether the decision came from the AI or the fallback —
there is only one door to the money action: Money Guard. The model touches the money path at
exactly one stage (3), and stage 4 independently re-derives everything any stage-3 output claimed,
whichever path produced it. The LLM can be jailbroken, can produce malformed JSON, or can fail
outright and hand off to the fallback — none of that matters, because nothing reaches stage 5
without first clearing stage 4.

**CLI output is a foundational design decision, not late polish.** A rejected action must print:
the proposed action · the invariant name · its source citation · what it did instead. If it prints
`REJECTED: guard_violation`, the action is not explainable, and the submission fails Track 01's own
bar — *every money action must be explainable, bounded and gated* — at the one moment it most needs
to hold. The terminal is where the audit trail is actually read; a rejection that can't explain
itself is a guard that can't be audited.

---

## Red-team suite — proving the core claim

`redteam.py`'s 30 hand-written adversarial cases — a fixed, non-negotiable set — all exist to
prove exactly one thing: **even when the AI (or a corrupted upstream signal) proposes a dangerous
or policy-violating action, Money Guard blocks it before execution — and it does not matter which
path the proposal came from.** Every category is an instance of that same claim, not a
disconnected checklist:

- **Prompt injection in product titles** — the AI is manipulated into proposing an arbitrary
  discount (e.g., "60% off"); blocked by never-discount-identical-SKU / discount ceiling, the same
  way any other over-limit proposal would be, whether it came from a jailbroken model or a buggy
  fallback.
- **Negative margins** — the AI or a corrupted catalog value proposes a bundle that sells below
  cost; blocked by the margin floor.
- **Qualified-buyer-with-downsell-history** — the AI proposes a downsell to a buyer eligibility
  has already marked qualified; blocked by never-downsell-qualified-buyer, independent of whatever
  reasoning the AI attached to the proposal.
- **Repeated webhook** — the same order event is replayed; idempotency ensures the second attempt
  returns the already-recorded ledger action instead of executing a second money action.

The scoreboard is **N/30 blocked, by invariant** — every one of the 30 is a case where a plausible
or adversarial signal tried to produce a bad money action, and Money Guard, the single chokepoint,
stopped every one before it reached Execute.

---

## Files

```
uplift/
  config.py           # every threshold + invariant, with source cites
  catalog.py          # SKUs, costs, margins, quality levers
  levers.py           # [1] Crazy Eight enumeration + feature-downsell ordering
  basket.py           # [1] co-occurrence
  eligibility.py      # [2] filters + downsell qualification gate
  selector.py         # [3] AI proposal + JSON-repair; fallback pulls from the same eligible set (JSON validity ≠ safety)
  money_guard.py      # [4] THE chokepoint — the only door to money, for AI and fallback alike
  razorpay_adapter.py # [5] Protocol + Live + Mock
  ledger.py           # [6] append-only audit log — sequential IDs, no cryptographic hash chain, not tamper-evident (see cut list)
  eval/
    generate.py  simulate.py  run.py
    redteam.py        # 30 hand-written adversarial inputs  ← the only real number
  cli.py
docs: README.md · ARCHITECTURE.md   (EVAL, FAILURES, MONEY_MODEL are ## sections inside it)
```

**Stack:** Python 3.11 · Pydantic · SQLite · `rich` · `anthropic` · `uv`.
**Models:** `claude-opus-5` for the `demo` command; **Sonnet for the eval sweep** — stated explicitly
in ARCHITECTURE.md, because the deterministic layer does the constraining, not the model. That is
itself an AI-judgment point.

**Read before writing `levers.py`:**
`.claude/skills/hormozi-money-models-playbook/references/offers-upsell-downsell.md` and
`.../hormozi-lifetime-value-playbook/references/frameworks.md` + `checklists.md`.
*(Playbook summaries contain corrupted currency figures in the McDonald's example — use references/.)*

**MONEY_MODEL section rule:** every row is `invariant → source page → config key → test name`.
**If a cited rule has no enforcing test, delete the row.** A rule you enforce is engineering; a rule
you quote is a book report.

**How to describe the Hormozi dependency in docs — one sentence, not a chapter:** *"The action space
is a closed set of six levers from a published monetization framework, so the model picks from an
enumerated list instead of inventing an offer."* That is the whole point; everything past it is a
book report.

`FAILURES` section: keep a terminal open and **append one timestamped line per real bug as it
happens.** Zero cost if done live; substantial effort to fake convincingly if not. A genuine log of
your own build process is unfakeable and almost nobody will have one.

---

## Cut list (these cuts are the plan)

- **`server.py` / FastAPI webhook receiver + approval queue.** Approval becomes a ledger state
  (`PENDING_APPROVAL`); idempotency becomes a unit test calling the handler twice.
- **n=500 → n=100.** 3,000+ Opus calls is a lot of wall-clock cost and would come at the expense of
  everything else; n=100 is the shipped default — enough for the three baselines and the
  sensitivity sweep to be legible without the run becoming the bottleneck.
- **Baselines (b) random and (c) most-popular.** Three baselines is credible; five is padding when
  all are simulator-scored.
- **Two of three ablations.** Keep only four-week-vs-monthly billing (arithmetic, not simulated).
- **Three of five markdown docs** → sections inside ARCHITECTURE.md.
- **All of P2** (dashboard, agent-catalog, MCP). UAP/ACP/x402 are named in ARCHITECTURE.md's
  "what's next" line only — no extra build work.
- **Hash chain in `ledger.py` — cut by default, unconditionally, not a maybe.** `ledger.py` ships
  as a simple append-only log (each entry references the previous entry's ID); the ledger is
  explicitly **not tamper-evident** (no cryptographic chaining, no tamper-detection demo). Keeping it
  "conditionally" meant claiming an integrity property nothing in the build would actually
  demonstrate — exactly the kind of unbacked claim this plan exists to avoid.

**Do not cut:** the money guard (Tier 1) · the invariant tests · CLI rendering quality · the live
Razorpay test-mode screenshot · the red-team suite · the failure demo.

---

## README must-haves

1. **60-second quickstart that works with no API key**: `git clone && uv sync && uv run uplift demo`
   against the mock adapter + a recorded LLM fixture. A judge who clones and hits an auth error
   never sees the project run at all.
2. **Hero image above the fold**: the terminal screenshot of a blocked action, citation visible.
   Most judges read exactly that far.
3. **Named limitations**: no real conversion data · single merchant · offers must be pre-created,
   so the action space is bounded by dashboard config. Absence of this section reads as unaware.

## Verification

1. `uv run uplift demo` — one order end to end; every lever, every guard, every rejection with its
   citation. The primary demonstration path.
2. `uv run uplift redteam` — proves the core claim: even when the AI proposes a dangerous action,
   Money Guard blocks it before execution. **N/30 blocked, by invariant — the only real number in
   the submission.**
3. `uv run uplift eval --n 100` — three baselines + sensitivity sweep.
4. `uv run uplift audit verify` — confirms the ledger is append-only and sequential. It is **not
   tamper-evident** (no cryptographic hash chain, see Cut list), so it verifies ordering only; it
   does not detect tampering.
5. `pytest` — one test per Tier 1 invariant (plus any Tier 2 rules that made the cut) proving the
   violation is blocked.

## Non-goals

Real payment capture · production frontend · multi-merchant · auth. The program explicitly prefers
"a complete, working project in a narrower scope."
