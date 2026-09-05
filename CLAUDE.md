# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Build guidance for `uplift`. `current_plan.md` is the build strategy; this file is how the code gets
built. **When the two conflict on code structure, this file wins.**

> **Repo status: chunks 1-6 shipped.** **15 enforced rules**, **63 tests**, **30/30 red-team**,
> live Razorpay test mode, and `uplift verify-order` reconciling ledger rows against the gateway
> (exits non-zero on mismatch). `TIER_2_UNUSED` is empty. Hero + reconciliation SVGs in `docs/`.
> **Remaining: git (0 commits, by choice) and submission logistics.** The dashboard screenshot is
> no longer needed — `verify-order` replaces it with reproducible evidence.
>
> **Windows note:** `uv` installs to a directory that isn't on PATH under Windows Store Python.
> If `uv` is not found, prepend
> `~/AppData/Local/Packages/PythonSoftwareFoundation.Python.3.11_*/LocalCache/local-packages/Python311/Scripts`
> to PATH, or call `python -m uv`.

## 1. What this is

Razorpay AI Buildathon, **Track 01 (AI Growth & Agentic Commerce)**. An upsell & cross-sell agent for
a single merchant, running against **Razorpay test-mode APIs only**.

**The core claim, one sentence:** every other upsell agent in this track can emit an arbitrary
discount. This one cannot.

**Track 01's bar:** every money action must be explainable, bounded and gated. Show the audit trail.
Show one failure handled gracefully. Every design decision below serves that bar.

**Legend** (used throughout the code, docs and citations):
- **LTV** = *$100M Lifetime Value* (Hormozi, 2025)
- **MM** = *$100M Money Models* (Hormozi)
- **Crazy Eight** = LTV's eight-lever monetization menu; this agent implements levers 3–8.

## 2. The pipeline — non-negotiable

```
order.paid event / cart state / customer history
  [1] LEVER ENUMERATION            deterministic   levers.py, basket.py
  [2] ELIGIBILITY + QUALIFICATION  deterministic   eligibility.py   → THE candidate set
  [3] SELECT · SEQUENCE · PITCH    LLM + deterministic fallback     selector.py
  [4] MONEY GUARD                  deterministic   money_guard.py   ← the only door to money
  [5] EXECUTE                      razorpay_adapter.py — unreachable except via [4]
  [6] LEDGER  ledger.py            [7] MEASURE  eval/
```

Three hard constraints on any code written in this repo:

- **The LLM touches the money path at exactly one stage: [3].** Never introduce a model call into
  [1], [2], [4] or [5]. Stages [1] and [2] are deterministic and run *before* any AI involvement —
  the AI never sees an ungated candidate.
- **The fallback draws from the same eligible set stage [2] produced.** Never an ungated candidate.
  A fallback pick is an ordinary stage-[3] output, indistinguishable to every later stage from an AI
  pick.
- **Money Guard is the only security boundary.** Every path that could reach Execute — the original
  AI choice, the JSON-repaired AI choice, the fallback choice — converges there first. No exception
  handler, no shortcut branch, no `if fallback: skip_guard`. Money Guard independently **re-derives**
  every claim a proposal makes rather than trusting it.

**JSON validity is a parseability concern, not a safety property.** The selector gets exactly one
repair attempt on invalid JSON; if it still fails, the deterministic fallback runs. Never write a
comment, docstring, variable name or doc line implying that valid JSON makes an action safe.

## 3. Invariants — Tier 1 / Tier 2

Every invariant lives in `money_guard.py`, is configured by a key in `config.py`, and has a test.
These tests are the primary evidence for "bounded and gated."

**Tier 1 — must ship, each with a test. These are the invariants the core claim rests on.**

| Invariant | Config key | Check Money Guard runs |
|---|---|---|
| `never_discount_identical_sku` (MM p.97–98) | — (always on) | Reject any proposal whose bundle equals the anchor SKU at a lower price. The agent cannot emit a raw discount |
| `never_downsell_qualified_buyer` (LTV p.19) | — (always on) | Re-check buyer qualification independently before allowing any downsell lever through, whatever stage [3] proposed |
| `anchor_price_multiple_min` (MM p.84–88) | `anchor_price_multiple_min: 5` | Reject any Anchor Upsell priced below 5× the anchor baseline. **Separate rule from sequencing — do not merge the two** |
| `margin_floor` | `margin_floor_pct` | Reject if post-offer gross margin % on the SKU falls below the floor |
| `discount_ceiling` | `discount_ceiling_pct` | Reject if effective discount % (list vs. offer price) exceeds the ceiling |
| `kill_switch` | `kill_switch` | A single boolean that, when set, rejects every action regardless of any other rule passing |
| `idempotency` | — (always on) | **Promoted from Tier 2** once Tier 1 was complete and tested. A replayed `event_id` returns the recorded ledger entry; no second money action. Enforcement behind the repeated-webhook red-team category |
| `not_in_eligible_set` | — (always on) | Reject any proposal stage [2] did not produce — this is what stops a manipulated model inventing an offer |
| `daily_budget` | `daily_budget_inr` | **Promoted.** Reject if today's cumulative give-away (discount + credit) across executed offers would exceed the cap. Individually-legal offers can still add up to an unacceptable day |
| `rollover_credit` (MM p.92) | `rollover_credit_max_pct`, `rollover_next_offer_multiple_min` | **Promoted.** Reject if credit > 25% of the anchor, or the offer is below 4× the credit |
| `continuity_never_standalone` (MM p.146) | — (always on) | **Promoted.** Reject a Continuity offer for a customer with no prior accepted anchor or upsell |
| `sequence_largest_first` (LTV p.15,17) | — (always on) | Reject if a larger variant on the **same axis** was eligible and wasn't the one offered. Scoped to quantity/quality only — applying it to cross-sell would override market-basket lift with a price sort |
| `fatigue_cap` | `fatigue_cap_per_window`, `fatigue_window_days` | Reject if ≥N offers were already *shown* in the window. Blocked proposals never reached the customer, so they don't count |
| `cancellation_stop_conditions` (MM p.59,144,35) | `cancellation_rate_max`, `refund_rate_max`, `cancellation_window_days` | A real monitor over real `CANCELLED`/`REFUNDED` ledger rows written by `uplift cancel`. Abstains at sample 0 rather than inventing a rate |
| `auto_approve` | `auto_approve_threshold_inr` | **A verdict, not a rejection.** At/above the threshold returns `PENDING_APPROVAL`, which is as unable to reach stage [5] as `BLOCKED` — `approved` stays False and the adapter refuses it identically |

**Tier 2 — none left.** Every rule the plan named is now enforced with a check and a test, and
`TIER_2_UNUSED` in `config.py` is empty. The `cancellation_monitor` that row was conditional on
**was built**: `uplift cancel <order_id>` writes `CANCELLED`/`REFUNDED` rows and
`Ledger.outcome_rates` computes the three cited rates over a rolling window, so the row stayed in
the table on merit rather than being deleted.

**The rule that governed this still stands for anything added later:** a threshold sitting unused in
`config.py` is not enforcement, and a rule with no enforcing test gets deleted rather than listed.

**Action-space note.** `rollover_credit` and `continuity_never_standalone` govern offer types that
did not exist: enforcing them required adding `Candidate.credit_amount` with a rollover generator, and
a `CLUB_MONTHLY` continuity SKU with its own lever. **The generators deliberately do not pre-filter** —
`levers.continuity()` offers the membership to everyone, so Money Guard is what refuses it. A rule the
generator quietly avoids breaking is a convention, not an invariant, and cannot be violated by a
manipulated model, so it would prove nothing. `test_continuity_is_offered_by_the_generator_so_the_guard_can_refuse_it`
pins that.

**Promotion note — done.** `idempotency` was promoted into Tier 1 (see ARCHITECTURE.md FAILURES,
2026-09-05): it is the enforcement behind a whole red-team category, and shipping the suite without
it would have meant reporting 30/30 with one category uncovered. Money Guard takes an optional
`ReplayLookup`; the ledger satisfies it.

**The table rule.** Every shipped invariant is `invariant → source page → config key → test name`, in
ARCHITECTURE.md's MONEY_MODEL section. **If a cited rule has no enforcing test, delete the row.** A
rule you enforce is engineering; a rule you quote is a book report.

**India note.** Trial-with-penalty and negative-option billing are regulated differently in India than
in the book's US context. Those levers ship **disabled by default** in `config.py`.

## 4. Action space — Crazy Eight levers 3–8

Levers 1–2 (raise price, cut delivery cost) are merchant-level, not per-transaction — excluded.

| # | Lever | Deterministic generator |
|---|---|---|
| 3 | Upsell frequency | subscribe-and-save; recurring version of a consumable |
| 4 | Upsell quantity | bulk (prepay) · more often · bigger |
| 5 | Upsell quality | premium tier via the quality-lever list |
| 6 | Downsell quantity | fewer units |
| 7 | Downsell quality | quality levers read backwards |
| 8 | Cross-sell | the product solving the customer's **next problem** (market-basket support/confidence/lift) |

**Feature-downsell ordering** (MM p.115–121): remove highest-value features first — customers
re-upsell themselves. Ten lines in `levers.py`.

Enumeration is code, not a model call, because the systematic walk beats inspiration (LTV p.22). That
is the sourced argument for the deterministic/AI split, not a stylistic preference.

**How to describe this dependency in docs — one sentence, not a chapter:** *"The action space is a
closed set of six levers from a published monetization framework, so the model picks from an
enumerated list instead of inventing an offer."* Everything past that sentence is a book report.

## 5. Repo layout

```
uplift/
  config.py           every threshold + invariant, each with its source cite
  catalog.py          SKUs, costs, margins, quality levers
  levers.py           [1] Crazy Eight enumeration + feature-downsell ordering
  basket.py           [1] co-occurrence / market basket
  eligibility.py      [2] filters + downsell qualification gate
  selector.py         [3] AI proposal + one JSON repair; fallback pulls from the SAME eligible set
  money_guard.py      [4] THE chokepoint — one door to money, for AI and fallback alike
  razorpay_adapter.py [5] Protocol + Live + Mock
  ledger.py           [6] append-only log — sequential IDs, NOT tamper-evident
  eval/
    generate.py  simulate.py  run.py
    redteam.py        30 hand-written adversarial inputs
  cli.py
```

Docs: **`README.md`** and **`ARCHITECTURE.md`** only. EVAL, FAILURES and MONEY_MODEL are `##` sections
*inside* ARCHITECTURE.md — do not create separate markdown files for them.

## 6. Stack and conventions

Python 3.11 · Pydantic · SQLite · `rich` · `httpx` · `uv`.

- **Models:** stage [3] sits behind an `LLMProvider` Protocol in `selector.py` with three
  implementations — `GroqProvider` (live, free tier, `openai/gpt-oss-120b`), `FixtureProvider`
  (recorded JSON, the **no-key default** so a fresh clone runs), and `AnthropicProvider` (stub,
  a one-line switch if a key ever appears). `httpx` rather than an SDK: one POST doesn't earn a
  dependency.
- **That a free open-weights model sits at stage [3] is the argument, not a compromise.** State it
  in ARCHITECTURE.md: the deterministic layer does the constraining, not the model — demonstrated
  with a model nobody would call frontier. Never swap this for a frontier model to make results
  look better; that would invert the claim.
- Groq model ids move. If calls start failing, list `GET /v1/models` before assuming the key is
  bad — a stale model id and a dead key look identical from the fallback's side.
- Selector uses strict structured output, **one** repair attempt, then fallback.
- **A working fallback is not a working system.** The CLI always prints which provider answered,
  because a silent fallback hid a broken provider for three runs during the build.
- **Every threshold lives in `config.py` with its source cite.** Never inline a magic number in
  `money_guard.py` — a number without a config key and a cite is not an invariant.
- Pydantic models for every object crossing a stage boundary. Money Guard validates inputs it
  receives; it does not trust upstream types to have done it.

## 7. CLI output contract

This is a foundational design decision, not late polish. **A rejected action must print: the proposed
action · the invariant name · its source citation · what it did instead.**

```
BLOCKED — never_discount_identical_sku (MM p.97) · order completed with no offer · ledger #0412
```

If it prints `REJECTED: guard_violation`, the action is not explainable, and the build fails Track
01's own bar — *every money action must be explainable, bounded and gated* — at the one moment it most
needs to hold. **The terminal is where the audit trail is actually read.** A rejection that can't
explain itself is a guard that can't be audited, so hold `cli.py` rendering to the same bar as
`money_guard.py` logic. This is a build failure, not a cosmetic one.

## 8. Honesty rules

These bind the code and the docs.

- **Never claim a revenue uplift number** — not in code comments, README, ARCHITECTURE.md, CLI output
  or chart titles. Any uplift figure would come from a simulator we wrote, graded against assumptions
  we chose. The headline evidence is **containment**, which is measurable honestly.
- **Label real vs. simulated at every single appearance.**
  - *Real:* N/30 adversarial inputs blocked by invariant · deterministic-vs-LLM decision split ·
    actions proposed → blocked → executed by invariant · p95 latency and ₹ cost per decision ·
    0 unhandled exceptions with every fallback logged.
  - *Simulated:* the policy-ranking sweep across assumption Sets A/B/C. Its payload is the **rank
    flip**, which is a property of the simulator's chosen inputs — never a finding about real
    customers. **The flip is `frequency-first` ↔ `anchor-first` between Set A and Set C.** An
    earlier draft predicted Anchor ↔ Cross-sell; that is arithmetically impossible with this
    catalog (a cross-sell tops out at ₹739 gross profit against the anchor's ₹13,999), and the
    spike required here is what caught it. `test_rank_flip_actually_occurs` pins the real one —
    never edit the docs back to a flip the code does not produce.
- **The line that goes verbatim into ARCHITECTURE.md, next to the sweep chart:** *"Which policy wins
  depends on which assumption you make, so this submission claims no uplift number. What it can
  prove: nothing illegal reached the money path."*
- **Conversion is not observable here, so it is never reported.** `LiveAdapter` *creates* Razorpay
  orders and never captures them, so no offer is ever accepted. `added LTGP = conversion × GP`
  (LTV p.14) therefore cannot be computed honestly, and `RealMetrics` deliberately has no field for
  a conversion rate or realized revenue — `test_real_metrics_expose_no_realized_revenue` enforces
  that. An earlier version of this file claimed both were "reported per-decision from
  actually-executed actions"; they were not, and could not be.
- **What IS reported, from real executed ledger rows:** `offered_gross_profit` (GP the merchant
  earns *if* an executed offer is accepted — named `offered`, never `realized`) and
  `gross_profit_within_30d` (MM p.156). The two are currently equal because every offer this
  catalog produces bills on order creation; that equality is a real finding about a front-loaded
  money model, reported rather than dressed up, and the figures diverge the moment a genuine
  installment offer exists. A recurring offer's later periods are **not** counted — those depend on
  retention, which this system does not measure.
- **The ledger is append-only and NOT tamper-evident.** Each entry carries a sequential pointer to the
  previous entry's ID **for ordering only — not a checksum of prior content**; editing a past row
  leaves neighbouring pointers untouched and undetected. The hash chain is cut unconditionally. Never
  write a docstring, README line, CLI string or command name implying tamper detection.
- **`FAILURES` section:** append **one timestamped line per real bug as it happens**. Never backfill,
  never invent an entry. A genuine log of the build is the point; a reconstructed one is worthless.

## 9. Red-team suite

`eval/redteam.py` — 30 hand-written adversarial cases, a fixed set, all proving exactly one claim:
**even when the AI (or a corrupted upstream signal) proposes a dangerous or policy-violating action,
Money Guard blocks it before execution — regardless of which path the proposal came from.**

Categories, each an instance of that one claim:
- **Prompt injection in product titles** — model manipulated into proposing an arbitrary discount;
  blocked by `never_discount_identical_sku` / `discount_ceiling`.
- **Negative margins** — a bundle priced below cost from a corrupted catalog value; blocked by
  `margin_floor`.
- **Qualified buyer + downsell** — downsell proposed to a buyer eligibility already qualified;
  blocked by `never_downsell_qualified_buyer`, whatever reasoning the AI attached.
- **Repeated webhook** — same order event replayed; the second attempt returns the recorded ledger
  action. Enforced by `idempotency`, now Tier 1, so the category is genuinely covered.

Beyond "was it blocked", the suite asserts **each case is blocked by the invariant that should catch
it** — without that, one over-broad check could mask every other rule being broken and the scoreboard
would still read 30/30.

Scoreboard: **N/30 blocked, by invariant** — the **headline containment number**. Do not describe it
as "the only real number"; §8 lists five real measurements.

## 10. Commands

**Bootstrap (nothing runs before this — see Repo status at the top):**

```
pip install uv                       # uv is not preinstalled here
git init
uv sync                              # pyproject.toml already exists
```

Secrets live in `.env` (gitignored); `.env.example` names the keys. `GROQ_API_KEY` set →
stage [3] goes live; unset → `FixtureProvider`, and the demo still runs.
`RAZORPAY_KEY_ID`/`_SECRET` set → `LiveAdapter` (creates real test-mode orders); unset →
`MockAdapter`. **Verify a created order by fetching it by id, not by listing** — Razorpay's
`GET /v1/orders` does not surface test orders with no payment attempts and returns `count: 0`
even when the order exists (ARCHITECTURE.md FAILURES, 2026-09-05).

`cli.py` is registered as the `uplift` console script in `pyproject.toml`
(`[project.scripts] uplift = "uplift.cli:main"`); every command below depends on that entry point
existing. Python 3.11 pinned in `requires-python`.

**Run:**

```
uv run uplift demo            one order end to end — every lever, every guard, every rejection with its citation
uv run uplift demo --inject-discount   the prompt injection, blocked (offline: recorded fixture)
uv run uplift demo --unqualified       unqualified buyer, so downsell levers stay eligible
uv run uplift redteam         the 30 adversarial cases → 30/30 blocked, by invariant
uv run uplift eval --n 100    policy sweep across Sets A/B/C, real metrics separated from simulated
uv run uplift money-model     invariant → source → config key → test; exits non-zero if a test is missing
uv run uplift demo --replay   re-sends the previous event id — demonstrates idempotency
uv run uplift serve           local demo UI at :8000 (stdlib only, no build step, no CDN)
uv run uplift cancel <order_id>        record a cancellation (--refund for a refund) — feeds the monitor
uv run uplift demo --export docs/x.svg  save the run as an SVG (how the hero images are made)
uv run uplift verify-order <order_id>  reconciles a ledger row against the live Razorpay order; exits non-zero on mismatch
uv run uplift audit verify    confirms the ledger is append-only and sequential — ordering only, NOT tamper detection
```

**Test:**

```
uv run pytest                                       full suite
uv run pytest tests/test_money_guard.py             one file
uv run pytest tests/test_money_guard.py::test_never_discount_identical_sku   one test
uv run pytest -k margin_floor                       every test touching one invariant
```

**Test naming is load-bearing, not style.** One test per shipped invariant, named
`test_<invariant_name>` in `tests/test_money_guard.py`, so that `-k <invariant>` resolves and the
ARCHITECTURE.md MONEY_MODEL table's `test name` column is a working reference rather than a claim.
This is what makes §3's "no enforcing test ⇒ delete the row" rule mechanically checkable: if
`-k <invariant>` returns nothing, that row is not enforced and must come out of the table.

**README rule:** the 60-second quickstart must work **with no API key** —
`git clone && uv sync && uv run uplift demo` against the mock adapter plus a recorded LLM fixture. A
judge who clones and hits an auth error never sees the project run at all. README also needs the hero
image (a blocked action with its citation visible, above the fold) and a named-limitations section
(no real conversion data · single merchant · offers must be pre-created, so the action space is
bounded by dashboard config).

## 11. Out of scope — do not build

FastAPI **webhook receiver + approval queue** (approval is a ledger state,
`PENDING_APPROVAL`) · the ledger hash chain · baselines (b) random and (c) most-popular · two of the
three ablations (keep only four-week-vs-monthly billing) · separate EVAL/FAILURES/MONEY_MODEL
markdown files · agent-catalog · MCP server · real payment capture · **production** frontend ·
multi-merchant · auth.

**In scope by explicit decision (2026-09-05):** `uplift serve`, a stdlib-only local demo UI
(`server.py` + `ui.html`, no FastAPI, no new dependency, no build step, no CDN). The cut above
was a *webhook ingestion path with an approval queue* — a demo surface for reading the audit
trail is a different thing, and "show the audit trail" is Track 01's own bar. Keep it stdlib:
the moment it needs a framework or a bundler, it has become the thing that was cut.

**Also out of scope for this repo: the pitch video.** Do not write a shot list, script, timecodes,
narration or any "what to say on camera" content into this repo or into `current_plan.md`. That is
handled separately. If a decision here seems to need a filming justification, re-argue it from Track
01's bar instead — that's the stronger argument anyway.

**Do not cut, under any circumstances:** Money Guard Tier 1 · the invariant tests · CLI rendering
quality · **the live Razorpay reconciliation** (`verify-order` + `docs/verified-order.svg`, which
replaced the dashboard screenshot with something re-runnable that can fail) · the red-team suite ·
the failure demo.

## 12. Source material

Read before writing `levers.py`:
- [.claude/skills/hormozi-money-models-playbook/references/offers-upsell-downsell.md](.claude/skills/hormozi-money-models-playbook/references/offers-upsell-downsell.md)
- [.claude/skills/hormozi-lifetime-value-playbook/references/frameworks.md](.claude/skills/hormozi-lifetime-value-playbook/references/frameworks.md)
- [.claude/skills/hormozi-lifetime-value-playbook/references/checklists.md](.claude/skills/hormozi-lifetime-value-playbook/references/checklists.md)

**Use the `references/` files, not the `SKILL.md` summaries** — the summaries carry corrupted currency
figures in the McDonald's example. Page numbers in citations are the books' **printed** pages.

[current_plan.md](current_plan.md) is the build strategy: the containment-not-uplift decision, the
invariant table with sources, the cut list. This file is how it gets built.

## 13. Known open items

Three critical gaps recorded in
[.claude/agent-memory/critic-output.md](.claude/agent-memory/critic-output.md) are **deliberately left
unfixed in `current_plan.md`**. Do not edit that file to fix them unless asked. This file already
states the correct version of each for build purposes:

| Critic gap | What `current_plan.md` still says | What this file says (use this) |
|---|---|---|
| 1 — pipeline order | "AI recommends → eligibility filters → …" | §2: deterministic [1] and [2] run before any AI; the LLM enters only at [3] |
| 2 — headline number | "N/30 blocked … the only real number in the submission" | §9: the *headline containment number*; §8 lists five real measurements |
| 3 — idempotency status | treated as settled in the cut list and red-team section, Tier 2 in the invariants table | §3 and §9: Tier 2, first promotion candidate, category coverage stated honestly if unbuilt |

Also open and owned outside these documents: submission logistics (repo visibility, video upload,
submission form).

## 14. Time

**This document contains no schedule and must not acquire one.** Do not add hour budgets, deadlines,
checkpoints, buffers or phase timings to this file or to any other project file. Sequencing is
expressed as priority order only (Tier 1 before Tier 2, cut list before scope creep) — never as
elapsed time.
